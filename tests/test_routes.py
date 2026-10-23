"""Recorded routes, replayed by a real headless browser against a local page whose filters live only
inside the page (the address never changes). Skipped when no browser can be started."""
import http.server
import threading
import time

import pytest
from cryptography.fernet import Fernet

from app import browser, config, db, extractor, fetcher, jobs, vault
from app.agent import routes

PAGE = """<!doctype html><html><body>
<div id="cookie-box">We use cookies <button id="cookie-ok">Accept all</button></div>
<input id="q" name="q" placeholder="Search books" aria-label="Search books">
<div class="filters"><button data-genre="fiction">Fiction</button><button data-genre="history">History</button></div>
<ul id="list"></ul>
<script>
const books = [];
for (let i = 1; i <= 30; i++) books.push({title: (i % 3 ? 'Dragon tale ' : 'Old empire ') + i,
                                          genre: i % 2 ? 'fiction' : 'history', price: 100 + i});
let genre = '', q = '';
function show() {
  document.getElementById('list').innerHTML = books
    .filter(b => (!genre || b.genre === genre) && (!q || b.title.toLowerCase().includes(q)))
    .map(b => `<li class="book"><h3>${b.title}</h3><span class="price">₹${b.price}</span></li>`).join('');
}
document.getElementById('cookie-ok').onclick = () => document.getElementById('cookie-box').remove();
document.querySelectorAll('[data-genre]').forEach(b => b.onclick = () => { genre = b.dataset.genre; show(); });
document.getElementById('q').addEventListener('keydown', e => { if (e.key === 'Enter') { q = e.target.value.toLowerCase(); show(); } });
show();
</script></body></html>"""

RECIPE = extractor.normalize_recipe({"item_selector": "li.book", "fields": [
    {"name": "title", "selector": "h3"}, {"name": "price", "selector": "span.price"}]})

# What the agent's actions look like once recorded (from agent_steps details).
STEPS = [
    {"kind": "action", "url": "", "detail": {"action": "navigate", "params": {"url": "START"}}},
    {"kind": "action", "detail": {"action": "click", "params": {},
                                  "target": {"tag": "button", "text": "Accept all", "attributes": {"id": "cookie-ok"}}}},
    {"kind": "action", "detail": {"action": "click", "params": {},
                                  "target": {"tag": "button", "text": "No thanks", "attributes": {"class": "newsletter-close"}}}},
    {"kind": "action", "detail": {"action": "input", "params": {"text": "dragon"},
                                  "target": {"tag": "input", "text": "", "attributes": {"id": "q", "placeholder": "Search books"}}}},
    {"kind": "action", "detail": {"action": "send_keys", "params": {"keys": "Enter"}}},
    {"kind": "action", "detail": {"action": "click", "params": {}, "error": "element not found",
                                  "target": {"tag": "button", "text": "Poetry"}}},
    {"kind": "thought", "detail": {}},
    {"kind": "action", "detail": {"action": "click", "params": {},
                                  "target": {"tag": "button", "text": "Fiction", "attributes": {}, "xpath": "html/body/div[2]/button[1]"}}},
]


@pytest.fixture(scope="module")
def site():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGE.encode() if self.path.startswith("/books") else b"User-agent: *\nAllow: /\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8" if self.path.startswith("/books") else "text/plain")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/books"
    server.shutdown()


@pytest.fixture
def local_ok(monkeypatch, site):
    if not browser.available():
        pytest.skip("no browser available")
    vault.set_key_for_tests(Fernet.generate_key())
    db.init()
    # The test page is on this computer, which the network guard normally refuses.
    monkeypatch.setattr(config, "NET_ALLOW_HOSTS", {"127.0.0.1"})
    monkeypatch.setattr(config, "MIN_DELAY", 0)
    monkeypatch.setattr(config, "MAX_DELAY", 0)
    monkeypatch.setattr(fetcher, "robots_allows", lambda url: True)
    yield site
    vault.set_key_for_tests(None)


def test_from_steps_keeps_successful_actions_only():
    route = routes.from_steps(STEPS)
    assert [s["action"] for s in route] == ["click", "click", "input", "send_keys", "click"]
    assert route[2]["value"] == "dragon" and route[3]["value"] == "Enter"
    assert routes.describe(route[2]) == "Type “dragon” into “Search books”"


def test_selectors_prefer_stable_attributes():
    sels = routes.selectors({"tag": "input", "attributes": {"id": "q", "placeholder": "Search books"}})
    assert sels[0] == 'input[id="q"]'
    assert routes.selectors({"tag": "div", "attributes": {"id": "react-select-12345"}, "text": "Go"})[0] == 'div:text-is("Go")'


def test_replay_reaches_the_in_page_results_without_ai(local_ok):
    pages = routes.replay(local_ok, routes.from_steps(STEPS), RECIPE, 1, "127.0.0.1")
    rows = extractor.extract(pages[0].html, RECIPE, pages[0].final_url)
    # search "dragon" + Fiction: odd numbers not divisible by 3
    assert rows and all(r["title"].startswith("Dragon tale") for r in rows)
    assert len(rows) == len([i for i in range(1, 31) if i % 3 and i % 2])


def test_broken_route_says_where(local_ok):
    route = routes.from_steps(STEPS) + [{"action": "click", "target": {"tag": "button", "text": "Poetry"}}]
    with pytest.raises(routes.RouteBroken) as e:
        routes.replay(local_ok, route, RECIPE, 1, "127.0.0.1")
    assert e.value.step == 6 and "Poetry" in e.value.reason


def test_job_with_a_route_collects_without_ai(local_ok):
    recipe = extractor.normalize_recipe({**RECIPE, "route": routes.from_steps(STEPS)})
    job_id = jobs.start([local_ok], recipe, 3)
    for _ in range(300):
        job = db.get_job(job_id)
        if job["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job["message"]
    assert job["rows"] == 10 and job["quality"]["route_steps"] == 5
    broken = extractor.normalize_recipe({**RECIPE, "route": [{"action": "click", "target": {"tag": "button", "text": "Poetry"}}]})
    job_id = jobs.start([local_ok], broken, 1)
    for _ in range(300):
        job = db.get_job(job_id)
        if job["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert job["status"] == "error" and job["quality"]["route_broken"]["step"] == 1
    assert "re-record" in job["message"]


def test_handoff_checks_and_saves_the_route(local_ok, monkeypatch):
    """The agent reached in-page results: the handoff replays its steps, saves them with the scrape, and
    the first run already replays them (no AI after the agent)."""
    from app import recipe_builder, request_parser
    from app.agent import handoff, store
    store.init()

    def fake_build(html, url, fields, rendered=False, redact=False, auto=False, request=None):
        rows = extractor.extract(html, RECIPE, url)
        return {"recipe": dict(RECIPE), "rows": rows, "report": {}, "notes": "", "page_type": "ecommerce", "absent": []}
    monkeypatch.setattr(recipe_builder, "build_recipe", fake_build)

    plan = {"start_url": local_ok, "navigation": ["search dragon", "Fiction"], "pages": 2, "item_pages": "no",
            "request": request_parser.clean_request({"columns": ["title", "price"]}), "summary": "Dragon fiction"}
    run_id = store.create("dragon fiction books", "127.0.0.1", local_ok, plan, {})
    store.update(run_id, status="navigating")
    for s in STEPS:                      # what the worker recorded while the agent worked
        store.add_step(run_id, s["kind"], "x", url=local_ok, detail=s["detail"])
    agent_page = routes.replay(local_ok, routes.from_steps(STEPS), RECIPE, 1, "127.0.0.1")[0]

    handoff._handoff(run_id, local_ok, agent_page.html, "Fiction + dragon")
    run = store.get(run_id)
    scrape = db.get_scrape(run["scrape_id"])
    assert scrape["mode"] == "agent" and len(scrape["route"]) == 5
    assert scrape["recipe"]["route"] and scrape["recipe"]["agent"]["goal"] == "dragon fiction books"
    for _ in range(300):
        job = db.get_job(run["job_id"])
        if job["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert job["status"] == "done" and job["rows"] == 10 and job["quality"]["route_steps"] == 5
    assert any("Recorded route works" in s["text"] for s in store.steps(run_id))
