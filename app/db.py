"""SQLite storage: saved site recipes, jobs, extracted records, the page cache, usage counters.

Records of jobs that used a login session are encrypted with the vault key.
"""
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
    settings   TEXT NOT NULL DEFAULT '{}',   -- per-site: min_delay, max_delay, render
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
CREATE TABLE IF NOT EXISTS job_pages (          -- every page a job has fetched, for resume/retry
    job_id     INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    url        TEXT NOT NULL,
    rows       INTEGER NOT NULL,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (job_id, url)
);
CREATE TABLE IF NOT EXISTS site_usage (         -- pages fetched per site per day (budgets)
    domain TEXT NOT NULL,
    day    TEXT NOT NULL,
    pages  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (domain, day)
);
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


MIGRATIONS = [
    ("sites", "settings", "ALTER TABLE sites ADD COLUMN settings TEXT NOT NULL DEFAULT '{}'"),
    ("jobs", "logged_in", "ALTER TABLE jobs ADD COLUMN logged_in INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "encrypted", "ALTER TABLE jobs ADD COLUMN encrypted INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "keep", "ALTER TABLE jobs ADD COLUMN keep INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "personal", "ALTER TABLE jobs ADD COLUMN personal TEXT NOT NULL DEFAULT '{}'"),
    ("jobs", "paused_url", "ALTER TABLE jobs ADD COLUMN paused_url TEXT NOT NULL DEFAULT ''"),
    ("jobs", "paused_kind", "ALTER TABLE jobs ADD COLUMN paused_kind TEXT NOT NULL DEFAULT ''"),
]


def init() -> None:
    with tx() as c:
        c.executescript(SCHEMA)
        for table, column, ddl in MIGRATIONS:
            cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                c.execute(ddl)
        # Jobs left running by a previous server process are re-queued; jobs.resume_pending()
        # picks them up and continues from the pages they had not fetched yet.
        c.execute("UPDATE jobs SET status='queued', message='Resuming after restart' "
                  "WHERE status='running'")


# ---- recipes -------------------------------------------------------------

def get_recipe(domain: str) -> dict | None:
    with tx() as c:
        row = c.execute("SELECT * FROM sites WHERE domain=?", (domain,)).fetchone()
    if not row:
        return None
    return {"domain": row["domain"], "source": row["source"],
            "updated_at": row["updated_at"], "recipe": json.loads(row["recipe"]),
            "settings": json.loads(row["settings"] or "{}")}


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
        rows = c.execute("SELECT domain, source, settings, updated_at FROM sites ORDER BY domain").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["settings"] = json.loads(d["settings"] or "{}")
        out.append(d)
    return out


def get_site_settings(domain: str) -> dict:
    with tx() as c:
        row = c.execute("SELECT settings FROM sites WHERE domain=?", (domain,)).fetchone()
    return json.loads(row["settings"] or "{}") if row else {}


def update_site_settings(domain: str, patch: dict) -> dict:
    """Merge keys into a site's settings (None removes a key). Returns the new settings."""
    with tx() as c:
        row = c.execute("SELECT settings FROM sites WHERE domain=?", (domain,)).fetchone()
        current = json.loads(row["settings"] or "{}") if row else {}
        for k, v in patch.items():
            if v is None:
                current.pop(k, None)
            else:
                current[k] = v
        c.execute(
            "INSERT INTO sites(domain, recipe, source, settings, updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(domain) DO UPDATE SET settings=excluded.settings",
            (domain, "{}", "manual", json.dumps(current), time.time()),
        )
    return current


def save_site_settings(domain: str, settings: dict) -> None:
    """Store settings for a domain; creates the site row (with an empty recipe) if needed."""
    with tx() as c:
        c.execute(
            "INSERT INTO sites(domain, recipe, source, settings, updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(domain) DO UPDATE SET settings=excluded.settings",
            (domain, "{}", "manual", json.dumps(settings), time.time()),
        )


# ---- jobs ----------------------------------------------------------------

def create_job(urls: list[str], recipe: dict, max_pages: int, logged_in: bool = False,
               personal: dict | None = None) -> int:
    with tx() as c:
        cur = c.execute(
            "INSERT INTO jobs(urls, recipe, max_pages, status, created_at, logged_in, encrypted, personal) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (json.dumps(urls), json.dumps(recipe), max_pages, "queued", time.time(),
             int(logged_in), int(logged_in), json.dumps(personal or {})),
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


def pending_jobs() -> list[int]:
    with tx() as c:
        rows = c.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY id").fetchall()
    return [r["id"] for r in rows]


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
    d["personal"] = json.loads(d.get("personal") or "{}")
    for k in ("logged_in", "encrypted", "keep"):
        d[k] = bool(d.get(k))
    return d


def _job_encrypted(c: sqlite3.Connection, job_id: int) -> bool:
    row = c.execute("SELECT encrypted FROM jobs WHERE id=?", (job_id,)).fetchone()
    return bool(row and row["encrypted"])


def add_records(job_id: int, page: str, rows: list[dict]) -> None:
    from . import vault  # lazy: only needed for logged-in jobs

    with tx() as c:
        enc = _job_encrypted(c, job_id)
        c.executemany(
            "INSERT INTO records(job_id, page, data) VALUES(?,?,?)",
            [(job_id, page, ("enc:" + vault.encrypt_json(r)) if enc else json.dumps(r, ensure_ascii=False))
             for r in rows],
        )


def add_job_page(job_id: int, url: str, rows: int) -> None:
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO job_pages(job_id, url, rows, fetched_at) VALUES(?,?,?,?)",
                  (job_id, url, rows, time.time()))


def job_pages(job_id: int) -> dict[str, int]:
    """URLs this job has already fetched, with the number of rows each one gave."""
    with tx() as c:
        rows = c.execute("SELECT url, rows FROM job_pages WHERE job_id=?", (job_id,)).fetchall()
    return {r["url"]: r["rows"] for r in rows}


def get_records(job_id: int) -> list[dict]:
    with tx() as c:
        rows = c.execute("SELECT data FROM records WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    out = []
    for r in rows:
        data = r["data"]
        if data.startswith("enc:"):
            from . import vault
            out.append(vault.decrypt_json(data[4:]))
        else:
            out.append(json.loads(data))
    return out


# ---- usage budgets ---------------------------------------------------------

def _today() -> str:
    return time.strftime("%Y-%m-%d")


def usage_today(domain: str) -> int:
    with tx() as c:
        row = c.execute("SELECT pages FROM site_usage WHERE domain=? AND day=?",
                        (domain, _today())).fetchone()
    return row["pages"] if row else 0


def consume_page(domain: str, budget: int) -> bool:
    """Count one page against today's budget. False (and nothing counted) if the budget is used up."""
    with tx() as c:
        row = c.execute("SELECT pages FROM site_usage WHERE domain=? AND day=?",
                        (domain, _today())).fetchone()
        used = row["pages"] if row else 0
        if used >= budget:
            return False
        c.execute("INSERT INTO site_usage(domain, day, pages) VALUES(?,?,1) "
                  "ON CONFLICT(domain, day) DO UPDATE SET pages=pages+1", (domain, _today()))
    return True


# ---- retention -------------------------------------------------------------

def purge(cache_max_age_s: float, job_max_age_days: float) -> dict:
    """Delete expired cache pages and old finished jobs (unless marked keep)."""
    now = time.time()
    month_ago = time.strftime("%Y-%m-%d", time.localtime(now - 31 * 86400))
    with tx() as c:
        cache = c.execute("DELETE FROM page_cache WHERE fetched_at < ?",
                          (now - cache_max_age_s,)).rowcount
        old = now - job_max_age_days * 86400
        jobs = c.execute(
            "DELETE FROM jobs WHERE keep=0 AND status NOT IN ('queued','running','needs_you') "
            "AND COALESCE(finished_at, created_at) < ?", (old,)).rowcount
        c.execute("DELETE FROM site_usage WHERE day < ?", (month_ago,))
    return {"cache_pages": cache, "jobs": jobs}


def _url_on_domain(url: str, domain: str) -> bool:
    from urllib.parse import urlsplit
    host = (urlsplit(url.removeprefix("render:")).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


def delete_site_data(domain: str) -> dict:
    """Remove jobs, records, cached pages and usage for a domain (and its subdomains)."""
    domain = domain.lower()
    with tx() as c:
        ids = [r["id"] for r in c.execute("SELECT id, urls FROM jobs").fetchall()
               if any(_url_on_domain(u, domain) for u in json.loads(r["urls"]))]
        for job_id in ids:
            c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        urls = [r["url"] for r in c.execute("SELECT url FROM page_cache").fetchall()
                if _url_on_domain(r["url"], domain)]
        for u in urls:
            c.execute("DELETE FROM page_cache WHERE url=?", (u,))
        for r in c.execute("SELECT DISTINCT domain FROM site_usage").fetchall():
            if r["domain"] == domain or r["domain"].endswith("." + domain):
                c.execute("DELETE FROM site_usage WHERE domain=?", (r["domain"],))
    return {"jobs": len(ids), "cache_pages": len(urls)}


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
