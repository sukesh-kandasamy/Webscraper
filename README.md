# Universal Scraper (draft v0.1)

A local web app that extracts listings from websites. You give it a page URL and the fields you
want. It finds the right CSS selectors (a **recipe**), shows you a preview, then crawls and lists
the results with CSV / Excel export.

Built-in recipes: **IndiaMART** and **TradeIndia** category pages. On any other site an AI model
writes the recipe for you, once per site: **Groq `openai/gpt-oss-120b`** by default, or Claude.
Without a key you can edit the starter recipe by hand.

## Setup

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium   # optional: browser rendering (~150 MB)
copy .env.example .env        # then add GROQ_API_KEY=... (optional, enables AI recipes)
```

## Run

```bash
python run.py
```

It starts the server and opens http://localhost:8000 in your browser. It uses the project's `.venv`
automatically, whichever Python you start it with. Options: `--port 8080`, `--reload` (restart when code
changes), `--no-browser`.

## How to use

1. **Paste URLs**, one per line. Use *listing/category* pages, e.g.
   - `https://dir.indiamart.com/impcat/pvc-pipes.html`
   - `https://www.tradeindia.com/manufacturers/upvc-pipe-fittings.html`

   Both sites block their **search** pages in robots.txt, so the app refuses them. Category pages
   are allowed. Add several category URLs to one job to cover more products.
   **Columns are optional.** Leave the box empty and the AI decides what kind of page it is (jobs,
   e-commerce, real estate, stocks / finance tables, directories, news, events) and picks every
   important column itself. Type columns (e.g. `salary, experience`) to make sure they are included;
   with *Let the AI also find every other important column* ticked, it adds the rest too.
2. **Analyse page.** The app uses the saved recipe for that site if one works and has the columns you
   typed; otherwise the AI writes a new one. *Re-analyse with AI* ignores the saved recipe.
3. **Check the preview.** The page type and the columns are shown as chips: click **×** to drop a
   column (instant, no AI), or type a name in *add a column…* and the AI finds it (it tells you when
   the page doesn't show it). The fill-rate bars show how often each column was found. Columns the AI
   added itself that are mostly empty or duplicate another column are left out automatically.
4. **Approve & scrape.** The job runs in the background (2–5 s between requests) and the results
   appear as they arrive. Search, sort, and download CSV or Excel.

## Logged-in pages, CAPTCHAs and personal data

- **Logging in:** *Sites & sessions → Log in* first asks you to confirm it is your own account and
  that the site allows automated access while logged in, then opens a real browser window. You log in yourself
  (password, OTP, 2-step); the app only keeps the session cookie, encrypted with a key held in Windows
  Credential Manager, for that site only. Sessions expire after 7 days; *Forget session* deletes one.
  Jobs that use a session store their results encrypted and have a lower daily page budget.
- **CAPTCHAs and bot checks** are never solved automatically. A job that meets one pauses as
  **needs you**: click *Open to solve*, solve it in the window, and the job continues. If a second
  check appears within 10 minutes the job stops and the site cools down for an hour.
- **Personal data** (phone numbers, emails) is masked by default, in what is stored, shown and sent
  to the AI. To collect a field unmasked, tick it before starting the job and say why.
- **Activity log** lists every fetch, export, login and settings change.

Details: [SECURITY.md](SECURITY.md). Check everything with `python check.py`.

## Recipes

A recipe is plain JSON. See [app/extractor.py](app/extractor.py) for every option.

```json
{
  "item_selector": "div.product-info-cnt",
  "fields": [
    {"name": "name",  "selector": "a.title-url", "attr": "text"},
    {"name": "url",   "selector": "a.title-url", "attr": "href"},
    {"name": "price", "selector": "p.price-text", "regex": "([\\d,.]+)\\s*INR"},
    {"name": "min_order", "kind": "kv", "label_selector": ".spec-title",
     "value_selector": ".spec-value", "label": "MOQ"}
  ],
  "pagination": {"type": "next_link", "selector": "li.next a"}
}
```

Pagination types: `none`, `next_link` (follow a "next" anchor), `url_param` (e.g. `?page=2`),
`click` (press a "Load more" button, `selector` required) and `scroll` (infinite scroll). The last two
run in the browser; *Max pages per URL* is then the number of clicks / scrolls.

## Browser rendering (JavaScript sites)

Plain HTTP is always tried first. If the page comes back without listings, the app renders it in
headless Chromium (Playwright) and tries again; when that works, the site's **Page rendering** setting
is switched to *Always* so later fetches go straight to the browser. You can set it to *Plain HTTP only*
or *Always* yourself under *Site settings*. The browser follows the same rules as plain fetching:
robots.txt, the per-site delay, and stopping on CAPTCHA / challenge pages.

Tested on: `quotes.toscrape.com/js/` (JavaScript-only), `quotes.toscrape.com/scroll` (infinite scroll),
`webscraper.io/test-sites/e-commerce/more/...` (Load more button) and IndiaMART category pages (load
more products on scroll).

## Jobs

At most `SCRAPER_MAX_JOBS` (default 2) jobs crawl at the same time; others wait as *queued* and can be
stopped while waiting. Every fetched page is recorded, so a job interrupted by a restart resumes where
it stopped without duplicating rows.

## Project layout

| Path | What it does |
|---|---|
| `app/main.py` | FastAPI routes + serves the UI; Host / Origin checks, security headers |
| `app/netguard.py` | Refuses local / private network targets (SSRF guard) |
| `app/sessions.py` | Encrypted login sessions; visible browser windows for logging in / solving checks |
| `app/vault.py` | Encryption with a key from the OS credential store |
| `app/blockdetect.py` | Recognises CAPTCHA, challenge, access-denied and login pages |
| `app/privacy.py` | Detects and masks phones / emails; redacts page text before AI calls |
| `app/audit.py`, `app/logsafe.py` | Activity log; secret redaction in logs |
| `app/fetcher.py` | HTTP client: robots.txt, per-site throttling, retries, cache, block detection; `fetch_smart` picks HTTP or browser |
| `app/browser.py` | Headless Chromium rendering (Playwright): wait for the page, click "load more" / scroll, detect challenge pages |
| `app/robots.py` | robots.txt parser with `*` / `$` wildcard support |
| `app/extractor.py` | Applies a recipe to HTML; pagination; field fill-rate report |
| `app/recipe_builder.py` | Finds repeated listing blocks, asks the AI (Groq or Claude) for a recipe, tests it, retries once with feedback |
| `app/jobs.py` | Background crawl jobs |
| `app/db.py` | SQLite: recipes, jobs, records, page cache (`data/scraper.db`) |
| `app/recipes/*.json` | Built-in recipes, loaded at startup |
| `static/` | Single-page UI (Alpine.js) |
| `tests/` | `python -m pytest tests` (runs offline on saved pages; `test_security.py` covers each threat) |
| `check.py`, `tools/secret_scan.py`, `.githooks/` | Security report; secret scanning (also as a pre-commit hook) |

## Settings (`.env`)

| Setting | Default | Notes |
|---|---|---|
| `SCRAPER_AI_PROVIDER` | `groq` if `GROQ_API_KEY` is set, else `anthropic` | |
| `GROQ_API_KEY` / `SCRAPER_GROQ_MODEL` | — / `openai/gpt-oss-120b` | |
| `ANTHROPIC_API_KEY` / `SCRAPER_CLAUDE_MODEL` | — / `claude-opus-5-5` | |
| `SCRAPER_AI_PROMPT_CHARS` | 11000 (Groq), 60000 (Claude) | Size of the whole AI prompt. Long links are shortened, and if the provider still says "too large" the app trims the sample and retries by itself |
| `SCRAPER_MIN_DELAY` / `SCRAPER_MAX_DELAY` | 2 / 5 s | Wait between requests to the same site |
| `SCRAPER_MAX_PAGES` | 10 | Per start URL (or scroll / click rounds) |
| `SCRAPER_MAX_JOBS` | 2 | Jobs crawling at the same time |
| `SCRAPER_DAILY_BUDGET` / `_LOGGED_IN` | 500 / 200 | Pages per site per day |
| `SCRAPER_SESSION_MAX_AGE_DAYS` | 7 | Saved logins expire after this |
| `SCRAPER_JOB_RETENTION_DAYS` | 30 | Finished jobs are deleted after this unless kept |
| `SCRAPER_ALLOW_HOSTS` / `SCRAPER_BLOCKED_DOMAINS` | — | Network-guard exceptions / do-not-scrape list |
| `SCRAPER_CACHE_TTL_HOURS` | 24 | Page cache |

## What's next

- Security and logged-in scraping: [PLAN-security.md](PLAN-security.md) (SSRF guard, safe exports,
  encrypted login sessions, pause-and-resume on CAPTCHAs, personal-data protection).
- Features: [PLAN-v2.md](PLAN-v2.md): browser rendering for JavaScript sites, detail-page crawling, clean numeric
output, recipe auto-repair, scheduled re-runs with price history, a point-and-click field picker.

## Ground rules built in

- robots.txt is checked before every fetch, and disallowed pages are refused.
- On HTTP 401/403/429 or a CAPTCHA page, the job stops instead of trying to get around it.
- Only public listing data is collected. Phone numbers and emails behind login are not.
- For local, personal use. Don't expose this server publicly.
