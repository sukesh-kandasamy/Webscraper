"""Agent runs and their steps (SQLite, same database as the rest of the app).

Run statuses:
  draft       plan made, waiting for your approval
  queued      approved, waiting for the agent worker
  navigating  the agent is moving around the site
  collecting  the agent reached the results; the normal engine is collecting (job_id)
  fixing      checking the quality report and fixing what it can (supervisor.py)
  done | stopped | needs_you | limit | error
"""
import json
import time

from .. import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    goal        TEXT NOT NULL,
    site        TEXT NOT NULL,
    start_url   TEXT NOT NULL,
    plan        TEXT NOT NULL DEFAULT '{}',
    limits      TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'draft',
    message     TEXT NOT NULL DEFAULT '',
    steps       INTEGER NOT NULL DEFAULT 0,
    ai_calls    INTEGER NOT NULL DEFAULT 0,
    final_url   TEXT NOT NULL DEFAULT '',
    job_id      INTEGER,
    scrape_id   INTEGER,
    summary     TEXT NOT NULL DEFAULT '',
    stop_requested INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    started_at  REAL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS agent_steps (
    run_id  INTEGER NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    n       INTEGER NOT NULL,
    kind    TEXT NOT NULL,          -- action | thought | check | info | warn
    text    TEXT NOT NULL,
    url     TEXT NOT NULL DEFAULT '',
    detail  TEXT NOT NULL DEFAULT '{}',   -- recorded target (for routes), refusal reason ...
    shot    INTEGER NOT NULL DEFAULT 0,
    at      REAL NOT NULL,
    PRIMARY KEY (run_id, n)
);
"""

ACTIVE = ("queued", "navigating", "collecting", "fixing")
FINISHED = ("done", "stopped", "needs_you", "limit", "error")


def init() -> None:
    with db.tx() as c:
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(agent_runs)")}
        if "target_scrape_id" not in cols:      # re-recording a saved scrape's route updates that scrape
            c.execute("ALTER TABLE agent_runs ADD COLUMN target_scrape_id INTEGER")
        if "fixes" not in cols:                 # check-and-fix: what was fixed, and how many rounds
            c.execute("ALTER TABLE agent_runs ADD COLUMN fixes TEXT NOT NULL DEFAULT '[]'")
            c.execute("ALTER TABLE agent_runs ADD COLUMN fix_rounds INTEGER NOT NULL DEFAULT 0")
        # The worker process died with the server: its navigating runs can't continue.
        c.execute("UPDATE agent_runs SET status='error', message='The app restarted while the agent was "
                  "working. Start it again.', finished_at=? WHERE status='navigating'", (time.time(),))


def _run(row) -> dict:
    d = dict(row)
    d["plan"] = json.loads(d["plan"] or "{}")
    d["limits"] = json.loads(d["limits"] or "{}")
    d["fixes"] = json.loads(d.get("fixes") or "[]")
    d["stop_requested"] = bool(d["stop_requested"])
    return d


def create(goal: str, site: str, start_url: str, plan: dict, limits: dict) -> int:
    with db.tx() as c:
        cur = c.execute("INSERT INTO agent_runs(goal, site, start_url, plan, limits, created_at) VALUES(?,?,?,?,?,?)",
                        (goal, site, start_url, json.dumps(plan, ensure_ascii=False), json.dumps(limits), time.time()))
        return cur.lastrowid


def get(run_id: int) -> dict | None:
    with db.tx() as c:
        row = c.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
    return _run(row) if row else None


def update(run_id: int, **fields) -> None:
    if not fields:
        return
    for k in ("plan", "limits", "fixes"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    cols = ", ".join(f"{k}=?" for k in fields)
    with db.tx() as c:
        c.execute(f"UPDATE agent_runs SET {cols} WHERE id=?", (*fields.values(), run_id))


def list_runs(limit: int = 30) -> list[dict]:
    with db.tx() as c:
        rows = c.execute("SELECT * FROM agent_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_run(r) for r in rows]


def transition(run_id: int, old: str, new: str, **fields) -> bool:
    """Move a run from one status to another only if it is still in the old one (no double work)."""
    cols = ", ".join(["status=?"] + [f"{k}=?" for k in fields])
    with db.tx() as c:
        cur = c.execute(f"UPDATE agent_runs SET {cols} WHERE id=? AND status=?",
                        (new, *fields.values(), run_id, old))
        return cur.rowcount == 1


def claim_next() -> dict | None:
    """The oldest queued run, marked as navigating (one worker, one run at a time)."""
    with db.tx() as c:
        row = c.execute("SELECT * FROM agent_runs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if not row:
            return None
        c.execute("UPDATE agent_runs SET status='navigating', started_at=?, message='Opening the site' WHERE id=?",
                  (time.time(), row["id"]))
    return get(row["id"])


def delete(run_id: int) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM agent_runs WHERE id=?", (run_id,))


def add_step(run_id: int, kind: str, text: str, url: str = "", detail: dict | None = None,
             shot: bool = False) -> int:
    with db.tx() as c:
        n = (c.execute("SELECT MAX(n) FROM agent_steps WHERE run_id=?", (run_id,)).fetchone()[0] or 0) + 1
        c.execute("INSERT INTO agent_steps(run_id, n, kind, text, url, detail, shot, at) VALUES(?,?,?,?,?,?,?,?)",
                  (run_id, n, kind, text[:500], url[:1000], json.dumps(detail or {}, ensure_ascii=False),
                   int(shot), time.time()))
    return n


def steps(run_id: int, after: int = 0) -> list[dict]:
    with db.tx() as c:
        rows = c.execute("SELECT * FROM agent_steps WHERE run_id=? AND n>? ORDER BY n", (run_id, after)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d["detail"] or "{}")
        d["shot"] = bool(d["shot"])
        out.append(d)
    return out
