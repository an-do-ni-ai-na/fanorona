#!/usr/bin/env python3
"""Match entre deux versions du moteur (auto-jeu), façon "fishtest" minimal.

Mode parties fixes :
    python3 tools/match.py ./fanorona ./fanorona-old --games 20 --movetime 100

Mode SPRT (arrêt séquentiel dès que l'écart d'Elo est statistiquement tranché) :
    python3 tools/match.py ./fanorona ./fanorona-old --sprt --elo0 0 --elo1 5 --movetime 100

NNUE contre HCE (même binaire, NNUE est une option UCI à l'exécution) :
    python3 tools/match.py ./fanorona ./fanorona --sprt --elo0 0 --elo1 10 --movetime 100 \
        --engine1-opts "UseNNUE=true,EvalFile=checkpoints/net_v1.nnue"

Les parties démarrent depuis des ouvertures aléatoires (quelques coups au hasard, jouées par
paires pour que chaque moteur ait les deux couleurs). Les lignes UCI "info" de chaque moteur
sont journalisées en JSONL (/var/log/fanorona/<run_id>.jsonl par défaut) pour suivi live via
Grafana/Loki ; désactivable avec --no-live-log si le répertoire n'est pas accessible.
"""
import argparse
import random
import subprocess

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
    def __init__(self, path, name, metrics, options=None):
        self.name = name
        self.metrics = metrics
        self.p = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
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


def run_fixed_games(e1, e2, ref, args, rng, metrics):
    score = {"w": 0, "l": 0, "d": 0}
    for g in range(args.games):
        opening = random_opening(ref, args.opening_plies, rng)
        for swap in (False, True):
            game_id = 2 * g + swap
            engines = [e2, e1] if swap else [e1, e2]
            for e in (e1, e2):
                e.send("ucinewgame")
            res = play_game(engines, opening, args.movetime, ref, game_id)
            e1_white = not swap
            result = game_result_for_engine1(res, e1_white)
            score[{"win": "w", "draw": "d", "loss": "l"}[result]] += 1
            metrics.log("game_result", game=game_id, raw_result=res, engine1_result=result)
            print(f"partie {game_id + 1}: {res}  | engine1 +{score['w']} ={score['d']} -{score['l']}", flush=True)
    n = sum(score.values())
    print(f"Score engine1 : {(score['w'] + score['d'] / 2) / n * 100:.1f}% sur {n} parties")


def run_sprt(e1, e2, ref, args, rng, metrics):
    sprt = Sprt(args.elo0, args.elo1, args.alpha, args.beta)
    print(
        f"SPRT elo0={args.elo0} elo1={args.elo1} alpha={args.alpha} beta={args.beta} "
        f"bornes LLR=[{sprt.lower:.3f}, {sprt.upper:.3f}]"
    )
    g = 0
    while args.max_games is None or sprt.n < args.max_games:
        opening = random_opening(ref, args.opening_plies, rng)
        for swap in (False, True):
            game_id = 2 * g + swap
            engines = [e2, e1] if swap else [e1, e2]
            for e in (e1, e2):
                e.send("ucinewgame")
            res = play_game(engines, opening, args.movetime, ref, game_id)
            e1_white = not swap
            result = game_result_for_engine1(res, e1_white)
            sprt.add_result(result)
            xbar, var, n = sprt.stats()
            llr = sprt.llr()
            metrics.log(
                "sprt_update",
                game=game_id,
                raw_result=res,
                engine1_result=result,
                wins=sprt.wins,
                draws=sprt.draws,
                losses=sprt.losses,
                llr=llr,
                lower=sprt.lower,
                upper=sprt.upper,
                mean_score=xbar,
            )
            print(
                f"partie {game_id + 1}: {res}  | W{sprt.wins} D{sprt.draws} L{sprt.losses}  "
                f"LLR {llr:+.3f} (bornes [{sprt.lower:.3f}, {sprt.upper:.3f}])",
                flush=True,
            )
            decision = sprt.decision()
            if decision is not None:
                verdict = "H1 acceptée (Elo >= elo1)" if decision == "h1" else "H0 acceptée (Elo <= elo0, rejeté)"
                print(f"\nSPRT terminé : {verdict} après {sprt.n} parties (LLR={llr:+.3f})")
                metrics.log("sprt_done", decision=decision, games=sprt.n, llr=llr)
                return
        g += 1
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

    rng = random.Random(args.seed)
    e1 = Engine(args.engine1, "engine1", metrics, options=parse_opts(args.engine1_opts))
    e2 = Engine(args.engine2, "engine2", metrics, options=parse_opts(args.engine2_opts))
    ref = Engine(args.engine1, "referee", metrics)
    try:
        if args.sprt:
            run_sprt(e1, e2, ref, args, rng, metrics)
        else:
            run_fixed_games(e1, e2, ref, args, rng, metrics)
    finally:
        for e in (e1, e2, ref):
            e.close()
        metrics.close()


if __name__ == "__main__":
    main()
