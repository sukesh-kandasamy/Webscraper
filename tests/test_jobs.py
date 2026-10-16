"""Job queue and resume behaviour, with a fake fetcher and a temporary database."""
import threading
import time

import pytest

from app import config, db, fetcher, jobs

PAGE1 = """<ul><li class="c"><a href="/p1">A</a></li><li class="c"><a href="/p2">B</a></li></ul>
<a class="next" href="/list?page=2">next</a>"""
PAGE2 = """<ul><li class="c"><a href="/p3">C</a></li></ul>"""
RECIPE = {"item_selector": "li.c", "fields": [{"name": "name", "selector": "a"},
                                               {"name": "url", "selector": "a", "attr": "href"}],
          "pagination": {"type": "next_link", "selector": "a.next"}}


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init()
    yield


@pytest.fixture
def fake_fetch(monkeypatch):
    calls = []
    gate = threading.Event()
    gate.set()

    def fetch(url, use_cache=True, retries=2):
        gate.wait(timeout=5)
        calls.append(url)
        html = PAGE2 if "page=2" in url else PAGE1
        return fetcher.Page(url, url, 200, html, False)

    monkeypatch.setattr(fetcher, "fetch", fetch)
    monkeypatch.setattr(fetcher, "robots_allows", lambda u: True)
    return calls, gate


def wait_for(job_id, statuses, timeout=5):
    for _ in range(int(timeout * 20)):
        job = db.get_job(job_id)
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} stuck in {db.get_job(job_id)['status']}")


def test_job_follows_pagination_and_records_pages(fresh_db, fake_fetch):
    calls, _ = fake_fetch
    job_id = jobs.start(["https://x.test/list"], RECIPE, max_pages=5)
    job = wait_for(job_id, {"done"})
    assert job["rows"] == 3 and job["pages_done"] == 2
    assert set(db.job_pages(job_id)) == {"https://x.test/list", "https://x.test/list?page=2"}
    assert [r["url"] for r in db.get_records(job_id)] == \
        ["https://x.test/p1", "https://x.test/p2", "https://x.test/p3"]


def test_queue_limits_concurrent_jobs(fresh_db, fake_fetch, monkeypatch):
    calls, gate = fake_fetch
    gate.clear()                     # fetches block, so jobs stay running
    monkeypatch.setattr(jobs, "_slots", threading.BoundedSemaphore(1))
    a = jobs.start(["https://x.test/a"], RECIPE, 1)
    b = jobs.start(["https://x.test/b"], RECIPE, 1)
    wait_for(a, {"running"})
    time.sleep(0.3)
    assert db.get_job(b)["status"] == "queued"
    assert jobs.stop(b)              # a queued job can be cancelled
    wait_for(b, {"stopped"})
    gate.set()
    wait_for(a, {"done"})


def test_resume_skips_pages_already_fetched(fresh_db, fake_fetch):
    calls, _ = fake_fetch
    # Simulate a job that fetched page 1 (2 rows) and then the server died.
    job_id = db.create_job(["https://x.test/list"], RECIPE, 5)
    db.update_job(job_id, status="running")
    db.add_records(job_id, "https://x.test/list",
                   [{"name": "A", "url": "https://x.test/p1"}, {"name": "B", "url": "https://x.test/p2"}])
    db.add_job_page(job_id, "https://x.test/list", 2)

    db.init()                        # restart: running -> queued
    assert db.get_job(job_id)["status"] == "queued"
    assert jobs.resume_pending() == [job_id]
    job = wait_for(job_id, {"done"})
    assert job["rows"] == 3 and job["pages_done"] == 2
    names = sorted(r["name"] for r in db.get_records(job_id))
    assert names == ["A", "B", "C"]  # page 1 rows were not inserted twice
