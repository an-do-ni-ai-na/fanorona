#!/usr/bin/env python3
"""Faisabilité des tables de finales, étape 2 : le moteur joue-t-il juste les finales ?

Pour un échantillon de positions d'entrée en finale (eg_stats.py), recherche longue (--movetime ms, réseau par
défaut) : score et mat éventuel du point de vue du camp au trait. Comparé au résultat réel de la partie (jouée à
100 ms) : gain forcé non converti, nulle perdue, etc. Parallèle sur --procs moteurs locaux.

    python3 eg_deep.py ./fanorona --net checkpoints/net_v9.nnue --in endgames.jsonl --n 600 --movetime 3000
"""
import argparse
import json
import random
import subprocess
from concurrent.futures import ThreadPoolExecutor


def deep(args, pos):
    # stdin reste ouvert jusqu'au bestmove : une fin de stdin vaut « quit » et arrêterait la recherche.
    p = subprocess.Popen([args.engine], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    for c in (f"setoption name EvalFile value {args.net}", "setoption name UseNNUE value true", "setoption name Hash value 256",
              f"position fen {pos['fen']}", f"go movetime {args.movetime}"):
        p.stdin.write(c + "\n")
    p.stdin.flush()
    last, best = None, None
    for line in p.stdout:
        if line.startswith("info depth"):
            last = line.split()
        elif line.startswith("bestmove"):
            best = line.split()[1]
            break
    p.stdin.write("quit\n"); p.stdin.flush(); p.wait(timeout=30)
    d = dict(pos, deep_best=best)
    if last:
        i = last.index("score")
        d["deep_depth"] = int(last[last.index("depth") + 1])
        d["deep_kind"], d["deep_val"] = last[i + 1], int(last[i + 2])
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("engine")
    ap.add_argument("--net", required=True)
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", default="endgames_deep.jsonl")
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--movetime", type=int, default=3000)
    ap.add_argument("--procs", type=int, default=5)
    a = ap.parse_args()
    a.engine = __import__("os").path.abspath(a.engine)
    allpos = [json.loads(l) for l in open(a.inp)]
    random.Random(1).shuffle(allpos)
    pos = []
    for k in (4, 5, 6):  # --n positions par seuil d'entrée en finale (≤ 4, ≤ 5, ≤ 6 pièces)
        pos += [p for p in allpos if p.get("k", 6) == k][:a.n]
    with ThreadPoolExecutor(a.procs) as ex, open(a.out, "w") as f:
        for k, d in enumerate(ex.map(lambda p: deep(a, p), pos), 1):
            f.write(json.dumps(d) + "\n")
            if k % 50 == 0:
                print(f"{k}/{len(pos)}", flush=True)


if __name__ == "__main__":
    main()
