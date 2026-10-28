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
from . import guard, handoff, planner, react, runner, store
from .react import memory as react_memory
from .react import tools as react_tools

router = APIRouter(prefix="/api/agent")
MAX_HTML = 2_000_000
MAX_SHOT = 400_000


# ---- UI ---------------------------------------------------------------------------------

class PlanIn(BaseModel):
    goal: str = Field(min_length=3, max_length=planner.MAX_GOAL_CHARS)
    site: str = Field(default="", max_length=500)        # empty: named in the goal, or the AI suggests sites
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


def _run_or_404(ref: str | int) -> dict:
    """An agent run by its Run ID (RUN-7K3QX9) or by its internal number (the worker uses numbers)."""
    ref = str(ref).strip()
    run = (store.get(int(ref)) if ref.isdigit() else
           store.get_by_uid(ref) if jobs.db.RUN_UID.match(ref.upper()) else None)
    if not run:
        raise HTTPException(404, "Agent run not found")
    return run


def _view(run: dict, after: int = 0) -> dict:
    run = handoff.refresh(run)
    out = {**run, "steps_list": store.steps(run["id"], after)}
    out.pop("scratchpad", None)            # the loop's working memory: large, and the timeline shows it already
    if run["status"] == "draft":
        out["memory"] = [{k: e.get(k) for k in ("goal", "at", "steps", "needs_browser")}
                         for e in react_memory.recall(run["start_url"])]
    if run.get("job_id"):
        job = jobs.db.get_job(run["job_id"])
        if job:
            out["job"] = {k: job[k] for k in ("id", "uid", "status", "rows", "pages_done", "pages_total", "message")}
            out["job_uid"] = job["uid"]
            out["job"]["quality"] = job.get("quality") or {}
    if run["started_at"]:
        out["elapsed_s"] = int((run["finished_at"] or time.time()) - run["started_at"])
    out["server_now"] = time.time()          # lets the page run the clock itself between updates
    return out


@router.get("/status")
def agent_status():
    return runner.status()


def _ai_error(e: Exception) -> HTTPException:
    if isinstance(e, planner.PlanError):
        return HTTPException(400, str(e))
    if isinstance(e, recipe_builder.RecipeBuildError):
        return HTTPException(422, str(e))
    return HTTPException(502, logsafe.redact(f"AI request failed: {type(e).__name__}: {e}")[:400])


@router.post("/plan")
def make_plan(body: PlanIn):
    """A plan for the goal. Without a site (in the box or named in the goal) the AI suggests 1-3 sites and
    no run is made yet: the answer is {needs_site: true, candidates, dropped, question}; post again with
    the chosen site."""
    _enabled()
    site = body.site.strip() or planner.site_in_goal(body.goal)
    if not site:
        try:
            found = planner.suggest_sites(body.goal)
        except Exception as e:  # noqa: BLE001
            raise _ai_error(e)
        audit.log("agent_sites_suggested", candidates=[c["site"] for c in found["candidates"]])
        return {"needs_site": True, "goal": body.goal.strip(), **found}
    try:
        plan = planner.make_plan(body.goal, site, body.pages)
    except Exception as e:  # noqa: BLE001
        raise _ai_error(e)
    limits = LimitsIn().model_dump()
    run_id = store.create(body.goal.strip(), guard.site_domain(plan["start_url"]), plan["start_url"], plan, limits)
    audit.log("agent_planned", run_id=run_id, domain=guard.site_domain(plan["start_url"]))
    return _view(store.get(run_id))


@router.put("/runs/{run_id}/plan")
def edit_plan(run_id: str, body: PlanEdit):
    run = _run_or_404(run_id)
    run_id = run["id"]
    if run["status"] != "draft":
        raise HTTPException(409, "Only a plan that hasn't started can be changed.")
    try:
        plan = planner.clean_plan(body.plan, run["goal"], body.plan.get("start_url") or run["start_url"])
    except planner.PlanError as e:
        raise HTTPException(400, str(e))
    store.update(run_id, plan=plan, start_url=plan["start_url"], site=guard.site_domain(plan["start_url"]),
                 checklist=plan["checklist"])
    return _view(store.get(run_id))


@router.post("/runs/{run_id}/start")
def start_run(run_id: str, request: Request, body: LimitsIn | None = None):
    _enabled()
    run = _run_or_404(run_id)
    run_id = run["id"]
    if run["status"] != "draft":
        raise HTTPException(409, "This run has already started.")
    if not config.agent_key():
        raise HTTPException(400, f"Agent mode needs an AI key for {config.AGENT_PROVIDER} in .env.")
    if fetcher.site_policy(fetcher.domain_of(run["start_url"])) == "approval":
        domain = fetcher.domain_of(run["start_url"])
        raise HTTPException(409, {"code": "needs_approval", "domain": domain,
                                  "message": str(fetcher.NeedsApproval(domain))})
    ok, why = guard.check_url(run["start_url"], run["site"])
    if not ok:
        raise HTTPException(403, f"The agent can't start there: {why}")
    store.update(run_id, status="queued", limits=(body or LimitsIn()).model_dump(), message="Waiting for the agent",
                 engine="react")
    react.start(run_id, str(request.base_url))
    audit.log("agent_started", run_id=run_id, domain=run["site"], provider=config.AGENT_PROVIDER)
    return _view(store.get(run_id))


@router.get("/runs")
def list_runs(limit: int = 30):
    return [handoff.refresh(r) for r in store.list_runs(min(max(limit, 1), 100))]


@router.get("/runs/{run_id}")
def get_run(run_id: str, after: int = 0):
    return _view(_run_or_404(run_id), after)


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str):
    run = _run_or_404(run_id)
    run_id = run["id"]
    if run["status"] in ("draft", "queued", "question"):
        store.update(run_id, status="stopped", message="Stopped before it started" if run["status"] != "question"
                     else "Stopped by you.", finished_at=time.time())
    elif run["status"] in ("thinking", "browsing", "fixing"):
        store.update(run_id, stop_requested=1, message="Stopping…")
    elif run["status"] == "collecting" and run.get("job_id"):
        jobs.stop(run["job_id"])
    return _view(store.get(run_id))


class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=500)


@router.post("/runs/{run_id}/answer")
def answer_run(run_id: str, body: AnswerIn, request: Request):
    """Your answer to the agent's question; the loop carries on from where it paused."""
    run = _run_or_404(run_id)
    if run["status"] != "question":
        raise HTTPException(409, "This run isn't waiting for an answer.")
    react.resume(run["id"], " ".join(body.answer.split()), str(request.base_url))
    audit.log("agent_answered", run_id=run["id"])
    return _view(store.get(run["id"]))


@router.post("/runs/{run_id}/continue")
def continue_run(run_id: str, request: Request):
    """Carry on a run that failed, hit a limit, was stopped or needed you, from where it stopped: its
    collection job resumes from its last page, or the agent's loop carries on with what it already found."""
    _enabled()
    run = _run_or_404(run_id)
    if run["status"] not in react.CONTINUABLE:
        raise HTTPException(409, "Only a run that failed, hit a limit, was stopped or needs you can continue.")
    if not run.get("job_id") and not config.agent_key():
        raise HTTPException(400, f"Agent mode needs an AI key for {config.AGENT_PROVIDER} in .env.")
    how = react.continue_run(run["id"], str(request.base_url))
    if not how:
        raise HTTPException(409, "This run can't continue.")
    audit.log("agent_continued", run_id=run["id"], how=how)
    return _view(store.get(run["id"]))


class TitleIn(BaseModel):
    title: str = Field(default="", max_length=200)


@router.put("/runs/{run_id}/title")
def rename_run(run_id: str, body: TitleIn):
    """Give a run your own name in the history; an empty name goes back to the plan's summary."""
    run = _run_or_404(run_id)
    title = " ".join(body.title.split())
    store.update(run["id"], title=title)
    return {"title": title}


@router.get("/memory")
def site_memory(site: str):
    """What worked on a site in earlier runs: the hint the agent gets when it starts there."""
    if not guard.site_domain(site):
        raise HTTPException(400, "Give a site.")
    return {"site": guard.site_domain(site), "entries": react_memory.recall(site)}


@router.delete("/memory")
def forget_site_memory(site: str):
    """Forget what worked on a site; the next run there starts without the hint."""
    if not guard.site_domain(site):
        raise HTTPException(400, "Give a site.")
    react_memory.forget(site)
    audit.log("agent_memory_forgotten", domain=guard.site_domain(site))
    return {"ok": True}


@router.delete("/runs/{run_id}")
def delete_run(run_id: str):
    run = _run_or_404(run_id)
    run_id = run["id"]
    if run["status"] in store.ACTIVE:
        raise HTTPException(409, "Stop the run before deleting it.")
    store.delete(run_id)
    audit.log("agent_run_deleted", run_id=run_id, run=run["uid"], domain=run["site"])
    shots = config.AGENT_SHOTS_DIR / str(run_id)
    if shots.exists():
        for f in shots.glob("*.jpg"):
            f.unlink()
        shots.rmdir()
    return {"ok": True}


@router.get("/runs/{run_id}/shots/{n}.jpg")
def step_shot(run_id: str, n: int):
    f = config.AGENT_SHOTS_DIR / str(_run_or_404(run_id)["id"]) / f"{int(n)}.jpg"
    if not f.exists():
        raise HTTPException(404, "No screenshot")
    return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


# ---- worker -----------------------------------------------------------------------------

def worker_auth(x_agent_token: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_agent_token, runner.TOKEN):
        raise HTTPException(403, "Not the agent worker")


worker = APIRouter(prefix="/api/agent/worker", dependencies=[Depends(worker_auth)])


def _worker_job(run: dict, start_url: str) -> dict:
    session = sessions.for_host(fetcher.host_of(start_url))
    use = fetcher.site_settings(fetcher.domain_of(start_url)).get("use_session", True)
    return {"id": run["id"], "start_url": start_url, "site": run["site"], "limits": run["limits"],
            "allowed_domains": [run["site"], f"*.{run['site']}", fetcher.domain_of(start_url),
                                f"*.{fetcher.domain_of(start_url)}"],
            "storage_state": session["state"] if session and use else None}


KEEP_BROWSER_S = 30 * 60        # a run that can Continue keeps its window this long, to carry on in place


def _close_browser(run: dict | None) -> bool:
    """A job's browser closes when the job is done, or a while after it stopped in a way it can continue from."""
    if not run or run["status"] == "done":
        return True
    if run["status"] not in store.FINISHED:
        return False
    return time.time() - float(run.get("finished_at") or 0) > KEEP_BROWSER_S


@worker.get("/next")
def next_run(open: str = ""):
    """The worker's next job: the oldest browse sub-task of a ReAct run, or none. `open` lists the runs whose
    browser the worker keeps open; "close" names those whose job has finished (each job keeps one browser
    window from its first sub-task until it ends)."""
    runner.seen()
    ids = [int(x) for x in open.split(",") if x.strip().isdigit()]
    close = [i for i in ids if _close_browser(store.get(i))]
    task = store.claim_browse_task()
    if not task:
        return {"close": close}
    run = store.get(task["run_id"])
    if not run or run["status"] != "browsing":
        store.finish_browse_task(task["id"], "failed", message="The run was no longer waiting for the browser.")
        return {"close": close}
    return {**_worker_job(run, task["start_url"]), "kind": "browse", "task_id": task["id"], "task": task["task"],
            "limits": {**run["limits"], **task["limits"]}, "counters": task["counters_base"], "close": close}


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
    kind: str = Field(pattern="^(action|thought|observation|info|warn)$")
    text: str = Field(max_length=1000)
    url: str = ""
    detail: dict = Field(default_factory=dict)
    shot: str = ""                 # base64 JPEG thumbnail
    html: str = ""                 # current page, for human-check detection
    steps: int | None = None
    ai_calls: int | None = None
    tokens: int | None = None


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
    counters = {k: v for k, v in (("steps", body.steps), ("ai_calls", body.ai_calls), ("tokens", body.tokens)) if v is not None}
    if counters:
        store.update(run_id, **counters)
    if body.url:
        store.update(run_id, final_url=body.url[:1000], message=body.text[:200] if body.kind == "action" else run["message"])
    run = store.get(run_id)
    if run["stop_requested"]:
        return {"stop": True, "reason": "stopped"}
    if run["status"] == "browsing":
        task = store.running_browse_task(run_id)
        if not task or task["stop_requested"]:
            return {"stop": True, "reason": "stopped", "message": "The browser sub-task took too long."}
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
    """The browser is on the page its sub-task was for: the page goes back to the ReAct loop."""
    run = _run_or_404(run_id)
    if run["status"] != "browsing":
        raise HTTPException(409, "This run isn't waiting for the browser.")
    ok, why = guard.check_url(body.url, run["site"])
    if not ok:
        return {"ok": False, "reason": why}
    kind = blockdetect.detect(body.html, 200, body.url)
    if kind:
        return {"ok": False, "reason": f"That page is a {blockdetect.describe(kind)}, not the results."}
    task = store.running_browse_task(run_id)
    if not task:
        return {"ok": False, "reason": "The browser sub-task has already ended."}
    react_tools.browse_html[task["id"]] = body.html
    store.finish_browse_task(task["id"], "done", result_url=body.url, note=logsafe.redact(body.note))
    return {"ok": True}


class FinishIn(BaseModel):
    status: str = Field(pattern="^(stopped|needs_you|limit|error)$")
    message: str = Field(default="", max_length=1000)
    steps: int | None = None
    ai_calls: int | None = None
    tokens: int | None = None


@worker.post("/runs/{run_id}/finish")
def finish(run_id: int, body: FinishIn):
    run = _run_or_404(run_id)
    msg = logsafe.redact(body.message) or {"stopped": "Stopped by you.", "limit": "Reached a limit.",
                                           "needs_you": "The site asked for a human check.",
                                           "error": "The agent could not reach the results."}[body.status]
    fields = {k: v for k, v in (("steps", body.steps), ("ai_calls", body.ai_calls), ("tokens", body.tokens)) if v is not None}
    if run["status"] != "browsing":
        return {"ok": True}           # the loop already moved on (timeout, stop)
    # The sub-task ended without reaching its page: the loop decides what next (a human check ends the run).
    task = store.running_browse_task(run_id)
    if task:
        store.finish_browse_task(task["id"], "failed" if body.status == "error" else body.status, message=msg)
    if fields:
        store.update(run_id, **fields)
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
    store.add_step(run_id, "info", f"Re-recording the steps for “{s['name']}”: the agent finds the results page "
                                   "again and the new steps replace the old ones.")
    react.start(run_id, str(request.base_url))
    audit.log("agent_rerecord", run_id=run_id, scrape_id=scrape_id, domain=guard.site_domain(plan["start_url"]))
    return _view(store.get(run_id))
