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
"""
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import regex as safe_regex   # same syntax as re, plus a timeout
from selectolax.lexbor import LexborHTMLParser, LexborNode

from . import config

_WS = re.compile(r"\s+")
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
    item = (recipe.get("item_selector") or "").strip()
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
        })
    if not fields:
        raise RecipeError("Recipe needs at least one field")
    for f in fields:
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
    for key in ("site", "notes", "page_type"):
        if recipe.get(key):
            out[key] = recipe[key]
    # Fail early on bad CSS instead of halfway through a crawl.
    probe = LexborHTMLParser("<div></div>")
    for sel in [item] + [s for f in fields for s in (f["selector"], f["label_selector"], f["value_selector"]) if s]:
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


def _node_value(node: LexborNode, attr: str, base_url: str) -> str:
    if attr == "text":
        return clean_text(node.text(separator=" "))
    val = node.attributes.get(attr) or ""
    if attr in URL_ATTRS and val:
        val = urljoin(base_url, val.strip())
    return clean_text(val)


def _extract_field(item: LexborNode, f: dict, base_url: str) -> str:
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

    node = item if not f["selector"] else item.css_first(f["selector"])
    if node is None:
        return ""
    return _apply_regex(_node_value(node, f["attr"], base_url), f["regex"])


def extract(html: str, recipe: dict, base_url: str) -> list[dict]:
    """Return one dict per item. Rows where every field is empty are dropped."""
    tree = parse_html(html)
    rows = []
    for item in tree.css(recipe["item_selector"])[:config.MAX_ITEMS_PER_PAGE]:
        row = {f["name"]: _extract_field(item, f, base_url) for f in recipe["fields"]}
        if any(row.values()):
            rows.append(row)
    return rows


def next_page_url(html: str, recipe: dict, current_url: str, page_index: int) -> str | None:
    """URL of the next page, or None. page_index is 0 for the first page fetched."""
    pag = recipe["pagination"]
    if pag["type"] == "next_link" and pag["selector"]:
        node = parse_html(html).css_first(pag["selector"])
        href = node.attributes.get("href") if node else None
        if href and not href.startswith(("javascript:", "#")):
            url = urljoin(current_url, href)
            return url if url != current_url else None
        return None
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
