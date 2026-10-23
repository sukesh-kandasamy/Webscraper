# Universal Scraper: Product Plan (draft)

> **Decision (2026-10-23): single user, no login for now.** The app stays in local mode on one PC.
> Phase 1 accounts / server mode are postponed. Building now: Phase 2 (guided wizard, saved scrapes,
> results view, plain-language messages) plus the Simple / Advanced switch from Phase 3.
> **Built:** app shell (2.9), wizard (2.1), templates (2.2), saved scrapes (2.3), results viewer with
> column picker and thumbnails (2.4), named exports (2.5), plain-language statuses (2.6) and errors (2.7),
> sample-shop onboarding (2.8 partly), plain wording (2.10), Advanced switch (3.1).
> **Also built:** bundled frontend assets with compiled Tailwind and no CDNs (1.7), professional
> design pass (icons, dialogs, toasts, stats, loader, paging, themes).

## Context

Today the app is a strong **single-user, local** tool: paste a URL, the AI picks columns, preview, crawl,
export. It already has solid internals (recipes, AI column detection, browser rendering, job queue,
budgets, CAPTCHA pauses, personal-data masking, encryption, audit log, 79 tests).

It is not yet a product a company can hand to its staff:
- It runs on one PC and is deliberately locked to `localhost` (`run.py` refuses other hosts,
  `TrustedHostMiddleware`, vault key in the user's Windows Credential Manager), so a team cannot share it.
- The screen is one long page built for a developer: JSON recipe editor, raw settings, technical words
  ("recipe", "selector", "render mode", "robots.txt").
- Nothing is organised: jobs are a flat list, there is no notion of a saved "scrape" you re-run.
- Tailwind and Alpine load from CDNs at runtime, which is fragile on company networks
  (the Fortinet firewall already intercepts some sites).

**Your choices:** shared company server · business users first with an Advanced mode for the data team ·
company features kept simple for now.

**Goal:** a business user can collect data from a website in three guided steps without seeing anything
technical; the company runs one installation for everyone, with simple accounts, shared saved scrapes and
an admin page.

---

## Guiding principles

1. **Three steps, no jargon.** Paste a link → check the columns → get the data. Everything else is optional.
2. **Simple by default, powerful on request.** Advanced mode reveals the recipe editor, site settings,
   activity log and API; business users never need it.
3. **Save once, re-run forever.** A configured scrape becomes a named, shareable item with run history.
4. **Explain, don't error.** Every failure says what happened and what to do next, in plain language.
5. **Keep the engine.** Reuse what exists (`recipe_builder`, `extractor`, `fetcher`, `jobs`, `privacy`,
   `sessions`, `audit`); the work is mostly product, UX and multi-user plumbing.

---

## Phase 1 — Server foundation (multi-user, simple)

| Item | What | Reuse / change |
|---|---|---|
| 1.1 Accounts | Email + password sign-in (bcrypt/argon2), first account created at setup becomes **Admin**; Admin invites users. Two roles only: **Admin**, **Member**. "Microsoft / Google sign-in" left for later. | New `app/auth.py`, `users` table in `db.py`; FastAPI dependency on every `/api/*` route |
| 1.2 Sessions & CSRF | Secure HTTP-only session cookie; CSRF token for POST/PUT/DELETE (replaces the localhost-only Origin assumption) | Extend the `same_origin_only` middleware in `app/main.py`; keep security headers |
| 1.3 Server mode config | `SCRAPER_MODE=server` with allowed hosts / public URL from config; local mode keeps today's behaviour | `config.py`, `run.py` (allow `--host 0.0.0.0` in server mode behind HTTPS) |
| 1.4 Secrets on a server | Vault key from an env var / secret file instead of Windows Credential Manager | `app/vault.py` `_load_key()` gets a server branch |
| 1.5 Ownership | Every scrape, job and result has an owner; Members see team-shared items + their own; Admin sees all | Add `owner_id`, `shared` columns; filter in `db.list_jobs` etc. |
| 1.6 Packaging | `Dockerfile` + `docker-compose.yml` (app + Playwright Chromium), health endpoint, data volume, nightly SQLite backup; HTTPS via reverse proxy (Caddy/Nginx) documented | New files; `run.py` unchanged for local use |
| 1.7 Frontend assets bundled | Tailwind compiled at build time (standalone CLI) and Alpine served locally — no runtime CDNs | `static/`, CSP in `main.py` tightened (no `unsafe-eval` if Alpine CSP build is used) |

**Decision needed later:** logged-in scraping and "solve the CAPTCHA yourself" open a *visible* browser on
the machine running the app. On a server nobody can see that window. Recommendation for this draft:
keep these two features in **local mode only**; in server mode a challenge stops the job with a clear
message. (A remote-browser view can come later.)

## Phase 2 — Guided experience for business users (Simple mode)

| Item | What | Builds on |
|---|---|---|
| 2.1 "New scrape" wizard | **Step 1 Link** (paste URL; site preview + "this site works / needs browser / blocked" check) → **Step 2 Columns** (page type badge, column chips with ×, "add a column", sample table of 10 rows, rename columns inline) → **Step 3 Run** (how many pages, run now or save, name the scrape) | `/api/analyse`, `/api/preview`, column chips from the current UI |
| 2.2 Templates by category | Start from "Jobs", "Products & prices", "Real estate", "Stock tables", "Business directory", "News" → pre-fills suggested columns; the AI still adapts to the page | `PAGE_TYPES` + typical columns in `recipe_builder.SYSTEM` |
| 2.3 Saved scrapes ("Projects") | Named scrape = URLs + recipe + columns + settings; list with last run, rows, status; **Run again** button; duplicate; share with team | New `scrapes` table; jobs link to a scrape |
| 2.4 Results viewer | Clean table with column types (number, money, date, link, image thumbnail), filters per column, sort, search, select columns to export; open a row's source link | Today's results table + `extractor` output; add light type detection |
| 2.5 Export & share | Excel / CSV (exists), plus "copy link to these results" for teammates; Google Sheets later | `export.py` |
| 2.6 Plain-language statuses | "Collecting page 3 of 10", "Waiting a few seconds between pages (polite mode)", "The site asked for a human check — we stopped", "Daily limit reached for this site" | Map job states/messages in `jobs.py` to friendly text in the UI |
| 2.7 Friendly errors | Every API error carries `code` + `user_message` + `what_to_do`; UI shows them as cards with an action button | Extend `HTTPException` details in `main.py` |
| 2.8 First-run onboarding | Welcome screen, 30-second tour, sample scrape on a practice site, empty states that explain the next step | New UI |
| 2.9 Navigation & layout | App shell: left sidebar (Home, New scrape, Saved scrapes, Results, Settings), header with user menu; responsive | Replace the single long page in `static/index.html` |
| 2.10 Wording | Replace jargon: recipe → "column setup", selector → hidden, render mode → "Load page like a browser", robots.txt → "Site's crawling rules" | UI text only |

## Phase 3 — Advanced mode for the data team

| Item | What | Builds on |
|---|---|---|
| 3.1 Advanced toggle | Per-user switch that reveals the items below | User preference |
| 3.2 Visual column editor | Form per column (where to find it, attribute, clean-up pattern) with live preview; JSON view still available | Existing recipe JSON + `/api/preview` |
| 3.3 Site settings & sessions | Today's settings panel, budgets, cool-downs, local-mode logins | Existing endpoints |
| 3.4 Activity log & run details | Per-run log (pages, timings, errors), audit log view | `audit.py`, `job_pages` |
| 3.5 REST API + API keys | Personal API keys to start scrapes and fetch results as JSON/CSV | Existing routes behind key auth |

## Phase 4 — Simple company features

| Item | What |
|---|---|
| 4.1 Admin page | Users (invite, role, disable), AI provider + key (stored encrypted, not in `.env`), default limits, data retention days |
| 4.2 Simple schedules | "Run every day / week at …" on a saved scrape; run history; email when finished or failed (SMTP settings in Admin) |
| 4.3 Usage overview | Per user / per site: pages fetched, rows collected, AI calls this month (from `audit.log` / `site_usage`) |
| 4.4 Data retention | Existing purge, configurable by Admin; "keep" on important runs (exists) |

## Phase 5 — Polish & reliability

- Branding: logo, name, colour theme, favicon, About page with version.
- Help: in-app help drawer per screen, short docs site (README → user guide + admin guide).
- Reliability: worker process separate from the web process (jobs survive web restarts), retries with
  clear limits, `/health` and basic metrics.
- Quality: end-to-end UI tests with Playwright (wizard → run → export), GitHub/GitLab CI running
  `pytest`, `check.py`, secret scan.
- Database: stay on SQLite for small teams; optional PostgreSQL via `SCRAPER_DB_URL` when a team grows.

---

## Suggested order and size (draft)

```
Phase 1 server foundation (≈1 week)
  └─► Phase 2 guided UX: 2.9 shell → 2.1 wizard → 2.3 saved scrapes → 2.4 results → rest (≈1.5–2 weeks)
        └─► Phase 4 admin + schedules (≈1 week)   ──┐
        └─► Phase 3 advanced mode (≈1 week)          ├─► Phase 5 polish (≈1 week)
```

Phases 3 and 4 can run in parallel once Phase 2's app shell and saved scrapes exist.

## Critical files

- `app/main.py` — auth dependency, CSRF, friendly error format, new routes (scrapes, users, admin, schedules)
- `app/db.py` — `users`, `scrapes`, `schedules` tables; owner/shared columns; filters
- `app/vault.py`, `app/config.py`, `run.py` — server mode, secret key source, host binding
- `app/jobs.py` — link runs to saved scrapes, friendly progress text, scheduler hook
- `static/index.html`, `static/app.js` — replaced by an app shell + wizard (still Alpine + Tailwind, but built locally)
- New: `app/auth.py`, `app/scrapes.py`, `app/scheduler.py`, `Dockerfile`, `docker-compose.yml`, `docs/`

## Verification (for each phase when built)

- `python check.py` and `pytest` stay green; new tests for auth (roles, CSRF, owner filtering), saved
  scrapes, schedules.
- Server mode: `docker compose up`, sign in as Admin, invite a Member, check the Member only sees shared
  and own scrapes.
- Wizard: a non-technical walkthrough on 4 page types (jobs, products, real estate, stock table) from
  link to Excel without opening Advanced mode.
- Playwright end-to-end test of the wizard and results export in CI.

## Open questions (for the next round)

1. Product name and branding?
2. Is email + password enough for sign-in at first, or is Microsoft 365 sign-in needed from day one?
3. Logged-in scraping / CAPTCHA solving on the server: local-only for now (recommended), or invest in a
   remote browser view?
4. Where will the server run: company VM, cloud (Azure/AWS), or an office machine?
