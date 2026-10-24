"""Agent mode API.

For the UI:      /api/agent/status, /plan, /runs (list, get, plan edit, start, stop, delete, step screenshots)
For the worker:  /api/agent/worker/* (needs the X-Agent-Token made at startup; see runner.py)
"""
import base64
import hmac
import io
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import config
from ..extraction import recipe_builder
from ..fetching import blockdetect, fetcher
from ..runs import jobs
from ..security import audit, logsafe, sessions
from . import guard, handoff, planner, runner, store

router = APIRouter(prefix="/api/agent")
MAX_HTML = 2_000_000
MAX_SHOT = 400_000


# ---- UI ---------------------------------------------------------------------------------

class PlanIn(BaseModel):
    goal: str = Field(min_length=3, max_length=planner.MAX_GOAL_CHARS)
    site: str = Field(min_length=3, max_length=500)
    pages: int | None = Field(default=None, ge=1, le=50)


class PlanEdit(BaseModel):
    plan: dict


class LimitsIn(BaseModel):
    steps: int = Field(default=config.AGENT_MAX_STEPS, ge=5, le=150)
    minutes: float = Field(default=config.AGENT_MAX_MINUTES, ge=1, le=60)
    ai_calls: int = Field(default=config.AGENT_MAX_AI_CALLS, ge=5, le=300)


def _enabled() -> None:
    if not config.AGENT_ENABLED:
        raise HTTPException(403, "Agent mode is turned off (SCRAPER_AGENT=0).")


def _run_or_404(run_id: int) -> dict:
    run = store.get(run_id)
    if not run:
        raise HTTPException(404, "Agent run not found")
    return run


def _view(run: dict, after: int = 0) -> dict:
    run = handoff.refresh(run)
    out = {**run, "steps_list": store.steps(run["id"], after)}
    if run.get("job_id"):
        job = jobs.db.get_job(run["job_id"])
        if job:
            out["job"] = {k: job[k] for k in ("id", "uid", "status", "rows", "pages_done", "pages_total", "message")}
            out["job_uid"] = job["uid"]
            out["job"]["quality"] = job.get("quality") or {}
    if run["started_at"]:
        out["elapsed_s"] = round((run["finished_at"] or time.time()) - run["started_at"])
    return out


@router.get("/status")
def agent_status():
    return runner.status()


@router.post("/plan")
def make_plan(body: PlanIn):
    _enabled()
    try:
        plan = planner.make_plan(body.goal, body.site, body.pages)
    except planner.PlanError as e:
        raise HTTPException(400, str(e))
    except recipe_builder.RecipeBuildError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(502, logsafe.redact(f"AI request failed: {type(e).__name__}: {e}")[:400])
    limits = LimitsIn().model_dump()
    run_id = store.create(body.goal.strip(), guard.site_domain(plan["start_url"]), plan["start_url"], plan, limits)
    audit.log("agent_planned", run_id=run_id, domain=guard.site_domain(plan["start_url"]))
    return _view(store.get(run_id))


@router.put("/runs/{run_id}/plan")
def edit_plan(run_id: int, body: PlanEdit):
    run = _run_or_404(run_id)
    if run["status"] != "draft":
        raise HTTPException(409, "Only a plan that hasn't started can be changed.")
    try:
        plan = planner.clean_plan(body.plan, run["goal"], body.plan.get("start_url") or run["start_url"])
    except planner.PlanError as e:
        raise HTTPException(400, str(e))
    store.update(run_id, plan=plan, start_url=plan["start_url"], site=guard.site_domain(plan["start_url"]))
    return _view(store.get(run_id))


@router.post("/runs/{run_id}/start")
def start_run(run_id: int, request: Request, body: LimitsIn | None = None):
    _enabled()
    run = _run_or_404(run_id)
    if run["status"] != "draft":
        raise HTTPException(409, "This run has already started.")
    st = runner.status()
    if not st["key_set"]:
        raise HTTPException(400, f"Agent mode needs an AI key for {config.AGENT_PROVIDER} in .env.")
    ok, why = guard.check_url(run["start_url"], run["site"])
    if not ok:
        raise HTTPException(403, f"The agent can't start there: {why}")
    store.update(run_id, status="queued", limits=(body or LimitsIn()).model_dump(), message="Waiting for the agent")
    try:
        runner.ensure(str(request.base_url))
    except RuntimeError as e:
        store.update(run_id, status="draft", message="")
        raise HTTPException(400, str(e))
    audit.log("agent_started", run_id=run_id, domain=run["site"], provider=config.AGENT_PROVIDER)
    store.add_step(run_id, "info", f"Started with {config.AGENT_PROVIDER} · {config.AGENT_MODEL}. "
                                   "Opening a browser…")
    return _view(store.get(run_id))


@router.get("/runs")
def list_runs(limit: int = 30):
    return [handoff.refresh(r) for r in store.list_runs(min(max(limit, 1), 100))]


@router.get("/runs/{run_id}")
def get_run(run_id: int, after: int = 0):
    return _view(_run_or_404(run_id), after)


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: int):
    run = _run_or_404(run_id)
    if run["status"] in ("draft", "queued"):
        store.update(run_id, status="stopped", message="Stopped before it started", finished_at=time.time())
    elif run["status"] in ("navigating", "fixing"):
        store.update(run_id, stop_requested=1, message="Stopping…")
    elif run["status"] == "collecting" and run.get("job_id"):
        jobs.stop(run["job_id"])
    return _view(store.get(run_id))


@router.delete("/runs/{run_id}")
def delete_run(run_id: int):
    run = _run_or_404(run_id)
    if run["status"] in store.ACTIVE:
        raise HTTPException(409, "Stop the run before deleting it.")
    store.delete(run_id)
    shots = config.AGENT_SHOTS_DIR / str(run_id)
    if shots.exists():
        for f in shots.glob("*.jpg"):
            f.unlink()
        shots.rmdir()
    return {"ok": True}


@router.get("/runs/{run_id}/shots/{n}.jpg")
def step_shot(run_id: int, n: int):
    f = config.AGENT_SHOTS_DIR / str(int(run_id)) / f"{int(n)}.jpg"
    if not f.exists():
        raise HTTPException(404, "No screenshot")
    return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


# ---- worker -----------------------------------------------------------------------------

def worker_auth(x_agent_token: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_agent_token, runner.TOKEN):
        raise HTTPException(403, "Not the agent worker")


worker = APIRouter(prefix="/api/agent/worker", dependencies=[Depends(worker_auth)])


@worker.get("/next")
def next_run():
    runner.seen()
    run = store.claim_next()
    if not run:
        return {}
    session = sessions.for_host(fetcher.host_of(run["start_url"]))
    use = fetcher.site_settings(fetcher.domain_of(run["start_url"])).get("use_session", True)
    return {"id": run["id"], "task": planner.task_text(run["plan"]), "start_url": run["start_url"],
            "site": run["site"], "limits": run["limits"],
            "allowed_domains": [run["site"], f"*.{run['site']}", fetcher.domain_of(run["start_url"]),
                                f"*.{fetcher.domain_of(run['start_url'])}"],
            "storage_state": session["state"] if session and use else None}


class CheckIn(BaseModel):
    action: str
    url: str = ""
    element: dict = Field(default_factory=dict)
    text: str = Field(default="", max_length=5000)


@worker.post("/runs/{run_id}/check")
def check(run_id: int, body: CheckIn):
    run = _run_or_404(run_id)
    if body.action == "navigate":
        ok, why = guard.check_url(body.url, run["site"])
    elif body.action in ("click", "select_dropdown"):
        ok, why = guard.check_click({**body.element, "text": body.text or body.element.get("text", "")}
                                    if body.action == "select_dropdown" else body.element)
    elif body.action == "input":
        ok, why = guard.check_input(body.element, body.text)
    else:
        ok, why = True, ""
    if not ok:
        store.add_step(run_id, "warn", f"Refused: {why}", url=body.url, detail={"action": body.action})
        audit.log("agent_refused", run_id=run_id, action=body.action, reason=why[:200])
    return {"ok": ok, "reason": why}


class StepIn(BaseModel):
    kind: str = Field(pattern="^(action|thought|info|warn)$")
    text: str = Field(max_length=1000)
    url: str = ""
    detail: dict = Field(default_factory=dict)
    shot: str = ""                 # base64 JPEG thumbnail
    html: str = ""                 # current page, for human-check detection
    steps: int | None = None
    ai_calls: int | None = None


@worker.post("/runs/{run_id}/step")
def add_step(run_id: int, body: StepIn):
    run = _run_or_404(run_id)
    shot = bool(body.shot) and len(body.shot) <= MAX_SHOT
    n = store.add_step(run_id, body.kind, logsafe.redact(body.text), url=body.url, detail=body.detail, shot=shot)
    if shot:
        d = config.AGENT_SHOTS_DIR / str(run_id)
        d.mkdir(parents=True, exist_ok=True)
        try:
            (d / f"{n}.jpg").write_bytes(base64.b64decode(body.shot))
        except ValueError:
            pass
    counters = {k: v for k, v in (("steps", body.steps), ("ai_calls", body.ai_calls)) if v is not None}
    if counters:
        store.update(run_id, **counters)
    if body.url:
        store.update(run_id, final_url=body.url[:1000], message=body.text[:200] if body.kind == "action" else run["message"])
    run = store.get(run_id)
    if run["stop_requested"]:
        return {"stop": True, "reason": "stopped"}
    limits = run["limits"]
    if run["ai_calls"] >= limits.get("ai_calls", config.AGENT_MAX_AI_CALLS):
        return {"stop": True, "reason": "limit", "message": f"Reached the limit of {limits.get('ai_calls')} AI calls."}
    if run["started_at"] and time.time() - run["started_at"] > limits.get("minutes", config.AGENT_MAX_MINUTES) * 60:
        return {"stop": True, "reason": "limit", "message": f"Reached the time limit of {limits.get('minutes')} minutes."}
    if body.html:
        kind = blockdetect.detect(body.html[:MAX_HTML], 200, body.url)
        if kind:
            return {"stop": True, "reason": "human_check", "kind": kind,
                    "message": f"The site showed {blockdetect.describe(kind)}."}
    return {"stop": False}


class ArrivedIn(BaseModel):
    url: str
    html: str = Field(max_length=10_000_000)
    note: str = Field(default="", max_length=500)


@worker.post("/runs/{run_id}/arrived")
def arrived(run_id: int, body: ArrivedIn):
    run = _run_or_404(run_id)
    if run["status"] != "navigating":
        raise HTTPException(409, "This run is not navigating")
    ok, why = guard.check_url(body.url, run["site"])
    if not ok:
        return {"ok": False, "reason": why}
    kind = blockdetect.detect(body.html, 200, body.url)
    if kind:
        return {"ok": False, "reason": f"That page is a {blockdetect.describe(kind)}, not the results."}
    handoff.start(run_id, body.url, body.html, body.note)
    return {"ok": True}


class FinishIn(BaseModel):
    status: str = Field(pattern="^(stopped|needs_you|limit|error)$")
    message: str = Field(default="", max_length=1000)
    steps: int | None = None
    ai_calls: int | None = None


@worker.post("/runs/{run_id}/finish")
def finish(run_id: int, body: FinishIn):
    run = _run_or_404(run_id)
    if run["status"] != "navigating":
        return {"ok": True}           # already handed over (collecting) or finished
    msg = logsafe.redact(body.message) or {"stopped": "Stopped by you.", "limit": "Reached a limit.",
                                           "needs_you": "The site asked for a human check.",
                                           "error": "The agent could not reach the results."}[body.status]
    fields = {k: v for k, v in (("steps", body.steps), ("ai_calls", body.ai_calls)) if v is not None}
    store.update(run_id, status=body.status, message=msg, summary=msg, finished_at=time.time(), **fields)
    store.add_step(run_id, "warn" if body.status != "stopped" else "info", msg)
    if body.status == "needs_you":
        fetcher.update_settings(fetcher.domain_of(run["start_url"]), {"last_challenge": time.time()})
    audit.log("agent_finished", run_id=run_id, status=body.status)
    return {"ok": True}


@router.post("/rerecord/{scrape_id}")
def rerecord(scrape_id: int, request: Request, body: LimitsIn | None = None):
    """The site changed and a saved agent scrape's steps stopped working: let the agent find the way
    again with the same plan. The new steps and columns replace the old ones in the same saved scrape."""
    _enabled()
    s = jobs.db.get_scrape(scrape_id)
    if not s:
        raise HTTPException(404, "Saved scrape not found")
    info = s["recipe"].get("agent")
    if not info or not info.get("plan"):
        raise HTTPException(400, "This scrape wasn't set up by the agent, so there are no steps to re-record.")
    if not runner.status()["key_set"]:
        raise HTTPException(400, f"Agent mode needs an AI key for {config.AGENT_PROVIDER} in .env.")
    start = info.get("start_url") or info["plan"].get("start_url")
    try:
        plan = planner.clean_plan(info["plan"], info["goal"], start)
    except planner.PlanError as e:
        raise HTTPException(400, str(e))
    ok, why = guard.check_url(plan["start_url"], guard.site_domain(plan["start_url"]))
    if not ok:
        raise HTTPException(403, f"The agent can't start there: {why}")
    run_id = store.create(info["goal"], guard.site_domain(plan["start_url"]), plan["start_url"], plan,
                          (body or LimitsIn()).model_dump())
    store.update(run_id, target_scrape_id=scrape_id, status="queued", message="Waiting for the agent")
    store.add_step(run_id, "info", f"Re-recording the steps for “{s['name']}” with {config.AGENT_PROVIDER} · "
                                   f"{config.AGENT_MODEL}. Opening a browser…")
    try:
        runner.ensure(str(request.base_url))
    except RuntimeError as e:
        store.update(run_id, status="error", message=str(e), finished_at=time.time())
        raise HTTPException(400, str(e))
    audit.log("agent_rerecord", run_id=run_id, scrape_id=scrape_id, domain=guard.site_domain(plan["start_url"]))
    return _view(store.get(run_id))
