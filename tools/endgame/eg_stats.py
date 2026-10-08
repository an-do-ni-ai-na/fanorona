#!/usr/bin/env python3
"""Faisabilité des tables de finales, étape 1 : fréquence des finales dans des parties réelles du moteur.

Lit un journal live de SPRT (match.py : une ligne « info » par coup joué avec la position cherchée, puis le
résultat de chaque partie) et mesure, partie par partie : le nombre minimal de pièces atteint, la répartition
du matériel à l'entrée en finale, le résultat. Écrit les positions d'entrée en finale (≤ --max-pieces pièces,
première fois) pour l'étape 2 (vérification par recherche profonde).

    python3 eg_stats.py /var/log/fanorona/gen12_n12c_sprt.jsonl --out /tmp/endgames.jsonl
"""
import argparse
import collections
import gzip
import json


def pieces(fen):
    board = fen.split()[0]
    return board.count("W"), board.count("B")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--out", default="/tmp/endgames.jsonl")
    ap.add_argument("--max-pieces", type=int, default=8)
    a = ap.parse_args()
    op = gzip.open if a.log.endswith(".gz") else open
    games = collections.defaultdict(dict)  # game -> {ply: (fen, score, move, engine)}
    result = {}
    with op(a.log, "rt") as f:
        for line in f:
            if '"event": "info"' in line:
                d = json.loads(line)
                if d.get("fen") and d.get("game") is not None:
                    w, b = pieces(d["fen"])
                    if w + b <= a.max_pieces:
                        games[d["game"]][d["ply"]] = (d["fen"], d.get("score_cp"), d.get("score_mate"), d.get("move"), d["engine"])
                    games[d["game"]].setdefault("min", 99)
                    games[d["game"]]["min"] = min(games[d["game"]]["min"], w + b)
            elif '"event": "sprt_update"' in line:
                d = json.loads(line)
                result[d["game"]] = d["raw_result"]
    n = len(result)
    print(f"{n} parties terminées, {len(games)} avec positions")
    mins = collections.Counter(games[g].get("min", 99) for g in result)
    print("\nNombre minimal de pièces atteint (total des deux camps) :")
    cum = 0
    for k in range(2, 13):
        cum += mins.get(k, 0)
        print(f"  ≤ {k:2d} pièces : {cum:5d} parties ({100 * cum / n:5.1f} %)")
    decisive = lambda r: not r.startswith("draw")
    print("\nRésultat selon la finale atteinte :")
    for lo, hi in ((2, 4), (5, 6), (7, 8), (9, 99)):
        gs = [g for g in result if lo <= games[g].get("min", 99) <= hi]
        if gs:
            dec = sum(decisive(result[g]) for g in gs)
            why = collections.Counter(result[g] for g in gs).most_common(4)
            print(f"  min {lo}-{hi if hi < 99 else '+'} : {len(gs):5d} parties, {100 * dec / len(gs):5.1f} % décisives  {why}")
    # Entrée en finale : première position à ≤ k pièces ; signature (pièces du camp au trait, de l'autre).
    out = open(a.out, "w")
    for k in (4, 5, 6):
        sig = collections.Counter()
        for g in result:
            plies = sorted(p for p in games[g] if isinstance(p, int))
            first = next((p for p in plies if sum(pieces(games[g][p][0])) <= k), None)
            if first is None:
                continue
            fen, sc, mate, mv, eng = games[g][first]
            w, b = pieces(fen)
            stm = fen.split()[1]
            me, opp = (w, b) if stm == "w" else (b, w)
            sig[(me, opp)] += 1
            out.write(json.dumps({"k": k, "game": g, "ply": first, "fen": fen, "score": sc, "mate": mate, "move": mv,
                                  "result": result[g], "stm": stm, "pieces": w + b}) + "\n")
        tot = sum(sig.values())
        print(f"\nEntrée à ≤ {k} pièces : {tot} parties ; matériel (trait, autre) le plus fréquent :")
        print("  " + ", ".join(f"{m}v{o}: {c}" for (m, o), c in sig.most_common(10)))
    out.close()
    print(f"\npositions d'entrée à ≤ 4, 5, 6 pièces -> {a.out}")


if __name__ == "__main__":
    main()
