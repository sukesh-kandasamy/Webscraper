"""SQLite storage: saved site recipes, jobs, extracted records and the page cache."""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    domain     TEXT PRIMARY KEY,
    recipe     TEXT NOT NULL,
    source     TEXT NOT NULL,          -- builtin | ai | manual
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    urls        TEXT NOT NULL,
    recipe      TEXT NOT NULL,
    max_pages   INTEGER NOT NULL,
    status      TEXT NOT NULL,         -- queued | running | done | blocked | error | stopped
    pages_done  INTEGER NOT NULL DEFAULT 0,
    pages_total INTEGER NOT NULL DEFAULT 0,
    rows        INTEGER NOT NULL DEFAULT 0,
    message     TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS records (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id  INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    page    TEXT NOT NULL,
    data    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_job ON records(job_id);
CREATE TABLE IF NOT EXISTS page_cache (
    url        TEXT PRIMARY KEY,
    status     INTEGER NOT NULL,
    final_url  TEXT NOT NULL,
    body       TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def tx():
    with _lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init() -> None:
    with tx() as c:
        c.executescript(SCHEMA)
        # Jobs left running by a previous server process can never finish.
        c.execute(
            "UPDATE jobs SET status='stopped', message='Server restarted' "
            "WHERE status IN ('queued','running')"
        )


# ---- recipes -------------------------------------------------------------

def get_recipe(domain: str) -> dict | None:
    with tx() as c:
        row = c.execute("SELECT * FROM sites WHERE domain=?", (domain,)).fetchone()
    if not row:
        return None
    return {"domain": row["domain"], "source": row["source"],
            "updated_at": row["updated_at"], "recipe": json.loads(row["recipe"])}


def save_recipe(domain: str, recipe: dict, source: str) -> None:
    with tx() as c:
        c.execute(
            "INSERT INTO sites(domain, recipe, source, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(domain) DO UPDATE SET recipe=excluded.recipe, "
            "source=excluded.source, updated_at=excluded.updated_at",
            (domain, json.dumps(recipe), source, time.time()),
        )


def list_sites() -> list[dict]:
    with tx() as c:
        rows = c.execute("SELECT domain, source, updated_at FROM sites ORDER BY domain").fetchall()
    return [dict(r) for r in rows]


# ---- jobs ----------------------------------------------------------------

def create_job(urls: list[str], recipe: dict, max_pages: int) -> int:
    with tx() as c:
        cur = c.execute(
            "INSERT INTO jobs(urls, recipe, max_pages, status, created_at) VALUES(?,?,?,?,?)",
            (json.dumps(urls), json.dumps(recipe), max_pages, "queued", time.time()),
        )
        return cur.lastrowid


def update_job(job_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with tx() as c:
        c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def get_job(job_id: int) -> dict | None:
    with tx() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _job_dict(row) if row else None


def list_jobs(limit: int = 50) -> list[dict]:
    with tx() as c:
        rows = c.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_job_dict(r) for r in rows]


def delete_job(job_id: int) -> None:
    with tx() as c:
        c.execute("DELETE FROM jobs WHERE id=?", (job_id,))


def _job_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["urls"] = json.loads(d["urls"])
    d["recipe"] = json.loads(d["recipe"])
    return d


def add_records(job_id: int, page: str, rows: list[dict]) -> None:
    with tx() as c:
        c.executemany(
            "INSERT INTO records(job_id, page, data) VALUES(?,?,?)",
            [(job_id, page, json.dumps(r, ensure_ascii=False)) for r in rows],
        )


def get_records(job_id: int) -> list[dict]:
    with tx() as c:
        rows = c.execute("SELECT data FROM records WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    return [json.loads(r["data"]) for r in rows]


# ---- page cache ----------------------------------------------------------

def cache_get(url: str, max_age_s: float) -> dict | None:
    with tx() as c:
        row = c.execute("SELECT * FROM page_cache WHERE url=?", (url,)).fetchone()
    if row and time.time() - row["fetched_at"] <= max_age_s:
        return dict(row)
    return None


def cache_put(url: str, status: int, final_url: str, body: str) -> None:
    with tx() as c:
        c.execute(
            "INSERT OR REPLACE INTO page_cache(url, status, final_url, body, fetched_at) "
            "VALUES(?,?,?,?,?)",
            (url, status, final_url, body, time.time()),
        )
