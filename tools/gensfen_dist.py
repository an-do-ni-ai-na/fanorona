#!/usr/bin/env python3
"""Génération de données NNUE (`gensfen`) répartie sur plusieurs machines, avec suivi live.

    python3 tools/gensfen_dist.py ./fanorona --count 12000000 --depth 6 --opening-plies 8 \
        --opts "UseNNUE=true,EvalFile=checkpoints/net_v6.nnue" --out data/gensfen_gen6.txt \
        --hosts "local:5,root@10.10.10.190:5,root@10.10.10.191:3"

Remplace le workflow manuel (lancer gensfen sur chaque nœud, rapatrier par scp, concaténer) : `--count` est
réparti entre tous les processus au prorata, chaque processus écrit son propre fichier partiel (sur sa machine),
puis les parties sont rapatriées et concaténées dans `--out` (ajout en fin de fichier, comme gensfen lui-même).
Hôtes distants comme dans match.py : binaire et fichiers d'options (EvalFile) copiés par scp, même jeu
d'instructions requis (-march=native), clé --ssh-key (~/.ssh/id_fanorona_match). Garder N <= nombre de cœurs.

Chaque ligne de progression de gensfen ("info string gensfen W/T positions, G parties, R pos/s", toutes les 20
parties) est journalisée en JSONL (/var/log/fanorona/<run_id>.jsonl) avec le nom de la machine, pour le
dashboard Grafana "Fanorona - Recherche live" (positions/s et avancement par machine).
"""
import argparse
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid

from match import LockedMetrics, parse_opts
from metrics_logger import MetricsLogger, new_run_id

PROGRESS_RE = re.compile(r"^info string gensfen (\d+)/(\d+) positions, (\d+) parties, (\d+) pos/s")
DONE_RE = re.compile(r"^info string gensfen terminé : (\d+) positions")


class Machine:
    """Machine d'exécution, locale ou distante (binaire et fichiers d'options copiés dans un répertoire temporaire)."""

    def __init__(self, spec, args):
        self.local = spec == "local"
        self.engine, self.opts = args.engine, parse_opts(args.opts)
        self.prefix, self.ssh = (), None
        if self.local:
            self.name, self.dir = socket.gethostname(), os.path.dirname(os.path.abspath(args.out))
            return
        key = os.path.expanduser(args.ssh_key)
        self.key, self.spec = key, spec
        self.ssh = ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30"]
        self.prefix = (*self.ssh, spec)
        self.dir = f"/tmp/fanorona-gensfen-{uuid.uuid4().hex[:8]}"
        self.name = subprocess.run([*self.prefix, f"mkdir -p {self.dir} && hostname"], check=True,
                                   capture_output=True, text=True).stdout.strip() or spec
        copied = {}
        for local in (self.engine,) + tuple(v for _, v in self.opts if os.path.isfile(v)):
            if local not in copied:
                copied[local] = f"{self.dir}/{len(copied)}_{os.path.basename(local)}"
                subprocess.run(["scp", "-q", "-i", key, "-o", "BatchMode=yes", local, f"{spec}:{copied[local]}"],
                               check=True)
        self.engine = copied[self.engine]
        self.opts = [(k, copied.get(v, v)) for k, v in self.opts]

    def fetch(self, remote, local):
        if self.local:
            return
        subprocess.run(["scp", "-q", "-i", self.key, "-o", "BatchMode=yes", f"{self.spec}:{remote}", local], check=True)

    def cleanup(self):
        if not self.local:
            subprocess.run([*self.prefix, f"rm -rf {self.dir}"], check=False)


def split_count(total, n):
    """Répartit `total` positions entre `n` processus (les premiers reçoivent le reste)."""
    return [total // n + (i < total % n) for i in range(n)]


def run(args, metrics):
    machines, slots = {}, []
    for part in args.hosts.split(","):
        spec, _, n = part.rpartition(":")
        machines.setdefault(spec, Machine(spec, args))
        slots += [machines[spec]] * int(n)
    counts = split_count(args.count, len(slots))
    per_host = {m.name: sum(s is m for s in slots) for m in machines.values()}
    print(f"{len(slots)} processus gensfen : " + ", ".join(f"{h} x{n}" for h, n in per_host.items()), flush=True)
    metrics.log("gensfen_start", hosts=per_host, count=args.count, depth=args.depth,
                opening_plies=args.opening_plies, opts=args.opts or "")

    tag = uuid.uuid4().hex[:6]
    errors, written = [], [0] * len(slots)
    lock = threading.Lock()

    def worker(i, m):
        out = f"{m.dir}/{os.path.basename(args.out)}.part-{tag}-{i}"
        cmds = [f"setoption name {k} value {v}" for k, v in m.opts]
        cmds.append(f"gensfen count {counts[i]} depth {args.depth} opening-plies {args.opening_plies} out {out}")
        try:
            # stdin reste ouvert jusqu'à la fin de gensfen : EOF == quit pour le moteur
            p = subprocess.Popen([*m.prefix, m.engine], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                 bufsize=1)
            p.stdin.write("\n".join(cmds) + "\n")
            p.stdin.flush()
            for line in p.stdout:
                if mo := PROGRESS_RE.match(line):
                    w, target, games, rate = map(int, mo.groups())
                    written[i] = w
                    metrics.log("gensfen_progress", host=m.name, proc=i, written=w, target=target, games=games,
                                pos_s=rate)
                elif mo := DONE_RE.match(line):
                    written[i] = int(mo.group(1))
                    metrics.log("gensfen_proc_done", host=m.name, proc=i, written=written[i])
                    with lock:
                        print(f"{m.name} #{i} terminé : {written[i]} positions "
                              f"(total {sum(written)}/{args.count})", flush=True)
                    p.stdin.write("quit\n")
                    p.stdin.flush()
                elif "impossible d'ouvrir" in line:
                    raise RuntimeError(f"{m.name}: {line.strip()}")
            if p.wait() != 0:
                raise RuntimeError(f"{m.name} #{i}: le moteur s'est arrêté avec le code {p.returncode}")
            m.fetch(out, os.path.join(os.path.dirname(os.path.abspath(args.out)), os.path.basename(out)))
        except Exception as e:
            with lock:
                errors.append(e)

    t0 = time.time()
    threads = [threading.Thread(target=worker, args=(i, m)) for i, m in enumerate(slots)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for m in machines.values():
        m.cleanup()

    out_dir = os.path.dirname(os.path.abspath(args.out))
    parts = sorted((f for f in os.listdir(out_dir) if f.startswith(f"{os.path.basename(args.out)}.part-{tag}-")),
                   key=lambda f: int(f.rsplit("-", 1)[1]))
    with open(args.out, "ab") as dst:
        for f in parts:
            with open(os.path.join(out_dir, f), "rb") as src:
                shutil.copyfileobj(src, dst)
            os.remove(os.path.join(out_dir, f))
    elapsed = time.time() - t0
    metrics.log("gensfen_done", positions=sum(written), seconds=round(elapsed), out=args.out,
                errors=len(errors))
    print(f"{sum(written)} positions en {elapsed / 60:.1f} min -> {args.out} ({len(parts)} fichiers concaténés)")
    if errors:
        raise errors[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("engine", help="binaire du moteur (copié sur les hôtes distants)")
    ap.add_argument("--count", type=int, required=True, help="nombre total de positions, réparti entre processus")
    ap.add_argument("--depth", type=int, default=6)
    ap.add_argument("--opening-plies", type=int, default=8)
    ap.add_argument("--opts", default=None, help="options UCI du professeur, ex. UseNNUE=true,EvalFile=net.nnue")
    ap.add_argument("--out", required=True, help="fichier de sortie (ajout en fin de fichier)")
    ap.add_argument("--hosts", default="local:5", help='machines et processus, ex. "local:5,root@10.10.10.190:4"')
    ap.add_argument("--ssh-key", default="~/.ssh/id_fanorona_match", help="clé SSH vers les hôtes distants")
    ap.add_argument("--no-live-log", action="store_true", help="désactive la journalisation JSONL live")
    ap.add_argument("--run-id", default=None, help="identifiant de run pour le suivi live (auto par défaut)")
    args = ap.parse_args()

    metrics = MetricsLogger("gensfen", run_id=args.run_id or new_run_id("gensfen"), enabled=not args.no_live_log)
    if metrics.enabled:
        print(f"suivi live : run_id={metrics.run_id}")
    metrics = LockedMetrics(metrics)
    try:
        run(args, metrics)
    finally:
        metrics.close()


if __name__ == "__main__":
    main()
