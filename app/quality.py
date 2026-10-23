"""How complete and clean a run is: the quality report shown with every run.

    rows stored, pages read / failed (with reasons), duplicates removed, items left out by the request's
    filters, per-column fill and type problems, item pages read, and coverage when the site states a
    total ("Showing 1–24 of 1,250 results" -> 1,250 expected).
"""
import re

from . import extractor, normalize

_TOTAL_PATTERNS = [
    # "1-48 of over 10,000 results", "Showing 1–24 of 1,250", "21 to 40 of 812 jobs"
    re.compile(r"\b\d[\d,]*\s*(?:-|–|—|to)\s*\d[\d,]*\s+of\s+(?:over\s+|about\s+|around\s+)?(\d[\d,]*)", re.I),
    # "1,250 results", "812 jobs found", "Found 96 properties", "56 products"
    re.compile(r"\b(?:found\s+)?(\d[\d,]*)\s+(?:results|jobs|products|properties|listings|ads|matches|"
               r"books|companies|homes|vacancies|openings)\b(?:\s+found)?", re.I),
]
MAX_TEXT = 200_000


def stated_total(html: str) -> int | None:
    """The number of items the page says exist, if it says so (first match in the page text)."""
    tree = extractor.parse_html(html)
    for tag in ("script", "style", "noscript", "template"):
        for n in tree.css(tag):
            n.decompose()
    body = tree.body
    text = extractor.clean_text(body.text(separator=" ") if body is not None else "")[:MAX_TEXT]
    for pattern in _TOTAL_PATTERNS:
        m = pattern.search(text)
        if m:
            n = int(m.group(1).replace(",", ""))
            if n >= 2:
                return n
    return None


def column_stats(rows: list[dict], names: list[str], types: dict[str, str]) -> dict[str, dict]:
    out = {}
    total = len(rows) or 1
    bad = normalize.type_failures(rows, types)
    for n in names:
        vals = [r.get(n) for r in rows if r.get(n) not in (None, "")]
        out[n] = {"fill": round(len(vals) / total, 3) if rows else 0.0,
                  "distinct": len({str(v) for v in vals}),
                  "type": types.get(n, "text"),
                  "unreadable": bad.get(n, 0)}
    return out


def build(rows: list[dict], names: list[str], types: dict[str, str], counters: dict,
          pages_done: int) -> dict:
    """The quality report of a finished (or stopped) run. `counters` holds what the run counted:
    items_seen, duplicates, excluded, trimmed, failed [{url, error}], expected_total, details_*."""
    expected = counters.get("expected_total")
    seen = counters.get("items_seen", len(rows))
    if expected and seen > expected * 1.1:
        expected = None          # the number on the page wasn't the total (e.g. "25 items" per page)
    report = {
        "rows": len(rows),
        "items_seen": seen,
        "pages_ok": pages_done,
        "pages_failed": counters.get("failed", []),
        "duplicates": counters.get("duplicates", 0),
        "excluded": counters.get("excluded", 0),
        "trimmed": counters.get("trimmed", 0),
        "expected_total": expected,
        "coverage": round(min(1.0, seen / expected), 3) if expected else None,
        "details": {k: counters.get(f"details_{k}", 0) for k in ("done", "failed", "skipped")},
        "skipped_filters": counters.get("skipped_filters", []),
        "columns": column_stats(rows, names, types),
    }
    if counters.get("health"):
        report["health"] = counters["health"]
    report["warnings"] = warnings(report)
    return report


def warnings(report: dict) -> list[str]:
    out = []
    if report.get("skipped_filters"):
        out.append("Not applied, because that column is empty on the page (the site's own filter or the page "
                   f"you chose already covers it): {', '.join(report['skipped_filters'])}.")
    if report["pages_failed"]:
        out.append(f"{len(report['pages_failed'])} page(s) could not be read.")
    if report["coverage"] is not None and report["coverage"] < 0.9:
        out.append(f"The site lists about {report['expected_total']:,} items; {report['items_seen']:,} were "
                   "collected. Raise the page limit (or choose All pages) to collect more.")
    empty = [n for n, c in report["columns"].items() if report["rows"] and c["fill"] < 0.2]
    if empty:
        out.append(f"Mostly empty: {', '.join(empty)}.")
    unread = [n for n, c in report["columns"].items() if c["unreadable"]]
    if unread:
        out.append(f"Some values could not be read as numbers/dates: {', '.join(unread)}.")
    if report["details"]["failed"]:
        out.append(f"{report['details']['failed']} item page(s) could not be read.")
    if report.get("health", {}).get("broken"):
        out.append(report["health"]["message"])
    return out


def health(current: dict, previous: dict | None) -> dict | None:
    """Compare a run's column fill with the scrape's last good run. A column that was well filled
    (≥ 60%) and is now nearly empty (< 10%) means the site changed its layout."""
    if not previous or not previous.get("columns"):
        return None
    broken = []
    for name, prev in previous["columns"].items():
        now = current.get("columns", {}).get(name)
        if prev.get("fill", 0) >= 0.6 and (now is None or now.get("fill", 0) < 0.1):
            broken.append(name)
    if previous.get("rows") and not current.get("rows"):
        broken = broken or ["all"]
    if not broken:
        return None
    what = "no rows at all" if broken == ["all"] else f"empty columns: {', '.join(broken)}"
    return {"broken": broken,
            "message": f"This run has {what}, though the last run had them. The site probably changed; "
                       "use 'Re-detect columns' to let the AI find them again."}
