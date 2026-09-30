#!/usr/bin/env python3
"""Génère des puzzles tactiques (Fanoron-Tsivy) à partir des positions d'auto-jeu (gensfen).

Comme sur lichess, un puzzle naît d'une faute : depuis une position d'auto-jeu, l'adversaire joue un coup au hasard,
puis on cherche une punition. Un puzzle est une position où UN SEUL coup gagne nettement : le moteur évalue chaque coup légal (recherche à
profondeur fixe depuis la position obtenue), le meilleur doit donner au moins +WIN centipions au camp au trait,
avec un écart d'au moins GAP sur le deuxième, et le deuxième ne doit pas être lui-même gagnant (<= SECOND_MAX).
La solution est prolongée (réponse du moteur, puis nouveau coup unique) jusqu'à 3 coups du joueur.

Difficulté (classement approximatif, à recalibrer avec les résultats réels) : profondeur minimale à laquelle le
moteur trouve le premier coup, nombre de coups légaux, longueur de la solution.

    python3 tools/puzzles/gen_puzzles.py data/gensfen_gen3.txt --count 600 --workers 5 --out tools/gui/puzzles.json
"""
import argparse
import json
import math
import multiprocessing as mp
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WIN, GAP, SECOND_MAX = 200, 200, 120
MATE = 10000


class Engine:
    def __init__(self, path):
        self.p = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.p.stdout.readline()  # bannière

    def send(self, *cmds):
        self.p.stdin.write("\n".join(cmds) + "\n")
        self.p.stdin.flush()

    def line(self):
        return self.p.stdout.readline().rstrip("\n")

    def moves(self, fen, extra=()):
        self.send(pos(fen, extra), "moves")
        return self.line().split()

    def fen_after(self, fen, extra):
        self.send(pos(fen, extra), "d")
        while True:
            l = self.line()
            if l.startswith("Fen: "):
                f = l[5:].strip()
                # vider le reste de la sortie de "d" (jusqu'à la ligne vide finale)
                while self.line() != "":
                    pass
                return f

    def go(self, fen, extra, depth):
        """(bestmove, score cp pour le camp au trait)"""
        self.send(pos(fen, extra), f"go depth {depth}")
        score = 0
        while True:
            l = self.line()
            if l.startswith("info depth"):
                t = l.split()
                i = t.index("score")
                v = int(t[i + 2])
                score = v if t[i + 1] == "cp" else (MATE - 10 * abs(v)) * (1 if v > 0 else -1)
            elif l.startswith("bestmove"):
                best = l.split()[1]
                if best == "(none)":
                    score = -MATE
                return best, score


def pos(fen, extra):
    return f"position fen {fen}" + (" moves " + " ".join(extra) if extra else "")


def scored_moves(eng, fen, extra, depth):
    """[(coup, score pour le camp qui joue)] triés du meilleur au pire."""
    out = []
    for m in eng.moves(fen, extra):
        _, s = eng.go(fen, list(extra) + [m], depth)
        out.append((m, -s))
    return sorted(out, key=lambda x: -x[1])


ACCEPT = 60  # coups acceptés en plus de la solution : à moins de 60 cp du meilleur (chemins équivalents)


def family(m):
    """Première étape d'un coup (départ, arrivée, type de prise) : une chaîne et ses débuts arrêtés plus tôt
    forment une même famille. L'unicité se juge entre familles, sinon « s'arrêter une prise plus tôt » serait
    toujours un deuxième coup gagnant et aucune chaîne ne ferait un puzzle."""
    return m[:5] if len(m) > 4 and m[4] in "AW" else m[:4]


def unique_best(sm):
    """(meilleur coup, coups acceptés) si un seul coup gagne nettement, sinon None."""
    if len(sm) < 2:
        return None
    m1, s1 = sm[0]
    others = [s for m, s in sm if family(m) != family(m1)]
    s2 = max(others) if others else -MATE
    if not others or s1 < WIN or s1 - s2 < GAP or s2 > SECOND_MAX:
        return None
    # Dans la famille gagnante, s'arrêter trop tôt est une erreur : seuls les coups quasi équivalents passent.
    return m1, [m for m, s in sm if s >= s1 - ACCEPT]


def pieces(fen):
    b = fen.split()[0]
    return b.count("W"), b.count("B")


def make_puzzle(eng, fen, depth):
    sm = scored_moves(eng, fen, [], depth)
    if len(sm) < 3:
        return None
    ub = unique_best(sm)
    if not ub:
        return None
    first, acc = ub
    accepted = [acc]
    # Confirmation plus profonde : le moteur doit jouer le même coup.
    best_deep, _ = eng.go(fen, [], depth + 3)
    if best_deep != first:
        return None
    line = [first]
    for _ in range(2):  # jusqu'à 3 coups du joueur
        reply, _ = eng.go(fen, line, depth + 1)
        if reply == "(none)":
            break
        sm2 = scored_moves(eng, fen, line + [reply], depth)
        ub2 = unique_best(sm2) if len(sm2) >= 2 else None
        if not ub2:
            break
        line += [reply, ub2[0]]
        accepted.append(ub2[1])
    # Difficulté : profondeur minimale qui trouve le premier coup.
    found = depth + 3
    for d in range(1, depth + 3):
        if eng.go(fen, [], d)[0] == first:
            found = d
            break
    stm = fen.split()[1]
    me = 0 if stm == "w" else 1
    before = pieces(fen)
    after_first = pieces(eng.fen_after(fen, [first]))
    end = eng.fen_after(fen, line)
    end_moves = eng.moves(end, [])
    themes = []
    taken_first = before[1 - me] - after_first[1 - me]
    if taken_first == 0:
        themes.append("paika")
    elif first.count("A") + first.count("W") > 1:
        themes.append("chaine")
    if taken_first >= 4:
        themes.append("rafle")
    if len(line) > 1:
        themes.append("combinaison")
    if not end_moves or pieces(end)[1 - me] == 0:
        themes.append("decisif")
    if first[4:5] == "W":
        themes.append("retrait")
    elif first[4:5] == "A":
        themes.append("approche")
    n_player = (len(line) + 1) // 2
    steps = max(1, first.count("A") + first.count("W"))  # longueur de la chaîne à trouver
    # Le moteur trouve presque tout à la profondeur 1 (quiescence) : la difficulté reflète plutôt ce qui est dur
    # pour un humain — choix nombreux, longue chaîne, coup calme, combinaison en plusieurs coups.
    rating = (650 + 55 * math.log2(len(sm)) + 90 * (steps - 1) + 260 * (taken_first == 0)
              + 220 * (n_player - 1) + 120 * (found - 1))
    rating = int(max(600, min(2600, round(rating / 10) * 10)))
    return {"fen": fen, "moves": line, "accept": accepted, "rating": rating, "themes": themes, "legal": len(sm), "found_depth": found,
            "gain": sm[0][1]}


def worker(args):
    wid, lines, engine, depth, out, target = args
    eng = Engine(engine)
    rng = random.Random(wid)
    n = 0
    with open(out, "w") as f:
        for i, base in enumerate(lines):
            try:
                ms = eng.moves(base)
                if not ms:
                    continue
                fen = eng.fen_after(base, [rng.choice(ms)])  # la faute (coup au hasard)
                if int(fen.split()[2]) > 60:  # trop près de la nulle par absence de prise
                    continue
                pz = make_puzzle(eng, fen, depth)
            except Exception as e:  # position exotique : on passe
                print(f"[{wid}] erreur {fen}: {e}", file=sys.stderr, flush=True)
                eng = Engine(engine)
                continue
            if pz:
                f.write(json.dumps(pz) + "\n")
                f.flush()
                n += 1
                if n >= target:
                    break
            if i % 200 == 0:
                print(f"[{wid}] {i} positions, {n} puzzles", flush=True)
    return n


def nnue_check(args):
    """Deuxième avis : le réseau NNUE (autre évaluation que la HCE du générateur) doit jouer un coup accepté."""
    p, engine, net, depth = args
    pr = subprocess.Popen([engine], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    pr.stdin.write(f"setoption name EvalFile value {net}\nsetoption name UseNNUE value true\n"
                   f"position fen {p['fen']}\ngo depth {depth}\n")
    pr.stdin.flush()
    best = None
    for l in pr.stdout:
        if l.startswith("bestmove"):
            best = l.split()[1]
            break
    pr.stdin.write("quit\n")
    pr.stdin.close()
    pr.wait()
    return best in p["accept"][0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("--count", type=int, default=600)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--depth", type=int, default=5)
    ap.add_argument("--sample", type=int, default=60000)
    ap.add_argument("--engine", default=str(ROOT / "fanorona"))
    ap.add_argument("--out", default=str(ROOT / "tools/gui/puzzles.json"))
    ap.add_argument("--net", default=str(ROOT / "checkpoints/net_v3.nnue"), help="réseau du second avis ('' : aucun)")
    ap.add_argument("--verify-only", action="store_true", help="refiltrer un fichier existant par le second avis")
    a = ap.parse_args()
    if a.verify_only:
        data = json.loads(Path(a.out).read_text())
        with mp.Pool(a.workers) as pool:
            keep = pool.map(nnue_check, [(p, a.engine, a.net, 10) for p in data["puzzles"]])
        before = len(data["puzzles"])
        data["puzzles"] = [p for p, k in zip(data["puzzles"], keep) if k]
        Path(a.out).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
        print(f"second avis NNUE : {len(data['puzzles'])}/{before} puzzles gardés")
        return

    # Échantillon : positions indécises (|score| <= 300) avec un peu de matériel des deux côtés.
    cand = []
    with open(a.data) as f:
        lines = f.readlines()
    random.seed(20261001)
    for l in random.sample(lines, min(len(lines), a.sample * 4)):
        fen, score, _ = l.strip().split("|")
        w, b = pieces(fen)
        if abs(int(score)) <= 300 and w >= 4 and b >= 4:
            cand.append(fen)
        if len(cand) >= a.sample:
            break
    cand = list(dict.fromkeys(cand))
    print(f"{len(cand)} positions candidates", flush=True)
    chunks = [cand[i::a.workers] for i in range(a.workers)]
    per = math.ceil(a.count / a.workers)
    tmp = [f"/tmp/puzzles_{i}.jsonl" for i in range(a.workers)]
    t0 = time.time()
    with mp.Pool(a.workers) as pool:
        pool.map(worker, [(i, chunks[i], a.engine, a.depth, tmp[i], per) for i in range(a.workers)])
    seen, puzzles = set(), []
    for t in tmp:
        for l in open(t):
            p = json.loads(l)
            if p["fen"] not in seen:
                seen.add(p["fen"])
                puzzles.append(p)
    if a.net:
        with mp.Pool(a.workers) as pool:
            keep = pool.map(nnue_check, [(p, a.engine, a.net, 10) for p in puzzles])
        print(f"second avis NNUE : {sum(keep)}/{len(puzzles)} puzzles gardés", flush=True)
        puzzles = [p for p, k in zip(puzzles, keep) if k]
    puzzles.sort(key=lambda p: p["rating"])
    for i, p in enumerate(puzzles):
        p["id"] = f"p{i + 1:04d}"
    Path(a.out).write_text(json.dumps({"version": 1, "source": Path(a.data).name, "puzzles": puzzles},
                                      ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"{len(puzzles)} puzzles en {time.time() - t0:.0f} s -> {a.out}")


if __name__ == "__main__":
    main()
