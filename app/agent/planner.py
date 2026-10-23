"""Goal + site → a task plan the agent follows (you approve it first).

    {"start_url": "https://www.magicbricks.com/",
     "navigation": ["open the rent section", "search locality 'Whitefield, Bangalore'", "filter: 3 BHK", ...],
     "request": {...request_parser request: columns with meanings/types, filters, sort, limit...},
     "pages": 50, "item_pages": "auto", "summary": "..."}

Conditions the site's own menus can apply go into navigation; all of them also stay in the request as
row filters, so nothing is lost if the site has no such filter.
"""
from urllib.parse import urlsplit

from .. import config, netguard, recipe_builder, request_parser
from . import guard

MAX_GOAL_CHARS = 2000
MAX_NAV_STEPS = 12

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
- columns, filters, sort, limit, auto, details_hint, question, summary: the data request, exactly as for a \
  scraper (columns: snake_case name, meaning with hints that avoid mistakes, type money|number|percent|\
  rating|date|url|image|text, required; filters on those columns with op lt|lte|gt|gte|eq|ne|contains|\
  not_contains and a plain value; sort {column, desc}; limit 0 if not asked).
- pages: how many result pages to collect: 1-50; 50 when the person says all / every / complete.
- item_pages: "yes" if some wanted values are usually only on each item's own page, "no" if not needed, \
  "auto" if unsure.
- question: "" normally; one short question only if the goal is too unclear to start."""


def _schema() -> dict:
    s = {"type": "string"}
    req = request_parser._schema()
    props = {"start_url": s, "navigation": {"type": "array", "items": s},
             **req["properties"],
             "pages": {"type": "integer"},
             "item_pages": {"type": "string", "enum": ["yes", "no", "auto"]}}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


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
    return {"start_url": start, "navigation": nav, "request": request, "pages": pages,
            "item_pages": item_pages, "summary": request.get("summary", ""), "question": request.get("question", "")}


def make_plan(goal: str, site: str, pages: int | None = None) -> dict:
    goal = (goal or "").strip()[:MAX_GOAL_CHARS]
    if not goal:
        raise PlanError("Describe what the agent should collect.")
    start = start_url_for(site)
    if not config.ai_enabled():
        raise PlanError(f"Agent mode needs an AI key. Add {config.ai_key_name()} to .env.")
    raw = recipe_builder.ask_json(
        SYSTEM, f"Website: {start}\nWhat the person wants:\n\"\"\"\n{goal}\n\"\"\"", _schema(), "agent_plan")
    plan = clean_plan(raw, goal, site)
    if pages:
        plan["pages"] = max(1, min(int(pages), 50))
    return plan


def task_text(plan: dict) -> str:
    """The instructions browser-use gets: reach the results list, nothing else."""
    req = plan["request"]
    cols = ", ".join(c["name"] + (f" ({c['type']})" if c.get("type") else "")
                     for c in req["columns"] if not c.get("auto_added"))
    conditions = "; ".join(request_parser.describe(req)) or "none"
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(plan["navigation"], 1))
    return f"""Your job: reach the page that LISTS the results described below, on {guard.site_domain(plan['start_url'])}, \
using the site's own search box, categories, filters and sort controls. Do not collect the data yourself.

Plan:
{steps}

The person wants these details for each item: {cols or 'all important details'}.
Conditions (apply them with the site's own filters if it has them; skip any it doesn't have): {conditions}.

Rules:
- Stay on {guard.site_domain(plan['start_url'])}. Never log in, sign up, contact anyone, buy, apply, post, or \
type personal information. Some actions are refused by the app; if one is refused, choose another way.
- Close or decline cookie / newsletter / app pop-ups (choose "reject" or "necessary only" when offered).
- If the site shows a CAPTCHA or "verify you are human" page, stop and call done with success=false and \
the text "HUMAN CHECK".
- When the current page shows the list of matching results, call the action results_reached (with a one-line \
note of which filters you applied), then call done.
- If you can't find a way to the results within a few tries, call done with success=false and explain briefly."""
