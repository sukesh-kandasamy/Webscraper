"""Which AI the app uses, chosen in Settings: one provider and model for building scrapes and plans,
one for the agent that drives the browser. The choice is saved in the database; .env holds the keys
and the defaults. Keys never reach the page, only whether each one is set.

Saving sends one tiny test request to each newly picked model, so a model that can't give the
structured answers this app needs is refused right away instead of failing in the middle of a run.
"""
import re
import time

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import config, db
from .agent import runner as agent_runner, store as agent_store
from .extraction import recipe_builder
from .security import audit, logsafe

router = APIRouter(prefix="/api/ai")

MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")
# Shown first, and used when the provider's model list can't be loaded (offline, key rejected).
SUGGESTED = {
    "groq": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
    "openai": ["gpt-5-mini", "gpt-5", "gpt-5-nano", "gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini"],
    "anthropic": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
}
# Models that are not chat models, or can't give structured answers through Chat Completions.
_SKIP = {
    "groq": re.compile(r"whisper|orpheus|guard|tts|playai|distil", re.I),
    "openai": re.compile(r"\d{4}-\d{2}-\d{2}$|search|transcribe|tts|audio|realtime|image|codex|-pro\b|"
                         r"chat-latest|diarize", re.I),
    "anthropic": re.compile(r"(?!)"),
}
_OPENAI_CHAT = re.compile(r"^(gpt-4o|gpt-4\.1|gpt-5|o3|o4)")
_LIST_TTL_S = 600
_lists: dict[str, tuple[float, list[str]]] = {}


class Choice(BaseModel):
    provider: str = Field(pattern="^(groq|openai|anthropic)$")
    model: str = Field(min_length=1, max_length=100)


class ChoiceIn(BaseModel):
    ai: Choice
    agent: Choice


def _agent_busy() -> bool:
    return any(r["status"] in agent_store.ACTIVE for r in agent_store.list_runs(50))


def view() -> dict:
    return {"ai": {"provider": config.AI_PROVIDER, "model": config.ai_model()},
            "agent": {"provider": config.AGENT_PROVIDER, "model": config.AGENT_MODEL, "vision": config.agent_vision()},
            "providers": [{"id": p, "name": name, "key_set": config.key_set(p), "key_name": config.KEY_NAMES[p]}
                          for p, name in config.PROVIDER_NAMES.items()],
            "custom": db.get_app_setting("ai") is not None, "agent_busy": _agent_busy()}


def _fetch_models(provider: str) -> list[str]:
    if provider == "anthropic":
        r = httpx.get("https://api.anthropic.com/v1/models?limit=100", timeout=15,
                      headers={"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"})
    else:
        base = "https://api.groq.com/openai/v1" if provider == "groq" else config.OPENAI_BASE_URL
        key = config.GROQ_API_KEY if provider == "groq" else config.OPENAI_API_KEY
        r = httpx.get(f"{base}/models", timeout=15, headers={"Authorization": f"Bearer {key}"})
    r.raise_for_status()
    ids = [m["id"] for m in r.json().get("data", []) if isinstance(m.get("id"), str)]
    ids = [i for i in ids if MODEL_NAME.match(i) and not _SKIP[provider].search(i)
           and (provider != "openai" or _OPENAI_CHAT.match(i))]
    return sorted(ids)


def models(provider: str) -> tuple[list[str], bool]:
    """The provider's chat models (suggested ones first) and whether the list came from the provider."""
    hit = _lists.get(provider)
    live = True
    if hit and time.time() - hit[0] < _LIST_TTL_S:
        ids = hit[1]
    else:
        try:
            ids = _fetch_models(provider)
            _lists[provider] = (time.time(), ids)
        except (httpx.HTTPError, ValueError, KeyError):
            ids, live = [], False
    first = [m for m in SUGGESTED[provider] if m in ids or not live]
    return first + [m for m in ids if m not in first], live


@router.get("")
def get_choice():
    return view()


@router.get("/models/{provider}")
def get_models(provider: str):
    if provider not in config.PROVIDER_NAMES:
        raise HTTPException(404, "Unknown AI provider.")
    if not config.key_set(provider):
        return {"models": SUGGESTED[provider], "live": False, "suggested": SUGGESTED[provider]}
    ids, live = models(provider)
    return {"models": ids, "live": live, "suggested": SUGGESTED[provider]}


def _check(role: str, c: Choice) -> None:
    name = config.PROVIDER_NAMES[c.provider]
    if not config.key_set(c.provider):
        raise HTTPException(400, f"{name} needs {config.KEY_NAMES[c.provider]} in .env before you can pick it.")
    if not MODEL_NAME.match(c.model):
        raise HTTPException(400, "That model name has characters a model name can't have.")
    try:
        recipe_builder.check_model(c.provider, c.model)
    except recipe_builder.RecipeBuildError as e:
        raise HTTPException(400, f"{name} · {c.model} didn't work for {role}: {logsafe.redact(str(e))}") from e


def _save(choice: dict | None) -> dict:
    before = (config.AGENT_PROVIDER, config.AGENT_MODEL)
    config.apply_ai_choice(choice)
    agent_changed = (config.AGENT_PROVIDER, config.AGENT_MODEL) != before
    if agent_changed and _agent_busy():
        config.apply_ai_choice(db.get_app_setting("ai"))          # undo: keep what the running agent uses
        raise HTTPException(409, "An agent run is in progress. Change the agent's AI after it finishes.")
    db.set_app_setting("ai", choice)
    if agent_changed:
        agent_runner.stop()                                       # the next run starts it with the new AI
    audit.log("ai_settings_changed", provider=config.AI_PROVIDER, model=config.ai_model(),
              agent_provider=config.AGENT_PROVIDER, agent_model=config.AGENT_MODEL, reset=choice is None)
    return view()


@router.put("")
def put_choice(body: ChoiceIn):
    ai_changed = (body.ai.provider, body.ai.model) != (config.AI_PROVIDER, config.ai_model())
    agent_changed = (body.agent.provider, body.agent.model) != (config.AGENT_PROVIDER, config.AGENT_MODEL)
    if agent_changed and _agent_busy():
        raise HTTPException(409, "An agent run is in progress. Change the agent's AI after it finishes.")
    if ai_changed:
        _check("building scrapes", body.ai)
    # The agent needs its own check unless it uses the very model that was just checked.
    if agent_changed and not (ai_changed and body.agent == body.ai):
        _check("the agent", body.agent)
    return _save(body.model_dump())


@router.delete("")
def reset_choice():
    """Go back to the provider and models set in .env."""
    return _save(None)
