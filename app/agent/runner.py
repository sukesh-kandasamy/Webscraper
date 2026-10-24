"""Starts and watches the agent worker: a separate process in its own environment (.venv-agent).

browser-use brings ~70 packages (and would downgrade groq), so it never loads in the app's process.
The worker talks to the app only through the app's local API, with a random token made at startup.
It gets a minimal environment: the agent's AI key, the app address, the token, telemetry off.
"""
import os
import secrets
import subprocess
import threading
import time

from .. import config
from ..fetching import browser

TOKEN = secrets.token_urlsafe(32)
WORKER = config.ROOT / "agent_worker" / "worker.py"
LOG = config.DATA_DIR / "agent_worker.log"

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
last_app_url = ""      # the app's own address, so the supervisor can restart the worker
_last_seen = 0.0
# Variables Windows / Chromium need to start at all; nothing else from the app's environment.
_KEEP_ENV = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA",
             "APPDATA", "HOMEDRIVE", "HOMEPATH", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA",
             "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "HOME", "LANG",
             "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")


def installed() -> bool:
    return config.AGENT_PYTHON.exists() and WORKER.exists()


def installed_browser() -> str:
    return next((str(p) for p in browser._INSTALLED_BROWSERS if p.exists()), "")


def alive() -> bool:
    return _proc is not None and _proc.poll() is None


def seen() -> None:
    global _last_seen
    _last_seen = time.time()


def status() -> dict:
    return {"enabled": config.AGENT_ENABLED, "installed": installed(), "worker_running": alive(),
            "provider": config.AGENT_PROVIDER, "model": config.AGENT_MODEL, "vision": config.agent_vision(),
            "key_set": bool(config.agent_key()), "browser": bool(installed_browser()),
            "limits": {"steps": config.AGENT_MAX_STEPS, "minutes": config.AGENT_MAX_MINUTES,
                       "ai_calls": config.AGENT_MAX_AI_CALLS}}


def _env(app_url: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k.upper() in _KEEP_ENV}
    env.update({
        "AGENT_APP_URL": app_url.rstrip("/"),
        "AGENT_TOKEN": TOKEN,
        "AGENT_PROVIDER": config.AGENT_PROVIDER,
        "AGENT_MODEL": config.AGENT_MODEL,
        "AGENT_API_KEY": config.agent_key(),
        "AGENT_BASE_URL": config.OPENAI_BASE_URL if config.AGENT_PROVIDER == "openai" else "",
        "AGENT_VISION": "1" if config.agent_vision() else "0",
        "AGENT_BROWSER": installed_browser(),
        "AGENT_SHOW_BROWSER": os.getenv("SCRAPER_AGENT_SHOW_BROWSER", "0"),
        "ANONYMIZED_TELEMETRY": "false",
        "BROWSER_USE_CLOUD_SYNC": "false",
        "BROWSER_USE_CONFIG_DIR": str(config.DATA_DIR / "browseruse"),
        "BROWSER_USE_LOGGING_LEVEL": "info",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    return env


def ensure(app_url: str) -> None:
    """Start the worker if it isn't running."""
    global _proc, last_app_url
    last_app_url = app_url
    with _lock:
        if alive():
            return
        if not installed():
            raise RuntimeError("Agent mode isn't installed. Run  python -m venv .venv-agent  and  "
                               ".venv-agent\\Scripts\\pip install -r agent_worker\\requirements.txt")
        log = open(LOG, "a", encoding="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        _proc = subprocess.Popen([str(config.AGENT_PYTHON), "-X", "utf8", "-s", str(WORKER)], cwd=str(config.ROOT),
                                 env=_env(app_url), stdout=log, stderr=subprocess.STDOUT, creationflags=flags)


def stop() -> None:
    global _proc
    with _lock:
        if alive():
            _proc.terminate()
            try:
                _proc.wait(10)
            except subprocess.TimeoutExpired:
                _proc.kill()
        _proc = None
