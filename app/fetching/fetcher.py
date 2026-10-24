"""Polite, safe fetching: robots.txt, network guard, per-site throttling and budgets, sessions,
block detection, caching.

Rules applied to every page, in plain HTTP and in the browser:
  - only public http(s) hosts (netguard), re-checked on every redirect hop
  - opt-out list and per-site cool-downs are refused up front
  - robots.txt is respected
  - a per-site daily page budget (stricter when logged in)
  - adaptive delay: waits grow on 429 / 503 / slow answers and recover slowly; Retry-After honoured
  - CAPTCHAs / challenges raise NeedsHuman (a person solves them), access-denied stops, login walls
    ask you to log in. Nothing is bypassed.
  - session cookies are sent only to the site they belong to; logged-in pages are never cached
"""
import random
import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from .. import config, db
from ..security import audit, netguard, sessions
from . import blockdetect, robots


class FetchError(Exception):
    """The page could not be fetched."""


class BlockedError(FetchError):
    """The site refused us, or our own rules forbid the fetch. Stop instead of retrying."""


class NeedsHuman(FetchError):
    """The page is a CAPTCHA / challenge (or a login wall): a person has to act in a visible browser."""

    def __init__(self, kind: str, url: str, message: str = ""):
        self.kind, self.url = kind, url
        super().__init__(message or f"The site showed {blockdetect.describe(kind)} at {url}")


class SessionExpired(NeedsHuman):
    """The saved login no longer works: log in again."""


class BudgetExceeded(FetchError):
    """Today's page budget for this site is used up."""


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    html: str
    from_cache: bool
    rendered: bool = False      # captured from headless Chromium instead of plain HTTP
    rounds: int = 0             # load-more clicks / scrolls performed while rendering
    logged_in: bool = False     # fetched with a saved login session


_client = httpx.Client(
    headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    },
    follow_redirects=False,      # redirects are followed by hand so every hop is checked
    timeout=config.REQUEST_TIMEOUT,
)
_robots_cache: dict[str, list[tuple[bool, str]]] = {}
_last_hit: dict[str, float] = {}
_penalty: dict[str, float] = {}      # adaptive delay multiplier per domain (1 = normal)
_domain_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()
MAX_PENALTY = 8.0
SLOW_RESPONSE_S = 10.0


def domain_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _lock_for(domain: str) -> threading.Lock:
    with _registry_lock:
        return _domain_locks.setdefault(domain, threading.Lock())


# ---- site rules ------------------------------------------------------------------

_settings_cache: dict[str, tuple[float, dict]] = {}
SETTINGS_TTL_S = 30


def site_settings(domain: str) -> dict:
    """Per-site settings saved in the UI, cached briefly."""
    hit = _settings_cache.get(domain)
    if hit and time.time() - hit[0] < SETTINGS_TTL_S:
        return hit[1]
    settings = db.get_site_settings(domain)
    _settings_cache[domain] = (time.time(), settings)
    return settings


def forget_settings(domain: str) -> None:
    _settings_cache.pop(domain, None)


def update_settings(domain: str, patch: dict) -> dict:
    new = db.update_site_settings(domain, patch)
    forget_settings(domain)
    return new


def delay_range(domain: str) -> tuple[float, float]:
    s = site_settings(domain)
    lo = float(s.get("min_delay") or config.MIN_DELAY)
    hi = float(s.get("max_delay") or config.MAX_DELAY)
    return (lo, max(lo, hi))


def is_opted_out(host: str) -> bool:
    host = host.lower()
    for d in config.BLOCKED_DOMAINS:
        if host == d or host.endswith("." + d):
            return True
    return bool(site_settings(host).get("blocked"))


def cooldown_left(domain: str) -> float:
    until = float(site_settings(domain).get("cooldown_until") or 0)
    return max(0.0, until - time.time())


def start_cooldown(domain: str, minutes: float, reason: str) -> None:
    update_settings(domain, {"cooldown_until": time.time() + minutes * 60, "cooldown_reason": reason})
    audit.log("cooldown", domain=domain, minutes=minutes, reason=reason)


def daily_budget(domain: str, logged_in: bool) -> int:
    s = site_settings(domain)
    if s.get("daily_budget"):
        return int(s["daily_budget"])
    return config.DAILY_BUDGET_LOGGED_IN if logged_in else config.DAILY_BUDGET


def user_agent(domain: str) -> str:
    return config.HONEST_USER_AGENT if site_settings(domain).get("honest_ua") else config.USER_AGENT


def _preflight(url: str) -> str:
    """Checks that apply before any network access. Returns the domain."""
    try:
        netguard.check_url(url)
    except netguard.UnsafeURL as e:
        raise BlockedError(str(e)) from e
    domain = domain_of(url)
    if is_opted_out(host_of(url)):
        raise BlockedError(f"{domain} is on your do-not-scrape list.")
    left = cooldown_left(domain)
    if left > 0:
        reason = site_settings(domain).get("cooldown_reason", "")
        raise BlockedError(f"{domain} is cooling down for {left / 60:.0f} more minute(s)"
                           + (f" ({reason})" if reason else "") + ".")
    if config.RESPECT_ROBOTS and not robots_allows(url):
        raise BlockedError(f"robots.txt disallows {urlsplit(url).path}; pick an allowed page "
                           "(e.g. a category page instead of a search page).")
    return domain


def _consume_budget(domain: str, logged_in: bool) -> None:
    budget = daily_budget(domain, logged_in)
    if not db.consume_page(domain, budget):
        raise BudgetExceeded(f"Today's page budget for {domain} ({budget} pages) is used up; "
                             "it resets tomorrow, or raise it in the site settings.")


def session_for(url: str) -> dict | None:
    """The saved session to use for this URL, unless the site turned sessions off."""
    if site_settings(domain_of(url)).get("use_session") is False:
        return None
    try:
        return sessions.for_host(host_of(url))
    except Exception:
        return None


# ---- robots ----------------------------------------------------------------------

def robots_rules(url: str) -> list[tuple[bool, str]]:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _robots_cache:
        text = ""
        if netguard.is_safe(origin + "/robots.txt"):
            try:
                r = _client.get(origin + "/robots.txt", timeout=15, follow_redirects=True,
                                headers={"User-Agent": config.USER_AGENT})
                text = r.text if r.status_code == 200 else ""
            except httpx.HTTPError:
                text = ""
        _robots_cache[origin] = robots.parse(text, config.USER_AGENT)
    return _robots_cache[origin]


def robots_allows(url: str) -> bool:
    return robots.is_allowed(robots_rules(url), url)


# ---- politeness ------------------------------------------------------------------

def _throttle(domain: str) -> None:
    wait = random.uniform(*delay_range(domain)) * _penalty.get(domain, 1.0)
    elapsed = time.time() - _last_hit.get(domain, 0)
    if elapsed < wait:
        time.sleep(wait - elapsed)
    _last_hit[domain] = time.time()


def _penalize(domain: str) -> None:
    _penalty[domain] = min(MAX_PENALTY, _penalty.get(domain, 1.0) * 2)


def _recover(domain: str) -> None:
    _penalty[domain] = max(1.0, _penalty.get(domain, 1.0) * 0.85)


def _retry_after(r: httpx.Response) -> float | None:
    value = r.headers.get("retry-after", "").strip()
    if value.isdigit():
        return float(value)
    if value:
        try:
            from email.utils import parsedate_to_datetime
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return None
    return None


# ---- plain HTTP ------------------------------------------------------------------

def _get(url: str, session: dict | None, ua: str) -> httpx.Response:
    """GET following redirects by hand: each hop is checked by the network guard, and session
    cookies are attached only while the hop stays on the session's own site."""
    current = url
    for _ in range(config.MAX_REDIRECTS + 1):
        netguard.check_url(current)
        headers = {"User-Agent": ua}
        if session and sessions.in_scope(session, host_of(current)):
            cookie = sessions.cookie_header(session["state"], current)
            if cookie:
                headers["Cookie"] = cookie
        r = _client.get(current, headers=headers)
        if r.is_redirect and r.headers.get("location"):
            current = urljoin(str(r.url), r.headers["location"])
            continue
        return r
    raise FetchError(f"Too many redirects starting at {url}")


def _check_size(html: str, url: str) -> None:
    if len(html.encode("utf-8", "ignore")) > config.MAX_PAGE_BYTES:
        raise FetchError(f"Page too large (> {config.MAX_PAGE_BYTES // 1_048_576} MB): {url}")


def _classify(html: str, status: int, url: str, session: dict | None) -> None:
    """Raise the right error for challenge / login / denied pages."""
    kind = blockdetect.detect(html, status, url)
    if kind is None:
        return
    if kind == "login":
        if session and session.get("kind") == sessions.KIND_LOGIN:
            raise SessionExpired("login", url, "Your saved login for this site has expired; "
                                 "log in again from Sites & sessions.")
        raise BlockedError(f"{url} needs a login. Use 'Log in to this site' if it is your own "
                           "account and the site allows automated access.")
    if kind in ("captcha", "challenge"):
        raise NeedsHuman(kind, url)
    raise BlockedError(f"The site showed {blockdetect.describe(kind)}; stopping.")


def fetch(url: str, use_cache: bool = True, retries: int = 2) -> Page:
    session = session_for(url)
    logged_in = bool(session and session.get("kind") == sessions.KIND_LOGIN)
    if use_cache and not session:
        hit = db.cache_get(url, config.CACHE_TTL_HOURS * 3600)
        if hit:
            return Page(url, hit["final_url"], hit["status"], hit["body"], True)

    domain = _preflight(url)
    _consume_budget(domain, logged_in)
    ua = user_agent(domain)
    last_error = ""
    for attempt in range(retries + 1):
        with _lock_for(domain):
            _throttle(domain)
            started = time.time()
            try:
                r = _get(url, session, ua)
            except netguard.UnsafeURL as e:
                raise BlockedError(str(e)) from e
            except httpx.HTTPError as e:
                last_error = f"{type(e).__name__}: {e}"
                _penalize(domain)
                continue
        elapsed = time.time() - started
        if r.status_code in (429, 503):
            _penalize(domain)
            wait = _retry_after(r)
            if wait is not None and wait <= config.MAX_RETRY_AFTER_S and attempt < retries:
                audit.log("retry_after", domain=domain, seconds=round(wait, 1), status=r.status_code)
                time.sleep(wait)
                continue
            if r.status_code == 429:
                start_cooldown(domain, config.COOLDOWN_MINUTES, "rate limited (HTTP 429)")
                raise BlockedError(f"{domain} is rate-limiting us (HTTP 429); cooling down.")
        if r.status_code >= 500:
            last_error = f"HTTP {r.status_code}"
            _penalize(domain)
            time.sleep(2 ** attempt * 3)
            continue
        html = r.text
        _check_size(html, url)
        final_url = str(r.url)
        audit.log("fetch", domain=domain, url=url, status=r.status_code, logged_in=logged_in,
                  rendered=False, ms=round(elapsed * 1000))
        if r.status_code in (401, 403):
            try:
                _classify(html, r.status_code, final_url, session)
            except BlockedError:
                start_cooldown(domain, config.COOLDOWN_MINUTES / 2, f"HTTP {r.status_code}")
                raise
        _classify(html, r.status_code, final_url, session)
        if r.status_code >= 400:
            raise FetchError(f"HTTP {r.status_code} for {url}")
        if elapsed > SLOW_RESPONSE_S:
            _penalize(domain)
        else:
            _recover(domain)
        if not session:
            db.cache_put(url, r.status_code, final_url, html)
        return Page(url, final_url, r.status_code, html, False, logged_in=logged_in)
    raise FetchError(f"Failed to fetch {url}: {last_error}")


# ---- browser rendering -----------------------------------------------------------

def fetch_rendered(url: str, item_selector: str = "", load_more: str = "", scroll: bool = False,
                   max_rounds: int = 0, use_cache: bool = True) -> Page:
    """Fetch through headless Chromium. Same rules as fetch()."""
    from . import browser  # lazy: Playwright is optional

    session = session_for(url)
    logged_in = bool(session and session.get("kind") == sessions.KIND_LOGIN)
    key = f"render:{url}#rounds={max_rounds}"
    if use_cache and not session:
        hit = db.cache_get(key, config.CACHE_TTL_HOURS * 3600)
        if hit:
            return Page(url, hit["final_url"], hit["status"], hit["body"], True, rendered=True)
    domain = _preflight(url)
    _consume_budget(domain, logged_in)
    with _lock_for(domain):
        _throttle(domain)
        started = time.time()
        try:
            out = browser.render(url, item_selector=item_selector, load_more=load_more,
                                 scroll=scroll, max_rounds=max_rounds,
                                 storage_state=session["state"] if session else None,
                                 user_agent=user_agent(domain))
        except browser.RenderBlocked as e:
            raise NeedsHuman(e.kind, url) from e
        except browser.BrowserUnavailable as e:
            raise FetchError(str(e)) from e
        except Exception as e:  # navigation timeouts and the like
            raise FetchError(f"Browser rendering failed for {url}: {type(e).__name__}: {e}") from e
    _check_size(out.html, url)
    audit.log("fetch", domain=domain, url=url, status=200, logged_in=logged_in, rendered=True,
              rounds=out.rounds, ms=round((time.time() - started) * 1000))
    _classify(out.html, 200, out.final_url, session)
    if not session:
        db.cache_put(key, 200, out.final_url, out.html)
    return Page(url, out.final_url, 200, out.html, False, rendered=True, rounds=out.rounds,
                logged_in=logged_in)


def looks_empty(html: str, recipe: dict | None) -> bool:
    """True when the HTML holds no listings: the recipe finds no rows, or (without a recipe)
    no repeated listing blocks can be found. That usually means the site renders with JavaScript."""
    from ..extraction import extractor, recipe_builder  # lazy to avoid an import cycle at module load

    if recipe:
        return not extractor.extract(html, recipe, "https://x/")
    return not recipe_builder.find_candidates(html)


def fetch_smart(url: str, recipe: dict | None = None, max_rounds: int = 0,
                use_cache: bool = True) -> Page:
    """Fetch according to the site's render setting: never / always / auto.

    In "auto" mode plain HTTP is tried first and the browser only when the page looks empty.
    Recipes with scroll / click pagination always need the browser.
    """
    mode = site_settings(domain_of(url)).get("render") or "auto"
    pag = (recipe or {}).get("pagination") or {}
    browser_paging = pag.get("type") in ("scroll", "click")
    render_kwargs = dict(
        item_selector=(recipe or {}).get("item_selector", ""),
        load_more=pag.get("selector", "") if pag.get("type") == "click" else "",
        scroll=pag.get("type") == "scroll",
        max_rounds=max_rounds if browser_paging else 0,
        use_cache=use_cache,
    )
    # Scroll / click paging only matters when we actually page (max_rounds > 0). A preview of
    # such a site can still use the fast plain fetch when the first items are in the HTML.
    if mode == "always" or (browser_paging and max_rounds > 0):
        return fetch_rendered(url, **render_kwargs)
    page = fetch(url, use_cache=use_cache)
    if mode == "never" or not looks_empty(page.html, recipe):
        return page
    try:
        rendered = fetch_rendered(url, **render_kwargs)
    except (BudgetExceeded, NeedsHuman, BlockedError):
        raise
    except FetchError:
        return page   # no browser installed, or rendering failed: keep what HTTP gave us
    return rendered if not looks_empty(rendered.html, recipe) else page
