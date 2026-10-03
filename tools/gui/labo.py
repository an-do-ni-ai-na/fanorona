"""Données de la page Labo (/labo) : entraînement du modèle en direct et en historique.

Sources, toutes en lecture seule :
- /var/log/fanorona/*.jsonl : journaux live de match.py (SPRT, matchs) et gensfen_dist.py, suivis en continu par
  `LiveTracker` (lecture incrémentale : seuls les octets ajoutés depuis le dernier passage sont lus) ;
- logs/train_*.log : sortie de train.py (une ligne par epoch) -> courbes d'apprentissage ;
- logs/gen*.log, logs/sprt_*.log, logs/depth7_*.log : gensfen lancé à la main, résultats de tests ;
- tools/gui/lineage.json : généalogie des réseaux (métadonnées seulement, les chiffres viennent des journaux) ;
- Prometheus (pve-exporter) : charge des machines de calcul, si joignable.
"""
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
LOGS = ROOT / "logs"
LIVE_DIR = Path("/var/log/fanorona")
PROMETHEUS = "http://10.10.10.35:9090"
MACHINES = {"qemu/3180": "fanorona-dev", "lxc/3190": "fanorona-c1", "lxc/3191": "fanorona-c3"}

ACTIVE_S = 120          # un run sans nouvelle ligne depuis 2 min est considéré terminé
KEEP_S = 30 * 60        # ... et reste affiché 30 min (« terminé il y a ... »)
GAME_STALE_S = 30       # une partie sans coup depuis 30 s n'est plus affichée sur un plateau
MAX_SERIES = 400        # points max par courbe envoyée au navigateur (LLR, débit)

EPOCH_RE = re.compile(r"^epoch\s+(\d+)\s+train_loss\s+([\d.]+)\s+val_loss\s+([\d.]+)(?:\s+\((\d+) s\))?")
EXPORT_RE = re.compile(r"-> (checkpoints/\S+\.nnue)")
GEN_RE = re.compile(r"^info string gensfen (\d+)/(\d+) positions, (\d+) parties, (\d+) pos/s")
SPRT_LINE_RE = re.compile(r"^partie (\d+).*\|\s*W(\d+) D(\d+) L(\d+)\s+LLR ([+-]?[\d.]+) \(bornes \[([-\d.]+), ([-\d.]+)\]\)")
MATCH_LINE_RE = re.compile(r"^partie (\d+).*\|\s*engine1 \+(\d+) =(\d+) -(\d+)")
SPRT_HEAD_RE = re.compile(r"^SPRT elo0=([-\d.]+) elo1=([-\d.]+)")
SAFE_NAME = re.compile(r"^[\w.-]+$")


# ------------------------------------------------------------------------------------------------ statistiques
def elo_from_score(s):
    s = min(max(s, 1e-6), 1 - 1e-6)
    return -400 * math.log10(1 / s - 1)


def score_stats(w, d, l):
    """Score, Elo et intervalle à 95 % (variance trinomiale par partie)."""
    n = w + d + l
    if n == 0:
        return None
    s = (w + d / 2) / n
    var = (w * (1 - s) ** 2 + d * (0.5 - s) ** 2 + l * s ** 2) / n
    se = math.sqrt(var / n) if n > 1 else 0.5
    return {"games": n, "w": w, "d": d, "l": l, "score": s, "elo": elo_from_score(s),
            "elo_lo": elo_from_score(s - 1.96 * se), "elo_hi": elo_from_score(s + 1.96 * se)}


def engine_label(opts):
    """Nom lisible d'un moteur d'après ses options UCI (match.py --engineN-opts)."""
    m = re.search(r"EvalFile=([^,]+)", opts or "")
    if m:
        return Path(m.group(1)).stem
    return "HCE" if opts is not None else None


def downsample(points, n=MAX_SERIES):
    if len(points) <= n:
        return points
    step = len(points) / n
    out = [points[int(i * step)] for i in range(n)]
    out[-1] = points[-1]
    return out


# ------------------------------------------------------------------------------------------------ journaux texte
def parse_training(path):
    epochs, export = [], None
    with open(path, errors="replace") as f:
        for line in f:
            m = EPOCH_RE.match(line)
            if m:
                epochs.append({"epoch": int(m.group(1)), "train": float(m.group(2)), "val": float(m.group(3)),
                               "t": int(m.group(4)) if m.group(4) else None})
            elif (e := EXPORT_RE.search(line)):
                export = e.group(1)
    return epochs, export


def trainings():
    """Tous les entraînements (logs/train*.log contenant au moins une epoch), du plus récent au plus ancien."""
    out, now = [], time.time()
    try:  # nom du réseau d'après la généalogie (train_s5.log -> net_v6)
        names = {n["train_log"]: n["name"] for n in json.loads((HERE / "lineage.json").read_text())["nets"] if n.get("train_log")}
    except (OSError, ValueError, KeyError):
        names = {}
    for p in sorted(LOGS.glob("train*.log"), key=lambda p: p.stat().st_mtime, reverse=True):
        epochs, export = parse_training(p)
        if not epochs:
            continue
        best = min(epochs, key=lambda e: e["val"])
        mtime = p.stat().st_mtime
        out.append({"name": p.stem, "net": names.get(p.name), "epochs": len(epochs), "final_val": epochs[-1]["val"],
                    "best_val": best["val"], "best_epoch": best["epoch"], "export": export,
                    "mtime": mtime, "active": now - mtime < ACTIVE_S and export is None})
    return out


def training(name):
    if not SAFE_NAME.match(name) or not name.startswith("train"):
        raise ValueError("nom d'entraînement invalide")
    p = LOGS / f"{name}.log"
    if not p.is_file():
        raise ValueError("entraînement introuvable")
    epochs, export = parse_training(p)
    return {"name": name, "epochs": epochs, "export": export, "mtime": p.stat().st_mtime}


def parse_test_log(name):
    """Résultat d'un journal de test (SPRT ou match) : dernier compteur W/D/L, verdict SPRT éventuel."""
    p = LOGS / name
    if not SAFE_NAME.match(name) or not p.is_file():
        return None
    res = {"log": name, "mtime": p.stat().st_mtime}
    w = d = l = None
    with open(p, errors="replace") as f:
        for line in f:
            if (m := SPRT_LINE_RE.match(line)):
                w, d, l = int(m.group(2)), int(m.group(3)), int(m.group(4))
                res["llr"], res["lower"], res["upper"] = float(m.group(5)), float(m.group(6)), float(m.group(7))
            elif (m := MATCH_LINE_RE.match(line)):
                w, d, l = int(m.group(2)), int(m.group(3)), int(m.group(4))
            elif (m := SPRT_HEAD_RE.match(line)):
                res["elo0"], res["elo1"] = float(m.group(1)), float(m.group(2))
            elif line.startswith("SPRT terminé"):
                res["verdict"] = "h1" if "H1" in line else "h0"
            elif line.startswith("SPRT arrêté"):
                res["verdict"] = "inconclusive"
    if w is None:
        return None
    res.update(score_stats(w, d, l))
    return res


def lineage():
    data = json.loads((HERE / "lineage.json").read_text())
    nets = data["nets"]
    known = {n["id"] for n in nets}
    for n in nets:
        for t in n.get("tests", []):
            t["result"] = parse_test_log(t["log"])
        if n.get("train_log"):
            p = LOGS / n["train_log"]
            if p.is_file():
                epochs, _ = parse_training(p)
                if epochs:
                    n["train"] = {"name": p.stem, "epochs": len(epochs), "final_val": epochs[-1]["val"]}
        n["known_parent"] = n.get("parent") in known
    # Elo cumulé le long de la lignée adoptée, à partir du test décisif de chaque génération
    by_id = {n["id"]: n for n in nets}
    for n in nets:
        if n["status"] == "base":
            n["elo_total"], n["elo_var"] = 0.0, 0.0
    changed = True
    while changed:
        changed = False
        for n in nets:
            if n["status"] != "adopted" or "elo_total" in n:
                continue
            parent = by_id.get(n.get("parent"))
            test = next((t for t in n.get("tests", []) if t.get("decides") and t.get("result")), None)
            if parent is None or "elo_total" not in parent or test is None:
                continue
            r = test["result"]
            half = (r["elo_hi"] - r["elo_lo"]) / 2 / 1.96
            n["elo_step"] = r["elo"]
            n["elo_total"] = parent["elo_total"] + r["elo"]
            n["elo_var"] = parent["elo_var"] + half ** 2
            changed = True
    for n in nets:
        if "elo_var" in n:
            n["elo_total_ci"] = 1.96 * math.sqrt(n.pop("elo_var"))
    return {"nets": nets}


# ------------------------------------------------------------------------------------------------ machines
def machines():
    """CPU / RAM / état des 3 machines de calcul via pve-exporter (None si Prometheus est injoignable)."""
    def q(expr):
        url = f"{PROMETHEUS}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
        with urllib.request.urlopen(url, timeout=2) as r:
            return {x["metric"]["id"]: float(x["value"][1]) for x in json.load(r)["data"]["result"]}
    ids = "|".join(MACHINES)
    try:
        up = q(f'max by (id) (pve_up{{id=~"{ids}"}})')
        cpu = q(f'max by (id) (pve_cpu_usage_ratio{{id=~"{ids}"}})')
        ncpu = q(f'max by (id) (pve_cpu_usage_limit{{id=~"{ids}"}})')
        mem = q(f'max by (id) (pve_memory_usage_bytes{{id=~"{ids}"}}) / max by (id) (pve_memory_size_bytes{{id=~"{ids}"}})')
    except Exception:
        return None
    return [{"name": name, "up": up.get(i, 0) > 0, "cpu": cpu.get(i, 0) * ncpu.get(i, 0), "ncpu": ncpu.get(i, 0),
             "mem": mem.get(i, 0)} for i, name in MACHINES.items()]


# ------------------------------------------------------------------------------------------------ suivi live
class Run:
    def __init__(self, path):
        self.path, self.run_id, self.offset, self.partial = path, path.stem, 0, b""
        self.run_type = None
        self.first_ts = self.last_ts = None
        self.hosts = {}
        self.sprt = None            # bornes et décision
        self.wdl = [0, 0, 0]
        self.llr = []               # (partie, llr)
        self.host_games = {}        # machine -> parties terminées
        self.host_recent = {}       # machine -> horodatages des parties récentes (cadence)
        self.nps = {}               # machine -> (somme, n) sur les lignes info récentes
        self.games = {}             # partie en cours -> dernier état (fen, coup, machine...)
        self.gensfen = {}           # processus -> dernière progression
        self.gen_rate = []          # (ts, pos/s total)
        self.done = None
        self.engines = None         # (engine1, engine2) d'après les options journalisées (match.py >= 2026-10-04)

    def feed(self, rec, catching_up):
        ev, ts = rec.get("event"), rec.get("ts", 0)
        self.run_type = self.run_type or rec.get("run_type")
        self.first_ts = self.first_ts or ts
        self.last_ts = ts
        if self.engines is None and "engine1_opts" in rec:
            self.engines = (engine_label(rec["engine1_opts"]), engine_label(rec["engine2_opts"]))
        if ev == "info":
            if catching_up:
                return
            g = rec.get("game")
            if g is not None and rec.get("fen"):
                self.games[g] = {"game": g, "host": rec.get("host"), "ply": rec.get("ply"), "fen": rec["fen"],
                                 "move": rec.get("move"), "engine": rec.get("engine"), "depth": rec.get("depth"),
                                 "score_cp": rec.get("score_cp"), "ts": ts}
            h = rec.get("host") or "?"
            s, n = self.nps.get(h, (0, 0))
            self.nps[h] = (s + rec.get("nps", 0), n + 1)
        elif ev in ("game_result", "sprt_update"):
            res = rec.get("engine1_result")
            if res in ("win", "draw", "loss"):
                self.wdl[("win", "draw", "loss").index(res)] += 1
            if ev == "sprt_update":
                self.wdl = [rec["wins"], rec["draws"], rec["losses"]]
                self.sprt = self.sprt or {}
                self.sprt.update(lower=rec["lower"], upper=rec["upper"])
                self.llr.append((sum(self.wdl), round(rec["llr"], 3)))
            h = rec.get("host") or "non renseignée"
            self.host_games[h] = self.host_games.get(h, 0) + 1
            self.host_recent.setdefault(h, []).append(ts)
            self.games.pop(rec.get("game"), None)
        elif ev == "sprt_done":
            self.done = {"decision": rec.get("decision"), "games": rec.get("games"), "llr": rec.get("llr")}
        elif ev == "match_hosts" or ev == "gensfen_start":
            self.hosts = rec.get("hosts", {})
            if ev == "gensfen_start":
                self.gen_target = rec.get("count")
        elif ev == "gensfen_progress":
            self.gensfen[(rec.get("host"), rec.get("proc"))] = {
                "host": rec.get("host"), "proc": rec.get("proc"), "written": rec.get("written", 0),
                "target": rec.get("target", 0), "pos_s": rec.get("pos_s", 0), "ts": ts}
            self.gen_rate.append((ts, sum(p["pos_s"] for p in self.gensfen.values())))
        elif ev == "gensfen_done":
            self.done = {"positions": rec.get("positions"), "seconds": rec.get("seconds")}

    def read(self):
        """Lit ce qui a été ajouté au fichier. Premier passage sur un gros fichier : les lignes « info » (une par
        coup) ne sont analysées que sur les 2 derniers Mo, le reste n'est filtré que pour les résultats."""
        size = self.path.stat().st_size
        if size < self.offset:          # fichier tronqué ou remplacé : on repart de zéro
            self.__init__(self.path)
        if size == self.offset:
            return
        catch_up_until = max(0, size - 2_000_000) if self.offset == 0 else 0
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            pos = self.offset - len(self.partial)       # position dans le fichier du début de `data`
            data = self.partial + f.read(size - self.offset)
        lines = data.split(b"\n")
        self.partial = lines.pop()
        for raw in lines:
            pos += len(raw) + 1
            catching_up = pos < catch_up_until
            if catching_up and b'"event": "info"' in raw:
                continue
            try:
                self.feed(json.loads(raw), catching_up)
            except (ValueError, KeyError, TypeError):
                continue
        self.offset = size

    def snapshot(self, now):
        active = now - (self.last_ts or 0) < ACTIVE_S and self.done is None
        rate = {}
        for h, tss in self.host_recent.items():
            recent = [t for t in tss if (self.last_ts or now) - t < 300]
            self.host_recent[h] = recent
            rate[h] = len(recent) / 5     # parties par minute sur les 5 dernières minutes
        nps = {h: s / n for h, (s, n) in self.nps.items() if n}
        self.nps = {}                     # moyenne glissante : remise à zéro à chaque instantané
        games = sorted((g for g in self.games.values() if now - g["ts"] < GAME_STALE_S),
                       key=lambda g: (g["host"] or "", g["game"]))
        snap = {"run_id": self.run_id, "run_type": self.run_type, "active": active, "first_ts": self.first_ts,
                "last_ts": self.last_ts, "hosts": self.hosts, "done": self.done, "engines": self.engines}
        if self.run_type in ("sprt", "match"):
            w, d, l = self.wdl
            snap.update(stats=score_stats(w, d, l), sprt=self.sprt, llr=downsample(self.llr),
                        host_games=self.host_games, host_rate=rate, nps=nps, games=games[:12])
        elif self.run_type == "gensfen":
            procs = sorted(self.gensfen.values(), key=lambda p: (p["host"] or "", p["proc"]))
            snap.update(procs=procs, target=getattr(self, "gen_target", None),
                        rate=downsample([(round(t), r) for t, r in self.gen_rate]))
        return snap


class LiveTracker:
    def __init__(self):
        self.runs, self.lock = {}, threading.Lock()
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                self.scan()
            except Exception:
                pass
            time.sleep(2)

    def scan(self):
        now = time.time()
        if not LIVE_DIR.is_dir():
            return
        for p in LIVE_DIR.glob("*.jsonl"):
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if now - mtime > KEEP_S:
                continue
            with self.lock:
                run = self.runs.setdefault(p.name, Run(p))
            run.read()
        with self.lock:
            for k in [k for k, r in self.runs.items() if now - (r.last_ts or 0) > KEEP_S]:
                del self.runs[k]

    def snapshot(self):
        now = time.time()
        with self.lock:
            runs = [r.snapshot(now) for r in self.runs.values() if r.run_type]
        runs.sort(key=lambda r: (not r["active"], -(r["last_ts"] or 0)))
        return {"now": now, "runs": runs, "manual": manual_activity(now), "machines": machines()}


def manual_activity(now):
    """Ce qui tourne hors des outils journalisés : entraînement (train.py -> logs/train*.log) et gensfen lancé à
    la main (logs/gen*.log), d'après les journaux modifiés récemment."""
    out = {"trainings": [], "gensfen": []}
    if not LOGS.is_dir():
        return out
    for p in LOGS.glob("train*.log"):
        if now - p.stat().st_mtime < ACTIVE_S:
            epochs, export = parse_training(p)
            if epochs and export is None:
                out["trainings"].append({"name": p.stem, "epochs": epochs})
    for p in LOGS.glob("gen*.log"):
        if now - p.stat().st_mtime < ACTIVE_S:
            last = None
            with open(p, errors="replace") as f:
                f.seek(max(0, p.stat().st_size - 4000))
                for line in f:
                    if (m := GEN_RE.match(line)):
                        last = m
            if last:
                w, t, g, r = map(int, last.groups())
                out["gensfen"].append({"name": p.stem, "written": w, "target": t, "games": g, "pos_s": r})
    return out


_tracker = None


def tracker():
    global _tracker
    if _tracker is None:
        _tracker = LiveTracker()
    return _tracker
