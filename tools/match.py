#!/usr/bin/env python3
"""Match entre deux versions du moteur (auto-jeu), façon "fishtest" minimal.

Exemple :
    python3 tools/match.py ./fanorona ./fanorona-old --games 20 --movetime 100

Les parties démarrent depuis des ouvertures aléatoires (quelques coups au
hasard, joués par paires pour que chaque moteur ait les deux couleurs).
"""
import argparse
import random
import subprocess


class Engine:
    def __init__(self, path):
        self.p = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.send("uci")
        self.wait("uciok")

    def send(self, cmd):
        self.p.stdin.write(cmd + "\n")
        self.p.stdin.flush()

    def wait(self, prefix):
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("engine died")
            if line.startswith(prefix):
                return line.strip()

    def query(self, cmd, prefix):
        self.send(cmd)
        return self.wait(prefix)

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


def play_game(engines, opening, movetime, referee):
    moves = list(opening)
    while True:
        referee.send(position_cmd(moves))
        status = referee.query("status", "status").split(" ", 1)[1]
        if status != "ongoing":
            return status
        eng = engines[len(moves) % 2]
        eng.send(position_cmd(moves))
        best = eng.query(f"go movetime {movetime}", "bestmove").split()[1]
        if best == "(none)":
            return "black wins" if len(moves) % 2 == 0 else "white wins"
        moves.append(best)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("engine1")
    ap.add_argument("engine2")
    ap.add_argument("--games", type=int, default=10, help="nombre de paires de parties")
    ap.add_argument("--movetime", type=int, default=100)
    ap.add_argument("--opening-plies", type=int, default=4)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    e1, e2, ref = Engine(args.engine1), Engine(args.engine2), Engine(args.engine1)
    score = {"w": 0, "l": 0, "d": 0}
    for g in range(args.games):
        opening = random_opening(ref, args.opening_plies, rng)
        for swap in (False, True):
            engines = [e2, e1] if swap else [e1, e2]
            for e in (e1, e2):
                e.send("ucinewgame")
            res = play_game(engines, opening, args.movetime, ref)
            e1_white = not swap
            if res.startswith("draw"):
                score["d"] += 1
            elif (res == "white wins") == e1_white:
                score["w"] += 1
            else:
                score["l"] += 1
            print(f"partie {2 * g + swap + 1}: {res}  | engine1 +{score['w']} ={score['d']} -{score['l']}", flush=True)
    for e in (e1, e2, ref):
        e.close()
    n = sum(score.values())
    print(f"Score engine1 : {(score['w'] + score['d'] / 2) / n * 100:.1f}% sur {n} parties")


if __name__ == "__main__":
    main()
