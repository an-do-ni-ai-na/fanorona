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
    GET  /api/config                       -> réseaux NNUE disponibles, limites
    POST /api/state {moves, variant}       -> fen, coups légaux, statut
    POST /api/go    {moves, variant, movetime, net}  -> meilleur coup + infos de recherche, puis nouvel état
"""

import argparse
import json
import os
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOVE_RE = re.compile(r"^(?:[a-i][1-5][AW]?){2,}$")
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


def preamble(req):
    """Commandes communes : variante de règles, puis position."""
    moves = check_moves(req.get("moves", []))
    cmds = []
    if req.get("variant") == "mandatory":
        cmds.append("setoption name MandatoryContinuation value true")
    cmds.append("position startpos" + (" moves " + " ".join(moves) if moves else ""))
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


def search(req):
    movetime = max(50, min(int(req.get("movetime", 1000)), MAX_MOVETIME))
    cmds = []
    net = req.get("net")
    if net:
        nets = available_nets()
        if net not in nets:
            raise EngineError(f"réseau inconnu : {net}")
        cmds += [f"setoption name EvalFile value {nets[net]}", "setoption name UseNNUE value true"]
    cmds += preamble(req) + [f"go movetime {movetime}"]

    if not search_slots.acquire(timeout=movetime / 1000 + 30):
        raise EngineError("moteur occupé, réessayez")
    try:
        # stdin doit rester ouvert jusqu'au bestmove : une fin de stdin vaut "quit", qui arrête la recherche.
        p = subprocess.Popen([args.engine], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
                             cwd=args.cwd)
        timer = threading.Timer(movetime / 1000 + 15, p.kill)
        timer.start()
        try:
            p.stdin.write("\n".join(cmds) + "\n")
            p.stdin.flush()
            last, best = {}, None
            for line in p.stdout:
                if line.startswith("info string illegal move") or (line.startswith("info string nnue:")
                                                                   and "chargé" not in line):
                    raise EngineError(line.strip()[len("info string "):])
                if line.startswith("info depth"):
                    last = parse_info(line)
                elif line.startswith("bestmove"):
                    best = line.split()[1]
                    break
            if best is None:
                raise EngineError("pas de réponse du moteur (délai dépassé)")
            p.stdin.write("quit\n")
            p.stdin.close()
            p.wait(timeout=5)
        finally:
            timer.cancel()
            if p.poll() is None:
                p.kill()
    finally:
        search_slots.release()
    return {"bestmove": best, "info": last}


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
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            body = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/config":
            nets = list(available_nets())
            default = "net_v3" if "net_v3" in nets else (nets[-1] if nets else None)
            self.send_json(200, {"nets": nets, "defaultNet": default, "maxMovetime": MAX_MOVETIME})
        else:
            self.send_json(404, {"error": "introuvable"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > 100_000:
                raise EngineError("requête trop grande")
            req = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/state":
                self.send_json(200, get_state(req))
            elif self.path == "/api/go":
                res = search(req)
                res["state"] = get_state({**req, "moves": req.get("moves", []) + [res["bestmove"]]})
                self.send_json(200, res)
            else:
                self.send_json(404, {"error": "introuvable"})
        except (EngineError, ValueError, json.JSONDecodeError) as e:
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
    args = ap.parse_args()
    args.engine = str(Path(args.engine).resolve())
    args.cwd = str(root)
    search_slots = threading.BoundedSemaphore(args.max_searches)
    print(f"Fanorona GUI sur http://{args.host}:{args.port}/ (moteur {args.engine})", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
