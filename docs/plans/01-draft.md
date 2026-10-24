# Universal Scraper: Draft Plan (v0.1)

> The draft is built. The plan for what comes next is in [02-features.md](02-features.md).

> **Status (2026-10-16): draft built.** Milestones 1–5 and 7 are done; milestone 6 (second site)
> was done with a built-in TradeIndia recipe. The AI recipe builder is written but **not yet tested
> against the live API** (no API key yet). Changes from the plan below:
> - Both sites block **search pages** in robots.txt, so the app works from **category pages**
>   (`/impcat/...` on IndiaMART, `/manufacturers/...` on TradeIndia). One job can take many URLs.
> - Neither category page has crawlable pagination (TradeIndia also disallows `?page=`), so each URL
>   is one page. Pagination support was verified on books.toscrape.com instead.
> - Recipe fields are a list, and gained `regex` and `kind: "kv"` (label/value spec lists).
> - `cleaner.py` was folded into `extractor.py` / `recipe_builder.py`. A `robots.py` was added.

A local web app. You give it a website URL and the fields you want. AI works out how
to extract them, you preview and approve, and the app crawls the pages and lists the
results in a table you can filter and export.

---

## 1. Goal of the draft version

Prove the core idea end to end on **one real site (IndiaMART)** and **one second site**
(to show it is flexible), running on your own machine.

**The draft is done when:**
1. I can paste a search or listing URL, type the fields I want, and click **Analyse**.
2. The app shows a **preview table** of about 10 extracted rows within a few seconds.
3. I click **Approve & Run**, and it crawls several pages with a live progress bar.
4. The results show in a sortable, searchable table, with **CSV / Excel download**.
5. The site's recipe is saved, so next time it runs without calling the AI again.

---

## 2. Scope

| In the draft | Later versions |
|---|---|
| Listing/search pages + pagination | Detail-page crawling (open each product page) |
| AI-generated recipe (CSS selectors) | Auto-repair of a recipe when a site changes |
| Manual recipe editing (YAML/JSON box) | Visual point-and-click field picker |
| Plain HTTP fetching | Playwright for JavaScript-heavy sites |
| Background job + progress polling | Scheduling (re-run daily), alerts |
| SQLite storage, CSV/Excel export | Multi-user, login, cloud deployment |
| Rate limiting, caching, robots.txt check | Proxy support |
| Works without an API key (manual recipes) | Pure AI extraction fallback per page |

---

## 3. How it works

```
 Browser UI ──► FastAPI backend ──► Fetcher ──► target website
     ▲                │
     │                ├─► AI Recipe Builder (Claude): sample HTML → selectors
     │                ├─► Extractor: recipe + HTML → rows
     │                ├─► Job Runner: background crawl, progress, rate limits
     │                └─► SQLite: sites, recipes, jobs, records, page cache
     └──── polls job progress / gets results
```

### User flow
1. **New scrape**: enter URL and fields (e.g. `name, price, unit, seller, city`).
2. **Analyse**: the app fetches the page, cleans the HTML, and sends it to Claude, which returns a recipe.
3. **Preview**: the recipe is applied to the page and the user sees the rows. They can edit the recipe and preview again.
4. **Run**: a background job follows pagination up to *N* pages, waiting 2–5 s between requests.
5. **Results**: table, search box, CSV/XLSX export. The job is kept in history.

---

## 4. The recipe (the heart of the flexibility)

Each site gets a recipe, stored as JSON in SQLite:

```json
{
  "site": "dir.indiamart.com",
  "item_selector": "div.card",
  "fields": {
    "name":   {"selector": ".prd-name", "attr": "text"},
    "price":  {"selector": ".price",    "attr": "text"},
    "seller": {"selector": ".cmp-name", "attr": "text"},
    "url":    {"selector": "a.prd-link","attr": "href"}
  },
  "pagination": {
    "type": "url_param",            // or "next_link"
    "param": "page", "start": 1
  }
}
```

- **The AI builds it** from one sample page. It is called **once per site**, not once per page.
- **The app tests it**: if any field comes back empty on more than 50% of rows, it warns you.
- **A human can edit it** in the UI, so the app still works without an API key.

---

## 5. Project structure

```
Web-Scraping/
├── PLAN.md
├── README.md                 # setup + run instructions
├── requirements.txt
├── .env.example              # ANTHROPIC_API_KEY=
├── app/
│   ├── main.py               # FastAPI app, routes, serves UI
│   ├── config.py             # settings (delays, max pages, model)
│   ├── db.py                 # SQLite models (sites, jobs, records, cache)
│   ├── fetcher.py            # httpx client: throttle, retry, cache, robots.txt
│   ├── cleaner.py            # HTML → compact form for the AI; value normalization
│   ├── recipe_builder.py     # Claude call → recipe JSON
│   ├── extractor.py          # apply recipe to HTML → rows
│   ├── jobs.py               # background crawl runner + progress
│   └── export.py             # CSV / XLSX
├── static/
│   ├── index.html            # single-page UI (Alpine.js + simple CSS)
│   └── app.js
└── data/
    └── scraper.db            # created at runtime
```

---

## 6. Backend API

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/analyse` | `{url, fields}` → AI recipe + preview rows |
| POST | `/api/preview` | `{url, recipe}` → preview rows (after a manual edit) |
| POST | `/api/jobs` | `{url, recipe, max_pages}` → start crawl, returns `job_id` |
| GET | `/api/jobs/{id}` | status, pages done, row count, errors |
| GET | `/api/jobs/{id}/records` | the extracted rows |
| GET | `/api/jobs/{id}/export?fmt=csv\|xlsx` | download |
| GET | `/api/jobs` | job history |
| GET/PUT | `/api/sites/{domain}/recipe` | view or edit a saved recipe |

---

## 7. Tech stack

- **Python 3.14**, **FastAPI** + **Uvicorn**
- **httpx** (fetching), **selectolax** (fast HTML parsing)
- **SQLite** via the standard library `sqlite3` (no extra setup)
- **anthropic** SDK, latest Claude model (configurable in `config.py`)
- **pandas** + **openpyxl** for export
- Frontend: a single HTML page with **Alpine.js** from a CDN and no build step

Run with one command: `uvicorn app.main:app --reload` → open `http://localhost:8000`

---

## 8. Build milestones

| # | Milestone | Output |
|---|---|---|
| 1 | Site research | Inspect IndiaMART live: is the data in the HTML or a JSON API? What does pagination look like? |
| 2 | Core engine | `fetcher`, `extractor`, `db` + a hand-written IndiaMART recipe working from the command line |
| 3 | AI recipe builder | `recipe_builder` generates a recipe that matches the hand-written one |
| 4 | Backend API | Endpoints above, background jobs, export |
| 5 | Web UI | Analyse → preview → run → results/export screens |
| 6 | Second site test | Run on a different site (e.g. TradeIndia) with no code changes |
| 7 | Polish | README, error messages, empty/blocked states |

---

## 9. Safety and good behavior (built in)

- Checks `robots.txt` by default and warns if it disallows a path.
- At most one request every 2–5 seconds per site, with a page cap per job (default 10).
- Caches responses, so a re-preview doesn't fetch the page again.
- **Stops and reports** if it hits a login wall, CAPTCHA or 403/429. It never tries to get around them.
- Collects public listing data only. No contact details hidden behind login.
- Meant for local, personal use, not to be exposed publicly.

---

## 10. Open questions for you

1. **API key**: do you have an Anthropic API key? (If not, the draft still works with manually written recipes. The AI step just stays off.)
2. **Fields for IndiaMART**: the default is `name, price, unit, min_order, seller, city, rating, url`. Anything to add?
3. **Second test site**: TradeIndia, Amazon.in, Flipkart, or another site you choose?
