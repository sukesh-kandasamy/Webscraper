"""Agent bench: a fixed set of prompts run through Agent mode, to compare models and catch regressions.

Run it against a TEST COPY of the app, never your own data: it creates runs and saved scrapes there, and
--models switches that copy's agent AI (put back at the end).

    set SCRAPER_DATA_DIR=%TEMP%\\scraper-bench
    .venv\\Scripts\\python -m uvicorn app.main:app --port 8013
    .venv\\Scripts\\python scripts\\agent_bench.py --app http://localhost:8013
    .venv\\Scripts\\python scripts\\agent_bench.py --models openai:gpt-5-mini,groq:openai/gpt-oss-120b --out bench.json

Every task names its site (public practice sites that allow scraping), so no site suggestion is involved.
A task passes when the run ends "done" with at least its minimum number of rows. Site memory is forgotten
before each task unless --keep-memory, so runs are comparable.
"""
import argparse
import json
import sys
import time

import httpx

TASKS = [
    {"id": "books", "min_rows": 30, "browser": False,
     "goal": "Mystery books from https://books.toscrape.com/: title, price and rating. All pages."},
    {"id": "quotes", "min_rows": 10, "browser": False,
     "goal": "Quotes tagged inspirational from https://quotes.toscrape.com/: the quote text and its author."},
    {"id": "hockey", "min_rows": 20, "browser": False,
     "goal": "Hockey teams with New in their name from https://www.scrapethissite.com/pages/forms/: "
             "team name, year, wins and losses."},
    {"id": "ajax", "min_rows": 15, "browser": True,
     "goal": "Oscar-winning films of 2015 from https://www.scrapethissite.com/pages/ajax-javascript/: "
             "title, nominations and awards."},
]
LIMITS = {"steps": 20, "minutes": 8, "ai_calls": 30}
ACTIVE = ("queued", "thinking", "browsing", "collecting", "fixing")
RUN_TIMEOUT_S = 12 * 60


def run_task(c: httpx.Client, task: dict, keep_memory: bool) -> dict:
    t0 = time.time()
    out = {"task": task["id"], "ok": False, "status": "", "rows": 0, "steps": 0, "ai_calls": 0, "tokens": 0,
           "seconds": 0, "run": "", "message": ""}
    try:
        plan = c.post("/api/agent/plan", json={"goal": task["goal"]})
        plan.raise_for_status()
        run = plan.json()
        if run.get("needs_site"):
            raise RuntimeError("the prompt's site wasn't recognised")
        if not keep_memory:
            c.delete("/api/agent/memory", params={"site": run["site"]}).raise_for_status()
        c.post(f"/api/agent/runs/{run['uid']}/start", json=LIMITS).raise_for_status()
        while True:
            v = c.get(f"/api/agent/runs/{run['uid']}").json()
            if v["status"] == "question":         # nobody answers in a bench: stop it and count it as a miss
                c.post(f"/api/agent/runs/{run['uid']}/stop")
                v = {**v, "message": f"asked: {v.get('question', '')}"}
                break
            if v["status"] not in ACTIVE:
                break
            if time.time() - t0 > RUN_TIMEOUT_S:
                c.post(f"/api/agent/runs/{run['uid']}/stop")
                v = {**v, "message": "timed out"}
                break
            time.sleep(3)
        rows = int((v.get("job") or {}).get("rows") or 0)
        out.update(status=v["status"], rows=rows, steps=v["steps"], ai_calls=v["ai_calls"], tokens=v["tokens"],
                   run=v["uid"], message=v.get("message", "")[:160],
                   ok=v["status"] == "done" and rows >= task["min_rows"],
                   browsed=any((s.get("detail") or {}).get("tool") == "browse" for s in v.get("steps_list", [])),
                   replans=sum(s["text"].startswith("Re-planned") for s in v.get("steps_list", [])))
    except Exception as e:  # noqa: BLE001 - one broken task shouldn't stop the bench
        out.update(status="bench error", message=f"{type(e).__name__}: {str(e)[:160]}")
    out["seconds"] = round(time.time() - t0)
    return out


def set_agent(c: httpx.Client, original: dict, provider: str, model: str) -> None:
    backup = original.get("agent_fallback")
    if backup and backup == {"provider": provider, "model": model}:
        backup = None
    r = c.put("/api/ai", json={"ai": original["ai"], "agent": {"provider": provider, "model": model},
                               "agent_fallback": backup})
    if r.status_code != 200:
        raise SystemExit(f"Could not switch the agent to {provider}:{model}: {r.json().get('detail', r.text)}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", default="http://localhost:8013", help="the test copy's address")
    ap.add_argument("--models", default="", help="provider:model,… (default: the copy's current agent AI)")
    ap.add_argument("--tasks", default="", help="task ids, comma-separated: " + ", ".join(t["id"] for t in TASKS))
    ap.add_argument("--keep-memory", action="store_true", help="let the agent use what it learned in earlier runs")
    ap.add_argument("--out", default="", help="write the results as JSON here")
    args = ap.parse_args()

    c = httpx.Client(base_url=args.app, timeout=240)
    original = c.get("/api/ai").json()
    tasks = [t for t in TASKS if not args.tasks or t["id"] in args.tasks.split(",")]
    models = [m.split(":", 1) for m in args.models.split(",") if m] or \
        [[original["agent"]["provider"], original["agent"]["model"]]]
    results = []
    try:
        for provider, model in models:
            if [provider, model] != [original["agent"]["provider"], original["agent"]["model"]]:
                set_agent(c, original, provider, model)
            for task in tasks:
                print(f"{provider}:{model} · {task['id']} …", flush=True)
                r = {"model": f"{provider}:{model}", **run_task(c, task, args.keep_memory)}
                results.append(r)
                print(f"  {'PASS' if r['ok'] else 'FAIL'} {r['status']} · {r['rows']} rows · {r['steps']} steps · "
                      f"{r['ai_calls']} AI calls · {r['tokens']:,} tokens · {r['seconds']}s · {r['run']} {r['message'][:80]}",
                      flush=True)
    finally:
        if [original["agent"]["provider"], original["agent"]["model"]] != [models[-1][0], models[-1][1]]:
            c.put("/api/ai", json={k: original[k] for k in ("ai", "agent", "agent_fallback")})

    print("\n| model | task | result | rows | steps | AI calls | tokens | seconds | browser | re-plans |")
    print("|---|---|---|---:|---:|---:|---:|---:|---|---:|")
    for r in results:
        print(f"| {r['model']} | {r['task']} | {'pass' if r['ok'] else 'FAIL: ' + r['status']} | {r['rows']} | "
              f"{r['steps']} | {r['ai_calls']} | {r['tokens']:,} | {r['seconds']} | "
              f"{'yes' if r.get('browsed') else ''} | {r.get('replans', 0)} |")
    for m in dict.fromkeys(r["model"] for r in results):
        mine = [r for r in results if r["model"] == m]
        print(f"\n{m}: {sum(r['ok'] for r in mine)}/{len(mine)} passed · "
              f"{sum(r['tokens'] for r in mine):,} tokens · {sum(r['seconds'] for r in mine)}s")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"at": time.time(), "results": results}, f, indent=2)
    sys.exit(0 if all(r["ok"] for r in results) else 1)


if __name__ == "__main__":
    main()
