"""Agent worker: runs browser-use in its own environment (.venv-agent), one run at a time.

Started by the app (app/agent/runner.py) and talks to it only through its local API:
  GET  /api/agent/worker/next                 claim the next approved run
  POST /api/agent/worker/runs/{id}/check      may the agent do this? (site, robots.txt, risky clicks)
  POST /api/agent/worker/runs/{id}/step       record a step (+ screenshot); the app answers "stop?"
  POST /api/agent/worker/runs/{id}/arrived    the results list: the app takes over and collects
  POST /api/agent/worker/runs/{id}/finish     the run ended without reaching the results

Every action the AI chooses is checked by the app before it runs; refused actions come back to the AI
as an error so it can choose another way. Telemetry and cloud features are off (set by the app).
"""
import asyncio
import base64
import io
import os
import sys
import time

import httpx

APP = os.environ["AGENT_APP_URL"]
HEADERS = {"X-Agent-Token": os.environ["AGENT_TOKEN"]}
IDLE_EXIT_S = 15 * 60
# Actions the agent never gets: web search engines, files, uploads, arbitrary JavaScript, PDFs.
EXCLUDED = ["search", "upload_file", "write_file", "replace_file", "read_file", "evaluate", "save_as_pdf",
            "screenshot", "extract"]
# Edge opens its own new-tab page at start; browser-use only knows Chrome's, and closing it deadlocks the start.
BROWSER_START_PAGES = ["edge://newtab", "edge://new-tab-page"]
KEEP_ATTRS = ("id", "name", "type", "role", "aria-label", "placeholder", "title", "href", "value", "class",
              "data-testid", "autocomplete", "alt")

from browser_use import Agent, BrowserProfile, Tools                      # noqa: E402
from browser_use.agent.views import ActionResult                          # noqa: E402

client = httpx.Client(base_url=APP, headers=HEADERS, timeout=60)


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def post(path: str, **body) -> dict:
    try:
        r = client.post(path, json=body)
        return r.json() if r.status_code < 500 else {}
    except httpx.HTTPError as e:
        log(f"app unreachable: {e}")
        return {}


def make_llm():
    provider, model, key = os.environ["AGENT_PROVIDER"], os.environ["AGENT_MODEL"], os.environ["AGENT_API_KEY"]
    if provider == "anthropic":
        from browser_use import ChatAnthropic
        return ChatAnthropic(model=model, api_key=key)
    if provider == "openai":
        from browser_use import ChatOpenAI
        return ChatOpenAI(model=model, api_key=key, base_url=os.environ.get("AGENT_BASE_URL") or None)
    from browser_use import ChatGroq
    return ChatGroq(model=model, api_key=key)


async def describe(browser_session, index) -> dict:
    """The element behind an index: tag, text, stable attributes and xpath (recorded for routes)."""
    try:
        node = await browser_session.get_element_by_index(int(index))
    except Exception:
        node = None
    if node is None:
        return {}
    try:
        text = node.get_all_children_text(max_depth=3)
    except Exception:
        text = ""
    attrs = {k: str(v)[:200] for k, v in (node.attributes or {}).items() if k in KEEP_ATTRS}
    return {"tag": node.tag_name, "text": " ".join((text or "").split())[:200], "attributes": attrs,
            "xpath": getattr(node, "xpath", "")}


def sentence(name: str, params: dict, el: dict) -> str:
    attrs = el.get("attributes") or {}
    label = el.get("text") or attrs.get("aria-label") or attrs.get("value") or attrs.get("placeholder") or \
        attrs.get("title") or attrs.get("name") or el.get("tag", "element")
    label = label[:60]
    return {
        "navigate": f"Opened {params.get('url', '')}",
        "click": f"Clicked “{label}”",
        "input": f"Typed “{params.get('text', '')}” into “{label}”",
        "select_dropdown": f"Chose “{params.get('text', '')}” in “{label}”",
        "send_keys": f"Pressed {params.get('keys', '')}",
        "scroll": "Scrolled " + ("down" if params.get("down", True) else "up"),
        "go_back": "Went back",
        "wait": f"Waited {params.get('seconds', 3)}s",
        "find_text": f"Scrolled to “{params.get('text', '')}”",
        "dropdown_options": f"Looked at the options of “{label}”",
        "switch": "Switched tab",
        "close": "Closed a tab",
        "search_page": f"Searched the page for “{params.get('pattern', params.get('query', ''))}”",
        "find_elements": "Looked for elements on the page",
        "results_reached": "Reached the results list",
        "done": "Finished",
    }.get(name, name.replace("_", " ").capitalize())


class Run:
    def __init__(self, job: dict):
        self.id = job["id"]
        self.job = job
        self.ai_calls = 0
        self.steps = 0
        self.arrived = False
        self.stop_reason = ""
        self.stop_message = ""
        self.browser_session = None

    def counters(self) -> dict:
        return {"steps": self.steps, "ai_calls": self.ai_calls}


class GuardedTools(Tools):
    """browser-use tools where the app approves every action first and each one is recorded."""

    def __init__(self, run: Run):
        super().__init__(exclude_actions=EXCLUDED)
        self.run = run

        @self.action("Call this when the current page shows the LIST of results the person wants (after "
                     "applying the filters you could). note: one line about the filters applied.")
        async def results_reached(note: str, browser_session) -> ActionResult:
            return await self._arrive(note, browser_session)

    async def _arrive(self, note: str, browser_session) -> ActionResult:
        page = await browser_session.get_current_page()
        url = await browser_session.get_current_page_url()
        html = await page.evaluate("() => document.documentElement.outerHTML") if page else ""
        res = post(f"/api/agent/worker/runs/{self.run.id}/arrived", url=url, html=html, note=note[:400])
        if res.get("ok"):
            self.run.arrived = True
            return ActionResult(is_done=True, success=True,
                                extracted_content="The app took over and is collecting the results.")
        return ActionResult(error=f"Not accepted as the results list: {res.get('reason', 'unknown')}. Keep going.")

    async def act(self, action, browser_session, *args, **kwargs):
        self.run.browser_session = browser_session
        for name, params in action.model_dump(exclude_unset=True).items():
            if params is None:
                continue
            params = params if isinstance(params, dict) else {}
            el = await describe(browser_session, params["index"]) if params.get("index") is not None else {}
            check = {"navigate": {"url": params.get("url", "")}}.get(name, {})
            if name in ("navigate", "click", "input", "select_dropdown"):
                res = post(f"/api/agent/worker/runs/{self.run.id}/check", action=name, element=el,
                           text=str(params.get("text", "")), url=check.get("url", ""))
                if not res.get("ok", False):
                    return ActionResult(error=f"Refused by the app: {res.get('reason') or 'not allowed'}. "
                                              "Choose another way (only search, filter, sort and read).")
            result = await super().act(action, browser_session, *args, **kwargs)
            if name not in ("done", "results_reached"):
                url = ""
                try:
                    url = await browser_session.get_current_page_url()
                except Exception:
                    pass
                post(f"/api/agent/worker/runs/{self.run.id}/step", kind="action", text=sentence(name, params, el),
                     url=url, detail={"action": name, "params": {k: v for k, v in params.items() if k != "index"},
                                      "target": el, "error": (result.error or "")[:300]},
                     **self.run.counters())
            return result
        return ActionResult()


async def thumbnail(browser_session) -> str:
    try:
        page = await browser_session.get_current_page()
        raw = await page.screenshot(format="jpeg", quality=60)
        from PIL import Image
        img = Image.open(io.BytesIO(base64.b64decode(raw)))
        img.thumbnail((640, 400))
        out = io.BytesIO()
        img.convert("RGB").save(out, "JPEG", quality=60)
        return base64.b64encode(out.getvalue()).decode()
    except Exception:
        return ""


async def page_html(browser_session) -> str:
    try:
        page = await browser_session.get_current_page()
        return (await page.evaluate("() => document.documentElement.outerHTML"))[:1_500_000]
    except Exception:
        return ""


async def execute(job: dict) -> None:
    run = Run(job)
    limits = job["limits"]
    llm = make_llm()
    original = llm.ainvoke

    async def counted(*a, **kw):          # every AI call is counted against the run's limit
        run.ai_calls += 1
        return await original(*a, **kw)
    object.__setattr__(llm, "ainvoke", counted)

    show = os.environ.get("AGENT_SHOW_BROWSER") == "1"
    profile = BrowserProfile(headless=not show, executable_path=os.environ.get("AGENT_BROWSER") or None,
                             allowed_domains=job["allowed_domains"] + BROWSER_START_PAGES, storage_state=job.get("storage_state"),
                             block_ip_addresses=True, keep_alive=False, window_size={"width": 1280, "height": 900})
    tools = GuardedTools(run)
    vision = os.environ.get("AGENT_VISION") == "1"

    async def on_step(state, output, n):
        run.steps = n
        goal = (getattr(output, "next_goal", "") or getattr(output, "memory", "") or "").strip()
        bs = run.browser_session
        shot = await thumbnail(bs) if bs else ""
        html = await page_html(bs) if bs else ""
        res = post(f"/api/agent/worker/runs/{run.id}/step", kind="thought", text=goal or "Looking at the page",
                   url=getattr(state, "url", ""), shot=shot, html=html, **run.counters())
        if res.get("stop"):
            run.stop_reason, run.stop_message = res.get("reason", "stopped"), res.get("message", "")

    async def should_stop() -> bool:
        return bool(run.stop_reason) or run.arrived

    agent = Agent(
        task=job["task"], llm=llm, browser_profile=profile, tools=tools,
        use_vision=vision, use_judge=False, enable_planning=False, calculate_cost=False,
        flash_mode=not vision, use_thinking=vision, max_actions_per_step=3, max_failures=3,
        max_clickable_elements_length=12000 if not vision else 30000,
        initial_actions=[{"navigate": {"url": job["start_url"], "new_tab": False}}],
        register_new_step_callback=on_step, register_should_stop_callback=should_stop,
        llm_timeout=90, step_timeout=150,
    )
    started = time.time()
    try:
        history = await asyncio.wait_for(agent.run(max_steps=int(limits.get("steps", 40))),
                                         timeout=float(limits.get("minutes", 10)) * 60 + 60)
        final = (history.final_result() or "") if history else ""
    except asyncio.TimeoutError as e:
        # The same class covers the run's own time limit and timeouts inside browser-use (e.g. the browser
        # not starting); only the first one is a limit.
        final = ""
        if time.time() - started >= float(limits.get("minutes", 10)) * 60:
            run.stop_reason = run.stop_reason or "limit"
            run.stop_message = run.stop_message or "Reached the time limit."
        else:
            run.stop_reason = run.stop_reason or "error"
            what = "The browser didn't start in time" if "BrowserStart" in str(e) else "The browser stopped responding"
            run.stop_message = run.stop_message or f"{what}. Try again; if it keeps happening, restart the app."
    except Exception as e:
        final = ""
        run.stop_reason = run.stop_reason or "error"
        run.stop_message = run.stop_message or f"The agent failed: {type(e).__name__}: {str(e)[:300]}"
    finally:
        try:
            await agent.close()
        except Exception:
            pass
    if run.arrived:
        log(f"run {run.id}: handed over after {run.steps} steps, {run.ai_calls} AI calls")
        post(f"/api/agent/worker/runs/{run.id}/step", kind="info",
             text=f"The agent finished in {run.steps} steps, {run.ai_calls} AI calls, {round(time.time() - started)}s.",
             **run.counters())
        return
    if "HUMAN CHECK" in final.upper() and not run.stop_reason:
        run.stop_reason = "human_check"
    status = {"stopped": "stopped", "limit": "limit", "human_check": "needs_you"}.get(run.stop_reason, "error")
    message = run.stop_message or (final[:600] if final else "")
    if status == "needs_you":
        message = (message or "The site asked for a human check.") + \
            " The agent never solves these. Use “Open to solve”, solve it yourself, then “Try again”."
    elif status == "limit" and not message:
        message = f"Reached the limit of {limits.get('steps')} steps before finding the results."
    elif status == "error" and not message:
        message = "The agent could not reach the results list."
    post(f"/api/agent/worker/runs/{run.id}/finish", status=status, message=message, **run.counters())
    log(f"run {run.id}: {status}: {message[:120]}")


def main() -> None:
    log(f"agent worker started (python {sys.version.split()[0]}, provider {os.environ.get('AGENT_PROVIDER')})")
    idle_since = time.time()
    while True:
        try:
            job = client.get("/api/agent/worker/next").json()
        except Exception as e:
            log(f"app unreachable: {e}")
            if time.time() - idle_since > 120:
                return
            time.sleep(3)
            continue
        if not job.get("id"):
            if time.time() - idle_since > IDLE_EXIT_S:
                log("idle, exiting")
                return
            time.sleep(2)
            continue
        log(f"run {job['id']}: starting on {job['start_url']}")
        try:
            asyncio.run(execute(job))
        except Exception as e:
            post(f"/api/agent/worker/runs/{job['id']}/finish", status="error",
                 message=f"The agent crashed: {type(e).__name__}: {str(e)[:300]}")
        idle_since = time.time()


if __name__ == "__main__":
    main()
