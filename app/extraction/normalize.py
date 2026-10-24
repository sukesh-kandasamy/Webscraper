"""Turn scraped text into typed values: money, numbers, percentages, ratings, dates.

    "₹ 1,62,499"        -> 162499   (currency INR)
    "Price: 150 INR/Piece" -> 150   (currency INR, unit piece)
    "4.5 out of 5 stars" -> 4.5
    "(151.6K)"          -> 151600
    "2.5 Lakh" / "1.2 Cr" -> 250000 / 12000000
    "-12%"              -> -12
    "2d ago", "23 Oct 2026", "2026-10-23" -> "2026-10-21" (ISO date)

Each column gets a type (detected from its values and its name, or given by the user's request); rows are
then converted so sorting, filtering and Excel sums work. Values that can't be read are left unchanged.
"""
import re
from datetime import date, datetime, timedelta

TYPES = ("money", "number", "percent", "rating", "date", "url", "image", "text")

CURRENCIES = [
    ("INR", re.compile(r"₹|\bRs\.?|\bINR\b", re.I)),
    ("USD", re.compile(r"US\$|\$|\bUSD\b", re.I)),
    ("EUR", re.compile(r"€|\bEUR\b", re.I)),
    ("GBP", re.compile(r"£|\bGBP\b", re.I)),
    ("JPY", re.compile(r"¥|\bJPY\b", re.I)),
    ("AED", re.compile(r"\bAED\b", re.I)),
]
_NUM = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?|[-+]?\.\d+")
_SCALE = [
    (re.compile(r"^\s*(?:cr|crore|crores)\b", re.I), 10_000_000),
    (re.compile(r"^\s*(?:l|lac|lakh|lakhs|lacs)\b", re.I), 100_000),
    (re.compile(r"^\s*(?:b|bn|billion)\b", re.I), 1_000_000_000),
    (re.compile(r"^\s*(?:m|mn|million)\b", re.I), 1_000_000),
    (re.compile(r"^\s*(?:k|thousand)\b", re.I), 1_000),
]
_UNIT = re.compile(r"/\s*([A-Za-z][A-Za-z .]{0,20})\s*$")
_RATING_OF = re.compile(r"(\d+(?:\.\d+)?)\s*(?:/|out of)\s*(\d+(?:\.\d+)?)", re.I)
_WORD_RATING = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_REL = re.compile(r"(\d+)\s*\+?\s*(minute|min|m|hour|hr|h|day|d|week|wk|w|month|mo|year|yr|y)s?\b(?:\s*ago)?", re.I)

NAME_HINTS = [
    ("money", re.compile(r"price|cost|amount|salary|mrp|fee|rent|budget|pay|ctc|value|deposit|emi", re.I)),
    ("percent", re.compile(r"percent|pct|discount|change_percent|%", re.I)),
    ("rating", re.compile(r"rating|stars?|score", re.I)),
    ("date", re.compile(r"date|posted|published|updated|added|time|deadline|created", re.I)),
    ("number", re.compile(r"reviews|count|qty|quantity|volume|area|sqft|bedrooms|bathrooms|beds|baths|"
                          r"stock|views|votes|rank|year|founded|change|market_cap|pe_ratio|experience|age", re.I)),
    ("image", re.compile(r"image|img|photo|picture|thumbnail|logo", re.I)),
    ("url", re.compile(r"url|link|href", re.I)),
]


# ---- single values ----------------------------------------------------------------

def _to_float(token: str) -> float | None:
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def _clean_int(x: float) -> float | int:
    return int(x) if float(x).is_integer() else round(x, 6)


def parse_number(text) -> float | int | None:
    """First number in the text, with K / L / Cr / M scale words applied: "(151.6K)" -> 151600."""
    if isinstance(text, (int, float)):
        return text
    if not isinstance(text, str):
        return None
    m = _NUM.search(text)
    if not m:
        return None
    value = _to_float(m.group(0))
    if value is None:
        return None
    rest = text[m.end():]
    for pattern, factor in _SCALE:
        if pattern.match(rest):
            value *= factor
            break
    return _clean_int(value)


def currency_of(text: str) -> str:
    for code, pattern in CURRENCIES:
        if pattern.search(text):
            return code
    return ""


def parse_money(text) -> tuple[float | int | None, str, str]:
    """(amount, currency, unit). "₹ 450 / Piece" -> (450, "INR", "piece"). Ranges use the first amount."""
    if isinstance(text, (int, float)):
        return text, "", ""
    if not isinstance(text, str) or not text.strip():
        return None, "", ""
    unit_m = _UNIT.search(text)
    unit = unit_m.group(1).strip().lower().rstrip(".") if unit_m else ""
    return parse_number(text), currency_of(text), unit


def parse_percent(text) -> float | int | None:
    if isinstance(text, (int, float)):
        return text
    if not isinstance(text, str) or "%" not in text and not re.search(r"\bper\s*cent\b", text, re.I):
        return parse_number(text) if isinstance(text, str) and re.fullmatch(r"\s*[-+]?\d+(\.\d+)?\s*", text) else None
    return parse_number(text)


def parse_rating(text) -> float | int | None:
    if isinstance(text, (int, float)):
        return text
    if not isinstance(text, str) or not text.strip():
        return None
    m = _RATING_OF.search(text)
    if m:
        return _clean_int(float(m.group(1)))
    word = text.strip().lower()
    if word in _WORD_RATING:
        return _WORD_RATING[word]
    n = parse_number(text)
    return n if n is not None and 0 <= n <= 10 else None


def parse_date(text, today: date | None = None) -> str | None:
    """ISO date (YYYY-MM-DD) from absolute dates, "2d ago", "3 weeks ago", "today", "yesterday", "New"."""
    if not isinstance(text, str) or not text.strip():
        return None
    today = today or date.today()
    t = text.strip().lower()
    if t in ("today", "just now", "new", "now") or re.search(r"\b(hours?|hrs?|minutes?|mins?)\s*ago\b", t):
        return today.isoformat()
    if t == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", t)          # 23/10/2026 (day first)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\.?,?\s+(\d{4})", t)   # 23 Oct 2026
    if m and m.group(2) in _MONTHS:
        try:
            return date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1))).isoformat()
        except ValueError:
            return None
    m = re.search(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", t)                   # Oct 23, 2026
    if m and m.group(1) in _MONTHS:
        try:
            return date(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2))).isoformat()
        except ValueError:
            return None
    m = _REL.search(t)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        days = {"m": 0, "min": 0, "minute": 0, "h": 0, "hr": 0, "hour": 0, "d": 1, "day": 1,
                "w": 7, "wk": 7, "week": 7, "mo": 30, "month": 30, "y": 365, "yr": 365, "year": 365}.get(unit)
        if days is not None:
            return (today - timedelta(days=n * days)).isoformat()
    return None


PARSERS = {
    "money": lambda v: parse_money(v)[0],
    "number": parse_number,
    "percent": parse_percent,
    "rating": parse_rating,
    "date": parse_date,
}


# ---- columns -------------------------------------------------------------------------

def _share(values: list, parser) -> float:
    ok = sum(1 for v in values if parser(v) is not None)
    return ok / len(values) if values else 0.0


def detect_type(name: str, values: list) -> str:
    """Pick a column type from its name and values (at least 70% of non-empty values must parse)."""
    vals = [v for v in values if v not in (None, "")]
    if not vals:
        return "text"
    strs = [v for v in vals if isinstance(v, str)]
    if strs and all(re.match(r"^https?://", s) for s in strs):
        is_img = sum(1 for s in strs if re.search(r"\.(jpe?g|png|webp|gif|avif|svg)(\?|$)", s, re.I))
        return "image" if is_img / len(strs) >= 0.6 or re.search(r"image|img|photo|thumb", name, re.I) else "url"
    hint = next((t for t, pattern in NAME_HINTS if pattern.search(name)), None)
    if hint in ("url", "image"):
        return "text"
    if hint and hint in PARSERS and _share(vals, PARSERS[hint]) >= 0.7:
        return hint
    # no (matching) hint: only call it numeric when the values clearly are
    if strs and sum(1 for s in strs if currency_of(s) and parse_number(s) is not None) / len(strs) >= 0.7:
        return "money"
    if strs and sum(1 for s in strs if "%" in s) / len(strs) >= 0.7 and _share(vals, parse_percent) >= 0.7:
        return "percent"
    if all(isinstance(v, (int, float)) or re.fullmatch(r"\s*[-+]?[\d,]+(\.\d+)?\s*", str(v)) for v in vals):
        return "number"
    return "text"


def detect_types(rows: list[dict], fields: list[str], given: dict | None = None) -> dict[str, str]:
    given = given or {}
    return {f: (given[f] if given.get(f) in TYPES else detect_type(f, [r.get(f) for r in rows])) for f in fields}


def convert(value, kind: str):
    """Typed value for one cell, or the original value when it can't be read."""
    parser = PARSERS.get(kind)
    if parser is None or value in (None, ""):
        return value
    out = parser(value)
    return value if out is None else out


def normalize_rows(rows: list[dict], types: dict[str, str]) -> list[dict]:
    """Convert values to their column's type. Money columns also get a `<col>_currency` column when the
    page shows a currency (and a `<col>_unit` column for "per piece / per kg" prices)."""
    out = []
    for r in rows:
        new = dict(r)
        for col, kind in types.items():
            v = r.get(col)
            if v in (None, ""):
                continue
            if kind == "money" and isinstance(v, str):
                amount, cur, unit = parse_money(v)
                if amount is not None:
                    new[col] = amount
                    if cur:
                        new[f"{col}_currency"] = cur
                    if unit and not r.get(f"{col}_unit"):
                        new[f"{col}_unit"] = unit
            else:
                new[col] = convert(v, kind)
        out.append(new)
    return out


def type_failures(rows: list[dict], types: dict[str, str]) -> dict[str, int]:
    """How many non-empty cells of typed columns could not be read (still text after normalising)."""
    bad = {}
    for col, kind in types.items():
        if kind not in PARSERS:
            continue
        n = sum(1 for r in rows if isinstance(r.get(col), str) and r.get(col).strip())
        if n:
            bad[col] = n
    return bad
