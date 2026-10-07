#!/usr/bin/env python3
"""Vérifie les exercices du tutoriel (lessons.json) contre le moteur.

Exercices « solo » (reach, clear, captures sans niveau, telo_win) : les Noirs ne jouent pas ; on explore toutes
les suites de coups blancs (le trait est rendu aux Blancs après chaque coup) jusqu'à `max` coups et on affiche
le nombre minimal de coups (par) nécessaire. Exercices contre le moteur : la position doit être gagnante
(ou au moins jouable) pour les Blancs d'après une recherche.

    python3 tools/gui/verify_lessons.py [--engine ./fanorona]
"""
import argparse
import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
args = None


def run(cmds):
    return subprocess.run([args.engine], input="\n".join(cmds) + "\nquit\n", capture_output=True, text=True,
                          timeout=60).stdout.splitlines()


def opts(step):
    o = []
    if (step.get("game") or "tsivy") != "tsivy":
        o.append("setoption name Variant value " + step["game"])
    if step.get("vela"):
        o.append("setoption name Vela value " + ("white" if step["vela"] == "W" else "black"))
    return o


@lru_cache(maxsize=None)
def children(key, fen):
    """(coup, fen résultante avec trait rendu aux Blancs) pour chaque coup légal des Blancs."""
    step = json.loads(key)
    lines = run(opts(step) + [f"position fen {fen}", "moves"])
    moves = [l for l in lines if l and not l.startswith(("Fanorona", "info"))][-1].split()
    out = []
    for m in moves:
        l2 = run(opts(step) + [f"position fen {fen} moves {m}", "d"])
        f2 = next(l[5:].strip() for l in l2 if l.startswith("Fen: "))
        board, _side, *rest = f2.split()
        out.append((m, f"{board} w"))
    return out


def board_of(fen):
    rows = fen.split()[0].split("/")
    h = len(rows)
    cells = {}
    for i, row in enumerate(rows):
        y, x = h - 1 - i, 0
        for ch in row:
            if ch.isdigit():
                x += int(ch)
            else:
                cells["abcdefghi"[x] + str(y + 1)] = ch
                x += 1
    return cells


def goal_met(goal, start, fen):
    b = board_of(fen)
    nb = sum(1 for c in b.values() if c == "B")
    if goal["type"] == "reach":
        return b.get(goal["target"]) == "W"
    if goal["type"] == "clear":
        return nb == 0
    if goal["type"] == "captures":
        return nb <= sum(1 for c in board_of(start).values() if c == "B") - goal["n"]
    if goal["type"] == "telo_win":
        lines = [("a1", "b1", "c1"), ("a2", "b2", "c2"), ("a3", "b3", "c3"), ("a1", "a2", "a3"), ("b1", "b2", "b3"),
                 ("c1", "c2", "c3"), ("a1", "b2", "c3"), ("c1", "b2", "a3")]
        return any(all(b.get(s) == "W" for s in l) for l in lines)
    raise ValueError(goal["type"])


def solve(step):
    key = json.dumps({k: step.get(k) for k in ("game", "vela")})
    goal, start = step["goal"], step["fen"]
    frontier, seen = [(start, [])], {start}
    for depth in range(1, goal["max"] + 1):
        nxt = []
        for fen, path in frontier:
            for m, f2 in children(key, fen):
                if goal_met(goal, start, f2):
                    return depth, path + [m]
                if f2 not in seen:
                    seen.add(f2)
                    nxt.append((f2, path + [m]))
        frontier = nxt
    return None, None


def run_go(cmds):
    """Comme run(), mais garde stdin ouvert jusqu'au bestmove (fin de stdin = quit = arrêt de la recherche)."""
    p = subprocess.Popen([args.engine], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    p.stdin.write("\n".join(cmds) + "\n")
    p.stdin.flush()
    lines = []
    for line in p.stdout:
        lines.append(line.rstrip("\n"))
        if line.startswith("bestmove"):
            break
    p.stdin.write("quit\n")
    p.stdin.close()
    p.wait(timeout=10)
    return lines


def engine_check(step):
    lines = run_go(opts(step) + [f"position fen {step['fen']}", "go depth 14" if step.get("game") != "telo" else "go"])
    info = [l for l in lines if l.startswith("info depth")]
    best = [l for l in lines if l.startswith("bestmove")]
    return (info[-1].split(" pv ")[0] if info else "?"), (best[-1] if best else "?")


def opening_check(step):
    """Leçon d'ouverture : la suite imposée doit être dans la base (tools/gui/book.json) ; coups acceptés."""
    book = HERE / "book.json"
    if not book.exists():
        return False, "book.json absent (non vérifiable)"
    nodes = json.loads(book.read_text())["nodes"]
    node = nodes.get(" ".join(step.get("line", [])))
    if not node or not node["l"]:
        return False, "suite hors de la base"
    best = node["l"][0][1]
    ok = [f"{m} {sc:+d}" for m, sc, *_ in node["l"] if sc >= best - step["goal"].get("tol", 30)]
    return True, "accepté au 1er coup : " + ", ".join(ok)


def main():
    global args
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default=str(HERE.parent.parent / "fanorona"))
    args = ap.parse_args()
    data = json.loads((HERE / "lessons.json").read_text())
    ok = True
    for ch in data["chapters"]:
        for st in ch["steps"]:
            g = st["goal"]
            solo = g["type"] in ("reach", "clear", "telo_win") or (g["type"] == "captures" and "level" not in g)
            if g["type"] == "opening":
                good, status = opening_check(st)
                ok &= good
            elif solo:
                par, line = solve(st)
                status = f"par {par} : {' '.join(line)}" if par else "IMPOSSIBLE"
                ok &= par is not None
            else:
                info, best = engine_check(st)
                status = f"contre le moteur — {info} — {best}"
            print(f"{st['id']:12} {g['type']:9} {status}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
