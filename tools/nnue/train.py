#!/usr/bin/env python3
"""Entraînement du réseau NNUE de Fanorona-Engine à partir des données générées par
`./fanorona gensfen` (format texte : <fen>|<score_cp>|<wdl> par ligne, cf. cmd_gensfen
dans src/uci.cpp).

Architecture (cf. CLAUDE.md, feuille de route NNUE) : entrée 2x45 (cases occupées par le
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

    def __init__(self, path):
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

        scores_arr = np.asarray(scores, dtype=np.float32)
        wdls_arr = np.asarray(wdls, dtype=np.float32)
        probs = 1.0 / (1.0 + np.exp(-scores_arr / SCORE_SCALE))
        self.targets = (0.5 * (probs + wdls_arr)).astype(np.float32)

    def __len__(self):
        return len(self.targets)

    def iter_batches(self, indices, batch_size):
        """Un seul gather numpy vectorisé par batch (pas d'appel Python par échantillon)."""
        for start in range(0, len(indices), batch_size):
            b = indices[start : start + batch_size]
            x = torch.from_numpy(self.features[b].astype(np.float32, copy=False))
            y = torch.from_numpy(self.targets[b])
            yield x, y


class NNUE(nn.Module):
    def __init__(self, hidden=HIDDEN_SIZE):
        super().__init__()
        self.input = nn.Linear(INPUT_SIZE, hidden)
        self.output = nn.Linear(hidden, 1)

    def forward(self, x):
        h = torch.clamp(self.input(x), 0.0, 1.0)  # "ClippedReLU" façon Stockfish NNUE
        return self.output(h)


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
    ap.add_argument("--hidden", type=int, default=HIDDEN_SIZE, help="taille de la couche cachée (multiple de 16)")
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)

    dataset = SfenDataset(args.data)
    n = len(dataset)
    perm = rng.permutation(n)
    n_val = max(1, int(n * args.val_split))
    val_idx, train_idx = perm[:n_val], perm[n_val:]
    n_train = len(train_idx)

    model = NNUE(hidden=args.hidden)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    loss_fn = nn.MSELoss()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    for epoch in range(1, args.epochs + 1):
        model.train()
        rng.shuffle(train_idx)
        train_loss = 0.0
        for x, y in dataset.iter_batches(train_idx, args.batch_size):
            opt.zero_grad()
            pred = torch.sigmoid(model(x).squeeze(-1))
            loss = loss_fn(pred, y)
            loss.backward()
            opt.step()
            train_loss += loss.item() * x.size(0)
        train_loss /= n_train

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for x, y in dataset.iter_batches(val_idx, args.batch_size):
                pred = torch.sigmoid(model(x).squeeze(-1))
                val_loss += loss_fn(pred, y).item() * x.size(0)
        val_loss /= n_val

        print(f"epoch {epoch:3d}  train_loss {train_loss:.5f}  val_loss {val_loss:.5f}", flush=True)
        torch.save(model.state_dict(), out_path)

    if args.export:
        Path(args.export).parent.mkdir(parents=True, exist_ok=True)
        export_weights(model, args.export)
        print(f"poids exportés (float32, non quantifiés) -> {args.export}")


if __name__ == "__main__":
    main()
