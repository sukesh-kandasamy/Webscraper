"""Ask an AI model (Groq or Claude) to write a recipe (CSS selectors) for a page, then check it.

The model is only called once per new site. The saved recipe is reused for every later scrape.
"""
import json
import re
from collections import defaultdict

import anthropic
from selectolax.lexbor import LexborHTMLParser, LexborNode

from . import config, extractor

DROP_TAGS = ["script", "style", "svg", "noscript", "iframe", "link", "meta", "head", "template"]
KEEP_ATTRS = {"class", "id", "href", "src", "itemprop", "title", "aria-label", "role", "data-src"}
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


def _compact(node: LexborNode) -> str:
    html = LexborHTMLParser(node.html)
    for tag in DROP_TAGS:
        for n in html.css(tag):
            n.decompose()
    for n in html.css("*"):
        for attr in list(n.attributes):
            # data-* attributes often hold values (data-rating="4"), so keep the short ones.
            keep = attr in KEEP_ATTRS or (attr.startswith("data-") and
                                          len(n.attributes[attr] or "") <= 40)
            if not keep:
                del n.attrs[attr]
            elif n.attributes[attr] and len(n.attributes[attr]) > 150:
                n.attrs[attr] = n.attributes[attr][:150] + "…"
    body = html.body
    text = body.inner_html if body is not None else html.html
    text = re.sub(r"<!--.*?-->", "", text or "", flags=re.S)
    text = re.sub(r"\s+", " ", text)
    return text[:MAX_SAMPLE_CHARS]


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
                    "samples": [_compact(n) for n in nodes[:2]]})
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


def _pagination_hints(html: str) -> list[str]:
    tree = extractor.parse_html(html)
    hints = []
    for a in tree.css("a[href]"):
        txt = extractor.clean_text(a.text())
        cls = a.attributes.get("class") or ""
        rel = a.attributes.get("rel") or ""
        if re.fullmatch(r"(next|next ›|›|»|>|\d{1,3})", txt, re.I) or "next" in (cls + rel).lower():
            hints.append(f'<a class="{cls}" rel="{rel}" href="{a.attributes.get("href")}">{txt}</a>')
        if len(hints) >= 12:
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
- pagination: "next_link" with a selector for the next-page anchor, "url_param" with the query \
parameter name, or "none" if you cannot see pagination.
- Only use selectors that you can see in the provided HTML."""


def _schema(field_names: list[str]) -> dict:
    s = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "item_selector": s,
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": field_names},
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
                    "type": {"type": "string", "enum": ["none", "next_link", "url_param"]},
                    "selector": s, "param": s, "start": {"type": "integer"},
                },
                "required": ["type", "selector", "param", "start"],
                "additionalProperties": False,
            },
            "notes": s,
        },
        "required": ["item_selector", "fields", "pagination", "notes"],
        "additionalProperties": False,
    }


class _Refused(Exception):
    pass


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


def _ask_model(messages: list[dict], field_names: list[str]) -> str:
    """Send the conversation to the configured provider and return its JSON text."""
    ask = _ask_groq if config.AI_PROVIDER == "groq" else _ask_claude
    return ask(messages, _schema(field_names))


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
    return (f"{prompt}\n\nYour previous recipe was:\n```json\n{previous[:3000]}\n```\n"
            f"Problem: {problem}\n"
            "Return a corrected recipe, using only selectors visible in the HTML above.")


def _drop_missing(recipe: dict) -> dict:
    recipe["fields"] = [f for f in recipe["fields"] if not (f.get("attr") == "none")]
    return recipe


def build_recipe(html: str, url: str, field_names: list[str]) -> dict:
    """Return {"recipe", "rows", "report", "notes"} or raise RecipeBuildError."""
    if not config.ai_enabled():
        key = "GROQ_API_KEY" if config.AI_PROVIDER == "groq" else "ANTHROPIC_API_KEY"
        raise RecipeBuildError(f"No {key} set. Add it to .env, or write a recipe by hand.")
    candidates = find_candidates(html)
    if not candidates:
        raise RecipeBuildError("Couldn't find any repeated listing blocks on this page. "
                               "It may be rendered with JavaScript or be a single-item page.")

    samples = _fit_samples(candidates, config.ai_prompt_budget())
    parts = [f"Page URL: {url}",
             f"Fields to extract: {', '.join(field_names)}",
             "",
             "Below are the groups of repeated elements found on the page (most likely listings first). "
             "Each shows a CSS path hint, how many siblings matched, and samples of cleaned HTML "
             "(only class/id/href/src/itemprop/title/aria-label/role and short data-* attributes kept; "
             "long samples are cut off). "
             "Values can live in attributes, e.g. a rating in data-rating: use attr for that."]
    for i, c in enumerate(candidates):
        if not samples[i]:
            continue
        parts.append(f"\n### Candidate {i + 1}: `{c['selector_hint']}` ({c['count']} siblings)")
        if c.get("page_counts"):
            parts.append("Matches on the whole page: " +
                         ", ".join(f"`{sel}` = {n}" for sel, n in c["page_counts"]))
        for j, sample in enumerate(samples[i], 1):
            parts.append(f"Sample {j}:\n```html\n{sample}\n```")
    pag = _pagination_hints(html)
    parts.append("\n### Possible pagination links\n" + ("\n".join(pag) if pag else "(none found)"))
    prompt = "\n".join(parts)

    # Each attempt is one self-contained message, so a retry costs about the same as the first
    # call. That keeps it under per-request token limits such as Groq's free tier.
    messages = [{"role": "user", "content": prompt + "\n\nWrite the recipe."}]
    last_problem = ""
    for attempt in range(2):
        try:
            text = _ask_model(messages, field_names)
        except _Refused:
            raise RecipeBuildError("The AI model declined to analyse this page.")
        try:
            raw = json.loads(text)
            recipe = extractor.normalize_recipe(_drop_missing(raw))
        except (json.JSONDecodeError, extractor.RecipeError) as e:
            last_problem = f"The recipe was invalid: {e}"
        else:
            rows = extractor.extract(html, recipe, url)
            report = extractor.field_report(rows, recipe)
            weak = [k for k, v in report.items() if v < 0.5]
            if len(rows) >= 2 and len(weak) <= len(report) // 2:
                notes = raw.get("notes", "")
                if weak:
                    notes = (notes + f" | Mostly empty: {', '.join(weak)}. Check these fields.").strip(" |")
                return {"recipe": recipe, "rows": rows, "report": report, "notes": notes}
            last_problem = (f"Testing that recipe on the page gave {len(rows)} rows. "
                            f"Fields filled in fewer than half the rows: {weak or 'none'}. "
                            f"First rows: {json.dumps(rows[:2], ensure_ascii=False)[:1500]}"
                            + _field_debug(html, recipe, weak))
            if attempt == 0:
                messages = [{"role": "user", "content": _retry_prompt(prompt, text, last_problem)}]
                continue
            # Second attempt still weak: return it anyway so the user can fix it by hand.
            return {"recipe": recipe, "rows": rows, "report": report,
                    "notes": (raw.get("notes", "") + " | Warning: " + last_problem).strip(" |")}
        if attempt == 0:
            messages = [{"role": "user", "content": _retry_prompt(prompt, text, last_problem)}]
    raise RecipeBuildError(last_problem or "The AI model did not return a usable recipe.")
