"""Choosing the AI provider and model in Settings: saved, checked, applied, and keys never shown."""
import pytest
from fastapi.testclient import TestClient

from app import ai_settings, config, db
from app.agent import runner as agent_runner, store as agent_store
from app.extraction import recipe_builder


@pytest.fixture
def client(monkeypatch):
    db.init()
    agent_store.init()
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk-secret-groq")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-secret-openai")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(config, "_ENV_CHOICE", {**config._ENV_CHOICE, "AI_PROVIDER": "openai", "OPENAI_MODEL": "gpt-5-mini",
                                                "GROQ_MODEL": "openai/gpt-oss-120b", "AGENT_PROVIDER": "openai",
                                                "AGENT_MODEL": "gpt-5-mini"})
    config.apply_ai_choice(None)
    checked = []
    monkeypatch.setattr(recipe_builder, "check_model", lambda p, m: checked.append((p, m)))
    monkeypatch.setattr(ai_settings, "_fetch_models", lambda p: ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]
                        if p == "groq" else ["gpt-4o", "gpt-5", "gpt-5-mini"])
    stops = []
    monkeypatch.setattr(agent_runner, "stop", lambda: stops.append(1))
    ai_settings._lists.clear()
    from app.main import app
    c = TestClient(app, base_url="http://localhost")
    c.checked, c.stops = checked, stops
    yield c
    config.apply_ai_choice(None)


def test_view_shows_which_keys_are_set_but_never_the_keys(client):
    r = client.get("/api/ai")
    v = r.json()
    assert v["ai"] == {"provider": "openai", "model": "gpt-5-mini"} and not v["custom"]
    assert {p["id"]: p["key_set"] for p in v["providers"]} == {"groq": True, "openai": True, "anthropic": False}
    assert "secret" not in r.text


def test_model_lists_put_suggested_models_first(client):
    m = client.get("/api/ai/models/groq").json()
    assert m["live"] and m["models"][:2] == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    assert client.get("/api/ai/models/openai").json()["models"][0] == "gpt-5-mini"
    assert client.get("/api/ai/models/anthropic").json()["live"] is False        # no key: suggestions only
    assert client.get("/api/ai/models/nope").status_code == 404


def test_saving_checks_the_new_models_and_applies_them(client):
    r = client.put("/api/ai", json={"ai": {"provider": "groq", "model": "openai/gpt-oss-20b"},
                                    "agent": {"provider": "groq", "model": "openai/gpt-oss-20b"}})
    assert r.status_code == 200 and r.json()["custom"]
    assert client.checked == [("groq", "openai/gpt-oss-20b")]                   # same model: checked once
    assert (config.AI_PROVIDER, config.ai_model()) == ("groq", "openai/gpt-oss-20b")
    assert (config.AGENT_PROVIDER, config.AGENT_MODEL) == ("groq", "openai/gpt-oss-20b")
    assert client.stops == [1]                                                  # worker restarts with the new AI
    assert client.get("/api/status").json()["provider"] == "groq"
    # survives a restart: the saved choice is applied again at startup
    config.apply_ai_choice(None)
    config.apply_ai_choice(db.get_app_setting("ai"))
    assert config.ai_model() == "openai/gpt-oss-20b"


def test_reset_goes_back_to_env(client):
    client.put("/api/ai", json={"ai": {"provider": "groq", "model": "openai/gpt-oss-120b"},
                                "agent": {"provider": "openai", "model": "gpt-5-mini"}})
    assert client.stops == []                                                   # agent unchanged: no restart
    v = client.delete("/api/ai").json()
    assert v["ai"] == {"provider": "openai", "model": "gpt-5-mini"} and not v["custom"]
    assert db.get_app_setting("ai") is None


def test_refusals(client, monkeypatch):
    agent = {"provider": "openai", "model": "gpt-5-mini"}
    r = client.put("/api/ai", json={"ai": {"provider": "anthropic", "model": "claude-sonnet-5-5"}, "agent": agent})
    assert r.status_code == 400 and "ANTHROPIC_API_KEY" in r.json()["detail"]
    r = client.put("/api/ai", json={"ai": {"provider": "openai", "model": "gpt 5; rm"}, "agent": agent})
    assert r.status_code == 400
    assert client.put("/api/ai", json={"ai": {"provider": "other", "model": "x"}, "agent": agent}).status_code == 422

    def broken(p, m):
        raise recipe_builder.RecipeBuildError("`max_completion_tokens` must be less than or equal to `4096`")
    monkeypatch.setattr(recipe_builder, "check_model", broken)
    r = client.put("/api/ai", json={"ai": {"provider": "groq", "model": "allam-2-7b"}, "agent": agent})
    assert r.status_code == 400 and "allam-2-7b didn't work" in r.json()["detail"]
    assert config.AI_PROVIDER == "openai" and db.get_app_setting("ai") is None  # nothing saved


def test_agent_ai_cannot_change_during_a_run(client, monkeypatch):
    monkeypatch.setattr(ai_settings, "_agent_busy", lambda: True)
    r = client.put("/api/ai", json={"ai": {"provider": "openai", "model": "gpt-5-mini"},
                                    "agent": {"provider": "groq", "model": "openai/gpt-oss-120b"}})
    assert r.status_code == 409 and config.AGENT_PROVIDER == "openai"
    ok = client.put("/api/ai", json={"ai": {"provider": "groq", "model": "openai/gpt-oss-120b"},
                                     "agent": {"provider": "openai", "model": "gpt-5-mini"}})
    assert ok.status_code == 200                                                # the scrape-building AI still can


def test_groq_only_sends_gpt_oss_options_to_gpt_oss(monkeypatch):
    sent = []

    class Groq:
        def __init__(self, api_key): pass

        @property
        def chat(self): return self

        @property
        def completions(self): return self

        def create(self, **kw):
            sent.append(kw)
            msg = type("M", (), {"content": '{"ok": true}'})
            return type("R", (), {"choices": [type("C", (), {"message": msg})]})

    import groq
    monkeypatch.setattr(groq, "Groq", Groq)
    recipe_builder._ask_groq([], {}, model="openai/gpt-oss-20b")
    recipe_builder._ask_groq([], {}, model="qwen/qwen3.8-27b")
    assert sent[0]["reasoning_effort"] == "medium" and sent[0]["response_format"]["json_schema"]["strict"]
    assert "reasoning_effort" not in sent[1] and not sent[1]["response_format"]["json_schema"]["strict"]
