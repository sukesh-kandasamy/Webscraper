"""Read "Describe what you need" prompts and apply the resulting request to scraped rows.

    "I need the image, price and brand of each shoe. Only men's shoes under ₹3,000, cheapest first."

becomes

    {"columns": [{"name": "image", "meaning": "...", "type": "image", "required": true},
                 {"name": "price", "meaning": "selling price ...", "type": "money", "required": true},
                 {"name": "brand", "meaning": "brand name; often the first word of the title", ...}],
     "filters": [{"column": "price", "op": "lt", "value": "3000"},
                 {"column": "name", "op": "contains", "value": "men"}],
     "sort": {"column": "price", "desc": false}, "limit": 0, "auto": false, ...}

One short AI call reads the prompt (no page HTML is sent). The request then guides the recipe builder
(column meanings and types) and every run (filters, sort and limit on the typed values).
"""
import re

from . import config, normalize, recipe_builder

OPS = ("lt", "lte", "gt", "gte", "eq", "ne", "contains", "not_contains")
OP_LABELS = {"lt": "<", "lte": "≤", "gt": ">", "gte": "≥", "eq": "=", "ne": "≠",
             "contains": "contains", "not_contains": "does not contain"}
MAX_PROMPT_CHARS = 2000
MAX_COLUMNS = 25

SYSTEM = """You turn a person's plain-language description of the data they want from a web page \
into a structured data request for a web scraper. You do not see the page; only its URL.

Return:
- columns: the data the person asked for, one column per value, in the order they mentioned them.
  name: short snake_case English ("price", "brand", "posted", "area_sqft").
  meaning: one sentence telling the scraper exactly which value to take, with hints that avoid
    mistakes, e.g. price -> "the selling price after discount, not the struck-out original price (MRP)";
    brand -> "the brand or manufacturer name; often the first word(s) of the product title";
    image -> "the main product image URL (src of the product picture)".
  type: money (prices, salaries, rent), number (counts, sizes, quantities), percent, rating, date,
    url (links), image (picture URLs) or text.
  required: true for everything the person explicitly asked for.
  If the person asks for "all details" / "everything" or names only a topic, set auto to true and list
  the columns they did name (if any).
- filters: only conditions the person stated. column must be one of your columns, op one of
  lt, lte, gt, gte, eq, ne, contains, not_contains; value as plain text without currency symbols or
  thousands separators ("3000", "4", "Bangalore"). "under ₹3,000" -> price lt 3000; "at least 4 stars"
  -> rating gte 4; "in Bangalore" -> location contains Bangalore. When the condition is about a kind
  of item ("men's shoes", "only laptops", "remote jobs") and there is no column for it, add a
  contains filter on the column that names the item (name / title), e.g. name contains "men".
  If a filter needs a column the person did not ask for, add that column with required false.
- sort: {"column": "", "desc": false} when no order was asked. "cheapest first" -> price asc;
  "newest first" -> posted desc; "highest rated" -> rating desc.
- limit: the number of items wanted ("top 10" -> 10), else 0.
- auto: see above; false when the person listed the columns they want.
- details_hint: true when some requested values usually appear only on each item's own page rather
  than on a listing (full description, specifications, seller GST, job description, amenities).
- question: "" normally. Only when you cannot tell what data is wanted at all, one short question.
- summary: one short sentence restating the request, e.g.
  "Image, price and brand of each shoe; men's only, under ₹3,000; cheapest first." """


def _schema() -> dict:
    s = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "columns": {"type": "array", "items": {
                "type": "object",
                "properties": {"name": s, "meaning": s,
                               "type": {"type": "string", "enum": list(normalize.TYPES)},
                               "required": {"type": "boolean"}},
                "required": ["name", "meaning", "type", "required"],
                "additionalProperties": False}},
            "filters": {"type": "array", "items": {
                "type": "object",
                "properties": {"column": s, "op": {"type": "string", "enum": list(OPS)}, "value": s},
                "required": ["column", "op", "value"],
                "additionalProperties": False}},
            "sort": {"type": "object", "properties": {"column": s, "desc": {"type": "boolean"}},
                     "required": ["column", "desc"], "additionalProperties": False},
            "limit": {"type": "integer"},
            "auto": {"type": "boolean"},
            "details_hint": {"type": "boolean"},
            "question": s,
            "summary": s,
        },
        "required": ["columns", "filters", "sort", "limit", "auto", "details_hint", "question", "summary"],
        "additionalProperties": False,
    }


class RequestError(ValueError):
    pass


def clean_request(raw: dict, prompt: str = "") -> dict:
    """Validate a request (from the AI or edited in the UI) and fill in defaults."""
    if not isinstance(raw, dict):
        raise RequestError("The request must be an object")
    columns, names = [], set()
    for c in (raw.get("columns") or [])[:MAX_COLUMNS]:
        if isinstance(c, str):
            c = {"name": c}
        name = recipe_builder.snake(str(c.get("name") or ""))
        if not name or name in names:
            continue
        names.add(name)
        kind = c.get("type") if c.get("type") in normalize.TYPES else ""
        col = {"name": name, "meaning": str(c.get("meaning") or "")[:300],
               "type": kind or "", "required": bool(c.get("required", True))}
        if c.get("auto_added"):
            col["auto_added"] = True
        columns.append(col)
    filters = []
    for f in raw.get("filters") or []:
        col = recipe_builder.snake(str(f.get("column") or ""))
        op = f.get("op")
        value = str(f.get("value") if f.get("value") is not None else "").strip()[:100]
        if col and op in OPS and value:
            filters.append({"column": col, "op": op, "value": value})
            if col not in names:      # a filter needs its column
                names.add(col)
                columns.append({"name": col, "meaning": "", "type": "", "required": False})
    if columns and "url" not in names and not any(c["type"] == "url" for c in columns):
        # Each item's own link: removes duplicates reliably and lets the run open item pages.
        names.add("url")
        columns.append({"name": "url", "meaning": "link to the item's own page (href of its title link)",
                        "type": "url", "required": False, "auto_added": True})
    sort = raw.get("sort") or {}
    sort_col = recipe_builder.snake(str(sort.get("column") or ""))
    try:
        limit = max(0, min(int(raw.get("limit") or 0), 100_000))
    except (TypeError, ValueError):
        limit = 0
    return {
        "prompt": (prompt or raw.get("prompt") or "")[:MAX_PROMPT_CHARS],
        "columns": columns,
        "filters": filters,
        "sort": {"column": sort_col, "desc": bool(sort.get("desc"))} if sort_col in names else
                {"column": "", "desc": False},
        "limit": limit,
        "auto": bool(raw.get("auto")) or not columns,
        "details_hint": bool(raw.get("details_hint")),
        "question": str(raw.get("question") or "")[:300],
        "summary": str(raw.get("summary") or "")[:300],
    }


def _local_parse(prompt: str) -> dict:
    """Without an AI key: take the column names from a list ("image, price and brand")."""
    text = re.sub(r"(?i)^\s*(i\s+(need|want)|get|give me|collect|extract)\s+(the\s+)?", "", prompt.strip())
    text = re.split(r"[.\n;]", text)[0]
    text = re.sub(r"(?i)\s+(of|for|from)\s+(each|every|all|the)\b.*$", "", text)
    parts = [p.strip() for p in re.split(r",|\band\b|&|/", text) if p.strip()]
    cols = [{"name": p, "required": True} for p in parts if len(p) <= 40]
    return {"columns": cols, "auto": not cols,
            "summary": ", ".join(recipe_builder.snake(c["name"]) for c in cols)}


def understand(prompt: str, url: str = "") -> dict:
    """Turn a "Describe what you need" prompt into a cleaned request."""
    prompt = (prompt or "").strip()[:MAX_PROMPT_CHARS]
    if not prompt:
        raise RequestError("Describe the data you need first.")
    if not config.ai_enabled():
        return clean_request(_local_parse(prompt), prompt)
    page = f"Page URL: {recipe_builder.short_url(url)}\n" if url else ""
    raw = recipe_builder.ask_json(SYSTEM, f"{page}What the person needs:\n\"\"\"\n{prompt}\n\"\"\"",
                                  _schema(), "data_request")
    return clean_request(raw, prompt)


def sort_words(request: dict) -> str:
    sort = request.get("sort") or {}
    kind = next((c.get("type") for c in request.get("columns", []) if c["name"] == sort.get("column")), "")
    if kind == "date":
        return "newest first" if sort.get("desc") else "oldest first"
    if kind in ("text", "url", "image"):
        return "Z → A" if sort.get("desc") else "A → Z"
    return "high → low" if sort.get("desc") else "low → high"


def describe(request: dict) -> list[str]:
    """Short human labels for the request's filters, sort and limit."""
    out = [f"{f['column']} {OP_LABELS[f['op']]} {f['value']}" for f in request.get("filters", [])]
    sort = request.get("sort") or {}
    if sort.get("column"):
        out.append(f"sorted by {sort['column']} ({sort_words(request)})")
    if request.get("limit"):
        out.append(f"first {request['limit']}")
    return out


# ---- applying a request to rows --------------------------------------------------------

NUMERIC = ("money", "number", "percent", "rating")


def _match(value, op: str, target: str, kind: str) -> bool:
    if value in (None, ""):
        return op in ("ne", "not_contains")
    if kind in NUMERIC or op in ("lt", "lte", "gt", "gte") and isinstance(value, (int, float)):
        want = normalize.parse_number(target)
        have = value if isinstance(value, (int, float)) else normalize.convert(value, kind or "number")
        if want is not None and isinstance(have, (int, float)):
            return {"lt": have < want, "lte": have <= want, "gt": have > want, "gte": have >= want,
                    "eq": have == want, "ne": have != want,
                    "contains": str(want) in str(value), "not_contains": str(want) not in str(value)}[op]
        if op in ("lt", "lte", "gt", "gte"):
            return False
    if kind == "date" and op in ("lt", "lte", "gt", "gte", "eq", "ne"):
        want = normalize.parse_date(target) or target
        have = str(value)
        return {"lt": have < want, "lte": have <= want, "gt": have > want, "gte": have >= want,
                "eq": have == want, "ne": have != want}[op]
    text, t = str(value).lower(), target.lower()
    # Word-start match, so "men" finds "Men's Running Shoe" but not "Women's".
    found = re.search(r"(?<![a-z0-9])" + re.escape(t), text) is not None
    return {"contains": found, "not_contains": not found, "eq": text == t, "ne": text != t,
            "lt": text < t, "lte": text <= t, "gt": text > t, "gte": text >= t}[op]


def filter_rows(rows: list[dict], request: dict | None, types: dict[str, str]) -> list[dict]:
    """Rows that match every filter of the request."""
    filters = (request or {}).get("filters") or []
    if not filters:
        return rows
    return [r for r in rows
            if all(_match(r.get(f["column"]), f["op"], f["value"], types.get(f["column"], "text"))
                   for f in filters)]


def sort_and_limit(rows: list[dict], request: dict | None) -> list[dict]:
    request = request or {}
    sort = request.get("sort") or {}
    col = sort.get("column")
    if col:
        def key(r):
            v = r.get(col)
            missing = v in (None, "")
            # Missing values always go last; numbers compare as numbers, everything else as text.
            if isinstance(v, (int, float)):
                return (missing, 0, -v if sort.get("desc") else v, "")
            return (missing, 1, 0, str(v or "").lower())
        rows = sorted(rows, key=key)
        if sort.get("desc"):
            # Text columns: reverse the text order but keep missing values at the end.
            nums = [r for r in rows if isinstance(r.get(col), (int, float))]
            texts = [r for r in rows if not isinstance(r.get(col), (int, float)) and r.get(col) not in (None, "")]
            empty = [r for r in rows if r.get(col) in (None, "")]
            rows = nums + texts[::-1] + empty
    limit = request.get("limit") or 0
    return rows[:limit] if limit else rows


def column_types(request: dict | None) -> dict[str, str]:
    return {c["name"]: c["type"] for c in (request or {}).get("columns", []) if c.get("type")}
