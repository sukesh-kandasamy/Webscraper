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

DEFAULT_FIELDS = ["name", "price", "unit", "min_order", "seller", "city", "rating", "url"]


def ai_prompt_budget() -> int:
    """Max characters of page HTML sent to the model. Groq's free tier allows 8,000 tokens/minute."""
    default = 11000 if AI_PROVIDER == "groq" else 60000
    return int(os.getenv("SCRAPER_AI_PROMPT_CHARS", default))


def ai_model() -> str:
    return GROQ_MODEL if AI_PROVIDER == "groq" else CLAUDE_MODEL


def ai_enabled() -> bool:
    return bool(GROQ_API_KEY if AI_PROVIDER == "groq" else ANTHROPIC_API_KEY)
