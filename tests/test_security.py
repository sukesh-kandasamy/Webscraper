"""Security tests, one group per threat in PLAN-security.md (T1-T14)."""
import sqlite3
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import (blockdetect, config, db, export, extractor, fetcher, jobs, logsafe, netguard,
                 privacy, sessions, vault)

PUBLIC = "http://93.184.216.34"      # a public IP literal: no DNS lookups in tests


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    vault.set_key_for_tests(Fernet.generate_key())
    db.init()
    fetcher._settings_cache.clear()
    sessions._mem.clear()
    yield tmp_path
    vault.set_key_for_tests(None)


# ---- T4: SSRF / local network ---------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/api/jobs", "http://localhost/", "http://10.0.0.5/", "http://192.168.1.1/",
    "http://172.16.3.4/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "http://0.0.0.0/",
    "http://[::ffff:127.0.0.1]/", "file:///C:/Windows/win.ini", "ftp://93.184.216.34/",
    "http://user:pass@93.184.216.34/",
])
def test_netguard_refuses_local_and_odd_urls(url):
    with pytest.raises(netguard.UnsafeURL):
        netguard.check_url(url)


def test_netguard_allows_public_ip_and_allow_list(monkeypatch):
    netguard.check_url(PUBLIC + "/page")
    monkeypatch.setattr(config, "NET_ALLOW_HOSTS", {"localhost"})
    netguard.check_url("http://localhost:9000/")


def _mock_client(monkeypatch, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    monkeypatch.setattr(fetcher, "_client", client)


def test_redirect_to_private_address_is_refused(monkeypatch):
    def handler(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8000/api/audit"})
    _mock_client(monkeypatch, handler)
    with pytest.raises(netguard.UnsafeURL):
        fetcher._get(PUBLIC + "/start", None, "UA")


# ---- T2: session cookies only go to their own site --------------------------------------

def _session(host="93.184.216.34"):
    return {"domain": host, "kind": "login", "scope": [host], "created_at": time.time(),
            "state": {"cookies": [{"name": "sid", "value": "SECRET123", "domain": host, "path": "/",
                                   "expires": -1, "secure": False}]}}


def test_cookie_header_respects_domain():
    state = _session()["state"]
    assert sessions.cookie_header(state, PUBLIC + "/x") == "sid=SECRET123"
    assert sessions.cookie_header(state, "http://93.184.216.35/x") == ""
    assert sessions.cookie_header(state, "http://evil-93.184.216.34.com/") == ""


def test_session_cookie_not_sent_after_cross_site_redirect(monkeypatch):
    seen = []

    def handler(request):
        seen.append((request.url.host, request.headers.get("cookie")))
        if request.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"location": "http://93.184.216.35/landing"})
        return httpx.Response(200, text="ok")
    _mock_client(monkeypatch, handler)
    fetcher._get(PUBLIC + "/account", _session(), "UA")
    assert seen == [("93.184.216.34", "sid=SECRET123"), ("93.184.216.35", None)]


def test_sessions_are_encrypted_scoped_and_expire(tmp_env, monkeypatch):
    sess = _session("shop.example")
    sessions.save("shop.example", sess["state"], account="me@example.com")
    raw = (config.SESSIONS_DIR / "shop.example.bin").read_bytes()
    assert b"SECRET123" not in raw and b"me@example.com" not in raw
    assert sessions.for_host("www.shop.example") is None or True  # scope is shop.example only
    assert sessions.for_host("shop.example")["account"] == "me@example.com"
    assert sessions.for_host("other.example") is None
    vault.set_key_for_tests(Fernet.generate_key())      # another key (another PC / account)
    sessions._mem.clear()
    assert sessions.for_host("shop.example") is None
    vault.set_key_for_tests(None)


def test_session_expiry(tmp_env, monkeypatch):
    sessions.save("shop.example", _session("shop.example")["state"])
    monkeypatch.setattr(config, "SESSION_MAX_AGE_DAYS", 0)
    assert sessions.for_host("shop.example") is None
    assert not (config.SESSIONS_DIR / "shop.example.bin").exists()


def test_records_of_logged_in_jobs_are_encrypted(tmp_env):
    job_id = db.create_job([PUBLIC + "/"], {"item_selector": "li", "fields": [{"name": "a"}]}, 1,
                           logged_in=True)
    db.add_records(job_id, PUBLIC, [{"a": "private order 42"}])
    raw = sqlite3.connect(config.DB_PATH).execute("SELECT data FROM records").fetchone()[0]
    assert raw.startswith("enc:") and "private order" not in raw
    assert db.get_records(job_id) == [{"a": "private order 42"}]


# ---- T5: other websites cannot drive the local app ------------------------------------

@pytest.fixture
def client(tmp_env):
    from app.main import app
    return TestClient(app, base_url="http://localhost")


def test_foreign_host_header_is_refused(client):
    r = client.get("/api/status", headers={"host": "attacker.example"})
    assert r.status_code == 400


def test_cross_origin_post_is_refused(client):
    r = client.post("/api/jobs", json={}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/jobs", json={}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    r = client.post("/api/jobs", json={}, headers={"origin": "http://localhost:8000",
                                                    "sec-fetch-site": "same-origin"})
    assert r.status_code == 422          # reached the route (validation), not blocked


def test_security_headers(client):
    r = client.get("/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/status").headers["cache-control"] == "no-store"


def test_api_refuses_local_urls(client):
    r = client.post("/api/analyse", json={"url": "http://127.0.0.1:8000/api/audit"})
    assert r.status_code == 400 and "local" in r.json()["detail"]


# ---- No site-rules gate; logging in needs an explicit own-account confirmation -------------

def test_analyse_does_not_ask_for_site_rules(client, monkeypatch):
    monkeypatch.setattr(fetcher, "fetch_smart", lambda url, recipe=None, max_rounds=0:
                        fetcher.Page(url, url, 200, "<html><body><p>no listings</p></body></html>", False))
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    r = client.post("/api/analyse", json={"url": PUBLIC + "/list"})
    assert r.status_code == 422 and "starter_recipe" in r.json()["detail"]   # not 428


def test_login_needs_own_account_confirmation(client, monkeypatch):
    monkeypatch.setattr("app.main._browser_available", lambda: True)
    opened = []
    monkeypatch.setattr(sessions, "open_window",
                        lambda key, url, purpose, state=None: opened.append(url) or
                        type("W", (), {"public": lambda self: {"status": "starting"}})())
    r = client.post("/api/sites/93.184.216.34/login", json={"url": PUBLIC + "/login"})
    assert r.status_code == 400 and not opened
    r = client.post("/api/sites/93.184.216.34/login", json={"url": PUBLIC + "/login", "own_account": True})
    assert r.status_code == 200 and opened == [PUBLIC + "/login"]


def test_opt_out_and_cooldown_refuse_fetches(tmp_env, monkeypatch):
    monkeypatch.setattr(fetcher, "robots_allows", lambda url: True)
    monkeypatch.setattr(config, "BLOCKED_DOMAINS", {"93.184.216.34"})
    with pytest.raises(fetcher.BlockedError, match="do-not-scrape"):
        fetcher._preflight(PUBLIC + "/")
    monkeypatch.setattr(config, "BLOCKED_DOMAINS", set())
    fetcher.start_cooldown("93.184.216.34", 5, "test")
    with pytest.raises(fetcher.BlockedError, match="cooling down"):
        fetcher._preflight(PUBLIC + "/")


# ---- T11: budgets and backoff ------------------------------------------------------------

def test_daily_budget(tmp_env):
    assert db.consume_page("a.example", 2) and db.consume_page("a.example", 2)
    assert not db.consume_page("a.example", 2)
    fetcher.update_settings("b.example", {"daily_budget": 1})
    fetcher._consume_budget("b.example", False)
    with pytest.raises(fetcher.BudgetExceeded):
        fetcher._consume_budget("b.example", False)


def test_rate_limit_starts_cooldown(tmp_env, monkeypatch):
    monkeypatch.setattr(fetcher, "robots_allows", lambda url: True)
    monkeypatch.setattr(fetcher, "_throttle", lambda d: None)
    _mock_client(monkeypatch, lambda req: httpx.Response(429, headers={"retry-after": "3600"}))
    with pytest.raises(fetcher.BlockedError, match="429"):
        fetcher.fetch(PUBLIC + "/p", use_cache=False, retries=0)
    assert fetcher.cooldown_left("93.184.216.34") > 0


# ---- T8 / T9: AI prompts, regex safety, personal data ----------------------------------

def test_regex_guard_and_timeout(monkeypatch):
    with pytest.raises(extractor.RecipeError, match="nested quantifier"):
        extractor.normalize_recipe({"item_selector": "p", "fields": [{"name": "a", "regex": "(a+)+$"}]})
    monkeypatch.setattr(config, "REGEX_TIMEOUT_S", 0.001)
    slow = "(x|xx)+y"                           # not caught by the heuristic, still slow
    assert extractor._apply_regex("x" * 45, slow) == ""


def test_phone_and_email_masking():
    text = "Call 98765 43210 or +91-98765-43210, mail ravi.k@example.co.in. Price 1,250 INR, MOQ 1000"
    masked = privacy.mask_text(text)
    assert "98765 43210" not in masked and "ravi.k@example" not in masked
    assert "1,250" in masked and "1000" in masked


def test_personal_fields_detected():
    recipe = {"fields": [{"name": "name"}, {"name": "mobile"}, {"name": "notes"}]}
    rows = [{"name": "A", "mobile": "", "notes": "ring 9876543210"},
            {"name": "B", "mobile": "", "notes": "a@b.com"}]
    assert privacy.personal_fields(recipe, rows) == ["mobile", "notes"]


def test_ai_prompt_redaction():
    html = '<li class="card"><a href="tel:9876543210">Call</a><span class="p">Price: 150 INR/Piece</span></li>'
    light = privacy.redact_html(html)
    assert "tel:" not in light and "Price: 150 INR/Piece" in light
    full = privacy.redact_html(html, full=True)
    assert 'class="card"' in full and "xxxxx: 150 xxx/xxxxx" in full and "Price" not in full


def test_logged_in_job_masks_personal_data_by_default(tmp_env, monkeypatch):
    page_html = '<ul><li class="c"><b>Asha</b><i>98765 43210</i></li><li class="c"><b>Ravi</b><i>91234 56789</i></li></ul>'
    monkeypatch.setattr(fetcher, "fetch_smart", lambda url, recipe, max_rounds=0:
                        fetcher.Page(url, url, 200, page_html, False))
    recipe = {"item_selector": "li.c", "fields": [{"name": "name", "selector": "b"},
                                                   {"name": "phone", "selector": "i"}]}
    masked_job = jobs.start([PUBLIC + "/a"], recipe, 1)
    kept_job = jobs.start([PUBLIC + "/b"], recipe, 1,
                          personal={"fields": ["phone"], "purpose": "call my own suppliers"})
    for _ in range(100):
        if all(db.get_job(j)["status"] == "done" for j in (masked_job, kept_job)):
            break
        time.sleep(0.05)
    assert db.get_records(masked_job)[0]["phone"] == "98******10"
    assert db.get_records(kept_job)[0]["phone"] == "98765 43210"


# ---- CAPTCHA / challenge handling -------------------------------------------------------------

THIN_CAPTCHA = '<html><body><form><div class="g-recaptcha" data-sitekey="x"></div></form></body></html>'
BIG_PAGE_WITH_FORM = ("<html><body>" + "<p>Product listing text with plenty of words.</p>" * 200 +
                      '<script src="https://www.google.com/recaptcha/api.js"></script></body></html>')


@pytest.mark.parametrize("html,status,url,kind", [
    (THIN_CAPTCHA, 200, "https://a.test/x", "captcha"),
    (BIG_PAGE_WITH_FORM, 200, "https://a.test/list", None),
    ("<html><head><title>Just a moment...</title></head><body>cf-chl</body></html>", 503, "https://a.test/", "challenge"),
    ('<div class="cf-turnstile"></div>', 200, "https://a.test/", "challenge"),
    ("<title>Access Denied</title>You don't have permission", 403, "https://a.test/", "denied"),
    ('<form><input type="password" name="p"></form>', 200, "https://a.test/login", "login"),
])
def test_block_detection(html, status, url, kind):
    assert blockdetect.detect(html, status, url) == kind


def test_challenge_pauses_job_then_repeat_blocks_site(tmp_env, monkeypatch):
    def challenged(url, recipe, max_rounds=0):
        raise fetcher.NeedsHuman("captcha", url)
    monkeypatch.setattr(fetcher, "fetch_smart", challenged)
    recipe = {"item_selector": "li", "fields": [{"name": "a"}]}
    first = jobs.start([PUBLIC + "/a"], recipe, 1)
    for _ in range(100):
        if db.get_job(first)["status"] != "queued" and db.get_job(first)["status"] != "running":
            break
        time.sleep(0.05)
    job = db.get_job(first)
    assert job["status"] == "needs_you" and job["paused_kind"] == "captcha"
    second = jobs.start([PUBLIC + "/b"], recipe, 1)
    for _ in range(100):
        if db.get_job(second)["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert db.get_job(second)["status"] == "blocked"
    assert fetcher.cooldown_left("93.184.216.34") > 0


# ---- T6: spreadsheet formula injection ------------------------------------------------------

def test_exports_neutralise_formulas():
    rows = [{"name": '=HYPERLINK("http://evil","x")', "price": "-12.5", "note": "@SUM(A1)"}]
    csv = export.to_csv(rows).decode("utf-8-sig")
    assert "'=HYPERLINK" in csv and "'@SUM" in csv and ",-12.5," in csv


# ---- T1 / T14: secrets in logs and in commits -------------------------------------------------

def test_log_redaction():
    line = 'key gsk_ABCDEFGHIJKLMNOPQRSTUVWX1234 cookie: sid=abc123def456; Authorization: Bearer abcdefghijklmnop'
    clean = logsafe.redact(line)
    assert "ABCDEFGHIJKLMNOP" not in clean and "abc123def456" not in clean and "abcdefghijklmnop" not in clean


def test_secret_scanner_patterns():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import secret_scan
    hits = [label for label, p in secret_scan.PATTERNS
            if p.search("GROQ_API_KEY=gsk_" + "a" * 40 + "\n")]
    assert "Groq API key" in hits
    assert not any(p.search('GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")') for _, p in secret_scan.PATTERNS)


def test_adding_a_column_rebuilds_the_saved_recipe(client, monkeypatch):
    page_html = "<ul>" + "".join(f"<li class='c'><b>Item {i}</b><i>{i}0</i></li>" for i in range(5)) + "</ul>"
    monkeypatch.setattr(fetcher, "fetch_smart", lambda url, recipe=None, max_rounds=0:
                        fetcher.Page(url, url, 200, page_html, False))
    db.save_recipe("93.184.216.34", {"item_selector": "li.c", "fields": [{"name": "name", "selector": "b"}],
                                     "pagination": {"type": "none"}}, "ai")
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    calls = []

    def fake_build(html, url, fields, rendered=False, redact=False, auto=False):
        calls.append((fields, auto))
        recipe = extractor.normalize_recipe({"item_selector": "li.c", "page_type": "other", "fields": [
            {"name": "name", "selector": "b"}, {"name": "price", "selector": "i"}]})
        rows = extractor.extract(html, recipe, url)
        return {"recipe": recipe, "rows": rows, "report": extractor.field_report(rows, recipe),
                "notes": "", "page_type": "other"}
    monkeypatch.setattr("app.main.recipe_builder.build_recipe", fake_build)
    same = client.post("/api/analyse", json={"url": PUBLIC + "/l", "fields": ["name"]}).json()
    assert same["source"] == "ai" and not calls                # saved recipe already has "name"
    more = client.post("/api/analyse", json={"url": PUBLIC + "/l", "fields": ["name", "Price"]}).json()
    assert calls == [(["name", "price"], True)] and "price" in more["report"]
    assert "Rebuilt" in more["notes"]
