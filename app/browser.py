"""Render a page in headless Chromium (Playwright) for sites that build their listings with JavaScript.

Used only when a site needs it: the fetcher tries plain HTTP first. Rendering follows the same rules
as plain fetching (robots.txt, throttling, stop on CAPTCHA / challenge pages); it never tries to get
past a block. Each call starts its own browser, because Playwright's sync API is tied to the thread
that created it and jobs run in worker threads.

Hardening: a fresh context per call (never your real Chrome profile), no downloads, no service
workers, pop-ups closed, and every request (including sub-resources and redirects) checked by the
network guard so a page cannot make the browser reach this computer or the local network.
"""
import threading
from dataclasses import dataclass
from pathlib import Path

from . import blockdetect, config, netguard

# Stylesheets are kept: some infinite-scroll pages decide what to load from the laid-out page height.
SKIP_RESOURCES = {"image", "media", "font"}
NAV_TIMEOUT_MS = 45_000
IDLE_TIMEOUT_MS = 10_000
SETTLE_MS = 1_200
GROWTH_TIMEOUT_MS = 8_000   # how long to wait for new items after a click / scroll
GROWTH_POLL_MS = 400
MAX_ROUNDS_DEFAULT = 10

_lock = threading.Lock()


class BrowserUnavailable(RuntimeError):
    """Playwright or Chromium is not installed."""


class RenderBlocked(RuntimeError):
    """The rendered page is a bot check or challenge page."""

    def __init__(self, kind: str, message: str = ""):
        self.kind = kind
        super().__init__(message or f"The site showed {blockdetect.describe(kind)}; stopping.")


@dataclass
class Rendered:
    final_url: str
    html: str
    rounds: int          # load-more clicks / scrolls performed
    items: int           # matches of the item selector at the end (0 if none given)


_INSTALLED_BROWSERS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path("/usr/bin/google-chrome"), Path("/usr/bin/microsoft-edge"),
]


def available() -> bool:
    """True when Playwright is installed and some Chromium-family browser can be used: the
    bundled one, or (as render() falls back to them) an installed Edge / Chrome."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    if any(p.exists() for p in _INSTALLED_BROWSERS):
        return True
    try:
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


def install_hint() -> str:
    return ("Browser rendering needs Playwright: run  "
            f"{Path(config.ROOT, '.venv', 'Scripts', 'pip')} install playwright  and then  "
            f"{Path(config.ROOT, '.venv', 'Scripts', 'python')} -m playwright install chromium")


def _route(route) -> None:
    req = route.request
    if req.resource_type in SKIP_RESOURCES or not netguard.is_safe(req.url):
        route.abort()
    else:
        route.continue_()


def render(url: str, item_selector: str = "", load_more: str = "", scroll: bool = False,
           max_rounds: int = MAX_ROUNDS_DEFAULT, storage_state: dict | None = None,
           user_agent: str | None = None) -> Rendered:
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
        browser = None
        for channel in (None, "msedge", "chrome"):     # bundled first, then installed browsers
            try:
                browser = p.chromium.launch(headless=True, channel=channel) if channel \
                    else p.chromium.launch(headless=True)
                break
            except PwError as e:
                last = e
        if browser is None:
            raise BrowserUnavailable(install_hint() + f"\n({last})")
        try:
            context = browser.new_context(user_agent=user_agent or config.USER_AGENT, locale="en-IN",
                                          viewport={"width": 1366, "height": 900},
                                          accept_downloads=False, service_workers="block",
                                          storage_state=storage_state)
            context.route("**/*", _route)
            page = context.new_page()
            # Pop-ups and new tabs opened by the page are closed straight away.
            context.on("page", lambda new: new.close() if new != page else None)
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
    kind = blockdetect.detect(page.content(), 200, page.url)
    if kind in ("captcha", "challenge", "denied"):
        raise RenderBlocked(kind)
