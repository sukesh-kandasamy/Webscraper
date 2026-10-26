"""App settings. Override any of these with environment variables or a .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("SCRAPER_DATA_DIR", ROOT / "data"))      # a separate folder for test copies of the app
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.getenv("SCRAPER_DB", DATA_DIR / "scraper.db"))
BUILTIN_RECIPES_DIR = ROOT / "app" / "recipes"
STATIC_DIR = ROOT / "frontend" / "public"      # the UI, served at /static
TEMPLATES_DIR = ROOT / "frontend" / "templates"   # one template per page

# Politeness
MIN_DELAY = float(os.getenv("SCRAPER_MIN_DELAY", "2"))
MAX_DELAY = float(os.getenv("SCRAPER_MAX_DELAY", "5"))
MAX_PAGES_PER_URL = int(os.getenv("SCRAPER_MAX_PAGES", "10"))
MAX_CONCURRENT_JOBS = int(os.getenv("SCRAPER_MAX_JOBS", "2"))
CACHE_TTL_HOURS = float(os.getenv("SCRAPER_CACHE_TTL_HOURS", "24"))
RESPECT_ROBOTS = os.getenv("SCRAPER_RESPECT_ROBOTS", "1") != "0"
REQUEST_TIMEOUT = float(os.getenv("SCRAPER_TIMEOUT", "30"))
USER_AGENT = os.getenv(
    "SCRAPER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
)

# AI recipe builder (optional). Provider: "groq", "anthropic" or "openai".
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("SCRAPER_GROQ_MODEL", "openai/gpt-oss-120b")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("SCRAPER_CLAUDE_MODEL", "claude-opus-5-5")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("SCRAPER_OPENAI_MODEL", "gpt-5-mini")
OPENAI_BASE_URL = os.getenv("SCRAPER_OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
AI_PROVIDER = os.getenv("SCRAPER_AI_PROVIDER", "groq" if GROQ_API_KEY else "openai" if OPENAI_API_KEY
                        else "anthropic").lower()
_KEYS = {"groq": lambda: GROQ_API_KEY, "anthropic": lambda: ANTHROPIC_API_KEY, "openai": lambda: OPENAI_API_KEY}
_MODELS = {"groq": lambda: GROQ_MODEL, "anthropic": lambda: CLAUDE_MODEL, "openai": lambda: OPENAI_MODEL}
KEY_NAMES = {"groq": "GROQ_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
PROVIDER_NAMES = {"groq": "Groq", "openai": "ChatGPT (OpenAI)", "anthropic": "Claude (Anthropic)"}

# Columns pre-filled in the UI. Empty: the AI picks the important columns for each page itself.
DEFAULT_FIELDS: list[str] = []


def _csv(name: str, default: str = "") -> set[str]:
    return {x.strip().lower() for x in os.getenv(name, default).split(",") if x.strip()}


# ---- Security ---------------------------------------------------------------
# Hosts the scraper may fetch even though they resolve to a local / private address.
NET_ALLOW_HOSTS = _csv("SCRAPER_ALLOW_HOSTS")
# Host headers the web UI answers to (blocks DNS-rebinding attacks from other websites).
ALLOWED_HOSTS = sorted(_csv("SCRAPER_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]"))
# Domains that must never be fetched (opt-out list), in addition to per-site "blocked".
BLOCKED_DOMAINS = _csv("SCRAPER_BLOCKED_DOMAINS")
MAX_PAGE_BYTES = int(os.getenv("SCRAPER_MAX_PAGE_BYTES", str(10 * 1024 * 1024)))
MAX_ITEMS_PER_PAGE = int(os.getenv("SCRAPER_MAX_ITEMS_PER_PAGE", "2000"))
REGEX_TIMEOUT_S = float(os.getenv("SCRAPER_REGEX_TIMEOUT", "0.05"))
MAX_REDIRECTS = 5
MAX_RETRY_AFTER_S = float(os.getenv("SCRAPER_MAX_RETRY_AFTER", "120"))

# Per-site daily page budgets and cool-downs
DAILY_BUDGET = int(os.getenv("SCRAPER_DAILY_BUDGET", "500"))
DAILY_BUDGET_LOGGED_IN = int(os.getenv("SCRAPER_DAILY_BUDGET_LOGGED_IN", "200"))
COOLDOWN_MINUTES = float(os.getenv("SCRAPER_COOLDOWN_MINUTES", "60"))
CHALLENGE_REPEAT_MINUTES = float(os.getenv("SCRAPER_CHALLENGE_REPEAT_MINUTES", "10"))

# Login sessions
SESSIONS_DIR = DATA_DIR / "sessions"
SESSION_MAX_AGE_DAYS = float(os.getenv("SCRAPER_SESSION_MAX_AGE_DAYS", "7"))
INTERACTIVE_TIMEOUT_S = int(os.getenv("SCRAPER_INTERACTIVE_TIMEOUT", "900"))

# Data retention
JOB_RETENTION_DAYS = float(os.getenv("SCRAPER_JOB_RETENTION_DAYS", "30"))
PURGE_INTERVAL_S = 3600

# Optional honest identification, e.g. "you@example.com" -> "UniversalScraper/0.2 (+you@example.com)"
CONTACT = os.getenv("SCRAPER_CONTACT", "")
HONEST_USER_AGENT = f"UniversalScraper/0.2 (+{CONTACT})" if CONTACT else "UniversalScraper/0.2"


def ai_prompt_budget() -> int:
    """Max characters of the whole AI prompt. Groq's free tier allows about 8,000 tokens per request."""
    default = 11000 if AI_PROVIDER == "groq" else 60000
    return int(os.getenv("SCRAPER_AI_PROMPT_CHARS", default))


def ai_model() -> str:
    return _MODELS.get(AI_PROVIDER, _MODELS["anthropic"])()


def ai_enabled() -> bool:
    return bool(_KEYS.get(AI_PROVIDER, _KEYS["anthropic"])())


def ai_key_name() -> str:
    return KEY_NAMES.get(AI_PROVIDER, "ANTHROPIC_API_KEY")


# ---- Agent mode (browser-use, in its own environment: .venv-agent) -----------------------
AGENT_ENABLED = os.getenv("SCRAPER_AGENT", "1") != "0"
AGENT_PYTHON = Path(os.getenv("SCRAPER_AGENT_PYTHON", ROOT / ".venv-agent" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")))
# The agent drives a browser step by step: a model that sees screenshots works best (Claude, OpenAI).
AGENT_PROVIDER = os.getenv("SCRAPER_AGENT_PROVIDER", "anthropic" if ANTHROPIC_API_KEY else "openai" if OPENAI_API_KEY
                           else "groq").lower()
AGENT_MODEL = os.getenv("SCRAPER_AGENT_MODEL", {"anthropic": "claude-sonnet-5-5", "openai": OPENAI_MODEL}
                        .get(AGENT_PROVIDER, GROQ_MODEL))
# Backup AI for the agent: used when its own AI hits a rate or daily limit, has no credit or refuses the key.
AGENT_FALLBACK_PROVIDER = os.getenv("SCRAPER_AGENT_FALLBACK_PROVIDER", "").lower()
AGENT_FALLBACK_MODEL = os.getenv("SCRAPER_AGENT_FALLBACK_MODEL", "")
# Which brain runs an agent run: "react" (the loop in the app: think, act with a tool, observe) or "browser"
# (the earlier browser-use-driven path, kept until the new one covers everything).
AGENT_ENGINE = os.getenv("SCRAPER_AGENT_ENGINE", "react").lower()
AGENT_MAX_STEPS = int(os.getenv("SCRAPER_AGENT_MAX_STEPS", "40"))
AGENT_MAX_MINUTES = float(os.getenv("SCRAPER_AGENT_MAX_MINUTES", "10"))
AGENT_MAX_AI_CALLS = int(os.getenv("SCRAPER_AGENT_MAX_AI_CALLS", "60"))
AGENT_SHOTS_DIR = DATA_DIR / "agent_shots"


def agent_key() -> str:
    return _KEYS.get(AGENT_PROVIDER, _KEYS["groq"])()


def agent_prompt_budget() -> int:
    """Max characters of one page summary shown to the agent's AI (Groq's free tier is the tight one)."""
    default = 3000 if AGENT_PROVIDER == "groq" else 7000
    return int(os.getenv("SCRAPER_AGENT_PROMPT_CHARS", default))


def agent_vision() -> bool:
    """Models that can see screenshots. gpt-oss on Groq is text-only."""
    return AGENT_PROVIDER in ("anthropic", "openai")


def key_set(provider: str) -> bool:
    return bool(_KEYS[provider]()) if provider in _KEYS else False


def agent_fallback() -> dict | None:
    """The agent's backup AI, when one is chosen and its key is set."""
    if AGENT_FALLBACK_PROVIDER in _KEYS and AGENT_FALLBACK_MODEL and key_set(AGENT_FALLBACK_PROVIDER):
        return {"provider": AGENT_FALLBACK_PROVIDER, "model": AGENT_FALLBACK_MODEL}
    return None


# ---- AI choice made in Settings (saved in the database; .env holds the keys and the defaults) ----
_ENV_CHOICE = {"AI_PROVIDER": AI_PROVIDER, "GROQ_MODEL": GROQ_MODEL, "OPENAI_MODEL": OPENAI_MODEL,
               "CLAUDE_MODEL": CLAUDE_MODEL, "AGENT_PROVIDER": AGENT_PROVIDER, "AGENT_MODEL": AGENT_MODEL,
               "AGENT_FALLBACK_PROVIDER": AGENT_FALLBACK_PROVIDER, "AGENT_FALLBACK_MODEL": AGENT_FALLBACK_MODEL}
_MODEL_VAR = {"groq": "GROQ_MODEL", "openai": "OPENAI_MODEL", "anthropic": "CLAUDE_MODEL"}


def default_agent_model(provider: str) -> str:
    if provider == _ENV_CHOICE["AGENT_PROVIDER"]:
        return _ENV_CHOICE["AGENT_MODEL"]
    return {"anthropic": "claude-sonnet-5-5"}.get(provider) or _ENV_CHOICE[_MODEL_VAR[provider]]


def apply_ai_choice(choice: dict | None) -> None:
    """Use the provider and model picked in Settings for building scrapes ("ai") and for the agent
    ("agent"), e.g. {"ai": {"provider": "groq", "model": "openai/gpt-oss-120b"}, "agent": {...}}.
    Anything missing falls back to the .env defaults."""
    g = globals()
    g.update(_ENV_CHOICE)
    ai, agent = (choice or {}).get("ai") or {}, (choice or {}).get("agent") or {}
    if ai.get("provider") in _MODEL_VAR:
        g["AI_PROVIDER"] = ai["provider"]
        if ai.get("model"):
            g[_MODEL_VAR[ai["provider"]]] = ai["model"]
    if agent.get("provider") in _MODEL_VAR:
        g["AGENT_PROVIDER"] = agent["provider"]
        g["AGENT_MODEL"] = agent.get("model") or default_agent_model(agent["provider"])
    if "agent_fallback" in (choice or {}):                 # None = no backup
        backup = choice["agent_fallback"] or {}
        ok = backup.get("provider") in _MODEL_VAR and backup.get("model")
        g["AGENT_FALLBACK_PROVIDER"] = backup["provider"] if ok else ""
        g["AGENT_FALLBACK_MODEL"] = backup["model"] if ok else ""
