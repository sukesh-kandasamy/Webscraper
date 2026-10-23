"""Agent mode: guard rules, planner, the worker API and the handoff to the engine (no real browser or AI)."""
import time

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import config, db, extractor, fetcher, netguard, recipe_builder, vault
from app.agent import guard, handoff, planner, runner, store

PUBLIC = "http://93.184.216.34"
LIST = "<ul>" + "".join(f"<li class='c'><h3><a href='/p/{i}'>Mystery book number {i}</a></h3><p class='price'>£{i}9.99</p></li>"
                        for i in range(1, 6)) + "</ul>"


# ---- guard -------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def test_hosts(monkeypatch):
    """*.test names don't resolve; everything else still goes through the real network guard."""
    real = netguard.check_url

    def check(url):
        from urllib.parse import urlsplit
        if (urlsplit(url).hostname or "").endswith(".test"):
            return
        return real(url)
    monkeypatch.setattr(netguard, "check_url", check)


@pytest.fixture
def open_site(monkeypatch):
    db.init()
    monkeypatch.setattr(fetcher, "robots_allows", lambda url: True)


def test_guard_keeps_the_agent_on_the_planned_site(open_site):
    assert guard.check_url("https://books.test/catalogue/mystery_3/", "books.test")[0]
    assert guard.check_url("https://www.books.test/x", "books.test")[0]
    ok, why = guard.check_url("https://evil.test/", "books.test")
    assert not ok and "not the planned site" in why
    assert not guard.check_url("http://127.0.0.1:8000/api/jobs", "books.test")[0]
    assert not guard.check_url("file:///C:/secret.txt", "books.test")[0]


def test_guard_respects_robots(monkeypatch):
    db.init()
    monkeypatch.setattr(fetcher, "robots_allows", lambda url: "/search" not in url)
    assert not guard.check_url("https://books.test/search?q=x", "books.test")[0]


@pytest.mark.parametrize("text", ["Add to cart", "Buy now", "Apply now", "Contact seller", "View phone number",
                                  "Sign in", "Get Best Price", "Send enquiry", "Post free ad", "WhatsApp"])
def test_guard_refuses_risky_clicks(text):
    assert not guard.check_click({"tag": "button", "text": text})[0]


@pytest.mark.parametrize("text", ["Mystery", "Next", "Apply", "Apply filters", "3 BHK", "Price: Low to High",
                                  "Search", "Load more", "Reject all"])
def test_guard_allows_navigation_clicks(text):
    assert guard.check_click({"tag": "button", "text": text})[0]


def test_guard_refuses_personal_fields():
    assert not guard.check_input({"attributes": {"type": "password"}}, "x")[0]
    assert not guard.check_input({"attributes": {"type": "text", "name": "mobile_number"}}, "98")[0]
    assert not guard.check_input({"attributes": {"type": "text", "placeholder": "Your email"}}, "a")[0]
    assert guard.check_input({"attributes": {"type": "search", "placeholder": "Search locality"}}, "Whitefield")[0]
    assert not guard.check_click({"tag": "a", "text": "Call", "attributes": {"href": "tel:+911234567890"}})[0]


# ---- planner -------------------------------------------------------------------------------

PLAN = {"start_url": "https://evil.test/", "navigation": ["open the Mystery category"],
        "columns": [{"name": "title", "meaning": "", "type": "text", "required": True},
                    {"name": "price", "meaning": "", "type": "money", "required": True}],
        "filters": [{"column": "price", "op": "lt", "value": "20"}], "sort": {"column": "", "desc": False},
        "limit": 0, "auto": False, "details_hint": False, "question": "", "summary": "Mystery books under £20",
        "pages": 99, "item_pages": "maybe"}


def test_clean_plan_keeps_the_site_and_sane_values():
    plan = planner.clean_plan(PLAN, "mystery books under £20", "books.test")
    assert plan["start_url"] == "https://books.test/"            # off-site start replaced
    assert plan["navigation"][-1] == "stop on the results list"
    assert plan["pages"] == 50 and plan["item_pages"] == "auto"
    assert [c["name"] for c in plan["request"]["columns"]] == ["title", "price", "url"]
    text = planner.task_text(plan)
    assert "books.test" in text and "results_reached" in text and "price < 20" in text


def test_make_plan_with_fake_ai(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(config, "AI_PROVIDER", "groq")
    monkeypatch.setattr(recipe_builder, "ask_json", lambda system, prompt, schema, name: {
        **PLAN, "start_url": "https://books.test/catalogue/"})
    plan = planner.make_plan("mystery books under £20", "books.test")
    assert plan["start_url"] == "https://books.test/catalogue/"
    with pytest.raises(planner.PlanError):
        planner.make_plan("x", "http://10.0.0.5/")


# ---- API + worker + handoff ------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    vault.set_key_for_tests(Fernet.generate_key())
    db.init()
    store.init()
    monkeypatch.setattr(fetcher, "robots_allows", lambda url: True)
    monkeypatch.setattr(fetcher, "fetch_smart", lambda url, recipe=None, max_rounds=0, use_cache=True:
                        fetcher.Page(url, url, 200, LIST, False))
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(config, "AGENT_PROVIDER", "groq")
    monkeypatch.setattr(recipe_builder, "ask_json", lambda system, prompt, schema, name: dict(PLAN))
    started = []
    monkeypatch.setattr(runner, "ensure", lambda url: started.append(url))
    from app.main import app
    c = TestClient(app, base_url="http://localhost")
    c.started = started
    yield c
    vault.set_key_for_tests(None)


def fake_build(html, url, fields, rendered=False, redact=False, auto=False, request=None):
    recipe = extractor.normalize_recipe({"item_selector": "li.c", "fields": [
        {"name": "title", "selector": "h3 a"}, {"name": "price", "selector": "p.price"},
        {"name": "url", "selector": "h3 a", "attr": "href"}],
        "pagination": {"type": "none"}, "request": request})
    rows = extractor.extract(html, recipe, url)
    return {"recipe": recipe, "rows": rows, "report": {}, "notes": "", "page_type": "ecommerce", "absent": []}


def test_plan_start_worker_flow_and_handoff(client, monkeypatch):
    monkeypatch.setattr(recipe_builder, "build_recipe", fake_build)
    run = client.post("/api/agent/plan", json={"goal": "Mystery books under £20", "site": PUBLIC}).json()
    assert run["status"] == "draft" and run["plan"]["start_url"] == PUBLIC + "/"
    started = client.post(f"/api/agent/runs/{run['id']}/start", json={"steps": 20, "minutes": 5, "ai_calls": 30}).json()
    assert started["status"] == "queued" and client.started

    w = {"X-Agent-Token": runner.TOKEN}
    assert client.get("/api/agent/worker/next").status_code == 403            # token required
    job = client.get("/api/agent/worker/next", headers=w).json()
    assert job["id"] == run["id"] and "results_reached" in job["task"] and job["limits"]["steps"] == 20
    assert store.get(run["id"])["status"] == "navigating"

    refused = client.post(f"/api/agent/worker/runs/{run['id']}/check", headers=w,
                          json={"action": "click", "element": {"tag": "button", "text": "Add to cart"}}).json()
    assert refused["ok"] is False
    off_site = client.post(f"/api/agent/worker/runs/{run['id']}/check", headers=w,
                           json={"action": "navigate", "url": "https://other.test/"}).json()
    assert off_site["ok"] is False
    step = client.post(f"/api/agent/worker/runs/{run['id']}/step", headers=w,
                       json={"kind": "action", "text": "Clicked “Mystery”", "url": PUBLIC + "/mystery",
                             "steps": 2, "ai_calls": 3}).json()
    assert step == {"stop": False}
    captcha = client.post(f"/api/agent/worker/runs/{run['id']}/step", headers=w, json={
        "kind": "thought", "text": "x", "url": PUBLIC + "/mystery",
        "html": "<html><body><div class='g-recaptcha'></div>Please verify you are human</body></html>"}).json()
    assert captcha["stop"] and captcha["reason"] == "human_check"

    assert client.post(f"/api/agent/worker/runs/{run['id']}/arrived", headers=w,
                       json={"url": PUBLIC + "/mystery", "html": LIST, "note": "Mystery category"}).json()["ok"]
    for _ in range(100):
        v = client.get(f"/api/agent/runs/{run['id']}").json()
        if v["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert v["status"] == "done", v["message"]
    # £19.99 only: the request's "price < 20" filter applied by the engine
    assert v["job"]["rows"] == 1 and "Collected 1 rows" in v["summary"]
    texts = [s["text"] for s in v["steps_list"]]
    assert any(t.startswith("Refused") for t in texts) and any("page address" in t for t in texts)
    scrape = db.get_scrape(v["scrape_id"])
    assert scrape["mode"] == "agent" and scrape["urls"] == [PUBLIC + "/mystery"]


def test_stop_and_limits(client):
    run = client.post("/api/agent/plan", json={"goal": "books", "site": PUBLIC}).json()
    client.post(f"/api/agent/runs/{run['id']}/start", json={"steps": 10, "minutes": 5, "ai_calls": 5})
    w = {"X-Agent-Token": runner.TOKEN}
    client.get("/api/agent/worker/next", headers=w)
    over = client.post(f"/api/agent/worker/runs/{run['id']}/step", headers=w,
                       json={"kind": "thought", "text": "x", "ai_calls": 5}).json()
    assert over["stop"] and over["reason"] == "limit"
    client.post(f"/api/agent/runs/{run['id']}/stop")
    assert client.post(f"/api/agent/worker/runs/{run['id']}/step", headers=w,
                       json={"kind": "thought", "text": "x"}).json()["reason"] == "stopped"
    client.post(f"/api/agent/worker/runs/{run['id']}/finish", headers=w, json={"status": "stopped"})
    assert store.get(run["id"])["status"] == "stopped"


def test_in_page_filters_collect_the_agents_page(client, monkeypatch):
    """When the address alone doesn't bring back the agent's results, its page is collected as is."""
    monkeypatch.setattr(recipe_builder, "build_recipe", fake_build)
    monkeypatch.setattr(fetcher, "fetch_smart", lambda url, recipe=None, max_rounds=0, use_cache=True:
                        fetcher.Page(url, url, 200, "<ul><li class='c'><h3><a href='/other'>Other</a></h3></li></ul>", False))
    run = client.post("/api/agent/plan", json={"goal": "books", "site": PUBLIC}).json()
    client.post(f"/api/agent/runs/{run['id']}/start", json={})
    client.get("/api/agent/worker/next", headers={"X-Agent-Token": runner.TOKEN})
    handoff._handoff(run["id"], PUBLIC + "/mystery", LIST, "")
    v = client.get(f"/api/agent/runs/{run['id']}").json()
    assert v["status"] == "done" and v["job"]["rows"] == 1 and v["job"]["pages_done"] == 1


# ---- engine fixes found in live runs --------------------------------------------------------

def test_loose_next_selector_stays_on_the_listing():
    html = ('<div class="quote"><a class="tag" href="/tag/life/page/1/">life</a></div>'
            '<li class="next"><a href="/tag/inspirational/page/2/">Next →</a></li>')
    recipe = extractor.normalize_recipe({"item_selector": "div", "fields": [{"name": "x", "selector": ""}],
                                         "pagination": {"type": "next_link", "selector": "a[href*='page/']"}})
    base = "https://q.test/tag/inspirational/page/1/"
    assert extractor.next_page_url(html, recipe, base, 0) == "https://q.test/tag/inspirational/page/2/"
    only_tags = '<a class="tag" href="/tag/life/page/1/">life</a>'
    assert extractor.next_page_url(only_tags, recipe, base, 0) is None


def test_list_columns_join_all_values():
    html = "<div>" + "".join(f"<div class='q'><span class='t'>Q{i}</span><a class='tag'>life</a><a class='tag'>hope</a></div>"
                             for i in range(3)) + "</div>"
    recipe = extractor.normalize_recipe({"item_selector": "div.q", "fields": [
        {"name": "text", "selector": "span.t"}, {"name": "tags", "selector": "a.tag"}]})
    recipe = recipe_builder.mark_list_columns(html, recipe)
    assert extractor.extract(html, extractor.normalize_recipe(recipe), "https://q.test/")[0]["tags"] == "life, hope"


def test_next_link_to_the_current_page_is_skipped():
    """scrapethissite: on the first results page (?q=New) the site's "Next »" points to ?page_num=1."""
    html = ('<ul class="pagination"><li><a href="?page_num=1&q=New">1</a></li><li><a href="?page_num=2&q=New">2</a></li>'
            '<li><a href="?page_num=1&q=New" aria-label="Next">»</a></li></ul><a href="?q=Old">Old teams</a>')
    recipe = extractor.normalize_recipe({"item_selector": "tr", "fields": [{"name": "x", "selector": ""}],
                                         "pagination": {"type": "next_link", "selector": "a[href]"}})
    base = "https://s.test/pages/forms/?q=New"
    assert extractor.next_page_url(html, recipe, base, 0) == "https://s.test/pages/forms/?page_num=2&q=New"


def test_missing_next_selector_falls_back_to_page_links():
    html = '<table><tr class="t"><td>A</td></tr></table><ul><li><a href="?page_num=2&q=New">2</a></li></ul>'
    recipe = extractor.normalize_recipe({"item_selector": "tr.t", "fields": [{"name": "x", "selector": "td"}],
                                         "pagination": {"type": "next_link", "selector": "a[rel=next]"}})
    fixed = recipe_builder.check_pagination(html, "https://s.test/pages/forms/?q=New", recipe)
    assert fixed["pagination"]["selector"] == "a[href]"


def test_openai_provider_structured_call(monkeypatch):
    """SCRAPER_AI_PROVIDER=openai: strict JSON schema over plain HTTPS; errors keep their status for retries."""
    import httpx
    sent = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, body=json, auth=headers["Authorization"])
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(config, "AI_PROVIDER", "openai")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    assert config.ai_enabled() and config.ai_key_name() == "OPENAI_API_KEY"
    out = recipe_builder.ask_json("sys", "hello", {"type": "object", "properties": {"ok": {"type": "boolean"}},
                                                   "required": ["ok"], "additionalProperties": False}, "t")
    assert out == {"ok": True} and sent["url"].endswith("/chat/completions") and sent["auth"] == "Bearer sk-test"
    assert sent["body"]["response_format"]["json_schema"]["strict"] is True

    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(recipe_builder.OpenAIError) as e:
        recipe_builder._ask_openai([{"role": "user", "content": "x"}], {}, "s", "t")
    assert e.value.status_code == 401 and "bad key" in str(e.value)


def test_openai_keys_are_redacted_and_scanned():
    import sys
    from pathlib import Path
    from app import logsafe
    key = "sk-proj-" + "Ab1_-" * 10
    assert key not in logsafe.redact(f"auth {key}")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import secret_scan
    assert any(p.search(f"OPENAI_API_KEY={key}") for _, p in secret_scan.PATTERNS)
