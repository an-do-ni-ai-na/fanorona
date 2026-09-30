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
LEVELS = {
    1: {"name": "Débutant", "mode": "sample", "depth": 1, "temp": 250},
    2: {"name": "Facile", "mode": "sample", "depth": 2, "temp": 120},
    3: {"name": "Intermédiaire", "mode": "sample", "depth": 3, "temp": 50},
    4: {"name": "Confirmé", "mode": "depth", "depth": 4},
    5: {"name": "Expert", "mode": "depth", "depth": 5},
    6: {"name": "Maître", "mode": "full"},
}
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
    with db() as con:
        cur = con.execute(
            "INSERT INTO games (created, game, mode, level, color, result, outcome, plies, white, black, analysed, record)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), r["game"], r["mode"], r["level"], r["color"], r["result"].get("score", "*"), outcome_of(r),
             len(r["moves"]), r["white"], r["black"], int("analysis" in r), json.dumps(r, ensure_ascii=False)))
        return {"id": cur.lastrowid}


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


def list_games(q):
    where, params = [], []
    for key in ("game", "mode", "outcome"):
        v = q.get(key, [""])[0]
        if v:
            where.append(f"{key} = ?")
            params.append(v)
    limit = max(1, min(int(q.get("limit", ["30"])[0]), 200))
    offset = max(0, int(q.get("offset", ["0"])[0]))
    sql = ("SELECT id, created, game, mode, level, color, result, outcome, plies, white, black, analysed,"
           " json_extract(record, '$.result') AS res, json_extract(record, '$.vela') AS vela FROM games"
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


def game_stats():
    """Bilan contre l'ordinateur par jeu et niveau (point de vue du joueur), et total des parties."""
    with db() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT game, level, SUM(outcome = 'win') AS win, SUM(outcome = 'draw') AS draw, SUM(outcome = 'loss') AS loss"
            " FROM games WHERE outcome IS NOT NULL GROUP BY game, level ORDER BY game, level")]
        total = con.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    return {"total": total, "vs_engine": rows}


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
        if path.startswith("/api/games"):
            try:
                if path == "/api/games":
                    return self.send_json(200, list_games(parse_qs(query)))
                if path == "/api/games/stats":
                    return self.send_json(200, game_stats())
                return self.send_json(200, get_game(int(path.rsplit("/", 1)[1])))
            except (EngineError, ValueError) as e:
                return self.send_json(400, {"error": str(e)})
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
            default = "net_v3" if "net_v3" in nets else (nets[-1] if nets else None)
            self.send_json(200, {"nets": nets, "defaultNet": default, "maxMovetime": MAX_MOVETIME,
                                 "levels": [{"id": k, "name": v["name"], "timed": v["mode"] != "sample"}
                                            for k, v in LEVELS.items()]})
        else:
            self.send_json(404, {"error": "introuvable"})

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
    print(f"Fanorona GUI sur http://{args.host}:{args.port}/ (moteur {args.engine})", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
