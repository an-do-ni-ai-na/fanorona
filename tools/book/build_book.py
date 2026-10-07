#!/usr/bin/env python3
"""Construction d'une base d'ouvertures de Fanoron-Tsivy par analyse profonde, répartie sur plusieurs machines.

Exploration en largeur depuis la position de départ : chaque position est analysée en MultiPV (plusieurs meilleurs
coups avec leur évaluation), puis on approfondit les coups proches du meilleur. Chaque position analysée est
ajoutée à un fichier JSONL : on peut interrompre et relancer, le travail déjà fait est repris. Les moteurs
gardent leur table de transposition d'une position à l'autre (les positions voisines s'aident).

    python3 tools/book/build_book.py ./fanorona --net checkpoints/net_v9.nnue \\
        --hosts "root@10.10.10.190:5,root@10.10.10.191:3" --out data/book_nodes.jsonl

Chaque ligne du JSONL : {"moves": [...], "ply": n, "depth": d, "lines": [{"move", "score", "pv"}, ...]},
scores en centipions du point de vue du camp au trait (mats : ±30000 - distance). tools/book/compile_book.py
en tire tools/gui/book.json pour l'interface.
"""
import argparse
import collections
import json
import os
import subprocess
import threading
import time
import uuid

MATE = 30000


def parse_info(line):
    """(multipv, score, pv) d'une ligne "info depth ... multipv k score cp X ... pv ...", ou None."""
    t = line.split()
    if "pv" not in t or "score" not in t:
        return None
    k = int(t[t.index("multipv") + 1]) if "multipv" in t else 1
    i = t.index("score")
    kind, val = t[i + 1], int(t[i + 2])
    score = val if kind == "cp" else (MATE - abs(val)) * (1 if val > 0 else -1)
    return k, score, t[t.index("pv") + 1:]


class Worker:
    """Un moteur persistant, local ou distant (binaire et réseau copiés une fois par machine)."""

    def __init__(self, cmd, net, multipv, hash_mb):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        for c in (f"setoption name EvalFile value {net}", "setoption name UseNNUE value true",
                  f"setoption name MultiPV value {multipv}", f"setoption name Hash value {hash_mb}"):
            self.send(c)

    def send(self, c):
        self.p.stdin.write(c + "\n")
        self.p.stdin.flush()

    def analyse(self, moves, depth):
        self.send("position startpos" + (" moves " + " ".join(moves) if moves else ""))
        self.send(f"go depth {depth}")
        best = {}
        for line in self.p.stdout:
            if line.startswith("info depth "):
                r = parse_info(line)
                if r and int(line.split()[2]) == depth:
                    best[r[0]] = r
            elif line.startswith("info string illegal"):
                raise RuntimeError(line.strip())
            elif line.startswith("bestmove"):
                break
        lines = [{"move": pv[0], "score": s, "pv": pv[:6]} for _, (k, s, pv) in sorted(best.items())]
        return sorted(lines, key=lambda l: -l["score"])  # les lignes secondaires ne sont pas toujours triées

    def close(self):
        try:
            self.send("quit")
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def prepare_host(spec, args):
    """Commande de lancement du moteur et chemin du réseau pour une machine ("local" ou "user@hôte")."""
    if spec == "local":
        return [os.path.abspath(args.engine)], os.path.abspath(args.net)
    key = os.path.expanduser(args.ssh_key)
    ssh = ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30"]
    d = f"/tmp/fanorona-book-{uuid.uuid4().hex[:8]}"
    subprocess.run([*ssh, spec, f"mkdir -p {d}"], check=True)
    for f in (args.engine, args.net):
        subprocess.run(["scp", "-q", "-i", key, "-o", "BatchMode=yes", f, f"{spec}:{d}/"], check=True)
    return [*ssh, spec, f"{d}/{os.path.basename(args.engine)}"], f"{d}/{os.path.basename(args.net)}"


def is_reply_node(node, done, args):
    """Position atteinte par un coup que le livre peut jouer (à moins de --window du meilleur de la position
    précédente) : c'est à l'adversaire de jouer, et il peut s'écarter n'importe comment."""
    if node["ply"] == 0:
        return False
    parent = done.get(" ".join(node["moves"][:-1]))
    if not parent or not parent["lines"]:
        return False
    best = max(l["score"] for l in parent["lines"])
    return any(l["move"] == node["moves"][-1] and l["score"] >= best - args.window for l in parent["lines"])


def children(node, args, done=None):
    """Coups à approfondir : tous au premier demi-coup, sinon ceux à moins de --window du meilleur (au plus
    --max-children), tant que la position n'est pas déjà décidée. Avec --replies : après un coup jouable par le
    livre, toutes les réponses analysées (MultiPV), y compris les mauvaises, pour que le livre sache les punir."""
    lines = sorted(node["lines"], key=lambda l: -l["score"])
    if not lines or node["ply"] >= args.max_ply:
        return []
    best = lines[0]["score"]
    if node["ply"] > 0 and abs(best) > args.decided:
        return []
    if node["ply"] == 0:
        return [l["move"] for l in lines]
    if args.replies and done is not None and is_reply_node(node, done, args):
        return [l["move"] for l in lines]
    return [l["move"] for l in lines if l["score"] >= best - args.window][:args.max_children]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("engine")
    ap.add_argument("--net", required=True)
    ap.add_argument("--hosts", default="local:4")
    ap.add_argument("--ssh-key", default="~/.ssh/id_fanorona_match")
    ap.add_argument("--out", default="data/book_nodes.jsonl")
    ap.add_argument("--depth", type=int, default=14)
    ap.add_argument("--root-depth", type=int, default=16, help="profondeur pour les deux premiers demi-coups")
    ap.add_argument("--multipv", type=int, default=6)
    ap.add_argument("--max-ply", type=int, default=10)
    ap.add_argument("--window", type=int, default=60, help="écart maximal au meilleur coup pour approfondir (cp)")
    ap.add_argument("--max-children", type=int, default=4)
    ap.add_argument("--decided", type=int, default=400, help="au-delà de cet écart (cp), position non approfondie")
    ap.add_argument("--max-nodes", type=int, default=20000)
    ap.add_argument("--replies", action="store_true",
                    help="après un coup jouable par le livre, approfondir toutes les réponses analysées")
    ap.add_argument("--hash", type=int, default=128)
    args = ap.parse_args()

    done = {}
    if os.path.exists(args.out):
        with open(args.out) as f:
            for line in f:
                n = json.loads(line)
                done[" ".join(n["moves"])] = n
    queue = collections.deque()
    seen = set(done)
    if "" not in done:
        queue.append([])
        seen.add("")
    for key in sorted(done, key=lambda k: len(k.split())):  # reprise : enfants pas encore analysés
        for m in children(done[key], args, done):
            child = done[key]["moves"] + [m]
            ck = " ".join(child)
            if ck not in seen:
                seen.add(ck)
                queue.append(child)
    print(f"{len(done)} positions déjà analysées, {len(queue)} en attente", flush=True)

    lock = threading.Lock()
    state = {"busy": 0, "count": len(done), "t0": time.time()}
    out = open(args.out, "a")

    def run(spec):
        cmd, net = prepare_host(spec, args)
        w = Worker(cmd, net, args.multipv, args.hash)
        try:
            while True:
                with lock:
                    if not queue:
                        if state["busy"] == 0 or state["count"] >= args.max_nodes:
                            return
                        moves = None
                    elif state["count"] >= args.max_nodes:
                        return
                    else:
                        moves = queue.popleft()
                        state["busy"] += 1
                if moves is None:
                    time.sleep(2)  # d'autres machines peuvent encore ajouter des enfants
                    continue
                ply = len(moves)
                depth = args.root_depth if ply <= 1 else args.depth
                lines = w.analyse(moves, depth)
                node = {"moves": moves, "ply": ply, "depth": depth, "lines": lines}
                with lock:
                    done[" ".join(moves)] = node
                    out.write(json.dumps(node) + "\n")
                    out.flush()
                    state["count"] += 1
                    state["busy"] -= 1
                    for m in children(node, args, done):
                        child = moves + [m]
                        ck = " ".join(child)
                        if ck not in seen:
                            seen.add(ck)
                            queue.append(child)
                    if state["count"] % 25 == 0:
                        el = time.time() - state["t0"]
                        print(f"{state['count']} positions, {len(queue)} en attente, demi-coup {ply}, "
                              f"{el / 60:.0f} min", flush=True)
        finally:
            w.close()

    slots = []
    for part in args.hosts.split(","):
        spec, _, n = part.rpartition(":")
        slots += [spec] * int(n)
    threads = [threading.Thread(target=run, args=(s,)) for s in slots]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"terminé : {state['count']} positions analysées -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
