# Plan: trustworthy data, collected the right way

> **Status: phase 1 built.** A run no longer "succeeds" on a block page or with no rows, values are checked
> against the page they came from, a site nobody has reviewed needs approval before its first scrape, and
> every row keeps the page it came from and when. Phases 2 and 3 wait for your review of phase 1.

## Context

Web research (Oct 2026) shows five problems with today's scraping methods:

1. Anti-bot systems score behaviour and share reputation between sites.
2. Cloudflare blocks AI crawlers by default and can charge per crawl (HTTP 402).
3. Scrapers break silently when a layout changes.
4. AI scraping is costly and can "succeed" on a block page.
5. Legal risk is rising: site terms forbid scraping and lawsuits are active.

Our answer is **not** to fight anti-bot systems: no proxies, no stealth, no CAPTCHA solvers. Instead the app
gives **trustworthy data, collected the right way**: results are checked, collecting is compliant by
default, and every value can be traced to its page.

Decisions: an unreviewed site needs approval first; alerts go in-app plus an optional Teams/Slack webhook
(phase 2); phase 1 ships first, then review.

## Phase 1 (built)

### 1.1 False success

`app/runs/trust.py`:

- `soft_block(html)` reads the visible text (scripts, styles and `<noscript>` removed) for verification and
  "access denied" phrases, **at any page length**. `blockdetect` only looks at thin pages, so a full-size
  "Verifying your request" page with HTTP 200 used to pass.
- `assess(rows, pages, quality, prev_rows)` names why a finished run looks wrong:

| Code | When |
|---|---|
| `stated_but_empty` | The page says "120 results" but nothing was collected |
| `zero_rows` | Pages were read, no rows |
| `empty_pages` | Some pages gave nothing although they state a total |
| `rows_dropped` | Rows fell by 70 % or more against the scrape's last finished run (that run had 10 or more) |

In `app/runs/jobs.py`:

- A page with 0 items is read by `soft_block`. A challenge pauses the job as **Needs you** ("Open to solve",
  then Continue); "access denied" stops it as blocked.
- A run with 0 rows ends as **error**, with the reason as its message, not **done**. A run with rows but
  findings stays done, with `quality.suspect`.
- The Results page shows a red banner for `suspect` (Re-detect columns, Open the page), the Quality tab lists
  the same warnings, and the progress line says "Finished, but it looks wrong: …".

### 1.2 Value checks

Values come from selectors, not from the AI, so this checks the **column setup**: AI-written regexes, title
fallbacks and wrong selectors that give values the page doesn't show.

- `check_values(rows, html, recipe)`: a text value that isn't in the page's visible text is *ungrounded*.
  Links (made absolute), and built values (`kv`, `join`, `json` fields and JSON-sourced recipes) are skipped.
- `check_typed(rows, types)`: a rating outside 0–10 or a price of 0 or less is *invalid*.
- Counts add up per run in `quality.value_checks`. The Quality tab's columns table has a **Checked** column,
  and a warning appears when 3 or more values (over 20 %) of a column aren't on the page.

### 1.3 Site policy

Per site, in `sites.settings`:

- `policy`: `approval` (nobody reviewed it yet), `allowed` or `blocked`. The older `blocked: true` reads as
  `blocked`, and the two stay in step. `www.` and the bare domain share one policy.
- `review`: `{terms: not_reviewed | ok | restricted, note, approved_by, approved_at}`.
- `SCRAPER_SITE_POLICY_DEFAULT` (default `approval`) sets what an unknown site gets.
- Once, at start-up, sites already in use (saved settings or any run) are back-filled as `allowed` with
  terms "not reviewed", so nothing that worked before stops working.

Enforcement, at one choke point: `fetcher.policy_gate(url)` runs at the top of `fetch()` and
`fetch_rendered()`, **before the cache**. Before this a never-scrape site could still be served from the
cache. The agent's `guard.check_url` uses the same `site_policy`.

Approve flow:

- Analyse, Start and the agent's Start answer **409** `{"code": "needs_approval", "domain", "message"}`.
  The page opens "Approve this site" (a note), calls `POST /api/sites/{domain}/approve`, and asks you to start
  again.
- A saved scrape run on an unapproved site pauses as **Needs you** with *Approve site and continue*.
- Settings → Sites shows a policy chip and the terms review, and lets you change the policy, the terms
  status and the note.
- The activity log records `site_approved`, `policy_changed`, and the personal-data purpose
  (`personal_data_purpose`) when a run collects unmasked fields.

### 1.4 Where each row came from

- `records.fetched_at` is stored with each row, next to the page it came from, and kept through sort and
  limit.
- The download menu has **Add where each row came from**, which adds `source_page` and `collected_at`.
- Hovering a cell in the Results table shows its page and time.

### Tests

`tests/test_trust.py` (block pages, findings, value checks, a "120 results but nothing" run ending as an
error, a verification page pausing and Continue finishing it, provenance through sort and export) and
`tests/test_site_policy.py` (409 on analyse and start, approve plus audit, never-scrape refused from the
cache, the agent refusing unapproved sites, the back-fill). Tests run with `SITE_POLICY_DEFAULT=allowed`
(`tests/conftest.py`) except the policy tests.

## Phase 2: official data first, schedules and alerts (after review)

- **Official-data finder** (`app/extraction/sources.py`): embedded JSON lists, RSS/Atom feeds, sitemaps,
  JSON responses seen while rendering, and a small registry of official APIs (booking.com Affiliate Partner
  API, Amazon Product Advertising API) shown as a "Better source found" card on New scrape.
- **Scheduled runs**: `scrapes.schedule`, a background scheduler that skips running, paused, unapproved or
  cooling-down scrapes.
- **Alerts**: a `notifications` table and a bell in both modes, rules on the run comparison, `health` and
  `suspect`, and an optional Teams/Slack webhook. A live data link `GET /api/scrapes/{id}/latest.{csv,json}`
  with a per-scrape key.

## Phase 3: gatekeepers (later)

- **HTTP 402 Payment Required**: `fetcher.PaymentRequired` reads the price headers, the job pauses with
  `paused_kind="payment"`, and the app **never pays by itself**.
- **Licensed data providers and official API connectors**, plugged in one at a time.
