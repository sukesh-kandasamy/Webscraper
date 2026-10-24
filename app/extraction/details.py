"""Item pages: open the first item's own page and let the AI find the extra columns there.

Shared by Guided mode (the item-page switch in step 2) and Agent mode (turned on when the request
needs details the list page doesn't show).
"""
from .. import config, db
from ..fetching import fetcher
from ..runs import jobs
from ..security import audit, netguard
from . import recipe_builder


class DetailError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def first_item_link(rows: list[dict], field: str) -> str:
    for r in rows:
        v = r.get(field)
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            return v
    return ""


def add_item_pages(recipe: dict, rows: list[dict], columns: list[dict], auto: bool = True,
                   fetch=None) -> dict:
    """Recipe with a "detail" part, built from the first item's page.

    Returns {"recipe", "link", "first", "row" (values read from that page), "missing", "notes"}.
    Raises DetailError (with an HTTP-like status) or fetcher errors from opening the item page.
    """
    field = jobs.url_field(recipe)
    link = first_item_link(rows, field) if field else ""
    if not link:
        raise DetailError(422, "The list has no link to each item's own page, so item pages can't be "
                               "opened. Add a 'url' column (the item's link) and try again.")
    try:
        netguard.check_url(link)
    except netguard.UnsafeURL as e:
        raise DetailError(400, str(e))
    if config.RESPECT_ROBOTS and not fetcher.robots_allows(link):
        raise DetailError(403, "The site's robots.txt does not allow opening its item pages.")
    ai_allowed = db.get_site_settings(fetcher.domain_of(link)).get("ai_allowed", True)
    if not config.ai_enabled() or not ai_allowed:
        raise DetailError(422, "Item pages need the AI to find their details, and AI is not available "
                               "for this site.")
    item = (fetch or fetcher.fetch_smart)(link, None)
    have = [f["name"] for f in recipe["fields"]]
    wanted = [{"name": recipe_builder.snake(str(c.get("name") or "")), "meaning": str(c.get("meaning") or ""),
               "type": c.get("type") or ""} for c in columns if c.get("name")]
    wanted = [c for c in wanted if c["name"] and c["name"] not in have]
    try:
        built = recipe_builder.build_detail(item.html, item.final_url, wanted, have, auto, redact=item.logged_in)
    except recipe_builder.RecipeBuildError as e:
        raise DetailError(422, str(e))
    audit.log("detail_recipe_built", domain=fetcher.domain_of(link), columns=len(built["detail"]["fields"]))
    out = {**recipe, "detail": {"url_field": field, **built["detail"]}}
    out.pop("column_types", None)
    first = next((r for r in rows if r.get(field) == link), rows[0] if rows else {})
    return {"recipe": out, "link": link, "first": first, "row": built["row"], "missing": built["missing"],
            "notes": built["notes"]}
