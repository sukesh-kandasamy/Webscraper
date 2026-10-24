"""Agent history: rename a run, delete a run (not while it is working)."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.agent import store
from app.security import audit


@pytest.fixture
def client():
    db.init()
    store.init()
    from app.main import app
    return TestClient(app, base_url="http://localhost")


def _run(**fields):
    run_id = store.create("Collect hotels in Goa", "booking.com", "https://www.booking.com/",
                          {"summary": "Hotels in Goa, 25–28 Nov"}, {})
    if fields:
        store.update(run_id, **fields)
    return store.get(run_id)


def test_rename_and_back_to_the_plans_summary(client):
    run = _run()
    r = client.put(f"/api/agent/runs/{run['uid']}/title", json={"title": "  Goa   hotels  for  Nov  "})
    assert r.status_code == 200 and r.json()["title"] == "Goa hotels for Nov"
    assert client.get(f"/api/agent/runs/{run['uid']}").json()["title"] == "Goa hotels for Nov"
    assert any(x["title"] == "Goa hotels for Nov" for x in client.get("/api/agent/runs").json())
    assert client.put(f"/api/agent/runs/{run['uid']}/title", json={"title": ""}).json()["title"] == ""
    assert client.put(f"/api/agent/runs/{run['uid']}/title", json={"title": "x" * 201}).status_code == 422
    assert client.put("/api/agent/runs/RUN-ZZZZZZ/title", json={"title": "a"}).status_code == 404


def test_delete_a_finished_run_but_not_a_working_one(client):
    working = _run(status="navigating")
    r = client.delete(f"/api/agent/runs/{working['uid']}")
    assert r.status_code == 409 and store.get(working["id"])
    done = _run(status="done")
    assert client.delete(f"/api/agent/runs/{done['uid']}").status_code == 200
    assert store.get(done["id"]) is None
    assert client.get(f"/api/agent/runs/{done['uid']}").status_code == 404
    assert any(e["event"] == "agent_run_deleted" and e.get("run") == done["uid"] for e in audit.tail(20))
