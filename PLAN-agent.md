# Plan: Agent mode (end-to-end automation with browser-use)

> **Status: phase 1 built** (Agent screen, plan card, worker in `.venv-agent`, guard, live timeline with
> screenshots, handoff to the engine, Open to solve for human checks). Tested live on books.toscrape.com
> (category), quotes.toscrape.com (tag) and scrapethissite.com (search form, 4 pages); We Work Remotely
> showed a Cloudflare check and the run stopped as *Needs you*, as designed.
> **Phase 2 built:** routes are recorded from the agent's actions, checked by a replay before saving, replayed
> by Playwright with no AI on every run (in-page filters, pagination inside the same browser), skip pop-ups
> that no longer appear, stop with "route broken" at the failing step, and can be re-recorded into the same
> saved scrape. Live: the scrapethissite AJAX page ("2012" films, address unchanged) replays in ~20 s with
> 0 AI calls. Phases 3–4 are next.

## Context

Today the app has one way to scrape (**Guided mode**): you paste a listing page, describe what you need,
check the columns, and run. It works when you already know the exact page.

**Agent mode** is a new, separate mode for when you only know the *goal*:

> "3BHK flats for rent in Whitefield, Bangalore under ₹40,000 on MagicBricks: price, area, furnishing,
> owner or agent, posted date. All pages."

An AI agent (browser-use) opens the site, gets past the cookie banner, uses the site's own search box and
filter menus, reaches the results list, and hands it to the existing engine, which collects the data
(pages, item pages, typed values, filters, quality report). The agent then checks the quality report,
fixes what it can, and delivers the result. You approve its plan once and step in only for logins and
human checks.

**Guided mode stays exactly as it is.** Agent mode reuses its engine and adds a new screen, a separate
background worker and a few new modules. Agent runs end as normal saved scrapes and results.

---

## 1. Two modes, one app

| | Guided mode (today, unchanged) | Agent mode (new) |
|---|---|---|
| You give | A listing page + what you need | The goal, the site, and limits |
| Finding the page | You | The agent: search box, filter menus, dropdowns, "load more" |
| Collecting | Recipe engine | The same recipe engine (the agent only reads data itself as a last resort) |
| Checking quality | You read the Quality tab | The agent reads it and fixes problems (more pages, item pages, re-detect) |
| Re-running | Run again | Run again: replays the recorded route with **no AI** |
| AI calls | 1–3 per setup | About 20–40 for the first run, ~0 for later runs |

- Sidebar: a new **Agent** item under New scrape, with a "New" badge. Home gets a second start card:
  "Let the agent do it".
- A saved scrape remembers which mode created it. Agent scrapes show an "Agent" chip and a "Route"
  section (the recorded steps). They are edited in the Agent screen.
- Settings → **Agent**: the AI model for the agent, default limits, and an on/off switch for the whole mode.

## 2. What you give the agent (the only inputs)

| Input | Required | Example |
|---|---|---|
| Goal, in your own words | yes | "3BHK flats for rent in Whitefield under ₹40,000; price, area, furnishing, owner/agent, posted date" |
| Site or start link | yes (phase 1) | `magicbricks.com` or a link |
| How much | no (defaults) | "all pages" / "first 200 items" |
| Limits | no (defaults) | max 40 agent steps, 10 minutes, 60 AI calls |
| Login | only if needed | uses the existing own-account login window; the agent never sees passwords |

## 3. How a run works

```
 Goal + site
     │
 1 PLAN ── AI turns the goal into a task plan ──► you approve or edit (one card)
     │
 2 NAVIGATE ── browser-use in a visible or headless browser, allowed actions only ──► results list
     │           records every step as a ROUTE            ▲
     │                                                    └── "needs you": login / human check → you solve → continue
 3 COLLECT ── existing recipe builder on the rendered results page → preview → job
     │           (pages, item pages, types, filters, sort, dedupe, retries)
 4 CHECK & FIX ── agent reads the quality report; up to 2 fix rounds within limits
     │
 5 DELIVER ── saved scrape + results + a short summary
```

### Step 1 · Plan
`app/agent/planner.py` extends `request_parser` into a **task plan**:
```json
{"site": "magicbricks.com",
 "navigation": ["open the rent section", "search locality 'Whitefield, Bangalore'",
                "filter: 3 BHK", "filter: budget up to 40,000", "sort: newest"],
 "request": {"columns": [...], "filters": [...], "sort": {...}, "limit": 0},
 "pages": "all", "item_pages": "if needed"}
```
Shown as a card (navigation steps, columns as chips, conditions, limits) that you edit and approve.
Conditions the site's own filter menus can apply are given to the agent. The rest stay as row filters in
the engine, so nothing is lost if the site has no such filter.

### Step 2 · Navigate (browser-use)
- Runs in the **agent worker** (section 4) with the browser-use `Agent`, our task text, and a **custom
  controller** that allows only: open a page (within the plan's site), click, type into search / filter
  inputs, choose from dropdowns and date pickers, scroll, go back, wait, and "done: this is the results
  list".
- The browser is your installed **Edge or Chrome**, as today (your company policy blocks Playwright's own
  bundled browser in a visible window). It starts headless and switches to visible when you press
  **Watch** or **Take over**, or when it needs you.
- Every step is saved as a route step: action, target (stable selector plus visible text plus role),
  value, the resulting URL, and a small screenshot for the timeline.
- Stops when the agent declares the results list, or at the step / time / AI-call limit. At the limit it
  reports what it reached and asks you.

### Step 3 · Collect (existing engine)
- The rendered results page goes to `recipe_builder.build_recipe` with the plan's request (meanings,
  types). The preview appears in the timeline.
- **If the final URL already holds the filters** (most sites: `?bedrooms=3&budget=...`), the scrape is
  just that URL, the same as a Guided scrape.
- **If the filters only exist in the page** (set by clicks, URL unchanged), the scrape keeps the
  **route**. Each run replays it with plain Playwright (no AI) before collecting. New `fetch_route` in
  `browser.py`, used by `fetcher.fetch_smart` when the recipe has a route.
- **Last resort:** if no recipe works (two failed attempts), the agent extracts the visible items itself
  with browser-use's extraction, page by page, capped at 5 pages. This is marked "AI-read (less
  reliable)" in the Quality tab.

### Step 4 · Check & fix
`app/agent/supervisor.py` reads the job's quality report and picks at most two fixes per run:

| Quality signal | Fix |
|---|---|
| coverage < 90% and the plan said "all pages" | raise pages (up to 50) and continue the run |
| a requested column is empty or missing | turn on item pages (`/api/details` logic) |
| columns that used to fill are now empty | re-detect columns on the current page |
| pagination stopped early | ask the agent to find the next-page control, update the recipe |
| replay failed (site changed) | re-navigate with the agent, then re-record the route |

Each fix is a line in the timeline with its reason.

### Step 5 · Deliver
Saved as a normal saved scrape (mode = agent, plan, route, recipe) with its results run. A summary card
reads, for example: "Collected 412 flats from 21 pages, 96% coverage; salary came from item pages; 2
pages failed and were retried."

## 4. Architecture

```
FastAPI app (existing venv)                      Agent worker (separate venv: .venv-agent)
 ├─ /api/agent/*  ── creates agent_runs ──►  DB ◄── polls runs, runs browser-use, writes steps
 ├─ recipe builder, jobs, quality (reused)        │   calls back into the app's API for
 └─ Agent screen (polls steps)                    │   "build recipe", "start job", "quality"
```

- **Separate worker process and virtual environment** (`.venv-agent`, `agent_worker.py`), because
  browser-use 0.13 would downgrade `groq` (1.7 → 1.0) and bring about 70 packages, including telemetry
  (`posthog`) and its cloud SDK. Separate also means a crashing agent can't take the app down.
  `run.py` starts the worker when Agent mode is on.
- The worker reads its tasks from the database (new `agent_runs` and `agent_steps` tables) and uses the
  app's own localhost API for everything else. One source of truth for robots.txt, budgets, sessions,
  masking and the audit log.
- Telemetry off (`ANONYMIZED_TELEMETRY=false`, `BROWSER_USE_CLOUD_SYNC=false`); none of the cloud,
  stealth or proxy features are used. Versions pinned in `requirements-agent.lock`.

### New files
| File | What it does |
|---|---|
| `app/agent/planner.py` | goal → task plan (AI, structured output); validation |
| `app/agent/routes.py` | route steps: model, record, replay with Playwright, "does this still work" check |
| `app/agent/supervisor.py` | quality report → fix decisions |
| `app/agent/api.py` | `/api/agent/plan`, `/api/agent/runs` (start, get, steps, pause, stop, take over, resume, answer) |
| `agent_worker.py` | the browser-use process: custom controller, guards, step recording, callbacks |
| `app/agent/guard.py` | checks each navigation and action before it happens (section 5) |
| `requirements-agent.txt` / `.lock` | browser-use and its dependencies, pinned |

### Changed files
- `db.py`: `agent_runs` (goal, plan, status, limits, counters, summary), `agent_steps` (run, n, action,
  target, value, url, screenshot path, note), `scrapes.mode`, `scrapes.route`.
- `browser.py` / `fetcher.py`: `fetch_route(route)`, plus the route option in `fetch_smart`.
- `jobs.py`: a recipe with a route replays it before page 1. Pagination continues as today.
- `main.py`: mount the agent API; the status endpoint reports whether the worker is running.
- `static/`: Agent screen, sidebar item, Home card, Settings → Agent, "Agent" chip on saved scrapes.
- `config.py`: `SCRAPER_AGENT_PROVIDER`, `SCRAPER_AGENT_MODEL`, the default limits, and `SCRAPER_AGENT`
  (on / off).

## 5. Safety (same rules as Guided mode, enforced in `guard.py`)

- **Allowed sites only:** the plan's site and its subdomains. Any other domain is refused before the
  browser goes there. robots.txt is checked on every navigation and refused paths are skipped.
- **Allowed actions only** (section 3, step 2). Never: submit enquiry, contact, sign-up, checkout,
  review or message forms; buy; post; upload; download files; change account settings. Buttons whose
  text means submit / send / buy / pay / book / apply / contact / call / WhatsApp / post are refused,
  whatever the agent says.
- **CAPTCHAs and human checks:** detected with the existing `blockdetect`. The run pauses as
  **Needs you** and you solve it in the visible browser. A second one soon after stops the run and
  cools the site down, as now. Never solved or worked around.
- **Logins:** only through the existing own-account login window. Passwords are never given to the
  agent (browser-use's `sensitive_data` is not used). Pages behind a login are reduced to text shape
  before they reach the AI, as in Guided mode.
- **Personal data:** phones and emails are masked in everything sent to the AI and in stored rows,
  unless you opted in with a purpose (existing rule).
- **Limits per run:** steps (default 40), minutes (default 10), AI calls (default 60), plus the site's
  daily page budget. Every action is written to the activity log.
- **You stay in control:** Pause, Stop and Take over at any moment. Take over hands you the visible
  browser; **Continue** resumes from wherever you left it.

## 6. The Agent screen

1. **Start:** goal box (with examples for jobs, products and property), site or link, a "How much"
   choice, limits under "More options".
2. **Plan card:** navigation steps (editable list), columns as chips, conditions, limits. Buttons:
   **Start**, Edit, Cancel.
3. **Live run:**
   - A timeline of steps, each with a small screenshot and a plain sentence ("Typed 'Whitefield' into
     the locality box", "Chose 3 BHK in Filters"). The current step pulses.
   - Counters: steps, AI calls, minutes, rows.
   - **Watch** (show the browser), **Pause**, **Take over**, **Stop**.
   - "Needs you" cards inline (log in / solve the check, then Continue).
4. **Result:** summary card, the quality tiles, **Open results**, **Save as**, and **Run again (no
   AI)**. A saved agent scrape also shows its route (the steps as a list) and a **Re-record route**
   button.

## 7. AI model (decision needed: see section 9)

browser-use sends the page state to the AI at each step. It works best with a model that can see
screenshots and has a large context. Groq's free gpt-oss-120b can't see screenshots and hit its daily
limit during normal use today, so it can't carry an agent loop.
- **Recommended:** Claude for the agent (`SCRAPER_AGENT_PROVIDER=anthropic`). Groq keeps the cheap
  structured calls (plan, recipe, request).
- **Alternatives:** a paid Groq plan with a vision model; any OpenAI-compatible endpoint. Text-only mode
  (`use_vision=False`) stays possible, with lower reliability.
- The agent screen shows the AI calls used per run; Settings shows the provider and model in use.

## 8. Phases

| Phase | Delivers | Done when |
|---|---|---|
| **1 · Foundation** | Agent mode switch, worker + separate venv, `agent_runs` / `agent_steps`, guard, planner, the Agent screen with plan card and live timeline, navigate → hand over to the recipe engine → job | Goal + site → plan → agent reaches the list → rows collected, on 3 test sites |
| **2 · Routes** | Record routes; replay with Playwright (no AI); saved agent scrapes; Run again with no AI; Re-record when replay fails | A filter-by-click site re-runs with 0 AI calls |
| **3 · Check & fix** | Supervisor loop (more pages, item pages, re-detect, pagination repair), summary card | A run with a missing column turns on item pages by itself and fills it |
| **4 · Take over & polish** | Watch, Take over and Continue; Needs-you inside agent runs; fallback AI extraction; Settings → Agent | You can take over mid-run and the agent continues from there |
| **5 · Later (optional)** | Several sites in one goal with merged results; finding sites through a search API (Brave / Bing key); scheduled agent runs | — |

## 9. Decisions needed (my proposed defaults)

1. **Sites:** you always name the site or paste a link. Finding sites by itself waits for phase 5
   and needs a search API key. *(Default: you name it.)*
2. **AI model for the agent:** Claude key, or a paid Groq vision model. *(Default: Claude; nothing
   else in the app changes.)*
3. **Hands-off level:** approve the plan once, then the agent runs within the allowed actions and
   limits. An optional "ask me before each filter" switch is available. *(Default: approve once.)*
4. **What the agent may do on a site:** search, filters, dropdowns, date pickers, sorting, paging,
   "load more", opening item pages. Never enquiry / contact / sign-up / checkout forms.
   *(Default: as listed.)*

## 10. Verification

- **Unit tests (offline):**
  - planner with a fake AI
  - guard refusing other domains, robots.txt-disallowed paths and submit / buy / contact buttons
  - route recording and replay against local test pages (search box + filter buttons + results that
    change without a URL change)
  - supervisor decisions from sample quality reports
  - worker ↔ app API with a fake browser-use agent
- **Live checks:**
  - books.toscrape.com, "mystery books under £20" (category navigation)
  - quotes.toscrape.com search / tag pages (search-box and tag navigation; filters live in the page)
  - one real listing site you choose (e.g. a job board or a property site that allows it in robots.txt)
- Run each live check twice: the second run must use **0 AI calls** (route replay).
- `check.py` stays green. The app's environment must stay unchanged (no browser-use packages in
  `.venv`). Screenshots of the Agent screen in light, dark and phone width.

## Risks

- **AI cost and speed:** first runs take 1–5 minutes and dozens of AI calls. Shown up front; capped by
  the limits.
- **Site changes break routes:** detected on replay, then re-recorded by the agent (phase 2).
- **browser-use changes fast:** pinned version, separate environment, our code depends only on its
  `Agent` + controller interface.
- **Company network / policies:** the visible bundled browser is blocked, so the agent uses installed
  Edge or Chrome, the same fallback as today. The SSL-intercepting firewall still blocks some sites,
  and that is not bypassed.
