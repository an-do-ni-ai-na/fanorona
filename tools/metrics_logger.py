"""Journalisation JSONL des métriques de recherche (lignes UCI 'info' + résultats de
parties/tests) pour suivi live via Grafana/Loki (Alloy tail /var/log/fanorona/*.jsonl).

Le suivi live est un bonus : si le répertoire de logs est inaccessible (droits, disque),
on se tait et on continue le match/bench normalement plutôt que de faire échouer le test.
"""
import json
import re
import time
import uuid
from pathlib import Path

DEFAULT_LOG_DIR = Path("/var/log/fanorona")

# Format exact émis par Search::think (src/search.cpp), commande UCI "go" verbeuse :
#   info depth D seldepth S score cp|mate V nodes N nps R hashfull H time T pv <coups...>
INFO_RE = re.compile(
    r"^info depth (?P<depth>\d+) seldepth (?P<seldepth>\d+) score (?P<score_kind>cp|mate) "
    r"(?P<score_val>-?\d+) nodes (?P<nodes>\d+) nps (?P<nps>\d+) hashfull (?P<hashfull>\d+) "
    r"time (?P<time_ms>\d+) pv (?P<pv>.*)$"
)


def new_run_id(run_type):
    return f"{run_type}-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"


def parse_info_line(line):
    """Parse une ligne 'info ...' UCI. Renvoie un dict de champs numériques, ou None si la
    ligne ne matche pas (autres lignes 'info string ...', bruit, etc.)."""
    m = INFO_RE.match(line.strip())
    if not m:
        return None
    d = m.groupdict()
    event = {
        "depth": int(d["depth"]),
        "seldepth": int(d["seldepth"]),
        "nodes": int(d["nodes"]),
        "nps": int(d["nps"]),
        "hashfull": int(d["hashfull"]),
        "time_ms": int(d["time_ms"]),
        "pv": d["pv"],
    }
    if d["score_kind"] == "cp":
        event["score_cp"] = int(d["score_val"])
    else:
        event["mate"] = int(d["score_val"])
    return event


class MetricsLogger:
    """Logger JSONL append-only, un fichier par run (identifié par run_id)."""

    def __init__(self, run_type, run_id=None, log_dir=DEFAULT_LOG_DIR, tags=None, enabled=True):
        self.run_type = run_type
        self.run_id = run_id or new_run_id(run_type)
        self.tags = tags or {}
        self._fh = None
        if not enabled:
            return
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            self._fh = open(log_dir / f"{self.run_id}.jsonl", "a")
        except OSError:
            self._fh = None

    @property
    def enabled(self):
        return self._fh is not None

    def log(self, event, **fields):
        if self._fh is None:
            return
        rec = {
            "ts": time.time(),
            "event": event,
            "run_id": self.run_id,
            "run_type": self.run_type,
            **self.tags,
            **fields,
        }
        self._fh.write(json.dumps(rec) + "\n")
        self._fh.flush()

    def log_info_line(self, line, **tags):
        parsed = parse_info_line(line)
        if parsed is not None:
            self.log("info", **parsed, **tags)

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None
