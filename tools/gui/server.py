#!/usr/bin/env python3
"""Interface web pour jouer contre Fanorona-Engine.

Serveur HTTP sans dépendance (bibliothèque standard uniquement) : sert la page `index.html` et une petite API
JSON qui pilote le binaire `./fanorona` par son protocole texte (UCI).

Sans état côté serveur : le navigateur garde la liste des coups joués et la renvoie à chaque requête ; chaque
requête lance un processus moteur éphémère (`position startpos moves ...`). Le moteur reste ainsi la seule
source de vérité pour les règles (coups légaux, fin de partie, répétitions), et plusieurs parties peuvent
tourner en parallèle sans session à gérer.

    python3 tools/gui/server.py --engine ./fanorona --nets checkpoints --port 8090

API :
    GET  /api/config                       -> réseaux NNUE disponibles, niveaux, limites
    POST /api/state {moves, variant}       -> fen, coups légaux, statut
    POST /api/go    {moves, variant, movetime, net, level}  -> coup du moteur + infos de recherche, puis nouvel état
                                           (level 1..6, 6 = pleine force ; les indices utilisent toujours 6)
    POST /api/eval  {moves, plies, depth, net, variant}  -> analyse : score + meilleur coup après moves[:ply]
    GET  /api/games[?game=&mode=&outcome=&limit=&offset=]  -> historique (plus récentes d'abord)
    POST /api/games {partie}              -> enregistre une partie terminée ; GET/DELETE /api/games/<id>
    POST /api/games/<id>/analysis {depth, evals}  -> ajoute l'analyse ; GET /api/games/stats -> bilan
    (liste et bilan : ?profile=<id> ou ?profile=guest)
    POST /api/analyse {sid, moves, game, …, multipv}  -> analyse en continu (remplace celle de la session) ;
    GET /api/analyse/<sid> -> {depth, lines} ; POST /api/analyse/<sid>/stop
    GET/POST /api/profiles ; GET/POST/DELETE /api/profiles/<id> ; POST /api/profiles/<id>/{puzzle,learn,import}
"""

import argparse
import json
import sqlite3
import time
import math
import os
import random
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import labo

HERE = Path(__file__).resolve().parent
MOVE_RE = re.compile(r"^[a-i][1-5](?:[a-i][1-5][AW]?)*$")  # une case seule = pose (Fanoron-Telo)
GAMES = {"tsivy", "dimy", "telo"}
FEN_RE = re.compile(r"^[WB1-9]{1,9}(?:/[WB1-9]{1,9}){2,4} [wb](?: \d{1,4} \d{1,4})?$")
MAX_MOVETIME = 30000
MAX_PLIES = 2000

args = None
# Une recherche occupe un cœur à 100 % : on borne le nombre de recherches simultanées.
search_slots = None


class EngineError(Exception):
    pass


def check_moves(moves):
    if not isinstance(moves, list) or len(moves) > MAX_PLIES:
        raise EngineError("liste de coups invalide")
    for m in moves:
        if not isinstance(m, str) or not MOVE_RE.match(m):
            raise EngineError(f"coup mal formé : {m!r}")
    return moves


def rule_options(req):
    """Options de règles : jeu (tsivy 9x5, dimy 5x5, telo 3x3), partie vela, continuation obligatoire."""
    game = req.get("game", "tsivy")
    if game not in GAMES:
        raise EngineError(f"jeu inconnu : {game}")
    cmds = [f"setoption name Variant value {game}"] if game != "tsivy" else []
    vela = req.get("vela")
    if vela not in (None, "", "W", "B"):
        raise EngineError("vela : W, B ou rien")
    if vela and game == "tsivy":
        cmds.append("setoption name Vela value " + ("white" if vela == "W" else "black"))
    if req.get("variant") == "mandatory" and game != "telo":
        cmds.append("setoption name MandatoryContinuation value true")
    return cmds


def net_options(req):
    net = req.get("net")
    if not net or req.get("game", "tsivy") != "tsivy":  # réseau entraîné sur le 9 x 5 uniquement
        return []
    nets = available_nets()
    if net not in nets:
        raise EngineError(f"réseau inconnu : {net}")
    return [f"setoption name EvalFile value {nets[net]}", "setoption name UseNNUE value true"]


def position_cmd(req, moves):
    """`position startpos` ou, pour une position de départ imposée (tutoriel, éditeur), `position fen`."""
    fen = req.get("fen")
    if fen:
        if not isinstance(fen, str) or not FEN_RE.match(fen):
            raise EngineError("FEN invalide")
        base = f"position fen {fen}"
    else:
        base = "position startpos"
    return base + (" moves " + " ".join(moves) if moves else "")


def preamble(req):
    """Commandes communes : règles, puis position."""
    moves = check_moves(req.get("moves", []))
    cmds = rule_options(req)
    cmds.append(position_cmd(req, moves))
    return cmds


def run_batch(cmds, timeout=10):
    """Exécute des commandes sans recherche (le moteur quitte à la fin de stdin)."""
    try:
        out = subprocess.run([args.engine], input="\n".join(cmds) + "\nquit\n", capture_output=True, text=True,
                             timeout=timeout, cwd=args.cwd).stdout
    except subprocess.TimeoutExpired:
        raise EngineError("le moteur ne répond pas")
    for line in out.splitlines():
        if line.startswith("info string illegal move") or line.startswith("info string invalid fen"):
            raise EngineError(line[len("info string "):])
    return out.splitlines()


def get_state(req):
    lines = run_batch(preamble(req) + ["d", "moves", "status"])
    fen = next((l[5:].strip() for l in lines if l.startswith("Fen: ")), None)
    idx = next((i for i, l in enumerate(lines) if l.startswith("status ")), None)
    if fen is None or idx is None:
        raise EngineError("réponse du moteur inattendue")
    legal = lines[idx - 1].split()
    return {"fen": fen, "legal": legal, "status": lines[idx][7:].strip()}


def parse_info(line):
    t = line.split()
    info = {}
    i = 1
    while i < len(t):
        k = t[i]
        if k in ("depth", "seldepth", "nodes", "nps", "time", "hashfull"):
            info[k] = int(t[i + 1])
            i += 2
        elif k == "multipv":
            info["multipv"] = int(t[i + 1])
            i += 2
        elif k == "score":
            info["score"] = {"type": t[i + 1], "value": int(t[i + 2])}
            i += 3
        elif k == "pv":
            info["pv"] = t[i + 1:]
            break
        else:
            i += 1
    return info


# Niveaux de difficulté. Le moteur n'a pas d'option de force : on l'affaiblit depuis l'extérieur.
#   "sample" : chaque coup légal est évalué par une recherche courte (profondeur `depth`), puis tiré au sort
#              avec une probabilité softmax exp(score / temp) — façon "Skill Level" de Stockfish : les bons coups
#              restent favoris, mais les erreurs sont possibles et d'autant plus graves que `temp` est grand ;
#   "depth"  : recherche normale plafonnée en profondeur (et par le temps choisi) ;
#   "full"   : recherche normale au temps choisi.
# Échelle de force continue (index 0 = le plus faible). « full » avec movetime fixe : pleine force à ce temps par coup.
# Chaque réglage a un Elo calibré par tournoi (tools/elo/calibrate.py -> tools/gui/elo.json, ancre Débutant = 800).
STRENGTHS = [
    {"mode": "sample", "depth": 1, "temp": 400},
    {"mode": "sample", "depth": 1, "temp": 250},   # niveau 1
    {"mode": "sample", "depth": 1, "temp": 150},
    {"mode": "sample", "depth": 2, "temp": 120},   # niveau 2
    {"mode": "sample", "depth": 2, "temp": 80},
    {"mode": "sample", "depth": 3, "temp": 50},    # niveau 3
    {"mode": "sample", "depth": 3, "temp": 25},
    {"mode": "depth", "depth": 3},
    {"mode": "depth", "depth": 4},                 # niveau 4
    {"mode": "depth", "depth": 5},                 # niveau 5
    {"mode": "depth", "depth": 6},
    {"mode": "full", "movetime": 300},
    {"mode": "full", "movetime": 1000},            # niveau 6 à 1 s
]
LEVELS = {
    1: {"name": "Débutant", **STRENGTHS[1], "strength": 1},
    2: {"name": "Facile", **STRENGTHS[3], "strength": 3},
    3: {"name": "Intermédiaire", **STRENGTHS[5], "strength": 5},
    4: {"name": "Confirmé", **STRENGTHS[8], "strength": 8},
    5: {"name": "Expert", **STRENGTHS[9], "strength": 9},
    6: {"name": "Maître", "mode": "full", "strength": 12},  # pleine force au temps choisi
}
ELO_FILE = HERE / "elo.json"


def strength_elos():
    """Elo calibré de chaque réglage (liste alignée sur STRENGTHS), ou None si pas encore calibré."""
    try:
        d = json.loads(ELO_FILE.read_text())
        r = d.get("ratings")
        return r if isinstance(r, list) and len(r) == len(STRENGTHS) else None
    except (OSError, ValueError):
        return None
MATE_CP = 10000


class Engine:
    """Processus moteur interactif. stdin doit rester ouvert jusqu'au bestmove : une fin de stdin vaut "quit",
    qui arrête la recherche en cours."""

    def __init__(self, timeout):
        self.p = subprocess.Popen([args.engine], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                  bufsize=1, cwd=args.cwd)
        self.timer = threading.Timer(timeout, self.p.kill)
        self.timer.start()

    def send(self, cmds):
        self.p.stdin.write("\n".join(cmds) + "\n")
        self.p.stdin.flush()

    def go(self, cmds):
        """Envoie des commandes se terminant par un `go`, renvoie (bestmove, dernière ligne info)."""
        self.send(cmds)
        last = {}
        for line in self.p.stdout:
            if line.startswith("info string illegal move") or (line.startswith("info string nnue:")
                                                               and "chargé" not in line):
                raise EngineError(line.strip()[len("info string "):])
            if line.startswith("info depth"):
                last = parse_info(line)
            elif line.startswith("bestmove"):
                return line.split()[1], last
        raise EngineError("pas de réponse du moteur (délai dépassé)")

    def close(self):
        self.timer.cancel()
        try:
            self.p.stdin.write("quit\n")
            self.p.stdin.close()
            self.p.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if self.p.poll() is None:
            self.p.kill()


def score_cp(info):
    """Score d'une ligne info en centipions (mats ramenés à ±MATE_CP, plus proche = plus grand)."""
    sc = info.get("score")
    if not sc:
        return 0
    if sc["type"] == "mate":
        v = sc["value"]
        return MATE_CP - abs(v) if v > 0 else -MATE_CP + abs(v)
    return sc["value"]


def sample_move(eng, req, legal, lv):
    """Évalue chaque coup légal (point de vue du camp qui joue) puis en tire un au sort (softmax)."""
    base = preamble(req)
    pos_cmd = base[-1] + ("" if req.get("moves") else " moves")
    scored = []
    for m in legal:
        best, info = eng.go(base[:-1] + [f"{pos_cmd} {m}", f"go depth {lv['depth']}"])
        # Après le coup, c'est l'adversaire qui a le trait : on inverse. Pas de coup pour lui = il a perdu
        # (ou nulle détectée : score 0 sans ligne info).
        s = MATE_CP - 1 if best == "(none)" else -score_cp(info)
        scored.append((s, m, info))
    top = max(s for s, _, _ in scored)
    weights = [math.exp((s - top) / lv["temp"]) for s, _, _ in scored]
    s, m, info = random.choices(scored, weights=weights)[0]
    info = dict(info, score={"type": "cp", "value": s}, pv=[m])
    if abs(s) >= MATE_CP - 100:  # mat en N coups (déjà en coups, pas en demi-coups : cf. score_cp)
        info["score"] = {"type": "mate", "value": (MATE_CP - abs(s)) * (1 if s > 0 else -1)}
    info["candidates"] = len(scored)
    return m, info


def search(req):
    movetime = max(50, min(int(req.get("movetime", 1000)), MAX_MOVETIME))
    if req.get("strength") is not None:  # réglage précis de l'échelle (choix par Elo)
        k = int(req["strength"])
        if not 0 <= k < len(STRENGTHS):
            raise EngineError("force inconnue")
        level = STRENGTHS[k]
        if level["mode"] == "full":
            movetime = level["movetime"]
    else:
        level = LEVELS.get(int(req.get("level", 6)))
        if level is None:
            raise EngineError("niveau inconnu")
    opts = net_options(req)

    legal = get_state(req)["legal"] if level["mode"] == "sample" else None
    if legal is not None and len(legal) == 1:
        level = LEVELS[6]  # coup forcé : inutile de tirer au sort
        movetime = 50

    if not search_slots.acquire(timeout=movetime / 1000 + 30):
        raise EngineError("moteur occupé, réessayez")
    try:
        eng = Engine(timeout=movetime / 1000 + 30)
        try:
            eng.send(opts)
            if level["mode"] == "sample" and legal:
                best, info = sample_move(eng, req, legal, level)
            else:
                go = f"go movetime {movetime}" + (f" depth {level['depth']}" if level["mode"] == "depth" else "")
                best, info = eng.go(preamble(req) + [go])
        finally:
            eng.close()
    finally:
        search_slots.release()
    return {"bestmove": best, "info": info}


MAX_EVAL_BATCH = 16


def evaluate_plies(req):
    """Analyse d'une partie : pour chaque demi-coup demandé, évalue la position obtenue après `moves[:ply]`
    (recherche à profondeur fixe). Scores du point de vue du camp au trait, comme en UCI."""
    moves = check_moves(req.get("moves", []))
    plies = req.get("plies", [])
    if not isinstance(plies, list) or not 0 < len(plies) <= MAX_EVAL_BATCH:
        raise EngineError(f"entre 1 et {MAX_EVAL_BATCH} positions par requête")
    if any(not isinstance(k, int) or not 0 <= k <= len(moves) for k in plies):
        raise EngineError("demi-coup hors de la partie")
    depth = max(1, min(int(req.get("depth", 6)), 12))
    opts = net_options(req) + rule_options(req)

    if not search_slots.acquire(timeout=60):
        raise EngineError("moteur occupé, réessayez")
    out = []
    try:
        eng = Engine(timeout=60 + 10 * len(plies))
        try:
            eng.send(opts)
            for k in plies:
                pos = position_cmd(req, moves[:k])
                # "ucinewgame" : chaque position est analysée sans dépendre de la précédente (TT vidée).
                # Fanoron-Telo : le jeu est résolu, "go" sans profondeur donne la valeur exacte.
                go = "go" if req.get("game") == "telo" else f"go depth {depth}"
                best, info = eng.go(["ucinewgame", pos, go])
                out.append({"ply": k, "best": None if best == "(none)" else best, "score": info.get("score"),
                            "depth": info.get("depth"), "pv": info.get("pv", [])})
        finally:
            eng.close()
    finally:
        search_slots.release()
    return {"evals": out}


# ---------------------------------------------------------------------------------------------------------
# Historique des parties (SQLite). Une ligne par partie terminée : colonnes indexées pour les listes et les
# statistiques, et l'enregistrement complet (coups, résultat, joueurs, analyse éventuelle) en JSON.
# ---------------------------------------------------------------------------------------------------------
RESULTS = {"1-0", "0-1", "½-½", "*"}
MODES = {"computer", "friend", "auto"}


def db():
    con = sqlite3.connect(args.db, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created REAL NOT NULL,
            game TEXT NOT NULL,
            mode TEXT NOT NULL,
            level INTEGER,
            color TEXT,
            result TEXT NOT NULL,
            outcome TEXT,          -- point de vue du joueur humain contre l'ordinateur : win / loss / draw
            plies INTEGER NOT NULL,
            white TEXT, black TEXT,
            analysed INTEGER NOT NULL DEFAULT 0,
            record TEXT NOT NULL)""")
        con.execute("CREATE INDEX IF NOT EXISTS games_created ON games(created DESC)")
        con.execute("""CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            color TEXT NOT NULL,
            created REAL NOT NULL,
            puzzle TEXT NOT NULL DEFAULT '{}',   -- classement Elo et séries des puzzles (JSON)
            learn TEXT NOT NULL DEFAULT '{}')""")  # étoiles du tutoriel par exercice (JSON)
        cols = [r["name"] for r in con.execute("PRAGMA table_info(games)")]
        if "profile_id" not in cols:  # migration : les parties d'avant les profils restent « invité » (NULL)
            con.execute("ALTER TABLE games ADD COLUMN profile_id INTEGER")
        con.execute("CREATE INDEX IF NOT EXISTS games_profile ON games(profile_id, created DESC)")
        pcols = [r["name"] for r in con.execute("PRAGMA table_info(profiles)")]
        if "rating" not in pcols:  # Elo du joueur contre l'ordinateur (JSON : elo, games, peak)
            con.execute("ALTER TABLE profiles ADD COLUMN rating TEXT NOT NULL DEFAULT '{}'")


def clean_record(rec):
    """Valide et normalise un enregistrement de partie envoyé par la page."""
    if not isinstance(rec, dict):
        raise EngineError("partie invalide")
    game = rec.get("game", "tsivy")
    if game not in GAMES:
        raise EngineError(f"jeu inconnu : {game}")
    moves = check_moves(rec.get("moves", []))
    result = rec.get("result") or {}
    score = result.get("score", "*") if isinstance(result, dict) else "*"
    if score not in RESULTS:
        raise EngineError("résultat invalide")
    mode = rec.get("mode") if rec.get("mode") in MODES else "friend"
    fen = rec.get("fen") or ""
    if fen and not FEN_RE.match(fen):
        raise EngineError("FEN invalide")
    txt = lambda v, n=60: str(v)[:n] if v is not None else None
    out = {
        "game": game, "vela": rec.get("vela") if rec.get("vela") in ("W", "B") else "", "fen": fen, "moves": moves,
        "result": {k: txt(result.get(k), 200) for k in ("score", "t", "w", "raw") if isinstance(result, dict) and result.get(k)},
        "mode": mode, "level": int(rec.get("level") or 0) or None, "color": rec.get("color") if rec.get("color") in ("W", "B") else None,
        "clock": txt(rec.get("clock"), 10), "net": txt(rec.get("net"), 40), "variant": txt(rec.get("variant"), 12),
        "white": txt(rec.get("white")), "black": txt(rec.get("black")), "date": txt(rec.get("date"), 30),
        "strength": int(rec["strength"]) if isinstance(rec.get("strength"), int) and 0 <= rec["strength"] < len(STRENGTHS) else None,
    }
    ana = rec.get("analysis")
    if isinstance(ana, dict) and isinstance(ana.get("evals"), list) and len(ana["evals"]) <= MAX_PLIES + 1:
        out["analysis"] = {"depth": int(ana.get("depth") or 0), "evals": ana["evals"]}
    return out


def outcome_of(r):
    if r["mode"] != "computer" or not r["color"] or r["result"].get("score", "*") == "*":
        return None
    sc = r["result"]["score"]
    if sc == "½-½":
        return "draw"
    return "win" if (sc == "1-0") == (r["color"] == "W") else "loss"


def save_game(rec):
    r = clean_record(rec)
    pid = rec.get("profile_id")
    pid = int(pid) if isinstance(pid, int) or (isinstance(pid, str) and pid.isdigit()) else None
    with db() as con:
        if pid is not None and not con.execute("SELECT 1 FROM profiles WHERE id = ?", (pid,)).fetchone():
            pid = None
        rated = rate_game(con, pid, r)
        if rated:
            r["rating"] = rated
        cur = con.execute(
            "INSERT INTO games (created, game, mode, level, color, result, outcome, plies, white, black, analysed, record,"
            " profile_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), r["game"], r["mode"], r["level"], r["color"], r["result"].get("score", "*"), outcome_of(r),
             len(r["moves"]), r["white"], r["black"], int("analysis" in r), json.dumps(r, ensure_ascii=False), pid))
        return {"id": cur.lastrowid, "rating": rated}


RATING_START = 1000


def rate_game(con, pid, r):
    """Elo du joueur après une partie classée : profil, contre l'ordinateur, Fanoron-Tsivy sans vela ni position
    de départ imposée, réglage calibré. E = 1 / (1 + 10^((moteur - joueur) / 400)) ; K = 40 pour les 20 premières
    parties, puis 24. Renvoie {before, after, delta, engine_elo} ou None (partie non classée)."""
    outcome = outcome_of(r)
    elos = strength_elos()
    if pid is None or outcome is None or r["game"] != "tsivy" or r["vela"] or r["fen"] or r["strength"] is None or not elos:
        return None
    row = con.execute("SELECT rating FROM profiles WHERE id = ?", (pid,)).fetchone()
    st = {"elo": RATING_START, "games": 0, "peak": RATING_START, **json.loads(row["rating"] or "{}")}
    eng = elos[r["strength"]]
    e = 1 / (1 + 10 ** ((eng - st["elo"]) / 400))
    k = 40 if st["games"] < 20 else 24
    delta = round(k * ({"win": 1, "draw": 0.5, "loss": 0}[outcome] - e))
    before = st["elo"]
    st["elo"] = max(100, before + delta)
    st["games"] += 1
    st["peak"] = max(st["peak"], st["elo"])
    con.execute("UPDATE profiles SET rating = ? WHERE id = ?", (json.dumps(st), pid))
    return {"before": before, "after": st["elo"], "delta": st["elo"] - before, "engine_elo": eng}


def update_analysis(gid, ana):
    with db() as con:
        row = con.execute("SELECT record FROM games WHERE id = ?", (gid,)).fetchone()
        if not row:
            raise EngineError("partie introuvable")
        r = json.loads(row["record"])
        r["analysis"] = clean_record({**r, "analysis": ana}).get("analysis")
        con.execute("UPDATE games SET record = ?, analysed = ? WHERE id = ?",
                    (json.dumps(r, ensure_ascii=False), int(bool(r["analysis"])), gid))
    return {"id": gid}


def profile_filter(q, where, params):
    """?profile=<id> : parties de ce profil ; ?profile=guest : parties sans profil ; absent : toutes."""
    p = q.get("profile", [""])[0]
    if p == "guest":
        where.append("profile_id IS NULL")
    elif p.isdigit():
        where.append("profile_id = ?")
        params.append(int(p))


def list_games(q):
    where, params = [], []
    profile_filter(q, where, params)
    for key in ("game", "mode", "outcome"):
        v = q.get(key, [""])[0]
        if v:
            where.append(f"{key} = ?")
            params.append(v)
    limit = max(1, min(int(q.get("limit", ["30"])[0]), 200))
    offset = max(0, int(q.get("offset", ["0"])[0]))
    sql = ("SELECT id, created, game, mode, level, color, result, outcome, plies, white, black, analysed,"
           " json_extract(record, '$.result') AS res, json_extract(record, '$.vela') AS vela,"
           " json_extract(record, '$.rating.delta') AS elo_delta, json_extract(record, '$.rating.engine_elo') AS engine_elo FROM games"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created DESC LIMIT ? OFFSET ?")
    with db() as con:
        rows = [dict(r) for r in con.execute(sql, params + [limit + 1, offset])]
        for r in rows:
            r["res"] = json.loads(r["res"]) if r["res"] else {}
    return {"games": rows[:limit], "more": len(rows) > limit}


def get_game(gid):
    with db() as con:
        row = con.execute("SELECT id, created, record FROM games WHERE id = ?", (gid,)).fetchone()
    if not row:
        raise EngineError("partie introuvable")
    return {"id": row["id"], "created": row["created"], **json.loads(row["record"])}


def delete_game(gid):
    with db() as con:
        con.execute("DELETE FROM games WHERE id = ?", (gid,))
    return {"deleted": gid}


def game_stats(q=None):
    """Bilan contre l'ordinateur par jeu et niveau (point de vue du joueur), et total des parties."""
    where, params = [], []
    profile_filter(q or {}, where, params)
    cond = " AND ".join(where)
    with db() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT game, level, SUM(outcome = 'win') AS win, SUM(outcome = 'draw') AS draw, SUM(outcome = 'loss') AS loss"
            " FROM games WHERE outcome IS NOT NULL" + (" AND " + cond if cond else "")
            + " GROUP BY game, level ORDER BY game, level", params)]
        total = con.execute("SELECT COUNT(*) FROM games" + (" WHERE " + cond if cond else ""), params).fetchone()[0]
    return {"total": total, "vs_engine": rows}


# ---------------------------------------------------------------------------------------------------------
# Profils : pas d'authentification (service réservé au réseau local) ; un profil regroupe ses parties, son
# classement de puzzles (Elo calculé ici, même formule que la page auparavant) et ses étoiles du tutoriel.
# ---------------------------------------------------------------------------------------------------------
PROFILE_COLORS = {"#a84f28", "#3e6a8a", "#4f7a3a", "#8a5a9e", "#b8860b", "#2f7f7a", "#9e3d4f", "#5b5f66"}
PUZZLE_DEFAULT = {"rating": 1000, "played": 0, "solved": 0, "streak": 0, "best": 0, "seen": []}


def profile_row(con, pid):
    row = con.execute("SELECT * FROM profiles WHERE id = ?", (pid,)).fetchone()
    if not row:
        raise EngineError("profil introuvable")
    games = con.execute("SELECT COUNT(*) FROM games WHERE profile_id = ?", (pid,)).fetchone()[0]
    return {"id": row["id"], "name": row["name"], "color": row["color"], "created": row["created"], "games": games,
            "puzzle": {**PUZZLE_DEFAULT, **json.loads(row["puzzle"])}, "learn": json.loads(row["learn"]),
            "rating": {"elo": RATING_START, "games": 0, "peak": RATING_START, **json.loads(row["rating"] or "{}")}}


def clean_profile(req, partial=False):
    out = {}
    if "name" in req or not partial:
        name = " ".join(str(req.get("name", "")).split())[:30]
        if not name:
            raise EngineError("nom de profil vide")
        out["name"] = name
    if "color" in req or not partial:
        color = req.get("color")
        out["color"] = color if color in PROFILE_COLORS else "#a84f28"
    return out


def list_profiles():
    with db() as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM profiles ORDER BY name COLLATE NOCASE")]
        out = []
        for i in ids:
            p = profile_row(con, i)
            out.append({k: p[k] for k in ("id", "name", "color", "games")} | {"rating": p["puzzle"]["rating"],
                                                                             "elo": p["rating"]["elo"], "rated": p["rating"]["games"]})
    return {"profiles": out}


def create_profile(req):
    p = clean_profile(req)
    with db() as con:
        try:
            cur = con.execute("INSERT INTO profiles (name, color, created) VALUES (?,?,?)", (p["name"], p["color"], time.time()))
        except sqlite3.IntegrityError:
            raise EngineError("ce nom de profil existe déjà")
        return profile_row(con, cur.lastrowid)


def update_profile(pid, req):
    p = clean_profile(req, partial=True)
    with db() as con:
        profile_row(con, pid)
        try:
            for k, v in p.items():
                con.execute(f"UPDATE profiles SET {k} = ? WHERE id = ?", (v, pid))
        except sqlite3.IntegrityError:
            raise EngineError("ce nom de profil existe déjà")
        return profile_row(con, pid)


def delete_profile(pid):
    with db() as con:
        con.execute("UPDATE games SET profile_id = NULL WHERE profile_id = ?", (pid,))  # parties gardées, sans profil
        con.execute("DELETE FROM profiles WHERE id = ?", (pid,))
    return {"deleted": pid}


def puzzle_result(pid, req):
    """Résultat d'un puzzle : E = 1 / (1 + 10^((puzzle - joueur) / 400)), K = 60 pour les 20 premiers puis 30."""
    pz_id = str(req.get("puzzle", ""))[:12]
    pz_rating = int(req.get("rating", 1000))
    win = bool(req.get("win"))
    with db() as con:
        st = profile_row(con, pid)["puzzle"]
        k = 60 if st["played"] < 20 else 30
        e = 1 / (1 + 10 ** ((pz_rating - st["rating"]) / 400))
        delta = round(k * ((1 if win else 0) - e))
        st["rating"] = max(400, st["rating"] + delta)
        st["played"] += 1
        if win:
            st["solved"] += 1
            st["streak"] += 1
            st["best"] = max(st["best"], st["streak"])
        else:
            st["streak"] = 0
        st["seen"] = ([s for s in st["seen"] if s != pz_id] + [pz_id])[-800:]
        con.execute("UPDATE profiles SET puzzle = ? WHERE id = ?", (json.dumps(st), pid))
    return {"puzzle": st, "delta": delta}


def learn_result(pid, req):
    step, stars = str(req.get("step", ""))[:40], max(0, min(3, int(req.get("stars", 0))))
    with db() as con:
        learn = profile_row(con, pid)["learn"]
        if step:
            learn[step] = max(learn.get(step, 0), stars)
        con.execute("UPDATE profiles SET learn = ? WHERE id = ?", (json.dumps(learn), pid))
    return {"learn": learn}


def import_progress(pid, req):
    """Reprise, dans un profil, de la progression gardée jusqu'ici dans le navigateur (au plus fort des deux)."""
    with db() as con:
        p = profile_row(con, pid)
        learn = p["learn"]
        for step, stars in (req.get("learn") or {}).items():
            if isinstance(stars, int):
                learn[str(step)[:40]] = max(learn.get(str(step)[:40], 0), max(0, min(3, stars)))
        pz, loc = p["puzzle"], req.get("puzzle") or {}
        if isinstance(loc, dict) and int(loc.get("played", 0)) > pz["played"]:
            pz = {**PUZZLE_DEFAULT, **{k: loc[k] for k in PUZZLE_DEFAULT if k in loc}}
            pz["seen"] = [str(s)[:12] for s in pz["seen"]][-800:]
        con.execute("UPDATE profiles SET learn = ?, puzzle = ? WHERE id = ?", (json.dumps(learn), json.dumps(pz), pid))
        return profile_row(con, pid)


# ---------------------------------------------------------------------------------------------------------
# Analyse en continu : une session par onglet (identifiant choisi par la page). Démarrer une analyse remplace
# celle de la session ; la page interroge l'état toutes les ~0,5 s ; sans interrogation pendant ANA_IDLE s, ou
# après ANA_MAX s, l'analyse s'arrête (le moteur reçoit "stop"). Chaque analyse occupe un créneau de recherche.
# ---------------------------------------------------------------------------------------------------------
ANA_MAX, ANA_IDLE = 120, 6
analyses, analyses_lock = {}, threading.Lock()


class Analysis:
    def __init__(self, sid, req):
        self.sid, self.req = sid, req
        self.lines, self.depth, self.done, self.reason = {}, 0, False, ""
        self.last_poll = self.started = time.time()
        self.eng = None
        self.lock = threading.Lock()
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        if not search_slots.acquire(timeout=5):
            self.done, self.reason = True, "busy"
            return
        try:
            mpv = max(1, min(int(self.req.get("multipv", 3)), 5))
            opts = net_options(self.req) + [f"setoption name MultiPV value {mpv}"]
            self.eng = Engine(timeout=ANA_MAX + 15)
            self.eng.send(opts + preamble(self.req) + ["go infinite"])
            for line in self.eng.p.stdout:
                if line.startswith("info string illegal move"):
                    self.reason = "illegal"
                    break
                if line.startswith("info depth"):
                    info = parse_info(line)
                    k = info.get("multipv", 1)
                    with self.lock:
                        if info.get("depth", 0) > self.depth:
                            # nouvelle profondeur : les lignes au-delà du nombre trouvé à cette profondeur disparaissent
                            self.lines = {kk: v for kk, v in self.lines.items() if kk <= k}
                            self.depth = info["depth"]
                        self.lines[k] = info
                elif line.startswith("bestmove"):
                    break
        except (OSError, ValueError, EngineError) as e:
            self.reason = str(e)
        finally:
            if self.eng:
                self.eng.close()
            search_slots.release()
            self.done = True

    def stop(self, reason="stopped"):
        if not self.done and self.eng and self.eng.p.poll() is None:
            self.reason = self.reason or reason
            try:
                self.eng.send(["stop"])
            except OSError:
                pass

    def state(self):
        self.last_poll = time.time()
        with self.lock:
            lines = [self.lines[k] for k in sorted(self.lines)]
        return {"sid": self.sid, "depth": self.depth, "lines": lines, "done": self.done, "reason": self.reason,
                "elapsed": round(time.time() - self.started, 1)}


def analysis_janitor():
    while True:
        time.sleep(2)
        now = time.time()
        with analyses_lock:
            for sid, a in list(analyses.items()):
                if now - a.last_poll > ANA_IDLE:
                    a.stop("idle")
                elif now - a.started > ANA_MAX:
                    a.stop("time")
                if a.done and now - a.last_poll > 60:
                    del analyses[sid]


def start_analysis(req):
    sid = str(req.get("sid", ""))[:40]
    if not sid:
        raise EngineError("session manquante")
    check_moves(req.get("moves", []))
    with analyses_lock:
        old = analyses.get(sid)
        if old:
            old.stop("replaced")
        a = analyses[sid] = Analysis(sid, req)
    return a.state()


def poll_analysis(sid):
    with analyses_lock:
        a = analyses.get(sid)
    if not a:
        raise EngineError("analyse inconnue")
    return a.state()


def stop_analysis(sid):
    with analyses_lock:
        a = analyses.get(sid)
    if a:
        a.stop("user")
    return {"stopped": sid}


# Application installable (PWA) : manifeste, service worker (à la racine pour couvrir tout le site), icônes.
PWA_FILES = {"/manifest.webmanifest": ("pwa/manifest.webmanifest", "application/manifest+json"),
             "/sw.js": ("pwa/sw.js", "text/javascript; charset=utf-8"),
             "/favicon.ico": ("pwa/favicon-32.png", "image/png")}
PWA_ICONS = {"icon-192.png", "icon-512.png", "maskable-512.png", "apple-touch-icon.png", "favicon-32.png"}


def available_nets():
    d = Path(args.nets)
    if not d.is_dir():
        return {}
    return {f.stem: str(f.resolve()) for f in sorted(d.glob("*.nnue"))}


class Handler(BaseHTTPRequestHandler):
    server_version = "FanoronaGUI/1.0"

    def log_message(self, fmt, *a):
        if args.verbose:
            super().log_message(fmt, *a)

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/labo" or path.startswith("/api/labo/"):
            return self.labo_get(path, query)
        if path.startswith("/api/analyse/"):
            try:
                return self.send_json(200, poll_analysis(path.split("/")[3]))
            except EngineError as e:
                return self.send_json(400, {"error": str(e)})
        if path.startswith("/api/profiles"):
            try:
                if path == "/api/profiles":
                    return self.send_json(200, list_profiles())
                with db() as con:
                    return self.send_json(200, profile_row(con, int(path.split("/")[3])))
            except (EngineError, ValueError) as e:
                return self.send_json(400, {"error": str(e)})
        if path.startswith("/api/games"):
            try:
                if path == "/api/games":
                    return self.send_json(200, list_games(parse_qs(query)))
                if path == "/api/games/stats":
                    return self.send_json(200, game_stats(parse_qs(query)))
                return self.send_json(200, get_game(int(path.rsplit("/", 1)[1])))
            except (EngineError, ValueError) as e:
                return self.send_json(400, {"error": str(e)})
        static = PWA_FILES.get(path) or (("pwa/" + path[7:], "image/png") if path.startswith("/icons/") and
                                          path[7:] in PWA_ICONS else None)
        if static:
            body = (HERE / static[0]).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", static[1])
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache" if path == "/sw.js" else "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/lessons.json", "/puzzles.json", "/i18n.json"):
            body = (HERE / path[1:]).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        elif path in ("/", "/index.html"):
            body = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/config":
            nets = list(available_nets())
            # réseau adopté le plus récent d'abord (net_v6 : couches empilées, 2026-10-03), sinon net_v3
            default = next((n for n in ("net_v6", "net_v3") if n in nets), nets[-1] if nets else None)
            self.send_json(200, {"nets": nets, "defaultNet": default, "maxMovetime": MAX_MOVETIME,
                                 "levels": [{"id": k, "name": v["name"], "timed": v["mode"] != "sample",
                                             "strength": v["strength"]} for k, v in LEVELS.items()],
                                 "strengths": [{"id": i, "elo": (strength_elos() or [None] * len(STRENGTHS))[i],
                                                "mode": v["mode"], "movetime": v.get("movetime")}
                                               for i, v in enumerate(STRENGTHS)]})
        else:
            self.send_json(404, {"error": "introuvable"})

    def labo_get(self, path, query):
        """Page Labo (entraînement du modèle, voir labo.py) : lecture seule des journaux de fanorona-dev."""
        try:
            if path == "/labo":
                body = (HERE / "labo.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/labo/live":
                self.send_json(200, labo.tracker().snapshot())
            elif path == "/api/labo/trainings":
                self.send_json(200, labo.trainings())
            elif path == "/api/labo/training":
                self.send_json(200, labo.training(parse_qs(query).get("name", [""])[0]))
            elif path == "/api/labo/lineage":
                self.send_json(200, labo.lineage())
            else:
                self.send_json(404, {"error": "introuvable"})
        except (ValueError, OSError) as e:
            self.send_json(400, {"error": str(e)})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > (1_000_000 if self.path.startswith("/api/games") else 100_000):
                raise EngineError("requête trop grande")
            req = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/state":
                self.send_json(200, get_state(req))
            elif self.path == "/api/eval":
                self.send_json(200, evaluate_plies(req))
            elif self.path == "/api/games":
                self.send_json(200, save_game(req))
            elif self.path.startswith("/api/games/") and self.path.endswith("/analysis"):
                self.send_json(200, update_analysis(int(self.path.split("/")[3]), req))
            elif self.path == "/api/analyse":
                self.send_json(200, start_analysis(req))
            elif self.path.startswith("/api/analyse/") and self.path.endswith("/stop"):
                self.send_json(200, stop_analysis(self.path.split("/")[3]))
            elif self.path == "/api/profiles":
                self.send_json(200, create_profile(req))
            elif self.path.startswith("/api/profiles/"):
                parts = self.path.split("/")
                pid, action = int(parts[3]), (parts[4] if len(parts) > 4 else "")
                handler = {"": update_profile, "puzzle": puzzle_result, "learn": learn_result, "import": import_progress}.get(action)
                if not handler:
                    raise EngineError("action inconnue")
                self.send_json(200, handler(pid, req))
            elif self.path == "/api/go":
                res = search(req)
                res["state"] = get_state({**req, "moves": req.get("moves", []) + [res["bestmove"]]})
                self.send_json(200, res)
            else:
                self.send_json(404, {"error": "introuvable"})
        except (EngineError, ValueError, json.JSONDecodeError) as e:
            self.send_json(400, {"error": str(e)})

    def do_DELETE(self):
        try:
            if self.path.startswith("/api/profiles/"):
                return self.send_json(200, delete_profile(int(self.path.split("/")[3])))
            if not self.path.startswith("/api/games/"):
                return self.send_json(404, {"error": "introuvable"})
            self.send_json(200, delete_game(int(self.path.split("/")[3])))
        except (EngineError, ValueError) as e:
            self.send_json(400, {"error": str(e)})


def main():
    global args, search_slots
    root = HERE.parent.parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--engine", default=str(root / "fanorona"))
    ap.add_argument("--nets", default=str(root / "checkpoints"), help="dossier des réseaux .nnue")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--max-searches", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--db", default=str(root / "data" / "gui_games.db"), help="historique des parties (SQLite)")
    args = ap.parse_args()
    args.engine = str(Path(args.engine).resolve())
    args.cwd = str(root)
    search_slots = threading.BoundedSemaphore(args.max_searches)
    init_db()
    threading.Thread(target=analysis_janitor, daemon=True).start()
    print(f"Fanorona GUI sur http://{args.host}:{args.port}/ (moteur {args.engine})", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
