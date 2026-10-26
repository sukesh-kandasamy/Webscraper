# Plan: an end-to-end ReAct agent (prompt → data)

> **Status: phase 1 built.** One prompt is enough: the AI suggests sites when none is named (you confirm),
> makes a plan with a must / app checklist, and a reason-and-act loop in the app (`app/agent/react/`) finds
> the results page with guarded tools and hands it to the engine. Live: "Mystery books under £20: title,
> price, rating, availability" with no site → suggested Waterstones, Foyles, WHSmith → Waterstones → the
> loop opened the home page, found the Crime category with `find_links`, explored the site's price links,
> was refused once for an invented address, and collected 191 rows (gpt-5-mini, 13 steps, 15 AI calls,
> 43k tokens); check-and-fix then turned item pages on for rating and availability.
> **Phase 2 built:** `browse(task, url)` sends one sub-task to the browser worker (browser-use) and reads
> the page it ends on; `ask_user(question)` pauses the run (status `question`) until you answer in the
> workspace, then the loop carries on from its scratchpad. Live: "Oscar-winning films of 2015 from
> scrapethissite.com/pages/ajax-javascript/" → the plain page had no films, the loop sent `browse` ("select
> year 2015"), the browser clicked 2015 in 3 steps, `check_results` read the rendered page (16 `tr.film`
> rows) and `collect` saved 16 rows (gpt-5-mini, 7 steps, 8 AI calls, 42k tokens, 183 s).
> **Phase 3 built:** widget skills in the browser worker (`pick_suggestion`, `set_date`, `next_page`), a
> re-plan when the loop is stuck (at most 2 per run) and site memory. Live, "Oscar-winning films of 2014"
> on the same page twice: run 1 wasted a browse on a search box the page doesn't have, re-planned at 12 of
> 15 steps and collected 16 rows (14 steps, 16 AI calls, 107k tokens); run 2 started from the memory of
> run 1 and collected the same 16 rows in 5 steps, 7 AI calls, 33k tokens. Phase 4 is next.

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

## Phase 2: the browser as a tool, questions (built)

- **`browse(task, url)`** (`react/tools.py`): the loop moves the run to `browsing`, queues a row in
  `agent_browse_tasks` (task text from `planner.browse_task_text`, the run's limits capped at 15 steps and
  6 minutes, the run's counters as a base) and starts the worker. `GET /worker/next` hands browse tasks out
  before whole runs (`kind: "browse"`); the worker's clicks and typing go through `/check` and `/step` as
  before, so they show on the timeline with screenshots and the run's counters carry on. `results_reached`
  or a successful `done` posts `/arrived`: on a browsing run that finishes the task with the page's HTML
  (kept in-process), and the tool answers the AI with that page's summary, caching it so `collect` on that
  address uses the browser's page (the hand-over's route replay then works as before). A failed sub-task
  (`/finish`) or a timeout (the tool marks the task `stop_requested`; `/step` tells the worker to stop) is an
  observation, not the end of the run; a human check ends it as *Needs you*.
- **`ask_user(question)`**: at most twice per run; the run becomes `question` (polling stops, the card shows
  the question with an answer box and Stop). `POST /runs/{id}/answer` records the answer at once (status back
  to `thinking`, "You answered: …" on the timeline, the answer appended to the last observation) and the loop
  resumes on a thread from the saved scratchpad. A question survives an app restart; a run that was
  thinking or browsing at a restart is marked as failed, as before.

## Phase 3: widget skills, re-plan, site memory (built)

- **Widget skills** (`agent_worker/skills.py`, registered as browser-use actions in `GuardedTools`):
  `pick_suggestion(index, text)` types through browser-use's own input action, waits for the dropdown
  (`[role=option]`, listbox / autocomplete / typeahead lists), and clicks the best match by text (exact,
  starts with, contains; otherwise it names the options it saw). `set_date(index, YYYY-MM-DD)` types into
  native date inputs, otherwise opens the picker, clicks the month arrow up to 14 times and clicks the day
  found by `data-date`-style attributes or an aria-label / title with day, month and year. `next_page()`
  clicks `a[rel=next]` or a visible Next / › / » control. Every click and typed value goes through
  `/check` first and is recorded as a plain `input` / `click` step with its target (attributes, text,
  xpath), so the hand-over's route replay repeats it without the AI. Checked in a real Edge against a
  local page with an autocomplete box, a calendar, a native date field and JS paging.
- **Re-plan** (`brain.stuck` / `brain.replan`, called at the top of each loop turn): stuck = the last 3
  actions all failed, were refused or were repeats, or 60% of the step limit is used with no re-plan yet.
  One AI call (`REPLAN_SYSTEM`, schema `{diagnosis, route[]}`) on the agent's AI; the new route replaces the
  plan's in the prompt ("YOUR NEW ROUTE (re-planned at step N: …)") and shows on the timeline as a numbered
  list. At most 2 per run; a failed call still counts, so it isn't retried at once.
- **Site memory** (`react/memory.py`, in `sites.settings["agent_memory"]`, newest 3 per site, the same goal
  replaces its entry): written by `supervisor.finish` when a ReAct run ends done: the goal, the successful
  steps (`open_page`, `search`, `browse`, `next_page`, `collect` with their arguments, redacted), the results
  address (no fragment), the list selector and whether the browser was needed. A new run on the site gets it
  in the prompt ("WHAT WORKED ON THIS SITE BEFORE … check the page") and the remembered results address
  counts as seen, so it can be opened directly (guarded as usual). `GET / DELETE /api/agent/memory?site=`;
  the draft plan card shows the memory with **Forget**.
- Brain AI calls now time out after 75 s (`ask_structured(timeout=)`), so a hung request is retried sooner.

## Next phases

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
- The browser's page was cached under its address with `#2015`, and the tools looked it up without the
  fragment, so `check_results` fetched the plain page again and saw no films; cached pages are keyed without
  the fragment now.
- One OpenAI request timed out twice (the request's own retry) and the run ended with a bare "ReadTimeout";
  a timeout or dropped connection now switches to the backup AI, or ends with "… did not answer in time".
- The planner repeated app items as musts ("awards: >0" beside the app's "awards > 0"); a must that matches
  an app item, ignoring spaces and colons, is dropped.
- Phase 3 run 1: the planner made "awards contains Oscar" a must on a page that only lists Oscar winners,
  and the agent spent a 9-step browse looking for a search box to apply it; the re-plan at 60% of the steps
  got it to collect. The planner prompt now says not to restate what the site already is.
- An OpenAI request hung for 180 s before its retry answered; brain calls wait 75 s now.
