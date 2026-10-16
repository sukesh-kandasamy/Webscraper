"""FastAPI app: serves the UI and the JSON API.

Run:  uvicorn app.main:app --reload     then open http://localhost:8000
"""
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, export, extractor, fetcher, jobs, recipe_builder

PREVIEW_ROWS = 20


def _seed_builtin_recipes() -> None:
    for path in sorted(config.BUILTIN_RECIPES_DIR.glob("*.json")):
        domain = path.stem
        existing = db.get_recipe(domain)
        if existing is None or existing["source"] == "builtin":
            recipe = extractor.normalize_recipe(json.loads(path.read_text(encoding="utf-8")))
            db.save_recipe(domain, recipe, "builtin")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    _seed_builtin_recipes()
    jobs.resume_pending()
    yield


app = FastAPI(title="Universal Scraper", version="0.1", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.ROOT / "static"), name="static")


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
    render: str = Field(default="auto", pattern="^(auto|never|always)$")


class JobIn(BaseModel):
    urls: list[str]
    recipe: dict
    max_pages: int = Field(default=config.MAX_PAGES_PER_URL, ge=1, le=100)
    save_recipe: bool = True
    recipe_source: str = "manual"   # where the recipe came from: ai | manual | builtin


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
    return out


def _fetch_or_http_error(url: str) -> fetcher.Page:
    try:
        return fetcher.fetch(url)
    except fetcher.BlockedError as e:
        raise HTTPException(403, str(e))
    except fetcher.FetchError as e:
        raise HTTPException(502, str(e))


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
    return {"domain": domain, "url": page.final_url, "from_cache": page.from_cache,
            "source": source, "recipe": recipe, "notes": notes,
            "settings": db.get_site_settings(domain),
            "row_count": len(rows), "rows": rows[:PREVIEW_ROWS],
            "report": extractor.field_report(rows, recipe)}


# ---- routes --------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(config.ROOT / "static" / "index.html")


@app.get("/api/status")
def status():
    return {"ai_enabled": config.ai_enabled(), "model": config.ai_model(), "provider": config.AI_PROVIDER,
            "default_fields": config.DEFAULT_FIELDS, "respect_robots": config.RESPECT_ROBOTS,
            "delay": [config.MIN_DELAY, config.MAX_DELAY], "max_pages": config.MAX_PAGES_PER_URL,
            "max_jobs": config.MAX_CONCURRENT_JOBS}


@app.post("/api/analyse")
def analyse(body: AnalyseIn):
    """Get a recipe for the page: the saved one if it still works, otherwise ask Claude."""
    url = _clean_urls([body.url])[0]
    fields = [f.strip() for f in body.fields if f.strip()] or list(config.DEFAULT_FIELDS)
    page = _fetch_or_http_error(url)
    domain = fetcher.domain_of(page.final_url)

    saved = db.get_recipe(domain)
    if saved and not body.force_ai:
        recipe = extractor.normalize_recipe(saved["recipe"])
        rows = extractor.extract(page.html, recipe, page.final_url)
        if rows:
            return _result(domain, page, recipe, saved["source"], rows,
                           "Using the saved recipe for this site. Click 'Re-analyse with AI' "
                           "to build a new one for your fields.")

    if not config.ai_enabled():
        starter = _starter_recipe(page.html, fields)
        raise HTTPException(422, {
            "message": "No working recipe saved for this site and no AI API key is set. "
                       "Edit the starter recipe below by hand, or add an API key to .env.",
            "starter_recipe": starter, "domain": domain})

    try:
        built = recipe_builder.build_recipe(page.html, page.final_url, fields)
    except recipe_builder.RecipeBuildError as e:
        raise HTTPException(422, {"message": str(e), "domain": domain,
                                  "starter_recipe": _starter_recipe(page.html, fields)})
    except Exception as e:  # API/network errors from the Anthropic SDK
        raise HTTPException(502, f"AI request failed: {type(e).__name__}: {e}")
    return _result(domain, page, built["recipe"], "ai", built["rows"], built["notes"])


@app.post("/api/preview")
def preview(body: PreviewIn):
    url = _clean_urls([body.url])[0]
    recipe = _normalize_or_http_error(body.recipe)
    page = _fetch_or_http_error(url)
    try:
        rows = extractor.extract(page.html, recipe, page.final_url)
    except extractor.RecipeError as e:
        raise HTTPException(400, f"Recipe problem: {e}")
    return _result(fetcher.domain_of(page.final_url), page, recipe, "manual", rows)


@app.get("/api/sites")
def sites():
    return db.list_sites()


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
    return {"ok": True}


@app.get("/api/sites/{domain}/settings")
def get_site_settings(domain: str):
    return db.get_site_settings(domain)


@app.put("/api/sites/{domain}/settings")
def put_site_settings(domain: str, body: SettingsIn):
    settings = {k: v for k, v in body.model_dump().items() if v is not None}
    if settings.get("min_delay") is not None and settings.get("max_delay") is not None             and settings["min_delay"] > settings["max_delay"]:
        raise HTTPException(400, "min_delay must not be larger than max_delay")
    db.save_site_settings(domain, settings)
    fetcher.forget_settings(domain)
    return settings


@app.post("/api/jobs")
def create_job(body: JobIn):
    urls = _clean_urls(body.urls)
    recipe = _normalize_or_http_error(body.recipe)
    if config.RESPECT_ROBOTS:
        blocked = [u for u in urls if not fetcher.robots_allows(u)]
        if blocked:
            raise HTTPException(403, f"robots.txt disallows: {', '.join(blocked)}")
    if body.save_recipe:
        for domain in {fetcher.domain_of(u) for u in urls}:
            saved = db.get_recipe(domain)
            if not saved or saved["recipe"] != recipe:
                db.save_recipe(domain, recipe, body.recipe_source)
    return {"job_id": jobs.start(urls, recipe, body.max_pages)}


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
    _job_or_404(job_id)
    return {"stopping": jobs.stop(job_id)}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int):
    job = _job_or_404(job_id)
    if job["status"] in ("queued", "running"):
        raise HTTPException(409, "Stop the job before deleting it")
    db.delete_job(job_id)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/records")
def job_records(job_id: int):
    _job_or_404(job_id)
    return db.get_records(job_id)


@app.get("/api/jobs/{job_id}/export")
def export_job(job_id: int, fmt: str = "csv"):
    _job_or_404(job_id)
    records = db.get_records(job_id)
    if fmt == "xlsx":
        return Response(export.to_xlsx(records),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="job-{job_id}.xlsx"'})
    if fmt == "csv":
        return Response(export.to_csv(records), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="job-{job_id}.csv"'})
    raise HTTPException(400, "fmt must be csv or xlsx")
