"""The ReAct agent, phase 3: site memory (written on done, read at the start, forgettable) and re-planning
when the agent is stuck. No browser, no network, no real AI. (The browser's widget skills run inside the
agent worker and were checked against a real browser; see docs/plans/08-react-agent.md.)"""
import time

from app.agent import store
from app.agent.react import brain, loop, memory, tools
from app.extraction import recipe_builder
from tests.test_react import (CAT, HOME, PAGE2, client, ctx_for, make_run, script, site,  # noqa: F401
                              test_hosts, wait_done)


def _books_run(monkeypatch) -> dict:
    run_id = make_run()
    monkeypatch.setattr(brain, "decide", script(
        brain.Step("Open the start page.", "open_page", {"url": HOME}),
        brain.Step("Find Mystery.", "find_links", {"url": HOME, "pattern": "mystery"}),
        brain.Step("Open it.", "open_page", {"url": CAT}, ["must_1"]),
        brain.Step("Collect.", "collect", {"url": CAT, "pages": 2}),
    ))
    loop.safe_run(run_id)
    v = wait_done(run_id)
    assert v["status"] == "done", v["message"]
    for _ in range(100):                       # written right after the run turns done
        if memory.recall(HOME):
            break
        time.sleep(0.05)
    return v


def test_done_run_is_remembered_and_used_next_time(site, monkeypatch):
    _books_run(monkeypatch)
    [entry] = memory.recall(HOME)
    assert entry["results_url"] == CAT and entry["goal"].startswith("Mystery books")
    assert entry["how"][0].startswith("open_page https://books.test/") and entry["how"][-1].startswith("collect")
    assert entry["list_selector"] == "li.c" and entry["needs_browser"] is False

    run_id = make_run()
    ctx = ctx_for(run_id)
    assert CAT in ctx.facts["seen_urls"]                  # the remembered results page may be opened directly
    ok, why, url = tools.allow_url(CAT, ctx)
    assert ok and url == CAT, why
    text = brain.prompt(ctx)
    assert "WHAT WORKED ON THIS SITE BEFORE" in text and f"results page: {CAT}" in text

    monkeypatch.setattr(brain, "decide", script(
        brain.Step("Memory says the Mystery page; open it.", "open_page", {"url": CAT}, ["must_1"]),
        brain.Step("Collect.", "collect", {"url": CAT}),
    ))
    store.update(run_id, status="queued")
    loop.safe_run(run_id)
    v = wait_done(run_id)
    assert v["status"] == "done" and v["steps"] == 2
    assert any(s["text"].startswith("Remembers what worked on this site in 1 earlier run") for s in v["steps_list"])
    for _ in range(100):
        if len(memory.recall(HOME)) == 1 and memory.recall(HOME)[0]["steps"] == 2:
            break
        time.sleep(0.05)
    assert len(memory.recall(HOME)) == 1 and memory.recall(HOME)[0]["steps"] == 2   # same goal: replaced, not added


def test_memory_shows_on_the_plan_and_can_be_forgotten(client, monkeypatch):
    _books_run(monkeypatch)
    draft = store.create("Mystery books again", "books.test", HOME, store.get(1)["plan"], {"steps": 20, "minutes": 5, "ai_calls": 30})
    view = client.get(f"/api/agent/runs/{store.get(draft)['uid']}").json()
    assert view["memory"][0]["goal"].startswith("Mystery books") and "results_url" not in view["memory"][0]
    assert client.get("/api/agent/memory", params={"site": "books.test"}).json()["entries"][0]["results_url"] == CAT
    assert client.delete("/api/agent/memory", params={"site": "books.test"}).json() == {"ok": True}
    assert memory.recall(HOME) == [] and client.get(f"/api/agent/runs/{store.get(draft)['uid']}").json()["memory"] == []


def test_stuck_agent_replans_at_most_twice(site, monkeypatch):
    run_id = make_run(limits={"steps": 14, "minutes": 5, "ai_calls": 40})
    invented = [brain.Step("Guess an address.", "open_page", {"url": f"https://books.test/made-up-{i}"}) for i in range(9)]
    monkeypatch.setattr(brain, "decide", script(
        brain.Step("Open.", "open_page", {"url": HOME}), *invented,
        brain.Step("Follow the new route.", "open_page", {"url": CAT}, ["must_1"]),
        brain.Step("Collect.", "collect", {"url": CAT}),
    ))
    asks = []

    def fake_replan(system, messages, schema, name, provider=None, model=None, timeout=None):
        asks.append(messages[0]["content"])
        assert name == "agent_replan" and timeout == brain.AI_TIMEOUT_S and "YOU LOOK STUCK: the last 3 actions were refused" in messages[0]["content"]
        return {"diagnosis": "invented addresses are refused", "route": ["find_links 'mystery' on the home page", "collect it"]}, 300
    monkeypatch.setattr(recipe_builder, "ask_structured", fake_replan)
    loop.safe_run(run_id)
    v = wait_done(run_id)
    replans = [s for s in v["steps_list"] if s["text"].startswith("Re-planned because")]
    assert len(asks) == 2 and len(replans) == 2                       # 9 refusals in a row, but only two re-plans
    assert replans[0]["detail"]["replan"]["route"][0] == "find_links 'mystery' on the home page"
    assert v["status"] == "done", v["message"]
    pad = store.get(run_id)["scratchpad"]
    assert [r["at"] for r in pad["replans"]] == [4, 7]
    ctx = loop.Context(store.get(run_id))
    assert "YOUR NEW ROUTE (re-planned at step 7: invented addresses are refused)" in brain.contract(ctx)


def test_replan_when_most_steps_are_used(site, monkeypatch):
    ctx = ctx_for(make_run(limits={"steps": 10, "minutes": 5, "ai_calls": 40}))
    ctx.steps = 5
    assert brain.stuck(ctx) == ""
    ctx.steps = 6
    assert brain.stuck(ctx) == "6 of 10 steps are used and nothing is collected yet"
    monkeypatch.setattr(recipe_builder, "ask_structured", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    r = brain.replan(ctx, brain.stuck(ctx))
    assert r["route"] == [] and brain.stuck(ctx) == ""                 # a failed re-plan isn't retried at once
    assert "SUGGESTED ROUTE" in brain.contract(ctx) and store.get(ctx.run_id)["ai_calls"] == 1


def test_paging_url_is_unaffected_by_memory(site, monkeypatch):
    """Memory only adds the remembered results page; other addresses still need to be seen first."""
    _books_run(monkeypatch)
    ctx = ctx_for(make_run())
    ok, why, _ = tools.allow_url(PAGE2, ctx)
    assert not ok and "never showed" in why


def test_ask_structured_timeout_applies_to_that_call_only(monkeypatch):
    seen = []

    def fake_openai(messages, schema, system, name, model):
        seen.append(recipe_builder._timeout(180))
        recipe_builder._usage.last = 7
        return '{"ok": true}'
    monkeypatch.setitem(recipe_builder._ASK, "openai", fake_openai)
    assert recipe_builder.ask_structured("s", [], {}, "n", "openai", "m", timeout=75) == ({"ok": True}, 7)
    recipe_builder.ask_structured("s", [], {}, "n", "openai", "m")
    assert seen == [75, 180]


def test_memory_is_one_per_site_with_or_without_www(site):
    """Remembered under the run's www. address, forgotten by the site name the UI sends (and read back
    from the old www. key used before)."""
    from app.fetching import fetcher
    run = {"goal": "Teams", "start_url": "https://www.hockey.test/pages/forms/", "final_url": "https://www.hockey.test/pages/forms/?q=New",
           "steps": 3, "scratchpad": {"history": [{"n": 1, "tool": "search", "arg": "q=New", "ok": True}]}}
    memory.remember(run)
    assert memory.recall("hockey.test")[0]["results_url"].endswith("?q=New")
    assert memory.recall("https://www.hockey.test/") == memory.recall("hockey.test")
    memory.forget("hockey.test")
    assert memory.recall("https://www.hockey.test/pages/forms/") == []
    fetcher.update_settings("www.hockey.test", {"agent_memory": [{"goal": "old", "how": []}]})
    assert memory.recall("hockey.test")[0]["goal"] == "old"
    memory.forget("hockey.test")
    assert memory.recall("hockey.test") == []


def test_a_timed_out_brain_call_is_tried_once_more(site, monkeypatch):
    class ReadTimeout(Exception):
        pass
    ok = ({"thought": "go", "checklist_done": [], "tool": "open_page", "url": HOME, "pattern": "", "params": [],
           "fields": [], "form": 0, "pages": 0, "task": "", "question": "", "note": "", "summary": "", "outcome": "ok"}, 40)
    answers = iter([ReadTimeout("timed out"), ok])

    def ask(*a, **k):
        x = next(answers)
        if isinstance(x, Exception):
            raise x
        return x
    monkeypatch.setattr(recipe_builder, "ask_structured", ask)
    monkeypatch.setattr(brain.time, "sleep", lambda s: None)
    ctx = ctx_for(make_run())
    assert brain.decide(ctx).tool == "open_page" and not ctx.using_backup
    assert store.get(ctx.run_id)["ai_calls"] == 2


# ---- Continue: a stopped run carries on where it stopped, it doesn't start over -------------------

def test_a_stopped_run_continues_where_it_stopped(client, monkeypatch):
    from app.agent import react
    run_id = make_run(limits={"steps": 2, "minutes": 5, "ai_calls": 30})
    monkeypatch.setattr(brain, "decide", script(
        brain.Step("Open the start page.", "open_page", {"url": HOME}),
        brain.Step("Find Mystery.", "find_links", {"url": HOME, "pattern": "mystery"}),
        brain.Step("Open it.", "open_page", {"url": CAT}, ["must_1"]),         # only reached after Continue
        brain.Step("Collect.", "collect", {"url": CAT, "pages": 2}),
    ))
    loop.safe_run(run_id)
    v = wait_done(run_id)
    assert v["status"] == "limit"
    runs_before = len(store.list_runs(100))
    monkeypatch.setattr(react, "start", lambda rid, app_url="": loop.safe_run(rid, app_url))   # the loop, inline
    r = client.post(f"/api/agent/runs/{v['uid']}/continue")
    assert r.status_code == 200, r.text
    v = wait_done(run_id)
    assert v["status"] == "done", v["message"]
    hist = store.get(run_id)["scratchpad"]["history"]
    assert [h["tool"] for h in hist] == ["open_page", "find_links", "open_page", "collect"]   # kept, then carried on
    assert v["steps"] == 4 and store.get(run_id)["limits"]["base"]["steps"] == 2
    assert any(s["text"].startswith("Continuing from step 2") for s in v["steps_list"])
    assert "asked to continue" in store.get(run_id)["scratchpad"]["digest"]
    assert len(store.list_runs(100)) == runs_before                                    # the same run, not a new one


def test_continue_resumes_the_collection_job_and_refuses_finished_runs(client, monkeypatch):
    from app import db
    from app.runs import jobs
    from tests.test_react import RECIPE
    run_id = make_run()
    job_id = db.create_job([CAT], dict(RECIPE), 2)
    db.update_job(job_id, status="error", message="The connection dropped on page 2")
    store.update(run_id, status="error", job_id=job_id, message="Collecting ended as 'error'", finished_at=time.time())
    resumed = []

    def resume(j):
        resumed.append(j)
        db.update_job(j, status="queued")
        return True
    monkeypatch.setattr(jobs, "resume", resume)
    r = client.post(f"/api/agent/runs/{store.get(run_id)['uid']}/continue").json()
    assert r["status"] == "collecting" and resumed == [job_id] and r["job_id"] == job_id
    assert any(s["text"].startswith("Continuing the collection") for s in r["steps_list"])
    done = make_run()
    store.update(done, status="done")
    assert client.post(f"/api/agent/runs/{store.get(done)['uid']}/continue").status_code == 409
