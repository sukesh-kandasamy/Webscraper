"""Goal (+ site) → a task plan the agent follows (you approve it first).

    {"start_url": "https://www.magicbricks.com/",
     "navigation": ["open the rent section", "search locality 'Whitefield, Bangalore'", "filter: 3 BHK", ...],
     "checklist": [{"id": "must_1", "kind": "must", "text": "locality: Whitefield, Bangalore"},
                   {"id": "app_1", "kind": "app", "text": "price < 40000"}],
     "request": {...request_parser request: columns with meanings/types, filters, sort, limit...},
     "pages": 50, "item_pages": "auto", "summary": "..."}

The checklist is the agent's contract: "must" items change WHICH results the site shows (a place, dates, a
category, a search term) and have to be true on the page before collecting; "app" items (price, rating,
sort, limit) are applied by the app to the rows after collecting, so the agent never has to find them on
the site. Conditions the site's own menus can apply also go into navigation; all of them stay in the
request as row filters, so nothing is lost if the site has no such filter.

When the goal names no site, suggest_sites() asks the AI for 1-3 real sites to choose from.
"""
import datetime as dt
import re
from urllib.parse import urlsplit

from .. import config
from ..extraction import recipe_builder, request_parser
from ..fetching import fetcher
from ..security import netguard
from . import browse_text, guard

MAX_GOAL_CHARS = 2000
MAX_NAV_STEPS = 12
MAX_MUST = 8
_APP_SIDE = re.compile(r"\b(price|prices|priced|cost|rating|ratings|stars?|sort|sorted|cheap\w*|expensive|budget|"
                       r"first \d+|top \d+|newest|latest|oldest|limit)\b|[£$€₹]|\b(rs|inr|usd|eur|gbp)\b", re.I)

SYSTEM = """You plan a web-scraping task for a browser agent. The person names a website and describes \
the data they want. You do not see the site.

Return:
- start_url: the best page of the site to start from (its home page unless the person gave a link or the \
  site has an obvious section for this, e.g. https://www.example.com/jobs). Must be on the named site.
- navigation: short steps, in order, for reaching the LIST of results on the site with its own search box, \
  menus and filter controls, e.g. "search for 'running shoes'", "open the Men category", \
  "filter: price up to 3000", "sort by newest". Only searching, choosing categories, filters, sorting. \
  Never logging in, contacting, buying, applying or filling personal forms. The last step is always \
  "stop on the results list". 2-8 steps.
- filters are only for values each item shows itself (its price, rating, location). Conditions that choose \
  which page or tab to look at (a year, a category, a city, a search term) go into navigation only, not filters.
- columns, filters, sort, limit, auto, details_hint, question, summary: the data request, exactly as for a \
  scraper (columns: snake_case name, meaning with hints that avoid mistakes, type money|number|percent|\
  rating|date|url|image|text, required; filters on those columns with op lt|lte|gt|gte|eq|ne|contains|\
  not_contains and a plain value; sort {column, desc}; limit 0 if not asked).
- pages: how many result pages to collect: 1-50; 50 when the person says all / every / complete.
- item_pages: "yes" if some wanted values are usually only on each item's own page, "no" if not needed, \
  "auto" if unsure.
- must: the conditions that change WHICH results the site shows and so must be true on the results page \
  before collecting: the search term, place, dates, category, type of listing. Short, one per line, e.g. \
  "destination: Goa", "check-in 2026-11-25, check-out 2026-11-28", "category: Mystery". Write dates in full \
  (YYYY-MM-DD); a date without a year is the next one after today. Not prices, \
  ratings, sorting or how many: the app applies those to the rows afterwards. Never restate what the site \
  or page already is (on an "Oscar winning films" page, "oscar winner: true" is not a condition; neither is \
  "awards contains Oscar"). 0-8 items.
- question: "" normally; one short question only if the goal is too unclear to start."""

SUGGEST_SYSTEM = """A person describes data they want collected from the web but names no website. Name 1-3 \
real, public websites where exactly this data is listed, best first. Only sites you are confident exist and \
that show the listings without logging in. Never suggest search engines, social networks, or aggregators \
that proxy other sites. Prefer the site the data belongs to (an official shop, job board, property portal, \
listings site) and, for India-specific requests, Indian sites.

Return:
- candidates: 1-3 items, each with site (the domain, e.g. "www.flipkart.com"), reason (one short sentence: \
  why this site, what it lists), note ("" or one short warning, e.g. "needs a browser for its search").
- question: "" normally; one short question only if the request is too vague to name a site."""


def _schema() -> dict:
    s = {"type": "string"}
    req = request_parser._schema()
    props = {"start_url": s, "navigation": {"type": "array", "items": s},
             **req["properties"],
             "pages": {"type": "integer"},
             "item_pages": {"type": "string", "enum": ["yes", "no", "auto"]},
             "must": {"type": "array", "items": s}}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _suggest_schema() -> dict:
    s = {"type": "string"}
    cand = {"type": "object", "properties": {"site": s, "reason": s, "note": s},
            "required": ["site", "reason", "note"], "additionalProperties": False}
    return {"type": "object", "properties": {"candidates": {"type": "array", "items": cand}, "question": s},
            "required": ["candidates", "question"], "additionalProperties": False}


_SITE_IN_GOAL = re.compile(r"(?:https?://[^\s<>\"']+|(?<![\w.@-])(?:[a-z0-9-]+\.)+(?:com|in|co|org|net|io|ai|uk|de|"
                           r"fr|es|it|nl|au|ca|us|sg|ae|dev|app|info|shop|store|me|tv|xyz|test)(?:\.[a-z]{2})?(?:/[^\s<>\"']*)?)",
                           re.I)


def site_in_goal(goal: str) -> str:
    """A website named inside the prompt ("hotels in Goa on booking.com"), or ""."""
    m = _SITE_IN_GOAL.search(goal or "")
    if not m:
        return ""
    site = m.group(0).rstrip(".,;:)")
    try:
        start_url_for(site)
    except PlanError:
        return ""
    return site


def suggest_sites(goal: str) -> dict:
    """1-3 sites the AI thinks list this data, checked against the network guard and your never-scrape
    list: {"candidates": [{site, reason, note, start_url}], "dropped": [{site, reason}], "question": ""}."""
    goal = (goal or "").strip()[:MAX_GOAL_CHARS]
    if not goal:
        raise PlanError("Describe what the agent should collect.")
    if not config.ai_enabled():
        raise PlanError(f"Agent mode needs an AI key. Add {config.ai_key_name()} to .env.")
    raw = recipe_builder.ask_json(SUGGEST_SYSTEM, f"What the person wants:\n\"\"\"\n{goal}\n\"\"\"",
                                  _suggest_schema(), "site_candidates")
    candidates, dropped, seen = [], [], set()
    for c in (raw.get("candidates") or [])[:3]:
        site = str(c.get("site") or "").strip().lower()
        site = re.sub(r"^https?://", "", site).split("/")[0]
        if not site or site in seen:
            continue
        seen.add(site)
        try:
            start = start_url_for(site)
        except PlanError as e:
            dropped.append({"site": site, "reason": str(e)})
            continue
        host = fetcher.host_of(start)
        if fetcher.is_opted_out(host) or fetcher.site_settings(fetcher.domain_of(start)).get("blocked"):
            dropped.append({"site": site, "reason": "on your never-scrape list"})
            continue
        candidates.append({"site": site, "start_url": start, "reason": str(c.get("reason") or "")[:200],
                           "note": str(c.get("note") or "")[:200]})
    return {"candidates": candidates, "dropped": dropped, "question": str(raw.get("question") or "")[:300]}


class PlanError(ValueError):
    pass


def start_url_for(site: str) -> str:
    site = site.strip()
    if not site:
        raise PlanError("Name the website (or paste a link) the agent should use.")
    url = site if "://" in site else "https://" + site
    parts = urlsplit(url)
    if not parts.hostname or "." not in parts.hostname:
        raise PlanError("That doesn't look like a website address.")
    try:
        netguard.check_url(url)
    except netguard.UnsafeURL as e:
        raise PlanError(str(e)) from e
    return url if parts.path not in ("", "/") or parts.query else f"{parts.scheme}://{parts.hostname}/"


def _bare(text: str) -> str:
    return re.sub(r"[\s:]+", "", str(text or "").lower())


def checklist_for(raw: dict, request: dict) -> list[dict]:
    """The plan's checklist: "must" items from the AI (or edited in the UI), "app" items regenerated from
    the request's filters, sort and limit so they can't drift from it."""
    items, texts = [], set()
    source = raw.get("checklist")
    musts = ([c.get("text") for c in source if isinstance(c, dict) and c.get("kind") == "must"]
             if isinstance(source, list) else raw.get("must") or [])
    app_side = bool(request.get("filters") or (request.get("sort") or {}).get("column") or request.get("limit"))
    app_items = request_parser.describe(request)
    app_texts = {_bare(t) for t in app_items}
    for text in musts:
        text = " ".join(str(text or "").split())[:160]
        if _bare(text) in app_texts:           # the app applies it already ("awards: >0" = "awards > 0")
            continue
        # price, rating, sorting and "how many" are the app's job on the rows, never a must on the site
        if app_side and _APP_SIDE.search(text):
            continue
        if text and text.lower() not in texts:
            texts.add(text.lower())
            items.append({"id": f"must_{len(items) + 1}", "kind": "must", "text": text})
        if len(items) >= MAX_MUST:
            break
    for i, label in enumerate(app_items, 1):
        items.append({"id": f"app_{i}", "kind": "app", "text": label})
    return items


def clean_plan(raw: dict, goal: str, site: str) -> dict:
    """Validate a plan (from the AI or edited in the UI)."""
    given = start_url_for(site)
    start = str(raw.get("start_url") or "").strip() or given
    if not guard.on_site(start, site):
        start = given
    nav = [str(s).strip()[:200] for s in (raw.get("navigation") or []) if str(s).strip()][:MAX_NAV_STEPS]
    if not nav or "result" not in nav[-1].lower():
        nav.append("stop on the results list")
    try:
        pages = max(1, min(int(raw.get("pages") or 3), 50))
    except (TypeError, ValueError):
        pages = 3
    item_pages = raw.get("item_pages") if raw.get("item_pages") in ("yes", "no", "auto") else "auto"
    request = request_parser.clean_request(raw.get("request") or raw, goal)
    return {"start_url": start, "navigation": nav, "checklist": checklist_for(raw, request), "request": request,
            "pages": pages, "item_pages": item_pages, "auto_fix": raw.get("auto_fix", True) is not False,
            "summary": request.get("summary", ""), "question": request.get("question", "")}


def today() -> str:
    """So "Nov 5" becomes a full date (the next one from today); the planner and the agent both get it."""
    return dt.date.today().strftime("%Y-%m-%d (%A)")


def make_plan(goal: str, site: str, pages: int | None = None) -> dict:
    goal = (goal or "").strip()[:MAX_GOAL_CHARS]
    if not goal:
        raise PlanError("Describe what the agent should collect.")
    start = start_url_for(site)
    if not config.ai_enabled():
        raise PlanError(f"Agent mode needs an AI key. Add {config.ai_key_name()} to .env.")
    raw = recipe_builder.ask_json(
        SYSTEM, f"Today: {today()}\nWebsite: {start}\nWhat the person wants:\n\"\"\"\n{goal}\n\"\"\"", _schema(),
        "agent_plan")
    plan = clean_plan(raw, goal, site)
    if pages:
        plan["pages"] = max(1, min(int(pages), 50))
    return plan


def browse_task_text(plan: dict, task: str, url: str) -> str:
    """What browser-use gets for one sub-task of the ReAct loop (browse_text.py, shared with the live test)."""
    return browse_text.task_text(guard.site_domain(plan["start_url"]), url, task)

