#!/usr/bin/env python3
"""Confronte le générateur de coups du moteur à la référence indépendante (tools/rules/reference.py).

Pour chaque position, compare l'ensemble EXACT des coups notés (chemin complet + A/W) donnés par la commande
`moves` du moteur à ceux de la référence. Positions : départ, positions réelles d'auto-jeu (data/gensfen*.txt),
positions de parties aléatoires (Fanoron-Tsivy et Fanoron-Dimy), en règle normale et en « continuation
obligatoire ». Puis perft depuis le départ. Toute différence est affichée avec la position.

    python3 tools/rules/crosscheck.py --positions 3000 --perft 4
"""
import argparse
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reference as R  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
START = {"tsivy": "BBBBBBBBB/BBBBBBBBB/BWBW1BWBW/WWWWWWWWW/WWWWWWWWW w", "dimy": "BBBBB/BBBBB/BW1BW/WWWWW/WWWWW w"}


def engine_moves(engine, fens, variant, mandatory):
    """Coups notés du moteur pour chaque FEN (un seul processus)."""
    cmds = [f"setoption name Variant value {variant}"]
    if mandatory:
        cmds.append("setoption name MandatoryContinuation value true")
    for f in fens:
        cmds += [f"position fen {f}", "moves"]
    out = subprocess.run([engine], input="\n".join(cmds) + "\nquit\n", capture_output=True, text=True).stdout
    lines = [l for l in out.splitlines()[1:] if not l.startswith("info")]
    assert len(lines) == len(fens), (len(lines), len(fens))
    return [set(l.split()) for l in lines]


def random_positions(variant, n, rng, mandatory):
    out = []
    while len(out) < n:
        board, pieces, us = R.parse_fen(START[variant])
        for _ in range(rng.randint(1, 60)):
            moves = R.generate(board, pieces, us, mandatory)
            if not moves:
                break
            pieces = R.apply(pieces, us, rng.choice(moves))
            us = "B" if us == "W" else "W"
        out.append(to_fen(board, pieces, us))
    return out


def to_fen(board, pieces, us):
    rows = []
    for y in range(board.ranks - 1, -1, -1):
        row, e = "", 0
        for x in range(board.files):
            c = pieces.get((x, y))
            if c is None:
                e += 1
                continue
            if e:
                row += str(e)
                e = 0
            row += c
        rows.append(row + (str(e) if e else ""))
    return "/".join(rows) + (" w" if us == "W" else " b")


def check(engine, variant, fens, mandatory, label):
    got = engine_moves(engine, fens, variant, mandatory)
    bad = 0
    total = 0
    for fen, eng in zip(fens, got):
        board, pieces, us = R.parse_fen(fen)
        ref = {m[0] for m in R.generate(board, pieces, us, mandatory)}
        total += len(ref)
        if ref != eng:
            bad += 1
            if bad <= 5:
                print(f"  DIFF {fen}\n    moteur seul : {sorted(eng - ref)[:8]}\n    référence seule : {sorted(ref - eng)[:8]}")
    print(f"{label:40} {len(fens):5} positions, {total:7} coups : {'OK' if not bad else f'{bad} positions différentes'}",
          flush=True)
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default=str(ROOT / "fanorona"))
    ap.add_argument("--positions", type=int, default=3000)
    ap.add_argument("--perft", type=int, default=4)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    bad = 0

    data = sorted(ROOT.glob("data/gensfen*.txt"))
    if data:
        with open(data[-1]) as f:
            sample = [l.split("|")[0].rsplit(" ", 2)[0] for _, l in zip(range(400000), f)]
        fens = rng.sample(sample, min(a.positions, len(sample)))
        bad += check(a.engine, "tsivy", fens, False, f"Tsivy, auto-jeu ({data[-1].name})")
    for variant in ("tsivy", "dimy"):
        for mand in (False, True):
            fens = random_positions(variant, a.positions // 2, rng, mand)
            bad += check(a.engine, variant, fens, mand, f"{variant}, parties aléatoires{', continuation obligatoire' if mand else ''}")

    for variant in ("tsivy", "dimy"):
        board, pieces, us = R.parse_fen(START[variant])
        cmds = [f"setoption name Variant value {variant}", "position startpos"] + [f"perft {d}" for d in range(1, a.perft + 1)]
        out = subprocess.run([a.engine], input="\n".join(cmds) + "\nquit\n", capture_output=True, text=True).stdout
        eng = [int(l.split(":")[1]) for l in out.splitlines() if l.startswith("Nodes searched")]
        ref = [R.perft(board, pieces, us, d) for d in range(1, a.perft + 1)]
        ok = eng == ref
        bad += not ok
        print(f"perft {variant:5} 1..{a.perft} : moteur {eng}  référence {ref}  {'OK' if ok else 'DIFFÉRENT'}", flush=True)
    print("\nRÉSULTAT :", "aucune divergence" if not bad else f"{bad} divergence(s)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
