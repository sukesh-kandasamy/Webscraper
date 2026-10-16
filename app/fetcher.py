"""Polite HTTP fetching: robots.txt, per-domain throttling, retries, caching, block detection."""
import random
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from . import config, db, robots


class FetchError(Exception):
    """The page could not be fetched."""


class BlockedError(FetchError):
    """The site refused us (robots.txt, login wall, CAPTCHA, 403/429). Stop instead of retrying."""


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    html: str
    from_cache: bool
    rendered: bool = False      # captured from headless Chromium instead of plain HTTP
    rounds: int = 0             # load-more clicks / scrolls performed while rendering


_BLOCK_MARKERS = re.compile(
    r"captcha|are you a robot|access denied|unusual traffic|verify you are human|cf-challenge",
    re.I,
)

_client = httpx.Client(
    headers={
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    },
    follow_redirects=True,
    timeout=config.REQUEST_TIMEOUT,
)
_robots_cache: dict[str, list[tuple[bool, str]]] = {}
_last_hit: dict[str, float] = {}
_domain_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


def domain_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def _lock_for(domain: str) -> threading.Lock:
    with _registry_lock:
        return _domain_locks.setdefault(domain, threading.Lock())


def robots_rules(url: str) -> list[tuple[bool, str]]:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _robots_cache:
        try:
            r = _client.get(origin + "/robots.txt", timeout=15)
            text = r.text if r.status_code == 200 else ""
        except httpx.HTTPError:
            text = ""
        _robots_cache[origin] = robots.parse(text, config.USER_AGENT)
    return _robots_cache[origin]


def robots_allows(url: str) -> bool:
    return robots.is_allowed(robots_rules(url), url)


_settings_cache: dict[str, tuple[float, dict]] = {}
SETTINGS_TTL_S = 60


def site_settings(domain: str) -> dict:
    """Per-site overrides saved in the UI (min_delay, max_delay, render), cached briefly."""
    hit = _settings_cache.get(domain)
    if hit and time.time() - hit[0] < SETTINGS_TTL_S:
        return hit[1]
    settings = db.get_site_settings(domain)
    _settings_cache[domain] = (time.time(), settings)
    return settings


def forget_settings(domain: str) -> None:
    _settings_cache.pop(domain, None)


def delay_range(domain: str) -> tuple[float, float]:
    s = site_settings(domain)
    lo = float(s.get("min_delay") or config.MIN_DELAY)
    hi = float(s.get("max_delay") or config.MAX_DELAY)
    return (lo, max(lo, hi))


def _throttle(domain: str) -> None:
    wait = random.uniform(*delay_range(domain))
    elapsed = time.time() - _last_hit.get(domain, 0)
    if elapsed < wait:
        time.sleep(wait - elapsed)
    _last_hit[domain] = time.time()


def fetch(url: str, use_cache: bool = True, retries: int = 2) -> Page:
    if not url.lower().startswith(("http://", "https://")):
        raise FetchError(f"Not an http(s) URL: {url}")
    if use_cache:
        hit = db.cache_get(url, config.CACHE_TTL_HOURS * 3600)
        if hit:
            return Page(url, hit["final_url"], hit["status"], hit["body"], True)

    if config.RESPECT_ROBOTS and not robots_allows(url):
        raise BlockedError(f"robots.txt disallows {urlsplit(url).path}; pick an allowed page "
                           "(e.g. a category page instead of a search page).")

    domain = domain_of(url)
    last_error = ""
    for attempt in range(retries + 1):
        with _lock_for(domain):
            _throttle(domain)
            try:
                r = _client.get(url)
            except httpx.HTTPError as e:
                last_error = f"{type(e).__name__}: {e}"
                continue
        if r.status_code in (401, 403, 429):
            raise BlockedError(f"Site returned HTTP {r.status_code}; stopping to avoid being blocked.")
        if r.status_code >= 500:
            last_error = f"HTTP {r.status_code}"
            time.sleep(2 ** attempt * 3)
            continue
        html = r.text
        if r.status_code == 200 and len(html) < 20000 and _BLOCK_MARKERS.search(html):
            raise BlockedError("The site is showing a CAPTCHA / bot check; stopping.")
        if r.status_code >= 400:
            raise FetchError(f"HTTP {r.status_code} for {url}")
        db.cache_put(url, r.status_code, str(r.url), html)
        return Page(url, str(r.url), r.status_code, html, False)
    raise FetchError(f"Failed to fetch {url}: {last_error}")


# ---- browser rendering -----------------------------------------------------

def fetch_rendered(url: str, item_selector: str = "", load_more: str = "", scroll: bool = False,
                   max_rounds: int = 0, use_cache: bool = True) -> Page:
    """Fetch through headless Chromium. Same robots / throttle / block rules as fetch()."""
    from . import browser  # lazy: Playwright is optional

    if not url.lower().startswith(("http://", "https://")):
        raise FetchError(f"Not an http(s) URL: {url}")
    key = f"render:{url}#rounds={max_rounds}"
    if use_cache:
        hit = db.cache_get(key, config.CACHE_TTL_HOURS * 3600)
        if hit:
            return Page(url, hit["final_url"], hit["status"], hit["body"], True, rendered=True)
    if config.RESPECT_ROBOTS and not robots_allows(url):
        raise BlockedError(f"robots.txt disallows {urlsplit(url).path}; pick an allowed page.")

    domain = domain_of(url)
    with _lock_for(domain):
        _throttle(domain)
        try:
            out = browser.render(url, item_selector=item_selector, load_more=load_more,
                                 scroll=scroll, max_rounds=max_rounds)
        except browser.RenderBlocked as e:
            raise BlockedError(str(e)) from e
        except browser.BrowserUnavailable as e:
            raise FetchError(str(e)) from e
        except Exception as e:  # navigation timeouts and the like
            raise FetchError(f"Browser rendering failed for {url}: {type(e).__name__}: {e}") from e
    db.cache_put(key, 200, out.final_url, out.html)
    return Page(url, out.final_url, 200, out.html, False, rendered=True, rounds=out.rounds)


def looks_empty(html: str, recipe: dict | None) -> bool:
    """True when the HTML holds no listings: the recipe finds no rows, or (without a recipe)
    no repeated listing blocks can be found. That usually means the site renders with JavaScript."""
    from . import extractor, recipe_builder  # lazy to avoid an import cycle at module load

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
    except FetchError:
        return page   # no browser installed, or rendering failed: keep what HTTP gave us
    return rendered if not looks_empty(rendered.html, recipe) else page
