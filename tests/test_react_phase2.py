"""The ReAct agent, phase 2: the browser as a tool (browse sub-tasks through the worker routes) and questions
to the person (ask_user, answer, resume). No browser, no network, no real AI."""
import threading
import time

from app import db
from app.agent import runner, store
from app.agent.react import brain, loop, tools
from app.fetching import fetcher
from tests.test_react import (CAT, CAT_HTML, HOME, client, ctx_for, make_run, script, site,  # noqa: F401
                              test_hosts, wait_done)


def _worker():
    return {"X-Agent-Token": runner.TOKEN}


def _installed(monkeypatch):
    monkeypatch.setattr(runner, "status", lambda: {"installed": True, "browser": "edge.exe", "key_set": True, "engine": "react"})
    monkeypatch.setattr(runner, "ensure", lambda url: None)


def _claim(client):
    for _ in range(200):
        job = client.get("/api/agent/worker/next", headers=_worker()).json()
        if job:
            return job
        time.sleep(0.05)
    raise AssertionError("no browse task was queued")


def test_browse_round_trip_through_the_worker_routes(client, monkeypatch):
    """The browse tool queues a sub-task, the worker claims it through /worker/next, its actions go through
    /check and /step, /arrived gives the page back, and collect uses that page."""
    _installed(monkeypatch)
    run_id = make_run()
    monkeypatch.setattr(brain, "decide", script(
        brain.Step("Open.", "open_page", {"url": HOME}),
        brain.Step("The category needs a click.", "browse", {"task": "open the Mystery category", "url": HOME}, ["must_1"]),
        brain.Step("Collect the page the browser reached.", "collect", {"url": CAT}),
    ))
    threading.Thread(target=loop.safe_run, args=(run_id, "http://localhost/"), daemon=True).start()
    job = _claim(client)
    assert job["kind"] == "browse" and job["id"] == run_id and "Your sub-task: open the Mystery category" in job["task"]
    assert job["start_url"] == HOME and job["limits"]["steps"] <= 15 and job["counters"]["steps"] == 1
    assert store.get(run_id)["status"] == "browsing"
    refused = client.post(f"/api/agent/worker/runs/{run_id}/check", headers=_worker(),
                          json={"action": "click", "element": {"tag": "button", "text": "Add to basket"}}).json()
    assert refused["ok"] is False
    step = client.post(f"/api/agent/worker/runs/{run_id}/step", headers=_worker(),
                       json={"kind": "action", "text": "Clicked Mystery", "url": CAT, "steps": 2, "ai_calls": 3,
                             "detail": {"action": "click", "params": {}, "target": {"tag": "a", "text": "Mystery"}}}).json()
    assert step == {"stop": False}
    assert client.post(f"/api/agent/worker/runs/{run_id}/arrived", headers=_worker(),
                       json={"url": CAT, "html": CAT_HTML, "note": "Opened Mystery"}).json()["ok"]
    v = wait_done(run_id)
    assert v["status"] == "done", v["message"]
    texts = [s["text"] for s in v["steps_list"]]
    assert any(t.startswith("Browser sub-task: open the Mystery category") for t in texts)
    assert any(t.startswith("The browser did it (Opened Mystery)") for t in texts)
    assert any("Refused" in t for t in texts) and "Clicked Mystery" in texts
    assert v["steps"] >= 3 and v["ai_calls"] >= 4                 # the worker's counters carried on
    assert db.get_scrape(v["scrape_id"])["mode"] == "agent"
    assert client.get("/api/agent/worker/next", headers=_worker()).json() == {}


def test_browse_failure_and_timeout_are_observations(client, monkeypatch):
    _installed(monkeypatch)
    monkeypatch.setattr(tools, "BROWSE_MAX_MINUTES", 0.02)
    monkeypatch.setattr(tools, "BROWSE_GRACE_S", 1)
    run_id = make_run(limits={"steps": 10, "minutes": 5, "ai_calls": 30})
    monkeypatch.setattr(brain, "decide", script(
        brain.Step("Open.", "open_page", {"url": HOME}),
        brain.Step("Try the browser.", "browse", {"task": "click the thing", "url": HOME}),
        brain.Step("Try again.", "browse", {"task": "click the other thing", "url": HOME}),
        brain.Step("Give up.", "finish", {"outcome": "failed", "summary": "the browser could not do it"}),
    ))
    threading.Thread(target=loop.safe_run, args=(run_id, "http://localhost/"), daemon=True).start()
    job = _claim(client)
    assert job["kind"] == "browse"
    client.post(f"/api/agent/worker/runs/{run_id}/finish", headers=_worker(), json={"status": "error", "message": "No such button"})
    v = wait_done(run_id, 30)                                     # the second task is never claimed: it times out
    obs = [s["text"] for s in v["steps_list"] if s["kind"] == "observation"]
    assert any("The browser could not do that: No such button" in t for t in obs)
    assert any("took too long" in t for t in obs)
    assert v["status"] == "error" and store.running_browse_task(run_id) is None


def test_browse_needs_the_worker_installed(site, monkeypatch):
    monkeypatch.setattr(runner, "status", lambda: {"installed": False, "browser": "", "key_set": True, "engine": "react"})
    ctx = ctx_for(make_run())
    tools.run_tool("open_page", {"url": HOME}, ctx)
    obs = tools.run_tool("browse", {"task": "click", "url": HOME}, ctx)
    assert not obs.ok and "isn't installed" in obs.text and store.get(ctx.run_id)["status"] == "thinking"


def test_ask_user_pauses_and_the_answer_resumes_the_loop(client, monkeypatch):
    run_id = make_run()
    answers = iter([
        brain.Step("Which category?", "ask_user", {"question": "Do you want Mystery or Thriller books?"}),
        brain.Step("Mystery it is.", "open_page", {"url": HOME}),
        brain.Step("Open it.", "open_page", {"url": CAT}, ["must_1"]),
        brain.Step("Collect.", "collect", {"url": CAT}),
    ])

    def decide(ctx):
        store.bump(ctx.run_id, ai_calls=1)
        step = next(answers)
        if step.tool == "open_page" and step.args["url"] == HOME:
            assert "The person answered: Mystery" in brain.prompt(ctx)
        return step
    monkeypatch.setattr(brain, "decide", decide)
    loop.safe_run(run_id)
    run = store.get(run_id)
    assert run["status"] == "question" and run["question"] == "Do you want Mystery or Thriller books?"
    uid = run["uid"]
    assert client.post(f"/api/agent/runs/{uid}/start", json={}).status_code == 409
    r = client.post(f"/api/agent/runs/{uid}/answer", json={"answer": "Mystery"})
    assert r.status_code == 200 and r.json()["status"] in ("thinking", "collecting", "fixing", "done")
    v = wait_done(run_id)
    assert v["status"] == "done" and v["answer"] == "Mystery"
    texts = [s["text"] for s in v["steps_list"]]
    assert "You answered: Mystery" in texts and "The person answered: Mystery" in texts
    assert client.post(f"/api/agent/runs/{uid}/answer", json={"answer": "again"}).status_code == 409


def test_question_survives_a_restart_and_can_be_stopped(client, monkeypatch):
    run_id = make_run()
    monkeypatch.setattr(brain, "decide", script(brain.Step("?", "ask_user", {"question": "Which city?"})))
    loop.safe_run(run_id)
    store.init()                                                  # the app restarted
    run = store.get(run_id)
    assert run["status"] == "question"
    assert client.post(f"/api/agent/runs/{run['uid']}/stop").json()["status"] == "stopped"
    other = make_run()                                            # a run busy at the restart is marked as failed
    store.transition(other, "queued", "browsing")
    store.init()
    assert store.get(other)["status"] == "error"


def test_two_questions_at_most(site):
    ctx = ctx_for(make_run())
    ctx.facts["questions"] = 2
    obs = tools.run_tool("ask_user", {"question": "Really?"}, ctx)
    assert not obs.ok and "asked twice" in obs.text and store.get(ctx.run_id)["status"] == "thinking"


def test_browsed_page_is_reused_for_an_address_with_a_fragment(site, monkeypatch):
    """The browser's rendered page answers "…/#2015" too, so check_results and collect don't fall back to
    the plain, unrendered page."""
    ctx = ctx_for(make_run())
    rendered = fetcher.Page(CAT + "#2015", CAT + "#2015", 200, CAT_HTML, False, rendered=True)
    ctx.pages[tools._norm(rendered.final_url)] = rendered

    def no_fetch(url, *a, **k):
        raise AssertionError("fetched again: " + url)
    monkeypatch.setattr(fetcher, "fetch_smart", no_fetch)
    page, bad = tools._fetch(ctx, CAT + "#2015")
    assert bad is None and page is rendered
    assert tools._fetch(ctx, CAT)[0] is rendered


class ReadTimeout(Exception):
    pass


def test_brain_timeout_switches_to_the_backup_or_ends_plainly(site, monkeypatch):
    """An AI that doesn't answer in time (after the request's own retry) is not waited on again: the backup
    AI takes over, or the run ends with a plain message."""
    from app import config
    from app.extraction import recipe_builder
    ok = ({"thought": "go", "checklist_done": [], "tool": "open_page", "url": HOME, "pattern": "", "params": [],
           "fields": [], "form": 0, "pages": 0, "task": "", "question": "", "note": "", "summary": "", "outcome": "ok"}, 50)
    calls = []

    def ask(system, messages, schema, name, provider=None, model=None):
        calls.append(model)
        if model != "backup-model":
            raise ReadTimeout("The read operation timed out")
        return ok
    monkeypatch.setattr(recipe_builder, "ask_structured", ask)
    monkeypatch.setattr(config, "agent_fallback", lambda: {"provider": "openai", "model": "backup-model"})
    ctx = ctx_for(make_run())
    assert brain.decide(ctx).tool == "open_page" and ctx.using_backup and calls[-1] == "backup-model"
    assert any("did not answer in time" in s["text"] for s in store.steps(ctx.run_id))
    monkeypatch.setattr(config, "agent_fallback", lambda: None)
    ctx = ctx_for(make_run())
    try:
        brain.decide(ctx)
        raise AssertionError("no error")
    except brain.BrainError as e:
        assert e.status == "error" and "did not answer in time (ReadTimeout)" in str(e)


def test_must_items_never_repeat_app_items():
    from app.agent import planner
    plan = planner.clean_plan({"must": ["year: 2015", "awards contains Oscar"],
                               "request": {"columns": [{"name": "title", "type": "text"}, {"name": "awards", "type": "text"}],
                                           "filters": [{"column": "awards", "op": "contains", "value": "Oscar"}]}},
                              "films", "films.test")
    assert [(c["kind"], c["text"]) for c in plan["checklist"]] == [("must", "year: 2015"), ("app", "awards contains Oscar")]
    plan = planner.clean_plan({"must": ["year: 2015", "awards: >0"],
                               "request": {"columns": [{"name": "title", "type": "text"}, {"name": "awards", "type": "number"}],
                                           "filters": [{"column": "awards", "op": "gt", "value": "0"}]}},
                              "films", "films.test")
    assert [c["text"] for c in plan["checklist"]] == ["year: 2015", "awards > 0"]
