#!/usr/bin/env python3
"""Réglage SPSA des paramètres de recherche (options UCI de Search::tune_params), parties réparties par SSH.

À chaque itération k, on tire un sens aléatoire (±1) par paramètre, on joue une paire de parties (même ouverture,
couleurs inversées) entre theta + c_k·sens et theta - c_k·sens, puis on déplace theta dans le sens du gagnant :
    theta += r_k · c_k · (points de theta+ - points de theta-) · sens
avec c_k = c / k^0.101 et r_k = a_k / c_k², a_k = a / (A + k)^0.602 (conventions de fishtest : c_end = pas de
perturbation en fin de réglage, r_end = taux d'apprentissage en fin de réglage). Les machines jouent en même
temps, chacune met theta à jour dès qu'une paire se termine.

Ouvertures : positions de la base d'ouvertures (tools/gui/book.json) entre --open-min et --open-max demi-coups, à
évaluation pas trop déséquilibrée.

    python3 tools/spsa.py ./fanorona --net checkpoints/net_v9.nnue --iterations 20000 \\
        --hosts "root@10.10.10.190:5,root@10.10.10.191:3" --out data/spsa_run1

Reprise : l'état (theta, k) est sauvegardé dans <out>/state.json ; <out>/history.jsonl garde la trajectoire.
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

ROOT = Path(__file__).resolve().parent.parent


class Engine:
    def __init__(self, cmd, options):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        for k, v in options:
            self.send(f"setoption name {k} value {v}")

    def send(self, c):
        self.p.stdin.write(c + "\n")
        self.p.stdin.flush()

    def query(self, c, prefix):
        self.send(c)
        for line in self.p.stdout:
            if line.startswith(prefix):
                return line.strip()
        raise RuntimeError("moteur arrêté")

    def options(self):
        """{nom: (défaut, min, max)} des options spin annoncées par "uci"."""
        self.send("uci")
        opts = {}
        for line in self.p.stdout:
            if line.startswith("uciok"):
                return opts
            t = line.split()
            if t[:2] == ["option", "name"] and "spin" in t:
                opts[t[2]] = tuple(int(t[t.index(k) + 1]) for k in ("default", "min", "max"))
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
    d = f"/tmp/fanorona-spsa-{uuid.uuid4().hex[:8]}"
    subprocess.run([*ssh, spec, f"mkdir -p {d}"], check=True)
    for f in (args.engine, args.net):
        subprocess.run(["scp", "-q", "-i", key, "-o", "BatchMode=yes", f, f"{spec}:{d}/"], check=True)
    return [*ssh, spec, f"{d}/{os.path.basename(args.engine)}"], f"{d}/{os.path.basename(args.net)}"


def play_game(white, black, opening, movetime, max_plies=400):
    """Résultat du point de vue des Blancs : 1, 0.5 ou 0. `white` sert aussi d'arbitre (commande status)."""
    moves = list(opening)
    while True:
        pos = "position startpos" + (" moves " + " ".join(moves) if moves else "")
        white.send(pos)
        status = white.query("status", "status").split(" ", 1)[1]
        if status != "ongoing" or len(moves) >= max_plies:
            return 1.0 if status == "white wins" else 0.0 if status == "black wins" else 0.5
        eng = white if len(moves) % 2 == 0 else black
        if eng is black:
            eng.send(pos)
        m = eng.query(f"go movetime {movetime}", "bestmove").split()[1]
        if m == "(none)":
            return 0.0 if eng is white else 1.0
        moves.append(m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("engine")
    ap.add_argument("--net", required=True)
    ap.add_argument("--hosts", default="local:2")
    ap.add_argument("--ssh-key", default="~/.ssh/id_fanorona_match")
    ap.add_argument("--out", default="data/spsa_run")
    ap.add_argument("--params", default=None, help="paramètres réglés, séparés par des virgules (défaut : tous)")
    ap.add_argument("--iterations", type=int, default=20000, help="nombre de paires de parties")
    ap.add_argument("--movetime", type=int, default=50)
    ap.add_argument("--c-frac", type=float, default=0.05, help="c_end = fraction de l'intervalle [min, max]")
    ap.add_argument("--r-end", type=float, default=0.002)
    ap.add_argument("--book", default=str(ROOT / "tools" / "gui" / "book.json"))
    ap.add_argument("--open-min", type=int, default=4)
    ap.add_argument("--open-max", type=int, default=8)
    ap.add_argument("--open-window", type=int, default=150, help="|évaluation| maximale de l'ouverture (cp)")
    ap.add_argument("--hash", type=int, default=16)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    probe = Engine([os.path.abspath(args.engine)], [])
    spins = probe.options()
    probe.close()
    tunable = ["QsDeltaPiece", "QsDeltaBase", "RfpDepth", "RfpMargin", "NmpBase", "FutDepth", "FutMargin", "LmpBase",
               "LmrDiv", "LmrHistory", "AspDelta", "HistCap"]
    names = args.params.split(",") if args.params else [n for n in tunable if n in spins]
    N = args.iterations
    A = 0.1 * N
    P = {}
    for n in names:
        d, lo, hi = spins[n]
        c_end = max(args.c_frac * (hi - lo), 0.5)
        a_end = args.r_end * c_end ** 2
        P[n] = {"min": lo, "max": hi, "def": d, "c": c_end * N ** 0.101, "a": a_end * (A + N) ** 0.602}

    state_file = out / "state.json"
    if state_file.exists():
        st = json.loads(state_file.read_text())
        theta, k0, stats = st["theta"], st["k"], st["stats"]
    else:
        theta, k0, stats = {n: float(P[n]["def"]) for n in names}, 0, {"pairs": 0, "plus": 0.0, "minus": 0.0}
    print(f"paramètres : {', '.join(f'{n}={theta[n]:.1f}' for n in names)} ; reprise à k={k0}/{N}", flush=True)

    nodes = json.loads(Path(args.book).read_text())["nodes"]
    openings = [k.split() for k, v in nodes.items()
                if args.open_min <= len(k.split()) <= args.open_max and v["l"] and abs(v["l"][0][1]) <= args.open_window]
    print(f"{len(openings)} ouvertures", flush=True)

    lock = threading.Lock()
    sched = {"next": k0 + 1, "done": k0, "t0": time.time(), "k0": k0}
    hist = open(out / "history.jsonl", "a")

    def save():
        state_file.write_text(json.dumps({"theta": theta, "k": sched["done"], "stats": stats, "params": P}, indent=1))

    def run(spec, slot):
        rng = random.Random(slot * 7919 + int(time.time()))
        cmd, net = prepare_host(spec, args)
        base = [("EvalFile", net), ("UseNNUE", "true"), ("Hash", args.hash)]
        plus, minus = Engine(cmd, base), Engine(cmd, base)
        try:
            while True:
                with lock:
                    k = sched["next"]
                    if k > N:
                        return
                    sched["next"] += 1
                    flip = {n: rng.choice((-1, 1)) for n in names}
                    ck = {n: P[n]["c"] / k ** 0.101 for n in names}
                    tp = {n: min(max(theta[n] + ck[n] * flip[n], P[n]["min"]), P[n]["max"]) for n in names}
                    tm = {n: min(max(theta[n] - ck[n] * flip[n], P[n]["min"]), P[n]["max"]) for n in names}
                for eng, t in ((plus, tp), (minus, tm)):
                    eng.send("ucinewgame")
                    for n in names:
                        eng.send(f"setoption name {n} value {round(t[n])}")
                op = rng.choice(openings)
                s1 = play_game(plus, minus, op, args.movetime)          # theta+ avec les Blancs
                s2 = 1.0 - play_game(minus, plus, op, args.movetime)    # theta+ avec les Noirs
                result = (s1 + s2) - (2 - s1 - s2)  # points de theta+ moins points de theta-, dans [-2, 2]
                with lock:
                    for n in names:
                        ak = P[n]["a"] / (A + k) ** 0.602
                        rk = ak / ck[n] ** 2
                        theta[n] = min(max(theta[n] + rk * ck[n] * result * flip[n], P[n]["min"]), P[n]["max"])
                    stats["pairs"] += 1
                    stats["plus"] += s1 + s2
                    stats["minus"] += 2 - s1 - s2
                    sched["done"] = max(sched["done"], k)
                    if stats["pairs"] % 20 == 0:
                        hist.write(json.dumps({"k": k, "t": round(time.time()), "theta": {n: round(theta[n], 2) for n in names}}) + "\n")
                        hist.flush()
                        save()
                    if stats["pairs"] % 200 == 0:
                        el = time.time() - sched["t0"]
                        rate = (sched["done"] - sched["k0"]) / el * 60
                        print(f"k={k}/{N} ({rate:.0f} paires/min, reste ~{(N - k) / max(rate, 1e-9) / 60:.1f} h) "
                              + " ".join(f"{n}={theta[n]:.1f}" for n in names), flush=True)
        finally:
            plus.close()
            minus.close()

    slots = []
    for part in args.hosts.split(","):
        spec, _, n = part.rpartition(":")
        slots += [spec] * int(n)
    threads = [threading.Thread(target=run, args=(s, i)) for i, s in enumerate(slots)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    save()
    final = ",".join(f"{n}={round(theta[n])}" for n in names)
    (out / "final_opts.txt").write_text(final + "\n")
    print(f"terminé : {final}", flush=True)


if __name__ == "__main__":
    main()
