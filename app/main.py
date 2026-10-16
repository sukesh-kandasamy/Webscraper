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
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
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
    fields: list[str] = Field(default_factory=lambda: list(config.DEFAULT_FIELDS))
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


class PolicyIn(BaseModel):
    basis: str = Field(pattern="^(allowed|permission|personal)$")
    terms_url: str = Field(default="", max_length=500)
    purpose: str = Field(min_length=5, max_length=500)
    login_ok: bool = False
    own_account: bool = False


class WindowIn(BaseModel):
    url: str | None = None


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


def _policy_for(host: str) -> dict | None:
    for d in dict.fromkeys([host, sessions.base_domain(host)]):
        p = db.get_site_settings(d).get("policy")
        if p and p.get("basis") and p.get("purpose"):
            return p
    return None


def _robots_summary(url: str) -> dict:
    rules = fetcher.robots_rules(url)
    return {"allowed": robots.is_allowed(rules, url),
            "disallow": [p for allow, p in rules if not allow][:40],
            "allow": [p for allow, p in rules if allow][:20],
            "robots_url": f"{urlsplit(url).scheme}://{urlsplit(url).netloc}/robots.txt"}


def _require_policy(url: str, login: bool = False) -> dict:
    host = fetcher.host_of(url)
    policy = _policy_for(host)
    if not policy:
        raise HTTPException(428, {"code": "policy_required", "domain": host, "url": url,
                                  "message": f"Before scraping {host}, confirm how you are allowed "
                                             "to use it (Terms, purpose).",
                                  "robots": _robots_summary(url)})
    if login and not (policy.get("login_ok") and policy.get("own_account")):
        raise HTTPException(428, {"code": "policy_login_required", "domain": host, "url": url,
                                  "message": "Logging in to this site needs your confirmation that "
                                             "it is your own account and automated access is allowed.",
                                  "robots": _robots_summary(url)})
    return policy


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
            "fields": [{"name": f, "selector": "", "attr": "text"} for f in fields],
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
            "report": extractor.field_report(rows, recipe)}


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
    _require_policy(url)
    fields = [f.strip() for f in body.fields if f.strip()] or list(config.DEFAULT_FIELDS)
    domain = fetcher.domain_of(url)
    saved = db.get_recipe(domain)
    saved_recipe = None
    if saved and saved["recipe"].get("item_selector") and not body.force_ai:
        saved_recipe = extractor.normalize_recipe(saved["recipe"])
    page = _fetch_or_http_error(url, saved_recipe)
    domain = fetcher.domain_of(page.final_url)
    render_note = _remember_render(domain, page)

    if saved_recipe:
        rows = extractor.extract(page.html, saved_recipe, page.final_url)
        if rows:
            return _result(domain, page, saved_recipe, saved["source"], rows,
                           "Using the saved recipe for this site. Click 'Re-analyse with AI' "
                           "to build a new one for your fields." + render_note)

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
                                            redact=page.logged_in)
    except recipe_builder.RecipeBuildError as e:
        raise HTTPException(422, {"message": str(e), "domain": domain,
                                  "starter_recipe": _starter_recipe(page.html, fields)})
    except Exception as e:  # API / network errors from the AI SDK
        raise HTTPException(502, logsafe.redact(f"AI request failed: {type(e).__name__}: {e}")[:400])
    audit.log("recipe_built", domain=domain, provider=config.AI_PROVIDER, redacted=page.logged_in,
              rows=len(built["rows"]))
    note = " Page text was redacted before it was sent to the AI." if page.logged_in else ""
    return _result(domain, page, built["recipe"], "ai", built["rows"],
                   built["notes"] + render_note + note)


@app.post("/api/preview")
def preview(body: PreviewIn):
    url = _clean_urls([body.url])[0]
    _require_policy(url)
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
    return {"domain": domain, "settings": settings, "policy": settings.get("policy"),
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


@app.put("/api/sites/{domain}/policy")
def put_site_policy(domain: str, body: PolicyIn):
    if body.terms_url:
        try:
            netguard.check_url(body.terms_url)
        except netguard.UnsafeURL as e:
            raise HTTPException(400, f"Terms URL: {e}")
    policy = {**body.model_dump(), "confirmed_at": time.time()}
    fetcher.update_settings(domain.lower(), {"policy": policy})
    audit.log("policy_confirmed", domain=domain.lower(), basis=body.basis, login_ok=body.login_ok,
              own_account=body.own_account)
    return policy


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
def start_login(domain: str, body: WindowIn):
    if not _browser_available():
        raise HTTPException(400, browser.install_hint())
    url = _clean_urls([body.url or f"https://{domain}/"])[0]
    _require_policy(url, login=True)
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
    _require_policy(url)
    return _open_solve_window(f"site:{domain}", url, "solve")


@app.post("/api/sites/{domain}/solve/finish")
def finish_site_solve(domain: str, body: FinishIn):
    win = _finish_window(f"site:{domain}")
    info = _save_after_solve(win, body.account)
    sessions.close_window(f"site:{domain}")
    audit.log("challenge_solved", domain=domain, by="user")
    return info


# ---- routes: jobs --------------------------------------------------------------------

@app.post("/api/jobs")
def create_job(body: JobIn):
    urls = _clean_urls(body.urls)
    recipe = _normalize_or_http_error(body.recipe)
    logged_in = any((s := sessions.for_host(fetcher.host_of(u))) and s.get("kind") == sessions.KIND_LOGIN
                    for u in urls)
    for u in urls:
        _require_policy(u, login=logged_in)
    if config.RESPECT_ROBOTS:
        blocked = [u for u in urls if not fetcher.robots_allows(u)]
        if blocked:
            raise HTTPException(403, f"robots.txt disallows: {', '.join(blocked)}")
    names = {f["name"] for f in recipe["fields"]}
    keep = [f for f in body.personal_fields if f in names]
    personal = None
    if keep:
        if len(body.personal_purpose.strip()) < 10:
            raise HTTPException(400, "Collecting personal data needs a purpose (at least 10 characters).")
        personal = {"fields": keep, "purpose": body.personal_purpose.strip(), "confirmed_at": time.time()}
    if body.save_recipe:
        for domain in {fetcher.domain_of(u) for u in urls}:
            saved = db.get_recipe(domain)
            if not saved or saved["recipe"] != recipe:
                db.save_recipe(domain, recipe, body.recipe_source)
    return {"job_id": jobs.start(urls, recipe, body.max_pages, logged_in=logged_in, personal=personal)}


@app.get("/api/jobs")
def list_jobs():
    return [{k: v for k, v in j.items() if k != "recipe"} for j in db.list_jobs()]


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
    if job["paused_kind"] == "login":
        _require_policy(job["paused_url"], login=True)
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


def _visible_records(job: dict, show_personal: bool) -> list[dict]:
    records = db.get_records(job["id"])
    keep = tuple(job["personal"].get("fields", [])) if show_personal else ()
    return privacy.mask_rows(records, keep)


@app.get("/api/jobs/{job_id}/records")
def job_records(job_id: int, show_personal: bool = False):
    job = _job_or_404(job_id)
    return _visible_records(job, show_personal)


@app.get("/api/jobs/{job_id}/export")
def export_job(job_id: int, fmt: str = "csv", show_personal: bool = False):
    job = _job_or_404(job_id)
    if fmt not in ("csv", "xlsx"):
        raise HTTPException(400, "fmt must be csv or xlsx")
    records = _visible_records(job, show_personal)
    audit.log("export", job_id=job_id, fmt=fmt, rows=len(records),
              personal=bool(show_personal and job["personal"].get("fields")))
    if fmt == "xlsx":
        return Response(export.to_xlsx(records),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="job-{job_id}.xlsx"'})
    return Response(export.to_csv(records), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="job-{job_id}.csv"'})


# ---- routes: activity log ------------------------------------------------------------

@app.get("/api/audit")
def audit_log(limit: int = 200, domain: str | None = None, event: str | None = None):
    return audit.tail(min(max(limit, 1), 2000), event=event, domain=domain)
