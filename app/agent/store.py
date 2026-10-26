"""Agent runs and their steps (SQLite, same database as the rest of the app).

Run statuses:
  draft       plan made, waiting for your approval
  queued      approved, waiting to start
  thinking    the ReAct loop is working in the app (react/): think, act with a tool, observe
  browsing    a browse sub-task is running on the browser worker (phase 2)
  question    paused: the agent asked you something (phase 2)
  navigating  the earlier engine: browser-use is moving around the site
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
CREATE TABLE IF NOT EXISTS agent_browse_tasks (   -- one browser sub-task of a ReAct run (the browse tool)
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | failed | needs_you | limit | stopped
    task        TEXT NOT NULL,
    start_url   TEXT NOT NULL,
    limits      TEXT NOT NULL DEFAULT '{}',
    counters_base TEXT NOT NULL DEFAULT '{}',    -- the run's steps / ai_calls / tokens when the task began
    result_url  TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    message     TEXT NOT NULL DEFAULT '',
    stop_requested INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    finished_at REAL
);
"""

ACTIVE = ("queued", "navigating", "thinking", "browsing", "collecting", "fixing")
PAUSED = ("question",)
FINISHED = ("done", "stopped", "needs_you", "limit", "error")
JSON_COLUMNS = ("plan", "limits", "fixes", "scratchpad", "checklist")


def init() -> None:
    with db.tx() as c:
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(agent_runs)")}
        if "target_scrape_id" not in cols:      # re-recording a saved scrape's route updates that scrape
            c.execute("ALTER TABLE agent_runs ADD COLUMN target_scrape_id INTEGER")
        if "fixes" not in cols:                 # check-and-fix: what was fixed, and how many rounds
            c.execute("ALTER TABLE agent_runs ADD COLUMN fixes TEXT NOT NULL DEFAULT '[]'")
            c.execute("ALTER TABLE agent_runs ADD COLUMN fix_rounds INTEGER NOT NULL DEFAULT 0")
        if "title" not in cols:                 # a name you gave the run ("" = its plan's summary)
            c.execute("ALTER TABLE agent_runs ADD COLUMN title TEXT NOT NULL DEFAULT ''")
        if "tokens" not in cols:                # AI tokens the run used (prompt + answer), all models
            c.execute("ALTER TABLE agent_runs ADD COLUMN tokens INTEGER NOT NULL DEFAULT 0")
        if "public_id" not in cols:             # the Run ID people see (RUN-7K3QX9); the number stays internal
            c.execute("ALTER TABLE agent_runs ADD COLUMN public_id TEXT")
        if "engine" not in cols:                # react (the loop in the app) | browser (the earlier engine)
            c.execute("ALTER TABLE agent_runs ADD COLUMN engine TEXT NOT NULL DEFAULT 'react'")
            c.execute("UPDATE agent_runs SET engine='browser'")          # every run so far used it
            # the ReAct loop's memory between steps, its checklist ticks, and a question for you
            c.execute("ALTER TABLE agent_runs ADD COLUMN scratchpad TEXT NOT NULL DEFAULT '{}'")
            c.execute("ALTER TABLE agent_runs ADD COLUMN checklist TEXT NOT NULL DEFAULT '[]'")
            c.execute("ALTER TABLE agent_runs ADD COLUMN question TEXT NOT NULL DEFAULT ''")
            c.execute("ALTER TABLE agent_runs ADD COLUMN answer TEXT NOT NULL DEFAULT ''")
        for (run_id,) in c.execute("SELECT id FROM agent_runs WHERE public_id IS NULL OR public_id=''").fetchall():
            c.execute("UPDATE agent_runs SET public_id=? WHERE id=?", (db.new_uid(c, "agent_runs", "RUN-"), run_id))
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS agent_runs_public_id ON agent_runs(public_id)")
        # The app (and the worker with it) restarted: runs that were working can't continue; a run waiting
        # for your answer can (it resumes from its scratchpad).
        c.execute("UPDATE agent_runs SET status='error', message='The app restarted while the agent was "
                  "working. Start it again.', finished_at=? WHERE status IN ('navigating', 'thinking', 'browsing')",
                  (time.time(),))


def _run(row) -> dict:
    d = dict(row)
    d["uid"] = d.pop("public_id", None) or ""
    d["plan"] = json.loads(d["plan"] or "{}")
    d["limits"] = json.loads(d["limits"] or "{}")
    d["fixes"] = json.loads(d.get("fixes") or "[]")
    d["scratchpad"] = json.loads(d.get("scratchpad") or "{}")
    d["checklist"] = json.loads(d.get("checklist") or "[]")
    d["stop_requested"] = bool(d["stop_requested"])
    return d


def create(goal: str, site: str, start_url: str, plan: dict, limits: dict, engine: str = "react") -> int:
    with db.tx() as c:
        cur = c.execute("INSERT INTO agent_runs(goal, site, start_url, plan, limits, created_at, public_id, engine, "
                        "checklist) VALUES(?,?,?,?,?,?,?,?,?)",
                        (goal, site, start_url, json.dumps(plan, ensure_ascii=False), json.dumps(limits), time.time(),
                         db.new_uid(c, "agent_runs", "RUN-"), engine,
                         json.dumps(plan.get("checklist") or [], ensure_ascii=False)))
        return cur.lastrowid


def get(run_id: int) -> dict | None:
    with db.tx() as c:
        row = c.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
    return _run(row) if row else None


def get_by_uid(uid: str) -> dict | None:
    with db.tx() as c:
        row = c.execute("SELECT * FROM agent_runs WHERE public_id=?", (uid.upper(),)).fetchone()
    return _run(row) if row else None


def run_uids(ids) -> dict[int, str]:
    ids = [int(i) for i in set(ids)]
    if not ids:
        return {}
    with db.tx() as c:
        rows = c.execute(f"SELECT id, public_id FROM agent_runs WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    return {r["id"]: r["public_id"] for r in rows}


def update(run_id: int, **fields) -> None:
    if not fields:
        return
    for k in JSON_COLUMNS:
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    cols = ", ".join(f"{k}=?" for k in fields)
    with db.tx() as c:
        c.execute(f"UPDATE agent_runs SET {cols} WHERE id=?", (*fields.values(), run_id))


def bump(run_id: int, **counts: int) -> None:
    """Add to counters (steps, ai_calls, tokens) in one statement, so the loop and the worker never lose
    each other's updates."""
    if not counts:
        return
    cols = ", ".join(f"{k}={k}+?" for k in counts)
    with db.tx() as c:
        c.execute(f"UPDATE agent_runs SET {cols} WHERE id=?", (*counts.values(), run_id))


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
    """The oldest queued run of the browser engine, marked as navigating (one worker, one run at a time)."""
    with db.tx() as c:
        row = c.execute("SELECT * FROM agent_runs WHERE status='queued' AND engine='browser' ORDER BY id LIMIT 1").fetchone()
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


# ---- browse sub-tasks (phase 2: the browser as one tool of the ReAct loop) ----------------

def _task(row) -> dict | None:
    if not row:
        return None
    d = dict(row)
    d["limits"] = json.loads(d["limits"] or "{}")
    d["counters_base"] = json.loads(d["counters_base"] or "{}")
    d["stop_requested"] = bool(d["stop_requested"])
    return d


def create_browse_task(run_id: int, task: str, start_url: str, limits: dict, counters_base: dict) -> int:
    with db.tx() as c:
        cur = c.execute("INSERT INTO agent_browse_tasks(run_id, task, start_url, limits, counters_base, created_at) "
                        "VALUES(?,?,?,?,?,?)", (run_id, task, start_url, json.dumps(limits), json.dumps(counters_base), time.time()))
        return cur.lastrowid


def claim_browse_task() -> dict | None:
    """The oldest queued browse task, marked running (the worker takes these before whole runs)."""
    with db.tx() as c:
        row = c.execute("SELECT * FROM agent_browse_tasks WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if not row:
            return None
        c.execute("UPDATE agent_browse_tasks SET status='running' WHERE id=?", (row["id"],))
        return _task(c.execute("SELECT * FROM agent_browse_tasks WHERE id=?", (row["id"],)).fetchone())


def get_browse_task(task_id: int) -> dict | None:
    with db.tx() as c:
        return _task(c.execute("SELECT * FROM agent_browse_tasks WHERE id=?", (task_id,)).fetchone())


def running_browse_task(run_id: int) -> dict | None:
    with db.tx() as c:
        return _task(c.execute("SELECT * FROM agent_browse_tasks WHERE run_id=? AND status IN ('queued', 'running') "
                               "ORDER BY id DESC LIMIT 1", (run_id,)).fetchone())


def update_browse_task(task_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with db.tx() as c:
        c.execute(f"UPDATE agent_browse_tasks SET {cols} WHERE id=?", (*fields.values(), task_id))


def finish_browse_task(task_id: int, status: str, result_url: str = "", note: str = "", message: str = "") -> bool:
    """End a task once (the first finisher wins: the worker's result or the tool's timeout)."""
    with db.tx() as c:
        cur = c.execute("UPDATE agent_browse_tasks SET status=?, result_url=?, note=?, message=?, finished_at=? "
                        "WHERE id=? AND status IN ('queued', 'running')",
                        (status, result_url[:1000], note[:500], message[:1000], time.time(), task_id))
        return cur.rowcount == 1


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
