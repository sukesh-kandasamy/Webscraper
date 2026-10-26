"""Editing collected values in Results: one cell at a time, typed like its column, used everywhere."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.agent import store as agent_store
from app.security import audit

RECIPE = {"item_selector": "li", "fields": [{"name": "title", "selector": "a"}, {"name": "price", "selector": ".p"}]}


@pytest.fixture
def client():
    db.init()
    agent_store.init()
    from app.main import app
    return TestClient(app, base_url="http://localhost")


def _job(rows, status="done", **kw):
    job_id = db.create_job(["https://books.example/list"], RECIPE, 1, **kw)
    db.add_records(job_id, "https://books.example/list", rows)
    db.update_job(job_id, status=status, rows=len(rows), columns='{"price": "money"}')
    return db.get_job(job_id)


def test_rows_come_with_ids_only_for_the_table(client):
    job = _job([{"title": "The Requiem Red", "price": 22.65}])
    plain = client.get(f"/api/jobs/{job['uid']}/records").json()
    assert plain == [{"title": "The Requiem Red", "price": 22.65}]
    rows = client.get(f"/api/jobs/{job['uid']}/records?ids=1").json()
    assert rows[0]["title"] == "The Requiem Red" and isinstance(rows[0]["_id"], int) and rows[0]["_edited"] == []


def test_edit_a_value_and_it_is_used_everywhere(client):
    job = _job([{"title": "The Requiem Red", "price": 22.65}, {"title": "Sharp Objects", "price": 47.82}])
    rid = client.get(f"/api/jobs/{job['uid']}/records?ids=1").json()[0]["_id"]
    r = client.patch(f"/api/jobs/{job['uid']}/records/{rid}", json={"column": "title", "value": "  The Requiem Green "})
    assert r.status_code == 200 and r.json()["title"] == "The Requiem Green" and r.json()["_edited"] == ["title"]
    rows = client.get(f"/api/jobs/{job['uid']}/records?ids=1").json()
    assert [x["title"] for x in rows] == ["The Requiem Green", "Sharp Objects"]          # order kept
    assert "The Requiem Green" in client.get(f"/api/jobs/{job['uid']}/export?fmt=csv").text
    # a money column keeps a number
    r = client.patch(f"/api/jobs/{job['uid']}/records/{rid}", json={"column": "price", "value": "£ 1,234.50"})
    assert r.json()["price"] == 1234.5 and r.json()["_edited"] == ["title", "price"]
    assert any(e["event"] == "record_edited" and e.get("job") == job["uid"] for e in audit.tail(20))


def test_edits_that_are_refused(client):
    job = _job([{"title": "A", "price": 1}])
    rid = client.get(f"/api/jobs/{job['uid']}/records?ids=1").json()[0]["_id"]
    url = f"/api/jobs/{job['uid']}/records/{rid}"
    assert client.patch(url, json={"column": "nope", "value": "x"}).status_code == 400
    assert client.patch(f"/api/jobs/{job['uid']}/records/999999", json={"column": "title", "value": "x"}).status_code == 404
    running = _job([{"title": "B", "price": 2}], status="running")
    rrid = client.get(f"/api/jobs/{running['uid']}/records?ids=1").json()[0]["_id"]
    assert client.patch(f"/api/jobs/{running['uid']}/records/{rrid}", json={"column": "title", "value": "x"}).status_code == 409


def test_hidden_personal_data_is_not_edited_blind(client):
    job = _job([{"title": "Shop", "price": 1, "contact": "Call 98765 43210"}])
    rid = client.get(f"/api/jobs/{job['uid']}/records?ids=1").json()[0]["_id"]
    url = f"/api/jobs/{job['uid']}/records/{rid}"
    r = client.patch(url, json={"column": "contact", "value": "x"})
    assert r.status_code == 409 and "Show personal data" in r.json()["detail"]
    with sqlite3.connect(config.DB_PATH) as c:                     # opted in to collect it, with a purpose
        c.execute("UPDATE jobs SET personal=? WHERE id=?", ('{"fields": ["contact"], "purpose": "call the shops back"}', job["id"]))
    r = client.patch(url, json={"column": "contact", "value": "Call 90000 00000", "show_personal": True})
    assert r.status_code == 200 and r.json()["contact"] == "Call 90000 00000"


def test_rows_of_logged_in_runs_stay_encrypted(client):
    job = _job([{"title": "Members only", "price": 5}], logged_in=True)
    rid = client.get(f"/api/jobs/{job['uid']}/records?ids=1").json()[0]["_id"]
    client.patch(f"/api/jobs/{job['uid']}/records/{rid}", json={"column": "title", "value": "Members only (renamed)"})
    with sqlite3.connect(config.DB_PATH) as c:
        stored = c.execute("SELECT data FROM records WHERE id=?", (rid,)).fetchone()[0]
    assert stored.startswith("enc:") and "renamed" not in stored
    assert db.get_records(job["id"])[0]["title"] == "Members only (renamed)"
