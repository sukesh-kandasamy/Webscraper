"""The agent reached the results list: hand the page to the normal engine.

1. The recipe builder reads the rendered page with the plan's request (columns, meanings, types).
2. If opening the final URL again shows the same results, the filters live in the address: the scrape is
   that URL and runs like any Guided scrape (pages, item pages, typing, quality report).
   Otherwise the filters exist only inside the page: the agent's page is collected as a one-page run
   (recorded routes that replay the clicks come in phase 2).
3. Item pages are switched on when the plan asks for them, or when requested columns aren't on the list.
4. A saved scrape (mode "agent") and its run are created; the run finishes like any other.
"""
from .. import config, db
from ..extraction import details, extractor, recipe_builder
from ..fetching import fetcher
from ..runs import jobs
from ..security import audit, sessions
from . import guard, routes, store, supervisor


def _log(run_id: int, kind: str, text: str, **detail) -> None:
    store.add_step(run_id, kind, text, detail=detail or None)


def _same_results(a: list[dict], b: list[dict]) -> bool:
    """Do two extractions show the same items? About as many items, and at least half of the agent's
    first items among them (an unfiltered page contains the filtered items too, but many more)."""
    if not a or not b:
        return False
    if abs(len(b) - len(a)) > max(2, len(a) * 0.2):
        return False
    sig = lambda r: tuple(str(v) for v in list(r.values())[:2])
    first = {sig(r) for r in a[:10]}
    return len(first & {sig(r) for r in b}) >= max(1, len(first) // 2)


def _handoff(run_id: int, url: str, html: str, note: str) -> None:
    run = store.get(run_id)
    plan = run["plan"]
    request = plan["request"]
    ok, why = guard.check_url(url, run["site"])
    if not ok:
        raise RuntimeError(f"The results page can't be used: {why}")
    store.update(run_id, status="collecting", final_url=url, message="Setting up the columns on the results page")
    note = note.strip().rstrip(".")
    _log(run_id, "info", "Reached the results list" + (f": {note}" if note else "") + ". Setting up the columns…", url=url)
    session = sessions.for_host(fetcher.host_of(url))
    logged_in = bool(session and session.get("kind") == sessions.KIND_LOGIN)

    try:
        built = recipe_builder.build_recipe(html, url, [], rendered=True, redact=logged_in, request=request)
    except recipe_builder.RecipeBuildError as e:
        if "rate-limited" in str(e):
            raise RuntimeError(f"The agent reached the results, but {e}") from e
        raise RuntimeError(f"No list of items could be read on the page the agent reached ({e})") from e
    recipe = built["recipe"]
    agent_rows = built["rows"]
    if not agent_rows:
        raise RuntimeError("The columns the AI set up find no items on the page the agent reached, so nothing "
                           "was collected. Try again, or set it up in Guided mode from that page: " + url)
    audit.log("recipe_built", domain=fetcher.domain_of(url), provider=config.AI_PROVIDER, rows=len(agent_rows),
              by="agent", run_id=run_id)
    _log(run_id, "info", f"Columns found: {', '.join(f['name'] for f in recipe['fields'])} "
                         f"({len(agent_rows)} items on the page).")

    # Does the address alone bring back these results?
    by_url = False
    try:
        page = fetcher.fetch_smart(url, recipe)
        by_url = _same_results(agent_rows, extractor.extract(page.html, recipe, page.final_url))
    except (fetcher.NeedsHuman, fetcher.BlockedError, fetcher.BudgetExceeded):
        raise
    except fetcher.FetchError:
        by_url = False
    route = routes.from_steps(store.steps(run_id))
    replay = False
    if by_url:
        _log(run_id, "info", "The filters are in the page address, so every page can be collected, and "
                             "running it again needs no AI.")
    elif route:
        # The filters exist only inside the page: check that the recorded steps lead to the same results
        # without the AI before relying on them.
        _log(run_id, "info", f"The filters exist only inside the page. Checking that the {len(route)} recorded "
                             "steps reach the same results without the AI…")
        store.update(run_id, message="Checking the recorded steps")
        try:
            pages = routes.replay(run["start_url"], route, recipe, 1, run["site"])
            replay = bool(pages) and _same_results(agent_rows, extractor.extract(pages[0].html, recipe, pages[0].final_url))
            why = "" if replay else "the replayed page shows different items"
        except (routes.RouteBroken, fetcher.FetchError) as e:
            why = str(e)
        _log(run_id, "info" if replay else "warn",
             f"Recorded route works: {len(route)} steps, replayed without AI on every run." if replay else
             f"The recorded steps couldn't be replayed ({why[:200]}), so only the page the agent reached is "
             "collected; running it again will need the agent (Re-record).",
             route=[routes.describe(st) for st in route])
    else:
        _log(run_id, "warn", "The filters exist only inside the page and no steps were recorded, so only the "
                             "page the agent reached is collected.")

    # Item pages, when the plan asks for them or requested columns aren't on the list.
    absent = built.get("absent") or []
    # "auto": the check-and-fix step turns them on if the quality report shows the columns missing.
    if plan.get("item_pages") == "yes" or (plan.get("item_pages") == "auto" and absent
                                           and not plan.get("auto_fix", True)):
        wanted = [c for c in request["columns"] if c["name"] in absent] or \
            [c for c in request["columns"] if c["name"] not in {f["name"] for f in recipe["fields"]}]
        store.update(run_id, message="Opening an item page for more details")
        try:
            got = details.add_item_pages(recipe, agent_rows, wanted, auto=not wanted)
            recipe = got["recipe"]
            _log(run_id, "info", "Item pages on: " + ", ".join(f["name"] for f in recipe["detail"]["fields"])
                 + (f" (not found: {', '.join(got['missing'])})" if got["missing"] else ""), url=got["link"])
        except (details.DetailError, fetcher.FetchError, recipe_builder.RecipeBuildError) as e:
            _log(run_id, "warn", f"Item pages could not be set up: {e}")

    name = (request.get("summary") or run["goal"])[:110]
    pages = int(plan.get("pages") or 3) if (by_url or replay) else 1
    # page_only: neither the address nor the recorded steps bring this page back, so later fixes can't
    # collect it again (or more pages of it) without the agent.
    recipe = {**recipe, "agent": {"goal": run["goal"], "plan": plan, "run_id": run_id, "start_url": run["start_url"],
                                  "page_only": not (by_url or replay)}}
    if replay:
        recipe["route"] = route
    urls = [run["start_url"]] if replay else [url]
    recipe = extractor.normalize_recipe(recipe)
    target = run.get("target_scrape_id")
    if target and db.get_scrape(target):
        # Re-recording a saved scrape: same scrape, new steps and columns.
        db.update_scrape(target, urls=urls, recipe=recipe, max_pages=pages, route=route if replay else [])
        scrape_id = target
    else:
        scrape_id = db.create_scrape(name, urls, recipe, pages, built.get("page_type") or "", mode="agent",
                                     route=route if replay else [])
    store.update(run_id, scrape_id=scrape_id)
    if by_url or replay:
        if config.RESPECT_ROBOTS and not fetcher.robots_allows(urls[0]):
            raise RuntimeError("robots.txt disallows the results page")
        job_id = jobs.start(urls, recipe, pages, logged_in=logged_in, scrape_id=scrape_id)
    else:
        rendered = fetcher.Page(url, url, 200, html, False, rendered=True, logged_in=logged_in)
        job_id = jobs.collect_page([url], recipe, rendered, scrape_id, logged_in)
    store.update(run_id, job_id=job_id, message="Collecting")
    audit.log("agent_handoff", run_id=run_id, job_id=job_id, domain=fetcher.domain_of(url), by_url=by_url,
              route_steps=len(route) if replay else 0)
    _log(run_id, "info", f"Collecting{' up to ' + str(pages) + ' pages' if pages > 1 else ''}…")


def refresh(run: dict) -> dict:
    """When a collecting run's job has finished, hand it to the check-and-fix step (called when the run
    is read; the status change makes sure it happens once)."""
    if run["status"] != "collecting" or not run.get("job_id"):
        return run
    job = db.get_job(run["job_id"])
    if not job or job["status"] in ("queued", "running"):
        return run
    if store.transition(run["id"], "collecting", "fixing", message="Checking the results"):
        _log(run["id"], "info", f"Collected {job['rows']:,} rows. Checking the quality report…")
        supervisor.check(run["id"])
    return store.get(run["id"])
