"""One AI call per step: the plan, the checklist, the recent steps and the latest observation go in; one
thought and one tool call come out. Uses the agent's AI from Settings, with its backup."""
import time
from dataclasses import dataclass, field

from ... import config
from ...extraction import recipe_builder
from .. import llm_errors, store
from . import memory, prompts

RECENT_FULL = {"groq": 4}             # steps shown in full in the prompt (others as one-line digests)
HISTORY_OBS_CHARS = 600
MAX_REPLANS = 2
AI_TIMEOUT_S = 75                     # one step is a short answer; a hung request is retried sooner
STUCK_FAILS = 3                       # this many failed / refused / repeated actions in a row = stuck


class BrainError(Exception):
    """The AI can't be used any more in this run: (status, message)."""

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Step:
    thought: str
    tool: str
    args: dict
    checklist_done: list = field(default_factory=list)


def contract(ctx) -> str:
    cols = ", ".join(f"{c['name']} ({c['type']})" if c.get("type") else c["name"]
                     for c in ctx.request.get("columns", []) if not c.get("auto_added"))
    checks = []
    for c in ctx.checklist:
        if c["kind"] == "must":
            checks.append(f"  [{'x' if c.get('done') else ' '}] {c['id']}: {c['text']}")
        else:
            checks.append(f"  (app) {c['text']}  — the app applies this to the rows after collecting; NOT your job on the site")
    replans = [r for r in ctx.pad.get("replans") or [] if r.get("route")]
    if replans:
        r = replans[-1]
        head, steps = f"YOUR NEW ROUTE (re-planned at step {r['at']}: {r['diagnosis'][:200]}):", r["route"]
    else:
        head, steps = "SUGGESTED ROUTE (a hint, not a script):", ctx.plan.get("navigation", [])
    route = "\n".join(f"  {i}. {s}" for i, s in enumerate(steps, 1))
    out = (f"GOAL: {ctx.goal}\nSITE: {ctx.site}   START PAGE: {ctx.start_url}\n"
           f"CHECKLIST:\n" + ("\n".join(checks) or "  (none)") +
           f"\nCOLUMNS WANTED: {cols or 'the important details of each item'}\n"
           f"PAGES TO COLLECT: {ctx.plan.get('pages', 3)} · item pages: {ctx.plan.get('item_pages', 'auto')}\n"
           f"{head}\n{route or '  (none)'}")
    if getattr(ctx, "memory", None):
        out += ("\nWHAT WORKED ON THIS SITE BEFORE (earlier runs; a strong hint, but check the page, sites change; "
                "its results page may be opened directly):\n" + memory.prompt_lines(ctx.memory))
    return out


def history_text(ctx) -> str:
    hist = ctx.pad["history"]
    full = RECENT_FULL.get(ctx.provider, 6)
    older, recent = hist[:-full] if len(hist) > full else [], hist[-full:]
    lines = []
    if ctx.pad.get("digest"):
        lines.append("EARLIER (digest): " + ctx.pad["digest"])
    for h in older:
        lines.append(f"step {h['n']}: {h['tool']} {h.get('arg', '')} → {h['obs'][:90].replace(chr(10), ' ')}")
    for h in recent[:-1] if recent else []:
        lines.append(f"step {h['n']} · thought: {h['thought'][:200]}\n  action: {h['tool']} {h.get('arg', '')}\n"
                     f"  observation: {h['obs'][:HISTORY_OBS_CHARS]}")
    if recent:
        h = recent[-1]
        lines.append(f"step {h['n']} · thought: {h['thought'][:300]}\n  action: {h['tool']} {h.get('arg', '')}\n"
                     f"  LATEST OBSERVATION:\n{h['obs']}")
    return "\n".join(lines) or "No steps yet. Start by opening the start page."


def prompt(ctx) -> str:
    left = ctx.left()
    return (contract(ctx) + "\n\nLIMITS LEFT: " + f"{left['steps']} steps, {left['ai_calls']} AI calls, {left['minutes']} min"
            + "\n\n" + history_text(ctx) + "\n\nWhat do you do next? Answer with the JSON.")


def stuck(ctx) -> str:
    """Why the agent looks stuck, or "" when it doesn't: its last actions all failed, were refused or
    repeated, or it used most of its steps without collecting. At most MAX_REPLANS re-plans per run."""
    replans = ctx.pad.get("replans") or []
    if len(replans) >= MAX_REPLANS:
        return ""
    since = replans[-1]["at"] if replans else 0
    recent = [h for h in ctx.pad["history"] if h["n"] > since]
    last = recent[-STUCK_FAILS:]
    if len(last) == STUCK_FAILS and not any(h.get("ok") for h in last):
        if sum(h["obs"].startswith("You already did") for h in last) >= 2:
            return f"the last {STUCK_FAILS} actions repeated earlier ones"
        if sum(h["obs"].startswith("Refused") for h in last) >= 2:
            return f"the last {STUCK_FAILS} actions were refused"
        return f"the last {STUCK_FAILS} actions failed or found nothing"
    limit = int(ctx.limits.get("steps", config.AGENT_MAX_STEPS))
    if not replans and limit >= 8 and ctx.steps >= int(limit * 0.6):
        return f"{ctx.steps} of {limit} steps are used and nothing is collected yet"
    return ""


def replan(ctx, why: str) -> dict:
    """One AI call: why isn't it working, and a new short route. Saved in the scratchpad; the prompt shows
    it in place of the plan's route. A failed call still counts, so it isn't retried at once."""
    left = ctx.left()
    text = (contract(ctx) + f"\n\nLIMITS LEFT: {left['steps']} steps, {left['ai_calls']} AI calls\n\n" +
            history_text(ctx) + f"\n\nYOU LOOK STUCK: {why}. Say in one line why the current approach isn't "
            "working, then write a NEW short route (at most 5 steps, with the tools you have) that tries "
            "something different. Answer with the JSON.")
    entry = {"at": ctx.steps, "why": why, "diagnosis": "", "route": []}
    try:
        raw, tokens = recipe_builder.ask_structured(prompts.REPLAN_SYSTEM, [{"role": "user", "content": text}],
                                                   prompts.replan_schema(), "agent_replan", ctx.provider, ctx.model,
                                                   timeout=AI_TIMEOUT_S)
        store.bump(ctx.run_id, ai_calls=1, tokens=tokens)
        entry["diagnosis"] = str(raw.get("diagnosis") or "").strip()[:300]
        entry["route"] = [str(s).strip()[:200] for s in raw.get("route") or [] if str(s).strip()][:5]
    except Exception:  # noqa: BLE001 - provider problems surface on the next decide(), which handles them
        store.bump(ctx.run_id, ai_calls=1)
    ctx.ai_calls += 1
    ctx.pad.setdefault("replans", []).append(entry)
    return entry


TOOL_ARGS = {"open_page": ("url", "params"), "find_links": ("url", "pattern"), "search": ("url", "form", "fields"),
             "check_results": ("url",), "next_page": ("url",), "collect": ("url", "pages", "note"),
             "browse": ("task", "url"), "ask_user": ("question",), "finish": ("outcome", "summary")}


def _parse(raw: dict) -> Step:
    tool = str(raw.get("tool") or "").strip()
    args = {k: raw.get(k) for k in TOOL_ARGS.get(tool, ())}
    args = {k: v for k, v in args.items() if v not in (None, "", 0, [])}
    done = [str(x) for x in (raw.get("checklist_done") or []) if str(x).startswith("must_")]
    return Step(str(raw.get("thought") or "").strip()[:400], tool, args, done)


def _no_answer(e: Exception) -> bool:
    """A timeout or a dropped connection (httpx / SDK class names), as opposed to an error answer."""
    return any(w in type(e).__name__ for w in ("Timeout", "Connect", "Network", "RemoteProtocol"))


def decide(ctx) -> Step:
    """Ask the AI for the next step. Provider errors are named plainly; a short rate limit is waited out
    once; a used-up daily limit, no credit or a refused key switches to the backup AI or ends the run."""
    for attempt in range(4):              # the 4th try is only reached after switching to the backup AI
        try:
            raw, tokens = recipe_builder.ask_structured(prompts.SYSTEM, [{"role": "user", "content": prompt(ctx)}],
                                                       prompts.step_schema(), "agent_step", ctx.provider, ctx.model,
                                                       timeout=AI_TIMEOUT_S)
            store.bump(ctx.run_id, ai_calls=1, tokens=tokens)
            ctx.ai_calls += 1
            return _parse(raw)
        except recipe_builder.PromptTooLarge:
            store.bump(ctx.run_id, ai_calls=1)
            ctx.ai_calls += 1
            if ctx.budget <= 1200:
                raise BrainError("error", "The page summaries are too large for this AI model's limit.")
            ctx.budget //= 2
            ctx.shrink_latest()
            continue
        except recipe_builder.RecipeBuildError as e:
            store.bump(ctx.run_id, ai_calls=1)
            ctx.ai_calls += 1
            if attempt >= 2:
                raise BrainError("error", f"The AI's answers could not be used: {e}")
            continue
        except Exception as e:  # noqa: BLE001 - provider SDK errors differ; classify them
            store.bump(ctx.run_id, ai_calls=1)
            ctx.ai_calls += 1
            text, status = str(getattr(e, "message", "") or e), getattr(e, "status_code", None)
            p = llm_errors.classify(text, status, ctx.provider, ctx.model)
            if not p and _no_answer(e):       # timed out or unreachable after the request's own retry
                name = llm_errors.PROVIDERS.get(ctx.provider, ctx.provider)
                p = {"kind": "server", "fatal": True, "wait_s": 0,
                     "message": f"{name} did not answer in time ({type(e).__name__}), twice."}
            if not p:
                raise BrainError("error", f"The AI request failed: {type(e).__name__}: {text[:200]}")
            if p["kind"] == "rate_limit" and not p["fatal"] and p["wait_s"] and attempt < 2:
                time.sleep(min(p["wait_s"], llm_errors.MAX_WAIT_S) + 1)
                continue
            backup = config.agent_fallback()
            if backup and not ctx.using_backup and p["kind"] in ("daily_limit", "quota", "auth", "rate_limit", "server"):
                ctx.provider, ctx.model, ctx.using_backup = backup["provider"], backup["model"], True
                store.add_step(ctx.run_id, "warn", f"{p['message']} Switched to the backup AI: "
                               f"{llm_errors.PROVIDERS.get(backup['provider'], backup['provider'])} · {backup['model']}.")
                continue
            if p["fatal"]:
                prefix = "The backup AI failed too: " if ctx.using_backup else ""
                raise BrainError("limit" if p["kind"] == "daily_limit" else "error",
                                 prefix + p["message"] + llm_errors.advice(bool(backup)))
            if attempt >= 2:
                raise BrainError("error", p["message"])
            time.sleep(2)
    raise BrainError("error", "The AI did not answer.")
