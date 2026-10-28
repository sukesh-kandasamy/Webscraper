"""Trustworthy runs (app/runs/trust.py): a run doesn't "succeed" on a block page or with no rows, the values it
collects are checked against the page they came from, and each row keeps where it came from."""
import time

import pytest

from app import config, db
from app.fetching import fetcher
from app.runs import jobs, quality, trust

FILLER = " ".join(f"Lorem ipsum filler sentence number {i} about our company and its services." for i in range(80))
VERIFY = f"<html><body><h1>One more step</h1><p>Verifying your request, please wait.</p><p>{FILLER}</p></body></html>"
LIST = """<ul><li class="c"><a href="/p1">Alpha book</a><span class="r">4.5</span><span class="p">£12.00</span></li>
<li class="c"><a href="/p2">Beta book</a><span class="r">3</span><span class="p">£8.50</span></li></ul>"""
STATED_EMPTY = "<html><body><h1>Results</h1><p>Showing 1-20 of 120 results</p><div class='grid'></div></body></html>"
RECIPE = {"item_selector": "li.c", "fields": [{"name": "name", "selector": "a"}, {"name": "url", "selector": "a", "attr": "href"},
                                               {"name": "rating", "selector": ".r"}, {"name": "price", "selector": ".p"}],
          "pagination": {"type": "none"}}


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init()
    yield


def serve(monkeypatch, pages: dict):
    def fetch(url, use_cache=True, retries=2):
        return fetcher.Page(url, url, 200, pages[url], False)
    def no_browser(*a, **k):
        raise fetcher.FetchError("no browser in tests")
    monkeypatch.setattr(fetcher, "fetch", fetch)
    monkeypatch.setattr(fetcher, "fetch_rendered", no_browser)     # an empty-looking page isn't re-tried in a browser
    monkeypatch.setattr(fetcher, "robots_allows", lambda u: True)


def wait_for(job_id, statuses, timeout=5):
    for _ in range(int(timeout * 20)):
        job = db.get_job(job_id)
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} stuck in {db.get_job(job_id)['status']}: {db.get_job(job_id)['message']}")


# ---- the checks on their own -----------------------------------------------------------------------

def test_soft_block_reads_long_pages_but_not_normal_ones():
    assert len(VERIFY) > 5000 and trust.soft_block(VERIFY) == "challenge"
    assert trust.soft_block("<html><body><h1>Access denied</h1>" + FILLER + "</body></html>") == "denied"
    assert trust.soft_block("<html><body><p>No results found for 'zzz'.</p>" + FILLER + "</body></html>") is None
    assert trust.soft_block(LIST) is None
    # "enable JavaScript" inside <noscript> is normal on real pages
    assert trust.soft_block("<html><body><noscript>Please enable JavaScript to continue.</noscript>" + LIST) is None


def test_assess_names_why_a_run_looks_wrong():
    assert trust.assess(0, 0, {}) == []
    assert trust.assess(0, 1, {"expected_total": 2781})[0]["code"] == "stated_but_empty"
    assert "2,781" in trust.assess(0, 1, {"expected_total": 2781})[0]["message"]
    assert trust.assess(0, 2, {})[0]["code"] == "zero_rows"
    found = trust.assess(15, 2, {"empty_pages": [{"url": "https://x.test/b", "says": 300}]}, prev_rows=100)
    assert [f["code"] for f in found] == ["empty_pages", "rows_dropped"]
    assert trust.assess(40, 2, {}, prev_rows=50) == []


def test_value_checks_find_values_that_are_not_on_the_page():
    rows = [{"name": "Alpha book", "url": "https://x.test/p1", "rating": "4.5", "price": "£12.00"},
            {"name": "Gamma book", "url": "https://x.test/p9", "rating": "3", "price": "£8.50"},     # not on the page
            {"name": "Delta", "url": "https://x.test/p8", "rating": "3", "price": "£8.50"}]
    checks = trust.check_values(rows, LIST, RECIPE)
    assert checks["name"] == {"checked": 3, "ungrounded": 2} and "url" not in checks        # links are made absolute
    assert checks["price"]["ungrounded"] == 0
    built = {**RECIPE, "fields": [{"name": "specs", "kind": "kv", "selector": "dl"}]}
    assert trust.check_values([{"specs": "Weight: 1 kg; Size: M"}], LIST, built) == {}     # built values aren't checked
    assert trust.check_typed([{"rating": 47.0, "price": 0}, {"rating": 4.0, "price": 9.0}],
                             {"rating": "rating", "price": "money"}) == {"rating": 1, "price": 1}


def test_quality_report_shows_value_checks_and_warns():
    report = quality.build([{"name": "x"}] * 10, ["name"], {}, {"value_checks": {"name": {"checked": 10, "ungrounded": 6,
                                                                                         "invalid": 0}}}, 1)
    assert report["columns"]["name"]["ungrounded"] == 6
    assert any("6 of 10 values aren't on the page" in w for w in report["warnings"])


# ---- in a real job ---------------------------------------------------------------------------------

def test_a_page_that_says_results_but_gives_none_is_an_error_not_done(fresh_db, monkeypatch):
    serve(monkeypatch, {"https://x.test/list": STATED_EMPTY})
    job = wait_for(jobs.start(["https://x.test/list"], RECIPE, 1), {"done", "error"})
    assert job["status"] == "error" and "120 results" in job["message"]
    assert job["quality"]["suspect"][0]["code"] == "stated_but_empty"
    assert any("120 results" in w for w in job["quality"]["warnings"])


def test_a_verification_page_pauses_the_job_and_continue_resumes(fresh_db, monkeypatch):
    pages = {"https://x.test/list": VERIFY}
    serve(monkeypatch, pages)
    job_id = jobs.start(["https://x.test/list"], RECIPE, 1)
    job = wait_for(job_id, {"needs_you", "done", "error"})
    assert job["status"] == "needs_you" and job["paused_kind"] == "challenge" and job["rows"] == 0
    pages["https://x.test/list"] = LIST                    # solved in a visible window: the real page now
    fetcher.update_settings("x.test", {"last_challenge": 0})
    assert jobs.resume(job_id)
    job = wait_for(job_id, {"done", "error"})
    assert job["status"] == "done" and job["rows"] == 2


def test_a_good_run_has_checked_columns_and_keeps_where_rows_came_from(fresh_db, monkeypatch):
    serve(monkeypatch, {"https://x.test/list": LIST})
    recipe = {**RECIPE, "request": {"columns": [], "filters": [], "sort": {"column": "name", "desc": True}, "limit": 0}}
    job = wait_for(jobs.start(["https://x.test/list"], recipe, 1), {"done", "error"})
    assert job["status"] == "done" and not job["quality"].get("suspect")
    assert job["quality"]["columns"]["name"]["checked"] == 2 and job["quality"]["columns"]["name"]["ungrounded"] == 0
    # sorted (Beta first): the rows were rewritten, and still know their page and time
    items = db.get_record_pages(job["id"])
    assert [r["name"] for _, r, _ in items] == ["Beta book", "Alpha book"]
    assert all(p == "https://x.test/list" and at for p, _, at in items)


def test_export_can_add_where_each_row_came_from(fresh_db, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    serve(monkeypatch, {"https://x.test/list": LIST})
    job = wait_for(jobs.start(["https://x.test/list"], RECIPE, 1), {"done"})
    c = TestClient(app, base_url="http://localhost")
    plain = c.get(f"/api/jobs/{job['uid']}/export?fmt=csv").content.decode("utf-8-sig")
    full = c.get(f"/api/jobs/{job['uid']}/export?fmt=csv&include_source=true").content.decode("utf-8-sig")
    assert "source_page" not in plain.splitlines()[0]
    assert full.splitlines()[0].endswith("source_page,collected_at") and "https://x.test/list" in full.splitlines()[1]
    rows = c.get(f"/api/jobs/{job['uid']}/records?ids=true").json()
    assert rows[0]["_page"] == "https://x.test/list" and rows[0]["_at"]
