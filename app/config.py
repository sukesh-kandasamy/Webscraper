"""App settings. Override any of these with environment variables or a .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = Path(os.getenv("SCRAPER_DB", DATA_DIR / "scraper.db"))
BUILTIN_RECIPES_DIR = ROOT / "app" / "recipes"

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

# AI recipe builder (optional). Provider: "groq" or "anthropic".
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("SCRAPER_GROQ_MODEL", "openai/gpt-oss-120b")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("SCRAPER_CLAUDE_MODEL", "claude-opus-5-5")
AI_PROVIDER = os.getenv("SCRAPER_AI_PROVIDER", "groq" if GROQ_API_KEY else "anthropic").lower()

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
    return GROQ_MODEL if AI_PROVIDER == "groq" else CLAUDE_MODEL


def ai_enabled() -> bool:
    return bool(GROQ_API_KEY if AI_PROVIDER == "groq" else ANTHROPIC_API_KEY)


# ---- Agent mode (browser-use, in its own environment: .venv-agent) -----------------------
AGENT_ENABLED = os.getenv("SCRAPER_AGENT", "1") != "0"
AGENT_PYTHON = Path(os.getenv("SCRAPER_AGENT_PYTHON", ROOT / ".venv-agent" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")))
AGENT_PROVIDER = os.getenv("SCRAPER_AGENT_PROVIDER", "anthropic" if ANTHROPIC_API_KEY else "groq").lower()
AGENT_MODEL = os.getenv("SCRAPER_AGENT_MODEL", "claude-sonnet-5-5" if AGENT_PROVIDER == "anthropic" else GROQ_MODEL)
AGENT_MAX_STEPS = int(os.getenv("SCRAPER_AGENT_MAX_STEPS", "40"))
AGENT_MAX_MINUTES = float(os.getenv("SCRAPER_AGENT_MAX_MINUTES", "10"))
AGENT_MAX_AI_CALLS = int(os.getenv("SCRAPER_AGENT_MAX_AI_CALLS", "60"))
AGENT_SHOTS_DIR = DATA_DIR / "agent_shots"


def agent_key() -> str:
    return ANTHROPIC_API_KEY if AGENT_PROVIDER == "anthropic" else GROQ_API_KEY


def agent_vision() -> bool:
    """Models that can see screenshots. gpt-oss on Groq is text-only."""
    return AGENT_PROVIDER == "anthropic"
