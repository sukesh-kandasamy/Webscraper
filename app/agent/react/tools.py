"""The agent's tools. Every one is guarded: addresses must be ones the site showed (or the start page),
on the planned site, allowed by robots.txt and your never-scrape list; form fields go through the same
personal-data checks as the browser agent's typing. A refusal is an observation the AI reads, not a crash.
"""
import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from ... import config
from ...fetching import blockdetect, fetcher
from ...security import audit, sessions
from .. import guard, handoff, planner, runner, store
from . import summary

browse_html: dict[int, str] = {}        # task id -> the page the browser ended on (this process only)
BROWSE_MAX_STEPS = 15
BROWSE_MAX_MINUTES = 6.0
BROWSE_GRACE_S = 60                  # extra wait for the browser to start and stop
MAX_QUESTIONS = 2


@dataclass
class Observation:
    text: str
    ok: bool = True
    terminal: bool = False
    end: tuple | None = None           # ("needs_you" | "limit" | "error", message): the run ends this way
    data: dict = field(default_factory=dict)


def _norm(url: str) -> str:
    return (url or "").strip().split("#")[0]


def _host(netloc: str) -> str:
    return netloc.lower().removeprefix("www.")


def _base(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, _host(p.netloc), p.path or "/", "", ""))


def _key(url: str) -> str:
    """One address however the site writes it: www. or not, http or https, a trailing slash or not."""
    p = urlsplit(_norm(url))
    return urlunsplit(("", _host(p.netloc), p.path.rstrip("/") or "/", p.query, ""))


PAGE_ID = re.compile(r"^\[?(P\d+)\]?$", re.I)
CURRENT = ("current", "current page", "this page", "here")


def page_id(ctx, url: str) -> str:
    """A short name ("P3") for an address the agent has seen. Long addresses (booking.com's are 500+
    characters of tracking parameters) get mangled when an AI copies them; the ID never does."""
    ids = ctx.facts.setdefault("page_ids", {})
    for pid, u in ids.items():
        if _key(u) == _key(url):
            return pid
    pid = f"P{len(ids) + 1}"
    ids[pid] = _norm(url)
    return pid


def display(url: str, limit: int = 160) -> str:
    """An address as the AI reads it: host, path and the short parameters (long tracking values left out)."""
    p = urlsplit(_norm(url))
    params = [f"{k}={v}" for k, v in parse_qsl(p.query, keep_blank_values=True) if len(v) <= 30][:12]
    text = p.netloc.removeprefix("www.") + (p.path or "/") + ("?" + "&".join(params) if params else "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _resolve(ctx, url: str) -> str:
    """A page ID ("P3"), "current" or nothing means a page the agent already has: its exact address."""
    s = (url or "").strip()
    if not s or s.lower() in CURRENT:
        return ctx.facts.get("last_urls", [ctx.start_url])[-1]
    m = PAGE_ID.match(s)
    if m:
        return ctx.facts.get("page_ids", {}).get(m.group(1).upper(), m.group(1).upper())
    return s


def _match_seen(url: str, seen: list[str]) -> str:
    """The seen address this is a copy of, when the AI cut it off or left some of its parameters out (the
    seen address is used, never the AI's version). "" when it matches none: then it's invented."""
    key, base = _key(url), _base(url)
    mine = set(parse_qsl(urlsplit(_norm(url)).query, keep_blank_values=True))
    hits = []
    for u in seen:
        if _base(u) != base:
            continue
        theirs = set(parse_qsl(urlsplit(u).query, keep_blank_values=True))
        if (len(key) >= 40 and _key(u).startswith(key)) or (mine and mine <= theirs):
            hits.append(u)
    return hits[-1] if hits else ""            # the most recently seen, e.g. the page the browser just reached


def _with_params(url: str, params: list[dict]) -> str:
    p = urlsplit(url)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    for item in params or []:
        name = str(item.get("name") or "").strip()
        if name:
            q[name] = str(item.get("value") or "").strip()
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q, doseq=False), ""))


def allow_url(url: str, ctx, params: list[dict] | None = None) -> tuple[bool, str, str]:
    """(ok, reason, final url). The site-address rule: only addresses the site showed, or a seen address
    with its own query parameters edited; then the usual guard (site, robots, cooldown, opt-out)."""
    url = _norm(_resolve(ctx, url))
    if PAGE_ID.match(url):
        known = ", ".join(ctx.facts.get("page_ids", {})) or "none yet"
        return False, f"There is no page {url}. Pages so far: {known}.", ""
    if not url.startswith(("http://", "https://")):
        url = urljoin(ctx.start_url, url)
    seen = ctx.facts.setdefault("seen_urls", [])
    keys = {_key(u) for u in seen} | {_key(ctx.start_url)}
    if _key(url) not in keys:
        url = _match_seen(url, seen) or url
    edited = False
    if params:
        base = _base(url)
        known = [u for u in seen if _base(u) == base]
        allowed_names = set()
        for u in known:
            allowed_names.update(k for k, _ in parse_qsl(urlsplit(u).query, keep_blank_values=True))
        for f in ctx.facts.get("forms", []):
            if _base(f["action"]) == base and f["method"] == "GET":
                allowed_names.update(fl["name"] for fl in f["fields"])
        names = [str(p.get("name") or "") for p in params]
        if not known:
            return False, "The site never showed this address, so its parameters can't be edited. Use search or find_links.", ""
        bad = [n for n in names if n not in allowed_names]
        if bad:
            return False, (f"The site never used the parameter(s) {', '.join(bad)} on this address; only parameters the "
                           f"site itself showed can be changed ({', '.join(sorted(allowed_names)) or 'none'}). "
                           "Use the search form instead."), ""
        url, edited = _with_params(url, params), True      # checked above: only the site's own parameters
    if not edited and _key(url) not in keys:
        return False, ("The site never showed this address. Open only the start page, links from a page summary "
                       "(find_links), search results (search) or a next page (next_page)."), ""
    if _key(url) in {_key(u) for u in ctx.facts.get("empty_urls", [])}:
        n = ctx.facts.get("empty_at", {}).get(url, "?")
        return False, f"That address showed no items at step {n}. Try another way.", ""
    ok, why = guard.check_url(url, ctx.site)
    if not ok:
        return False, why, ""
    return True, "", url


def _refuse(ctx, tool: str, why: str) -> Observation:
    store.add_step(ctx.run_id, "warn", f"Refused: {why}", detail={"tool": tool})
    audit.log("agent_refused", run_id=ctx.run_id, action=tool, reason=why[:200])
    return Observation(f"Refused: {why}", ok=False)


def _fetch(ctx, url: str) -> tuple:
    """(page, observation-or-None). Opens the address politely; a human check or a used-up budget ends the run."""
    if _norm(url) in ctx.pages:                  # keys have no #fragment: a browsed page answers "…/#2015" too
        return ctx.pages[_norm(url)], None
    try:
        page = fetcher.fetch_smart(url)
    except fetcher.NeedsHuman as e:
        kind = getattr(e, "kind", "") or "captcha"
        return None, Observation(f"The site showed {blockdetect.describe(kind)}.", ok=False,
                                 end=("needs_you", f"The site showed {blockdetect.describe(kind)}. The agent never "
                                      "solves these. Use “Open to solve”, solve it yourself, then “Try again”."))
    except fetcher.BudgetExceeded as e:
        return None, Observation(str(e), ok=False, end=("limit", f"The site's daily page budget is used up: {e}"))
    except fetcher.BlockedError as e:
        return None, Observation(f"Could not open {url}: {e}", ok=False)
    except fetcher.FetchError as e:
        return None, Observation(f"Could not open {url}: {e}", ok=False)
    kind = blockdetect.detect(page.html, page.status, page.final_url or url)
    if kind in ("captcha", "challenge", "login"):
        return None, Observation(f"The site showed {blockdetect.describe(kind)}.", ok=False,
                                 end=("needs_you", f"The site showed {blockdetect.describe(kind)}. The agent never "
                                      "solves these. Use “Open to solve”, solve it yourself, then “Try again”."))
    ctx.pages[_norm(url)] = page
    if page.final_url:
        ctx.pages[_norm(page.final_url)] = page
    return page, None


def _remember(ctx, page, facts: dict) -> None:
    seen = ctx.facts.setdefault("seen_urls", [])
    for u in [page.url, page.final_url] + [u for _, u in facts["links"]] + [u for _, u in facts["next"] if u] \
            + [f["action"] for f in facts["forms"]]:
        if u and u not in seen:
            seen.append(u)
    ctx.facts["forms"] = [f for f in ctx.facts.get("forms", []) if f.get("page") != page.final_url] + \
        [{**f, "page": page.final_url} for f in facts["forms"]]
    ctx.facts["last_urls"] = (ctx.facts.get("last_urls", []) + [page.final_url])[-4:]
    if facts.get("dead_end"):
        for u in {page.url, page.final_url}:
            if u not in ctx.facts.setdefault("empty_urls", []):
                ctx.facts["empty_urls"].append(u)
                ctx.facts.setdefault("empty_at", {})[u] = ctx.step_no
    ctx.summaries[page.final_url] = facts


def _labelled(ctx, page, text: str) -> str:
    """The summary's first line names the page by its ID and a short form of its address."""
    pid = page_id(ctx, page.final_url or page.url)
    head, _, rest = text.partition("\n")
    head = re.sub(r"^PAGE \S+", f"PAGE {pid} = {display(page.final_url or page.url)}", head, count=1)
    return f"{head}  (use {pid} as the url for this page)\n{rest}"


def _open(ctx, url: str) -> Observation:
    page, bad = _fetch(ctx, url)
    if bad:
        return bad
    text, facts = summary.page_summary(page, ctx.goal, ctx.musts(), ctx.column_names(), ctx.budget)
    _remember(ctx, page, facts)
    return Observation(_labelled(ctx, page, text), data={"url": page.final_url, "items": facts["items"]})


# ---- the tools --------------------------------------------------------------------------------

def open_page(args: dict, ctx) -> Observation:
    ok, why, url = allow_url(args.get("url", ""), ctx, args.get("params") or None)
    if not ok:
        return _refuse(ctx, "open_page", why)
    return _open(ctx, url)


def find_links(args: dict, ctx) -> Observation:
    pattern = str(args.get("pattern") or "").strip()[:60]
    if not pattern:
        return Observation("Give a pattern: a word or a simple regex that the link text or address contains.", ok=False)
    ok, why, url = allow_url(args.get("url") or ctx.facts.get("last_urls", [ctx.start_url])[-1], ctx)
    if not ok:
        return _refuse(ctx, "find_links", why)
    page, bad = _fetch(ctx, url)
    if bad:
        return bad
    found = summary.links(page.html, pattern, page.final_url or url)
    seen = ctx.facts.setdefault("seen_urls", [])
    for _, u in found:
        if u not in seen:
            seen.append(u)
    if not found:
        return Observation(f"No links matching \"{pattern}\" on {url}.", ok=False)
    return Observation(f"Links matching \"{pattern}\" on {page_id(ctx, url)} ({display(url, 80)}):\n" +
                       "\n".join(f'  "{t}" → {page_id(ctx, u)} = {display(u, 100)}' for t, u in found))


def search(args: dict, ctx) -> Observation:
    ok, why, url = allow_url(args.get("url") or ctx.facts.get("last_urls", [ctx.start_url])[-1], ctx)
    if not ok:
        return _refuse(ctx, "search", why)
    page, bad = _fetch(ctx, url)
    if bad:
        return bad
    forms = summary.forms(page.html, page.final_url or url)
    try:
        form = next(f for f in forms if f["index"] == int(args.get("form") or 0))
    except (StopIteration, ValueError, TypeError):
        return Observation(f"No form {args.get('form')} on this page. Forms here: " +
                           (", ".join(str(f["index"]) for f in forms) or "none"), ok=False)
    if form["method"] != "GET":
        return Observation("This form sends a POST request, which needs the browser. Try a GET search form, "
                           "category links, or finish if there is no other way.", ok=False)
    by_name = {f["name"]: f for f in form["fields"]}
    query = {f["name"]: f.get("value", "") for f in form["fields"] if f["type"] == "hidden" or f.get("value")}
    filled = []
    for item in args.get("fields") or []:
        name, value = str(item.get("name") or "").strip(), str(item.get("value") or "").strip()
        if name not in by_name:
            return Observation(f"The form has no field \"{name}\". Its fields: {', '.join(by_name) or 'none'}.", ok=False)
        fl = by_name[name]
        ok, why = guard.check_input({"attributes": {"name": name, "type": fl["type"], "placeholder": fl.get("placeholder", "")}}, value)
        if not ok:
            return _refuse(ctx, "search", why)
        if fl.get("options") and value and not any(value in (v, t) for v, t in fl["options"]):
            match = next((v for v, t in fl["options"] if value.lower() in (v.lower(), t.lower())), None)
            if match is None:
                return Observation(f"\"{value}\" is not an option of {name}. Options: " +
                                   ", ".join(t or v for v, t in fl["options"][:15]), ok=False)
            value = match
        query[name] = value
        filled.append(f"{name}={value}")
    target = urlunsplit(urlsplit(form["action"])[:3] + (urlencode(query), ""))
    ctx.facts.setdefault("seen_urls", []).append(target)
    ok, why = guard.check_url(target, ctx.site)
    if not ok:
        return _refuse(ctx, "search", why)
    obs = _open(ctx, target)
    obs.text = f"Searched with {', '.join(filled) or 'the form defaults'}.\n" + obs.text
    return obs


def check_results(args: dict, ctx) -> Observation:
    ok, why, url = allow_url(args.get("url") or ctx.facts.get("last_urls", [ctx.start_url])[-1], ctx)
    if not ok:
        return _refuse(ctx, "check_results", why)
    page, bad = _fetch(ctx, url)
    if bad:
        return bad
    facts = ctx.summaries.get(page.final_url)
    if not facts:
        _, facts = summary.page_summary(page, ctx.goal, ctx.musts(), ctx.column_names(), ctx.budget)
        _remember(ctx, page, facts)
    lists = facts["lists"]
    if not lists or facts["items"] == 0:
        return Observation("No list of items here" + (f' (the page says "{facts["says"]}")' if facts["says"] else "") +
                           ". A results page shows repeated blocks of items; this one doesn't.", ok=False,
                           data={"is_list": False})
    best = lists[0]
    cols = ctx.column_names()
    seen_cols = [c for c in cols if any(c.lower().replace("_", " ") in s.lower() or c.lower() in s.lower() for s in best["samples"])]
    lines = [f"Looks like a results list: {best['count']} items ({best['selector']}), e.g. " +
             " | ".join(f'"{s}"' for s in best["samples"][:2])]
    if facts["says"]:
        lines.append(f"The page says \"{facts['says']}\".")
    lines.append("Next page: " + (", ".join(f'"{l}"' for l, _ in facts["next"][:2]) if facts["next"] else "none seen") + ".")
    if cols:
        lines.append("Wanted column words seen in the items: " + (", ".join(seen_cols) or "none yet (the app's AI maps columns when collecting)") + ".")
    app_side = [c["text"] for c in ctx.checklist if c["kind"] == "app"]
    if app_side:
        lines.append("After collecting, the app itself applies: " + "; ".join(app_side) + ". Do NOT sort, filter or "
                     "limit for these on the site: when the must items are true, collect this page.")
    pid = page_id(ctx, page.final_url or url)
    lines.append(f"To collect it: collect with url {pid}.")
    return Observation("\n".join(lines), data={"is_list": True, "items": best["count"]})


def next_page(args: dict, ctx) -> Observation:
    ok, why, url = allow_url(args.get("url") or ctx.facts.get("last_urls", [ctx.start_url])[-1], ctx)
    if not ok:
        return _refuse(ctx, "next_page", why)
    page, bad = _fetch(ctx, url)
    if bad:
        return bad
    nxt = [(l, u) for l, u in summary.pagination(page.html, page.final_url or url) if u]
    if not nxt:
        return Observation("No next-page link found on this page (the app finds in-page paging itself when collecting).", ok=False)
    label, target = nxt[0]
    ctx.facts.setdefault("seen_urls", []).append(target)
    return Observation(f'Next page: "{label}" → {target}', data={"url": target})


def browse(args: dict, ctx) -> Observation:
    """One sub-task for the browser worker (browser-use): the run waits as "browsing", then reads the page
    the browser ended on. The worker's clicks and typing go through the same /check guard as before, and
    its steps show on the timeline."""
    task = " ".join(str(args.get("task") or "").split())[:400]
    if not task:
        return Observation("Say what the browser should do (task: one plain sentence).", ok=False)
    ok, why, url = allow_url(args.get("url") or ctx.facts.get("last_urls", [ctx.start_url])[-1], ctx)
    if not ok:
        return _refuse(ctx, "browse", why)
    st = runner.status()
    if not (st["installed"] and st["browser"]):
        return Observation("The browser worker isn't installed on this computer, so pages that need clicking can't "
                           "be handled here. Use links, search forms and addresses, or finish.", ok=False)
    left = ctx.left()
    if left["steps"] < 2 or left["minutes"] < 1:
        return Observation("Not enough steps or time left for the browser.", ok=False)
    if not store.transition(ctx.run_id, "thinking", "browsing", message="Using the browser"):
        return Observation("The run is no longer thinking.", ok=False)
    limits = {"steps": min(BROWSE_MAX_STEPS, left["steps"]), "minutes": min(BROWSE_MAX_MINUTES, left["minutes"])}
    run = store.get(ctx.run_id)
    task_id = store.create_browse_task(ctx.run_id, planner.browse_task_text(ctx.plan, task, url), url, limits,
                                       {"steps": run["steps"], "ai_calls": run["ai_calls"], "tokens": run["tokens"]})
    store.add_step(ctx.run_id, "info", f"Browser sub-task: {task}" +
                   (" (a browser window opens on your screen; you can watch it work)" if config.AGENT_SHOW_BROWSER else ""),
                   url=url)
    try:
        runner.ensure(ctx.app_url or runner.last_app_url)
    except RuntimeError as e:
        store.finish_browse_task(task_id, "failed", message=str(e))
        store.transition(ctx.run_id, "browsing", "thinking", message="Thinking")
        return Observation(f"The browser could not start: {e}", ok=False)
    deadline = time.time() + limits["minutes"] * 60 + BROWSE_GRACE_S
    while True:
        t = store.get_browse_task(task_id)
        if not t or t["status"] not in ("queued", "running"):
            break
        if time.time() > deadline:
            if not t["stop_requested"]:
                store.update_browse_task(task_id, stop_requested=1)   # the worker stops at its next step
                deadline += BROWSE_GRACE_S * 0.75
            else:
                store.finish_browse_task(task_id, "stopped", message="The browser took too long.")
        time.sleep(1)
    t = store.get_browse_task(task_id) or {"status": "failed", "message": "The sub-task vanished."}
    store.transition(ctx.run_id, "browsing", "thinking", message="Thinking")
    run = store.get(ctx.run_id)
    ctx.steps, ctx.ai_calls = int(run["steps"] or 0), int(run["ai_calls"] or 0)
    if t["status"] == "done":
        html = browse_html.pop(task_id, "")
        final = t["result_url"] or url
        session = sessions.for_host(fetcher.host_of(final))
        page = fetcher.Page(final, final, 200, html, False, rendered=True,
                            logged_in=bool(session and session.get("kind") == sessions.KIND_LOGIN))
        ctx.pages[_norm(final)] = page
        text, facts = summary.page_summary(page, ctx.goal, ctx.musts(), ctx.column_names(), ctx.budget)
        _remember(ctx, page, facts)
        return Observation(f"The browser did it ({t['note'] or 'sub-task done'}) and is on this page:\n"
                           f"{_labelled(ctx, page, text)}",
                           data={"url": final, "items": facts["items"]})
    if t["status"] == "needs_you":
        return Observation(t["message"], ok=False, end=("needs_you", t["message"]))
    return Observation(f"The browser could not do that: {t['message'] or t['status']}. Try another way or finish.", ok=False)


def ask_user(args: dict, ctx) -> Observation:
    """Pause the run with one question; the loop carries on when the person answers."""
    question = " ".join(str(args.get("question") or "").split())[:300]
    if not question:
        return Observation("Say what to ask (question).", ok=False)
    asked = int(ctx.facts.get("questions", 0))
    if asked >= MAX_QUESTIONS:
        return Observation("You already asked twice. Decide with what you know, or finish with the reason.", ok=False)
    ctx.facts["questions"] = asked + 1
    if not store.transition(ctx.run_id, "thinking", "question", question=question, answer="",
                            message="Waiting for your answer"):
        return Observation("The run is no longer thinking.", ok=False)
    return Observation(f"Asked: {question}", terminal=True, data={"paused": True})


def collect(args: dict, ctx) -> Observation:
    ok, why, url = allow_url(args.get("url") or ctx.facts.get("last_urls", [ctx.start_url])[-1], ctx)
    if not ok:
        return _refuse(ctx, "collect", why)
    note = str(args.get("note") or "").strip()[:400]
    pending = [c for c in ctx.checklist if c["kind"] == "must" and not c.get("done")]
    if pending and not note:
        return Observation("Not yet: these must items aren't ticked: " + "; ".join(c["text"] for c in pending) +
                           ". Make them true on the site (tick them in checklist_done), or say in note why this "
                           "site can't do one of them.", ok=False)
    for c in pending:
        c["done"] = True
        c["evidence"] = f"not done on the site: {note}"
    ctx.save_checklist()
    page, bad = _fetch(ctx, url)
    if bad:
        return bad
    try:
        pages = int(args.get("pages") or 0)
    except (TypeError, ValueError):
        pages = 0
    if pages > 0:
        plan = {**ctx.plan, "pages": max(1, min(pages, 50))}
        store.update(ctx.run_id, plan=plan)
        ctx.plan = plan
    store.bump(ctx.run_id, ai_calls=1)        # build_recipe inside the hand-over
    try:
        handoff._handoff(ctx.run_id, page.final_url or url, page.html, note)
    except Exception as e:  # noqa: BLE001 - the reason goes back to the agent as an observation
        store.transition(ctx.run_id, "collecting", "thinking", message="Thinking")
        return Observation(f"The app could not collect from this page: {str(e)[:400]}", ok=False)
    return Observation("Handed over: the app is collecting the results.", terminal=True)


def finish(args: dict, ctx) -> Observation:
    text = str(args.get("summary") or "").strip()[:400] or "The agent gave up without a reason."
    if (args.get("outcome") or "failed") == "ok":
        return Observation("Finishing with outcome ok is only possible through collect(url). If the page lists the "
                           "items, call collect; otherwise finish with outcome failed and the reason.", ok=False)
    return Observation(text, terminal=True, end=("error", f"The agent gave up: {text}"))


TOOLS = {"open_page": open_page, "find_links": find_links, "search": search, "check_results": check_results,
         "next_page": next_page, "browse": browse, "ask_user": ask_user, "collect": collect, "finish": finish}


def call_key(tool: str, args: dict) -> str:
    used = {k: v for k, v in args.items() if v not in ("", 0, [], None)}
    return tool + " " + json.dumps(used, sort_keys=True, ensure_ascii=False)


def run_tool(tool: str, args: dict, ctx) -> Observation:
    fn = TOOLS.get(tool)
    if fn is None:
        return Observation(f"Unknown tool \"{tool}\". Choose one of: {', '.join(TOOLS)}.", ok=False)
    key = call_key(tool, args)
    earlier = ctx.facts.setdefault("calls", {}).get(key)
    if earlier and tool not in ("collect", "ask_user"):
        prev = next((h for h in ctx.pad["history"] if h["n"] == earlier), None)
        return Observation(f"You already did exactly this at step {earlier}; the result is the same. Try another way "
                           f"(find_links, search, a different page).\n" + (prev["obs"] if prev else ""), ok=False)
    ctx.facts["calls"][key] = ctx.step_no
    return fn(args, ctx)
