"""A page as the agent's AI sees it: a few hundred to a few thousand characters of plain lines, built by
the app without any AI call, from HTML with personal data already redacted.

    PAGE https://books.toscrape.com/catalogue/category/books/mystery_3/index.html (200, plain fetch)
    title: Mystery | Books to Scrape        says: "32 results"
    lists:
      1. 20 items · li.col-xs-6 · e.g. "Sharp Objects £47.82 In stock" | "In a Dark, Dark Wood £19.63"
         fields seen: h3>a[title] · p.price_color · p.star-rating[class] · img[src]
    json lists: ld:ItemList · 20 items · keys name, offers.price, url
    next page: "next" → page-2.html
    search forms: form 0: GET /search · q (search, "Search books…") · category (select: All, Fiction, +12)
    links matching the goal (mystery, books): "Mystery (32)" → /catalogue/category/books/mystery_3/index.html
    headings: h1 "Mystery"
"""
import re
from urllib.parse import urljoin, urlsplit

from ...extraction import extractor, recipe_builder, structured
from ...fetching import blockdetect
from ...runs import quality
from ...security import privacy

MAX_HTML = 1_500_000
STOPWORDS = {"the", "and", "with", "from", "that", "this", "their", "each", "every", "only", "under", "over",
             "about", "into", "page", "pages", "list", "data", "collect", "items", "want", "need", "all",
             "first", "then", "than", "sorted", "sort", "price", "rating", "title", "name", "link", "image",
             "most", "best", "newest", "latest", "cheapest", "between", "more", "less", "also", "site"}
_ZERO = re.compile(r"\b(?:0|no|zero)\s+(?:results?|properties|items|products|listings|matches|hotels|jobs|"
                   r"books|ads|offers|records)\b|\bnothing (?:found|matched)\b|\bno (?:matching|exact) ", re.I)
_LINK_TAG = re.compile(r'<(\w+)([^>]*)>(.*?)</\1>', re.S)
_ATTR = re.compile(r'(\w[\w-]*)="([^"]*)"')
SKIP_INPUTS = {"submit", "button", "image", "reset", "password", "file", "email", "tel"}
_NOT_SEARCH = re.compile(r"basket|cart|checkout|login|signin|sign-in|register|signup|newsletter|subscribe|account|wishlist|"
                         r"review|comment|contact|enquir|inquir", re.I)
_MENU = re.compile(r"\b(nav|navs|navbar|menu|header|footer|breadcrumb|subnav|sidebar|tabs)\b|[-_](nav|menu)\b", re.I)


def goal_words(*texts: str) -> list[str]:
    words = []
    for t in texts:
        for w in re.findall(r"[a-zA-Z][a-zA-Z0-9'-]{3,}", t or ""):
            w = w.lower()
            if w not in STOPWORDS and w not in words:
                words.append(w)
    return words[:12]


def _text(node) -> str:
    return extractor.clean_text(node.text(separator=" ")) if node is not None else ""


def lists(html: str) -> list[dict]:
    """Repeated blocks that look like records: [{selector, count, samples, fields, score}]."""
    out = []
    try:
        cands = recipe_builder.find_candidates(html)
    except Exception:
        return out
    tree = extractor.parse_html(html)
    for c in cands:
        counts = c.get("page_counts") or []
        selector = counts[-1][0] if counts else c["selector_hint"]
        count = max([n for _, n in counts] + [c["count"]])
        try:
            nodes = tree.css(selector)[:4] if selector else []
        except Exception:
            nodes = []
        samples = [privacy.mask_text(_text(n))[:90] for n in nodes[:2]]
        fields = []
        outline = c.get("outline")
        if not outline and nodes:
            try:
                outline = recipe_builder.merged_outline(nodes)
            except Exception:
                outline = []
        for e in (outline or [])[:8]:
            attrs = [k for k, _ in (e.get("attrs") or []) if k in ("href", "src", "title", "class", "datetime", "content")]
            fields.append(e["path"] + (f"[{attrs[0]}]" if attrs else ""))
        # Records beat menus: a site's navigation also repeats, but it has no prices or numbers and sits in
        # nav / menu / header / footer containers.
        menu = bool(_MENU.search(c["selector_hint"] + " " + selector))
        rich = any(re.search(r"\d|[£$€₹]", s) for s in samples)
        score = count * (0.15 if menu else 1) * (1.5 if rich else 1)
        out.append({"selector": selector, "count": count, "samples": samples, "fields": fields, "menu": menu, "score": score})
    out.sort(key=lambda d: -d["score"])
    return out[:3]


def forms(html: str, base_url: str) -> list[dict]:
    """Search-like forms: [{index, method, action, fields: [{name, type, placeholder, options, value}]}]."""
    tree = extractor.parse_html(html)
    out = []
    for i, f in enumerate(tree.css("form")):
        method = (f.attributes.get("method") or "get").upper()
        action = urljoin(base_url, f.attributes.get("action") or "") or base_url
        if _NOT_SEARCH.search(action + " " + (f.attributes.get("id") or "") + " " + (f.attributes.get("class") or "")):
            continue                      # baskets, logins, newsletters: never something to search with
        fields = []
        for el in f.css("input, select, textarea"):
            name = el.attributes.get("name")
            if not name:
                continue
            kind = (el.attributes.get("type") or ("select" if el.tag == "select" else "text")).lower()
            if kind in SKIP_INPUTS:
                continue
            field = {"name": name, "type": kind, "placeholder": privacy.mask_text(el.attributes.get("placeholder") or "")[:40],
                     "value": (el.attributes.get("value") or "")[:80]}
            if el.tag == "select":
                opts = [(o.attributes.get("value", ""), _text(o)) for o in el.css("option")]
                field["options"] = opts[:30]
            fields.append(field)
        if fields:
            out.append({"index": i, "method": method, "action": action, "fields": fields})
    return out[:6]


def links(html: str, pattern: str, base_url: str, limit: int = 20) -> list[tuple[str, str]]:
    """Links whose text or address matches the pattern (a word, or a simple regex), deduplicated."""
    try:
        rx = re.compile(pattern, re.I)
    except re.error:
        rx = re.compile(re.escape(pattern), re.I)
    tree = extractor.parse_html(html)
    out, seen = [], set()
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        text = privacy.mask_text(_text(a))[:70] or (a.attributes.get("title") or a.attributes.get("aria-label") or "")[:70]
        url = urljoin(base_url, href).split("#")[0]
        if url in seen or not (rx.search(text) or rx.search(url)):
            continue
        seen.add(url)
        out.append((text, url))
        if len(out) >= limit:
            break
    return out


def pagination(html: str, base_url: str) -> list[tuple[str, str]]:
    """Next / page links the page shows: [(label, absolute url)]."""
    out = []
    for hint in recipe_builder._pagination_hints(html):
        m = _LINK_TAG.search(hint)
        if not m:
            continue
        attrs = dict(_ATTR.findall(m.group(2)))
        label = extractor.clean_text(re.sub(r"<[^>]+>", "", m.group(3)))[:30]
        if attrs.get("href"):
            out.append((label, urljoin(base_url, attrs["href"])))
        else:
            out.append((label + " (button)", ""))
    return out


def short(url: str, base_url: str = "") -> str:
    """Show the path for same-site links, the whole address otherwise."""
    if base_url and urlsplit(url).netloc == urlsplit(base_url).netloc:
        p = urlsplit(url)
        return (p.path or "/") + (f"?{p.query}" if p.query else "")
    return url


def page_summary(page, goal: str, musts: list[str], columns: list[str], budget: int) -> tuple[str, dict]:
    """The summary text and the facts it is built from (lists, forms, links, next pages), so the tools can
    allow exactly the addresses the agent has seen."""
    html = page.html[:MAX_HTML]
    url = page.final_url or page.url
    redacted = privacy.redact_html(html, full=bool(getattr(page, "logged_in", False)))
    tree = extractor.parse_html(redacted)
    facts = {"url": url, "lists": lists(redacted), "forms": forms(redacted, url),
             "links": links(redacted, "|".join(map(re.escape, goal_words(goal, *musts))) or r"(?!)", url, 12),
             "next": pagination(redacted, url), "json": structured.find_lists(html)[:2],
             "blocked": blockdetect.detect(html, page.status, url)}
    body = tree.body
    body_text = extractor.clean_text(body.text(separator=" ") if body is not None else "")[:20000]
    total = quality.stated_total(redacted)
    # "0 items" next to a basket / wishlist is the shop's counter, not the result count
    zero = next((m for m in _ZERO.finditer(body_text)
                 if not re.search(r"basket|cart|bag|wishlist|wish list|saved|compare|checkout|update",
                                  body_text[max(0, m.start() - 40):m.end() + 25], re.I)), None)
    facts["says"] = f"{total:,} results" if total else (zero.group(0) if zero else "")
    facts["items"] = max([lst["count"] for lst in facts["lists"]] + [0])
    # A dead end (remembered, so it isn't opened again): the page says it has no results, or it shows no
    # list of items and hardly any links to go on with.
    facts["dead_end"] = facts["items"] == 0 and not facts["json"] and (bool(zero) or len(tree.css("a[href]")) < 3)
    title = _text(tree.css_first("title"))[:80]
    headings = [_text(h)[:70] for h in tree.css("h1")[:1] + tree.css("h2")[:2] if _text(h)]
    col_words = [c.lower() for c in columns]

    def render(level: int) -> str:
        how = "browser-rendered" if getattr(page, "rendered", False) else "plain fetch"
        lines = [f"PAGE {url} ({page.status}, {how})",
                 f"title: {title}" + (f"        says: \"{facts['says']}\"" if facts["says"] else "")]
        if facts["blocked"]:
            lines.append(f"blocked: {blockdetect.describe(facts['blocked'])}")
        if facts["lists"]:
            lines.append("lists:")
            for i, lst in enumerate(facts["lists"], 1):
                samples = lst["samples"][: (2 if level >= 2 else 1)]
                lines.append(f"  {i}. {lst['count']} items · {lst['selector']} · e.g. " + " | ".join(f'"{s}"' for s in samples)
                             + ("   (looks like a menu)" if lst.get("menu") else ""))
                if level >= 1 and lst["fields"]:
                    seen_cols = [c for c in col_words if any(c in s.lower() for s in lst["samples"])]
                    lines.append("     fields seen: " + " · ".join(lst["fields"][: (8 if level >= 3 else 5)])
                                 + (f"   (goal words in samples: {', '.join(seen_cols)})" if seen_cols else ""))
        else:
            lines.append("lists: none found (no repeated blocks of items on this page)")
        for j in facts["json"][:1]:
            lines.append(f"json lists: {j['path']} · {j['count']} items · keys " + ", ".join(list(j["keys"])[:6]))
        if facts["next"]:
            lines.append("next page: " + " · ".join(f'"{label}" → {short(u, url) if u else "(in-page button)"}'
                                                   for label, u in facts["next"][:3]))
        if facts["forms"]:
            parts = []
            for f in facts["forms"][:3]:
                fs = []
                for fl in f["fields"][:6]:
                    if fl["type"] == "hidden":
                        continue
                    desc = fl["name"]
                    if fl.get("options"):
                        opts = [t or v for v, t in fl["options"][: (6 if level >= 2 else 3)]]
                        desc += f" (select: {', '.join(opts)}" + (f", +{len(fl['options']) - len(opts)}" if len(fl["options"]) > len(opts) else "") + ")"
                    elif fl["placeholder"]:
                        desc += f' ({fl["type"]}, "{fl["placeholder"]}")'
                    else:
                        desc += f" ({fl['type']})"
                    fs.append(desc)
                parts.append(f"form {f['index']}: {f['method']} {short(f['action'], url)} · " + " · ".join(fs))
            lines.append("search forms: " + " || ".join(parts))
        if facts["links"] and level >= 1:
            words = ", ".join(goal_words(goal, *musts)[:6])
            shown = facts["links"][: (12 if level >= 3 else 6 if level >= 2 else 3)]
            lines.append(f"links matching the goal ({words}): " + "; ".join(f'"{t}" → {short(u, url)}' for t, u in shown))
        if headings:
            lines.append("headings: " + " · ".join(f'"{h}"' for h in headings))
        return "\n".join(lines)

    text = ""
    for level in (3, 2, 1, 0):
        text = render(level)
        if len(text) <= budget:
            break
    return text[:budget], facts
