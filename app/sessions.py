"""Login sessions and visible-browser windows.

How logging in works (the app never sees your password):
  1. open_window() opens a *visible* Chromium window on the site's login page.
  2. You log in yourself (password, OTP, 2FA) in that window.
  3. When you click "I'm logged in" in the app (or close the window), only the browser's session
     state (cookies + local storage) is captured, encrypted with the vault key and saved to
     data/sessions/<domain>.bin.
  4. Fetches to that domain (and its subdomains) attach the session; other sites never see it.

The same window is used when a job hits a CAPTCHA: you solve it yourself, the job continues with the
cookies the site gave your browser. Nothing here solves a CAPTCHA automatically.
"""
import json
import re
import threading
import time
from urllib.parse import urlsplit

from . import audit, config, netguard, vault

KIND_LOGIN = "login"        # you logged in: personal account data, stricter budget, encrypted records
KIND_BROWSER = "browser"    # cookies from passing a browser check by hand; not an account login

_mem: dict[str, tuple[float, dict]] = {}     # decrypted payloads, keyed by domain, with file mtime
_mem_lock = threading.Lock()


# ---- storage -----------------------------------------------------------------

def _file(domain: str):
    safe = re.sub(r"[^a-z0-9.\-]", "_", domain.lower())
    return config.SESSIONS_DIR / f"{safe}.bin"


def base_domain(host: str) -> str:
    host = host.lower().strip(".")
    return host[4:] if host.startswith("www.") else host


def default_scope(domain: str) -> list[str]:
    return sorted({domain.lower(), base_domain(domain)})


def save(domain: str, state: dict, account: str = "", kind: str = KIND_LOGIN,
         scope: list[str] | None = None) -> dict:
    config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"domain": domain.lower(), "account": account.strip()[:120], "kind": kind,
               "scope": scope or default_scope(domain), "created_at": time.time(), "state": state}
    _file(domain).write_bytes(vault.encrypt(json.dumps(payload).encode("utf-8")))
    with _mem_lock:
        _mem.pop(domain.lower(), None)
    audit.log("session_saved", domain=domain.lower(), kind=kind, account=payload["account"],
              cookies=len(state.get("cookies", [])))
    return _public(payload)


def _expired(payload: dict) -> bool:
    return time.time() - payload["created_at"] > config.SESSION_MAX_AGE_DAYS * 86400


def _read(domain: str) -> dict | None:
    f = _file(domain)
    if not f.exists():
        return None
    mtime = f.stat().st_mtime
    with _mem_lock:
        hit = _mem.get(domain.lower())
        if hit and hit[0] == mtime:
            payload = hit[1]
        else:
            payload = None
    if payload is None:
        try:
            payload = json.loads(vault.decrypt(f.read_bytes()).decode("utf-8"))
        except vault.VaultError:
            return None
        with _mem_lock:
            _mem[domain.lower()] = (mtime, payload)
    if _expired(payload):
        forget(domain, reason="expired")
        return None
    return payload


def _public(payload: dict) -> dict:
    age = time.time() - payload["created_at"]
    return {"domain": payload["domain"], "account": payload.get("account", ""),
            "kind": payload.get("kind", KIND_LOGIN), "scope": payload["scope"],
            "created_at": payload["created_at"], "age_hours": round(age / 3600, 1),
            "expires_in_hours": round(config.SESSION_MAX_AGE_DAYS * 24 - age / 3600, 1),
            "cookies": len(payload["state"].get("cookies", []))}


def info(domain: str) -> dict | None:
    payload = _read(domain)
    return _public(payload) if payload else None


def list_sessions() -> list[dict]:
    if not config.SESSIONS_DIR.exists():
        return []
    out = []
    for f in sorted(config.SESSIONS_DIR.glob("*.bin")):
        payload = _read(f.stem)
        if payload:
            out.append(_public(payload))
    return out


def forget(domain: str, reason: str = "user") -> bool:
    f = _file(domain)
    existed = f.exists()
    if existed:
        f.unlink()
    with _mem_lock:
        _mem.pop(domain.lower(), None)
    if existed:
        audit.log("session_forgotten", domain=domain.lower(), reason=reason)
    return existed


def in_scope(payload: dict, host: str) -> bool:
    host = host.lower()
    return any(host == s or host.endswith("." + s) for s in payload["scope"])


def for_host(host: str) -> dict | None:
    """The saved session whose scope covers this host, if any (most specific first)."""
    host = host.lower()
    candidates = [host, base_domain(host)]
    parts = host.split(".")
    candidates += [".".join(parts[i:]) for i in range(1, len(parts) - 1)]
    for d in dict.fromkeys(candidates):
        payload = _read(d)
        if payload and in_scope(payload, host):
            return payload
    return None


def cookie_header(state: dict, url: str) -> str:
    """Cookies from a saved browser state that apply to this URL (domain, path, secure, expiry)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    now = time.time()
    pairs = []
    for c in state.get("cookies", []):
        cdom = (c.get("domain") or "").lower().lstrip(".")
        if not cdom or not (host == cdom or host.endswith("." + cdom)):
            continue
        if not path.startswith(c.get("path") or "/"):
            continue
        if c.get("secure") and parts.scheme != "https":
            continue
        exp = c.get("expires", -1)
        if exp not in (-1, None) and exp > 0 and exp < now:
            continue
        pairs.append(f"{c['name']}={c['value']}")
    return "; ".join(pairs)


# ---- visible browser windows -----------------------------------------------------

def launch_visible(p):
    """Start a visible browser. Playwright's bundled Chromium first; if Windows blocks it (company
    policies often block programs in AppData), the installed Edge, then Chrome. Playwright always
    gives it a fresh temporary profile, never your everyday browser profile."""
    errors = []
    for channel in (None, "msedge", "chrome"):
        try:
            return p.chromium.launch(headless=False, channel=channel) if channel \
                else p.chromium.launch(headless=False)
        except Exception as e:
            errors.append(f"{channel or 'bundled Chromium'}: {str(e).splitlines()[0][:120]}")
    raise RuntimeError("Could not open a browser window. " + " | ".join(errors))


class Window:
    """A visible Chromium window the user drives (log in, or solve a CAPTCHA)."""

    def __init__(self, key: str, url: str, purpose: str, storage_state: dict | None = None):
        self.key, self.url, self.purpose = key, url, purpose
        self.storage_state = storage_state
        self.status = "starting"        # starting | open | finished | cancelled | timeout | error
        self.message = ""
        self.state: dict | None = None   # captured browser state when finished
        self.final_url = ""
        self.title = ""
        self._finish = threading.Event()
        self._cancel = threading.Event()
        self._last_state: dict | None = None
        self.started_at = time.time()
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"window-{key}")

    def public(self) -> dict:
        return {"key": self.key, "url": self.url, "purpose": self.purpose, "status": self.status,
                "message": self.message, "final_url": self.final_url, "title": self.title,
                "open_seconds": round(time.time() - self.started_at)}

    def finish(self) -> None:
        self._finish.set()

    def cancel(self) -> None:
        self._cancel.set()

    def wait(self, timeout: float | None = None) -> None:
        self.thread.join(timeout)

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.status, self.message = "error", "Playwright is not installed"
            return
        try:
            with sync_playwright() as p:
                browser = launch_visible(p)
                try:
                    context = browser.new_context(
                        storage_state=self.storage_state, user_agent=config.USER_AGENT,
                        locale="en-IN", accept_downloads=False, service_workers="block",
                        no_viewport=True)
                    context.route("**/*", lambda route: route.continue_()
                                  if netguard.is_safe(route.request.url) else route.abort())
                    page = context.new_page()
                    page.goto(self.url, wait_until="domcontentloaded", timeout=45_000)
                    self.status = "open"
                    deadline = time.time() + config.INTERACTIVE_TIMEOUT_S
                    while not (self._finish.is_set() or self._cancel.is_set()):
                        if time.time() > deadline:
                            self.status, self.message = "timeout", "The window was open too long"
                            return
                        live = [pg for pg in context.pages if not pg.is_closed()]
                        if not browser.is_connected() or not live:
                            # The user closed the window: use the last state we captured.
                            self._finish.set()
                            break
                        page = live[-1]
                        try:
                            self._last_state = context.storage_state()
                            self.final_url, self.title = page.url, page.title()
                            page.wait_for_timeout(1000)
                        except Exception:
                            time.sleep(0.5)
                    if self._cancel.is_set():
                        self.status = "cancelled"
                        return
                    try:
                        self.state = context.storage_state()
                    except Exception:
                        self.state = self._last_state
                    self.status = "finished" if self.state else "error"
                    if not self.state:
                        self.message = "Could not read the browser session"
                finally:
                    try:
                        browser.close()
                    except Exception:
                        pass
        except Exception as e:
            self.status, self.message = "error", f"{type(e).__name__}: {e}"[:300]


_windows: dict[str, Window] = {}
_windows_lock = threading.Lock()


def open_window(key: str, url: str, purpose: str, storage_state: dict | None = None) -> Window:
    netguard.check_url(url)
    with _windows_lock:
        current = _windows.get(key)
        if current and current.status in ("starting", "open"):
            return current
        win = Window(key, url, purpose, storage_state)
        _windows[key] = win
    win.thread.start()
    audit.log("window_opened", key=key, purpose=purpose, domain=(urlsplit(url).hostname or ""))
    return win


def get_window(key: str) -> Window | None:
    with _windows_lock:
        return _windows.get(key)


def close_window(key: str) -> None:
    with _windows_lock:
        _windows.pop(key, None)
