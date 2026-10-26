"""One AI call per step: the plan, the checklist, the recent steps and the latest observation go in; one
thought and one tool call come out. Uses the agent's AI from Settings, with its backup."""
import time
from dataclasses import dataclass, field

from ... import config
from ...extraction import recipe_builder
from .. import llm_errors, store
from . import prompts

RECENT_FULL = {"groq": 4}             # steps shown in full in the prompt (others as one-line digests)
HISTORY_OBS_CHARS = 600


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
    route = "\n".join(f"  {i}. {s}" for i, s in enumerate(ctx.plan.get("navigation", []), 1))
    return (f"GOAL: {ctx.goal}\nSITE: {ctx.site}   START PAGE: {ctx.start_url}\n"
            f"CHECKLIST:\n" + ("\n".join(checks) or "  (none)") +
            f"\nCOLUMNS WANTED: {cols or 'the important details of each item'}\n"
            f"PAGES TO COLLECT: {ctx.plan.get('pages', 3)} · item pages: {ctx.plan.get('item_pages', 'auto')}\n"
            f"SUGGESTED ROUTE (a hint, not a script):\n{route or '  (none)'}")


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


TOOL_ARGS = {"open_page": ("url", "params"), "find_links": ("url", "pattern"), "search": ("url", "form", "fields"),
             "check_results": ("url",), "next_page": ("url",), "collect": ("url", "pages", "note"),
             "finish": ("outcome", "summary")}


def _parse(raw: dict) -> Step:
    tool = str(raw.get("tool") or "").strip()
    args = {k: raw.get(k) for k in TOOL_ARGS.get(tool, ())}
    args = {k: v for k, v in args.items() if v not in (None, "", 0, [])}
    done = [str(x) for x in (raw.get("checklist_done") or []) if str(x).startswith("must_")]
    return Step(str(raw.get("thought") or "").strip()[:400], tool, args, done)


def decide(ctx) -> Step:
    """Ask the AI for the next step. Provider errors are named plainly; a short rate limit is waited out
    once; a used-up daily limit, no credit or a refused key switches to the backup AI or ends the run."""
    for attempt in range(3):
        try:
            raw, tokens = recipe_builder.ask_structured(prompts.SYSTEM, [{"role": "user", "content": prompt(ctx)}],
                                                       prompts.step_schema(), "agent_step", ctx.provider, ctx.model)
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
            if attempt == 2:
                raise BrainError("error", f"The AI's answers could not be used: {e}")
            continue
        except Exception as e:  # noqa: BLE001 - provider SDK errors differ; classify them
            store.bump(ctx.run_id, ai_calls=1)
            ctx.ai_calls += 1
            text, status = str(getattr(e, "message", "") or e), getattr(e, "status_code", None)
            p = llm_errors.classify(text, status, ctx.provider, ctx.model)
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
            if attempt == 2:
                raise BrainError("error", p["message"])
            time.sleep(2)
    raise BrainError("error", "The AI did not answer.")
