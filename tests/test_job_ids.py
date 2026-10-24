"""Every job has a Job ID (JOB-7K3QX9) that people see and use; the number stays internal."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.agent import store as agent_store
from app.security import audit

RECIPE = {"item_selector": "li", "fields": [{"name": "title", "selector": "a"}]}


@pytest.fixture
def client():
    db.init()
    agent_store.init()
    from app.main import app
    return TestClient(app, base_url="http://localhost", follow_redirects=False)


def _job(**kw):
    job_id = db.create_job(["https://example.com/list"], RECIPE, 3, **kw)
    db.update_job(job_id, status="done", rows=0)
    return db.get_job(job_id)


def test_new_jobs_get_unique_job_ids(client):
    uids = {_job()["uid"] for _ in range(30)}
    assert len(uids) == 30 and all(db.JOB_UID.match(u) for u in uids)
    assert not any(ch in u[4:] for u in uids for ch in "ILOU")                   # no look-alike letters


def test_old_jobs_get_a_job_id_on_upgrade(client):
    job = _job()
    with sqlite3.connect(config.DB_PATH) as c:
        c.execute("UPDATE jobs SET public_id=NULL WHERE id=?", (job["id"],))
    db.init()
    assert db.JOB_UID.match(db.get_job(job["id"])["uid"])


def test_api_and_pages_use_the_job_id(client):
    job = _job()
    by_uid = client.get(f"/api/jobs/{job['uid']}").json()
    assert by_uid["id"] == job["id"] and by_uid["uid"] == job["uid"]
    assert client.get(f"/api/jobs/{job['uid'].lower()}").status_code == 200
    assert client.get("/api/jobs/JOB-ZZZZZZ").status_code == 404
    assert client.get("/api/jobs/not-a-job").status_code == 404
    assert client.get(f"/api/jobs/{job['uid']}/records").json() == []
    # old number links move to the Job ID address, keeping the tab
    r = client.get(f"/results/{job['id']}?tab=quality")
    assert r.status_code == 302 and r.headers["location"] == f"/results/{job['uid']}?tab=quality"
    assert any(j["uid"] == job["uid"] for j in client.get("/api/jobs").json())


def test_downloads_and_activity_log_name_the_job_id(client):
    job = _job()
    r = client.get(f"/api/jobs/{job['uid']}/export?fmt=csv&name=Books")
    assert f'filename="Books-{job["uid"]}.csv"' in r.headers["content-disposition"]
    audit.log("job_started", job_id=job["id"], domain="example.com")
    entries = client.get("/api/audit?limit=20").json()
    assert any(e.get("job") == job["uid"] for e in entries if e["event"] == "job_started")


def test_saved_scrape_runs_answer_with_the_job_id(client):
    r = client.post("/api/scrapes", json={"name": "Books", "urls": ["https://example.com/list"], "recipe": RECIPE,
                                          "max_pages": 1, "run": False})
    assert r.status_code == 200 and r.json()["job_uid"] is None
    s = db.get_scrape(r.json()["scrape_id"])
    _job(scrape_id=s["id"])
    last = next(x for x in client.get("/api/scrapes").json() if x["id"] == s["id"])["last_run"]
    assert db.JOB_UID.match(last["uid"])
