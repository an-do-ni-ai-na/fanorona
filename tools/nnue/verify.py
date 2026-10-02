#!/usr/bin/env python3
"""Vérifie que l'inférence NNUE C++ (src/nnue.cpp) donne EXACTEMENT le même résultat que le
modèle de référence en numpy (même formule que le forward PyTorch d'entraînement), sur un
échantillon de positions. À relancer après toute modification de src/nnue.cpp ou de
l'encodage des features dans tools/nnue/train.py, ou avec un nouveau réseau exporté — une
divergence ici veut dire un vrai bug d'éval (silencieux sinon, jamais un crash).

Tolérance par défaut : ±1 centipion. numpy (BLAS) et la boucle C++ naïve n'accumulent pas les
256 termes de la couche cachée dans le même ordre ; sur une valeur brute tombant à quelques
1e-4 d'une frontière d'arrondi (X.4996 par ex.), cette différence d'ordre de sommation peut
suffire à faire arrondir à l'entier voisin d'un côté ou de l'autre. Vécu : 1/1000 sur le réseau
net_v2 (-3128.4994 -> round()=-3128 côté python, -3129 côté C++). Une VRAIE divergence
(mauvais encodage own/opp, mauvais ordre des cases...) donnerait des écarts systématiques et
bien plus grands qu'1 cp, pas un cas isolé à la limite d'un arrondi.

Usage :
    python3 tools/nnue/verify.py checkpoints/net_v1.nnue --samples data/gensfen.txt --n 200
"""
import argparse
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from train import INPUT_SIZE, SCORE_SCALE, SQUARE_NB, parse_fen_features  # noqa: E402


def load_nnue(path):
    """Renvoie une fonction x (90 flottants) -> sortie brute, pour les formats FNUE et FNU2."""
    with open(path, "rb") as f:
        def floats(n):
            return np.frombuffer(f.read(n * 4), dtype="<f4")

        magic = f.read(4)
        if magic == b"FNUE":
            (hidden,) = struct.unpack("<i", f.read(4))
            w1 = floats(hidden * INPUT_SIZE).reshape(hidden, INPUT_SIZE)
            b1, w2 = floats(hidden), floats(hidden)
            (b2,) = struct.unpack("<f", f.read(4))
            return lambda x: float(w2 @ np.clip(w1 @ x + b1, 0.0, 1.0) + b2)
        if magic != b"FNU2":
            raise ValueError(f"magic invalide dans {path}: {magic!r}")
        hidden, l2, l3, nb = struct.unpack("<4i", f.read(16))
        thresholds = struct.unpack(f"<{nb - 1}i", f.read(4 * (nb - 1)))
        w1 = floats(hidden * INPUT_SIZE).reshape(hidden, INPUT_SIZE)
        b1 = floats(hidden)
        stacks = []
        for _ in range(nb):
            w2, b2 = floats(l2 * hidden).reshape(l2, hidden), floats(l2)
            w3, b3 = floats(l3 * l2).reshape(l3, l2), floats(l3)
            w4, b4 = floats(l3), floats(1)[0]
            stacks.append((w2, b2, w3, b3, w4, b4))

    def forward(x):
        w2, b2, w3, b3, w4, b4 = stacks[sum(int(x.sum()) >= t for t in thresholds)]
        h1 = np.clip(w1 @ x + b1, 0.0, 1.0)
        h2 = np.clip(w2 @ h1 + b2, 0.0, 1.0)
        h3 = np.clip(w3 @ h2 + b3, 0.0, 1.0)
        return float(w4 @ h3 + b4)
    return forward


def eval_fen_reference(fen, net):
    own_sq, opp_sq = parse_fen_features(fen)
    x = np.zeros(INPUT_SIZE, dtype=np.float32)
    x[own_sq] = 1.0
    x[[SQUARE_NB + s for s in opp_sq]] = 1.0
    return round(net(x) * SCORE_SCALE)


def eval_fens_engine(engine, nnue_path, fens):
    cmds = [f"setoption name EvalFile value {nnue_path}", "setoption name UseNNUE value true"]
    for fen in fens:
        cmds += [f"position fen {fen}", "eval"]
    cmds.append("quit")
    p = subprocess.run([engine], input="\n".join(cmds) + "\n", capture_output=True, text=True, timeout=120)
    return [int(line.rsplit(":", 1)[1].strip()) for line in p.stdout.splitlines() if line.startswith("Evaluation")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("nnue")
    ap.add_argument("--engine", default="./fanorona")
    ap.add_argument("--samples", default=None, help="fichier gensfen (fen|score|wdl) dont on prend les FEN")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--tol", type=int, default=1, help="tolérance en centipions (bruit d'arrondi flottant)")
    args = ap.parse_args()

    if args.samples:
        fens = []
        with open(args.samples) as f:
            for line in f:
                fens.append(line.split("|", 1)[0])
                if len(fens) >= args.n:
                    break
    else:
        fens = ["BBBBBBBBB/BBBBBBBBB/BWBW1BWBW/WWWWWWWWW/WWWWWWWWW w 0 1"]

    net = load_nnue(args.nnue)
    ref_scores = [eval_fen_reference(fen, net) for fen in fens]
    cpp_scores = eval_fens_engine(args.engine, args.nnue, fens)

    if len(cpp_scores) != len(fens):
        print(f"ERREUR : {len(cpp_scores)} scores C++ reçus pour {len(fens)} positions envoyées")
        sys.exit(1)

    mismatches = [(f, p, c) for f, p, c in zip(fens, ref_scores, cpp_scores) if abs(p - c) > args.tol]
    for fen, p, c in mismatches:
        print(f"DIVERGENCE {fen} : reference={p} c++={c} (écart {abs(p - c)} > tol {args.tol})")

    if mismatches:
        print(f"\n{len(mismatches)}/{len(fens)} divergences -> ÉCHEC")
        sys.exit(1)
    print(f"{len(fens)}/{len(fens)} positions dans la tolérance (±{args.tol}cp) -> OK")


if __name__ == "__main__":
    main()
