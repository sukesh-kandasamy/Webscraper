"""The ReAct agent (app/agent/react): page summaries, guarded tools, the loop, the brain's error handling,
site suggestion and the plan's checklist. No browser, no network, no real AI."""
import time

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import config, db
from app.agent import planner, store
from app.agent.react import brain, loop, summary, tools
from app.extraction import extractor, recipe_builder
from app.fetching import fetcher
from app.security import netguard, vault

SITE = "books.test"
HOME = "https://books.test/"
CAT = "https://books.test/catalogue/category/books/mystery_3/index.html"
PAGE2 = "https://books.test/catalogue/category/books/mystery_3/page-2.html"


def books(n0: int) -> str:
    return "".join(f"<li class='c'><h3><a href='/p/{i}'>Mystery book number {i}: a long thrilling story</a></h3>"
                   f"<p class='price'>£{i}9.99</p></li>" for i in range(n0, n0 + 5))


HOME_HTML = f"""<html><head><title>All products | Books</title></head><body>
<form method="get" action="/search"><input type="search" name="q" placeholder="Search books"><button>Go</button></form>
<ul class="nav"><li><a href="/catalogue/category/books/travel_2/index.html">Travel</a></li>
<li><a href="{CAT}">Mystery</a></li><li><a href="/catalogue/category/books/romance_8/index.html">Romance</a></li></ul>
<p>1000 results · call 98765 43210 or mail help@books.test</p>
<ul>{''.join(f"<li class='c'><h3><a href='/p/{i}'>Book {i} with a long enough title</a></h3><p class='price'>£{i}.00</p></li>" for i in range(1, 21))}</ul>
<a class="next" href="/catalogue/page-2.html">next</a></body></html>"""
CAT_HTML = f"<html><head><title>Mystery | Books</title></head><body><h1>Mystery</h1><p>32 results</p><ul>{books(1)}</ul><a class='next' href='page-2.html'>next</a></body></html>"
PAGE2_HTML = f"<html><head><title>Mystery | Books</title></head><body><ul>{books(6)}</ul></body></html>"
PAGES = {HOME: HOME_HTML, CAT: CAT_HTML, PAGE2: PAGE2_HTML, "https://books.test/search?q=Sharp": CAT_HTML}
RECIPE = extractor.normalize_recipe({
    "item_selector": "li.c", "fields": [{"name": "title", "selector": "h3 a"}, {"name": "price", "selector": ".price"},
                                        {"name": "url", "selector": "h3 a", "attr": "href"}],
    "pagination": {"type": "next_link", "selector": "a.next"}})


@pytest.fixture(autouse=True)
def test_hosts(monkeypatch):
    real = netguard.check_url

    def check(url):
        from urllib.parse import urlsplit
        if (urlsplit(url).hostname or "").endswith(".test"):
            return
        return real(url)
    monkeypatch.setattr(netguard, "check_url", check)
    monkeypatch.setattr(fetcher, "robots_allows", lambda url: True)


@pytest.fixture
def site(monkeypatch):
    vault.set_key_for_tests(Fernet.generate_key())
    db.init()
    store.init()

    def fake_fetch(url, recipe=None, max_rounds=0, use_cache=True):
        if url not in PAGES:
            raise fetcher.FetchError(f"404 for {url}")
        return fetcher.Page(url, url, 200, PAGES[url], False)
    monkeypatch.setattr(fetcher, "fetch_smart", fake_fetch)
    monkeypatch.setattr(config, "AGENT_PROVIDER", "groq")
    monkeypatch.setattr(config, "AGENT_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")

    def fake_build(html, url, field_names, rendered=False, redact=False, auto=False, request=None):
        rows = extractor.extract(html, RECIPE, url)
        return {"recipe": dict(RECIPE), "rows": rows, "report": {}, "notes": "", "page_type": "ecommerce", "absent": []}
    monkeypatch.setattr(recipe_builder, "build_recipe", fake_build)
    yield
    vault.set_key_for_tests(None)


def make_run(musts=("category: Mystery",), limits=None, pages=2) -> int:
    plan = planner.clean_plan({"start_url": HOME, "navigation": ["open the Mystery category"], "must": list(musts),
                               "pages": pages, "request": {"columns": [{"name": "title", "type": "text"}, {"name": "price", "type": "money"}],
                                                           "filters": [{"column": "price", "op": "lt", "value": "40"}],
                                                           "sort": {"column": "", "desc": False}, "limit": 0, "summary": "Mystery books"}},
                              "Mystery books: title and price, under £40", SITE)
    run_id = store.create("Mystery books: title and price, under £40", SITE, HOME, plan,
                          limits or {"steps": 20, "minutes": 5, "ai_calls": 30})
    store.update(run_id, status="queued")
    return run_id


def ctx_for(run_id: int) -> loop.Context:
    store.transition(run_id, "queued", "thinking", started_at=time.time())
    return loop.Context(store.get(run_id))


def script(*steps):
    """A fake brain: answers the given steps in order."""
    it = iter(steps)

    def decide(ctx):
        store.bump(ctx.run_id, ai_calls=1, tokens=1000)
        ctx.ai_calls += 1
        return next(it)
    return decide


def wait_done(run_id, timeout=10):
    from app.agent import api
    for _ in range(int(timeout * 20)):
        v = api._view(store.get(run_id))
        if v["status"] not in store.ACTIVE:
            return v
        time.sleep(0.05)
    raise AssertionError(f"run stuck in {v['status']}: {v['message']}")


# ---- summary -------------------------------------------------------------------------------

def test_summary_fits_budget_and_masks(site):
    page = fetcher.Page(HOME, HOME, 200, HOME_HTML, False)
    text, facts = summary.page_summary(page, "Mystery books: title and price", ["category: Mystery"], ["title", "price"], 900)
    assert len(text) <= 900 and "98765" not in text and "help@books" not in text
    assert "20 items" in text and "li.c" in text and "1,000 results" in text
    assert facts["forms"][0]["method"] == "GET" and facts["forms"][0]["fields"][0]["name"] == "q"
    assert any(u == CAT for _, u in facts["links"]) and facts["next"][0][1] == "https://books.test/catalogue/page-2.html"
    full, _ = summary.page_summary(page, "Mystery books", ["category: Mystery"], ["title"], 7000)
    assert "search forms: form 0: GET /search" in full and '"Mystery" → /catalogue/category/books/mystery_3/index.html' in full


# ---- tools --------------------------------------------------------------------------------

def test_url_rule_refuses_invented_addresses(site):
    ctx = ctx_for(make_run())
    assert tools.run_tool("open_page", {"url": CAT}, ctx).ok is False            # never shown yet
    obs = tools.run_tool("open_page", {"url": HOME}, ctx)
    assert obs.ok and "lists:" in obs.text
    assert tools.run_tool("open_page", {"url": CAT}, ctx).ok                     # a link the summary showed
    assert not tools.run_tool("open_page", {"url": "https://books.test/admin"}, ctx).ok
    # editing a parameter the site never used on that address is refused; the search form's field is fine
    bad = tools.run_tool("open_page", {"url": HOME, "params": [{"name": "sort", "value": "price"}]}, ctx)
    assert not bad.ok and "never used" in bad.text
    ctx.facts["seen_urls"].append("https://books.test/search?q=Sharp")
    ok = tools.run_tool("open_page", {"url": "https://books.test/search", "params": [{"name": "q", "value": "Sharp"}]}, ctx)
    assert ok.ok
    # a page with no items is remembered and refused on reopen
    PAGES["https://books.test/empty"] = "<html><body><p>No results found</p></body></html>"
    ctx.facts["seen_urls"].append("https://books.test/empty")
    empty = tools.run_tool("open_page", {"url": "https://books.test/empty"}, ctx)
    assert empty.ok and "no results" in empty.text.lower()
    ctx.facts["calls"].clear()
    again = tools.run_tool("open_page", {"url": "https://books.test/empty"}, ctx)
    assert not again.ok and "showed no items" in again.text
    assert not tools.run_tool("open_page", {"url": "https://evil.test/"}, ctx).ok


def test_search_builds_get_url_and_guards_fields(site):
    ctx = ctx_for(make_run())
    tools.run_tool("open_page", {"url": HOME}, ctx)
    obs = tools.run_tool("search", {"url": HOME, "form": 0, "fields": [{"name": "q", "value": "Sharp"}]}, ctx)
    assert obs.ok and obs.text.startswith("Searched with q=Sharp") and "https://books.test/search?q=Sharp" in ctx.facts["seen_urls"]
    assert not tools.run_tool("search", {"url": HOME, "form": 0, "fields": [{"name": "email", "value": "a@b"}]}, ctx).ok
    PAGES["https://books.test/filter"] = ("<html><body><h1>Filter books</h1><p>Pick a brand to filter the catalogue of books.</p>"
                                          "<form method='post' action='/filter'><input name='brand'></form></body></html>")
    ctx.facts["seen_urls"].append("https://books.test/filter")
    assert "needs the browser" in tools.run_tool("search", {"url": "https://books.test/filter", "form": 0, "fields": []}, ctx).text


def test_check_results_and_next_page(site):
    ctx = ctx_for(make_run())
    tools.run_tool("open_page", {"url": HOME}, ctx)
    tools.run_tool("open_page", {"url": CAT}, ctx)
    obs = tools.run_tool("check_results", {"url": CAT}, ctx)
    assert obs.ok and "Looks like a results list: 5 items (li.c)" in obs.text and "32 results" in obs.text
    nxt = tools.run_tool("next_page", {"url": CAT}, ctx)
    assert nxt.ok and nxt.data["url"] == PAGE2 and PAGE2 in ctx.facts["seen_urls"]
    PAGES["https://books.test/about"] = "<html><body><h1>About us</h1><p>We sell books.</p></body></html>"
    ctx.facts["seen_urls"].append("https://books.test/about")
    tools.run_tool("open_page", {"url": "https://books.test/about"}, ctx)
    assert not tools.run_tool("check_results", {"url": "https://books.test/about"}, ctx).ok


# ---- the loop -------------------------------------------------------------------------------

def test_loop_books_scenario(site, monkeypatch):
    run_id = make_run()
    monkeypatch.setattr(brain, "decide", script(
        brain.Step("Open the start page first.", "open_page", {"url": HOME}),
        brain.Step("Find the Mystery category.", "find_links", {"url": HOME, "pattern": "mystery"}),
        brain.Step("Open it.", "open_page", {"url": CAT}, ["must_1"]),
        brain.Step("Check it lists books.", "check_results", {"url": CAT}),
        brain.Step("It does: collect two pages.", "collect", {"url": CAT, "pages": 2, "note": "Mystery category"}),
    ))
    loop.safe_run(run_id)
    v = wait_done(run_id)
    assert v["status"] == "done", v["message"] + " | " + " || ".join(f"{s['kind']}: {s['text'][:160]}" for s in v["steps_list"])
    assert v["job"]["rows"] >= 1                                         # the app's "price < 40" filter applied
    kinds = [s["kind"] for s in v["steps_list"]]
    assert kinds.count("thought") == 5 and kinds.count("action") == 5 and kinds.count("observation") == 4
    assert v["ai_calls"] >= 6 and v["tokens"] >= 5000 and v["steps"] == 5
    assert [c["done"] for c in v["checklist"] if c["kind"] == "must"] == [True]
    assert any("Checklist: category: Mystery" in s["text"] for s in v["steps_list"])
    assert db.get_scrape(v["scrape_id"])["mode"] == "agent" and v["plan"]["pages"] == 2
    assert "scratchpad" not in v


def test_collect_waits_for_must_items(site, monkeypatch):
    run_id = make_run(musts=("category: Mystery", "in stock only"))
    monkeypatch.setattr(brain, "decide", script(
        brain.Step("Open.", "open_page", {"url": HOME}),
        brain.Step("Open Mystery.", "open_page", {"url": CAT}, ["must_1"]),
        brain.Step("Collect.", "collect", {"url": CAT}),
        brain.Step("The site has no stock filter.", "collect", {"url": CAT, "note": "no in-stock filter on this site"}),
    ))
    loop.safe_run(run_id)
    v = wait_done(run_id)
    assert v["status"] == "done"
    texts = [s["text"] for s in v["steps_list"] if s["kind"] == "observation"]
    assert any("Not yet: these must items aren't ticked: in stock only" in t for t in texts)
    assert [c["evidence"] for c in v["checklist"] if c["text"] == "in stock only"][0].startswith("not done on the site")


def test_loop_limits_and_stop(site, monkeypatch):
    run_id = make_run(limits={"steps": 2, "minutes": 5, "ai_calls": 30})
    monkeypatch.setattr(brain, "decide", script(*[brain.Step("look", "open_page", {"url": HOME})] * 5))
    loop.safe_run(run_id)
    v = store.get(run_id)
    assert v["status"] == "limit" and "limit of 2 steps" in v["message"] and v["steps"] == 2

    run_id = make_run()

    def stop_then(ctx):
        store.update(ctx.run_id, stop_requested=1)
        return brain.Step("look", "open_page", {"url": HOME})
    monkeypatch.setattr(brain, "decide", stop_then)
    loop.safe_run(run_id)
    assert store.get(run_id)["status"] == "stopped"


def test_stuck_repeat_is_short_circuited(site, monkeypatch):
    run_id = make_run(limits={"steps": 5, "minutes": 5, "ai_calls": 30})
    fetches = []
    real = fetcher.fetch_smart
    monkeypatch.setattr(fetcher, "fetch_smart", lambda url, **kw: fetches.append(url) or real(url, **kw))
    monkeypatch.setattr(brain, "decide", script(*[brain.Step("look", "open_page", {"url": HOME})] * 6))
    loop.safe_run(run_id)
    obs = [s["text"] for s in store.steps(run_id) if s["kind"] == "observation"]
    assert fetches.count(HOME) == 1 and sum("You already did exactly this at step 1" in t for t in obs) >= 2


def test_failed_collect_continues(site, monkeypatch):
    run_id = make_run(musts=())
    calls = {"n": 0}

    def flaky_build(html, url, field_names, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise recipe_builder.RecipeBuildError("No list of items could be read on this page")
        return {"recipe": dict(RECIPE), "rows": extractor.extract(html, RECIPE, url), "report": {}, "notes": "",
                "page_type": "ecommerce", "absent": []}
    monkeypatch.setattr(recipe_builder, "build_recipe", flaky_build)
    monkeypatch.setattr(brain, "decide", script(
        brain.Step("Open.", "open_page", {"url": HOME}),
        brain.Step("Collect the home page.", "collect", {"url": HOME}),
        brain.Step("That failed; open Mystery.", "open_page", {"url": CAT}),
        brain.Step("Collect it.", "collect", {"url": CAT}),
    ))
    loop.safe_run(run_id)
    v = wait_done(run_id)
    assert v["status"] == "done"
    assert any("could not collect from this page" in s["text"] for s in v["steps_list"] if s["kind"] == "observation")


def test_finish_gives_up_plainly(site, monkeypatch):
    run_id = make_run()
    monkeypatch.setattr(brain, "decide", script(
        brain.Step("Nothing works.", "finish", {"outcome": "ok", "summary": "done"}),
        brain.Step("Nothing works.", "finish", {"outcome": "failed", "summary": "the site has no mystery section"}),
    ))
    loop.safe_run(run_id)
    v = store.get(run_id)
    assert v["status"] == "error" and v["message"] == "The agent gave up: the site has no mystery section"


def test_unknown_tool_is_an_observation(site, monkeypatch):
    run_id = make_run(limits={"steps": 5, "minutes": 5, "ai_calls": 30})
    monkeypatch.setattr(brain, "decide", script(
        brain.Step("?", "teleport", {}),
        brain.Step("ok", "finish", {"outcome": "failed", "summary": "x"}),
    ))
    loop.safe_run(run_id)
    assert any("Unknown tool" in s["text"] for s in store.steps(run_id))


# ---- brain ----------------------------------------------------------------------------------

class _ProviderError(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.message, self.status_code = message, status_code


def test_brain_parses_steps_and_switches_to_the_backup(site, monkeypatch):
    ctx = ctx_for(make_run())
    answers = iter([
        _ProviderError("Rate limit reached ... on tokens per day (TPD): Limit 200000, Used 199720. Please try again in 28m40s", 429),
        ({"thought": "go", "checklist_done": ["must_1", "nope"], "tool": "open_page", "url": HOME, "pattern": "", "params": [],
          "fields": [], "form": 0, "pages": 0, "note": "", "summary": "", "outcome": "ok"}, 1234),
    ])

    def fake_ask(system, messages, schema, name, provider=None, model=None, timeout=None):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        assert provider == "openai" and model == "gpt-5-mini"
        assert "GOAL: Mystery books" in messages[0]["content"] and "[ ] must_1: category: Mystery" in messages[0]["content"]
        return a
    monkeypatch.setattr(recipe_builder, "ask_structured", fake_ask)
    monkeypatch.setattr(config, "agent_fallback", lambda: {"provider": "openai", "model": "gpt-5-mini"})
    step = brain.decide(ctx)
    assert step.tool == "open_page" and step.args == {"url": HOME} and step.checklist_done == ["must_1"]
    assert ctx.using_backup and ctx.provider == "openai"
    run = store.get(ctx.run_id)
    assert run["ai_calls"] == 2 and run["tokens"] == 1234
    assert any("Switched to the backup AI" in s["text"] for s in store.steps(ctx.run_id))


def test_brain_without_backup_ends_the_run_as_limit(site, monkeypatch):
    ctx = ctx_for(make_run())

    def fail(*a, **kw):
        raise _ProviderError("Rate limit reached ... per day (TPD) ... Please try again in 10m", 429)
    monkeypatch.setattr(recipe_builder, "ask_structured", fail)
    monkeypatch.setattr(config, "agent_fallback", lambda: None)
    with pytest.raises(brain.BrainError) as e:
        brain.decide(ctx)
    assert e.value.status == "limit" and "daily limit" in str(e.value) and "Settings" in str(e.value)
    monkeypatch.setattr(brain, "decide", fail if False else (lambda c: (_ for _ in ()).throw(brain.BrainError("limit", "Groq's daily limit is used up."))))
    run_id = make_run()
    loop.safe_run(run_id)
    assert store.get(run_id)["status"] == "limit"


# ---- planner: site suggestion and checklist ------------------------------------------------------

def test_clean_plan_checklist_and_site_in_goal():
    plan = planner.clean_plan({"start_url": "", "must": ["destination: Goa", "check-in 25 Nov", "destination: Goa"],
                               "request": {"columns": [{"name": "hotel", "type": "text"}, {"name": "price", "type": "money"}],
                                           "filters": [{"column": "price", "op": "lt", "value": "20000"}],
                                           "sort": {"column": "price", "desc": False}, "limit": 10}},
                              "hotels in goa", "booking.test")
    ids = [(c["id"], c["kind"]) for c in plan["checklist"]]
    assert ids == [("must_1", "must"), ("must_2", "must"), ("app_1", "app"), ("app_2", "app"), ("app_3", "app")]
    assert plan["checklist"][2]["text"] == "price < 20000" and "engine" not in plan
    edited = planner.clean_plan({**plan, "checklist": [{"id": "must_1", "kind": "must", "text": "destination: Goa, India"},
                                                       {"id": "app_1", "kind": "app", "text": "stale"}]}, "hotels", "booking.test")
    assert [c["text"] for c in edited["checklist"]][:2] == ["destination: Goa, India", "price < 20000"]
    assert planner.site_in_goal("hotels in Goa on booking.com under 20000") == "booking.com"
    assert planner.site_in_goal("Mystery books: title, price") == ""
    assert planner.site_in_goal("mail a@b.com for info") == ""


@pytest.fixture
def client(site, monkeypatch):
    from app.main import app
    return TestClient(app, base_url="http://localhost")


def test_plan_without_site_suggests_candidates(client, monkeypatch):
    monkeypatch.setattr(recipe_builder, "ask_json", lambda system, prompt, schema, name: {
        "candidates": [{"site": "https://books.test/", "reason": "lists every book", "note": ""},
                       {"site": "blocked.test", "reason": "also books", "note": ""},
                       {"site": "10.0.0.5", "reason": "private", "note": ""}], "question": ""})
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(fetcher, "is_opted_out", lambda host: host == "blocked.test")
    r = client.post("/api/agent/plan", json={"goal": "Mystery books: title and price"}).json()
    assert r["needs_site"] and [c["site"] for c in r["candidates"]] == ["books.test"]
    assert r["candidates"][0]["start_url"] == "https://books.test/" and len(r["dropped"]) == 2
    assert store.list_runs() == []                                     # no run until a site is chosen


def test_plan_with_site_in_goal_makes_a_react_run_and_starts_the_loop(client, monkeypatch):
    plan = {"start_url": HOME, "navigation": ["open Mystery", "stop on the results list"], "must": ["category: Mystery"],
            "columns": [{"name": "title", "meaning": "", "type": "text", "required": True}], "filters": [], "sort": {"column": "", "desc": False},
            "limit": 0, "auto": False, "details_hint": False, "question": "", "summary": "Mystery books", "pages": 2, "item_pages": "no"}
    monkeypatch.setattr(recipe_builder, "ask_json", lambda system, prompt, schema, name: dict(plan))
    monkeypatch.setattr(brain, "decide", script(
        brain.Step("Open.", "open_page", {"url": HOME}),
        brain.Step("Mystery.", "open_page", {"url": CAT}, ["must_1"]),
        brain.Step("Collect.", "collect", {"url": CAT}),
    ))
    r = client.post("/api/agent/plan", json={"goal": "Mystery books on books.test: title"}).json()
    assert r["status"] == "draft" and r["engine"] == "react" and r["checklist"][0]["text"] == "category: Mystery"
    started = client.post(f"/api/agent/runs/{r['uid']}/start", json={"steps": 10, "minutes": 5, "ai_calls": 20}).json()
    assert started["status"] in ("queued", "thinking")
    v = wait_done(r["id"])
    assert v["status"] == "done" and v["job"]["rows"] == 10
    assert client.get("/api/agent/worker/next", headers={"X-Agent-Token": "x"}).status_code == 403
