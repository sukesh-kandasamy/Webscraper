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
    "/results/JOB-ABC234?tab=quality": ("results.js", "results"),
    "/settings": ("settings.js", "settings"),
    "/agent": ("agent.js", "agent"),
    "/agent/runs/RUN-ABC234": ("agent.js", "agent"),
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
    r = client.get("/results/job-abc234?tab=info")
    assert 'data-job-uid="JOB-ABC234"' in r.text and 'data-tab="info"' in r.text
    assert 'data-tab="data"' in client.get("/results/JOB-ABC234?tab=<script>").text  # unknown tab -> default
    evil = client.get('/scrapes/new?url="><script>alert(1)</script>').text
    assert "<script>alert(1)</script>" not in evil and "&lt;script&gt;" in evil


def test_unknown_ids_still_render_the_page(client):
    assert client.get("/results/JOB-ZZZZZZ").status_code == 200
    assert client.get("/results/999999").status_code == 200
    assert client.get("/agent/runs/999999").status_code == 200
    assert client.get("/agent/runs/RUN-ZZZZZZ").status_code == 200
    assert client.get("/agent/runs/nope").status_code == 404
    assert client.get("/results/abc").status_code == 404                           # not a Job ID


def test_no_inline_scripts_in_templates():
    for f in TEMPLATES.rglob("*.html"):
        for tag in re.findall(r"<script\b[^>]*>", f.read_text(encoding="utf8")):
            assert "src=" in tag, f"{f.name}: inline <script> is not allowed by the CSP"


def test_static_assets_and_status_counters(client):
    for path in ("/static/js/core.js", "/static/css/app.css", "/static/img/logo.svg",
                 "/static/js/vendor/alpine-3.14.1.min.js"):
        assert client.get(path).status_code == 200, path
    assert client.get("/static/../app/config.py").status_code == 404
    assert client.get("/static/js/core.js").headers["cache-control"] == "no-cache"     # updates are picked up
    s = client.get("/api/status").json()
    assert {"active_jobs", "active_agent_runs", "saved_scrapes", "needs_you"} <= set(s)


def test_agent_workspace_has_its_own_layout(client):
    page = client.get("/agent").text
    assert 'aria-label="Main"' not in page and 'aria-label="Agent mode sections"' in page
    assert 'aria-label="Agent runs"' in page and "Manual mode" in page          # the mode switch
    home = client.get("/").text
    assert 'aria-label="Main"' in home and 'href="/agent"' in home
    for path in ("/agent/results", "/agent/scrapes"):                            # Agent mode's own data pages
        page = client.get(path).text
        assert 'aria-label="Agent mode sections"' in page and 'aria-label="Main"' not in page and 'data-mode="agent"' in page


def test_design_assets_are_served_locally(client):
    for path in ("/static/js/icons.js", "/static/fonts/roboto-latin-wght-normal.woff2",
                 "/static/fonts/ROBOTO-LICENSE.txt"):
        assert client.get(path).status_code == 200, path
    assert "MATERIAL_ICONS" in client.get("/static/js/icons.js").text
    css = client.get("/static/css/app.css").text
    assert "Roboto" in css and "--md-primary" in css and "fonts.googleapis" not in css


def test_docs_and_settings_are_shared_by_both_modes(client):
    """Docs (with Production risks as its first document) and Settings open in the mode you were last in."""
    docs = client.get("/docs").text
    assert "Production Readiness: Legal, Data and Operational Risks Review" in docs and 'href="/docs/production-risks"' in docs
    assert 'aria-label="Main"' in docs and 'data-mode="manual"' in docs             # never visited Agent mode yet
    risks = client.get("/docs/production-risks").text
    assert 'aria-label="Breadcrumb"' in risks and 'href="/docs"' in risks
    client.get("/agent")                                                               # now in Agent mode
    for path in ("/docs", "/docs/production-risks", "/settings"):
        page = client.get(path).text
        assert 'data-mode="agent"' in page and 'aria-label="Agent mode sections"' in page, path
        assert 'href="/docs"' in page and 'href="/settings"' in page                   # both reachable from Agent mode
    client.get("/")                                                                    # back to Manual mode
    page = client.get("/settings").text
    assert 'data-mode="manual"' in page and 'aria-label="Docs and settings"' in page
