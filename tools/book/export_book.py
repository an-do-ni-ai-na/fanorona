#!/usr/bin/env python3
"""Exporte la base d'ouvertures (tools/gui/book.json, avec le score pratique) en livre texte pour le moteur.

    python3 tools/book/export_book.py --out checkpoints/book.txt
    ./fanorona  puis  setoption name BookFile value checkpoints/book.txt / setoption name OwnBook value true

Choix des coups, position par position :
- candidats : coups à moins de --margin cp du meilleur selon l'analyse profonde ;
- avec le score pratique (parties de tools/book/book_stats.py) : score lissé (v + n/2 + k/2) / (parties + k),
  qui tire les coups peu joués vers 50 % ; on garde le meilleur et ceux à moins de --spread de lui, pondérés
  par exp((score - meilleur) / --temp) : variété sans tomber dans les pièges ;
- sans parties (positions profondes) : le meilleur coup de l'analyse seul.
Format : <coups depuis la position initiale, ou "-">\\t<coup> <poids> [<coup> <poids> ...]
"""
import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def choose(lines, a):
    best_eval = lines[0][1]
    cand = [l for l in lines if l[1] >= best_eval - a.margin]
    with_stats = [l for l in cand if len(l) > 3 and sum(l[3]) >= a.min_games]
    if not with_stats:
        return [(lines[0][0], 100)]
    scored = []
    for l in with_stats:
        w, d, lo = l[3]
        n = w + d + lo
        scored.append((l[0], (w + d / 2 + a.k / 2) / (n + a.k)))
    top = max(s for _, s in scored)
    return [(m, max(1, round(100 * math.exp((s - top) / a.temp)))) for m, s in scored if s >= top - a.spread]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default=str(ROOT / "tools" / "gui" / "book.json"))
    ap.add_argument("--out", default=str(ROOT / "checkpoints" / "book.txt"))
    ap.add_argument("--margin", type=int, default=60, help="écart maximal à la meilleure évaluation (cp)")
    ap.add_argument("--min-games", type=int, default=20, help="parties minimales pour utiliser le score pratique")
    ap.add_argument("--k", type=float, default=20, help="lissage du score pratique (parties fictives à 50 %%)")
    ap.add_argument("--spread", type=float, default=0.02, help="écart maximal au meilleur score pratique")
    ap.add_argument("--temp", type=float, default=0.01, help="température des poids")
    a = ap.parse_args()
    b = json.loads(Path(a.book).read_text())
    out, multi = [], 0
    for key, node in b["nodes"].items():
        if not node["l"]:
            continue
        ch = choose(node["l"], a)
        multi += len(ch) > 1
        out.append(f"{key or '-'}\t" + " ".join(f"{m} {w}" for m, w in ch))
    head = (f"# Livre d'ouvertures Fanoron-Tsivy ({b.get('net')}, {b.get('date')}, {b.get('games', 0)} parties) : "
            f"tools/book/export_book.py --margin {a.margin} --min-games {a.min_games} --spread {a.spread}\n")
    Path(a.out).write_text(head + "\n".join(sorted(out, key=lambda s: (s.count(" ", 0, s.index("\t")), s))) + "\n")
    print(f"{len(out)} positions ({multi} avec plusieurs coups) -> {a.out}")


if __name__ == "__main__":
    main()
