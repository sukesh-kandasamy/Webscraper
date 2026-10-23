"""FastAPI app: serves the UI and the JSON API.

Run:  python run.py      (or: uvicorn app.main:app)      then open http://localhost:8000

The app only answers requests addressed to localhost from its own page: other websites open in
your browser cannot drive it (Host / Origin / Sec-Fetch-Site checks), and responses carry strict
security headers.
"""
import functools
import json
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (audit, browser, config, db, export, extractor, fetcher, jobs, logsafe, netguard,
               privacy, recipe_builder, robots, sessions)

PREVIEW_ROWS = 20


def _seed_builtin_recipes() -> None:
    for path in sorted(config.BUILTIN_RECIPES_DIR.glob("*.json")):
        domain = path.stem
        existing = db.get_recipe(domain)
        if existing is None or existing["source"] == "builtin":
            recipe = extractor.normalize_recipe(json.loads(path.read_text(encoding="utf-8")))
            db.save_recipe(domain, recipe, "builtin")


def _retention_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            removed = db.purge(config.CACHE_TTL_HOURS * 3600, config.JOB_RETENTION_DAYS)
            if removed["cache_pages"] or removed["jobs"]:
                audit.log("retention_purge", **removed)
        except Exception:
            pass
        stop.wait(config.PURGE_INTERVAL_S)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logsafe.install()
    db.init()
    _seed_builtin_recipes()
    stop = threading.Event()
    threading.Thread(target=_retention_loop, args=(stop,), daemon=True, name="retention").start()
    jobs.resume_pending()
    yield
    stop.set()


app = FastAPI(title="Universal Scraper", version="0.2", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.ALLOWED_HOSTS)
app.mount("/static", StaticFiles(directory=config.ROOT / "static"), name="static")

_LOCAL_NAMES = {"localhost", "127.0.0.1", "::1", "[::1]"} | set(config.ALLOWED_HOSTS)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    # Tailwind's CDN build and Alpine need inline / eval script; nothing else is allowed in.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' "
        "https://cdn.tailwindcss.com https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'self'; object-src 'none'"),
}


@app.middleware("http")
async def same_origin_only(request: Request, call_next):
    """Refuse state-changing requests that come from another website (CSRF)."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        if site and site not in ("same-origin", "none"):
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        if origin and (urlsplit(origin).hostname or "") not in _LOCAL_NAMES:
            return JSONResponse({"detail": "Cross-origin request refused"}, status_code=403)
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# ---- request models ------------------------------------------------------

class AnalyseIn(BaseModel):
    url: str
    fields: list[str] = Field(default_factory=list)   # columns you want (always included)
    auto: bool = True          # let the AI add every other important column it finds
    force_ai: bool = False


class PreviewIn(BaseModel):
    url: str
    recipe: dict


class RecipeIn(BaseModel):
    recipe: dict


class SettingsIn(BaseModel):
    min_delay: float | None = Field(default=None, ge=0, le=120)
    max_delay: float | None = Field(default=None, ge=0, le=120)
    render: str | None = Field(default=None, pattern="^(auto|never|always)$")
    daily_budget: int | None = Field(default=None, ge=1, le=100_000)
    honest_ua: bool | None = None
    ai_allowed: bool | None = None
    use_session: bool | None = None
    blocked: bool | None = None


class WindowIn(BaseModel):
    url: str | None = None


class LoginIn(BaseModel):
    url: str | None = None
    own_account: bool = False       # "this is my own account and the site allows automated access"


class FinishIn(BaseModel):
    account: str = Field(default="", max_length=120)


class JobIn(BaseModel):
    urls: list[str]
    recipe: dict
    max_pages: int = Field(default=config.MAX_PAGES_PER_URL, ge=1, le=100)
    save_recipe: bool = True
    recipe_source: str = "manual"   # where the recipe came from: ai | manual | builtin
    personal_fields: list[str] = Field(default_factory=list)
    personal_purpose: str = Field(default="", max_length=500)


class KeepIn(BaseModel):
    keep: bool


class ScrapeIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    urls: list[str]
    recipe: dict
    max_pages: int = Field(default=3, ge=1, le=100)
    page_type: str = Field(default="", max_length=40)
    run: bool = True                       # start a run straight away
    personal_fields: list[str] = Field(default_factory=list)
    personal_purpose: str = Field(default="", max_length=500)


class ScrapeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    urls: list[str] | None = None
    recipe: dict | None = None
    max_pages: int | None = Field(default=None, ge=1, le=100)


class RunIn(BaseModel):
    personal_fields: list[str] = Field(default_factory=list)
    personal_purpose: str = Field(default="", max_length=500)


# ---- helpers -------------------------------------------------------------

def _clean_urls(urls: list[str]) -> list[str]:
    out = []
    for u in urls:
        u = u.strip()
        if not u:
            continue
        if not u.lower().startswith(("http://", "https://")):
            u = "https://" + u
        if u not in out:
            out.append(u)
    if not out:
        raise HTTPException(400, "Enter at least one URL")
    for u in out:
        try:
            netguard.check_url(u)
        except netguard.UnsafeURL as e:
            raise HTTPException(400, str(e))
    return out


def _robots_summary(url: str) -> dict:
    rules = fetcher.robots_rules(url)
    return {"allowed": robots.is_allowed(rules, url),
            "disallow": [p for allow, p in rules if not allow][:40],
            "allow": [p for allow, p in rules if allow][:20],
            "robots_url": f"{urlsplit(url).scheme}://{urlsplit(url).netloc}/robots.txt"}


def _fetch_or_http_error(url: str, recipe: dict | None = None) -> fetcher.Page:
    try:
        return fetcher.fetch_smart(url, recipe)
    except fetcher.NeedsHuman as e:
        raise HTTPException(409, {"code": "needs_human", "kind": e.kind, "url": e.url,
                                  "domain": fetcher.host_of(e.url), "message": str(e)})
    except fetcher.BudgetExceeded as e:
        raise HTTPException(429, str(e))
    except fetcher.BlockedError as e:
        raise HTTPException(403, str(e))
    except fetcher.FetchError as e:
        raise HTTPException(502, logsafe.redact(str(e)))


def _normalize_or_http_error(recipe: dict) -> dict:
    try:
        return extractor.normalize_recipe(recipe)
    except extractor.RecipeError as e:
        raise HTTPException(400, f"Recipe problem: {e}")


def _starter_recipe(html: str, fields: list[str]) -> dict:
    """A recipe skeleton for hand-editing when AI is off, seeded with the likeliest listing block."""
    cands = recipe_builder.find_candidates(html)
    item = cands[0]["selector_hint"].split(" > ")[-1] if cands else "CHANGE-ME"
    return {"item_selector": item,
            "fields": [{"name": f, "selector": "", "attr": "text"} for f in (fields or ["name", "url"])],
            "pagination": {"type": "none"}}


def _result(domain: str, page: fetcher.Page, recipe: dict, source: str, rows: list[dict],
            notes: str = "") -> dict:
    personal = privacy.personal_fields(recipe, rows)
    found = sessions.for_host(fetcher.host_of(page.final_url))
    session = sessions._public(found) if found else None
    return {"domain": domain, "url": page.final_url, "from_cache": page.from_cache,
            "source": source, "recipe": recipe, "notes": notes,
            "settings": db.get_site_settings(domain), "rendered": page.rendered,
            "logged_in": page.logged_in, "session": session,
            "personal_fields": personal,
            "row_count": len(rows), "rows": privacy.mask_rows(rows[:PREVIEW_ROWS]),
            "report": extractor.field_report(rows, recipe),
            "page_type": recipe.get("page_type") or ""}


@functools.lru_cache(maxsize=1)
def _browser_available() -> bool:
    return browser.available()


def _remember_render(domain: str, page: fetcher.Page) -> str:
    """When auto mode had to use the browser, save that so later fetches go straight to it."""
    settings = db.get_site_settings(domain)
    if page.rendered and settings.get("render", "auto") == "auto":
        fetcher.update_settings(domain, {"render": "always"})
        return " This site needed browser rendering; saved that in its settings."
    return ""


# ---- routes: page and status -------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(config.ROOT / "static" / "index.html")


@app.get("/api/status")
def status():
    return {"ai_enabled": config.ai_enabled(), "model": config.ai_model(), "provider": config.AI_PROVIDER,
            "default_fields": config.DEFAULT_FIELDS, "respect_robots": config.RESPECT_ROBOTS,
            "delay": [config.MIN_DELAY, config.MAX_DELAY], "max_pages": config.MAX_PAGES_PER_URL,
            "max_jobs": config.MAX_CONCURRENT_JOBS, "browser_available": _browser_available(),
            "budget": [config.DAILY_BUDGET, config.DAILY_BUDGET_LOGGED_IN],
            "session_max_age_days": config.SESSION_MAX_AGE_DAYS,
            "retention": {"cache_hours": config.CACHE_TTL_HOURS, "job_days": config.JOB_RETENTION_DAYS},
            "needs_you": [j["id"] for j in db.list_jobs(100) if j["status"] == "needs_you"]}


# ---- routes: analyse / preview -------------------------------------------------------

@app.post("/api/analyse")
def analyse(body: AnalyseIn):
    """Get a recipe for the page: the saved one if it still works, otherwise ask the AI."""
    url = _clean_urls([body.url])[0]
    fields = recipe_builder.clean_field_names(body.fields)
    domain = fetcher.domain_of(url)
    saved = db.get_recipe(domain)
    saved_recipe = None
    if saved and saved["recipe"].get("item_selector") and not body.force_ai:
        saved_recipe = extractor.normalize_recipe(saved["recipe"])
    page = _fetch_or_http_error(url, saved_recipe)
    domain = fetcher.domain_of(page.final_url)
    render_note = _remember_render(domain, page)

    missing: list[str] = []
    if saved_recipe:
        have = {f["name"] for f in saved_recipe["fields"]}
        missing = [f for f in fields if f not in have]
        rows = extractor.extract(page.html, saved_recipe, page.final_url)
        # Use the saved recipe unless you asked for columns it doesn't have (then the AI rebuilds it).
        if rows and not (missing and config.ai_enabled()):
            note = ("Using the saved recipe for this site. Click 'Re-analyse with AI' to let the AI "
                    "pick the columns again.")
            if missing:
                note += f" It has no column for: {', '.join(missing)} (no AI key to add them)."
            return _result(domain, page, saved_recipe, saved["source"], rows, note + render_note)

    ai_allowed = db.get_site_settings(domain).get("ai_allowed", True)
    if not config.ai_enabled() or not ai_allowed:
        why = ("AI is turned off for this site in its settings" if not ai_allowed
               else "no AI API key is set")
        raise HTTPException(422, {
            "message": f"No working recipe saved for this site and {why}. "
                       "Edit the starter recipe below by hand.",
            "starter_recipe": _starter_recipe(page.html, fields), "domain": domain})

    try:
        built = recipe_builder.build_recipe(page.html, page.final_url, fields, page.rendered,
                                            redact=page.logged_in, auto=body.auto)
    except recipe_builder.RecipeBuildError as e:
        raise HTTPException(422, {"message": str(e), "domain": domain,
                                  "starter_recipe": _starter_recipe(page.html, fields)})
    except Exception as e:  # API / network errors from the AI SDK
        raise HTTPException(502, logsafe.redact(f"AI request failed: {type(e).__name__}: {e}")[:400])
    audit.log("recipe_built", domain=domain, provider=config.AI_PROVIDER, redacted=page.logged_in,
              rows=len(built["rows"]))
    note = " Page text was redacted before it was sent to the AI." if page.logged_in else ""
    if missing:
        note += f" Rebuilt because the saved recipe had no column for: {', '.join(missing)}."
    return _result(domain, page, built["recipe"], "ai", built["rows"],
                   built["notes"] + render_note + note)


@app.post("/api/preview")
def preview(body: PreviewIn):
    url = _clean_urls([body.url])[0]
    recipe = _normalize_or_http_error(body.recipe)
    page = _fetch_or_http_error(url, recipe)
    _remember_render(fetcher.domain_of(page.final_url), page)
    try:
        rows = extractor.extract(page.html, recipe, page.final_url)
    except extractor.RecipeError as e:
        raise HTTPException(400, f"Recipe problem: {e}")
    return _result(fetcher.domain_of(page.final_url), page, recipe, "manual", rows)


@app.get("/api/robots")
def robots_view(url: str):
    url = _clean_urls([url])[0]
    return _robots_summary(url)


# ---- routes: sites, policy, settings ----------------------------------------------------

def _site_view(domain: str, settings: dict) -> dict:
    session = sessions.info(domain) or (sessions.info(sessions.base_domain(domain))
                                        if sessions.base_domain(domain) != domain else None)
    return {"domain": domain, "settings": settings,
            "session": session, "usage_today": db.usage_today(domain),
            "budget": fetcher.daily_budget(domain, bool(session and session["kind"] == "login")),
            "cooldown_left_min": round(fetcher.cooldown_left(domain) / 60, 1)}


@app.get("/api/sites")
def sites():
    out = {}
    for s in db.list_sites():
        out[s["domain"]] = {**_site_view(s["domain"], s["settings"]), "source": s["source"],
                            "has_recipe": s["source"] != "manual" or bool(db.get_recipe(s["domain"])
                                                                          and db.get_recipe(s["domain"])["recipe"])}
    for sess in sessions.list_sessions():
        if sess["domain"] not in out:
            out[sess["domain"]] = {**_site_view(sess["domain"], {}), "source": "", "has_recipe": False}
    return sorted(out.values(), key=lambda s: s["domain"])


@app.get("/api/sites/{domain}/recipe")
def get_site_recipe(domain: str):
    saved = db.get_recipe(domain)
    if not saved:
        raise HTTPException(404, "No recipe saved for this site")
    return saved


@app.put("/api/sites/{domain}/recipe")
def put_site_recipe(domain: str, body: RecipeIn):
    recipe = _normalize_or_http_error(body.recipe)
    db.save_recipe(domain, recipe, "manual")
    audit.log("recipe_saved", domain=domain)
    return {"ok": True}


@app.get("/api/sites/{domain}/settings")
def get_site_settings(domain: str):
    return db.get_site_settings(domain)


@app.put("/api/sites/{domain}/settings")
def put_site_settings(domain: str, body: SettingsIn):
    patch = body.model_dump(exclude_unset=True)
    current = db.get_site_settings(domain)
    lo = patch.get("min_delay", current.get("min_delay"))
    hi = patch.get("max_delay", current.get("max_delay"))
    if lo is not None and hi is not None and lo > hi:
        raise HTTPException(400, "min_delay must not be larger than max_delay")
    settings = fetcher.update_settings(domain, patch)
    audit.log("settings_changed", domain=domain, changed=sorted(patch))
    return settings


@app.delete("/api/sites/{domain}/cooldown")
def clear_cooldown(domain: str):
    fetcher.update_settings(domain, {"cooldown_until": None, "cooldown_reason": None,
                                     "last_challenge": None})
    audit.log("cooldown_cleared", domain=domain)
    return {"ok": True}


@app.delete("/api/sites/{domain}/data")
def delete_site_data(domain: str):
    removed = db.delete_site_data(domain.lower())
    removed["session"] = sessions.forget(domain.lower(), reason="site data deleted")
    audit.log("site_data_deleted", domain=domain.lower(), **removed)
    return removed


# ---- routes: login sessions and visible windows -------------------------------------

def _window_or_404(key: str) -> sessions.Window:
    win = sessions.get_window(key)
    if not win:
        raise HTTPException(404, "No browser window for this")
    return win


def _finish_window(key: str, timeout: float = 20) -> sessions.Window:
    win = _window_or_404(key)
    win.finish()
    win.wait(timeout)
    if win.status != "finished" or not win.state:
        raise HTTPException(409, f"The browser window ended as '{win.status}'. {win.message}".strip())
    return win


@app.get("/api/sessions")
def list_sessions():
    return sessions.list_sessions()


@app.delete("/api/sessions/{domain}")
def forget_session(domain: str):
    return {"forgotten": sessions.forget(domain)}


@app.post("/api/sites/{domain}/login")
def start_login(domain: str, body: LoginIn):
    if not body.own_account:
        raise HTTPException(400, "Confirm that this is your own account and that the site allows "
                                 "automated access while logged in.")
    if not _browser_available():
        raise HTTPException(400, browser.install_hint())
    url = _clean_urls([body.url or f"https://{domain}/"])[0]
    audit.log("login_confirmed", domain=domain.lower(), own_account=True)
    existing = sessions.for_host(fetcher.host_of(url))
    win = sessions.open_window(f"login:{domain}", url, "login",
                               existing["state"] if existing else None)
    return win.public()


@app.post("/api/sites/{domain}/login/finish")
def finish_login(domain: str, body: FinishIn):
    win = _finish_window(f"login:{domain}")
    blocked = privacy_check_login_page(win)
    info = sessions.save(domain, win.state, account=body.account, kind=sessions.KIND_LOGIN)
    sessions.close_window(f"login:{domain}")
    return {**info, "warning": blocked}


def privacy_check_login_page(win: sessions.Window) -> str:
    """Warn when the window still seems to be on a login page (the user may not have logged in)."""
    if win.final_url and fetcher.blockdetect._LOGIN_PATH.search(urlsplit(win.final_url).path):
        return "The window was still on a login page; if jobs see a login page, log in again."
    return ""


@app.post("/api/sites/{domain}/login/cancel")
def cancel_login(domain: str):
    win = _window_or_404(f"login:{domain}")
    win.cancel()
    sessions.close_window(f"login:{domain}")
    return {"ok": True}


@app.get("/api/windows/{key}")
def window_status(key: str):
    return _window_or_404(key).public()


def _open_solve_window(key: str, url: str, purpose: str) -> dict:
    if not _browser_available():
        raise HTTPException(400, browser.install_hint())
    existing = sessions.for_host(fetcher.host_of(url))
    return sessions.open_window(key, url, purpose, existing["state"] if existing else None).public()


def _save_after_solve(win: sessions.Window, account: str = "", kind_hint: str = "") -> dict:
    host = fetcher.host_of(win.url)
    existing = sessions.for_host(host)
    if existing and existing.get("kind") == sessions.KIND_LOGIN:
        info = sessions.save(existing["domain"], win.state, account=account or existing.get("account", ""),
                             kind=sessions.KIND_LOGIN, scope=existing["scope"])
    else:
        kind = sessions.KIND_LOGIN if kind_hint == "login" else sessions.KIND_BROWSER
        info = sessions.save(host, win.state, account=account, kind=kind)
    # Cookies a site gives a real browser after a check work best in the browser again.
    if info["kind"] == sessions.KIND_BROWSER:
        fetcher.update_settings(fetcher.domain_of(win.url), {"render": "always"})
    return info


@app.post("/api/sites/{domain}/solve")
def start_site_solve(domain: str, body: WindowIn):
    url = _clean_urls([body.url or f"https://{domain}/"])[0]
    return _open_solve_window(f"site:{domain}", url, "solve")


@app.post("/api/sites/{domain}/solve/finish")
def finish_site_solve(domain: str, body: FinishIn):
    win = _finish_window(f"site:{domain}")
    info = _save_after_solve(win, body.account)
    sessions.close_window(f"site:{domain}")
    audit.log("challenge_solved", domain=domain, by="user")
    return info


# ---- routes: jobs --------------------------------------------------------------------

def _start_run(urls: list[str], recipe: dict, max_pages: int, personal_fields: list[str],
               personal_purpose: str, scrape_id: int | None = None) -> int:
    """Validate and start a crawl job (shared by /api/jobs and saved scrapes)."""
    logged_in = any((s := sessions.for_host(fetcher.host_of(u))) and s.get("kind") == sessions.KIND_LOGIN
                    for u in urls)
    if config.RESPECT_ROBOTS:
        blocked = [u for u in urls if not fetcher.robots_allows(u)]
        if blocked:
            raise HTTPException(403, f"robots.txt disallows: {', '.join(blocked)}")
    names = {f["name"] for f in recipe["fields"]}
    keep = [f for f in personal_fields if f in names]
    personal = None
    if keep:
        if len(personal_purpose.strip()) < 10:
            raise HTTPException(400, "Collecting personal data needs a purpose (at least 10 characters).")
        personal = {"fields": keep, "purpose": personal_purpose.strip(), "confirmed_at": time.time()}
    return jobs.start(urls, recipe, max_pages, logged_in=logged_in, personal=personal, scrape_id=scrape_id)


def friendly_progress(job: dict) -> str:
    """One plain-language line about a run, for people who don't want the technical message."""
    s, done, total, rows = job["status"], job["pages_done"], job["pages_total"], job["rows"]
    if s == "queued":
        if "Resum" in job["message"]:
            return "Picking up where it stopped…"
        if time.time() - job["created_at"] < 15:
            return "Starting…"
        return "Waiting for a free slot: other scrapes are still running."
    if s == "running":
        return (f"Collecting page {min(done + 1, max(total, 1))} of {max(total, done + 1)} · {rows} rows so far. "
                "It pauses a few seconds between pages to be polite to the site.")
    if s == "done":
        return f"Finished: {rows} rows from {done} page(s)." + (" Some pages had problems." if "Issues" in job["message"] else "")
    if s == "needs_you":
        return ("The site wants you to log in again before continuing." if job.get("paused_kind") == "login"
                else "The site asked for a human check. Open it, solve it, and the scrape continues.")
    if s == "blocked":
        if "robots" in job["message"]:
            return "Stopped: the site's crawling rules don't allow this page."
        if "cooling" in job["message"] or "429" in job["message"] or "challenge" in job["message"]:
            return "Stopped: the site is limiting automated visits. Try again later."
        return f"Stopped: the site refused access. {rows} rows were saved."
    if s == "stopped":
        if "budget" in job["message"]:
            return f"Stopped: today's page limit for this site is reached. {rows} rows were saved."
        return f"Stopped. {rows} rows were saved."
    if s == "error":
        return "Something went wrong while collecting. See the details below."
    return job["message"]


@app.post("/api/jobs")
def create_job(body: JobIn):
    urls = _clean_urls(body.urls)
    recipe = _normalize_or_http_error(body.recipe)
    if body.save_recipe:
        for domain in {fetcher.domain_of(u) for u in urls}:
            saved = db.get_recipe(domain)
            if not saved or saved["recipe"] != recipe:
                db.save_recipe(domain, recipe, body.recipe_source)
    return {"job_id": _start_run(urls, recipe, body.max_pages, body.personal_fields, body.personal_purpose)}


@app.get("/api/jobs")
def list_jobs(scrape_id: int | None = None, limit: int = 50):
    return [{**{k: v for k, v in j.items() if k != "recipe"}, "progress": friendly_progress(j),
             "columns": [f["name"] for f in j["recipe"].get("fields", [])]}
            for j in db.list_jobs(min(max(limit, 1), 200), scrape_id)]


# ---- routes: saved scrapes -----------------------------------------------------------

def _scrape_or_404(scrape_id: int) -> dict:
    s = db.get_scrape(scrape_id)
    if not s:
        raise HTTPException(404, "Saved scrape not found")
    return s


@app.get("/api/scrapes")
def list_scrapes():
    out = []
    for s in db.list_scrapes():
        out.append({**{k: v for k, v in s.items() if k != "recipe"},
                    "columns": [f["name"] for f in s["recipe"].get("fields", [])],
                    "domain": fetcher.domain_of(s["urls"][0]) if s["urls"] else ""})
    return out


@app.get("/api/scrapes/{scrape_id}")
def get_scrape(scrape_id: int):
    return _scrape_or_404(scrape_id)


@app.post("/api/scrapes")
def create_scrape(body: ScrapeIn):
    urls = _clean_urls(body.urls)
    recipe = _normalize_or_http_error(body.recipe)
    scrape_id = db.create_scrape(body.name.strip(), urls, recipe, body.max_pages, body.page_type)
    domain = fetcher.domain_of(urls[0])
    saved = db.get_recipe(domain)
    if not saved or saved["recipe"] != recipe:
        db.save_recipe(domain, recipe, "ai" if not saved else "manual")
    audit.log("scrape_saved", scrape_id=scrape_id, domain=domain)
    job_id = None
    if body.run:
        job_id = _start_run(urls, recipe, body.max_pages, body.personal_fields, body.personal_purpose,
                            scrape_id)
    return {"scrape_id": scrape_id, "job_id": job_id}


@app.put("/api/scrapes/{scrape_id}")
def update_scrape(scrape_id: int, body: ScrapeUpdate):
    _scrape_or_404(scrape_id)
    urls = _clean_urls(body.urls) if body.urls is not None else None
    recipe = _normalize_or_http_error(body.recipe) if body.recipe is not None else None
    db.update_scrape(scrape_id, name=body.name.strip() if body.name else None, urls=urls, recipe=recipe,
                     max_pages=body.max_pages)
    return db.get_scrape(scrape_id)


@app.post("/api/scrapes/{scrape_id}/run")
def run_scrape(scrape_id: int, body: RunIn):
    s = _scrape_or_404(scrape_id)
    recipe = _normalize_or_http_error(s["recipe"])
    return {"job_id": _start_run(s["urls"], recipe, s["max_pages"], body.personal_fields,
                                 body.personal_purpose, scrape_id)}


@app.post("/api/scrapes/{scrape_id}/duplicate")
def duplicate_scrape(scrape_id: int):
    s = _scrape_or_404(scrape_id)
    new_id = db.create_scrape(f"{s['name']} (copy)", s["urls"], s["recipe"], s["max_pages"], s["page_type"])
    return {"scrape_id": new_id}


@app.delete("/api/scrapes/{scrape_id}")
def delete_scrape(scrape_id: int):
    _scrape_or_404(scrape_id)
    db.delete_scrape(scrape_id)
    audit.log("scrape_deleted", scrape_id=scrape_id)
    return {"ok": True}


def _job_or_404(job_id: int) -> dict:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    return _job_or_404(job_id)


@app.post("/api/jobs/{job_id}/stop")
def stop_job(job_id: int):
    job = _job_or_404(job_id)
    if job["status"] == "needs_you":
        db.update_job(job_id, status="stopped", message="Stopped by user", finished_at=time.time())
        return {"stopping": True}
    return {"stopping": jobs.stop(job_id)}


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: int):
    _job_or_404(job_id)
    if not jobs.resume(job_id):
        raise HTTPException(409, "This job cannot be resumed")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/solve")
def start_job_solve(job_id: int):
    job = _job_or_404(job_id)
    if job["status"] != "needs_you" or not job["paused_url"]:
        raise HTTPException(409, "This job is not waiting for you")
    return _open_solve_window(f"job:{job_id}", job["paused_url"],
                              "login" if job["paused_kind"] == "login" else "solve")


@app.post("/api/jobs/{job_id}/solve/finish")
def finish_job_solve(job_id: int, body: FinishIn):
    job = _job_or_404(job_id)
    win = _finish_window(f"job:{job_id}")
    info = _save_after_solve(win, body.account, job["paused_kind"])
    sessions.close_window(f"job:{job_id}")
    audit.log("challenge_solved", job_id=job_id, domain=fetcher.domain_of(job["paused_url"]), by="user")
    jobs.resume(job_id)
    return {"session": info, "resumed": True}


@app.post("/api/jobs/{job_id}/keep")
def keep_job(job_id: int, body: KeepIn):
    _job_or_404(job_id)
    db.update_job(job_id, keep=int(body.keep))
    return {"keep": body.keep}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int):
    job = _job_or_404(job_id)
    if job["status"] in ("queued", "running"):
        raise HTTPException(409, "Stop the job before deleting it")
    db.delete_job(job_id)
    audit.log("job_deleted", job_id=job_id)
    return {"ok": True}


def _file_stem(name: str, job_id: int) -> str:
    import re as _re
    stem = _re.sub(r"[^A-Za-z0-9.-]+", "_", name).strip("_.")
    stem = _re.sub(r"_+", "_", stem)[:60]
    return f"{stem}-run{job_id}" if stem else f"job-{job_id}"


def _visible_records(job: dict, show_personal: bool) -> list[dict]:
    records = db.get_records(job["id"])
    keep = tuple(job["personal"].get("fields", [])) if show_personal else ()
    return privacy.mask_rows(records, keep)


@app.get("/api/jobs/{job_id}/records")
def job_records(job_id: int, show_personal: bool = False):
    job = _job_or_404(job_id)
    return _visible_records(job, show_personal)


@app.get("/api/jobs/{job_id}/export")
def export_job(job_id: int, fmt: str = "csv", show_personal: bool = False, columns: str = "",
               name: str = ""):
    job = _job_or_404(job_id)
    if fmt not in ("csv", "xlsx"):
        raise HTTPException(400, "fmt must be csv or xlsx")
    records = _visible_records(job, show_personal)
    wanted = [c for c in columns.split(",") if c]
    if wanted:
        records = [{c: r.get(c, "") for c in wanted} for r in records]
    audit.log("export", job_id=job_id, fmt=fmt, rows=len(records),
              personal=bool(show_personal and job["personal"].get("fields")))
    if fmt == "xlsx":
        return Response(export.to_xlsx(records),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{_file_stem(name, job_id)}.xlsx"'})
    return Response(export.to_csv(records), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{_file_stem(name, job_id)}.csv"'})


# ---- routes: activity log ------------------------------------------------------------

@app.get("/api/audit")
def audit_log(limit: int = 200, domain: str | None = None, event: str | None = None):
    return audit.tail(min(max(limit, 1), 2000), event=event, domain=domain)
