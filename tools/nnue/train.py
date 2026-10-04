#!/usr/bin/env python3
"""Entraînement du réseau NNUE de Fanorona-Engine à partir des données générées par
`./fanorona gensfen` (format texte : <fen>|<score_cp>|<wdl> par ligne, cf. cmd_gensfen
dans src/uci.cpp).

Deux architectures (cf. CLAUDE.md, feuille de route NNUE). NNUE2 (--l2 > 0, format FNU2, réseau par défaut
net_v6 depuis le 2026-10-03) : même accumulateur, suivi de couches denses par nombre de pièces (classe NNUE2).
NNUE (format FNUE, net_v1 à net_v3) : entrée 2x45 (cases occupées par le
camp au trait / cases occupées par l'adversaire, un bit chacune), une couche cachée 256
avec ReLU clippé [0,1] ("accumulateur"), sortie scalaire. La sortie du réseau est en unités
score/SCORE_SCALE (logit) : score_cp = SCORE_SCALE * sortie_brute.

Cible d'entraînement : mélange 50/50 du score de recherche (converti en probabilité via une
sigmoïde) et du résultat réel de la partie — mêmes principes que nnue-pytorch/Stockfish, en
plus simple (pas de pondération lambda réglable pour l'instant).

Usage :
    python3 tools/nnue/train.py data/gensfen.txt --epochs 20 --out checkpoints/net.pt

Ce script produit un checkpoint PyTorch (.pt) et, avec --export, un export binaire *non
quantifié* (float32) des poids : format provisoire documenté dans export_weights(), en
attendant l'écriture du chargeur C++ (src/nnue/, feuille de route CLAUDE.md) qui décidera
du vrai format quantifié int16/int8 à ce moment-là.
"""
import argparse
import struct
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

SQUARE_NB = 45
FILE_NB = 9
RANK_NB = 5
INPUT_SIZE = 2 * SQUARE_NB
HIDDEN_SIZE = 256

# Pente de la sigmoïde score->probabilité, en centipions (mêmes unités que score_to_uci
# dans src/search.cpp). 400cp -> ~91% de probabilité de gain, échelle usuelle en Elo/logistique.
SCORE_SCALE = 400.0


def symmetry_permutations():
    """Les 4 symétries du plateau 9x5 qui conservent le jeu : identité, miroir gauche-droite (x -> 8-x), miroir
    haut-bas (y -> 4-y), demi-tour. Les diagonales partent des points où x+y est pair, parité conservée puisque
    8 et 4 sont pairs. Pas d'échange de couleurs : les entrées sont déjà vues du camp au trait.
    Renvoie un tableau [4][90] : la colonne c de la position transformée = la colonne perm[c] de l'originale
    (chaque symétrie est sa propre inverse)."""
    perms = []
    for mx, my in ((False, False), (True, False), (False, True), (True, True)):
        sq = []
        for s in range(SQUARE_NB):
            y, x = divmod(s, FILE_NB)
            sq.append((RANK_NB - 1 - y if my else y) * FILE_NB + (FILE_NB - 1 - x if mx else x))
        perms.append(sq + [SQUARE_NB + t for t in sq])
    return torch.tensor(perms, dtype=torch.long)


def parse_fen_features(fen):
    """FEN -> (cases du camp au trait, cases adverses), en suivant exactement l'encodage de
    Position::fen()/Position::set() (src/position.cpp) : rangées 5->1, x=0..8 (colonnes a..i)."""
    board, stm = fen.split()[0], fen.split()[1]
    us_is_white = stm == "w"
    white_sq, black_sq = [], []
    y, x = RANK_NB - 1, 0
    for ch in board:
        if ch == "/":
            y -= 1
            x = 0
        elif ch.isdigit():
            x += int(ch)
        else:
            sq = y * FILE_NB + x
            (white_sq if ch in "Ww" else black_sq).append(sq)
            x += 1
    return (white_sq, black_sq) if us_is_white else (black_sq, white_sq)


class SfenDataset:
    """Pré-encode tout le fichier en deux tableaux numpy contigus (features uint8, cibles
    float32), une seule fois au chargement. Volontairement PAS un torch.utils.data.Dataset
    utilisé via DataLoader : pour un jeu de données qui tient entièrement en RAM (quelques
    Go), le chemin Dataset/DataLoader par défaut rappelle __getitem__ un échantillon à la
    fois puis collate — mesuré à l'usage sur 10M positions : des dizaines de minutes rien que
    pour l'overhead Python par-échantillon, largement dominant devant le calcul du réseau
    lui-même. `iter_batches()` ci-dessous fait un seul slicing numpy vectorisé par batch."""

    def __init__(self, path, wdl_weight=0.5, score_scale=SCORE_SCALE, device="cpu"):
        self.packed = None
        if str(path).endswith(".npz"):
            # Format binaire produit par --save-npz : évite l'analyse du texte (des minutes et plusieurs Go de
            # mémoire pour ~20M positions), indispensable sur une machine d'entraînement à 8 Go de RAM.
            # Plusieurs fichiers séparés par des virgules sont concaténés. Format compacté (clé "packed") : les 90
            # entrées sur 12 octets (np.packbits), 7,5x moins de mémoire ; ancien format (clé "features") accepté.
            parts = [np.load(p) for p in str(path).split(",")]
            self.scores = np.concatenate([d["scores"] for d in parts])
            self.wdls = np.concatenate([d["wdl"] for d in parts])
            packed = [d["packed"] if "packed" in d else np.packbits(d["features"], axis=1) for d in parts]
            self.packed = np.concatenate(packed) if len(packed) > 1 else packed[0]
            self.features = None if torch.device(device).type != "cpu" else np.unpackbits(self.packed, axis=1)[:, :INPUT_SIZE]
        else:
            fens, scores, wdls = [], [], []
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    fen, score, wdl = line.split("|")
                    fens.append(fen)
                    scores.append(float(score))
                    wdls.append(float(wdl))

            n = len(fens)
            self.features = np.zeros((n, INPUT_SIZE), dtype=np.uint8)
            for i, fen in enumerate(fens):
                own_sq, opp_sq = parse_fen_features(fen)
                self.features[i, own_sq] = 1
                self.features[i, [SQUARE_NB + s for s in opp_sq]] = 1
            self.scores = np.asarray(scores, dtype=np.float32)
            self.wdls = np.asarray(wdls, dtype=np.float32)

        probs = 1.0 / (1.0 + np.exp(-self.scores / score_scale))
        # cible = mélange score de recherche / résultat de partie (0.5 = moitié-moitié, historique)
        self.targets = ((1.0 - wdl_weight) * probs + wdl_weight * self.wdls).astype(np.float32)

        # Sur GPU, tout le jeu de données (uint8, ~1,8 Go pour 20M positions) est copié une fois dans la mémoire
        # de la carte et les batches y sont découpés : le processeur hôte ne fait presque rien.
        self.device = torch.device(device)
        if self.device.type != "cpu":
            if self.packed is None:
                self.packed = np.packbits(self.features, axis=1)
            # compacté sur la carte (12 octets par position), décompacté batch par batch dans batch_x()
            self.packed_t = torch.from_numpy(self.packed).to(self.device)
            self.shifts = torch.arange(7, -1, -1, dtype=torch.uint8, device=self.device)
            self.targets_t = torch.from_numpy(self.targets).to(self.device)
            # libère la copie en RAM (la machine GPU n'a que 8 Go : permet plusieurs entraînements en parallèle)
            self.features = self.packed = self.scores = self.wdls = self.targets = None

    def batch_x(self, idx):
        """Entrées (float) des positions d'indices `idx`, décompactées sur la carte."""
        bits = (self.packed_t[idx].unsqueeze(2) >> self.shifts) & 1
        return bits.reshape(len(idx), -1)[:, :INPUT_SIZE].float()

    def __len__(self):
        return len(self.targets_t) if self.targets is None else len(self.targets)




    def iter_batches(self, indices, batch_size):
        """Un seul gather vectorisé par batch (pas d'appel Python par échantillon)."""
        if self.device.type != "cpu":
            idx = torch.from_numpy(indices).to(self.device)
            for start in range(0, len(indices), batch_size):
                b = idx[start : start + batch_size]
                yield self.batch_x(b), self.targets_t[b]
            return
        for start in range(0, len(indices), batch_size):
            b = indices[start : start + batch_size]
            x = torch.from_numpy(self.features[b].astype(np.float32, copy=False))
            y = torch.from_numpy(self.targets[b])
            yield x, y


def convert_to_npz(text_path, npz_path):
    """Convertit un fichier gensfen texte en .npz compacté, en flux : pas de liste Python de dizaines de millions de
    lignes en mémoire (40M positions : ~0,5 Go d'entrées compactées au lieu de ~10 Go)."""
    with open(text_path) as f:
        n = sum(1 for line in f if line.strip())
    packed = np.zeros((n, 12), dtype=np.uint8)
    scores = np.zeros(n, dtype=np.float32)
    wdls = np.zeros(n, dtype=np.float32)
    i = 0
    with open(text_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            fen, score, wdl = line.split("|")
            own_sq, opp_sq = parse_fen_features(fen)
            v = 0
            for k in own_sq:
                v |= 1 << (95 - k)  # bit 7 de l'octet 0 = entrée 0 (ordre de np.packbits / np.unpackbits)
            for k in opp_sq:
                v |= 1 << (95 - SQUARE_NB - k)
            packed[i] = np.frombuffer(v.to_bytes(12, "big"), dtype=np.uint8)
            scores[i], wdls[i] = float(score), float(wdl)
            i += 1
    np.savez(npz_path, packed=packed, scores=scores, wdl=wdls)
    return n


class NNUE(nn.Module):
    def __init__(self, hidden=HIDDEN_SIZE):
        super().__init__()
        self.input = nn.Linear(INPUT_SIZE, hidden)
        self.output = nn.Linear(hidden, 1)

    def forward(self, x):
        h = torch.clamp(self.input(x), 0.0, 1.0)  # "ClippedReLU" façon Stockfish NNUE
        return self.output(h)


class NNUE2(nn.Module):
    """Réseau à couches empilées (2026-10-02) : même accumulateur 90 -> hidden que NNUE (donc même mise à jour
    incrémentale côté moteur), suivi de deux petites couches denses hidden -> l2 -> l3 -> 1 (ClippedReLU entre
    chaque). Les couches denses existent en `nb` exemplaires ("buckets") choisis par le nombre total de pièces :
    bucket = nombre de seuils <= nb_pièces. Au Fanorona le plateau se vide très vite (63 % des positions des
    données ont <= 8 pièces), et un plateau plein ne s'évalue pas comme une finale à 3 contre 2.
    Les nb exemplaires sont calculés d'un coup (une seule nn.Linear de sortie nb*l2) puis le bon est extrait
    par indexation : c'est l'astuce des "layer stacks" de nnue-pytorch."""

    def __init__(self, hidden=HIDDEN_SIZE, l2=16, l3=32, thresholds=(5, 8, 12)):
        super().__init__()
        self.hidden, self.l2_size, self.l3_size = hidden, l2, l3
        self.thresholds = list(thresholds)
        self.nb = len(self.thresholds) + 1
        self.register_buffer("thr", torch.tensor(self.thresholds, dtype=torch.float32))
        self.input = nn.Linear(INPUT_SIZE, hidden)
        self.l2 = nn.Linear(hidden, l2 * self.nb)
        self.l3 = nn.Linear(l2, l3 * self.nb)
        self.output = nn.Linear(l3, self.nb)

    def bucket(self, x):
        return (x.sum(1, keepdim=True) >= self.thr).sum(1)

    def forward(self, x):
        b = self.bucket(x)
        rows = torch.arange(x.size(0), device=x.device)
        h1 = torch.clamp(self.input(x), 0.0, 1.0)
        h2 = torch.clamp(self.l2(h1).view(-1, self.nb, self.l2_size)[rows, b], 0.0, 1.0)
        h3 = torch.clamp(self.l3(h2).view(-1, self.nb, self.l3_size)[rows, b], 0.0, 1.0)
        return self.output(h3)[rows, b].unsqueeze(-1)


def export_weights2(model, path):
    """Export float32 little-endian du réseau NNUE2 :
        magic b"FNU2" | hidden, l2, l3, nb (int32 x4) | seuils (int32 x (nb-1)) |
        input.weight [hidden][90] | input.bias [hidden] |
        pour chaque bucket b : w2 [l2][hidden], b2 [l2], w3 [l3][l2], b3 [l3], w4 [l3], b4 (1 flottant)
    """
    def arr(t):
        return t.detach().numpy().astype("<f4")

    with open(path, "wb") as f:
        f.write(b"FNU2")
        f.write(struct.pack("<4i", model.hidden, model.l2_size, model.l3_size, model.nb))
        f.write(struct.pack(f"<{model.nb - 1}i", *model.thresholds))
        f.write(arr(model.input.weight).tobytes())
        f.write(arr(model.input.bias).tobytes())
        w2, b2 = arr(model.l2.weight), arr(model.l2.bias)
        w3, b3 = arr(model.l3.weight), arr(model.l3.bias)
        w4, b4 = arr(model.output.weight), arr(model.output.bias)
        L2, L3 = model.l2_size, model.l3_size
        for b in range(model.nb):
            f.write(w2[b * L2:(b + 1) * L2].tobytes())
            f.write(b2[b * L2:(b + 1) * L2].tobytes())
            f.write(w3[b * L3:(b + 1) * L3].tobytes())
            f.write(b3[b * L3:(b + 1) * L3].tobytes())
            f.write(w4[b].tobytes())
            f.write(b4[b:b + 1].tobytes())


def export_weights(model, path):
    """Export binaire *non quantifié* (float32, little-endian) :
        magic b"FNUE" (4o) | hidden_size (int32) |
        input.weight [hidden][90] (row-major) | input.bias [hidden] |
        output.weight [hidden] | output.bias [1]
    Provisoire : sert de contrat de départ pour le futur chargeur C++, qui ajoutera la
    quantification int16/int8 (poids d'entrée QW, sortie QO) une fois écrit.
    """
    with open(path, "wb") as f:
        f.write(b"FNUE")
        f.write(struct.pack("<i", model.input.out_features))
        for t in (model.input.weight, model.input.bias, model.output.weight, model.output.bias):
            f.write(t.detach().numpy().astype("<f4").tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data", help="fichier gensfen (fen|score|wdl par ligne)")
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch-size", type=int, default=1024)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--val-split", type=float, default=0.02)
    ap.add_argument("--out", default="checkpoints/net.pt")
    ap.add_argument("--export", default=None, help="chemin du fichier .nnue (float32, optionnel)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--score-scale", type=float, default=SCORE_SCALE,
                    help="échelle score -> probabilité de la perte, à ajuster au jeu de données")
    ap.add_argument("--wdl-weight", type=float, default=0.5, help="poids du résultat de partie dans la cible (0 = score seul)")
    ap.add_argument("--hidden", type=int, default=HIDDEN_SIZE, help="taille de la couche cachée (multiple de 16)")
    ap.add_argument("--l2", type=int, default=0,
                    help="> 0 : réseau à couches empilées NNUE2 (hidden -> l2 -> l3 -> 1, multiples de 8), format FNU2")
    ap.add_argument("--l3", type=int, default=32)
    ap.add_argument("--buckets", default="5,8,12",
                    help="NNUE2 : seuils de nombre de pièces séparant les sous-réseaux ('' = un seul)")
    ap.add_argument("--lr-gamma", type=float, default=1.0, help="taux d'apprentissage multiplié par ce facteur à chaque epoch")
    ap.add_argument("--cuda-graph", action="store_true",
                    help="GPU : étape d'entraînement enregistrée en CUDA Graph et rejouée d'un seul appel par batch "
                         "(le processeur hôte ne limite plus la carte) ; ignore le dernier batch incomplet")
    ap.add_argument("--graph-steps", type=int, default=8,
                    help="--cuda-graph : nombre de pas d'entraînement enregistrés dans un même graphe (le processeur "
                         "hôte n'intervient qu'une fois tous les N batches)")
    ap.add_argument("--device", default="auto", help="cpu, cuda ou auto (cuda si une carte est disponible)")
    ap.add_argument("--save-npz", default=None,
                    help="convertit les données au format binaire .npz (à donner ensuite comme données) puis s'arrête")
    ap.add_argument("--augment", action="store_true",
                    help="présente chaque position d'entraînement dans une des 4 symétries du plateau, tirée au hasard")
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)

    device = torch.device("cuda" if args.device == "auto" and torch.cuda.is_available()
                          else ("cpu" if args.device == "auto" else args.device))
    if args.save_npz:
        convert_to_npz(args.data, args.save_npz)
        print(f"données converties -> {args.save_npz}")
        return
    dataset = SfenDataset(args.data, args.wdl_weight, args.score_scale, device)
    print(f"{len(dataset)} positions, entraînement sur {device}"
          + (f" ({torch.cuda.get_device_name(device)})" if device.type == "cuda" else ""), flush=True)
    # La sortie du réseau reste un logit en unités score/SCORE_SCALE (lue ainsi par src/nnue.cpp) ; seule la
    # perte passe en probabilité avec l'échelle --score-scale, ajustée au jeu de données (scores -> résultats).
    out_to_logit = SCORE_SCALE / args.score_scale
    n = len(dataset)
    perm = rng.permutation(n)
    n_val = max(1, int(n * args.val_split))
    val_idx, train_idx = perm[:n_val], perm[n_val:]
    n_train = len(train_idx)

    if args.l2 > 0:
        thresholds = [int(t) for t in args.buckets.split(",") if t.strip()]
        model = NNUE2(hidden=args.hidden, l2=args.l2, l3=args.l3, thresholds=thresholds)
    else:
        model = NNUE(hidden=args.hidden)
    model.to(device)
    graph = args.cuda_graph and device.type == "cuda"
    if graph:  # taux d'apprentissage tenseur + Adam "capturable" : tout l'état reste sur la carte
        opt = torch.optim.Adam(model.parameters(), lr=torch.tensor(args.lr, device=device), capturable=True)
    else:
        opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    sched = None if graph else torch.optim.lr_scheduler.ExponentialLR(opt, gamma=args.lr_gamma)
    perms = symmetry_permutations().to(device)
    loss_fn = nn.MSELoss()

    if graph:
        bs, gk = args.batch_size, args.graph_steps
        static_idx = torch.zeros(gk * bs, dtype=torch.long, device=device)
        loss_acc = torch.zeros((), device=device)

        def train_step():  # gk pas complets, chacun sur sa tranche de static_idx
            for k in range(gk):
                idx = static_idx[k * bs:(k + 1) * bs]
                x = dataset.batch_x(idx)
                if args.augment:
                    x = x.gather(1, perms[torch.randint(0, 4, (bs,), device=device)])
                pred = torch.sigmoid(model(x).squeeze(-1) * out_to_logit)
                loss = loss_fn(pred, dataset.targets_t[idx])
                loss.backward()
                opt.step()
                opt.zero_grad(set_to_none=False)
                loss_acc.add_(loss.detach())

        # quelques pas réels sur un flux annexe (exigé avant la capture), puis enregistrement d'un pas complet
        side = torch.cuda.Stream()
        side.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(side):
            for k in range(2):
                static_idx.copy_(torch.from_numpy(train_idx[k * gk * bs:(k + 1) * gk * bs]).to(device))
                train_step()
        torch.cuda.current_stream().wait_stream(side)
        train_idx_t = torch.from_numpy(train_idx).to(device)
        step_graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(step_graph):
            train_step()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        model.train()
        if not graph:
            rng.shuffle(train_idx)
        if graph:  # mélange sur la carte : ni tri sur le processeur hôte, ni transfert de 150 Mo d'indices
            idx_t = train_idx_t[torch.randperm(len(train_idx_t), device=device)]
            n_chunks = len(train_idx) // (gk * bs)  # le reste (< gk batches, tiré au hasard) est ignoré
            loss_acc.zero_()
            for k in range(n_chunks):
                static_idx.copy_(idx_t[k * gk * bs:(k + 1) * gk * bs])
                step_graph.replay()
            train_loss = loss_acc.item() / (n_chunks * gk)
            for g in opt.param_groups:
                g["lr"].fill_(args.lr * args.lr_gamma ** epoch)
        train_loss_t = torch.zeros((), device=device)  # cumul sur l'appareil : pas de synchronisation par batch
        for x, y in ([] if graph else dataset.iter_batches(train_idx, args.batch_size)):
            if args.augment:  # une symétrie au hasard par position (la validation, elle, reste non transformée)
                x = x.gather(1, perms[torch.randint(0, 4, (x.size(0),), device=device)])
            opt.zero_grad()
            pred = torch.sigmoid(model(x).squeeze(-1) * out_to_logit)
            loss = loss_fn(pred, y)
            loss.backward()
            opt.step()
            train_loss_t += loss.detach() * x.size(0)
        if not graph:
            train_loss = train_loss_t.item() / n_train
            sched.step()

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for x, y in dataset.iter_batches(val_idx, args.batch_size):
                pred = torch.sigmoid(model(x).squeeze(-1) * out_to_logit)
                val_loss += loss_fn(pred, y).item() * x.size(0)
        val_loss /= n_val

        print(f"epoch {epoch:3d}  train_loss {train_loss:.5f}  val_loss {val_loss:.5f}  ({time.time() - t0:.0f} s)", flush=True)
        torch.save({k: v.cpu() for k, v in model.state_dict().items()}, out_path)

    if args.export:
        Path(args.export).parent.mkdir(parents=True, exist_ok=True)
        (export_weights2 if args.l2 > 0 else export_weights)(model.cpu(), args.export)
        print(f"poids exportés (float32, non quantifiés) -> {args.export}")


if __name__ == "__main__":
    main()
