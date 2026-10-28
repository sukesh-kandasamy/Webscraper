"""Is a run's data trustworthy? Checks that keep a run from "succeeding" on the wrong page or with wrong values.

    soft_block(html)                  a verification / blocked page served as a normal page (HTTP 200), any length
    assess(rows, pages, q, prev_rows) why a finished run looks wrong: no rows, a site that states results
                                      but gave none, pages that said they had results but were empty, a big drop
    check_values(rows, html, recipe)  per column: values that don't appear on the page they came from
    check_typed(rows, types)          per column: typed values out of range (a rating of 47, a price of 0)

blockdetect.detect() only reads short pages, because many real pages carry a CAPTCHA script for a contact form.
soft_block() is called only when a page gave no items at all, so it can read pages of any length.
"""
import re

from ..extraction import extractor

# Phrases of verification and "you are blocked" pages. Only consulted for pages that produced no items.
_SOFT_BLOCK: list[tuple[str, re.Pattern]] = [
    ("challenge", re.compile(r"verify(ing)? (that )?you are (a )?human|verifying your request|"
                             r"checking (if the site connection is secure|your browser)|press (&|and) hold|"
                             r"are you a robot|i'?m not a robot|robot check|unusual traffic|"
                             r"enter the characters you see below|automated (access|queries|requests)|"
                             r"enable javascript and cookies to continue|please enable (javascript|cookies) to continue",
                             re.I)),
    ("denied", re.compile(r"access (to this page )?(has been )?denied|request (has been )?blocked|"
                          r"you have been blocked|your (ip|access) (address )?has been (blocked|banned)", re.I)),
]
_TAGS = re.compile(r"<script\b.*?</script>|<style\b.*?</style>|<noscript\b.*?</noscript>|<[^>]+>", re.I | re.S)
DROP_RATIO = 0.3          # a run with < 30% of the last good run's rows looks wrong
MIN_PREV_ROWS = 10


def _visible_text(html: str) -> str:
    return re.sub(r"\s+", " ", _TAGS.sub(" ", html[:600_000])).strip()


def soft_block(html: str) -> str | None:
    """'challenge' or 'denied' when an empty page is really a verification or blocked page, else None."""
    text = _visible_text(html)[:20_000]
    for kind, pattern in _SOFT_BLOCK:
        if pattern.search(text):
            return kind
    return None


def assess(rows_total: int, pages_done: int, q: dict, prev_rows: int = 0) -> list[dict]:
    """Findings [{code, message}] that make a finished run look wrong; [] when it looks fine."""
    out = []
    if not pages_done:
        return out
    stated = q.get("expected_total") or max([p.get("says") or 0 for p in q.get("empty_pages", [])] + [0])
    if not rows_total:
        if stated:
            out.append({"code": "stated_but_empty",
                        "message": f"The page says there are {stated:,} results, but nothing could be collected: the "
                                   "site probably showed a different page to the app, or the columns no longer match "
                                   "the page. Try 'Re-detect columns', or open the page to check it."})
        else:
            out.append({"code": "zero_rows",
                        "message": f"{pages_done} page(s) were read but no rows were found. The page may not list "
                                   "the items any more, or the columns no longer match it."})
        return out
    empty = q.get("empty_pages") or []
    if empty:
        out.append({"code": "empty_pages",
                    "message": f"{len(empty)} page(s) said they had results but gave none, e.g. {empty[0]['url']} "
                               f"(it says {empty[0]['says']:,}). They may have been blocked or loaded differently."})
    if prev_rows >= MIN_PREV_ROWS and rows_total < prev_rows * DROP_RATIO:
        out.append({"code": "rows_dropped",
                    "message": f"Only {rows_total:,} rows this time against {prev_rows:,} in the last good run. The "
                               "site may have changed or limited the app; compare the runs before using the data."})
    return out


def check_values(rows: list[dict], html: str, recipe: dict) -> dict[str, dict]:
    """Per column {checked, ungrounded}: values read from the page's elements must appear on the page. Built
    values (label/value pairs, joined lists, JSON lists) and links (made absolute) are not checked."""
    if not rows or recipe.get("source") == "json":
        return {}
    fields = [f for f in recipe.get("fields", []) if f.get("kind", "text") not in ("kv", "join", "json")
              and f.get("attr", "text") not in extractor.URL_ATTRS]
    if not fields:
        return {}
    page_text = extractor.clean_text(_visible_text(html)).lower()
    raw = html[:2_000_000].lower()
    out = {}
    for f in fields:
        name = f["name"]
        checked = ungrounded = 0
        for r in rows:
            v = extractor.clean_text(str(r.get(name) or "")).lower()
            if len(v) < 2:
                continue
            checked += 1
            if v not in page_text and v not in raw:
                ungrounded += 1
        if checked:
            out[name] = {"checked": checked, "ungrounded": ungrounded}
    return out


def check_typed(rows: list[dict], types: dict[str, str]) -> dict[str, int]:
    """Per column: typed values out of range (ratings outside 0-10, prices of zero or less)."""
    out = {}
    for name, kind in types.items():
        bad = 0
        for r in rows:
            v = r.get(name)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            if (kind == "rating" and not 0 <= v <= 10) or (kind == "money" and v <= 0):
                bad += 1
        if bad:
            out[name] = bad
    return out


def merge_checks(total: dict, checked: dict[str, dict], invalid: dict[str, int]) -> dict:
    """Add one page's checks into the run's running totals (q["value_checks"])."""
    for name, c in checked.items():
        t = total.setdefault(name, {"checked": 0, "ungrounded": 0, "invalid": 0})
        t["checked"] += c["checked"]
        t["ungrounded"] += c["ungrounded"]
    for name, n in invalid.items():
        total.setdefault(name, {"checked": 0, "ungrounded": 0, "invalid": 0})["invalid"] += n
    return total
