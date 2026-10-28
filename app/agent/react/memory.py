"""Site memory: what worked on a site before, kept in its site settings (sites.settings["agent_memory"]).

Written when a ReAct run ends as done; read when the next run on that site starts. It is a hint for the
agent's AI ("WHAT WORKED ON THIS SITE BEFORE"), and the results address it names counts as an address the
site showed, so the agent may open it again; the usual guard still checks it.

    recall(url)            the remembered entries for a site, newest first
    remember(run)          add an entry from a finished run (its steps, results address, list selector)
    forget(url)            drop the site's memory
    prompt_lines(entries)  the hint as prompt lines
"""
import re
import time

from ... import db
from ...fetching import fetcher
from ...security import logsafe
from .. import guard

PAGE_REF = re.compile(r"\bP\d+ = ")
MAX_ENTRIES = 3
MAX_HOW = 6
KEPT_TOOLS = ("open_page", "search", "browse", "next_page", "collect")


def _domain(url: str) -> str:
    """One key per site, whether given as "www.site.com/page", "https://site.com" or "site.com"."""
    return guard.site_domain(url)


def recall(url: str) -> list[dict]:
    if not url:
        return []
    entries = (fetcher.site_settings(_domain(url)).get("agent_memory")
               or fetcher.site_settings("www." + _domain(url)).get("agent_memory") or [])
    return [e for e in entries if isinstance(e, dict)][:MAX_ENTRIES]


def forget(url: str) -> None:
    fetcher.update_settings(_domain(url), {"agent_memory": []})
    if fetcher.site_settings("www." + _domain(url)).get("agent_memory"):     # kept there before 2026-10-26
        fetcher.update_settings("www." + _domain(url), {"agent_memory": []})


def remember(run: dict) -> dict | None:
    """Add what this done run did to its site's memory; the same goal replaces its older entry."""
    pad = run.get("scratchpad") or {}
    hist = [h for h in pad.get("history", []) if h.get("ok") and h.get("tool") in KEPT_TOOLS]
    if not run.get("final_url") or not hist:
        return None
    selector = ""
    if run.get("scrape_id"):
        scrape = db.get_scrape(run["scrape_id"]) or {}
        selector = (scrape.get("recipe") or {}).get("item_selector", "")
    # Page IDs ("P3 = site.com/…") only mean something inside one run: keep the address alone.
    how = [logsafe.redact(PAGE_REF.sub("", f"{h['tool']} {h.get('arg', '')}").strip())[:180] for h in hist][-MAX_HOW:]
    entry = {"goal": logsafe.redact(run["goal"])[:160], "results_url": run["final_url"].split("#")[0],
             "how": how, "list_selector": selector[:120],
             "needs_browser": any(h["tool"] == "browse" for h in hist),
             "steps": int(run.get("steps") or 0), "at": time.time()}
    old = [e for e in recall(run["start_url"]) if e.get("goal", "").lower() != entry["goal"].lower()]
    fetcher.update_settings(_domain(run["start_url"]), {"agent_memory": [entry, *old][:MAX_ENTRIES]})
    return entry


def prompt_lines(entries: list[dict]) -> str:
    lines = []
    for e in entries:
        when = time.strftime("%d %b %Y", time.localtime(e.get("at") or 0))
        lines.append(f"  - {when}, for \"{e.get('goal', '')[:100]}\" ({e.get('steps', '?')} steps"
                     f"{', needed the browser' if e.get('needs_browser') else ''}):")
        lines += [f"      {h}" for h in e.get("how", [])]
        lines.append(f"      results page: {e.get('results_url', '')}"
                     + (f" · list: {e['list_selector']}" if e.get("list_selector") else ""))
    return "\n".join(lines)
