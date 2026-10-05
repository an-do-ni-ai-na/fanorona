#!/usr/bin/env python3
"""Score pratique de la base d'ouvertures : parties rapides moteur contre moteur, réparties sur plusieurs machines.

Chaque partie part de la position initiale et suit la base (tools/gui/book.json) : à chaque position connue, un
coup est tiré parmi ceux de la base avec un poids exp((score - meilleur) / --temperature) (les coups à plus de
--window du meilleur sont écartés). Hors de la base, le moteur joue les deux camps jusqu'à la fin de la partie.
On obtient, pour chaque coup de la base, les victoires/nulles/défaites de celui qui l'a joué : le score
pratique, complément de l'évaluation (un coup à +0,2 peut gagner plus souvent qu'un coup à +0,4 s'il pose des
problèmes plus durs à résoudre).

    python3 tools/book/book_stats.py ./fanorona --net checkpoints/net_v9.nnue --games 20000 \\
        --hosts "root@10.10.10.190:5,root@10.10.10.191:3" --out data/book_games.jsonl

Chaque ligne du JSONL : {"book": [coups suivis dans la base], "plies": n, "result": "white wins" | "black wins" |
"draw ..."}. Reprise : les parties déjà écrites comptent dans --games. tools/book/compile_book.py --stats
agrège ces parties dans book.json.
"""
import argparse
import json
import math
import os
import random
import subprocess
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


class Engine:
    """Moteur persistant (local ou distant) : arbitre (commande status) et joueur des deux camps."""

    def __init__(self, cmd, net, hash_mb):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        for c in (f"setoption name EvalFile value {net}", "setoption name UseNNUE value true",
                  f"setoption name Hash value {hash_mb}"):
            self.send(c)

    def send(self, c):
        self.p.stdin.write(c + "\n")
        self.p.stdin.flush()

    def query(self, c, prefix):
        self.send(c)
        for line in self.p.stdout:
            if line.startswith(prefix):
                return line.strip()
        raise RuntimeError("moteur arrêté")

    def close(self):
        try:
            self.send("quit")
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def prepare_host(spec, args):
    if spec == "local":
        return [os.path.abspath(args.engine)], os.path.abspath(args.net)
    key = os.path.expanduser(args.ssh_key)
    ssh = ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30"]
    d = f"/tmp/fanorona-bookstats-{uuid.uuid4().hex[:8]}"
    subprocess.run([*ssh, spec, f"mkdir -p {d}"], check=True)
    for f in (args.engine, args.net):
        subprocess.run(["scp", "-q", "-i", key, "-o", "BatchMode=yes", f, f"{spec}:{d}/"], check=True)
    return [*ssh, spec, f"{d}/{os.path.basename(args.engine)}"], f"{d}/{os.path.basename(args.net)}"


def book_choice(lines, rng, args):
    """Coup tiré dans la base : poids exp((score - meilleur) / T), coups à plus de --window du meilleur écartés."""
    best = lines[0][1]
    cand = [(m, math.exp((s - best) / args.temperature)) for m, s, *_ in lines if s >= best - args.window]
    r = rng.random() * sum(w for _, w in cand)
    for m, w in cand:
        r -= w
        if r <= 0:
            return m
    return cand[-1][0]


def play(eng, nodes, rng, args):
    moves, book = [], []
    while True:
        pos = "position startpos" + (" moves " + " ".join(moves) if moves else "")
        eng.send(pos)
        status = eng.query("status", "status").split(" ", 1)[1]
        if status != "ongoing":
            return {"book": book, "plies": len(moves), "result": status}
        if len(moves) >= args.max_plies:
            return {"book": book, "plies": len(moves), "result": "draw (limite de demi-coups)"}
        node = nodes.get(" ".join(moves)) if len(book) == len(moves) else None
        if node and node["l"]:
            m = book_choice(node["l"], rng, args)
            book.append(m)
        else:
            m = eng.query(f"go movetime {args.movetime}", "bestmove").split()[1]
            if m == "(none)":
                return {"book": book, "plies": len(moves), "result": "black wins" if len(moves) % 2 == 0 else "white wins"}
        moves.append(m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("engine")
    ap.add_argument("--net", required=True)
    ap.add_argument("--book", default=str(ROOT / "tools" / "gui" / "book.json"))
    ap.add_argument("--hosts", default="local:4")
    ap.add_argument("--ssh-key", default="~/.ssh/id_fanorona_match")
    ap.add_argument("--out", default="data/book_games.jsonl")
    ap.add_argument("--games", type=int, default=20000, help="nombre total de parties (reprise comprise)")
    ap.add_argument("--movetime", type=int, default=50, help="temps par coup hors de la base (ms)")
    ap.add_argument("--temperature", type=float, default=100, help="température du tirage dans la base (cp)")
    ap.add_argument("--window", type=int, default=300, help="coups de la base écartés au-delà de cet écart (cp)")
    ap.add_argument("--max-plies", type=int, default=400)
    ap.add_argument("--hash", type=int, default=32)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    nodes = json.loads(Path(args.book).read_text())["nodes"]
    done = sum(1 for _ in open(args.out)) if os.path.exists(args.out) else 0
    print(f"{len(nodes)} positions dans la base, {done} parties déjà jouées", flush=True)
    lock = threading.Lock()
    state = {"next": done, "count": done, "t0": time.time(), "w": 0, "d": 0, "b": 0}
    out = open(args.out, "a")
    seed = args.seed if args.seed is not None else int(time.time())

    def run(spec, k):
        rng = random.Random(seed * 1000 + k)
        cmd, net = prepare_host(spec, args)
        eng = Engine(cmd, net, args.hash)
        try:
            while True:
                with lock:
                    if state["next"] >= args.games:
                        return
                    state["next"] += 1
                eng.send("ucinewgame")
                g = play(eng, nodes, rng, args)
                with lock:
                    out.write(json.dumps(g) + "\n")
                    out.flush()
                    state["count"] += 1
                    state["w" if g["result"] == "white wins" else "b" if g["result"] == "black wins" else "d"] += 1
                    if state["count"] % 100 == 0:
                        el = time.time() - state["t0"]
                        rate = (state["count"] - done) / el * 60
                        print(f"{state['count']} parties (+{state['w']} ={state['d']} -{state['b']} pour les Blancs), "
                              f"{rate:.0f}/min, reste ~{(args.games - state['count']) / max(rate, 1e-9):.0f} min", flush=True)
        finally:
            eng.close()

    slots = []
    for part in args.hosts.split(","):
        spec, _, n = part.rpartition(":")
        slots += [spec] * int(n)
    threads = [threading.Thread(target=run, args=(s, k)) for k, s in enumerate(slots)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"terminé : {state['count']} parties -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
