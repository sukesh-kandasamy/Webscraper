"""Saved scrapes: create, list, run again, duplicate, delete; column-picked exports."""
import time

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import db
from app.fetching import fetcher
from app.security import vault

PUBLIC = "http://93.184.216.34"
PAGE = "<ul>" + "".join(f"<li class='c'><b>Item {i}</b><i>{i}0</i><a href='/p/{i}'>x</a></li>" for i in range(4)) + "</ul>"
RECIPE = {"item_selector": "li.c", "fields": [{"name": "name", "selector": "b"}, {"name": "price", "selector": "i"},
                                              {"name": "url", "selector": "a", "attr": "href"}]}


@pytest.fixture
def client(monkeypatch):
    vault.set_key_for_tests(Fernet.generate_key())
    db.init()
    monkeypatch.setattr(fetcher, "fetch_smart", lambda url, recipe=None, max_rounds=0:
                        fetcher.Page(url, url, 200, PAGE, False))
    monkeypatch.setattr(fetcher, "robots_allows", lambda url: True)
    from app.main import app
    yield TestClient(app, base_url="http://localhost")
    vault.set_key_for_tests(None)


def wait_done(client, job_id):
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_save_run_list_and_rerun(client):
    r = client.post("/api/scrapes", json={"name": "Test items", "urls": [PUBLIC + "/list"], "recipe": RECIPE,
                                          "max_pages": 1, "page_type": "ecommerce"}).json()
    sid, first = r["scrape_id"], r["job_id"]
    assert wait_done(client, first)["rows"] == 4
    listing = client.get("/api/scrapes").json()
    assert listing[0]["name"] == "Test items" and listing[0]["columns"] == ["name", "price", "url"]
    assert listing[0]["runs"] == 1 and listing[0]["last_run"]["status"] == "done"
    second = client.post(f"/api/scrapes/{sid}/run", json={}).json()["job_id"]
    wait_done(client, second)
    runs = client.get(f"/api/jobs?scrape_id={sid}").json()
    assert [j["id"] for j in runs] == [second, first]
    assert runs[0]["scrape_name"] == "Test items" and runs[0]["progress"].startswith("Finished: 4 rows")


def test_save_without_running_update_duplicate_delete(client):
    sid = client.post("/api/scrapes", json={"name": "Draft", "urls": [PUBLIC + "/l"], "recipe": RECIPE,
                                            "run": False}).json()["scrape_id"]
    assert client.get("/api/scrapes").json()[0]["last_run"] is None
    client.put(f"/api/scrapes/{sid}", json={"name": "Renamed", "max_pages": 5})
    assert client.get(f"/api/scrapes/{sid}").json()["name"] == "Renamed"
    copy = client.post(f"/api/scrapes/{sid}/duplicate").json()["scrape_id"]
    assert client.get(f"/api/scrapes/{copy}").json()["name"] == "Renamed (copy)"
    client.delete(f"/api/scrapes/{sid}")
    assert client.get(f"/api/scrapes/{sid}").status_code == 404


def test_export_picks_columns_and_names_the_file(client):
    started = client.post("/api/scrapes", json={"name": "My Items", "urls": [PUBLIC + "/l"], "recipe": RECIPE,
                                                "max_pages": 1}).json()
    job, uid = started["job_id"], started["job_uid"]
    wait_done(client, job)
    r = client.get(f"/api/jobs/{uid}/export?fmt=csv&columns=name,price&name=My Items")
    assert r.headers["content-disposition"].endswith(f'filename="My_Items-{uid}.csv"')
    r2 = client.get(f"/api/jobs/{uid}/export?fmt=csv&name=Products %26 prices – books.toscrape.com")
    assert r2.headers["content-disposition"].endswith(f'filename="Products_prices_books.toscrape.com-{uid}.csv"')
    assert r.content.decode("utf-8-sig").splitlines()[0] == "name,price"
