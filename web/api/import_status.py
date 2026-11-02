"""Dernier événement d'import par source, lu depuis data/auto_sync.log
(JSON-lines émis par scripts/auto-sync.sh). Lecture pure, hors chemin de l'argent."""
import json


def last_per_source(log_text, event=None):
    """Dernier événement par source ; restreint à `event` s'il est donné."""
    latest = {}
    for line in log_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
            src = ev["source"]
            if event is not None and ev.get("event") != event:
                continue
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        latest[src] = {"timestamp": ev.get("timestamp"),
                       "event": ev.get("event"),
                       "transactions": ev.get("transactions")}
    return latest
