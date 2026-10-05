#!/usr/bin/env python3
"""Compile l'analyse de tools/book/build_book.py (JSONL) en tools/gui/book.json, lu par l'interface.

    python3 tools/book/compile_book.py data/book_nodes.jsonl --net net_v9

Format : {"net", "date", "positions", "nodes": {"<coups séparés par des espaces>": {"d": profondeur,
"l": [[coup, score, [variante]], ...]}}}, scores en centipions du point de vue du camp au trait.
"""
import argparse
import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("nodes", help="JSONL produit par build_book.py")
    ap.add_argument("--net", default="net_v9")
    ap.add_argument("--out", default=str(ROOT / "tools" / "gui" / "book.json"))
    ap.add_argument("--pv", type=int, default=5, help="longueur des variantes conservées")
    a = ap.parse_args()
    nodes = {}
    with open(a.nodes) as f:
        for line in f:
            n = json.loads(line)
            if n["lines"]:
                lines = sorted(n["lines"], key=lambda l: -l["score"])  # MultiPV pas toujours trié au dernier palier
                nodes[" ".join(n["moves"])] = {"d": n["depth"], "l": [[l["move"], l["score"], l["pv"][:a.pv]] for l in lines]}
    book = {"_doc": "Base d'ouvertures de Fanoron-Tsivy (position initiale, règles standard) : analyse MultiPV par "
                    "tools/book/build_book.py, compilée par tools/book/compile_book.py. Scores du point de vue du camp au trait.",
            "net": a.net, "date": datetime.date.today().isoformat(), "positions": len(nodes), "nodes": nodes}
    Path(a.out).write_text(json.dumps(book, ensure_ascii=False, separators=(",", ":")))
    print(f"{len(nodes)} positions -> {a.out} ({Path(a.out).stat().st_size / 1e6:.1f} Mo)")


if __name__ == "__main__":
    main()
