# Plan: an end-to-end ReAct agent (prompt → data)

> **Status: phase 1 built.** One prompt is enough: the AI suggests sites when none is named (you confirm),
> makes a plan with a must / app checklist, and a reason-and-act loop in the app (`app/agent/react/`) finds
> the results page with guarded tools and hands it to the engine. Live: "Mystery books under £20: title,
> price, rating, availability" with no site → suggested Waterstones, Foyles, WHSmith → Waterstones → the
> loop opened the home page, found the Crime category with `find_links`, explored the site's price links,
> was refused once for an invented address, and collected 191 rows (gpt-5-mini, 13 steps, 15 AI calls,
> 43k tokens); check-and-fix then turned item pages on for rating and availability. Phase 2 is next.

## Context

Agent mode used to need a goal **and** a site, and its brain was browser-use: the AI drove a browser step by
step, the app approved each action, and the engine collected when the AI said "results reached". Two
booking.com runs (docs/plans/07-agent-thinking.md) showed the weakness: a fixed script written before seeing
the site, guessed addresses, no check of what each action did, and 5–9k tokens per step.

Now the agent works like Firecrawl's: **one natural-language prompt → the data**, as a **ReAct agent**
(Reason → Act with tools → Observe). Decisions: no site named → the AI suggests 1–3 and you confirm one; a
plan is shown first; the brain runs in the app and browser-use becomes one tool (phase 2); one site per
prompt; today's check-and-fix stays after collecting.

## How it works (phase 1)

```
prompt ─▶ POST /api/agent/plan ─(no site)─▶ suggest_sites ─▶ "Which site?" ─▶ plan (draft) ─▶ you approve
                                                                                               │
   app/agent/react/                                                                            ▼
   loop: Thought → Action(tool) → Observation, saved on the run every step  (status "thinking")
   tools: open_page · find_links · search · check_results · next_page · collect · finish
   brain: one strict-JSON call per step on the agent's AI (Settings → AI models), backup AI on limits
                      │ collect(url)
                      ▼
            handoff._handoff → job → supervisor (check & fix) → done
```

- **Site suggestion** (`planner.suggest_sites`, `site_in_goal`): one AI call on the scrape-building AI;
  candidates pass the network guard and your never-scrape list; no run exists until you pick one.
- **Checklist** (`planner.checklist_for`): `must` items change which results the site shows (place, dates,
  category, search term); `app` items (price, rating, sort, limit) are regenerated from the request and
  applied by the app to the rows. `collect` is refused while a must item is unticked, unless the note says
  why the site can't do it.
- **Page summaries** (`react/summary.py`): plain lines from redacted HTML, no AI: title, what the page says
  (results count, "no results"), lists of items (records ranked above menus), JSON lists, next page, GET
  search forms, links matching the goal, headings. Budget 3,000 chars on Groq, 7,000 otherwise.
- **Guard** (`react/tools.py::allow_url`): only addresses the site showed (start page, links, search results,
  next pages) or a seen address with its own parameters edited; then the usual site / robots / cooldown /
  opt-out checks; form fields go through `guard.check_input`. Refusals are observations and count as steps.
- **Brain** (`react/brain.py`): `recipe_builder.ask_structured` on `config.AGENT_PROVIDER/MODEL`; last 6
  steps in full (4 on Groq), older ones as one-line digests; `llm_errors` names provider problems, waits out
  a short rate limit once, switches to the backup AI, or ends the run (`limit` on a daily limit).
- **Statuses**: `draft → queued → thinking → collecting → fixing → done | needs_you | error`, plus
  `stopped | limit`. `SCRAPER_AGENT_ENGINE=browser` keeps the earlier browser-use path (its tests and
  `/rerecord` still use it) until phase 4.
- **UI**: prompt-only composer with an optional site box; "Which site?" card; the plan's checklist (must
  items editable, app items shown as chips); live checklist ticks on the run; thought / action (tool chip) /
  observation (monospace, "More") in the timeline.

## Next phases

- **Phase 2 — the browser as a tool, questions.** `browse(task, url)` runs a sub-task on the browser worker
  (`agent_browse_tasks`, `/worker/next` claims it first, `/arrived` ends it) and returns the page summary,
  caching the HTML so `collect` uses the agent's page; `ask_user(question)` pauses the run (`question`) until
  `POST /runs/{id}/answer`. Needed for sites whose search lives in JavaScript widgets (booking.com dates).
- **Phase 3 — widget skills, re-plan, site memory.** `pick_suggestion`, `set_date`, `next_page` as guarded
  browser actions; one re-plan when stuck (≤ 2 per run); per-site memory in `sites.settings["agent_memory"]`.
- **Phase 4 — bench and legacy removal.** `scripts/agent_bench.py`; `/rerecord` on the ReAct engine; drop the
  browser engine switch.

## Lessons from the first live runs

- gpt-5-mini still tries to apply price filters on the site although they are marked app-side; the prompt
  now says so in capitals on each app item. A text-only Groq model would need the shorter budget.
- A shop's "0 items" basket counter looked like "no results"; counters next to basket / checkout words are
  ignored now. Navigation menus repeat like records; lists with prices or numbers rank above lists inside
  nav / menu / header containers.
- The planner put "price ≤ £20" among the musts; must items that name price, rating, sort or a count are
  dropped when the request has such conditions.
