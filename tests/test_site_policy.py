"""Site policy (fetcher.site_policy): a site nobody has reviewed needs approval before its first scrape, approving it
is recorded, a never-scrape site stays refused even from the cache, and sites already in use keep working."""
import json
import time
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.agent import guard
from app.fetching import fetcher
from app.security import audit, netguard

RECIPE = {"item_selector": "li", "fields": [{"name": "name", "selector": "a"}], "pagination": {"type": "none"}}


@pytest.fixture
def approval(monkeypatch):
    """Unreviewed sites need approval (the real default), and made-up .test sites count as public."""
    monkeypatch.setattr(config, "SITE_POLICY_DEFAULT", "approval")
    real = netguard.check_url
    monkeypatch.setattr(netguard, "check_url",
                        lambda u: None if (urlsplit(u).hostname or "").endswith(".test") else real(u))
    db.init()
    yield


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app, base_url="http://localhost")


def test_an_unreviewed_site_needs_approval(approval):
    assert fetcher.site_policy("new.test") == "approval"
    with pytest.raises(fetcher.NeedsApproval) as e:
        fetcher.fetch("https://new.test/list")
    assert e.value.domain == "new.test" and isinstance(e.value, fetcher.BlockedError)


def test_analyse_and_start_answer_needs_approval(approval, client):
    r = client.post("/api/analyse", json={"url": "https://new.test/list"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "needs_approval"
    assert r.json()["detail"]["domain"] == "new.test"
    r = client.post("/api/jobs", json={"urls": ["https://new.test/list"], "recipe": RECIPE, "save_recipe": False})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "needs_approval"


def test_approving_records_who_and_lets_the_site_be_used(approval, client):
    db.cache_put("https://new.test/list", 200, "https://new.test/list", "<ul><li><a>A</a></li></ul>")
    r = client.post("/api/sites/new.test/approve", json={"terms": "ok", "note": "Terms allow listings"})
    assert r.status_code == 200 and r.json()["policy"] == "allowed"
    review = r.json()["review"]
    assert review["terms"] == "ok" and review["note"] == "Terms allow listings" and review["approved_at"]
    assert fetcher.site_policy("www.new.test") == "allowed"            # www. and the bare site share it
    assert fetcher.fetch("https://new.test/list").from_cache
    entry = audit.tail(event="site_approved")[-1]
    assert entry["domain"] == "new.test" and entry["note"] == "Terms allow listings"


def test_settings_change_the_policy_and_keep_never_scrape_in_step(approval, client):
    r = client.put("/api/sites/new.test/settings", json={"policy": "blocked", "terms": "restricted", "note": "No bots"})
    assert r.status_code == 200 and r.json()["blocked"] is True
    assert fetcher.site_policy("new.test") == "blocked"
    assert client.post("/api/sites/new.test/approve", json={}).status_code == 409   # unblock it in Settings first
    client.put("/api/sites/new.test/settings", json={"blocked": False})
    assert fetcher.site_policy("new.test") == "allowed"
    assert any(e["event"] == "policy_changed" and e["policy"] == "blocked" for e in audit.tail(domain="new.test"))


def test_a_blocked_site_is_refused_even_from_the_cache(approval):
    fetcher.update_settings("old.test", {"policy": "allowed"})
    db.cache_put("https://old.test/a", 200, "https://old.test/a", "<p>cached</p>")
    assert fetcher.fetch("https://old.test/a").from_cache
    fetcher.update_settings("old.test", {"policy": "blocked", "blocked": True})
    with pytest.raises(fetcher.BlockedError) as e:
        fetcher.fetch("https://old.test/a")
    assert not isinstance(e.value, fetcher.NeedsApproval)
    with pytest.raises(fetcher.BlockedError):
        fetcher.fetch_rendered("https://old.test/a")


def test_the_agent_refuses_sites_that_are_not_approved(approval, monkeypatch):
    monkeypatch.setattr(fetcher, "robots_allows", lambda u: True)
    ok, why = guard.check_url("https://new.test/list", "https://new.test")
    assert not ok and "approved" in why
    fetcher.update_settings("new.test", {"policy": "allowed"})
    assert guard.check_url("https://new.test/list", "https://new.test") == (True, "")


def test_sites_already_in_use_are_backfilled_as_allowed(monkeypatch):
    monkeypatch.setattr(config, "SITE_POLICY_DEFAULT", "approval")
    db.init()
    db.create_job(["https://used.test/x"], RECIPE, 1)
    with db.tx() as c:                    # as an older database had them: a run, a saved site, no policy anywhere
        c.execute("DELETE FROM app_settings WHERE key='site_policy_backfill'")
        c.execute("INSERT INTO sites(domain, recipe, source, settings, updated_at) VALUES(?,?,?,?,?)",
                  ("saved.test", "{}", "ai", json.dumps({"min_delay": 2}), time.time()))
        c.execute("INSERT INTO sites(domain, recipe, source, settings, updated_at) VALUES(?,?,?,?,?)",
                  ("never.test", "{}", "manual", json.dumps({"blocked": True}), time.time()))
    db.init()
    fetcher._settings_cache.clear()
    assert fetcher.site_policy("used.test") == "allowed"
    assert fetcher.site_policy("saved.test") == "allowed"
    assert db.get_site_settings("saved.test")["min_delay"] == 2
    assert db.get_site_settings("used.test")["review"]["terms"] == "not_reviewed"
    assert fetcher.site_policy("never.test") == "blocked"
    assert fetcher.site_policy("brand-new.test") == "approval"
