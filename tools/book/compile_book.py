#!/usr/bin/env python3
"""Compile l'analyse de tools/book/build_book.py (JSONL) en tools/gui/book.json, lu par l'interface.

    python3 tools/book/compile_book.py data/book_nodes.jsonl --net net_v9

    python3 tools/book/compile_book.py data/book_nodes.jsonl --net net_v9 --stats data/book_games.jsonl

Format : {"net", "date", "positions", "games", "nodes": {"<coups séparés par des espaces>": {"d": profondeur,
"l": [[coup, score, [variante], [victoires, nulles, défaites]], ...]}}}, scores en centipions du point de vue du
camp au trait ; le quatrième élément (avec --stats, parties de tools/book/book_stats.py) compte les résultats
du point de vue du camp qui joue le coup.
"""
import argparse
import collections
import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def load_stats(path):
    """{position: {coup: [v, n, d]}} du point de vue du camp qui joue le coup, et le nombre de parties."""
    stats = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0]))
    games = 0
    with open(path) as f:
        for line in f:
            try:
                g = json.loads(line)
            except ValueError:
                continue  # ligne tronquée (partie interrompue)
            games += 1
            white = 1 if g["result"] == "white wins" else -1 if g["result"] == "black wins" else 0
            for k, m in enumerate(g["book"]):
                r = white if k % 2 == 0 else -white  # les Blancs jouent les demi-coups pairs
                stats[" ".join(g["book"][:k])][m][0 if r > 0 else 1 if r == 0 else 2] += 1
    return stats, games


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("nodes", help="JSONL produit par build_book.py")
    ap.add_argument("--net", default="net_v9")
    ap.add_argument("--out", default=str(ROOT / "tools" / "gui" / "book.json"))
    ap.add_argument("--pv", type=int, default=5, help="longueur des variantes conservées")
    ap.add_argument("--stats", help="parties de tools/book/book_stats.py (score pratique de chaque coup)")
    a = ap.parse_args()
    stats, games = load_stats(a.stats) if a.stats else ({}, 0)
    nodes = {}
    with open(a.nodes) as f:
        for line in f:
            n = json.loads(line)
            if n["lines"]:
                lines = sorted(n["lines"], key=lambda l: -l["score"])  # MultiPV pas toujours trié au dernier palier
                key = " ".join(n["moves"])
                st = stats.get(key, {})
                nodes[key] = {"d": n["depth"], "l": [[l["move"], l["score"], l["pv"][:a.pv]]
                                                     + ([st[l["move"]]] if l["move"] in st else []) for l in lines]}
    book = {"_doc": "Base d'ouvertures de Fanoron-Tsivy (position initiale, règles standard) : analyse MultiPV par "
                    "tools/book/build_book.py, compilée par tools/book/compile_book.py. Scores du point de vue du camp au trait.",
            "net": a.net, "date": datetime.date.today().isoformat(), "positions": len(nodes), "games": games,
            "nodes": nodes}
    Path(a.out).write_text(json.dumps(book, ensure_ascii=False, separators=(",", ":")))
    print(f"{len(nodes)} positions, {games} parties -> {a.out} ({Path(a.out).stat().st_size / 1e6:.1f} Mo)")


if __name__ == "__main__":
    main()
