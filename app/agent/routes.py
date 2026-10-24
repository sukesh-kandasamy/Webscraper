"""Recorded routes: the steps the agent took on a site, replayed later by plain Playwright with no AI.

A route is needed when a site's filters live only inside the page (the address doesn't change when
you filter). It is recorded from the agent's actions:

    [{"action": "click", "target": {"tag": "a", "text": "2012", "attributes": {"id": "2012", ...},
                                    "xpath": "html/body/..."}},
     {"action": "input", "target": {...search box...}, "value": "New"},
     {"action": "send_keys", "value": "Enter"}]

On replay each target is found again from what was recorded (id, name, test id, aria-label,
placeholder, link address, exact text, then xpath). Cookie / pop-up clicks that no longer appear are
skipped. Any other step that can't be done stops the replay with RouteBroken, so the run says the
site changed instead of collecting the wrong page. Replay follows the same rules as everything else:
the planned site only, robots.txt, the daily page budget, polite pauses, and a stop on human checks.
"""
import re
import time
from urllib.parse import urljoin, urlsplit

from .. import config
from ..extraction import extractor
from ..fetching import blockdetect, browser, fetcher
from . import guard

ACTIONS = extractor.ROUTE_ACTIONS
MAX_STEPS = 40
FIND_TIMEOUT_MS = 8_000
POPUP = re.compile(r"\b(accept|reject|decline|agree|consent|cookie|close|dismiss|got it|no,? thanks|not now|"
                   r"maybe later|skip|continue without|necessary only|only necessary)\b|^[×✕x]$", re.I)
GENERATED_ID = re.compile(r"\d{4,}|[a-f0-9]{8,}|^:r|^ember|^react|^mui-|__", re.I)


class RouteBroken(RuntimeError):
    def __init__(self, step: int, reason: str):
        super().__init__(f"Step {step}: {reason}")
        self.step, self.reason = step, reason


def from_steps(steps: list[dict]) -> list[dict]:
    """The agent's successful actions, as a route (read-only looks around are left out)."""
    route = []
    for s in steps:
        d = s.get("detail") or {}
        name = d.get("action")
        if s.get("kind") != "action" or name not in ACTIONS or d.get("error"):
            continue
        params = d.get("params") or {}
        step = {"action": name}
        if d.get("target"):
            step["target"] = d["target"]
        if name == "navigate":
            step["url"] = params.get("url", "")
        elif name in ("input", "select_dropdown"):
            step["value"] = str(params.get("text", ""))
        elif name == "send_keys":
            step["value"] = str(params.get("keys", ""))
        elif name == "scroll":
            step["down"] = bool(params.get("down", True))
        elif name == "wait":
            step["seconds"] = min(int(params.get("seconds", 2) or 2), 10)
        if s.get("url"):
            step["url_after"] = s["url"]
        route.append(step)
    # The first step is opening the start page, which replay does anyway.
    while route and route[0]["action"] == "navigate":
        route.pop(0)
    return clean(route)


def clean(route) -> list[dict]:
    if not isinstance(route, list):
        return []
    out = []
    for step in route[:MAX_STEPS]:
        if isinstance(step, dict) and step.get("action") in ACTIONS:
            out.append({k: v for k, v in step.items()
                        if k in ("action", "target", "value", "url", "url_after", "down", "seconds")})
    return out


def describe(step: dict) -> str:
    t = step.get("target") or {}
    label = t.get("text") or (t.get("attributes") or {}).get("aria-label") or \
        (t.get("attributes") or {}).get("placeholder") or (t.get("attributes") or {}).get("value") or t.get("tag", "")
    return {"click": f"Click “{label}”", "input": f"Type “{step.get('value', '')}” into “{label}”",
            "select_dropdown": f"Choose “{step.get('value', '')}” in “{label}”",
            "send_keys": f"Press {step.get('value', '')}", "navigate": f"Open {step.get('url', '')}",
            "scroll": "Scroll", "go_back": "Go back", "wait": "Wait"}.get(step["action"], step["action"])


# ---- finding elements again ---------------------------------------------------------------

def _q(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"')


def selectors(target: dict) -> list[str]:
    """Ways to find the recorded element, most reliable first."""
    tag = (target.get("tag") or "*").lower()
    a = target.get("attributes") or {}
    text = " ".join((target.get("text") or "").split())
    out = []
    if a.get("id") and not GENERATED_ID.search(a["id"]):
        out.append(f'{tag}[id="{_q(a["id"])}"]')
    if a.get("data-testid"):
        out.append(f'[data-testid="{_q(a["data-testid"])}"]')
    if a.get("name"):
        out.append(f'{tag}[name="{_q(a["name"])}"]')
    if a.get("aria-label"):
        out.append(f'{tag}[aria-label="{_q(a["aria-label"])}"]')
    if a.get("placeholder"):
        out.append(f'{tag}[placeholder="{_q(a["placeholder"])}"]')
    if tag == "a" and a.get("href") and not a["href"].startswith(("#", "javascript")):
        out.append(f'a[href="{_q(a["href"])}"]')
    if tag in ("input", "button") and a.get("value"):
        out.append(f'{tag}[value="{_q(a["value"])}"]')
    if text and len(text) <= 80:
        out.append(f'{tag}:text-is("{_q(text)}")')
        out.append(f':is(a,button,[role=button],[role=tab],[role=option],label,li,span):text-is("{_q(text)}")')
    elif text:
        out.append(f'{tag}:has-text("{_q(text[:60])}")')
    if target.get("xpath"):
        xp = target["xpath"]
        out.append("xpath=" + (xp if xp.startswith("/") else "/" + xp))
    return out


def _find(page, target: dict, timeout_ms: int = FIND_TIMEOUT_MS):
    deadline = time.time() + timeout_ms / 1000
    sels = selectors(target)
    while True:
        for sel in sels:
            try:
                loc = page.locator(sel)
                n = loc.count()
            except Exception:
                continue
            for i in range(min(n, 5)):
                el = loc.nth(i)
                try:
                    if el.is_visible():
                        return el
                except Exception:
                    continue
        if time.time() > deadline:
            return None
        page.wait_for_timeout(300)


def _optional(step: dict) -> bool:
    t = step.get("target") or {}
    words = " ".join([t.get("text", ""), (t.get("attributes") or {}).get("aria-label", ""),
                      (t.get("attributes") or {}).get("id", ""), (t.get("attributes") or {}).get("class", "")])
    return step["action"] == "click" and bool(POPUP.search(words))


# ---- replay -----------------------------------------------------------------------------------

STEP_IDLE_MS = 4_000
STEP_SETTLE_MS = 700


def _settle(page) -> None:
    """After a step: wait for the page's requests to finish (shorter than a full page load; pages that
    never go idle just get the short pause)."""
    from playwright.sync_api import TimeoutError as PwTimeout
    try:
        page.wait_for_load_state("networkidle", timeout=STEP_IDLE_MS)
    except PwTimeout:
        pass
    page.wait_for_timeout(STEP_SETTLE_MS)


def _check_block(page) -> None:
    kind = blockdetect.detect(page.content(), 200, page.url)
    if kind in ("captcha", "challenge", "denied"):
        raise fetcher.NeedsHuman(kind, page.url)


def _do(page, step: dict, n: int, site: str) -> None:
    name = step["action"]
    if name == "navigate":
        url = urljoin(page.url, step.get("url", ""))
        ok, why = guard.check_url(url, site)
        if not ok:
            raise RouteBroken(n, why)
        page.goto(url, wait_until="domcontentloaded", timeout=browser.NAV_TIMEOUT_MS)
        return
    if name == "go_back":
        page.go_back(wait_until="domcontentloaded", timeout=browser.NAV_TIMEOUT_MS)
        return
    if name == "wait":
        page.wait_for_timeout(min(int(step.get("seconds", 2)), 10) * 1000)
        return
    if name == "scroll":
        page.mouse.wheel(0, 900 if step.get("down", True) else -900)
        return
    if name == "send_keys" and not step.get("target"):
        page.keyboard.press(_key(step.get("value", "")))
        return
    target = step.get("target") or {}
    # Same rules as when the agent recorded it (a route could have been edited since).
    ok, why = (guard.check_input(target, step.get("value", "")) if name == "input"
               else guard.check_click(target) if name in ("click", "select_dropdown") else (True, ""))
    if not ok:
        raise RouteBroken(n, why)
    el = _find(page, target, 2_500 if _optional(step) else FIND_TIMEOUT_MS)
    if el is None:
        if _optional(step):
            return                       # the cookie / pop-up box didn't appear this time
        raise RouteBroken(n, f"couldn't find the element for “{describe(step)}” on the page")
    el.scroll_into_view_if_needed(timeout=3000)
    if name == "click":
        el.click(timeout=5000)
    elif name == "input":
        el.fill(step.get("value", ""), timeout=5000)
    elif name == "select_dropdown":
        try:
            el.select_option(label=step.get("value", ""), timeout=5000)
        except Exception:
            el.click(timeout=5000)
            opt = _find(page, {"tag": "*", "text": step.get("value", "")}, 3000)
            if opt is None:
                raise RouteBroken(n, f"couldn't choose “{step.get('value', '')}”")
            opt.click(timeout=5000)
    elif name == "send_keys":
        el.press(_key(step.get("value", "")))


def _key(keys: str) -> str:
    k = (keys or "Enter").strip()
    return {"enter": "Enter", "return": "Enter", "tab": "Tab", "escape": "Escape", "esc": "Escape"}.get(k.lower(), k)


def _next_page(page, recipe: dict, index: int, visited: set) -> bool:
    """Go to the next page inside the same browser (so in-page filters are kept). False at the end."""
    pag = recipe["pagination"]
    html = page.content()
    if pag["type"] in ("click", "scroll"):
        before = browser._count(page, recipe["item_selector"])
        if pag["type"] == "click":
            button = page.locator(pag["selector"]).first
            if not (button.count() and button.is_visible()):
                return False
            button.click(timeout=5000)
        else:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        from playwright.sync_api import TimeoutError as PwTimeout
        return browser._wait_for_growth(page, recipe["item_selector"], before, PwTimeout) > before
    nxt = extractor.next_page_url(html, recipe, page.url, index, visited)
    if not nxt:
        return False
    # Click the link itself when it's on the page (keeps the page's state), else open its address.
    clicked = page.evaluate("""(u) => { const a = [...document.querySelectorAll('a[href]')].find(e => e.href === u);
                                        if (a) { a.click(); return true; } return false; }""", nxt)
    if not clicked:
        page.goto(nxt, wait_until="domcontentloaded", timeout=browser.NAV_TIMEOUT_MS)
    return True


def replay(start_url: str, route: list[dict], recipe: dict, max_pages: int = 1,
           site: str | None = None, on_step=None) -> list[fetcher.Page]:
    """Open start_url, do the route's steps, then collect up to max_pages pages of results.
    Raises RouteBroken (site changed), fetcher.NeedsHuman (human check), fetcher.BlockedError /
    BudgetExceeded / FetchError like any fetch."""
    site = site or guard.site_domain(start_url)
    ok, why = guard.check_url(start_url, site)
    if not ok:
        raise fetcher.BlockedError(why)
    domain = fetcher._preflight(start_url)
    session = fetcher.session_for(start_url)
    logged_in = bool(session and session.get("kind") == "login")
    pages: list[fetcher.Page] = []
    seen_rows: set = set()
    try:
        with browser.open_page(session["state"] if session else None, fetcher.user_agent(domain)) as page:
            fetcher._consume_budget(domain, logged_in)
            with fetcher._lock_for(domain):
                fetcher._throttle(domain)
            page.goto(start_url, wait_until="domcontentloaded", timeout=browser.NAV_TIMEOUT_MS)
            _settle(page)
            _check_block(page)
            for n, step in enumerate(route, 1):
                if on_step:
                    on_step(n, step)
                _do(page, step, n, site)
                _settle(page)
                _check_block(page)
                if not guard.on_site(page.url, site):
                    raise RouteBroken(n, f"the page moved off the site ({urlsplit(page.url).hostname})")
            visited = {page.url}
            for index in range(max(1, max_pages)):
                html = page.content()
                rows = extractor.extract(html, recipe, page.url)
                sig = {tuple(str(v) for v in r.values()) for r in rows}
                if index == 0 and not rows:
                    raise RouteBroken(len(route), "the steps ran, but the page shows no items any more")
                if index and not (sig - seen_rows):
                    break                    # pagination ran past the end
                seen_rows |= sig
                url = page.url if page.url not in {p.final_url for p in pages} else f"{page.url}#page-{index + 1}"
                pages.append(fetcher.Page(start_url, url, 200, html, False, rendered=True, logged_in=logged_in))
                if index + 1 >= max_pages:
                    break
                fetcher._consume_budget(domain, logged_in)
                with fetcher._lock_for(domain):
                    fetcher._throttle(domain)
                if not _next_page(page, recipe, index, visited):
                    break
                _settle(page)
                _check_block(page)
                visited.add(page.url)
    except (RouteBroken, fetcher.FetchError, fetcher.NeedsHuman):
        raise
    except browser.BrowserUnavailable as e:
        raise fetcher.FetchError(str(e)) from e
    except Exception as e:  # Playwright timeouts and the like
        raise fetcher.FetchError(f"Replaying the route failed: {type(e).__name__}: {str(e)[:300]}") from e
    return pages
