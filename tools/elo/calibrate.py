#!/usr/bin/env python3
"""Calibration Elo de l'échelle de force du moteur (STRENGTHS dans tools/gui/server.py).

Tournoi entre réglages voisins (i contre i+1) et à deux crans (i contre i+2), en réutilisant exactement le code de
jeu du serveur (server.search) : chaque réglage joue comme dans l'interface. Ouverture : 2 demi-coups au hasard,
couleurs alternées ; partie nulle au-delà de 300 demi-coups. Résultats ajoutés au fil de l'eau dans un JSONL
(reprise possible). Ajustement : Bradley-Terry par maximum de vraisemblance (nulle = demi-point), avec un a priori
faible (une nulle virtuelle par paire, façon BayesElo) pour borner les scores à 100 % ; ancre : réglage 1
(niveau Débutant) = 800.

    python3 tools/elo/calibrate.py --games 30 --gap2 20 --workers 5      # joue puis écrit tools/gui/elo.json
    python3 tools/elo/calibrate.py --fit-only                              # recalcule depuis le JSONL
"""
import argparse
import json
import math
import multiprocessing as mp
import random
import sys
import threading
import time
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools" / "gui"))
import server as S  # noqa: E402

ANCHOR, ANCHOR_ELO = 1, 800
MAX_PLIES = 300


def setup(net):
    S.args = Namespace(engine=str(ROOT / "fanorona"), cwd=str(ROOT), nets=str(ROOT / "checkpoints"))
    S.search_slots = threading.BoundedSemaphore(64)
    return {"game": "tsivy", "net": net}


def play(job):
    a, b, seed, net = job  # a joue les Blancs
    base = setup(net)
    rng = random.Random(seed)
    moves = []
    st = S.get_state({**base, "moves": moves})
    for _ in range(2):
        if st["status"] != "ongoing":
            break
        moves.append(rng.choice(st["legal"]))
        st = S.get_state({**base, "moves": moves})
    random.seed(seed * 7919)  # tirages des réglages « sample »
    while st["status"] == "ongoing" and len(moves) < MAX_PLIES:
        k = a if len(moves) % 2 == 0 else b
        r = S.search({**base, "moves": moves, "strength": k, "movetime": 1000})
        moves.append(r["bestmove"])
        st = S.get_state({**base, "moves": moves})
    res = 1.0 if st["status"] == "white wins" else 0.0 if st["status"] == "black wins" else 0.5
    return {"white": a, "black": b, "score": res, "plies": len(moves), "status": st["status"], "seed": seed}


def fit(results, n):
    """Classements Bradley-Terry (échelle Elo) par montée de gradient, a priori : une nulle virtuelle par paire."""
    games = [(r["white"], r["black"], r["score"]) for r in results]
    pairs = {tuple(sorted((w, b))) for w, b, _ in games}
    games += [(i, j, 0.5) for i, j in pairs]
    R = [0.0] * n
    c = math.log(10) / 400
    for it in range(20000):
        g = [0.0] * n
        for w, b, sc in games:
            p = 1 / (1 + 10 ** (-(R[w] - R[b]) / 400))
            g[w] += c * (sc - p)
            g[b] -= c * (sc - p)
        step = 4000 / max(1, len(games))
        mx = 0
        for i in range(n):
            R[i] += step * g[i] * 400
            mx = max(mx, abs(g[i]))
        if mx < 1e-7:
            break
    off = ANCHOR_ELO - R[ANCHOR]
    return [round(r + off) for r in R]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=30, help="parties par paire voisine")
    ap.add_argument("--gap2", type=int, default=20, help="parties par paire à deux crans")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--net", default="net_v3")
    ap.add_argument("--log", default=str(ROOT / "data" / "elo_calibration.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "tools" / "gui" / "elo.json"))
    ap.add_argument("--fit-only", action="store_true")
    a = ap.parse_args()
    n = len(S.STRENGTHS)
    log = Path(a.log)
    done = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    if not a.fit_only:
        have = {}
        for r in done:
            key = (r["white"], r["black"], r["seed"])
            have[key] = True
        jobs = []
        for i in range(n):
            for gap, cnt in ((1, a.games), (2, a.gap2)):
                j = i + gap
                if j >= n:
                    continue
                for g in range(cnt):
                    w, b = (i, j) if g % 2 == 0 else (j, i)
                    seed = 1000 * i + 100 * gap + g
                    if (w, b, seed) not in have:
                        jobs.append((w, b, seed, a.net))
        random.Random(1).shuffle(jobs)  # mélange : les parties lentes (pleine force) se répartissent
        print(f"{len(done)} parties déjà jouées, {len(jobs)} à jouer", flush=True)
        t0 = time.time()
        with mp.Pool(a.workers) as pool, log.open("a") as f:
            for k, r in enumerate(pool.imap_unordered(play, jobs), 1):
                f.write(json.dumps(r) + "\n")
                f.flush()
                done.append(r)
                if k % 20 == 0 or k == len(jobs):
                    print(f"{k}/{len(jobs)} parties, {time.time() - t0:.0f} s", flush=True)
    ratings = fit(done, n)
    table = {}
    for r in done:
        i, j = sorted((r["white"], r["black"]))
        sc = r["score"] if r["white"] == j else 1 - r["score"]  # point de vue du plus fort (indice haut)
        t = table.setdefault(f"{i}-{j}", [0, 0.0])
        t[0] += 1
        t[1] += sc
    Path(a.out).write_text(json.dumps({
        "anchor": f"réglage {ANCHOR} (niveau Débutant) = {ANCHOR_ELO}", "conditions": f"Fanoron-Tsivy, {a.net}, 1 s/coup (pleine force)",
        "games": len(done), "date": time.strftime("%Y-%m-%d"), "ratings": ratings,
        "pairs": {k: {"games": v[0], "score_of_stronger": round(v[1] / v[0], 3)} for k, v in sorted(table.items(), key=lambda x: [int(y) for y in x[0].split("-")])},
    }, ensure_ascii=False, indent=2) + "\n")
    print("Elo :", ratings)
    for k, v in sorted(table.items(), key=lambda x: [int(y) for y in x[0].split("-")]):
        print(f"  {k:>6} : {v[1]:.1f}/{v[0]}")


if __name__ == "__main__":
    main()
