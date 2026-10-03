#!/usr/bin/env python3
"""Symétries du plateau de Fanoron-Tsivy et réseaux NNUE.

Les 4 symétries du plateau 9x5 (identité, miroir gauche-droite, miroir haut-bas, demi-tour) conservent le jeu :
les diagonales partent des points où x+y est pair, parité conservée puisque 8 et 4 sont pairs. Cet outil :

  rules   vérifie sur des positions réelles que chaque position retournée a les mêmes coups légaux (en nombre)
          et la même évaluation HCE que l'originale ;
  nets    pour chaque réseau donné : écart d'évaluation entre les 4 orientations d'une même position (un réseau
          idéal donnerait 0), et erreur de prédiction du résultat réel des parties (MSE entre sigmoïde(éval/400)
          et 0/½/1) sur des positions JAMAIS vues à l'entraînement, dans chaque orientation.

    python3 tools/nnue/symmetry.py rules
    .venv/bin/python tools/nnue/symmetry.py nets checkpoints/net_v6.nnue checkpoints/essais/net_v7b.nnue \\
        --heldout data/gensfen_gen5.txt
"""
import argparse
import random
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ORIENTATIONS = (("identité", False, False), ("miroir G-D", True, False), ("miroir H-B", False, True),
                ("demi-tour", True, True))


def mirror_fen(fen, mx, my):
    board, rest = fen.split(" ", 1)
    rows = ["".join("." * int(c) if c.isdigit() else c for c in r) for r in board.split("/")]
    if mx:
        rows = [r[::-1] for r in rows]
    if my:
        rows = rows[::-1]
    out = []
    for r in rows:
        s, e = "", 0
        for c in r:
            if c == ".":
                e += 1
                continue
            s, e = s + (str(e) if e else "") + c, 0
        out.append(s + (str(e) if e else ""))
    return "/".join(out) + " " + rest


def sample(path, n, seed):
    """n lignes tirées uniformément (réservoir) dans un fichier gensfen."""
    rng = random.Random(seed)
    res = []
    with open(path) as f:
        for i, line in enumerate(f):
            if len(res) < n:
                res.append(line)
            elif (j := rng.randrange(i + 1)) < n:
                res[j] = line
    return res


def engine(cmds, eng):
    return subprocess.run([eng], input="\n".join(cmds) + "\nquit\n", capture_output=True, text=True).stdout


def cmd_rules(a):
    fens = [l.split("|")[0].rsplit(" ", 2)[0] for l in sample(a.samples, a.n, 1)]
    bad = 0
    for name, mx, my in ORIENTATIONS[1:]:
        cmds = []
        for f in fens:
            cmds += [f"position fen {f}", "moves", "eval", f"position fen {mirror_fen(f, mx, my)}", "moves", "eval"]
        out = [l for l in engine(cmds, a.engine).splitlines()[1:] if not l.startswith("info")]
        nm = [len(l.split()) for l in out if not l.startswith("Evaluation")]
        ev = [l for l in out if l.startswith("Evaluation")]
        dm = sum(nm[2 * i] != nm[2 * i + 1] for i in range(len(fens)))
        de = sum(ev[2 * i] != ev[2 * i + 1] for i in range(len(fens)))
        bad += dm + de
        print(f"{name:11s} : coups légaux différents {dm}/{len(fens)}, évaluation HCE différente {de}/{len(fens)}")
    print("OK : symétries du jeu confirmées" if not bad else "DIVERGENCES")
    return 1 if bad else 0


def cmd_nets(a):
    import numpy as np
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from train import SCORE_SCALE, parse_fen_features, symmetry_permutations
    from verify import load_nnue

    lines = sample(a.heldout, a.n, 7)
    fens = [l.split("|")[0] for l in lines]
    wdl = np.array([float(l.strip().split("|")[2]) for l in lines], dtype=np.float32)
    X = np.zeros((len(lines), 90), dtype=np.float32)
    for i, f in enumerate(fens):
        own, opp = parse_fen_features(f)
        X[i, own] = 1
        X[i, [45 + s for s in opp]] = 1
    perms = symmetry_permutations().numpy()
    short = [f.rsplit(" ", 2)[0] for f in fens[:a.asym_n]]
    print(f"{len(lines)} positions jamais vues ({Path(a.heldout).name}) ; asymétrie mesurée sur {len(short)}\n")
    print(f"{'réseau':16s} {'asym. médiane':>13s} {'90e centile':>11s}   MSE/résultat : "
          + "  ".join(f"{o[0]:>10s}" for o in ORIENTATIONS) + "    moyenne")
    for path in a.nets:
        cmds = [f"setoption name EvalFile value {path}", "setoption name UseNNUE value true"]
        for f in short:
            cmds += [x for _, mx, my in ORIENTATIONS for x in (f"position fen {mirror_fen(f, mx, my)}", "eval")]
        ev = [int(l.rsplit(":", 1)[1]) for l in engine(cmds, a.engine).splitlines() if l.startswith("Evaluation")]
        d = sorted(max(ev[4 * i:4 * i + 4]) - min(ev[4 * i:4 * i + 4]) for i in range(len(short)))
        net = load_nnue(path)
        mse = []
        for k in range(4):
            p = 1.0 / (1.0 + np.exp(-np.array([net(x) for x in X[:, perms[k]]])))
            mse.append(float(np.mean((p - wdl) ** 2)))
        print(f"{Path(path).name:16s} {statistics.median(d):10.0f} cp {d[int(.9 * len(d))]:8d} cp   "
              + " " * 15 + "  ".join(f"{m:10.5f}" for m in mse) + f"   {np.mean(mse):.5f}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default=str(ROOT / "fanorona"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rules")
    r.add_argument("--samples", default=str(ROOT / "data" / "gensfen_gen4.txt"))
    r.add_argument("--n", type=int, default=3000)
    n = sub.add_parser("nets")
    n.add_argument("nets", nargs="+")
    n.add_argument("--heldout", default=str(ROOT / "data" / "gensfen_gen5.txt"),
                   help="positions absentes de l'entraînement des réseaux comparés")
    n.add_argument("--n", type=int, default=20000)
    n.add_argument("--asym-n", type=int, default=3000)
    a = ap.parse_args()
    sys.exit(cmd_rules(a) if a.cmd == "rules" else cmd_nets(a))


if __name__ == "__main__":
    main()
