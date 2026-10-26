"""What an AI provider's error means for an agent run, in plain words.

Kept free of browser-use so the app's tests can check it too. classify() answers:
  kind   daily_limit | quota | auth | model | rate_limit | server
  fatal  True when retrying in this run can't help (a daily limit, no credit, a refused key, a missing
         model, or a wait longer than MAX_WAIT_S); browser-use then switches to the backup model, if one
         is set, or the run stops at once with the message instead of retrying.
"""
import re

PROVIDERS = {"groq": "Groq", "openai": "ChatGPT (OpenAI)", "anthropic": "Claude (Anthropic)"}
KEY_NAMES = {"groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
MAX_WAIT_S = 60

_WAIT = re.compile(r"(?:try again in|retry after|reset[s]? in)\s*((?:\d+(?:\.\d+)?\s*(?:h|ms|m|s)\s*)+)", re.I)
_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(h|ms|m|s)", re.I)


def wait_seconds(text: str) -> float | None:
    """'Please try again in 28m40.656s' -> 1720.656"""
    m = _WAIT.search(text or "")
    if not m:
        return None
    unit = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}
    return sum(float(n) * unit[u.lower()] for n, u in _PART.findall(m.group(1)))


def _when(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds >= 5400:
        return f" (it resets in about {round(seconds / 3600)} h)"
    if seconds >= 90:
        return f" (it resets in about {round(seconds / 60)} min)"
    return f" (try again in {max(1, round(seconds))} s)"


def classify(text: str, status: int | None, provider: str, model: str) -> dict | None:
    t = (text or "").lower()
    name = PROVIDERS.get(provider, provider)
    wait = wait_seconds(text)
    if re.search(r"per day|\(tpd\)|\(rpd\)|daily", t):
        return {"kind": "daily_limit", "fatal": True, "wait_s": wait,
                "message": f"{name}'s daily limit for {model} is used up{_when(wait)}."}
    if status == 402 or re.search(r"insufficient_quota|exceeded your current quota|credit balance|billing", t):
        return {"kind": "quota", "fatal": True, "wait_s": None,
                "message": f"The {name} account has no credit or quota left."}
    if status in (401, 403) or re.search(r"invalid[_ ]api[_ ]key|incorrect api key|authentication|unauthorized", t):
        return {"kind": "auth", "fatal": True, "wait_s": None,
                "message": f"{name} refused the key. Check {KEY_NAMES.get(provider, 'the key')} in .env."}
    if status == 404 or re.search(r"model_not_found|does not exist|decommissioned|no such model|unknown model", t):
        return {"kind": "model", "fatal": True, "wait_s": None,
                "message": f"The model {model} isn't available on {name}. Pick another one in Settings → AI models."}
    if status == 429 or re.search(r"rate limit|rate_limit|too many requests", t):
        long_wait = wait is not None and wait > MAX_WAIT_S
        return {"kind": "rate_limit", "fatal": long_wait, "wait_s": wait,
                "message": f"{name} is rate-limiting {model}{_when(wait)}."}
    if status and status >= 500:
        return {"kind": "server", "fatal": False, "wait_s": wait, "message": f"{name} had a server error."}
    return None


def advice(has_backup: bool) -> str:
    return ("" if has_backup else
            " Pick another AI for the agent, or set a backup for it, in Settings → AI models.")
