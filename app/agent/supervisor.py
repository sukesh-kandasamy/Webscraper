"""Check and fix: after the engine collects an agent run, read its quality report and fix what it can.

    quality signal                                         fix
    a requested column is missing or mostly empty      ->  turn on item pages for it
    coverage < 90% and the run stopped at its page cap ->  collect more pages (up to 50)
    one page only, but the site lists many more items  ->  ask the AI for the next-page control
    the recorded route stopped working                 ->  the agent finds the way again (re-records it)

At most MAX_ROUNDS rounds per run and two fixes per round; AI-backed fixes count against the run's AI
call limit. Each round updates the saved scrape and collects again; pages already read come from the
cache, so only the new work (item pages, extra pages) reaches the site. Every decision is written to
the run's timeline with its reason. You can switch it off in the plan ("auto_fix": false).
"""
import math
import threading
import time

from .. import db
from ..extraction import details, extractor, recipe_builder
from ..fetching import fetcher
from ..runs import jobs
from ..security import audit, logsafe
from . import runner, store

MAX_ROUNDS = 2
WEAK_FILL = 0.5
LOW_COVERAGE = 0.9
MAX_PAGES = 50


def _fill(q: dict, name: str) -> float:
    col = (q.get("columns") or {}).get(name)
    return col.get("fill", 0.0) if col else 0.0


def decide(run: dict, job: dict, recipe: dict) -> list[dict]:
    """The fixes worth trying for this run's latest job (pure: no side effects)."""
    q = job.get("quality") or {}
    plan = run["plan"]
    tried = {f["kind"] for f in run.get("fixes", [])}
    fixes: list[dict] = []

    if q.get("route_broken") and "renavigate" not in tried:
        rb = q["route_broken"]
        return [{"kind": "renavigate", "why": f"the recorded steps stopped working at step {rb.get('step')} "
                                               f"({rb.get('reason', '')})"}]
    if job["status"] != "done":
        return []

    # Requested columns that came out missing or mostly empty: item pages usually have them.
    names = {f["name"] for f in recipe["fields"]}
    weak = [c for c in plan["request"]["columns"]
            if c.get("required", True) and not c.get("auto_added") and _fill(q, c["name"]) < WEAK_FILL]
    if weak and plan.get("item_pages") != "no" and not recipe.get("detail") and "item_pages" not in tried \
            and jobs.url_field(recipe) and job["rows"]:
        why = ", ".join(f"{c['name']} " + ("isn't on the list page" if c["name"] not in names
                                            else f"is filled in only {round(_fill(q, c['name']) * 100)}% of rows")
                        for c in weak)
        fixes.append({"kind": "item_pages", "columns": weak, "why": why})

    cov, expected = q.get("coverage"), q.get("expected_total")
    seen, pages = q.get("items_seen", 0), job["pages_done"]
    if cov is not None and cov < LOW_COVERAGE and expected:
        if pages >= job["max_pages"] and job["max_pages"] < MAX_PAGES and "more_pages" not in tried:
            per_page = max(1, seen / max(pages, 1))
            need = min(MAX_PAGES, max(job["max_pages"] * 2, math.ceil(expected / per_page) + 1))
            fixes.append({"kind": "more_pages", "pages": need,
                          "why": f"only {round(cov * 100)}% of the ~{expected:,} items the site lists were "
                                 f"collected, and the run stopped at its limit of {job['max_pages']} pages"})
        elif pages <= 1 and job["max_pages"] > 1 and "pagination" not in tried and not recipe.get("route") \
                and recipe.get("source") != "json":
            fixes.append({"kind": "pagination",
                          "why": f"only one page was read, but the site lists ~{expected:,} items"})
    return fixes[:2]


# ---- applying fixes ------------------------------------------------------------------------

class _Fixed(Exception):
    pass


def _log(run_id: int, kind: str, text: str, **detail) -> None:
    store.add_step(run_id, kind, text, detail=detail or None)


def _ai_left(run: dict) -> int:
    return int(run["limits"].get("ai_calls", 60)) - int(run["ai_calls"])


def _count_ai(run_id: int, n: int = 1) -> None:
    run = store.get(run_id)
    store.update(run_id, ai_calls=run["ai_calls"] + n)


def repair_pagination(url: str, recipe: dict) -> dict:
    """Ask the AI for the next-page control of the listing page (one small call)."""
    page = fetcher.fetch_smart(url, recipe)
    hints = recipe_builder._pagination_hints(page.html)
    if not hints:
        raise RuntimeError("the page shows no next-page link or 'load more' button")
    schema = recipe_builder._schema([], True)["properties"]["pagination"]
    raw = recipe_builder.ask_json(
        "You fix the pagination of a web scraper recipe. Choose the next-page control from the candidates: "
        "next_link (selector that matches ONLY the next-page link), url_param (the page parameter name), "
        "click (selector of a 'load more' button) or none.",
        f"Page: {recipe_builder.short_url(page.final_url)}\nItems: {recipe['item_selector']}\n"
        "Candidate controls:\n" + "\n".join(hints), {**schema, "additionalProperties": False}, "pagination")
    fixed = extractor.normalize_recipe({**recipe, "pagination": raw})
    fixed = recipe_builder.check_pagination(page.html, page.final_url, fixed)
    if fixed["pagination"]["type"] in ("next_link", "url_param") and \
            not extractor.next_page_url(page.html, fixed, page.final_url, 0):
        raise RuntimeError("the suggested next-page control doesn't lead to another page")
    if fixed["pagination"]["type"] == "none":
        raise RuntimeError("no usable next-page control was found")
    return fixed


def _apply(run: dict, job: dict, scrape: dict, fix: dict) -> tuple[dict, int, str]:
    """(recipe, max_pages, note) after one fix. Raises with a reason when it can't be done."""
    recipe, pages = dict(scrape["recipe"]), scrape["max_pages"]
    kind = fix["kind"]
    if kind == "item_pages":
        if _ai_left(run) < 2:
            raise RuntimeError("the run's AI-call limit is reached")
        rows = db.get_records(job["id"])
        got = details.add_item_pages(recipe, rows, fix["columns"], auto=False)
        _count_ai(run["id"], 1)
        cols = [f["name"] for f in got["recipe"]["detail"]["fields"]]
        note = f"item pages on for {', '.join(cols)}" + \
            (f" (not on the first item page either: {', '.join(got['missing'])})" if got["missing"] else "")
        return got["recipe"], pages, note
    if kind == "more_pages":
        return recipe, fix["pages"], f"collecting up to {fix['pages']} pages"
    if kind == "pagination":
        if _ai_left(run) < 1:
            raise RuntimeError("the run's AI-call limit is reached")
        fixed = repair_pagination(scrape["urls"][0], recipe)
        _count_ai(run["id"], 1)
        return fixed, max(pages, int(run["plan"].get("pages") or 3), 5), \
            f"next-page control found ({fixed['pagination']['type']}: {fixed['pagination']['selector'] or fixed['pagination']['param']})"
    raise RuntimeError(f"unknown fix {kind}")


def check(run_id: int) -> None:
    threading.Thread(target=_safe_check, args=(run_id,), daemon=True, name=f"check-{run_id}").start()


def _safe_check(run_id: int) -> None:
    try:
        _check(run_id)
    except Exception as e:
        msg = logsafe.redact(f"{type(e).__name__}: {e}")[:300]
        _log(run_id, "warn", f"Checking the results failed: {msg}")
        finish(store.get(run_id))


def _check(run_id: int) -> None:
    run = store.get(run_id)
    job = db.get_job(run["job_id"])
    scrape = db.get_scrape(run["scrape_id"]) if run.get("scrape_id") else None
    if not job or not scrape:
        return finish(run)
    if not run["plan"].get("auto_fix", True):
        return finish(run)
    if run["stop_requested"]:
        return finish(run)
    fixes = decide(run, job, scrape["recipe"])
    if not fixes:
        _log(run_id, "info", "Checked the results: nothing to fix." if job["status"] == "done"
             else "Checked the results: nothing the agent can fix.")
        return finish(run)
    if run.get("fix_rounds", 0) >= MAX_ROUNDS:
        _log(run_id, "warn", f"Would fix: {'; '.join(f['why'] for f in fixes)}, but the agent already made "
                             f"{MAX_ROUNDS} rounds of fixes.")
        return finish(run)

    applied = list(run.get("fixes", []))
    if fixes[0]["kind"] == "renavigate":
        fix = fixes[0]
        _log(run_id, "warn", f"Fixing: {fix['why']}. The agent finds the way again…")
        applied.append({"kind": "renavigate", "why": fix["why"], "result": "re-navigating"})
        store.update(run_id, target_scrape_id=scrape["id"], fixes=applied, fix_rounds=run.get("fix_rounds", 0) + 1)
        audit.log("agent_fix", run_id=run_id, kind="renavigate")
        from . import react
        react.renavigate(run_id, fix["why"], runner.last_app_url)
        return

    recipe, pages, changed = scrape["recipe"], scrape["max_pages"], False
    for fix in fixes:
        _log(run_id, "info", f"Fixing: {fix['why']}…")
        store.update(run_id, message=f"Fixing: {fix['why']}")
        try:
            new_recipe, new_pages, note = _apply(store.get(run_id), job, {**scrape, "recipe": recipe,
                                                                          "max_pages": pages}, fix)
            recipe, pages, changed = new_recipe, new_pages, True
            applied.append({"kind": fix["kind"], "why": fix["why"], "result": note})
            _log(run_id, "info", f"Fix applied: {note}.")
            audit.log("agent_fix", run_id=run_id, kind=fix["kind"])
        except Exception as e:
            reason = logsafe.redact(str(e))[:300]
            applied.append({"kind": fix["kind"], "why": fix["why"], "result": f"not possible: {reason}"})
            _log(run_id, "warn", f"Couldn't fix it: {reason}.")
    store.update(run_id, fixes=applied, fix_rounds=run.get("fix_rounds", 0) + 1)
    if not changed:
        return finish(store.get(run_id))
    recipe = extractor.normalize_recipe(recipe)
    db.update_scrape(scrape["id"], recipe=recipe, max_pages=pages)
    new_job = jobs.start(scrape["urls"], recipe, pages, logged_in=job.get("logged_in", False),
                         scrape_id=scrape["id"])
    store.update(run_id, job_id=new_job, status="collecting", message="Collecting again with the fixes")
    _log(run_id, "info", "Collecting again with the fixes (pages already read come from the cache)…",
         previous_job=job["id"], job=new_job)


def finish(run: dict) -> None:
    """Write the run's summary from its latest job and the fixes made."""
    job = db.get_job(run["job_id"]) if run.get("job_id") else None
    if not job:
        store.update(run["id"], status="error", finished_at=time.time())
        return
    q = job.get("quality") or {}
    bits = [f"Collected {job['rows']:,} rows from {job['pages_done']} page(s)"]
    if q.get("coverage") is not None:
        bits.append(f"{round(q['coverage'] * 100)}% of the ~{q['expected_total']:,} the site lists")
    if q.get("excluded"):
        bits.append(f"{q['excluded']:,} didn't match your conditions")
    if (q.get("details") or {}).get("done"):
        bits.append(f"{q['details']['done']} item pages read")
    fixed = [f for f in run.get("fixes", []) if not str(f.get("result", "")).startswith("not possible")]
    if fixed:
        bits.append(f"{len(fixed)} fix{'es' if len(fixed) > 1 else ''} made")
    bits.append(f"the agent used {run['steps']} steps and {run['ai_calls']} AI calls")
    status = "done" if job["status"] == "done" else ("needs_you" if job["status"] == "needs_you" else "error")
    summary = "; ".join(bits) + "."
    if status != "done":
        summary = f"Collecting ended as '{job['status']}': {job['message']}"
    store.update(run["id"], status=status, summary=summary, message=summary, finished_at=time.time())
    _log(run["id"], "info" if status == "done" else "warn", summary)
    if status == "done" and run.get("engine") == "react":
        try:
            from .react import memory
            memory.remember(store.get(run["id"]))
        except Exception as e:  # noqa: BLE001 - memory is a hint; never fail a finished run over it
            _log(run["id"], "warn", f"Could not remember this site's route: {type(e).__name__}")
