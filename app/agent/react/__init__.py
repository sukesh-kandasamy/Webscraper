"""The ReAct agent: a reason-and-act loop that runs in the app process.

Each step the agent's AI sees the plan, its checklist, what it did so far and the latest page summary,
and answers with one thought and one tool call (open_page, find_links, search, check_results, next_page,
collect, finish). The app runs the tool, guarded like everything else (the planned site only, robots.txt,
no personal data, no invented addresses), and feeds the observation back. collect() hands the results
page to the normal engine (handoff.py), after which collecting -> fixing -> done runs as before.

    start(run_id, app_url)            run the loop for an approved run on a background thread
    resume(run_id, answer, app_url)   carry on after the person answered the agent's question
    renavigate(run_id, why, app_url)  find the results page again after the saved steps broke
"""
import threading

from ..llm_errors import classify  # noqa: F401  (re-exported for callers that only import react)

_threads: dict[int, threading.Thread] = {}


def start(run_id: int, app_url: str = "") -> None:
    from . import loop

    t = threading.Thread(target=loop.safe_run, args=(run_id, app_url), daemon=True, name=f"react-{run_id}")
    _threads[run_id] = t
    t.start()


def resume(run_id: int, answer: str, app_url: str = "") -> bool:
    """Take the answer now (the run is "thinking" again when this returns) and carry on in the background."""
    from . import loop

    if not loop.take_answer(run_id, answer):
        return False
    t = threading.Thread(target=loop.safe_run, args=(run_id, app_url, True), daemon=True, name=f"react-{run_id}")
    _threads[run_id] = t
    t.start()
    return True


def renavigate(run_id: int, why: str, app_url: str = "") -> None:
    """The check-and-fix step found the recorded steps broken: run the loop again with a fresh step, AI-call
    and time budget. Its scratchpad keeps what it learned; the digest says what broke."""
    import time

    from .. import store

    run = store.get(run_id)
    limits = run["limits"] or {}
    pad = run.get("scratchpad") or {}
    pad["digest"] = (pad.get("digest", "") + f" After collecting, the recorded steps stopped working ({why}); "
                     "find the results page again.").strip()
    store.update(run_id, status="queued", message="Finding the way again", scratchpad=pad, started_at=time.time(),
                 limits={**limits, "steps": int(run["steps"] or 0) + int(limits.get("steps", 40)),
                         "ai_calls": int(run["ai_calls"] or 0) + int(limits.get("ai_calls", 60))})
    start(run_id, app_url)


CONTINUABLE = ("error", "limit", "stopped", "needs_you")


def continue_run(run_id: int, app_url: str = "") -> str:
    """Carry on a run that stopped (failed, hit a limit, was stopped, or needed you) from where it stopped,
    instead of planning a new one. Returns "collecting" when its collection job resumes from its last page,
    "thinking" when the loop carries on with everything it found so far, or "" when it can't continue."""
    import time

    from ... import db
    from ...runs import jobs
    from .. import store

    run = store.get(run_id)
    if not run or run["status"] not in CONTINUABLE:
        return ""
    job = db.get_job(run["job_id"]) if run.get("job_id") else None
    unfinished = job and (job["status"] in ("needs_you", "stopped", "blocked", "error")
                          or (job["status"] == "done" and (job.get("quality") or {}).get("pages_failed")))
    if unfinished and jobs.resume(job["id"]):
        store.update(run_id, status="collecting", stop_requested=0, finished_at=None, message="Collecting (continued)")
        store.add_step(run_id, "info", f"Continuing the collection ({job['uid']}) from the page it stopped at; "
                                       f"the {job['rows']:,} rows it already has are kept.")
        return "collecting"
    limits = run["limits"] or {}
    base = limits.get("base") or {k: limits[k] for k in ("steps", "ai_calls", "minutes") if k in limits}
    pad = run.get("scratchpad") or {}
    why = (run.get("message") or run["status"]).strip().rstrip(".")[:200]
    pad["digest"] = (pad.get("digest", "") + f" The run stopped ({why}). The person asked to continue: carry on "
                     "from where it stopped with what you found, don't start over.").strip()
    store.update(run_id, status="queued", stop_requested=0, finished_at=None, message="Continuing", scratchpad=pad,
                 started_at=time.time(),
                 limits={**limits, "base": base,
                         "steps": int(run["steps"] or 0) + int(base.get("steps", 40)),
                         "ai_calls": int(run["ai_calls"] or 0) + int(base.get("ai_calls", 60)),
                         "minutes": float(base.get("minutes", 15))})
    store.add_step(run_id, "info", f"Continuing from step {run['steps']}: the agent keeps its checklist, the pages "
                                   "it found and its notes, with a fresh step and time budget.")
    start(run_id, app_url)
    return "thinking"


def running(run_id: int) -> bool:
    t = _threads.get(run_id)
    return bool(t and t.is_alive())
