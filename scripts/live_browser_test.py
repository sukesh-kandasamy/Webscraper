"""Live browser test: browser-use opens a visible Edge / Chrome window on your desktop and does a search.

It runs the agent worker's real code (agent_worker/worker.py: the browser-use agent, the app's guarded
actions and the widget skills: pick a suggestion, set a date, next page) with the app's own rules checked
locally instead of over HTTP: it stays on the site, follows robots.txt, refuses risky clicks (buy, contact,
log in, ...) and personal-data fields, and stops on a human check. Every step, check and refusal is printed
in this terminal while you watch. Nothing is saved; it needs no running app.

Run it with the agent's environment (where browser-use is installed):

    .venv-agent\\Scripts\\python scripts\\live_browser_test.py
    .venv-agent\\Scripts\\python scripts\\live_browser_test.py --query "Boston"
    .venv-agent\\Scripts\\python scripts\\live_browser_test.py --provider groq --hotels Goa ^
        --checkin 2026-11-05 --checkout 2026-11-10 --adults 2 --top 15

--hotels runs a booking.com hotel search (destination, dates, adults) and prints the top hotels it lists.
The AI is the agent's AI from Settings → AI models / .env, or --provider / --model.
PASS when the browser reaches a page that shows what was asked for. Exit code 0 / 1.
"""
import argparse
import csv
import datetime as dt
import html as htmllib
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app import config  # noqa: E402  (the AI keys and choices, as the app would pass them to the worker)
from app.agent import browse_text, guard  # noqa: E402
from app.agent.runner import installed_browser  # noqa: E402
from app.fetching import blockdetect, fetcher  # noqa: E402
from app.security import netguard  # noqa: E402

os.environ.update({
    "AGENT_APP_URL": "http://127.0.0.1:1",          # never contacted: post() below answers locally
    "AGENT_TOKEN": "local",
    "AGENT_BROWSER": installed_browser(),
    "AGENT_SHOW_BROWSER": "1",                      # the visible window is the point of this test
    "ANONYMIZED_TELEMETRY": "false", "BROWSER_USE_CLOUD_SYNC": "false",
    "BROWSER_USE_LOGGING_LEVEL": "warning",
})
sys.path.insert(0, str(ROOT / "agent_worker"))
import worker  # noqa: E402

DEFAULT_URL = "https://www.scrapethissite.com/pages/forms/"
DEFAULT_QUERY = "New York"
BOOKING = "https://www.booking.com/"
CARD_FIELDS = ["title", "address", "distance", "review-score", "price-and-discounted-price", "recommended-units"]


class LocalApp:
    """Answers the worker's /check, /step, /arrived and /finish calls in-process, with the app's rules."""

    def __init__(self, site: str, hold_s: float):
        self.site, self.hold_s = site, hold_s
        self.t0 = time.time()
        self.refused = 0
        self.arrived: dict = {}
        self.finished: dict = {}

    def say(self, mark: str, text: str) -> None:
        print(f"{time.time() - self.t0:6.1f}s  {mark}  {text}", flush=True)

    def check_url(self, url: str) -> tuple[bool, str]:
        if url.startswith(("about:", "edge:", "chrome:")):
            return True, ""
        try:
            netguard.check_url(url)
        except netguard.UnsafeURL as e:
            return False, str(e)
        if not guard.on_site(url, self.site):
            return False, f"{urlsplit(url).hostname} is not the planned site ({self.site})."
        if config.RESPECT_ROBOTS and not fetcher.robots_allows(url):    # the app's own robots.txt reading
            return False, "The site's robots.txt does not allow this page."
        return True, ""

    def post(self, path: str, **body) -> dict:
        what = path.rsplit("/", 1)[-1]
        if what == "check":
            action, el = body.get("action"), body.get("element") or {}
            if action == "navigate":
                ok, why = self.check_url(body.get("url", ""))
            elif action in ("click", "select_dropdown"):
                ok, why = guard.check_click({**el, "text": body.get("text") or el.get("text", "")}
                                            if action == "select_dropdown" else el)
            elif action == "input":
                ok, why = guard.check_input(el, body.get("text", ""))
            else:
                ok, why = True, ""
            if not ok:
                self.refused += 1
                self.say("✗", f"refused {action}: {why}")
            return {"ok": ok, "reason": why}
        if what == "step":
            mark = {"thought": "…", "action": "→", "info": "i", "warn": "!"}.get(body.get("kind"), "·")
            self.say(mark, body.get("text", "")[:160] + (f"   [{body['url']}]" if body.get("kind") == "action" and body.get("url") else ""))
            kind = blockdetect.detect(body.get("html", "")[:2_000_000], 200, body.get("url", "")) if body.get("html") else None
            if kind:
                self.say("!", f"the site showed {blockdetect.describe(kind)}: stopping (the agent never solves these)")
                return {"stop": True, "reason": "human_check"}
            return {"stop": False}
        if what == "arrived":
            ok, why = self.check_url(body.get("url", ""))
            if not ok:
                return {"ok": False, "reason": why}
            self.arrived = body
            self.say("✓", f"sub-task done on {body['url']}: {body.get('note', '')}")
            if self.hold_s:
                self.say("i", f"keeping the window open for {self.hold_s:.0f}s so you can look")
                time.sleep(self.hold_s)
            return {"ok": True}
        if what == "finish":
            self.finished = body
            self.say("✗", f"ended without finishing: {body.get('status')}: {body.get('message', '')[:200]}")
            return {"ok": True}
        return {}


def page_lines(html: str, words: str, n: int = 10) -> list[str]:
    """Visible text lines of the page that contain the expected words (a quick look at the results)."""
    text = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", html)
    text = re.sub(r"(?i)<(br|/p|/tr|/li|/h\d|/div)\b[^>]*>", "\n", text)
    text = htmllib.unescape(re.sub(r"<[^>]+>", " ", text))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return [line for line in lines if line and words.lower() in line.lower()][:n]


def cards(html: str, base: str, top: int) -> list[dict]:
    """Listing cards marked with data-testid="…card" (booking.com's property cards and similar), each with
    the text of its labelled parts and its link."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select('[data-testid$="property-card"], [data-testid="property-card"]')[:top]:
        row = {}
        for name in CARD_FIELDS:
            el = card.select_one(f'[data-testid="{name}"]')
            if el:
                row[name] = " ".join(el.get_text(" ").split())
        if row.get("review-score"):        # "Scored 7.8 7.8 Good 758 reviews" -> "7.8 Good · 758 reviews"
            m = re.search(r"(\d+(?:\.\d+)?)\s+(?:\1\s+)?(.*?)\s*([\d,]+ reviews?)?$", row["review-score"].removeprefix("Scored "))
            if m:
                row["review-score"] = f"{m.group(1)} {m.group(2)}".strip() + (f" · {m.group(3)}" if m.group(3) else "")
        link = card.select_one('a[data-testid="title-link"], a[href]')
        if link and link.get("href"):
            row["url"] = link["href"].split("?")[0] if link["href"].startswith("http") else base + link["href"].split("?")[0]
        if row.get("title"):
            out.append(row)
    return out


def hotel_task(args) -> tuple[str, str]:
    cin, cout = dt.date.fromisoformat(args.checkin), dt.date.fromisoformat(args.checkout)
    if cout <= cin:
        raise SystemExit("--checkout has to be after --checkin.")
    task = (f"Enter {args.hotels} in the destination box and pick its suggestion, set check-in {cin.isoformat()} "
            f"and check-out {cout.isoformat()}, set {args.adults} adults and 1 room, then click Search.")
    return task, BOOKING


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL, help="the page to start on")
    ap.add_argument("--query", default=DEFAULT_QUERY, help="what to search for with the site's search box")
    ap.add_argument("--task", default="", help="your own sub-task instead of the search (one plain sentence)")
    ap.add_argument("--expect", default="", help="text the final page must contain (default: the query)")
    ap.add_argument("--hotels", default="", help="booking.com hotel search for this destination")
    ap.add_argument("--checkin", default="", help="YYYY-MM-DD (with --hotels)")
    ap.add_argument("--checkout", default="", help="YYYY-MM-DD (with --hotels)")
    ap.add_argument("--adults", type=int, default=2)
    ap.add_argument("--top", type=int, default=15, help="how many listings to print (with --hotels)")
    ap.add_argument("--csv", default="", help="also write the listings to this CSV file")
    ap.add_argument("--provider", default="", choices=["", "groq", "openai", "anthropic"], help="the AI (default: Settings)")
    ap.add_argument("--model", default="", help="the model (default: the provider's model from Settings / .env)")
    ap.add_argument("--steps", type=int, default=0, help="browser-use step limit (default 12, 25 with --hotels)")
    ap.add_argument("--minutes", type=float, default=0, help="time limit (default 5, 10 with --hotels)")
    ap.add_argument("--hold", type=float, default=8, help="seconds to keep the window open at the end")
    args = ap.parse_args()

    provider = args.provider or config.AGENT_PROVIDER
    model = args.model or (config.AGENT_MODEL if provider == config.AGENT_PROVIDER else config._MODELS[provider]())
    key = config._KEYS[provider]()
    if not key:
        print(f"No AI key for {provider}. Add {config.KEY_NAMES[provider]} to .env.")
        return 2
    if not os.environ["AGENT_BROWSER"]:
        print("No Microsoft Edge or Google Chrome found.")
        return 2
    os.environ.update({"AGENT_PROVIDER": provider, "AGENT_MODEL": model, "AGENT_API_KEY": key,
                       "AGENT_BASE_URL": config.OPENAI_BASE_URL if provider == "openai" else "",
                       "AGENT_VISION": "1" if provider in ("anthropic", "openai") else "0"})   # gpt-oss is text-only

    if args.hotels:
        if not (args.checkin and args.checkout):
            raise SystemExit("--hotels needs --checkin and --checkout (YYYY-MM-DD).")
        task, url = hotel_task(args)
        expect = args.expect or args.hotels
    else:
        url = args.url
        task = args.task or (f"Type “{args.query}” into the site's search box, run the search, and stop when the "
                             "results for it are showing.")
        expect = args.expect or args.query
    steps = args.steps or (25 if args.hotels else 12)
    minutes = args.minutes or (10 if args.hotels else 5)
    site = guard.site_domain(url)
    local = LocalApp(site, args.hold)
    ok, why = local.check_url(url)
    if not ok:
        print(f"Can't start there: {why}")
        return 2
    worker.post = local.post

    text = browse_text.task_text(site, url, task)          # exactly what Agent mode sends
    job = {"id": 0, "kind": "browse", "task": text, "start_url": url, "site": site, "counters": {},
           "limits": {"steps": steps, "minutes": minutes}, "storage_state": None,
           "allowed_domains": [site, f"*.{site}"]}
    print(f"Live browser test · {provider} · {model} · {os.path.basename(os.environ['AGENT_BROWSER'])} · "
          f"up to {steps} steps / {minutes:g} min")
    print(f"Start: {url}\nTask:  {task}\n")
    t0 = time.time()
    worker.LOOP.run_until_complete(worker.execute(job))
    worker.LOOP.run_until_complete(worker.close_browser(job["id"]))      # the test is the whole job
    print()

    html, final = local.arrived.get("html", ""), local.arrived.get("url", "")
    if args.hotels:
        q = {k: v[0] for k, v in parse_qs(urlsplit(final).query).items()}
        wanted = {"checkin": args.checkin, "checkout": args.checkout, "group_adults": str(args.adults)}
        wrong = [f"{k}={q.get(k, '(missing)')} (wanted {v})" for k, v in wanted.items() if q.get(k) != v]
        listings = cards(html, f"{urlsplit(BOOKING).scheme}://{urlsplit(final or BOOKING).netloc}", args.top) if html else []
        for i, r in enumerate(listings, 1):
            print(f"{i:>2}. {r.get('title', '')}")
            details = " · ".join(r[k] for k in ("address", "distance", "review-score", "price-and-discounted-price") if r.get(k))
            if details:
                print(f"    {details[:180]}")
        if args.csv and listings:
            with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=CARD_FIELDS + ["url"], extrasaction="ignore")
                w.writeheader()
                w.writerows(listings)
            print(f"\nWrote {len(listings)} listings to {args.csv}")
        if wrong:
            print("\nThe results page doesn't match the search: " + "; ".join(wrong))
        passed = bool(local.arrived) and not wrong and len(listings) >= min(args.top, 1) and \
            expect.lower() in html.lower()
        found_text = f"{len(listings)} listings"
    else:
        found = page_lines(html, expect) if html else []
        for line in found:
            print(f"   {line[:150]}")
        passed = bool(local.arrived) and bool(found)
        found_text = f"{len(found)} matching lines"
    print(f"\n{'PASS' if passed else 'FAIL'} · {round(time.time() - t0)}s · {found_text} · {local.refused} action(s) refused"
          + (f"\nfinal page: {final}" if final else f" · {local.finished.get('message', 'no page reached')[:200]}"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
