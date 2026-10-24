"""Agent plan, phase A: AI provider errors named plainly, a backup AI for the agent, tokens per run."""
import sys

import pytest
from fastapi.testclient import TestClient

from app import ai_settings, config, db
from app.agent import runner as agent_runner, store as agent_store
from app.extraction import recipe_builder

sys.path.insert(0, str(config.ROOT / "agent_worker"))
import llm_errors  # noqa: E402  (the worker's own module; it has no browser-use imports)

GROQ_TPD = ("Rate limit reached for model `openai/gpt-oss-120b` in organization `org_x` service tier `on_demand` "
            "on tokens per day (TPD): Limit 200000, Used 198608, Requested 5375. Please try again in 28m40.656s.")


def test_wait_times_are_read_from_provider_messages():
    assert llm_errors.wait_seconds(GROQ_TPD) == pytest.approx(1720.656)
    assert llm_errors.wait_seconds("Please try again in 2.5s") == 2.5
    assert llm_errors.wait_seconds("try again in 1h2m") == 3720
    assert llm_errors.wait_seconds("try again in 850ms") == pytest.approx(0.85)
    assert llm_errors.wait_seconds("no hint here") is None


@pytest.mark.parametrize("text,status,kind,fatal,words", [
    (GROQ_TPD, 429, "daily_limit", True, "Groq's daily limit for openai/gpt-oss-120b is used up (it resets in about 29 min)"),
    ("You exceeded your current quota, please check your plan and billing details. insufficient_quota", 429,
     "quota", True, "no credit or quota left"),
    ("Incorrect API key provided", 401, "auth", True, "Check GROQ_API_KEY in .env"),
    ("The model `gpt-9` does not exist", 404, "model", True, "isn't available"),
    ("Rate limit reached ... tokens per minute (TPM) ... Please try again in 2.5s", 429, "rate_limit", False, "try again in 2 s"),
    ("Rate limit reached. Please try again in 5m0s", 429, "rate_limit", True, "resets in about 5 min"),
    ("Service unavailable", 503, "server", False, "server error"),
])
def test_provider_errors_are_classified(text, status, kind, fatal, words):
    p = llm_errors.classify(text, status, "groq", "openai/gpt-oss-120b")
    assert p["kind"] == kind and p["fatal"] is fatal and words in p["message"]


def test_other_errors_are_left_alone():
    assert llm_errors.classify("Could not parse the model's answer", 400, "openai", "gpt-5-mini") is None
    assert "Settings → AI models" in llm_errors.advice(False) and llm_errors.advice(True) == ""


@pytest.fixture
def client(monkeypatch):
    db.init()
    agent_store.init()
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk-secret-groq")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-secret-openai")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(config, "_ENV_CHOICE", {**config._ENV_CHOICE, "AI_PROVIDER": "openai", "OPENAI_MODEL": "gpt-5-mini",
                                                "GROQ_MODEL": "openai/gpt-oss-120b", "AGENT_PROVIDER": "groq",
                                                "AGENT_MODEL": "openai/gpt-oss-120b", "AGENT_FALLBACK_PROVIDER": "",
                                                "AGENT_FALLBACK_MODEL": ""})
    config.apply_ai_choice(None)
    checked, stops = [], []
    monkeypatch.setattr(recipe_builder, "check_model", lambda p, m: checked.append((p, m)))
    monkeypatch.setattr(agent_runner, "stop", lambda: stops.append(1))
    from app.main import app
    c = TestClient(app, base_url="http://localhost")
    c.checked, c.stops = checked, stops
    yield c
    config.apply_ai_choice(None)


AI = {"provider": "openai", "model": "gpt-5-mini"}
AGENT = {"provider": "groq", "model": "openai/gpt-oss-120b"}
BACKUP = {"provider": "openai", "model": "gpt-5-mini"}


def test_backup_ai_is_saved_checked_and_given_to_the_worker(client):
    assert client.get("/api/ai").json()["agent_fallback"] is None
    r = client.put("/api/ai", json={"ai": AI, "agent": AGENT, "agent_fallback": BACKUP})
    assert r.status_code == 200 and r.json()["agent_fallback"] == BACKUP
    assert client.checked == [("openai", "gpt-5-mini")]                        # a newly picked backup is checked
    assert client.stops == [1]               # the worker restarts with the backup
    env = agent_runner._env("http://localhost:8012")
    assert env["AGENT_FALLBACK_PROVIDER"] == "openai" and env["AGENT_FALLBACK_MODEL"] == "gpt-5-mini"
    assert env["AGENT_FALLBACK_API_KEY"] == "sk-secret-openai" and env["AGENT_API_KEY"] == "gsk-secret-groq"
    assert "secret" not in client.get("/api/ai").text and "secret" not in client.get("/api/status").text
    assert client.get("/api/status").json()["agent"]["fallback"] == BACKUP
    # survives a restart
    config.apply_ai_choice(None)
    config.apply_ai_choice(db.get_app_setting("ai"))
    assert config.agent_fallback() == BACKUP


def test_backup_rules(client):
    other = {"provider": "openai", "model": "gpt-4.1-mini"}
    assert client.put("/api/ai", json={"ai": AI, "agent": AGENT, "agent_fallback": AGENT}).status_code == 400
    client.put("/api/ai", json={"ai": AI, "agent": AGENT, "agent_fallback": other})
    assert client.checked == [("openai", "gpt-4.1-mini")]                       # a new model is checked
    r = client.put("/api/ai", json={"ai": AI, "agent": AGENT, "agent_fallback": None})
    assert r.json()["agent_fallback"] is None and "AGENT_FALLBACK_PROVIDER" not in agent_runner._env("http://x")
    no_key = {"provider": "anthropic", "model": "claude-sonnet-5-5"}
    assert client.put("/api/ai", json={"ai": AI, "agent": AGENT, "agent_fallback": no_key}).status_code == 400


def test_backup_cannot_change_during_an_agent_run(client, monkeypatch):
    monkeypatch.setattr(ai_settings, "_agent_busy", lambda: True)
    r = client.put("/api/ai", json={"ai": AI, "agent": AGENT, "agent_fallback": BACKUP})
    assert r.status_code == 409 and config.agent_fallback() is None
