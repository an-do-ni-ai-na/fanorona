#!/usr/bin/env python3
"""Match entre deux versions du moteur (auto-jeu), façon "fishtest" minimal.

Mode parties fixes :
    python3 tools/match.py ./fanorona ./fanorona-old --games 20 --movetime 100

Mode SPRT (arrêt séquentiel dès que l'écart d'Elo est statistiquement tranché) :
    python3 tools/match.py ./fanorona ./fanorona-old --sprt --elo0 0 --elo1 5 --movetime 100

NNUE contre HCE (même binaire, NNUE est une option UCI à l'exécution) :
    python3 tools/match.py ./fanorona ./fanorona --sprt --elo0 0 --elo1 10 --movetime 100 \
        --engine1-opts "UseNNUE=true,EvalFile=checkpoints/net_v1.nnue"

Parties en parallèle (--concurrency N) : N paires de moteurs jouent simultanément, chaque fil prenant la paire de
parties suivante ; les ouvertures dépendent seulement de la graine et du numéro de paire (pas du parallélisme).
Plusieurs machines (--hosts "local:5,root@10.10.10.190:4,root@10.10.10.191:2") : chaque fil lance ses deux moteurs
sur son hôte par SSH (UCI passe par stdin/stdout), après copie des binaires et des fichiers d'options (EvalFile) ;
les deux moteurs d'une partie sont toujours sur la même machine, la partie reste équitable. Processeurs : même jeu
d'instructions requis (binaires compilés avec -march=native). Clé : --ssh-key (~/.ssh/id_fanorona_match).
Garder N <= nombre de cœurs - 1 : avec un temps fixe par coup, des moteurs qui se disputent un cœur jouent plus
faiblement (des deux côtés, donc sans biais, mais le test mesure alors une autre cadence).

Les parties démarrent depuis des ouvertures aléatoires (quelques coups au hasard, jouées par
paires pour que chaque moteur ait les deux couleurs). Les lignes UCI "info" de chaque moteur
sont journalisées en JSONL (/var/log/fanorona/<run_id>.jsonl par défaut) pour suivi live via
Grafana/Loki ; désactivable avec --no-live-log si le répertoire n'est pas accessible.
"""
import argparse
import os
import random
import subprocess
import threading
import uuid

from metrics_logger import MetricsLogger, new_run_id
from sprt import Sprt


def parse_opts(spec):
    """"Name1=Value1,Name2=Value2" -> [(Name1, Value1), (Name2, Value2)]. Sert à activer NNUE
    (UseNNUE=true,EvalFile=checkpoints/net.nnue) sur un binaire qui, par défaut, joue en HCE :
    NNUE est une option UCI à l'exécution, pas un binaire séparé."""
    if not spec:
        return []
    return [tuple(kv.split("=", 1)) for kv in spec.split(",")]


class Engine:
    def __init__(self, path, name, metrics, options=None, prefix=()):
        """`prefix` : commande de lancement à distance (ssh ... hôte), vide en local."""
        self.name = name
        self.metrics = metrics
        self.p = subprocess.Popen([*prefix, path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.send("uci")
        self.wait("uciok")
        for opt_name, value in options or []:
            self.send(f"setoption name {opt_name} value {value}")

    def send(self, cmd):
        self.p.stdin.write(cmd + "\n")
        self.p.stdin.flush()

    def wait(self, prefix, game=None, ply=None):
        # Ne journalise que la dernière ligne "info" (profondeur finale atteinte) d'une
        # recherche, pas chaque itération : pour un SPRT de milliers de parties, journaliser
        # chaque profondeur intermédiaire produirait des dizaines de Mo de logs par partie.
        last_info = None
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("engine died")
            if prefix and line.startswith("info"):
                last_info = line
                continue
            if line.startswith(prefix):
                if last_info is not None:
                    self.metrics.log_info_line(last_info, engine=self.name, game=game, ply=ply)
                return line.strip()

    def query(self, cmd, prefix, game=None, ply=None):
        self.send(cmd)
        return self.wait(prefix, game=game, ply=ply)

    def close(self):
        self.send("quit")
        self.p.wait(timeout=5)


def position_cmd(moves):
    return "position startpos" + (" moves " + " ".join(moves) if moves else "")


def random_opening(engine, plies, rng):
    moves = []
    for _ in range(plies):
        engine.send(position_cmd(moves))
        legal = engine.query("moves", "").split()
        if not legal:
            break
        moves.append(rng.choice(legal))
    return moves


def play_game(engines, opening, movetime, referee, game_id):
    moves = list(opening)
    while True:
        referee.send(position_cmd(moves))
        status = referee.query("status", "status").split(" ", 1)[1]
        if status != "ongoing":
            return status
        eng = engines[len(moves) % 2]
        eng.send(position_cmd(moves))
        best = eng.query(f"go movetime {movetime}", "bestmove", game=game_id, ply=len(moves)).split()[1]
        if best == "(none)":
            return "black wins" if len(moves) % 2 == 0 else "white wins"
        moves.append(best)


def game_result_for_engine1(res, e1_white):
    """Score du point de vue d'engine1 : 'win' | 'draw' | 'loss'."""
    if res.startswith("draw"):
        return "draw"
    return "win" if (res == "white wins") == e1_white else "loss"


class LockedMetrics:
    """Journal partagé entre fils : une écriture à la fois."""

    def __init__(self, metrics):
        self.m, self.lock = metrics, threading.Lock()
        self.enabled, self.run_id = metrics.enabled, metrics.run_id

    def log(self, *a, **k):
        with self.lock:
            self.m.log(*a, **k)

    def log_info_line(self, *a, **k):
        with self.lock:
            self.m.log_info_line(*a, **k)

    def close(self):
        self.m.close()


class Host:
    """Machine d'exécution : locale, ou distante (binaires et fichiers d'options copiés par scp)."""

    def __init__(self, spec, args):
        self.local = spec == "local"
        self.spec, self.args = spec, args
        self.e1, self.e2 = args.engine1, args.engine2
        self.o1, self.o2 = parse_opts(args.engine1_opts), parse_opts(args.engine2_opts)
        self.prefix = ()
        if self.local:
            return
        key = os.path.expanduser(args.ssh_key)
        ssh = ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30"]
        self.prefix = (*ssh, spec)
        self.dir = f"/tmp/fanorona-match-{uuid.uuid4().hex[:8]}"
        subprocess.run([*ssh, spec, f"mkdir -p {self.dir}"], check=True)
        files, self.copied = [], {}
        for local in (self.e1, self.e2) + tuple(v for _, v in self.o1 + self.o2 if os.path.isfile(v)):
            if local not in self.copied:
                self.copied[local] = f"{self.dir}/{len(self.copied)}_{os.path.basename(local)}"
                files.append(local)
        for local in files:
            subprocess.run(["scp", "-q", "-i", key, "-o", "BatchMode=yes", local, f"{spec}:{self.copied[local]}"], check=True)
        remap = lambda opts: [(k, self.copied.get(v, v)) for k, v in opts]
        self.e1, self.e2 = self.copied[self.e1], self.copied[self.e2]
        self.o1, self.o2 = remap(self.o1), remap(self.o2)

    def cleanup(self):
        if not self.local:
            subprocess.run([*self.prefix, f"rm -rf {self.dir}"], check=False)


def run(args, metrics):
    """Parties fixes (args.games paires) ou SPRT, avec args.concurrency paires de moteurs en parallèle."""
    sprt = Sprt(args.elo0, args.elo1, args.alpha, args.beta) if args.sprt else None
    if sprt:
        print(f"SPRT elo0={args.elo0} elo1={args.elo1} alpha={args.alpha} beta={args.beta} "
              f"bornes LLR=[{sprt.lower:.3f}, {sprt.upper:.3f}]")
    hosts, slots = {}, []
    for part in (args.hosts or f"local:{args.concurrency}").split(","):
        spec, _, n = part.rpartition(":")
        hosts[spec] = Host(spec, args)
        slots += [hosts[spec]] * int(n)
    print(f"{len(slots)} partie(s) en parallèle : " + ", ".join(f"{h} x{sum(s is hosts[h] for s in slots)}" for h in hosts),
          flush=True)
    lock = threading.Lock()
    state = {"next": 0, "stop": False, "w": 0, "d": 0, "l": 0, "done": None, "error": None}

    def take_pair():
        with lock:
            if state["stop"]:
                return None
            g = state["next"]
            if not sprt and g >= args.games:
                return None
            if sprt and args.max_games is not None and 2 * g >= args.max_games:
                return None
            state["next"] += 1
            return g

    def record(game_id, res, e1_white):
        result = game_result_for_engine1(res, e1_white)
        with lock:
            if state["stop"]:
                return
            state[{"win": "w", "draw": "d", "loss": "l"}[result]] += 1
            n = state["w"] + state["d"] + state["l"]
            if not sprt:
                metrics.log("game_result", game=game_id, raw_result=res, engine1_result=result)
                print(f"partie {n} (#{game_id + 1}): {res}  | engine1 +{state['w']} ={state['d']} -{state['l']}", flush=True)
                return
            sprt.add_result(result)
            xbar, var, _ = sprt.stats()
            llr = sprt.llr()
            metrics.log("sprt_update", game=game_id, raw_result=res, engine1_result=result, wins=sprt.wins,
                        draws=sprt.draws, losses=sprt.losses, llr=llr, lower=sprt.lower, upper=sprt.upper,
                        mean_score=xbar)
            print(f"partie {sprt.n}: {res}  | W{sprt.wins} D{sprt.draws} L{sprt.losses}  "
                  f"LLR {llr:+.3f} (bornes [{sprt.lower:.3f}, {sprt.upper:.3f}])", flush=True)
            decision = sprt.decision()
            if decision is not None:
                state["stop"], state["done"] = True, (decision, sprt.n, llr)

    def worker(host):
        e1 = e2 = ref = None
        try:
            e1 = Engine(host.e1, "engine1", metrics, options=host.o1, prefix=host.prefix)
            e2 = Engine(host.e2, "engine2", metrics, options=host.o2, prefix=host.prefix)
            ref = Engine(args.engine1, "referee", metrics)  # arbitre local (règles uniquement, coût négligeable)
            while (g := take_pair()) is not None:
                opening = random_opening(ref, args.opening_plies, random.Random(args.seed * 1_000_003 + g))
                for swap in (False, True):
                    if state["stop"]:
                        return
                    for e in (e1, e2):
                        e.send("ucinewgame")
                    engines = [e2, e1] if swap else [e1, e2]
                    res = play_game(engines, opening, args.movetime, ref, 2 * g + swap)
                    record(2 * g + swap, res, not swap)
        except Exception as e:  # un moteur qui meurt arrête tout le match
            with lock:
                state["stop"], state["error"] = True, e
        finally:
            for e in (e1, e2, ref):
                if e:
                    try:
                        e.close()
                    except Exception:
                        pass

    threads = [threading.Thread(target=worker, args=(h,)) for h in slots]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for h in hosts.values():
        h.cleanup()
    if state["error"]:
        raise state["error"]
    if not sprt:
        n = state["w"] + state["d"] + state["l"]
        print(f"Score engine1 : {(state['w'] + state['d'] / 2) / n * 100:.1f}% sur {n} parties")
    elif state["done"]:
        decision, n, llr = state["done"]
        verdict = "H1 acceptée (Elo >= elo1)" if decision == "h1" else "H0 acceptée (Elo <= elo0, rejeté)"
        print(f"\nSPRT terminé : {verdict} après {n} parties (LLR={llr:+.3f})")
        metrics.log("sprt_done", decision=decision, games=n, llr=llr)
    else:
        print(f"\nSPRT arrêté (max_games={args.max_games} atteint) sans conclusion tranchée.")
        metrics.log("sprt_done", decision="inconclusive", games=sprt.n, llr=sprt.llr())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("engine1")
    ap.add_argument("engine2")
    ap.add_argument("--games", type=int, default=10, help="nombre de paires de parties (mode parties fixes)")
    ap.add_argument("--movetime", type=int, default=100)
    ap.add_argument("--opening-plies", type=int, default=4)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--sprt", action="store_true", help="arrêt séquentiel au lieu d'un nombre fixe de parties")
    ap.add_argument("--elo0", type=float, default=0.0, help="H0 : Elo réel <= elo0 (SPRT)")
    ap.add_argument("--elo1", type=float, default=5.0, help="H1 : Elo réel >= elo1 (SPRT)")
    ap.add_argument("--alpha", type=float, default=0.05, help="erreur de type I (SPRT)")
    ap.add_argument("--beta", type=float, default=0.05, help="erreur de type II (SPRT)")
    ap.add_argument("--max-games", type=int, default=None, help="garde-fou : arrêt même sans conclusion (SPRT)")
    ap.add_argument("--no-live-log", action="store_true", help="désactive la journalisation JSONL live")
    ap.add_argument("--run-id", default=None, help="identifiant de run pour le suivi live (auto par défaut)")
    ap.add_argument("--engine1-opts", default=None, help="options UCI engine1, ex. UseNNUE=true,EvalFile=net.nnue")
    ap.add_argument("--engine2-opts", default=None, help="options UCI engine2, même format")
    ap.add_argument("--concurrency", type=int, default=1, help="paires de moteurs jouant en parallèle (en local)")
    ap.add_argument("--hosts", default=None, help='machines et parties simultanées, ex. "local:5,root@10.10.10.190:4"')
    ap.add_argument("--ssh-key", default="~/.ssh/id_fanorona_match", help="clé SSH vers les hôtes distants")
    args = ap.parse_args()

    run_type = "sprt" if args.sprt else "match"
    metrics = MetricsLogger(
        run_type,
        run_id=args.run_id or new_run_id(run_type),
        tags={"engine1_path": args.engine1, "engine2_path": args.engine2},
        enabled=not args.no_live_log,
    )
    if metrics.enabled:
        print(f"suivi live : run_id={metrics.run_id}")

    metrics = LockedMetrics(metrics)
    try:
        run(args, metrics)
    finally:
        metrics.close()


if __name__ == "__main__":
    main()
