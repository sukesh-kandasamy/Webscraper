"""Agent worker: runs browser-use in its own environment (.venv-agent), one browse sub-task at a time.

The ReAct loop in the app sends it one thing to do in a real browser ("pick Goa in the destination box and
search") when a page needs clicking or typing. Started by the app (app/agent/runner.py) and talks to it only
through its local API:
  GET  /api/agent/worker/next                 claim the next browse sub-task
  POST /api/agent/worker/runs/{id}/check      may the agent do this? (site, robots.txt, risky clicks)
  POST /api/agent/worker/runs/{id}/step       record a step (+ screenshot); the app answers "stop?"
  POST /api/agent/worker/runs/{id}/arrived    the sub-task is done: the page goes back to the loop
  POST /api/agent/worker/runs/{id}/finish     the sub-task ended without getting there

Every action the AI chooses is checked by the app before it runs; refused actions come back to the AI
as an error so it can choose another way. Telemetry and cloud features are off (set by the app).
"""
import asyncio
import base64
import io
import os
import sys
import time
from urllib.parse import urlsplit

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

from browser_use import Agent, BrowserProfile, BrowserSession, Tools                      # noqa: E402
from browser_use.agent.views import ActionResult                          # noqa: E402

import llm_errors                                                         # noqa: E402
import skills                                                             # noqa: E402

SKILLS = ("pick_suggestion", "set_date", "next_page")

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


def make_llm(role: str = "AGENT"):
    """The agent's AI (role AGENT) or its backup (AGENT_FALLBACK), from the environment the app set."""
    provider, model, key = os.environ[f"{role}_PROVIDER"], os.environ[f"{role}_MODEL"], os.environ[f"{role}_API_KEY"]
    if provider == "anthropic":
        from browser_use import ChatAnthropic
        return ChatAnthropic(model=model, api_key=key)
    if provider == "openai":
        from browser_use import ChatOpenAI
        return ChatOpenAI(model=model, api_key=key, base_url=os.environ.get(f"{role}_BASE_URL") or None)
    from browser_use import ChatGroq
    return ChatGroq(model=model, api_key=key)


# browser-use moves to the backup model on these (rate limits, no credit, refused key, server errors)
SWITCHES_TO_BACKUP = ("daily_limit", "quota", "auth", "rate_limit", "server")


def track(llm, run: "Run", role: str) -> None:
    """Count every AI call and its tokens; turn provider errors into a clear stop (or a note when the
    backup takes over); wait out a short rate limit once instead of failing the step."""
    provider, model = os.environ[f"{role}_PROVIDER"], os.environ[f"{role}_MODEL"]
    original = llm.ainvoke

    async def counted(*a, **kw):
        run.ai_calls += 1
        if role == "AGENT_FALLBACK" and not run.using_backup:
            run.using_backup = True
            post(f"/api/agent/worker/runs/{run.id}/step", kind="warn",
                 text=f"{run.problem or 'The agent’s AI failed.'} Switched to the backup AI: "
                      f"{llm_errors.PROVIDERS.get(provider, provider)} · {model}.", **run.counters())
        for attempt in (1, 2):
            try:
                out = await original(*a, **kw)
                break
            except Exception as e:
                text, status = str(getattr(e, "message", "") or e), getattr(e, "status_code", None)
                p = llm_errors.classify(text, status, provider, model)
                if not p:
                    raise
                if attempt == 1 and p["kind"] == "rate_limit" and not p["fatal"] and p["wait_s"]:
                    # Say so: a minute of silence on the timeline looks like a hang.
                    post(f"/api/agent/worker/runs/{run.id}/step", kind="info",
                         text=f"Waiting {round(p['wait_s'])}s for {llm_errors.PROVIDERS.get(provider, provider)}'s "
                              "per-minute limit, then carrying on.", **run.counters())
                    await asyncio.sleep(p["wait_s"] + 1)
                    continue
                run.problem = p["message"]
                backup_takes_over = role == "AGENT" and run.has_backup and p["kind"] in SWITCHES_TO_BACKUP
                if p["fatal"] and not backup_takes_over:
                    run.stop_reason = run.stop_reason or "error"
                    prefix = "The backup AI failed too: " if role == "AGENT_FALLBACK" else ""
                    run.stop_message = run.stop_message or prefix + p["message"] + llm_errors.advice(run.has_backup)
                log(f"run {run.id}: {role.lower()} error ({p['kind']}): {p['message']}")
                raise
        usage = getattr(out, "usage", None)
        if usage is not None:
            run.tokens += int(getattr(usage, "total_tokens", 0)
                              or (getattr(usage, "prompt_tokens", 0) or 0) + (getattr(usage, "completion_tokens", 0) or 0))
        return out
    object.__setattr__(llm, "ainvoke", counted)


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
    text = el.get("text") or ""
    if text and not any(ch.isalnum() for ch in text):      # "›", ">" arrows: their label says more
        text = attrs.get("aria-label") or attrs.get("title") or text
    label = text or attrs.get("aria-label") or attrs.get("value") or attrs.get("placeholder") or \
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
        "results_reached": "Sub-task done",
        "done": "Finished",
    }.get(name, name.replace("_", " ").capitalize())


class Run:
    def __init__(self, job: dict):
        self.id = job["id"]
        self.job = job
        base = job.get("counters") or {}             # a sub-task continues the run's counters
        self.base_steps = int(base.get("steps") or 0)
        self.ai_calls = int(base.get("ai_calls") or 0)
        self.tokens = int(base.get("tokens") or 0)
        self.steps = self.base_steps
        self.arrived = False
        self.has_backup = False
        self.using_backup = False
        self.problem = ""             # the last AI provider problem, in plain words
        self.stop_reason = ""
        self.stop_message = ""
        self.browser_session = None

    def counters(self) -> dict:
        return {"steps": self.steps, "ai_calls": self.ai_calls, "tokens": self.tokens}


class GuardedTools(Tools):
    """browser-use tools where the app approves every action first and each one is recorded."""

    def __init__(self, run: Run):
        super().__init__(exclude_actions=EXCLUDED)
        self.run = run

        self.widgets = skills.Skills(self, self._check, self._record)

        @self.action("Call this when your sub-task is done and the current page shows what was asked for. "
                     "note: one line about what you did.")
        async def results_reached(note: str, browser_session) -> ActionResult:
            return await self._arrive(note, browser_session)

        @self.action("Type text into a search / destination box (index) and click the matching suggestion in "
                     "the list that drops down. Use this for autocomplete boxes instead of input + click.")
        async def pick_suggestion(index: int, text: str, browser_session) -> ActionResult:
            return self._result(*await self.widgets.pick_suggestion(index, text, browser_session, describe))

        @self.action("Set a date field or date-picker opener (index) to date (YYYY-MM-DD): types into native "
                     "date inputs, otherwise opens the calendar, moves to the month and clicks the day.")
        async def set_date(index: int, date: str, browser_session) -> ActionResult:
            return self._result(*await self.widgets.set_date(index, date, browser_session, describe))

        @self.action("Go to the next page of results by clicking the page's own Next control.")
        async def next_page(browser_session) -> ActionResult:
            return self._result(*await self.widgets.next_page(browser_session))

    @staticmethod
    def _result(message: str, error: str) -> ActionResult:
        if error:
            return ActionResult(error=error)
        return ActionResult(extracted_content=message, long_term_memory=message)

    def _check(self, action: str, el: dict, text: str) -> str:
        """The app's answer for one click or typed value inside a skill: "" when allowed, else the reason."""
        res = post(f"/api/agent/worker/runs/{self.run.id}/check", action=action, element=el, text=text, url="")
        if res.get("ok", False):
            return ""
        return f"Refused by the app: {res.get('reason') or 'not allowed'}. Choose another way."

    async def _record(self, browser_session, action: str, el: dict, params: dict, error: str) -> None:
        url = ""
        try:
            url = await browser_session.get_current_page_url()
        except Exception:
            pass
        post(f"/api/agent/worker/runs/{self.run.id}/step", kind="action", text=sentence(action, params, el), url=url,
             detail={"action": action, "params": params, "target": el, "error": (error or "")[:300]},
             **self.run.counters())

    async def _arrive(self, note: str, browser_session) -> ActionResult:
        page = await browser_session.get_current_page()
        url = await browser_session.get_current_page_url()
        html = await page.evaluate("() => document.documentElement.outerHTML") if page else ""
        res = post(f"/api/agent/worker/runs/{self.run.id}/arrived", url=url, html=html, note=note[:400])
        if res.get("ok"):
            self.run.arrived = True
            return ActionResult(is_done=True, success=True,
                                extracted_content="The app took the page back.")
        return ActionResult(error=f"The app did not accept this page: {res.get('reason', 'unknown')}. Keep going.")

    async def act(self, action, browser_session, *args, **kwargs):
        self.run.browser_session = browser_session
        for name, params in action.model_dump(exclude_unset=True).items():
            if params is None:
                continue
            params = params if isinstance(params, dict) else {}
            # Ending with success without results_reached: hand the page back all the same.
            if name == "done" and params.get("success") and not self.run.arrived:
                return await self._arrive(str(params.get("text", ""))[:400], browser_session)
            el = await describe(browser_session, params["index"]) if params.get("index") is not None else {}
            check = {"navigate": {"url": params.get("url", "")}}.get(name, {})
            if name in ("navigate", "click", "input", "select_dropdown"):
                res = post(f"/api/agent/worker/runs/{self.run.id}/check", action=name, element=el,
                           text=str(params.get("text", "")), url=check.get("url", ""))
                if not res.get("ok", False):
                    return ActionResult(error=f"Refused by the app: {res.get('reason') or 'not allowed'}. "
                                              "Choose another way (only search, filter, sort and read).")
            result = await super().act(action, browser_session, *args, **kwargs)
            if name not in ("done", "results_reached", *SKILLS):      # skills record their own clicks and typing
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


# One browser per job (agent run): its sub-tasks share the window, so a destination typed or a date picked
# in one sub-task is still there in the next. It closes when the app says the job has finished.
BROWSERS: dict[int, BrowserSession] = {}
LOOP = asyncio.new_event_loop()        # one event loop for the worker's life: the browsers belong to it


def same_page(a: str, b: str) -> bool:
    """Same site and path, www. or not, whatever the query (the page's own parameters change as you use it)."""
    pa, pb = urlsplit(a), urlsplit(b)
    host = lambda p: p.netloc.lower().removeprefix("www.")
    return host(pa) == host(pb) and (pa.path.rstrip("/") or "/") == (pb.path.rstrip("/") or "/")


async def browser_for(job: dict) -> tuple[BrowserSession, bool]:
    """(the job's browser, True when it was just opened). A browser closed by hand is opened again."""
    session = BROWSERS.get(job["id"])
    if session is not None:
        try:
            await session.get_current_page_url()
            return session, False
        except Exception:
            await close_browser(job["id"])
    show = os.environ.get("AGENT_SHOW_BROWSER") == "1"
    profile = BrowserProfile(headless=not show, executable_path=os.environ.get("AGENT_BROWSER") or None,
                             allowed_domains=job["allowed_domains"] + BROWSER_START_PAGES, storage_state=job.get("storage_state"),
                             block_ip_addresses=True, keep_alive=True, window_size={"width": 1280, "height": 900})
    session = BrowserSession(browser_profile=profile)
    await session.start()
    BROWSERS[job["id"]] = session
    log(f"run {job['id']}: opened its browser")
    return session, True


async def close_browser(run_id: int) -> None:
    session = BROWSERS.pop(run_id, None)
    if session is None:
        return
    try:
        await session.kill()
    except Exception:
        pass
    log(f"run {run_id}: closed its browser (the job has finished)")


async def execute(job: dict) -> None:
    run = Run(job)
    limits = job["limits"]
    llm = make_llm()
    track(llm, run, "AGENT")
    backup = make_llm("AGENT_FALLBACK") if os.environ.get("AGENT_FALLBACK_PROVIDER") else None
    if backup is not None:
        track(backup, run, "AGENT_FALLBACK")
    run.has_backup = backup is not None

    session, fresh = await browser_for(job)
    run.browser_session = session
    # A later sub-task of the same job carries on in the same window: only navigate when it's for another page.
    here = ""
    if not fresh:
        try:
            here = await session.get_current_page_url()
        except Exception:
            here = ""
    start = [] if here and same_page(here, job["start_url"]) else [{"navigate": {"url": job["start_url"], "new_tab": False}}]
    tools = GuardedTools(run)
    vision = os.environ.get("AGENT_VISION") == "1"

    async def on_step(state, output, n):
        run.steps = run.base_steps + n
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
        task=job["task"], llm=llm, browser_session=session, tools=tools,
        use_vision=vision, use_judge=False, enable_planning=False, calculate_cost=False,
        flash_mode=not vision, use_thinking=vision, max_actions_per_step=3, max_failures=3,
        # fewer tokens per call: only the latest steps in full, and a shorter element list without screenshots
        max_history_items=8, max_clickable_elements_length=9000 if not vision else 30000,
        fallback_llm=backup,
        initial_actions=start or None,
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
            await agent.close()            # the job's browser stays open (keep_alive): closed when the job ends
        except Exception:
            pass
    if run.arrived:
        log(f"run {run.id}: browser sub-task done after {run.steps - run.base_steps} steps, {run.ai_calls} AI calls")
        post(f"/api/agent/worker/runs/{run.id}/step", kind="info",
             text=f"The browser finished its sub-task in {run.steps - run.base_steps} steps, {round(time.time() - started)}s.",
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
        message = f"Used its {limits.get('steps')} steps without finishing the sub-task."
    elif status == "error" and not message:
        message = "The browser could not finish the sub-task."
    post(f"/api/agent/worker/runs/{run.id}/finish", status=status, message=message, **run.counters())
    log(f"run {run.id}: {status}: {message[:120]}")


def main() -> None:
    log(f"agent worker started (python {sys.version.split()[0]}, provider {os.environ.get('AGENT_PROVIDER')})")
    idle_since = time.time()
    try:
        while True:
            try:
                # The app also says which of our open browsers belong to jobs that have finished.
                job = client.get("/api/agent/worker/next", params={"open": ",".join(map(str, BROWSERS))}).json()
            except Exception as e:
                log(f"app unreachable: {e}")
                if time.time() - idle_since > 120:
                    return
                time.sleep(3)
                continue
            for run_id in job.get("close") or []:
                LOOP.run_until_complete(close_browser(int(run_id)))
            if not job.get("id"):
                # Stay while a job's browser is open (it may be waiting for your answer); exit when idle.
                if not BROWSERS and time.time() - idle_since > IDLE_EXIT_S:
                    log("idle, exiting")
                    return
                time.sleep(2)
                continue
            log(f"run {job['id']}: starting on {job['start_url']}")
            try:
                LOOP.run_until_complete(execute(job))
            except Exception as e:
                post(f"/api/agent/worker/runs/{job['id']}/finish", status="error",
                     message=f"The agent crashed: {type(e).__name__}: {str(e)[:300]}")
            idle_since = time.time()
    finally:
        for run_id in list(BROWSERS):
            LOOP.run_until_complete(close_browser(run_id))


if __name__ == "__main__":
    main()
