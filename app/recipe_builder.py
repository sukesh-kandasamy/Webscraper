"""Ask an AI model (Groq or Claude) to write a recipe (CSS selectors) for a page, then check it.

The model is only called once per new site. The saved recipe is reused for every later scrape.
"""
import json
import re
import time
from collections import defaultdict

import anthropic
from selectolax.lexbor import LexborHTMLParser, LexborNode

from . import config, extractor, privacy

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


def outline(item: LexborNode) -> list[dict]:
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
        if len(lines) >= OUTLINE_LINES:
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
            if avg < 30:
                continue
            links = sum(1 for n in nodes if n.css_first("a[href]"))
            score = len(nodes) * min(avg, 400) * (1.5 if links >= len(nodes) / 2 else 1)
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
- pagination: "next_link" with a selector for the next-page anchor; "url_param" with the query \
parameter name; "click" with the selector of a "Load more" / "Show more" button (the scraper will \
press it in a real browser); "scroll" if the page clearly loads more items on scrolling and has no \
links or button; "none" if you cannot see any pagination.
- Only use selectors that you can see in the provided HTML.
- Table rows: when items are <tr> rows, td:nth-child(n) / th:nth-child(n) selectors are fine.

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
                        "kind": {"type": "string", "enum": ["text", "kv"]},
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
            "notes": s,
        },
        "required": ["item_selector", "fields", "pagination", "page_type", "notes"],
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
                raise RecipeBuildError(
                    f"The AI provider is rate-limited (asked to wait {delay:.0f}s). "
                    "Try again in a minute.") from e
            time.sleep(delay)
            waited += delay


def _ask_claude(messages: list[dict], schema: dict) -> str:
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    kwargs = dict(
        model=config.CLAUDE_MODEL,
        max_tokens=16000,
        system=SYSTEM,
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


def _ask_groq(messages: list[dict], schema: dict) -> str:
    from groq import Groq  # imported lazily so the app runs without the package when unused

    client = Groq(api_key=config.GROQ_API_KEY)
    resp = client.chat.completions.create(
        model=config.GROQ_MODEL,
        messages=[{"role": "system", "content": SYSTEM}, *messages],
        reasoning_effort="medium",
        max_completion_tokens=8000,
        response_format={"type": "json_schema",
                         "json_schema": {"name": "recipe", "strict": True, "schema": schema}},
    )
    return resp.choices[0].message.content or ""


def _ask_model(messages: list[dict], field_names: list[str], auto: bool = False) -> str:
    """Send the conversation to the configured provider and return its JSON text."""
    ask = _ask_groq if config.AI_PROVIDER == "groq" else _ask_claude
    return _with_retries(ask, messages, _schema(field_names, auto))


def _fit_samples(candidates: list[dict], budget: int) -> list[list[str]]:
    """Choose which HTML samples to send, best first, so the prompt stays within budget chars."""
    order = [(i, 0) for i in range(len(candidates))]
    order.insert(min(1, len(order)), (0, 1))  # a second sample of the top candidate comes early
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


def _drop_missing(recipe: dict) -> dict:
    recipe["fields"] = [f for f in recipe["fields"] if not (f.get("attr") == "none")]
    return recipe


def build_recipe(html: str, url: str, field_names: list[str], rendered: bool = False,
                 redact: bool = False, auto: bool = False) -> dict:
    """Ask the AI for a recipe.

    field_names  columns you asked for (always included)
    auto=True    the AI also finds every other important column on the page and names it itself
    redact=True  pages fetched with a login session: the AI sees only the shape of the text
    """
    names = clean_field_names(field_names)
    if not names and not auto:
        auto = True
    return _build(html, url, names, rendered, redact, auto)


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


def _fields_instruction(field_names: list[str], auto: bool) -> str:
    if not auto:
        return f"Fields to extract: {', '.join(field_names)}"
    if field_names:
        return (f"Required fields (the user asked for these; include each one): {', '.join(field_names)}.\n"
                "Also find every other important field the items show and add it as a column, "
                "following 'Choosing columns' in your rules.")
    return ("Choose the columns yourself: find every important field the items show, following "
            "'Choosing columns' in your rules.")


def _make_prompt(candidates: list[dict], pag: list[str], url: str, field_names: list[str],
                 rendered: bool, redact: bool, total_budget: int, auto: bool = False) -> str:
    """Build the prompt so that *everything* (instructions, samples, pagination hints) fits in
    total_budget characters: the samples get whatever room is left."""
    text_fn = privacy.shape if redact else privacy.mask_text
    outline_caps = [int(total_budget * 0.38), int(total_budget * 0.12)]

    def assemble(samples: list[list[str]]) -> str:
        parts = [f"Page URL: {short_url(url)}",
                 _fields_instruction(field_names, auto),
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
           auto: bool = False) -> dict:
    """Return {"recipe", "rows", "report", "notes"} or raise RecipeBuildError."""
    if not config.ai_enabled():
        key = "GROQ_API_KEY" if config.AI_PROVIDER == "groq" else "ANTHROPIC_API_KEY"
        raise RecipeBuildError(f"No {key} set. Add it to .env, or write a recipe by hand.")
    candidates = find_candidates(html)
    if not candidates:
        raise RecipeBuildError("Couldn't find any repeated listing blocks on this page. "
                               "It may be rendered with JavaScript or be a single-item page.")
    pag = _pagination_hints(html)
    budget = config.ai_prompt_budget()
    prompt = _make_prompt(candidates, pag, url, field_names, rendered, redact, budget, auto)

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
            prompt = _make_prompt(candidates, pag, url, field_names, rendered, redact, budget, auto)
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
        except (json.JSONDecodeError, extractor.RecipeError) as e:
            last_problem = f"The recipe was invalid: {e}"
        else:
            absent = [f for f in field_names if f not in {x["name"] for x in recipe["fields"]}]
            rows = extractor.extract(html, recipe, url)
            report = extractor.field_report(rows, recipe)
            # Columns you asked for must work; extra columns the AI chose were already filtered.
            judged = {k: v for k, v in report.items() if not auto or k in field_names} or report
            weak = [k for k, v in judged.items() if v < 0.5]
            enough_columns = len(report) >= (1 if field_names else 2)
            if len(rows) >= 2 and enough_columns and len(weak) <= len(judged) // 2:
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
                            f"First rows: {json.dumps(rows[:2], ensure_ascii=False)[:1500]}"
                            + _field_debug(html, recipe, weak))
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
