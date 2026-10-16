"""Render a page in headless Chromium (Playwright) for sites that build their listings with JavaScript.

Used only when a site needs it: the fetcher tries plain HTTP first. Rendering follows the same rules
as plain fetching (robots.txt, throttling, stop on CAPTCHA / challenge pages); it never tries to get
past a block. Each call starts its own browser, because Playwright's sync API is tied to the thread
that created it and jobs run in worker threads.
"""
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from . import config

# Stylesheets are kept: some infinite-scroll pages decide what to load from the laid-out page height.
SKIP_RESOURCES = {"image", "media", "font"}
NAV_TIMEOUT_MS = 45_000
IDLE_TIMEOUT_MS = 10_000
SETTLE_MS = 1_200
GROWTH_TIMEOUT_MS = 8_000   # how long to wait for new items after a click / scroll
GROWTH_POLL_MS = 400
MAX_ROUNDS_DEFAULT = 10

_CHALLENGE = re.compile(
    r"just a moment|cf-chl|challenge-platform|verify you are human|are you a robot|captcha", re.I)
_lock = threading.Lock()


class BrowserUnavailable(RuntimeError):
    """Playwright or Chromium is not installed."""


class RenderBlocked(RuntimeError):
    """The rendered page is a bot check or challenge page."""


@dataclass
class Rendered:
    final_url: str
    html: str
    rounds: int          # load-more clicks / scrolls performed
    items: int           # matches of the item selector at the end (0 if none given)


def available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


def install_hint() -> str:
    return ("Browser rendering needs Playwright: run  "
            f"{Path(config.ROOT, '.venv', 'Scripts', 'pip')} install playwright  and then  "
            f"{Path(config.ROOT, '.venv', 'Scripts', 'python')} -m playwright install chromium")


def render(url: str, item_selector: str = "", load_more: str = "", scroll: bool = False,
           max_rounds: int = MAX_ROUNDS_DEFAULT) -> Rendered:
    """Load the page, wait for it to settle, optionally keep clicking / scrolling for more items.

    load_more  CSS selector of a "Load more" button to click until it disappears
    scroll     scroll to the bottom repeatedly (infinite-scroll pages)
    Both stop when the item count stops growing or max_rounds is reached.
    """
    try:
        from playwright.sync_api import Error as PwError, TimeoutError as PwTimeout, sync_playwright
    except ImportError as e:
        raise BrowserUnavailable(install_hint()) from e

    with _lock, sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except PwError as e:
            raise BrowserUnavailable(install_hint() + f"\n({e})") from e
        try:
            context = browser.new_context(user_agent=config.USER_AGENT, locale="en-IN",
                                          viewport={"width": 1366, "height": 900})
            page = context.new_page()
            page.route("**/*", lambda route: route.abort()
                       if route.request.resource_type in SKIP_RESOURCES else route.continue_())
            page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            _settle(page, PwTimeout)
            _check_block(page)

            rounds = 0
            count = _count(page, item_selector)
            while rounds < max_rounds and (load_more or scroll):
                acted = False
                if load_more:
                    button = page.locator(load_more).first
                    if button.count() and button.is_visible():
                        try:
                            button.scroll_into_view_if_needed(timeout=3000)
                            button.click(timeout=3000)
                            acted = True
                        except (PwError, PwTimeout):
                            pass
                if scroll and not acted:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    acted = True
                if not acted:
                    break
                rounds += 1
                new_count = _wait_for_growth(page, item_selector, count, PwTimeout)
                if item_selector and new_count <= count:
                    break
                count = new_count
            return Rendered(page.url, page.content(), rounds, count)
        finally:
            browser.close()


def _settle(page, PwTimeout) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=IDLE_TIMEOUT_MS)
    except PwTimeout:
        pass  # busy pages never go idle; the extra pause below is enough
    page.wait_for_timeout(SETTLE_MS)


def _wait_for_growth(page, selector: str, before: int, PwTimeout) -> int:
    """After a click / scroll, poll until the item count grows or the timeout passes.
    The request that loads more items often starts a moment after the action, so a plain
    network-idle wait can fire before anything has happened."""
    if not selector:
        _settle(page, PwTimeout)
        return 0
    waited = 0
    while waited < GROWTH_TIMEOUT_MS:
        page.wait_for_timeout(GROWTH_POLL_MS)
        waited += GROWTH_POLL_MS
        now = _count(page, selector)
        if now > before:
            _settle(page, PwTimeout)      # let the rest of the batch arrive
            return _count(page, selector)
    return _count(page, selector)


def _count(page, selector: str) -> int:
    if not selector:
        return 0
    try:
        return page.locator(selector).count()
    except Exception:
        return 0


def _check_block(page) -> None:
    title = page.title() or ""
    body = page.content()
    if _CHALLENGE.search(title) or (len(body) < 30000 and _CHALLENGE.search(body)):
        raise RenderBlocked("The site showed a bot check / challenge page; stopping.")
