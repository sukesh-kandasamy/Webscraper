"""Apply a site recipe to HTML and get rows back.

Recipe format:
{
  "item_selector": "article.product-card",      # one match per record
  "fields": [
    {"name": "name",  "selector": "h2 a", "attr": "text"},
    {"name": "url",   "selector": "h2 a", "attr": "href"},
    {"name": "price", "selector": ".price", "attr": "text", "regex": "([\\d,.]+)"},
    {"name": "moq",   "kind": "kv", "label_selector": ".spec-title",
     "value_selector": ".spec-value", "label": "MOQ"}
  ],
  "pagination": {"type": "none" | "next_link" | "url_param" | "click" | "scroll",
                 "selector": "a.next", "param": "page", "start": 1}
                 # click: press the "load more" button (selector) in a browser until no new items
                 # scroll: scroll to the bottom in a browser until no new items
}

Field options:
  selector  CSS selector relative to the item ("" = the item itself)
  attr      "text" (default) or an attribute name such as href / src
  regex     optional; keeps group 1 if present, else the whole match
  kind      "text" (default) or "kv" for label/value spec lists. With "label" set it
            returns that one value; without it, every pair as "Label: value; ...".
            "json": selector is a dot path ("offers.price") into the page's structured data
            (see structured.py): into each item when the recipe has "source": "json", otherwise
            into the page's main JSON-LD object (item pages).

Structured-data recipes: {"source": "json", "json_list": "ld:[0].itemListElement", "fields": [kind json...]}
read items from JSON-LD / embedded app state instead of CSS.

Optional keys kept with the recipe: "request" (what the user asked for: columns, filters, sort,
limit), "column_types" ({column: money|number|...}) and "detail" (a single-item recipe applied to each
item's own page: {"url_field", "item_selector", "fields"}).
"""
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import regex as safe_regex   # same syntax as re, plus a timeout
from selectolax.lexbor import LexborHTMLParser, LexborNode

from .. import config
from . import structured

_WS = re.compile(r"\s+")
ROUTE_ACTIONS = ("navigate", "click", "input", "select_dropdown", "send_keys", "scroll", "go_back", "wait")
URL_ATTRS = {"href", "src", "data-src", "data-href"}


class RecipeError(ValueError):
    pass


_COMMENT = re.compile(r"<!--.*?-->", re.S)


def parse_html(html: str) -> LexborHTMLParser:
    """Parse HTML with comments removed. React pages put <!-- --> inside text
    ("150 INR<!-- -->/Piece"), which would otherwise read as "150 INR /Piece"."""
    return LexborHTMLParser(_COMMENT.sub("", html))


_MOJIBAKE = re.compile("[ÂÃâ][\u0080-¿]")


def _fix_mojibake(s: str) -> str:
    """Repair UTF-8 text that a site double-encoded as Latin-1 (e.g. 'Â°C' -> '°C')."""
    if not _MOJIBAKE.search(s):
        return s
    try:
        return s.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def clean_text(s: str | None) -> str:
    return _fix_mojibake(_WS.sub(" ", s or "").strip())


def normalize_recipe(recipe: dict) -> dict:
    """Validate a recipe and fill in defaults. Accepts fields as a list or a {name: spec} dict."""
    if not isinstance(recipe, dict):
        raise RecipeError("Recipe must be a JSON object")
    source = recipe.get("source") or "html"
    if source not in ("html", "json"):
        raise RecipeError("source must be html or json")
    json_list = (recipe.get("json_list") or "").strip()
    if source == "json" and not json_list:
        raise RecipeError("A structured-data recipe needs 'json_list'")
    item = (recipe.get("item_selector") or "").strip() or ("(json)" if source == "json" else "")
    if not item:
        raise RecipeError("Recipe needs an 'item_selector'")
    raw_fields = recipe.get("fields") or []
    if isinstance(raw_fields, dict):
        raw_fields = [{"name": k, **(v if isinstance(v, dict) else {"selector": v})}
                      for k, v in raw_fields.items()]
    fields = []
    for f in raw_fields:
        name = clean_text(f.get("name"))
        if not name:
            raise RecipeError("Every field needs a 'name'")
        fields.append({
            "name": name,
            "kind": f.get("kind") or "text",
            "selector": f.get("selector") or "",
            "attr": f.get("attr") or "text",
            "regex": fix_regex(f.get("regex") or ""),
            "label_selector": f.get("label_selector") or "",
            "value_selector": f.get("value_selector") or "",
            "label": f.get("label") or "",
            **({"join": True} if f.get("join") else {}),
        })
    if not fields:
        raise RecipeError("Recipe needs at least one field")
    for f in fields:
        if f["kind"] not in ("text", "kv", "json"):
            raise RecipeError(f"Field {f['name']!r}: kind must be text, kv or json")
        if f["regex"]:
            check_regex(f["regex"])
    pag = recipe.get("pagination") or {}
    pagination = {
        "type": pag.get("type") or "none",
        "selector": pag.get("selector") or "",
        "param": pag.get("param") or "",
        "start": int(pag.get("start") or 1),
    }
    if pagination["type"] not in ("none", "next_link", "url_param", "click", "scroll"):
        raise RecipeError("pagination.type must be none, next_link, url_param, click or scroll")
    if pagination["type"] == "click" and not pagination["selector"]:
        raise RecipeError("pagination.type 'click' needs a selector for the load-more button")
    out = {"item_selector": item, "fields": fields, "pagination": pagination}
    if source == "json":
        out.update(source="json", json_list=json_list)
    for key in ("site", "notes", "page_type", "request", "column_types"):
        if recipe.get(key):
            out[key] = recipe[key]
    # Agent scrapes: the recorded route (steps replayed before page 1) and the plan it came from.
    if isinstance(recipe.get("route"), list) and recipe["route"]:
        steps = [st for st in recipe["route"][:40] if isinstance(st, dict) and st.get("action") in ROUTE_ACTIONS]
        if steps:
            out["route"] = steps
    if isinstance(recipe.get("agent"), dict):
        out["agent"] = {k: recipe["agent"][k] for k in ("goal", "plan", "run_id", "start_url") if k in recipe["agent"]}
    detail = recipe.get("detail")
    if isinstance(detail, dict) and detail.get("fields"):
        inner = normalize_recipe({"item_selector": detail.get("item_selector") or "body",
                                  "fields": detail["fields"]})
        out["detail"] = {"url_field": detail.get("url_field") or "url",
                         "item_selector": inner["item_selector"], "fields": inner["fields"]}
    # Fail early on bad CSS instead of halfway through a crawl.
    probe = LexborHTMLParser("<div></div>")
    css = [] if source == "json" else [item]
    css += [s for f in fields if f["kind"] != "json"
            for s in (f["selector"], f["label_selector"], f["value_selector"]) if s]
    for sel in css:
        try:
            probe.css(sel)
        except Exception as e:  # selectolax raises a plain exception on bad selectors
            raise RecipeError(f"Invalid CSS selector {sel!r}: {e}") from e
    return out


# Patterns like (a+)+ or (\w*)* can take exponential time on some inputs (ReDoS).
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\s*[+*{]")
MAX_REGEX_LENGTH = 300


def check_regex(pattern: str) -> None:
    if len(pattern) > MAX_REGEX_LENGTH:
        raise RecipeError(f"Regex too long (max {MAX_REGEX_LENGTH} characters)")
    if _NESTED_QUANTIFIER.search(pattern):
        raise RecipeError(f"Regex {pattern!r} has a nested quantifier like (a+)+, which can hang; "
                          "simplify it")
    try:
        safe_regex.compile(pattern)
    except safe_regex.error as e:
        raise RecipeError(f"Invalid regex {pattern!r}: {e}") from e


_OVER_ESCAPED = re.compile(r"\\\\(?=[dDwWsSbB.$()\[\]+*?{}|^/-])")


def fix_regex(pattern: str) -> str:
    """Undo JSON over-escaping that LLMs often produce (r'\\\\d' -> r'\\d')."""
    return _OVER_ESCAPED.sub(r"\\", pattern) if pattern else pattern


def _apply_regex(value: str, pattern: str) -> str:
    if not pattern or not value:
        return value
    try:
        m = safe_regex.search(pattern, value, safe_regex.I, timeout=config.REGEX_TIMEOUT_S)
    except TimeoutError:
        return ""          # pathological input: drop this value rather than hang the job
    except safe_regex.error as e:
        raise RecipeError(f"Invalid regex {pattern!r}: {e}") from e
    if not m:
        return ""
    return clean_text(m.group(1) if m.groups() else m.group(0))


_CUT = re.compile(r"\s*(\.\.\.|…)$")


def _node_value(node: LexborNode, attr: str, base_url: str) -> str:
    if attr == "text":
        text = clean_text(node.text(separator=" "))
        # Sites shorten long titles on cards ("Tastes Like Fear (DI ...") and keep the full text in a
        # title / aria-label attribute: use that when it starts with the visible part.
        if _CUT.search(text):
            prefix = _CUT.sub("", text).lower()
            for n in (node, node.css_first("[title]"), node.css_first("[aria-label]")):
                if n is None:
                    continue
                for a in ("title", "aria-label"):
                    full = clean_text(n.attributes.get(a) or "")
                    if len(full) > len(prefix) and full.lower().startswith(prefix):
                        return full
        return text
    val = node.attributes.get(attr) or ""
    if attr in URL_ATTRS and val:
        val = urljoin(base_url, val.strip())
    return clean_text(val)


def _json_value(data: dict, f: dict, base_url: str) -> str:
    value = clean_text(data.get(f["selector"]) or "")
    if value and re.search(r"url|image|link|href|logo|photo", f["selector"].split(".")[-1], re.I) \
            and not value.startswith(("http://", "https://")) and value.startswith(("/", "./")):
        value = urljoin(base_url, value)
    return _apply_regex(value, f["regex"])


def _extract_field(item: LexborNode, f: dict, base_url: str, page_data=None) -> str:
    if f["kind"] == "json":
        return _json_value(page_data() if page_data else {}, f, base_url)
    if f["kind"] == "kv":
        scope = item.css_first(f["selector"]) if f["selector"] else item
        if scope is None:
            return ""
        labels = [clean_text(n.text(separator=" ")).rstrip(":") for n in scope.css(f["label_selector"])]
        values = [clean_text(n.text(separator=" ")) for n in scope.css(f["value_selector"])]
        pairs = list(zip(labels, values))
        if f["label"]:
            want = f["label"].lower()
            value = next((v for k, v in pairs if k.lower() == want), "") or \
                next((v for k, v in pairs if want in k.lower()), "")
        else:
            value = "; ".join(f"{k}: {v}" for k, v in pairs if k or v)
        return _apply_regex(value, f["regex"])

    if f.get("join") and f["selector"]:
        # List-like columns (tags, skills, amenities): every match, joined.
        values = [v for v in (_node_value(n, f["attr"], base_url) for n in item.css(f["selector"])) if v]
        return _apply_regex(", ".join(dict.fromkeys(values)), f["regex"])
    node = item if not f["selector"] else item.css_first(f["selector"])
    if node is None:
        return ""
    return _apply_regex(_node_value(node, f["attr"], base_url), f["regex"])


def extract(html: str, recipe: dict, base_url: str) -> list[dict]:
    """Return one dict per item. Rows where every field is empty are dropped."""
    rows = []
    if recipe.get("source") == "json":
        for data in structured.items_at(html, recipe["json_list"])[:config.MAX_ITEMS_PER_PAGE]:
            row = {f["name"]: _json_value(data, f, base_url) for f in recipe["fields"]}
            if any(row.values()):
                rows.append(row)
        return rows
    tree = parse_html(html)
    cache: dict = {}

    def page_data() -> dict:          # the page's JSON-LD, read once and only if a field needs it
        if "d" not in cache:
            cache["d"] = structured.main_entity(html)
        return cache["d"]

    for item in tree.css(recipe["item_selector"])[:config.MAX_ITEMS_PER_PAGE]:
        row = {f["name"]: _extract_field(item, f, base_url, page_data) for f in recipe["fields"]}
        if any(row.values()):
            rows.append(row)
    return rows


def extract_detail(html: str, detail: dict, base_url: str) -> dict:
    """Values from one item's own page (the first match of the detail recipe's item_selector)."""
    rows = extract(html, {"item_selector": detail["item_selector"], "fields": detail["fields"],
                          "pagination": {"type": "none"}}, base_url)
    return rows[0] if rows else {}


_PREV = re.compile(r"\bprev|previous|«|‹|←|\bback\b|\bfirst\b", re.I)
_NEXT = re.compile(r"\bnext\b|»|›|→", re.I)


def _link_words(a: LexborNode) -> str:
    parent = a.parent
    return " ".join([clean_text(a.text()), a.attributes.get("rel") or "", a.attributes.get("class") or "",
                     a.attributes.get("aria-label") or "", (parent.attributes.get("class") or "") if parent else ""])


def _same_listing(url: str, current: str) -> bool:
    """Is url another page of the listing at current? Same address apart from numbers, or the
    current address with a page part added (/tag/x/ -> /tag/x/page/2/)."""
    a, b = urlsplit(url), urlsplit(current)
    if a.netloc != b.netloc:
        return False
    # Every non-page parameter of this page (?q=New) must be kept: a link that drops the search isn't a next page.
    shape = lambda q: {(k, re.sub(r"\d+", "#", v)) for k, v in parse_qsl(q, keep_blank_values=True)}
    if not shape(b.query) <= shape(a.query):
        return False
    if re.sub(r"\d+", "#", a.path) == re.sub(r"\d+", "#", b.path):
        return a.path != b.path or a.query != b.query
    return a.path.startswith(b.path.rstrip("/") + "/") and re.search(r"\d", a.path[len(b.path):] + a.query) is not None


def _page_number_match(url: str, current: str, wanted: int) -> bool:
    """Does url carry page number `wanted` where it differs from the current address?"""
    mine = re.findall(r"\d+", urlsplit(current).path + "?" + urlsplit(current).query)
    return str(wanted) in [n for n in re.findall(r"\d+", urlsplit(url).path + "?" + urlsplit(url).query)
                           if n not in mine or n == str(wanted)]


def next_page_url(html: str, recipe: dict, current_url: str, page_index: int,
                  visited: set | None = None) -> str | None:
    """URL of the next page, or None. page_index is 0 for the first page fetched.

    A next_link selector can match more than the "next" link (page numbers, "previous"): links that
    say previous / first, and pages already visited, are skipped; a link that says next wins."""
    pag = recipe["pagination"]
    if pag["type"] == "next_link" and pag["selector"]:
        visited = visited or set()
        options, numbered = [], {}
        for node in parse_html(html).css(pag["selector"]):
            href = node.attributes.get("href")
            if not href or href.startswith(("javascript:", "#")):
                continue
            url = urljoin(current_url, href)
            words = _link_words(node)
            label = clean_text(node.text())
            if label.isdigit():
                numbered.setdefault(label, url)      # page-number links: "1" -> its address
            if url == current_url or url in visited or (_PREV.search(words) and not _NEXT.search(words)):
                continue
            options.append((0 if _NEXT.search(words) else 1, url))
        # The link numbered like this page is this page under another address (?page_num=1 on the
        # first results page): never "next", even when the site labels it so.
        this_page = numbered.get(str(page_index + 1))
        options = [(rank, u) for rank, u in options if u != this_page]
        nexts = [u for rank, u in options if rank == 0]
        if nexts:
            return nexts[0]
        if numbered.get(str(page_index + 2)) in [u for _, u in options]:
            return numbered[str(page_index + 2)]
        # No link says "next": only follow links shaped like this page's address, so a loose selector
        # can't wander off to other categories or tags; prefer page n+1.
        same = [u for _, u in options if _same_listing(u, current_url)]
        return next((u for u in same if _page_number_match(u, current_url, page_index + 2)), same[0] if same else None)
    if pag["type"] == "url_param" and pag["param"]:
        parts = urlsplit(current_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query[pag["param"]] = str(pag["start"] + page_index + 1)
        return urlunsplit(parts._replace(query=urlencode(query)))
    return None


def field_report(rows: list[dict], recipe: dict) -> dict[str, float]:
    """Share of rows where each field has a value (0..1)."""
    if not rows:
        return {f["name"]: 0.0 for f in recipe["fields"]}
    return {f["name"]: round(sum(1 for r in rows if r.get(f["name"])) / len(rows), 2)
            for f in recipe["fields"]}
