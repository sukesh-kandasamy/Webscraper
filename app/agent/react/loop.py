"""The reason-and-act loop for one run: think (brain) → act (tools) → observe, until the results page is
handed over, the agent gives up, or a limit is reached. Everything the loop knows is saved on the run
(scratchpad, checklist, counters), so the UI shows it live."""
import time

from ... import config
from ...fetching import fetcher
from ...security import audit, logsafe
from .. import guard, store
from . import brain, memory, tools

OBS_STEP_CHARS = 500            # shown in the timeline; the full text sits in the step's detail
OBS_DETAIL_CHARS = 4000
OBS_HISTORY_CHARS = 1500        # kept in the scratchpad for the prompt (the latest observation stays whole)


class Context:
    def __init__(self, run: dict, app_url: str = ""):
        self.run_id = run["id"]
        self.app_url = app_url
        self.goal, self.site, self.start_url = run["goal"], run["site"], run["start_url"]
        self.plan = run["plan"]
        self.request = self.plan.get("request", {})
        self.limits = run["limits"] or {}
        self.checklist = run.get("checklist") or self.plan.get("checklist") or []
        self.pad = run.get("scratchpad") or {}
        self.pad.setdefault("history", [])
        self.pad.setdefault("facts", {})
        self.pad.setdefault("digest", "")
        self.facts = self.pad["facts"]
        self.pages: dict = {}            # url -> fetcher.Page (this process only)
        self.summaries: dict = {}        # url -> facts of its summary
        self.started_at = run.get("started_at") or time.time()
        self.steps = int(run.get("steps") or 0)
        self.ai_calls = int(run.get("ai_calls") or 0)
        self.step_no = self.steps
        self.budget = config.agent_prompt_budget()
        self.provider, self.model, self.using_backup = config.AGENT_PROVIDER, config.AGENT_MODEL, False
        self.memory = memory.recall(self.start_url)
        self.pad.setdefault("replans", [])
        seen = self.facts.setdefault("seen_urls", [])
        for e in self.memory:                # a results page the site showed in an earlier run may be opened again
            u = e.get("results_url", "")
            if u and u not in seen and guard.on_site(u, self.site):
                seen.append(u)

    def musts(self) -> list[str]:
        return [c["text"] for c in self.checklist if c["kind"] == "must"]

    def column_names(self) -> list[str]:
        return [c["name"] for c in self.request.get("columns", []) if not c.get("auto_added")]

    def left(self) -> dict:
        mins = float(self.limits.get("minutes", config.AGENT_MAX_MINUTES))
        return {"steps": max(0, int(self.limits.get("steps", config.AGENT_MAX_STEPS)) - self.steps),
                "ai_calls": max(0, int(self.limits.get("ai_calls", config.AGENT_MAX_AI_CALLS)) - self.ai_calls),
                "minutes": max(0, round(mins - (time.time() - self.started_at) / 60, 1))}

    def exceeded(self) -> tuple[str, str] | None:
        run = store.get(self.run_id)
        if not run or run["stop_requested"]:
            return "stopped", "Stopped by you."
        if run["status"] != "thinking":
            return "stopped", "The run was changed from outside."
        self.ai_calls = int(run["ai_calls"] or 0)
        left = self.left()
        if left["steps"] <= 0:
            return "limit", f"Reached the limit of {self.limits.get('steps')} steps before collecting."
        if left["ai_calls"] <= 0:
            return "limit", f"Reached the limit of {self.limits.get('ai_calls')} AI calls before collecting."
        if left["minutes"] <= 0:
            return "limit", f"Reached the time limit of {self.limits.get('minutes')} minutes before collecting."
        return None

    def save(self) -> None:
        store.update(self.run_id, scratchpad=self.pad, checklist=self.checklist)

    def save_checklist(self) -> None:
        store.update(self.run_id, checklist=self.checklist)

    def shrink_latest(self) -> None:
        if self.pad["history"]:
            self.pad["history"][-1]["obs"] = self.pad["history"][-1]["obs"][: self.budget]

    def tick(self, ids: list[str], evidence: str) -> list[str]:
        ticked = []
        for c in self.checklist:
            if c["id"] in ids and not c.get("done"):
                c["done"], c["evidence"] = True, evidence[:160]
                ticked.append(c["text"])
        return ticked


def _arg_text(tool: str, args: dict) -> str:
    bits = []
    for k in ("url", "pattern", "form", "pages", "outcome", "task", "question"):
        if args.get(k) not in (None, "", 0):
            bits.append(f"{k}={args[k]}" if k != "url" else str(args[k]))
    for k in ("params", "fields"):
        if args.get(k):
            bits.append(k + "=" + ",".join(f"{p.get('name')}={p.get('value')}" for p in args[k]))
    if args.get("note"):
        bits.append(f"note: {args['note'][:120]}")
    if args.get("summary"):
        bits.append(args["summary"][:160])
    return " ".join(bits)[:300]


def _end(ctx: Context, status: str, message: str) -> None:
    message = logsafe.redact(message)
    store.update(ctx.run_id, status=status, message=message, summary=message, finished_at=time.time())
    store.add_step(ctx.run_id, "info" if status == "stopped" else "warn", message)
    if status == "needs_you":
        fetcher.update_settings(fetcher.domain_of(ctx.start_url), {"last_challenge": time.time()})
    audit.log("agent_finished", run_id=ctx.run_id, status=status, engine="react")


def take_answer(run_id: int, answer: str) -> bool:
    """Record the person's answer and move the run from question back to thinking (done at once, in the
    request, so the page sees the new status; the loop itself then continues on a thread)."""
    if not store.transition(run_id, "question", "thinking", answer=answer, message="Thinking"):
        return False
    ctx = Context(store.get(run_id))
    store.add_step(run_id, "info", f"You answered: {answer}")
    text = f"The person answered: {answer}"
    if ctx.pad["history"]:
        ctx.pad["history"][-1]["obs"] = (ctx.pad["history"][-1]["obs"] + "\n" + text)[:OBS_HISTORY_CHARS]
    store.add_step(run_id, "observation", text, detail={"ok": True, "full": text})
    ctx.save()
    return True


def run(run_id: int, app_url: str = "", resumed: bool = False) -> None:
    """The loop for a queued run, or (resumed) for one that just got its answer."""
    run_row = store.get(run_id)
    if not run_row:
        return
    if resumed:
        if run_row["status"] != "thinking":
            return
        ctx = Context(run_row, app_url)
    else:
        if not store.transition(run_id, "queued", "thinking", started_at=run_row.get("started_at") or time.time(),
                                message="Thinking"):
            return
        ctx = Context(store.get(run_id), app_url)
        store.add_step(run_id, "info", f"Started with {ctx.provider} · {ctx.model} (reason, act, observe).")
        if ctx.memory:
            n = len(ctx.memory)
            store.add_step(run_id, "info", f"Remembers what worked on this site in {n} earlier run{'s' if n > 1 else ''}; "
                                           "the agent gets it as a hint.", detail={"memory": ctx.memory})
    while True:
        over = ctx.exceeded()
        if over:
            return _end(ctx, *over)
        why = brain.stuck(ctx)
        if why:
            store.update(run_id, message="Re-planning")
            r = brain.replan(ctx, why)
            ctx.save()
            if r["route"]:
                store.add_step(run_id, "info", f"Re-planned because {why}: {r['diagnosis'] or 'a different approach'}",
                               detail={"replan": r})
            else:
                store.add_step(run_id, "warn", f"Looked stuck ({why}) but couldn't make a new route; carrying on.")
            store.update(run_id, message="Thinking")
            continue                          # the limits are checked again: the re-plan used an AI call
        try:
            step = brain.decide(ctx)
        except brain.BrainError as e:
            return _end(ctx, e.status, str(e))
        ctx.step_no = ctx.steps + 1
        ticked = ctx.tick(step.checklist_done, step.thought)
        store.add_step(run_id, "thought", step.thought or "(no thought)", detail={"checklist_done": step.checklist_done})
        if ticked:
            store.add_step(run_id, "info", "Checklist: " + "; ".join(ticked))
        arg = _arg_text(step.tool, step.args)
        store.add_step(run_id, "action", f"{step.tool} {arg}".strip(), url=str(step.args.get("url") or ""),
                       detail={"tool": step.tool, "args": step.args})
        obs = tools.run_tool(step.tool, step.args, ctx)
        text = logsafe.redact(obs.text)
        ctx.steps += 1
        store.bump(run_id, steps=1)
        ctx.pad["history"].append({"n": ctx.step_no, "thought": step.thought, "tool": step.tool, "arg": arg,
                                   "obs": text[:OBS_HISTORY_CHARS] if not obs.terminal else text[:200], "ok": obs.ok})
        for h in ctx.pad["history"][:-1]:
            h["obs"] = h["obs"][:brain.HISTORY_OBS_CHARS]
        if not obs.terminal:
            store.add_step(run_id, "observation", text[:OBS_STEP_CHARS], url=str(obs.data.get("url") or ""),
                           detail={"ok": obs.ok, "full": text[:OBS_DETAIL_CHARS]})
        ctx.save()
        if obs.end:
            return _end(ctx, *obs.end)
        if obs.terminal:
            return


def safe_run(run_id: int, app_url: str = "", resumed: bool = False) -> None:
    try:
        run(run_id, app_url, resumed)
    except Exception as e:  # noqa: BLE001 - anything else ends the run with a readable message
        run_row = store.get(run_id)
        if run_row and run_row["status"] in ("thinking", "browsing", "queued"):
            msg = logsafe.redact(f"The agent failed: {type(e).__name__}: {str(e)[:300]}")
            store.update(run_id, status="error", message=msg, summary=msg, finished_at=time.time())
            store.add_step(run_id, "warn", msg)
