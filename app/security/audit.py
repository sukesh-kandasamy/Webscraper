"""Append-only activity log: what was fetched, exported, logged in to, and changed, and when.

One JSON object per line in data/audit.log. Never contains cookies, passwords or page content.
"""
import json
import threading
import time
from collections import deque

from .. import config
from . import logsafe

_lock = threading.Lock()


def path():
    return config.DATA_DIR / "audit.log"


def log(event: str, **fields) -> None:
    entry = {"ts": round(time.time(), 3), "event": event,
             **{k: logsafe.redact(v) if isinstance(v, str) else v for k, v in fields.items()}}
    line = json.dumps(entry, ensure_ascii=False)
    with _lock:
        with open(path(), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def tail(limit: int = 200, event: str | None = None, domain: str | None = None) -> list[dict]:
    p = path()
    if not p.exists():
        return []
    out: deque = deque(maxlen=limit)
    with _lock:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event and entry.get("event") != event:
                    continue
                if domain and entry.get("domain") != domain:
                    continue
                out.append(entry)
    return list(reversed(out))
