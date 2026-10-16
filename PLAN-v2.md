# Universal Scraper: v2 Plan

The draft (v0.1) proved the idea: URL in, AI-built recipe, preview, crawl, table, export. This plan
takes it from a draft to a tool you can rely on every day. It is split into five phases. Each phase is
useful on its own and can be built in one sitting, so the app keeps working between phases.

| Phase | Goal | Size |
|---|---|---|
| 0 | Safety net: git, limits, rate-limit handling | ½ day |
| 1 | More sites: JavaScript rendering, load-more pages, table pages | 1–2 days |
| 2 | More data per product: detail pages, clean numeric output | 1–2 days |
| 3 | Keep it working: recipe auto-repair, scheduled re-runs, price history | 1–2 days |
| 4 | Easier to use: point-and-click fields, multi-site compare, filters, packaging | 2–3 days |

---

## Phase 0: Safety net

**Why first:** everything after this changes a lot of code. With no git, a bad change can't be undone,
and Groq's free-tier limits currently make the AI step fail outright instead of waiting.

| # | Change | Files | Done when |
|---|---|---|---|
| 0.1 | `git init`, commit the draft, `.gitignore` already excludes `.venv`, `data/`, `.env` | — | `git log` shows the draft as the first commit |
| 0.2 | Retry Groq / Claude calls on rate limits: read the `retry-after` hint, wait up to 60 s, try 3 times | `recipe_builder.py` | A 429 from Groq no longer fails the analyse; the UI shows "waiting for AI quota…" |
| 0.3 | Job queue: run at most N jobs at once (default 2), the rest wait as `queued`; resume jobs that were running when the server restarted instead of marking them stopped | `jobs.py`, `db.py` | Start 5 jobs: 2 run, 3 queue; restart the server mid-job: it continues from the next page |
| 0.4 | Per-site settings: delay, max pages, and "use browser rendering" stored with the recipe | `db.py`, `main.py`, UI | A site's delay can be changed in the UI and is remembered |

---

## Phase 1: More sites

**Why:** the fetcher reads only the HTML the server sends. Sites that build listings in the browser
(Flipkart, Naukri, IMDb, most Alibaba pages) come back empty, and IndiaMART loads more products only
when you scroll.

| # | Change | How | Done when |
|---|---|---|---|
| 1.1 | **Browser rendering fallback (Playwright)** | New `app/browser.py`. The fetcher tries plain HTTP first; if `find_candidates()` finds no repeated blocks, it re-fetches in headless Chromium, waits for network idle, and returns the rendered HTML. The choice is saved on the site recipe (`render: auto/never/always`) so later pages don't try twice. Same throttling and robots.txt rules apply. | A JavaScript-only listing page (e.g. `webscraper.io/test-sites/e-commerce/ajax`) analyses and scrapes with no manual setting |
| 1.2 | **Load-more / infinite scroll** | In browser mode, click "load more" or scroll until the item count stops growing or `max_pages` worth of items is reached. Recipe field: `pagination.type: "scroll"` or `"click"` + selector. The AI prompt learns about these two types. | IndiaMART category page returns more than the first 28 products |
| 1.3 | **Table pages** | A second extraction mode: `item_selector` points at `table tr`, and fields are columns (`"column": 3` or `"header": "Price"`). The candidate finder reports tables too. | A Wikipedia list page gives one row per table row with the header names as fields |
| 1.4 | **Hidden JSON data** | Many sites embed the listing data as JSON in a `<script>` tag (TradeIndia has `__NEXT_DATA__`). When present, offer it to the AI as a candidate; recipe type `json` with a path like `props.pageProps.products[*]`. Cleaner and faster than CSS. | TradeIndia recipe can be built from its JSON instead of hashed class names |
| 1.5 | **Block detection in browser mode** | Recognise Cloudflare challenge pages and CAPTCHAs in the rendered page as well, and stop. Never attempt to solve them. | A blocked site shows a clear "blocked" job status |

**Note on Playwright:** install adds ~300 MB (Chromium). Make it optional: the app runs without it, and
the UI says "install with `playwright install chromium`" when a site needs it.

---

## Phase 2: More data per product

**Why:** listing cards show part of the data. IndiaMART cards have no minimum order; TradeIndia cuts the
specs short. The full details are on each product's own page.

| # | Change | How | Done when |
|---|---|---|---|
| 2.1 | **Detail-page crawling** | Recipe gains `detail: {url_field: "url", fields: [...]}`. After a listing page is extracted, the job visits each product URL (same throttle, cache, robots rules) and merges the detail fields into the row. Detail pages are *single-item*, so the AI gets a different prompt: "here is one product page, find these fields". Fetched lazily: listing rows appear first, detail columns fill in as pages load. | IndiaMART rows gain `min_order`, full specs table, and seller GST / TrustSEAL status from the product page |
| 2.2 | **Clean numeric output** | New `app/normalize.py`: `price` → number + currency; `unit` standardised ("Piece/Pieces" → "piece", "Kg"/"Kgs" → "kg"); `min_order` → `min_order_qty` + `min_order_unit`; `rating` → float; `years_in_business` → int. Original text kept in `*_raw`. Applied at extract time so the preview shows it. | Sorting by price in the UI sorts numerically; Excel export has numeric columns |
| 2.3 | **Specs as columns** | The `specs` field ("Brand: X; Material: PVC") is split into one column per label, with the most common labels first. Optional, per job. | Excel export has `Brand`, `Material`, `Size` columns for IndiaMART |
| 2.4 | **Dedupe across pages and sites** | Rows with the same product URL are merged; rows with the same seller + name are flagged as duplicates rather than dropped. | A product listed on two category pages appears once |
| 2.5 | **Images** | Capture `image_url` by default (it's in every card). Optional download of thumbnails to `data/images/` for the Excel export. | Preview table shows thumbnails |

---

## Phase 3: Keep it working

**Why:** sites change their layout, and saved recipes then break quietly. And a one-off scrape is far
less useful than the same scrape run every week with the differences shown.

| # | Change | How | Done when |
|---|---|---|---|
| 3.1 | **Recipe health check and auto-repair** | Every job stores its field fill rates. When a job's fill rate for a field drops by more than 40 points compared with the last good run, or `item_selector` matches 0 items, the job pauses, rebuilds the recipe with AI (using the new page), previews it, and either continues with the new recipe (if fill rates recover) or stops with "recipe needs attention". A `recipe_versions` table keeps the history so you can roll back. | Change a built-in recipe's selector to something wrong; the next job repairs it on its own and says so |
| 3.2 | **Scheduled re-runs** | A `schedules` table: job template (URLs, recipe, fields) + interval (daily / weekly / custom cron). A background scheduler thread starts jobs on time while the server runs. Windows Task Scheduler instructions for running `python run.py --headless` at boot. | A daily schedule for an IndiaMART category runs at the set time and appears in the job list |
| 3.3 | **History and change detection** | Rows get a stable key (product URL). Each run compares with the previous run of the same schedule: *new*, *removed*, *price changed* (with old → new). A "Changes" tab shows them; export includes a `change` column. | After two runs, the Changes tab lists price drops |
| 3.4 | **Price history chart** | Per product, a small line chart of price over runs. Per schedule, min / median / max price over time. | Clicking a product shows its price history |
| 3.5 | **Notifications** | Optional: email or a desktop notification when a run finishes or finds price changes beyond a threshold. | A price drop > 10 % sends an email |

---

## Phase 4: Easier to use

| # | Change | How | Done when |
|---|---|---|---|
| 4.1 | **Point-and-click field picker** | Show the fetched page in an iframe (served through the app so scripts are stripped). Click an element → the app computes a stable selector for it, relative to the item block → the field's selector updates and the preview refreshes. Replaces most JSON editing. | A wrong AI field can be fixed with two clicks |
| 4.2 | **Product-name search across sites** | Type "PVC pipes", tick sites. For each site with a known *category URL pattern* (`dir.indiamart.com/impcat/{slug}.html`, `tradeindia.com/manufacturers/{slug}.html`) the app builds the URL, fetches it, and runs the job. Site patterns live in the recipe. | One search produces one job per site |
| 4.3 | **Compare view** | A merged table across jobs/sites for the same product: cheapest first, with site, seller, city, min order. | IndiaMART and TradeIndia results for PVC pipes in one sorted table |
| 4.4 | **Filters and charts in results** | Price-range slider, city and seller filters, "verified only", column chooser, and a price-distribution histogram. Filtered view exports as shown. | Filter to Ahmedabad sellers under ₹200 and export only those rows |
| 4.5 | **Better job page** | Live log per job (which page, how many rows, retries), per-URL status, "retry failed URLs" button. | A job that hit one bad URL can retry just that one |
| 4.6 | **Packaging** | `pyinstaller` build → a single `UniversalScraper.exe` that runs `run.py`; and a `Dockerfile` for Linux servers. Playwright's Chromium bundled or downloaded on first run. | The exe runs on a PC without Python installed |
| 4.7 | **Tidy the code** | Split `main.py` into routers (`sites`, `jobs`, `schedules`), add type-checked request/response models, and raise test coverage to the new modules. | `pytest` covers fetcher (with mocked HTTP), jobs, normalize, browser mode |

---

## Suggested build order

```
Phase 0 (git, retries, queue)
   └─► Phase 1.1 Playwright ──► 1.2 load-more ──► 1.4 JSON data ──► 1.3 tables
   └─► Phase 2.2 normalize ──► 2.1 detail pages ──► 2.3 specs columns
          └─► Phase 3.1 auto-repair ──► 3.2 schedules ──► 3.3 changes ──► 3.4 charts
                 └─► Phase 4 in any order; 4.1 picker and 4.2 search first
```

Phases 1 and 2 are independent of each other and can be built in parallel (separate sessions, separate
files: `browser.py` / `fetcher.py` vs `normalize.py` / `jobs.py` detail logic). Phase 3 needs Phase 2's
stable row keys. Phase 4 can start any time after Phase 0.

---

## Design rules that stay the same

- **One engine, per-site recipes.** New capabilities are new recipe fields, not new code paths per site.
- **AI once per site, deterministic after.** The model writes or repairs recipes; it never parses every page.
- **Polite and legal by default.** robots.txt respected, 2–5 s delays, stop on blocks, no CAPTCHA solving,
  no login, public listing data only. Browser mode follows the same rules.
- **Preview before crawl.** Every recipe change is shown on a real page before a job uses it.
- **Local first.** No account, no cloud; SQLite on disk. Packaging in 4.6 keeps it that way.

## Open questions

1. **Playwright size:** OK with a ~300 MB Chromium download for browser mode, or should it stay optional?
2. **Schedules while the PC is off:** the scheduler only runs while the server runs. Is "run when I start it"
   enough, or should it be a Windows service / Task Scheduler job?
3. **Notifications:** email (needs an SMTP account), desktop toast, or neither for now?
4. **Priority between Phase 1 (more sites) and Phase 2 (more data per product)?** They can run in parallel.
