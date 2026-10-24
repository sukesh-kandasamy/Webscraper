"""The UI's pages: one template and one script per route, safe headers, no inline scripts."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.agent import store as agent_store

TEMPLATES = Path(config.TEMPLATES_DIR)
PAGES = {
    "/": ("home.js", "home"),
    "/scrapes": ("scrapes.js", "scrapes"),
    "/scrapes/new": ("new-scrape.js", "new"),
    "/scrapes/7/edit": ("new-scrape.js", "scrapes"),
    "/results": ("results.js", "results"),
    "/results/42?tab=quality": ("results.js", "results"),
    "/settings": ("settings.js", "settings"),
    "/agent": ("agent.js", "agent"),
    "/agent/runs/5": ("agent.js", "agent"),
}


@pytest.fixture
def client():
    db.init()
    agent_store.init()
    from app.main import app
    return TestClient(app, base_url="http://localhost")


@pytest.mark.parametrize("path", PAGES)
def test_every_page_has_its_own_template_and_script(client, path):
    script, active = PAGES[path]
    r = client.get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert f'src="/static/js/pages/{script}"' in r.text and 'src="/static/js/core.js"' in r.text
    assert f'data-active="{active}"' in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_page_data_goes_into_attributes_and_is_escaped(client):
    r = client.get("/results/42?tab=quality")
    assert 'data-job-id="42"' in r.text and 'data-tab="quality"' in r.text
    assert 'data-tab="data"' in client.get("/results/1?tab=<script>").text          # unknown tab -> default
    evil = client.get('/scrapes/new?url="><script>alert(1)</script>').text
    assert "<script>alert(1)</script>" not in evil and "&lt;script&gt;" in evil


def test_unknown_ids_still_render_the_page(client):
    assert client.get("/results/999999").status_code == 200
    assert client.get("/agent/runs/999999").status_code == 200
    assert client.get("/results/abc").status_code == 422


def test_no_inline_scripts_in_templates():
    for f in TEMPLATES.rglob("*.html"):
        for tag in re.findall(r"<script\b[^>]*>", f.read_text(encoding="utf8")):
            assert "src=" in tag, f"{f.name}: inline <script> is not allowed by the CSP"


def test_static_assets_and_status_counters(client):
    for path in ("/static/js/core.js", "/static/css/app.css", "/static/img/logo.svg",
                 "/static/js/vendor/alpine-3.14.1.min.js"):
        assert client.get(path).status_code == 200, path
    assert client.get("/static/../app/config.py").status_code == 404
    s = client.get("/api/status").json()
    assert {"active_jobs", "active_agent_runs", "saved_scrapes", "needs_you"} <= set(s)


def test_agent_workspace_has_its_own_layout(client):
    page = client.get("/agent").text
    assert 'aria-label="Main"' not in page and "Back to Universal Scraper" in page
    assert 'aria-label="Agent runs"' in page
    home = client.get("/").text
    assert 'aria-label="Main"' in home and 'href="/agent"' in home


def test_design_assets_are_served_locally(client):
    for path in ("/static/js/icons.js", "/static/fonts/roboto-latin-wght-normal.woff2",
                 "/static/fonts/ROBOTO-LICENSE.txt"):
        assert client.get(path).status_code == 200, path
    assert "MATERIAL_ICONS" in client.get("/static/js/icons.js").text
    css = client.get("/static/css/app.css").text
    assert "Roboto" in css and "--md-primary" in css and "fonts.googleapis" not in css
