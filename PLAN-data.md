# Plan: complete, accurate data + "Describe what you need" prompt before analysing

## Context

The app now collects listings well from many sites and looks professional, but two gaps remain:

1. **Data is not always complete or clean.** It only reads what listing cards show (salary, specs or
   seller details often live on each item's own page); it stops at the pages it can see; values are text
   (`"1,62,499"`, `"₹ 450 / Piece"`), so sorting and maths are unreliable; and nothing tells you how
   complete a run was (pages that failed, duplicates, empty columns, "28 of 1,250 items").
2. **You can only say *which* columns, not *what you need*.** Today step 1 has a comma-separated columns
   box. You can't say "from this shoes page I need the image, price and brand, only men's shoes under
   ₹3,000, price as a number", so the AI guesses meanings from bare names and collects everything.

**Your choices:** item-page details are an **optional switch per scrape** · before analysing, the user
**describes the data they need in a prompt** and the AI collects exactly that.

**Outcome:** every run comes with a quality report you can trust, values are typed (numbers, money,
dates), the app collects all pages and optional item details, and step 1 has a "Describe what you need"
prompt whose request (columns, their meaning, filters, sort, limit) drives the whole scrape.

---

## Phase A — Data you can trust (quality foundation)

| # | Change | How | Reuse / files |
|---|---|---|---|
| A1 | **Column types & clean values** | New `app/normalize.py`: detect each column's type from its values (number, money, percent, rating, date, URL, image, text). Money → number + currency (`₹ 1,62,499` → 162499, INR; handles lakh/crore commas and `k`/`L` suffixes); "4.5 out of 5" → 4.5; "2d ago"/dates → ISO date; units split (`450 / Piece` → 450 + piece). Original text is kept; typed values go in `<col>` and raw in `<col>_text` only when they differ. | Applied in `jobs._run` after `extractor.extract`; types stored on the job (`jobs.columns` JSON) |
| A2 | **Quality report per run** | `app/quality.py` computes: rows, pages fetched / failed (with reasons), duplicates removed, per-column fill %, distinct values, type-check failures (e.g. price without a number), and **coverage** when the page states a total ("Showing 1–24 of 1,250" → 24/1,250). Stored in `jobs.quality` JSON. | Uses `extractor.field_report`, `db.job_pages`; new column in `db.MIGRATIONS` |
| A3 | **Better de-duplication** | Dedupe by the item URL when there is one (today: whole-row equality), so the same product on two pages counts once; keep the most complete copy. | `jobs._run` `seen_rows` |
| A4 | **Retry failed pages** | Failed pages are retried once at the end of the run (after the polite delay); still-failed ones are listed in the quality report with a "Retry failed pages" button. | `jobs._run`, new `POST /api/jobs/{id}/retry-failed` |
| A5 | **Typed exports** | Excel gets real number/date cells (filters and sums work); CSV keeps plain values. | `app/export.py` |

## Phase B — Collect more of what's there

| # | Change | How | Reuse / files |
|---|---|---|---|
| B1 | **Structured data first** | New `app/structured.py`: read schema.org JSON-LD (`Product`, `Offer`, `JobPosting`, `RealEstateListing`, `ItemList`, `Event`…) and embedded app state (`__NEXT_DATA__`, `window.__INITIAL_STATE__`). When a page carries a clean list of items, offer it to the AI as a candidate and allow a recipe `"source": "json"` with key paths. Cleaner and more complete than CSS when present. | `recipe_builder.find_candidates` / prompt; `extractor` gains a JSON path mode |
| B2 | **"All pages" option** | Step 3 gets "Collect all pages (up to N)" next to the page count; the job keeps following next-page links / scroll / load-more until no new items appear or the cap is hit (default cap 50, per-site daily budget still applies). | `jobs._run` loop, `fetcher.fetch_smart` |
| B3 | **Item pages (optional switch)** | Wizard step 2: "Also open each item's page for more details". Turning it on fetches **one** item page and asks the AI with a single-item prompt for the extra columns (e.g. salary, specs, description, seller, GST); preview shows the merged row. Recipe gains `detail: {url_field, fields}`. During the run, listing rows are saved first, then item pages are visited (same throttle / robots / budget / CAPTCHA pause rules) and their fields merged into the row; progress shows "details 12 / 81". | New detail prompt in `recipe_builder`; `extractor.extract` reused on item HTML; `db.update_record` (new); `jobs._run` second pass |
| B4 | **Missing-column hint** | When a column you asked for isn't on the list page, step 2 suggests turning on item pages ("salary is probably on each job's page"). | `analysis.absent` (exists) |

## Phase C — "Describe what you need" prompt (before analysing)

The user writes, in plain words, what they want from the page. Example on a shoes page:
*"I need the image, price and brand of each shoe. Only men's shoes under ₹3,000. Price as a number,
cheapest first."* The AI turns that into a **data request** that drives the whole scrape.

| # | Change | How |
|---|---|---|
| C1 | **Prompt box in step 1** | "Describe what you need" text area (with an example placeholder) becomes the main input; the old comma-separated columns box moves under "or list columns". Templates pre-fill an example prompt for their data type ("I need the job title, company, location, salary and posted date"). Ctrl+Enter runs it. |
| C2 | **AI reads the request** | New `app/request_parser.py`: one short AI call (no page HTML needed, fast) with structured output → `{columns: [{name, meaning, type, required}], filters: [{column, op, value}], sort: {column, desc}, limit, include_details_hint}`. E.g. `brand` = "manufacturer / brand name; may be the first word of the product title", type `text`; `price` type `money`. Unclear requests get a short follow-up question instead of a guess. Reuses `recipe_builder._ask_model` / `_with_retries` and provider settings. |
| C3 | **"Here's what we'll collect" check** | Before reading the page, step 1 shows the understood request as editable chips: columns with their meaning (hover), filters ("price < 3,000", "category contains men"), sort and limit. The user can remove/rename/add before continuing, or edit the prompt and re-read it. |
| C4 | **Meanings guide the recipe** | `recipe_builder.build_recipe` receives column **meanings and types**, not just names, so the AI picks the right element ("price" = selling price, not the struck-out MRP) and can **derive** a column from another one with a regex when the page has no separate element (brand from the title). Typed columns (A1) are validated against the requested type; mismatches are sent back to the AI once (extends the existing number check). |
| C5 | **Filters, sort, limit** | Applied after extraction on the typed values (from A1) in the step-2 preview and in every run: "Found 48 items · 31 match your request". Rows that don't match are not stored (or kept in a hidden "excluded" set for the quality report). Saved with the scrape and re-applied on Run again. |
| C6 | **Missing data → item pages** | If a requested column isn't on the list page, step 2 says so and offers the item-page switch (B3) with the request's meaning ("salary is probably on each job's own page"). |
| C7 | **Saved with the scrape** | The prompt and the parsed request are stored on the saved scrape; Edit shows the prompt, so changing "under ₹3,000" to "under ₹5,000" and re-running is one step. |

## Phase D — Keep it accurate over time

| # | Change | How |
|---|---|---|
| D1 | **Recipe health check** | Compare each run's fill rates with the scrape's last good run; a column dropping from 95% to 0% marks the setup as broken: rebuild with the AI on the new page and show "Columns were re-detected because the site changed". |
| D2 | **Run comparison** | On a saved scrape: "Compare with previous run" → new / removed / changed items (keyed by URL), e.g. price changes. |

## Other improvements (later, not in this plan)

Asking questions about finished results ("cheapest 5 under ₹1 lakh") · schedules & email alerts on
changes · Google Sheets export · API keys for other tools · visual point-and-click column picker ·
per-site rate presets · job queue progress ETA.

---

## Build order

```
A1 types ─► C1–C5 prompt + request (filters need typed values) ─► A2 quality ─► A3 dedupe ─► A4 retries ─► A5 typed export
                    │
                    └─► B1 structured data ─► B2 all pages ─► B3 item pages ─► C6 hint ─► C7 saved prompt ─► D1 health ─► D2 compare
```
The prompt (Phase C) comes right after A1 because it is the main new way of starting a scrape. Each item
lands with tests and a commit.

## Critical files

- New: `app/normalize.py`, `app/request_parser.py`, `app/quality.py`, `app/structured.py`
- `app/recipe_builder.py` (column meanings/types in the prompt, derived columns, JSON candidates, item-page prompt)
- `app/jobs.py` (typing, request filters/sort/limit, URL dedupe, retries, all-pages loop, item-page pass, quality report)
- `app/extractor.py` (JSON path mode), `app/db.py` (`scrapes.prompt`, `scrapes.request`, `jobs.columns`, `jobs.quality`, `update_record`), `app/export.py`
- `app/main.py` (`POST /api/understand` for the prompt, analyse/preview accept the request, retry-failed, compare), `static/index.html`, `static/app.js`, `static/src/app.css` (rebuild with `tools/build_css.py`)

## Verification

- **Unit tests:** money/date/rating parsing (Indian number formats, currencies), type detection, coverage
  parsing, URL dedupe, JSON-LD extraction (fixtures), item-page merge with a fake fetcher, request parsing
  with a fake AI (columns, meanings, filters "under ₹3,000" → `price < 3000`, sort, limit), filters applied
  on typed values, derived column (brand from title).
- **Live checks (headless UI script, as before):**
  - A shoes listing page with the prompt "image, price and brand; under ₹3,000; cheapest first" →
    exactly those 3 columns, price numeric, only matching rows, sorted.
  - books.toscrape.com, "title, price and rating, all pages" → 1,000 books, quality report 100% coverage.
  - We Work Remotely, "job title, company, salary" with item pages on → salary filled where shown.
- `python check.py` green; screenshots of the prompt step, request chips, preview and Quality tab in
  light, dark and phone width.
