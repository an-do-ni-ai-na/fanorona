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
import random
import struct
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset, random_split

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


class SfenDataset(Dataset):
    def __init__(self, path):
        self.samples = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                fen, score, wdl = line.split("|")
                self.samples.append((fen, float(score), float(wdl)))

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        fen, score, wdl = self.samples[idx]
        own_sq, opp_sq = parse_fen_features(fen)
        x = torch.zeros(INPUT_SIZE)
        x[own_sq] = 1.0
        x[[SQUARE_NB + s for s in opp_sq]] = 1.0
        target = 0.5 * (torch.sigmoid(torch.tensor(score / SCORE_SCALE)) + wdl)
        return x, target


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
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    random.seed(args.seed)

    dataset = SfenDataset(args.data)
    n_val = max(1, int(len(dataset) * args.val_split))
    n_train = len(dataset) - n_val
    train_set, val_set = random_split(dataset, [n_train, n_val], generator=torch.Generator().manual_seed(args.seed))
    train_loader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(val_set, batch_size=args.batch_size)

    model = NNUE()
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    loss_fn = nn.MSELoss()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    for epoch in range(1, args.epochs + 1):
        model.train()
        train_loss = 0.0
        for x, y in train_loader:
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
            for x, y in val_loader:
                pred = torch.sigmoid(model(x).squeeze(-1))
                val_loss += loss_fn(pred, y).item() * x.size(0)
        val_loss /= n_val

        print(f"epoch {epoch:3d}  train_loss {train_loss:.5f}  val_loss {val_loss:.5f}")
        torch.save(model.state_dict(), out_path)

    if args.export:
        Path(args.export).parent.mkdir(parents=True, exist_ok=True)
        export_weights(model, args.export)
        print(f"poids exportés (float32, non quantifiés) -> {args.export}")


if __name__ == "__main__":
    main()
