"""Ask an AI model (Groq or Claude) to write a recipe (CSS selectors) for a page, then check it.

The model is only called once per new site. The saved recipe is reused for every later scrape.
"""
import json
import re
import time
from collections import defaultdict

import anthropic
from selectolax.lexbor import LexborHTMLParser, LexborNode

from .. import config
from ..security import privacy
from . import extractor, normalize, structured

DROP_TAGS = ["script", "style", "svg", "noscript", "iframe", "link", "meta", "head", "template"]
KEEP_ATTRS = {"class", "id", "href", "src", "itemprop", "title", "aria-label", "role", "data-src"}
# data-* attributes that usually carry values worth extracting (ratings, prices, ids of products);
# others (tracking ids, render ids, UUIDs, widget config) are noise that hides the real content.
VALUE_DATA_ATTR = re.compile(r"^data-(rating|stars?|score|price|amount|currency|reviews?|count|qty|"
                             r"stock|brand|title|name|asin|sku|product-?id|value|src)$", re.I)
_ID_LIKE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-|^amzn1\.|^[A-Za-z0-9_\-]{24,}$")


def _keep_data_attr(name: str, value: str) -> bool:
    return bool(VALUE_DATA_ATTR.match(name)) and len(value) <= 40 and not _ID_LIKE.match(value)
MAX_SAMPLE_CHARS = 8000
MAX_CANDIDATES = 4


class RecipeBuildError(Exception):
    pass


# ---- page summarising ----------------------------------------------------

def _sig(node: LexborNode) -> str:
    classes = (node.attributes.get("class") or "").split()
    # Hashed CSS-in-JS classes (sc-a1b2c3, css-1x2y3z) change between deploys, so skip them.
    stable = [c for c in classes
              if not re.match(r"^(sc|css|jsx)-[0-9a-z-]+$|^(?=.*[a-z])(?=.*[A-Z])[a-zA-Z]{6}$", c)]
    return node.tag + "".join(f".{c}" for c in stable[:2])


def _path_hint(node: LexborNode, depth: int = 3) -> str:
    parts, cur = [], node
    while cur is not None and cur.tag not in ("body", "html", "-document") and len(parts) < depth:
        parts.append(_sig(cur))
        cur = cur.parent
    return " > ".join(reversed(parts))


_PAGE_PARAMS = re.compile(r"^(page|p|pg|pn|pageno|page_no|pagenum|pagenumber|start|offset|from|skip)$", re.I)
MAX_URL_CHARS = 100
MAX_CLASS_CHARS = 100


def short_url(href: str) -> str:
    """Shorten a link for the AI prompt: keep the path and the query parameters that matter for
    paging (plus the first one, usually the search term); drop tracking parameters. Long shop URLs
    cost many tokens and tell the AI nothing about selectors."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return href
    parts = urlsplit(href)
    params = parse_qsl(parts.query, keep_blank_values=True)
    kept = params[:1] + [(k, v) for k, v in params[1:] if _PAGE_PARAMS.match(k)]
    path = parts.path if len(parts.path) <= 60 else parts.path[:57] + "..."
    out = urlunsplit((parts.scheme, parts.netloc, path, urlencode(kept), ""))
    if len(params) > len(kept):
        out += "&…" if kept else "?…"
    return out[:MAX_URL_CHARS]


def _compact(node: LexborNode) -> str:
    html = LexborHTMLParser(node.html)
    for tag in DROP_TAGS:
        for n in html.css(tag):
            n.decompose()
    for n in html.css("*"):
        for attr in list(n.attributes):
            value = n.attributes[attr] or ""
            # Keep value-like data-* attributes (data-rating="4"); drop tracking ones.
            keep = attr in KEEP_ATTRS or (attr.startswith("data-") and _keep_data_attr(attr, value))
            if not keep:
                del n.attrs[attr]
            elif attr in ("href", "src", "data-src"):
                n.attrs[attr] = short_url(value) if attr == "href" else value[:80]
            elif attr == "class" and len(value) > MAX_CLASS_CHARS:
                n.attrs[attr] = value[:MAX_CLASS_CHARS] + "…"
            elif len(value) > 150:
                n.attrs[attr] = value[:150] + "…"
    body = html.body
    text = body.inner_html if body is not None else html.html
    text = re.sub(r"<!--.*?-->", "", text or "", flags=re.S)
    text = re.sub(r"\s+", " ", text)
    return text[:MAX_SAMPLE_CHARS]


OUTLINE_LINES = 45
_OUTLINE_ATTRS = ("aria-label", "title", "alt", "itemprop", "datetime", "content")
_SEMANTIC_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "a", "img", "button", "time", "li", "dt", "dd",
                  "td", "th", "p", "strong", "b"}


def _rel_path(el: LexborNode, item: LexborNode, depth: int = 3) -> str:
    """CSS path of el inside item, built from the nearest meaningful ancestors (descendant form)."""
    parts, cur = [], el
    while cur is not None and cur is not item and cur.mem_id != item.mem_id:
        sig = _sig(cur)
        if cur.tag in ("td", "th") and cur.parent is not None:
            cells = [c for c in cur.parent.iter() if c.tag in ("td", "th")]
            pos = next((i for i, c in enumerate(cells, 1) if c.mem_id == cur.mem_id), 0)
            if pos:
                sig += f":nth-child({pos})"
        if cur is el or "." in sig or cur.tag in _SEMANTIC_TAGS:
            parts.append(sig)
        if len(parts) >= depth:
            break
        cur = cur.parent
    return " ".join(reversed(parts)) or el.tag


def outline(item: LexborNode, max_lines: int = OUTLINE_LINES) -> list[dict]:
    """The useful parts of one listing card, compactly: for every element that has its own text,
    a link, an image or a value attribute, its CSS path inside the card plus what it holds.
    Big cards (shops wrap each product in dozens of layout divs) fit in a few hundred characters."""
    lines, seen = [], set()
    for el in item.css("*"):
        if el.tag in DROP_TAGS:
            continue
        own = extractor.clean_text(el.text(deep=False))
        attrs = []
        if el.tag == "a" and el.attributes.get("href"):
            attrs.append(("href", short_url(el.attributes["href"])))
        if el.tag == "img":
            attrs.append(("src", "…"))
        for k, v in el.attributes.items():
            v = (v or "").strip()
            if v and (k in _OUTLINE_ATTRS or (k.startswith("data-") and _keep_data_attr(k, v))):
                attrs.append((k, extractor.clean_text(v)[:50]))
        if not own and not attrs:
            continue
        entry = {"path": _rel_path(el, item, depth=2), "attrs": attrs, "text": own[:60]}
        key = (entry["path"], tuple(attrs), entry["text"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(entry)
        if len(lines) >= max_lines:
            break
    return lines


OUTLINE_CARDS = 4


def merged_outline(items: list[LexborNode]) -> list[dict]:
    """Outline of several cards combined: each distinct element (same path and attribute names)
    appears once, with an example from the first card that has it. Cards differ (one shows a price,
    the next says "See options"), so looking at a few of them shows every field that exists."""
    merged, index = [], {}
    for item in items[:OUTLINE_CARDS]:
        for e in outline(item):
            key = (e["path"], tuple(k for k, _ in e["attrs"]), bool(e["text"]))
            if key not in index:
                index[key] = len(merged)
                merged.append(e)
    return merged[:OUTLINE_LINES + 15]


def render_outline(entries: list[dict], text_fn, max_chars: int) -> str:
    out, used = [], 0
    for e in entries:
        line = e["path"]
        for k, v in e["attrs"]:
            line += f' [{k}="{text_fn(v) if k not in ("href", "src") else v}"]'
        if e["text"]:
            line += f' → "{text_fn(e["text"])}"'
        if used + len(line) > max_chars:
            out.append("…")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def find_candidates(html: str) -> list[dict]:
    """Find groups of sibling elements that look like repeated records (product cards, rows…)."""
    tree = extractor.parse_html(html)
    for tag in DROP_TAGS:
        for n in tree.css(tag):
            n.decompose()
    scored = []
    for parent in tree.css("body *"):
        children = [c for c in parent.iter() if c.tag and not c.tag.startswith("-")]
        if len(children) < 4:
            continue
        groups: dict[str, list[LexborNode]] = defaultdict(list)
        for c in children:
            groups[_sig(c)].append(c)
        for sig, nodes in groups.items():
            if len(nodes) < 4:
                continue
            lens = [len(extractor.clean_text(n.text(separator=" "))) for n in nodes]
            avg = sum(lens) / len(lens)
            # Table rows with several cells are records even when short ("Argo · 7 · 3"); other short
            # repeated blocks are usually menus or tags.
            table_rows = nodes[0].tag == "tr" and len(nodes[0].css("td")) >= 2
            if avg < (6 if table_rows else 30):
                continue
            links = sum(1 for n in nodes if n.css_first("a[href]"))
            score = len(nodes) * min(avg, 400) * (1.5 if links >= len(nodes) / 2 else 1) * (4 if table_rows else 1)
            scored.append((score, nodes))
    scored.sort(key=lambda x: -x[0])

    out, seen = [], set()
    for score, nodes in scored:
        hint = _path_hint(nodes[0])
        counts = _page_counts(tree, nodes[0])
        # Listings split across several containers show up as separate groups with the same
        # inner card class. Keep only the first; its page-wide count already covers the rest.
        key = counts[-1][0] if counts else hint
        if hint in seen or key in seen:
            continue
        seen.update((hint, key))
        out.append({"selector_hint": hint, "count": len(nodes), "page_counts": counts,
                    "samples": [_compact(n) for n in nodes[:2]],
                    "outline": merged_outline(nodes) if len(out) < 2 else []})
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def _page_counts(tree: LexborHTMLParser, node: LexborNode) -> list[tuple[str, int]]:
    """For the item and its single-child wrappers, how often each stable class selector occurs."""
    out, cur = [], node
    for _ in range(3):
        sig = _sig(cur)
        if "." in sig:
            try:
                out.append((sig, len(tree.css(sig))))
            except Exception:
                pass
        kids = [c for c in cur.iter() if c.tag and not c.tag.startswith("-")]
        if not kids:
            break
        if len(kids) > 1:
            # Still report the first child: often the real card inside a styling wrapper.
            first = _sig(kids[0])
            if "." in first and not out:
                out.append((first, len(tree.css(first))))
            break
        cur = kids[0]
    return out


MAX_LINK_HINTS = 6
MAX_BUTTON_HINTS = 3


def _pagination_hints(html: str) -> list[str]:
    """Up to a few "next" / page-number links and "load more" buttons, with short URLs."""
    tree = extractor.parse_html(html)
    hints, seen = [], set()
    for a in tree.css("a[href]"):
        txt = extractor.clean_text(a.text())
        cls = (a.attributes.get("class") or "")[:80]
        rel = a.attributes.get("rel") or ""
        # "Next", "Next →", "›", "»", page numbers, or rel/class containing "next"
        is_next = ("next" in txt.lower() and len(txt) <= 12) or re.fullmatch(r"[›»>]|\d{1,3}", txt)
        if not (is_next or "next" in (cls + rel).lower()):
            continue
        # One example per link style is enough: page 2, page 3 ... look the same.
        key = (cls, "num" if txt.isdigit() else txt.lower())
        if key in seen:
            continue
        seen.add(key)
        hints.append(f'<a class="{cls}" rel="{rel}" href="{short_url(a.attributes.get("href") or "")}">{txt}</a>')
        if len(hints) >= MAX_LINK_HINTS:
            break
    buttons = 0
    for b in tree.css("button, a, div[role=button]"):
        txt = extractor.clean_text(b.text())
        # "Load more", "Show more products", "More", "View more…": short labels with "more" in them
        if re.search(r"\bmore\b", txt, re.I) and len(txt.split()) <= 4:
            ident = b.attributes.get("id") or ""
            cls = (b.attributes.get("class") or "")[:80]
            hints.append(f'<{b.tag} id="{ident}" class="{cls}">{txt}</{b.tag}>')
            buttons += 1
        if buttons >= MAX_BUTTON_HINTS:
            break
    return hints


# ---- AI call -----------------------------------------------------------

SYSTEM = """You write extraction recipes for a web scraper. A recipe is a set of CSS selectors \
that a deterministic parser (lexbor CSS engine) applies to raw HTML, with no JavaScript.

Rules:
- item_selector must match exactly one element per record (product/listing card).
- Field selectors are relative to the item element. Use "" to mean the item itself.
- attr is "text" for visible text, or an attribute name (href, src, title, ...). Links: use href.
- Prefer stable, meaningful class names. Never use hashed classes like "sc-c6d82cff-0" or "css-1x2y3z", \
and avoid :nth-child unless there is no alternative.
- regex is optional (Python syntax, case-insensitive). Write it as it would appear in a Python raw string: one backslash, e.g. ([\\d,.]+), not double backslashes. If it has a group, group 1 is kept. Use it to \
split values like "Price: 150 INR/Piece" into price "150" and unit "Piece".
- kind "kv" reads spec lists made of label/value pairs: label_selector and value_selector match the \
labels and values inside the item, label picks one pair by its label text (e.g. "MOQ"). Leave label "" \
to return all pairs. For kind "text", leave label_selector, value_selector and label as "".
- If a requested field does not exist in the HTML, still include it with selector "" and attr "none".
- pagination: "next_link" with a selector that matches ONLY the next-page anchor (e.g. "li.next a", "a[rel=next]"), never the previous-page link or the page numbers; "url_param" with the query \
parameter name; "click" with the selector of a "Load more" / "Show more" button (the scraper will \
press it in a real browser); "scroll" if the page clearly loads more items on scrolling and has no \
links or button; "none" if you cannot see any pagination.
- Only use selectors that you can see in the provided HTML.
- Table rows: when items are <tr> rows, td:nth-child(n) / th:nth-child(n) selectors are fine.
- Derived columns: when a requested value has no element of its own but is part of another value (the brand is the first word of the title, the city is the last part of the address, bedrooms are in "3 BHK Flat"), use that element's selector with a regex that keeps only the wanted part.
- Columns with a meaning: follow it exactly (e.g. the selling price, not the struck-out original price). Columns typed money / number / percent / rating must select an element whose text contains the number.
- Structured data: when the page carries the items as JSON (listed under "Structured data lists"), you may set source "json", json_list to that list's path, and give every field kind "json" with selector set to one of its key paths (e.g. "offers.price"). Use it when it has the requested fields and at least as many items as the HTML cards; otherwise use source "html" (and json_list "").
- kind "json" with source "html" reads the page's main JSON-LD object; use it only on single-item pages.

Choosing columns (when asked to find the important fields yourself):
- First decide page_type from what the items are.
- Include every field the items actually show that a person collecting this data would want, as
  separate columns (aim for 5-15). Skip decoration: badges like "Sponsored", button labels, icons.
- Typical columns by page type (use the ones that exist on the page, add others you see):
  ecommerce: name, brand, price, original_price, discount, currency, rating, reviews, availability,
             delivery, seller, url, image
  jobs: title, company, location, salary, experience, job_type, work_mode, skills, posted, url
  real_estate: title, price, price_per_sqft, location, area_sqft, bedrooms, bathrooms, property_type,
               furnishing, builder_or_agent, posted, url
  stocks_finance: symbol, name, price, change, change_percent, volume, market_cap, pe_ratio, high, low
  directory (B2B / local businesses): name, company, category, location, rating, reviews, years, url
  news_articles: title, summary, author, published, category, url
  events: title, date, time, venue, city, price, organizer, url
- Column names: short snake_case English (e.g. "salary", "posted", "area_sqft"). Split combined
  values with regex when it helps (price vs currency, "3 BHK" -> bedrooms "3").
- Fields the user asked for must always be included (use selector "" and attr "none" if absent)."""


PAGE_TYPES = ["ecommerce", "jobs", "real_estate", "stocks_finance", "directory", "news_articles",
              "events", "other"]


def _schema(field_names: list[str], auto: bool = False) -> dict:
    s = {"type": "string"}
    # In auto mode the model names the columns itself; otherwise only the requested names are allowed.
    name = {"type": "string"} if auto or not field_names else {"type": "string", "enum": field_names}
    return {
        "type": "object",
        "properties": {
            "item_selector": s,
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": name,
                        "kind": {"type": "string", "enum": ["text", "kv", "json"]},
                        "selector": s, "attr": s, "regex": s,
                        "label_selector": s, "value_selector": s, "label": s,
                    },
                    "required": ["name", "kind", "selector", "attr", "regex",
                                 "label_selector", "value_selector", "label"],
                    "additionalProperties": False,
                },
            },
            "pagination": {
                "type": "object",
                "properties": {
                    "type": {"type": "string",
                             "enum": ["none", "next_link", "url_param", "click", "scroll"]},
                    "selector": s, "param": s, "start": {"type": "integer"},
                },
                "required": ["type", "selector", "param", "start"],
                "additionalProperties": False,
            },
            "page_type": {"type": "string", "enum": PAGE_TYPES},
            "source": {"type": "string", "enum": ["html", "json"]},
            "json_list": s,
            "notes": s,
        },
        "required": ["item_selector", "fields", "pagination", "page_type", "source", "json_list", "notes"],
        "additionalProperties": False,
    }


class _Refused(Exception):
    pass


class PromptTooLarge(RecipeBuildError):
    """The provider rejected the request as too large (e.g. Groq's tokens-per-request limit)."""

    def __init__(self, message: str, limit: int | None = None, requested: int | None = None):
        super().__init__(message)
        self.limit, self.requested = limit, requested


_RETRY_HINT = re.compile(r"try again in\s*([\d.]+)\s*(ms|s|m)", re.I)
MAX_AI_ATTEMPTS = 4
MAX_AI_WAIT_S = 90.0


def _retry_delay(exc: Exception, attempt: int) -> float:
    """How long to wait before retrying an AI call, from the server's hint if it gave one."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    for key in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        value = headers.get(key)
        if value:
            m = _RETRY_HINT.search(f"try again in {value}") or re.match(r"^\s*([\d.]+)\s*$", value)
            if m:
                return _hint_seconds(m)
    m = _RETRY_HINT.search(str(exc))
    if m:
        return _hint_seconds(m)
    return min(2.0 * 2 ** attempt, 30.0)


def _hint_seconds(m: re.Match) -> float:
    n = float(m.group(1))
    unit = m.group(2).lower() if m.lastindex and m.lastindex >= 2 and m.group(2) else "s"
    return n / 1000 if unit == "ms" else n * 60 if unit == "m" else n


def _is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status in (429, 500, 502, 503, 504, 529):
        return True
    # Groq's strict JSON mode rejects a generation that broke the schema; a new attempt usually works.
    if "json_validate_failed" in str(exc) or "does not match the expected schema" in str(exc):
        return True
    return isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)) or \
        type(exc).__name__ in ("APIConnectionError", "APITimeoutError")


def _with_retries(call, *args):
    """Run an AI call, waiting and retrying on rate limits and transient server errors."""
    waited = 0.0
    for attempt in range(MAX_AI_ATTEMPTS):
        try:
            return call(*args)
        except Exception as e:  # SDK-specific classes differ between providers
            if getattr(e, "status_code", None) == 413 or "request too large" in str(e).lower():
                m = re.search(r"Limit\s+(\d+),\s*Requested\s+(\d+)", str(e))
                raise PromptTooLarge("The page sample is too large for the AI provider's limit.",
                                     int(m.group(1)) if m else None,
                                     int(m.group(2)) if m else None) from e
            if not _is_retryable(e) or attempt == MAX_AI_ATTEMPTS - 1:
                raise
            delay = 0.5 if "json_validate_failed" in str(e) else _retry_delay(e, attempt) + 0.5
            if waited + delay > MAX_AI_WAIT_S:
                wait = (f"about {round(delay / 60)} minutes" if delay >= 90 else f"{delay:.0f} seconds")
                raise RecipeBuildError(
                    f"The AI provider is rate-limited: it asks to wait {wait} before the next request. "
                    "Your plan's limit may be used up for now.") from e
            time.sleep(delay)
            waited += delay


def _ask_claude(messages: list[dict], schema: dict, system: str = SYSTEM, name: str = "recipe") -> str:
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    kwargs = dict(
        model=config.CLAUDE_MODEL,
        max_tokens=16000,
        system=system,
        messages=messages,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
    )
    try:
        # Server-side fallback re-runs a declined request on another model instead of failing.
        resp = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    except anthropic.BadRequestError as e:
        if "fallback" not in str(e).lower():
            raise
        resp = client.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise _Refused()
    return next((b.text for b in resp.content if b.type == "text"), "")


def _ask_groq(messages: list[dict], schema: dict, system: str = SYSTEM, name: str = "recipe") -> str:
    from groq import Groq  # imported lazily so the app runs without the package when unused

    client = Groq(api_key=config.GROQ_API_KEY)
    resp = client.chat.completions.create(
        model=config.GROQ_MODEL,
        messages=[{"role": "system", "content": system}, *messages],
        reasoning_effort="medium",
        max_completion_tokens=8000,
        response_format={"type": "json_schema",
                         "json_schema": {"name": name, "strict": True, "schema": schema}},
    )
    return resp.choices[0].message.content or ""


class OpenAIError(Exception):
    """An error answer from the OpenAI API (status_code / response feed the retry logic)."""

    def __init__(self, message: str, status_code: int, response=None):
        super().__init__(message)
        self.status_code, self.response = status_code, response


def _ask_openai(messages: list[dict], schema: dict, system: str = SYSTEM, name: str = "recipe") -> str:
    """OpenAI Chat Completions with strict structured output (plain HTTPS: no extra package)."""
    import httpx

    r = httpx.post(f"{config.OPENAI_BASE_URL}/chat/completions", timeout=180,
                   headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                   json={"model": config.OPENAI_MODEL,
                         "messages": [{"role": "system", "content": system}, *messages],
                         "max_completion_tokens": 16000,
                         "response_format": {"type": "json_schema",
                                             "json_schema": {"name": name, "strict": True, "schema": schema}}})
    if r.status_code != 200:
        try:
            detail = r.json().get("error", {}).get("message", "")
        except ValueError:
            detail = r.text[:300]
        if r.status_code == 400 and "context_length" in r.text:
            r.status_code = 413          # same handling as "request too large"
        raise OpenAIError(f"Error code: {r.status_code} - {detail}", r.status_code, r)
    choice = r.json()["choices"][0]
    if choice["message"].get("refusal"):
        raise _Refused()
    return choice["message"].get("content") or ""


_ASK = {"groq": _ask_groq, "anthropic": _ask_claude, "openai": _ask_openai}


def _ask_model(messages: list[dict], field_names: list[str], auto: bool = False) -> str:
    """Send the conversation to the configured provider and return its JSON text."""
    ask = _ASK.get(config.AI_PROVIDER, _ask_claude)
    return _with_retries(ask, messages, _schema(field_names, auto))


def ask_json(system: str, prompt: str, schema: dict, name: str) -> dict:
    """One structured AI call with its own instructions and schema (used for reading requests and
    item pages). Raises RecipeBuildError when the answer can't be used."""
    ask = _ASK.get(config.AI_PROVIDER, _ask_claude)
    try:
        text = _with_retries(ask, [{"role": "user", "content": prompt}], schema, system, name)
    except _Refused:
        raise RecipeBuildError("The AI model declined this request.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RecipeBuildError(f"The AI model returned invalid JSON: {e}") from e


def _fit_samples(candidates: list[dict], budget: int) -> list[list[str]]:
    """Choose which HTML samples to send, best first, so the prompt stays within budget chars."""
    order = [(i, 0) for i in range(len(candidates))]
    if candidates:
        order.insert(1, (0, 1))  # a second sample of the top candidate comes early
    order += [(i, 1) for i in range(1, len(candidates))]
    chosen: list[list[str]] = [[] for _ in candidates]
    left = budget
    for i, j in order:
        if j >= len(candidates[i]["samples"]) or left < 600:
            continue
        sample = candidates[i]["samples"][j]
        # The top candidate's first sample may use up to 60% of the budget, others less.
        cap = int(budget * (0.6 if (i, j) == (0, 0) else 0.3))
        cut = sample[:min(cap, left)]
        if len(cut) < len(sample):
            cut += " …(cut)"
        chosen[i].append(cut)
        left -= len(cut)
    return chosen


def _field_debug(html: str, recipe: dict, weak: list[str]) -> str:
    """Show what the weak fields' selectors actually matched, so the model can fix them."""
    if recipe.get("source") == "json":
        items = structured.items_at(html, recipe["json_list"])
        return f"\njson_list {recipe['json_list']} has {len(items)} items; first item keys: " \
               f"{list(items[0])[:30] if items else []}"
    all_items = extractor.parse_html(html).css(recipe["item_selector"])
    lines = []
    for f in recipe["fields"]:
        if f["name"] not in weak:
            continue
        for n, item in enumerate(all_items[:3], 1):
            if f["kind"] == "kv":
                scope = item.css_first(f["selector"]) if f["selector"] else item
                labels = [extractor.clean_text(x.text(separator=" "))
                          for x in (scope.css(f["label_selector"]) if scope else [])]
                lines.append(f"- {f['name']} item {n}: labels found = {labels[:10]}")
            else:
                node = item.css_first(f["selector"]) if f["selector"] else item
                raw = extractor.clean_text(node.text(separator=" ")) if node else ""
                lines.append(f"- {f['name']} item {n}: selector matched "
                             + (f"text {raw[:200]!r} (before regex)" if node else "nothing"))
    out = f"\nitem_selector matched {len(all_items)} elements."
    if lines:
        out += "\nWhat the weak fields matched:\n" + "\n".join(lines)
    return out


def _retry_prompt(prompt: str, previous: str, problem: str) -> str:
    return (f"{prompt}\n\nYour previous recipe was:\n```json\n{previous[:2000]}\n```\n"
            f"Problem: {problem[:1500]}\n"
            "Return a corrected recipe, using only selectors visible in the HTML above.")


LIST_COLUMN = re.compile(r"(tags|skills|categories|keywords|features|amenities|labels|genres|authors|colou?rs|sizes|"
                         r"languages|benefits|perks|highlights|topics|badges|facilities|specialities|specialties)$", re.I)


def mark_list_columns(html: str, recipe: dict) -> dict:
    """Columns named like lists (tags, skills, amenities...) whose selector matches several elements in
    most items collect all of them, joined with commas, instead of only the first."""
    if recipe.get("source") == "json":
        return recipe
    items = extractor.parse_html(html).css(recipe["item_selector"])[:10]
    if not items:
        return recipe
    for f in recipe["fields"]:
        if f["kind"] != "text" or not f["selector"] or f.get("join") or not LIST_COLUMN.search(f["name"]):
            continue
        try:
            multi = sum(1 for it in items if len(it.css(f["selector"])) > 1)
        except Exception:
            continue
        if multi >= max(1, len(items) // 2):
            f["join"] = True
    return recipe


def check_pagination(html: str, url: str, recipe: dict) -> dict:
    """The AI's next-page selector must match something on the page. If it matches nothing (or the AI
    saw no pagination) but the page has a "next" link or a link to page 2 of the same listing, use the
    page's links instead: next_page_url only follows "next" links and same-listing page numbers."""
    pag = recipe["pagination"]
    if pag["type"] not in ("none", "next_link") or recipe.get("source") == "json":
        return recipe
    if pag["type"] == "next_link" and pag["selector"]:
        try:
            if extractor.parse_html(html).css(pag["selector"]):
                return recipe
        except Exception:
            pass
    loose = {**recipe, "pagination": {**pag, "type": "next_link", "selector": "a[href]"}}
    nxt = extractor.next_page_url(html, loose, url, 0)
    if nxt and (extractor._NEXT.search(nxt) or extractor._page_number_match(nxt, url, 2)
                or _says_next(html, nxt)):
        return loose
    return recipe


def _says_next(html: str, link: str) -> bool:
    from urllib.parse import urljoin
    for a in extractor.parse_html(html).css("a[href]"):
        if extractor._NEXT.search(extractor._link_words(a)) and urljoin(link, a.attributes.get("href") or "") == link:
            return True
    return False


def _drop_missing(recipe: dict) -> dict:
    recipe["fields"] = [f for f in recipe["fields"] if not (f.get("attr") == "none")]
    return recipe


def build_recipe(html: str, url: str, field_names: list[str], rendered: bool = False,
                 redact: bool = False, auto: bool = False, request: dict | None = None) -> dict:
    """Ask the AI for a recipe.

    field_names  columns you asked for (always included)
    auto=True    the AI also finds every other important column on the page and names it itself
    redact=True  pages fetched with a login session: the AI sees only the shape of the text
    request      a "Describe what you need" request (request_parser): its columns, with their
                 meanings and types, replace field_names, and its auto flag replaces auto
    """
    if request and request.get("columns"):
        field_names = [c["name"] for c in request["columns"]]
        auto = bool(request.get("auto"))
    names = clean_field_names(field_names)
    if not names and not auto:
        auto = True
    built = _build(html, url, names, rendered, redact, auto, request)
    if request:
        built["recipe"]["request"] = request
    return built


def snake(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", name.strip()).strip("_").lower()
    if s and s[0].isdigit():
        s = "f_" + s
    return s[:40]


def clean_field_names(names: list[str]) -> list[str]:
    out = []
    for n in names:
        s = snake(n)
        if s and s not in out:
            out.append(s)
    return out


# Columns whose names promise a number (price, salary, volume ...). If their values contain no
# digits the selector or regex caught the wrong part (e.g. only the "£" sign): ask the AI again.
NUMERIC_FIELD_RE = re.compile(r"price|cost|amount|salary|mrp|fee|rent|volume|market_cap|change|percent|"
                              r"qty|quantity|reviews|count|area|sqft|bedrooms|bathrooms|discount|pe_ratio", re.I)


def non_numeric_fields(rows: list[dict], recipe: dict, types: dict | None = None) -> list[str]:
    """Columns that should hold numbers (by name, or typed money/number/... in the request) but whose
    values mostly have no digits."""
    out, types = [], types or {}
    for f in recipe["fields"]:
        name = f["name"]
        typed = types.get(name)
        if typed and typed not in ("money", "number", "percent", "rating"):
            continue
        if not typed and not NUMERIC_FIELD_RE.search(name):
            continue
        values = [str(r.get(name)) for r in rows if r.get(name)]
        if values and sum(1 for v in values if re.search(r"\d", v)) / len(values) < 0.5:
            out.append(name)
    return out


AUTO_MIN_FILL = 0.2   # columns the AI added itself are dropped when filled in fewer than 20% of rows


def _tidy_auto_fields(raw: dict, html: str, url: str, required: list[str]) -> tuple[dict, list[str]]:
    """Clean the AI's own columns: snake_case names, no duplicates, drop near-empty extras and
    columns that repeat another column's values exactly. Required columns are never dropped."""
    seen, fields = set(), []
    for f in raw.get("fields", []):
        name = snake(f.get("name", ""))
        if not name or name in seen:
            continue
        seen.add(name)
        fields.append({**f, "name": name})
    recipe = extractor.normalize_recipe({**raw, "fields": fields})
    rows = extractor.extract(html, recipe, url)
    report = extractor.field_report(rows, recipe)
    dropped, keep, signatures = [], [], {}
    for f in recipe["fields"]:
        n = f["name"]
        if n not in required:
            if report.get(n, 0) < AUTO_MIN_FILL:
                dropped.append(n)
                continue
            sig = tuple(r.get(n, "") for r in rows)
            if sig in signatures:
                dropped.append(n)        # same values as another column
                continue
            signatures[sig] = n
        keep.append(f)
    if not keep:
        return recipe, dropped
    return {**recipe, "fields": keep}, dropped


MIN_PROMPT_CHARS = 3_000
MAX_SHRINKS = 3


def _fields_instruction(field_names: list[str], auto: bool, request: dict | None = None) -> str:
    cols = (request or {}).get("columns") or []
    if cols:
        lines = [f"- {c['name']}" + (f" ({c['type']})" if c.get("type") else "")
                 + (f": {c['meaning']}" if c.get("meaning") else "") for c in cols]
        head = ("The user described the data they need. Columns (include each one, named exactly so):"
                if not auto else "Required columns the user described (include each one, named exactly so):")
        tail = ("" if not auto else "\nAlso find every other important field the items show and add it "
                "as a column, following 'Choosing columns' in your rules.")
        prompt = (request or {}).get("prompt") or ""
        said = f'\nThe user wrote: "{prompt[:400]}"' if prompt else ""
        return head + "\n" + "\n".join(lines) + tail + said
    if not auto:
        return f"Fields to extract: {', '.join(field_names)}"
    if field_names:
        return (f"Required fields (the user asked for these; include each one): {', '.join(field_names)}.\n"
                "Also find every other important field the items show and add it as a column, "
                "following 'Choosing columns' in your rules.")
    return ("Choose the columns yourself: find every important field the items show, following "
            "'Choosing columns' in your rules.")


JSON_LIST_CHARS = 1500


def _json_lists_text(lists: list[dict], text_fn, max_chars: int = JSON_LIST_CHARS) -> str:
    """Structured data lists for the prompt: path, item count and key paths with an example value."""
    out = []
    per_list = max_chars // max(1, min(2, len(lists)))
    for lst in lists[:2]:
        lines = [f"List `{lst['path']}` ({lst['count']} items). Keys of an item:"]
        used = len(lines[0])
        for k, v in list(lst["keys"].items())[:40]:
            line = f"  {k} = {text_fn(str(v))[:50]!r}"
            if used + len(line) > per_list:
                lines.append("  …")
                break
            lines.append(line)
            used += len(line) + 1
        out.append("\n".join(lines))
    return "\n".join(out)


def _make_prompt(candidates: list[dict], pag: list[str], url: str, field_names: list[str],
                 rendered: bool, redact: bool, total_budget: int, auto: bool = False,
                 request: dict | None = None, json_lists: list[dict] | None = None) -> str:
    """Build the prompt so that *everything* (instructions, samples, pagination hints) fits in
    total_budget characters: the samples get whatever room is left."""
    text_fn = privacy.shape if redact else privacy.mask_text
    outline_caps = [int(total_budget * 0.38), int(total_budget * 0.12)]

    def assemble(samples: list[list[str]]) -> str:
        parts = [f"Page URL: {short_url(url)}",
                 _fields_instruction(field_names, auto, request),
                 ("This HTML was captured from a real browser after the page's scripts ran."
                  if rendered else "This HTML is the server's plain response (no scripts were run)."),
                 "",
                 "Below are the groups of repeated elements found on the page (most likely listings first). "
                 "Each shows a CSS path hint, how many siblings matched, and samples of cleaned HTML "
                 "(only class/id/href/src/itemprop/title/aria-label/role and short data-* attributes kept; "
                 "long samples are cut off; long links are shortened and end in …). "
                 "For the top candidates an outline comes first: one line per element that holds "
                 "text, a link, an image or a value, as `CSS path inside the item [attributes] → "
                 "\"text\"`. Outline paths are descendant selectors you can use directly; prefer "
                 "them when the HTML sample is cut off before the field you need. "
                 "Values can live in attributes, e.g. a rating in data-rating: use attr for that."]
        if redact:
            parts.insert(3, "Text content has been replaced by its shape (letters shown as x) for "
                            "privacy; write selectors from the structure and class names.")
        for i, c in enumerate(candidates):
            if not samples[i]:
                continue
            parts.append(f"\n### Candidate {i + 1}: `{c['selector_hint']}` ({c['count']} siblings)")
            if c.get("page_counts"):
                parts.append("Matches on the whole page: " +
                             ", ".join(f"`{sel}` = {n}" for sel, n in c["page_counts"]))
            if c.get("outline") and i < len(outline_caps):
                parts.append(f"Outline of the first {min(c['count'], OUTLINE_CARDS)} items combined:\n```\n" +
                             render_outline(c["outline"], text_fn, outline_caps[i]) + "\n```")
            for j, sample in enumerate(samples[i], 1):
                parts.append(f"Sample {j}:\n```html\n{sample}\n```")
        if json_lists:
            parts.append("\n### Structured data lists (JSON in the page)\n" + _json_lists_text(json_lists, text_fn))
        parts.append("\n### Possible pagination links\n" + ("\n".join(pag) if pag else "(none found)"))
        # Phones / emails are always masked; logged-in pages are reduced to text shape.
        return privacy.redact_html("\n".join(parts), full=redact)

    overhead = len(assemble([[] for _ in candidates]))
    sample_budget = max(800, total_budget - overhead)
    prompt = assemble(_fit_samples(candidates, sample_budget))
    # Masking can change lengths a little; trim the samples again if we ended up over.
    while len(prompt) > total_budget and sample_budget > 800:
        sample_budget = int(sample_budget * 0.85)
        prompt = assemble(_fit_samples(candidates, sample_budget))
    return prompt


def _build(html: str, url: str, field_names: list[str], rendered: bool, redact: bool,
           auto: bool = False, request: dict | None = None) -> dict:
    """Return {"recipe", "rows", "report", "notes"} or raise RecipeBuildError."""
    if not config.ai_enabled():
        raise RecipeBuildError(f"No {config.ai_key_name()} set. Add it to .env, or write a recipe by hand.")
    candidates = find_candidates(html)
    try:
        json_lists = [] if redact else structured.find_lists(html)
    except Exception:
        json_lists = []
    if not candidates and not json_lists:
        raise RecipeBuildError("Couldn't find any repeated listing blocks on this page. "
                               "It may be rendered with JavaScript or be a single-item page.")
    types = {c["name"]: c["type"] for c in (request or {}).get("columns", []) if c.get("type")}
    pag = _pagination_hints(html)
    budget = config.ai_prompt_budget()

    def make(b: int) -> str:
        return _make_prompt(candidates, pag, url, field_names, rendered, redact, b, auto, request, json_lists)

    prompt = make(budget)

    # Each attempt is one self-contained message, so a retry costs about the same as the first
    # call. That keeps it under per-request token limits such as Groq's free tier.
    messages = [{"role": "user", "content": prompt + "\n\nWrite the recipe."}]
    last_problem, text, shrinks, attempt = "", "", 0, 0
    while attempt < 2:
        try:
            text = _ask_model(messages, field_names, auto)
        except _Refused:
            raise RecipeBuildError("The AI model declined to analyse this page.")
        except PromptTooLarge as e:
            # Shrink the sample to fit the provider's limit and try the same step again.
            if shrinks >= MAX_SHRINKS or budget <= MIN_PROMPT_CHARS:
                raise RecipeBuildError(
                    "This page is too large for the AI provider's request limit, even after "
                    "trimming the sample. Use the starter recipe below, or switch to a provider "
                    "with a larger limit (SCRAPER_AI_PROVIDER=anthropic).") from e
            factor = 0.7
            if e.limit and e.requested:
                factor = min(0.8, e.limit / e.requested * 0.85)
            budget = max(MIN_PROMPT_CHARS, int(budget * factor))
            shrinks += 1
            prompt = make(budget)
            messages = [{"role": "user", "content": (_retry_prompt(prompt, text, last_problem)
                                                     if attempt else prompt + "\n\nWrite the recipe.")}]
            continue
        dropped: list[str] = []
        try:
            raw = json.loads(text)
            page_type = raw.get("page_type") or "other"
            if auto:
                recipe, dropped = _tidy_auto_fields(_drop_missing(raw), html, url, field_names)
            else:
                recipe = extractor.normalize_recipe(_drop_missing(raw))
            recipe["page_type"] = page_type
            recipe = mark_list_columns(html, recipe)
            recipe = check_pagination(html, url, recipe)
        except (json.JSONDecodeError, extractor.RecipeError) as e:
            last_problem = f"The recipe was invalid: {e}"
        else:
            absent = [f for f in field_names if f not in {x["name"] for x in recipe["fields"]}]
            rows = extractor.extract(html, recipe, url)
            report = extractor.field_report(rows, recipe)
            # Columns you asked for must work; extra columns the AI chose were already filtered.
            judged = {k: v for k, v in report.items() if not auto or k in field_names} or report
            weak = [k for k, v in judged.items() if v < 0.5]
            bad_numbers = non_numeric_fields(rows, recipe, types)
            if bad_numbers and attempt == 1:
                # Still wrong after a retry: drop AI-added columns, keep the ones you asked for.
                keep = [f for f in recipe["fields"] if f["name"] not in bad_numbers or f["name"] in field_names]
                if keep and len(keep) < len(recipe["fields"]):
                    dropped += [f["name"] for f in recipe["fields"] if f not in keep]
                    recipe = {**recipe, "fields": keep}
                    rows = extractor.extract(html, recipe, url)
                    report = extractor.field_report(rows, recipe)
                bad_numbers = []
            enough_columns = len(report) >= (1 if field_names else 2)
            if len(rows) >= 2 and enough_columns and not bad_numbers and len(weak) <= len(judged) // 2:
                notes = raw.get("notes", "")
                if absent:
                    notes = (notes + f" | Not shown on this page: {', '.join(absent)}.").strip(" |")
                if dropped:
                    notes = (notes + f" | Left out (mostly empty or duplicate): {', '.join(dropped)}.").strip(" |")
                if weak:
                    notes = (notes + f" | Mostly empty: {', '.join(weak)}. Check these fields.").strip(" |")
                if shrinks:
                    notes = (notes + " | The page sample was trimmed to fit the AI provider's limit.").strip(" |")
                return {"recipe": recipe, "rows": rows, "report": report, "notes": notes,
                        "page_type": page_type, "absent": absent}
            last_problem = (f"Testing that recipe on the page gave {len(rows)} rows. "
                            f"Fields filled in fewer than half the rows: {weak or 'none'}. "
                            + (f"These columns should contain numbers but their values have no digits "
                               f"(wrong element or regex): {', '.join(bad_numbers)}. " if bad_numbers else "")
                            + f"First rows: {json.dumps(rows[:2], ensure_ascii=False)[:1500]}"
                            + _field_debug(html, recipe, weak + bad_numbers))
            last_problem = privacy.shape(last_problem) if redact else privacy.mask_text(last_problem)
            if attempt == 1:
                # Second attempt still weak: return it anyway so the user can fix it by hand.
                return {"recipe": recipe, "rows": rows, "report": report, "page_type": page_type,
                        "absent": absent,
                        "notes": (raw.get("notes", "") + " | Warning: " + last_problem).strip(" |")}
        attempt += 1
        if attempt < 2:
            messages = [{"role": "user", "content": _retry_prompt(prompt, text, last_problem)}]
    raise RecipeBuildError(last_problem or "The AI model did not return a usable recipe.")


# ---- item (detail) pages ------------------------------------------------------------------

DETAIL_SYSTEM = """You write extraction recipes for ONE item's own page (a product, job, property...) \
for a web scraper. The recipe is CSS selectors applied to raw HTML by the lexbor engine (no JavaScript).

Rules:
- item_selector: the element that contains the item's details (e.g. "main", "#product", "body").
- Fields: selectors relative to item_selector; attr "text" for visible text or an attribute name.
- kind "json" reads the page's JSON-LD (listed under "Structured data"): selector is one of its key
  paths, e.g. "offers.price" or "hiringOrganization.name". Prefer it when it holds the value: it is
  cleaner than the HTML.
- kind "kv" reads label/value spec tables: label_selector, value_selector and label (the label text to
  pick, e.g. "Material"). For other kinds leave those three "".
- regex (Python, optional, one backslash) keeps group 1; use it to cut a value out of longer text.
- Columns with a meaning: follow it exactly. money / number / percent / rating columns must select
  text that contains the number.
- If a requested column is not on the page, include it with selector "" and attr "none".
- Never use hashed class names like "sc-a1b2c3" or "css-1x2y3z"."""


def _detail_schema(names: list[str], auto: bool) -> dict:
    s = {"type": "string"}
    name = {"type": "string"} if auto or not names else {"type": "string", "enum": names}
    return {
        "type": "object",
        "properties": {
            "item_selector": s,
            "fields": {"type": "array", "items": {
                "type": "object",
                "properties": {"name": name, "kind": {"type": "string", "enum": ["text", "kv", "json"]},
                               "selector": s, "attr": s, "regex": s,
                               "label_selector": s, "value_selector": s, "label": s},
                "required": ["name", "kind", "selector", "attr", "regex",
                             "label_selector", "value_selector", "label"],
                "additionalProperties": False}},
            "notes": s,
        },
        "required": ["item_selector", "fields", "notes"],
        "additionalProperties": False,
    }


def _main_node(html: str):
    tree = extractor.parse_html(html)
    for tag in DROP_TAGS:
        for n in tree.css(tag):
            n.decompose()
    for sel in ("main", "[role=main]", "article", "#content", "#main", ".content", "body"):
        node = tree.css_first(sel)
        if node is not None and len(extractor.clean_text(node.text(separator=" "))) > 200:
            return node
    return tree.body


DETAIL_OUTLINE_LINES = 90
MAX_DETAIL_EXTRAS = 10


def _detail_prompt(html: str, url: str, columns: list[dict], have: list[str], auto: bool,
                   redact: bool, budget: int) -> str:
    text_fn = privacy.shape if redact else privacy.mask_text
    lines = [f"- {c['name']}" + (f" ({c['type']})" if c.get("type") else "")
             + (f": {c['meaning']}" if c.get("meaning") else "") for c in columns]
    parts = [f"Item page URL: {short_url(url)}"]
    if lines:
        parts.append("Columns to read from this page (named exactly so):\n" + "\n".join(lines))
    if auto:
        parts.append(f"Also add up to {MAX_DETAIL_EXTRAS} other useful details this page shows that a listing "
                     "card usually lacks (specifications, description, seller, salary, amenities...)."
                     + (f" The listing already has: {', '.join(have)}; do not repeat those." if have else ""))
    data = {} if redact else structured.main_entity(html)
    if data:
        kv = [f"  {k} = {text_fn(v)[:60]!r}" for k, v in list(data.items())[:60]]
        parts.append("### Structured data (JSON-LD main object)\n" + "\n".join(kv)[:int(budget * 0.3)])
    node = _main_node(html)
    if node is not None:
        entries = outline(node, DETAIL_OUTLINE_LINES)
        root = node.tag if node.tag != "body" else "body"
        parts.append(f"### Outline of `{root}` (CSS path inside it [attributes] → \"text\")\n```\n"
                     + render_outline(entries, text_fn, int(budget * 0.45)) + "\n```")
        used = sum(len(p) for p in parts)
        room = max(800, budget - used - 200)
        parts.append(f"### Cleaned HTML of `{root}` (cut)\n```html\n{_compact(node)[:room]}\n```")
    return privacy.redact_html("\n\n".join(parts), full=redact) + "\n\nWrite the recipe."


def build_detail(html: str, url: str, columns: list[dict], have: list[str] | None = None,
                 auto: bool = True, redact: bool = False) -> dict:
    """Recipe for one item's own page: {"detail": {...}, "row": {...}, "report": {...}, "notes"}.

    columns  [{name, meaning, type}] wanted from the item page (e.g. the ones missing on the listing)
    have     columns the listing already gives (so the AI does not repeat them)
    auto     also add other useful details the page shows
    """
    if not config.ai_enabled():
        raise RecipeBuildError(f"No {config.ai_key_name()} set: item pages need the AI to find their details.")
    names = [c["name"] for c in columns]
    have = have or []
    budget = config.ai_prompt_budget()
    prompt = _detail_prompt(html, url, columns, have, auto or not names, redact, budget)
    problem = ""
    for attempt in range(2):
        try:
            raw = ask_json(DETAIL_SYSTEM, prompt if not problem else _retry_prompt(prompt, last, problem),
                           _detail_schema(names, auto or not names), "detail_recipe")
        except PromptTooLarge:
            budget = max(MIN_PROMPT_CHARS, int(budget * 0.6))
            prompt = _detail_prompt(html, url, columns, have, auto or not names, redact, budget)
            continue
        last = json.dumps(raw)[:2000]
        fields, seen = [], set(have)
        for f in raw.get("fields", []):
            n = snake(f.get("name", ""))
            if n and n not in seen and f.get("attr") != "none":
                seen.add(n)
                fields.append({**f, "name": n})
        try:
            detail = extractor.normalize_recipe({"item_selector": raw.get("item_selector") or "body",
                                                 "fields": fields or [{"name": "_", "selector": ""}]})
        except extractor.RecipeError as e:
            problem = f"The recipe was invalid: {e}"
            continue
        row = extractor.extract_detail(html, detail, url)
        # Drop extra columns that came out empty; keep requested ones so the user sees them missing.
        keep = [f for f in detail["fields"] if f["name"] != "_" and (row.get(f["name"]) or f["name"] in names)]
        filled = [f["name"] for f in keep if row.get(f["name"])]
        missing = [n for n in names if not row.get(n)]
        if (missing and attempt == 0) or not filled:
            problem = (f"Testing it on the page: {', '.join(missing or names) or 'every column'} came out empty. "
                       f"Values read: {json.dumps(row, ensure_ascii=False)[:800]}")
            problem = privacy.shape(problem) if redact else privacy.mask_text(problem)
            if attempt == 0:
                continue
        if not keep:
            break
        out = {"item_selector": detail["item_selector"], "fields": keep}
        return {"detail": out, "row": {f["name"]: row.get(f["name"], "") for f in keep},
                "missing": missing, "notes": raw.get("notes", "")}
    raise RecipeBuildError("The AI could not find these details on the item page"
                           + (f": {problem}" if problem else "."))
