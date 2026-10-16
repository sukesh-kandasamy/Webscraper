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


def _throttle(domain: str) -> None:
    wait = random.uniform(config.MIN_DELAY, config.MAX_DELAY)
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
