# Security

What Universal Scraper does to keep **you**, **your accounts**, **your data** and **the websites you
scrape** safe, where secrets live, and what to do when something goes wrong. The design and threat
model are in [PLAN-security.md](PLAN-security.md). Run `python check.py` for a live report.

## What the app will never do

- Solve, bypass or outsource CAPTCHAs. When one appears, the job **pauses** and *you* solve it in a
  visible browser window, like any visitor. A second challenge soon after stops the job and puts the
  site on cool-down.
- Rotate IPs or proxies, fake browser fingerprints, or use "stealth" plugins to avoid detection.
- Ignore robots.txt, or keep going after HTTP 401 / 403 / 429 or an access-denied page.
- Type, store or log your passwords. You log in yourself in a real browser window.
- Create accounts, or log in with accounts that are not yours.

## Where things live

| Item | Location | Protection |
|---|---|---|
| AI API keys (Groq / Anthropic) | `.env` | Git-ignored; redacted from logs and errors; pre-commit secret scan |
| Encryption key | Windows Credential Manager (`UniversalScraper` / `vault-key`) | Tied to your Windows login; never on disk |
| Login sessions (cookies) | `data/sessions/<domain>.bin` | Encrypted (Fernet / AES-128 + HMAC); expire after `SCRAPER_SESSION_MAX_AGE_DAYS` (7) |
| Results of logged-in jobs | `data/scraper.db` (records table) | Encrypted with the same key |
| Other results and the page cache | `data/scraper.db` | Cache deleted after 24 h; jobs after 30 days unless *kept* |
| Activity log | `data/audit.log` | Append-only; no cookies, passwords or page content |
| Passwords | Nowhere | You type them into the site yourself |

Turn on **BitLocker** for the drive holding `data/` so everything is encrypted at rest if the
computer is lost.

## Protections, by threat

| Threat | Protection | Code |
|---|---|---|
| The app reaches your own PC or local network (SSRF) | Every URL and every redirect hop is resolved and refused if it is loopback, private, link-local or reserved; the headless browser checks every request too. Exceptions only via `SCRAPER_ALLOW_HOSTS`. | `app/netguard.py`, `fetcher._get`, `browser._route` |
| Another website drives your local app (CSRF / DNS rebinding) | Only `localhost` Host headers are answered; POST / PUT / DELETE from another origin are refused; strict CSP, no framing, `no-store` on API responses; API docs disabled | `app/main.py` |
| The app is exposed on your network | `run.py` refuses `--host 0.0.0.0` without `--i-understand-this-exposes-the-app` | `run.py` |
| Session cookies leak to another site | Cookies are attached only to hosts in the session's scope, re-checked on every redirect hop | `sessions.cookie_header`, `fetcher._get` |
| Session file copied | Encrypted with a key held by the OS credential store | `app/vault.py`, `app/sessions.py` |
| Malicious formulas in exports | Cells starting with `= + - @` (and tab / CR) are prefixed with `'` in CSV and Excel | `app/export.py` |
| Catastrophic regex (ReDoS) from an AI recipe | Nested quantifiers rejected; every regex runs with a 50 ms timeout | `extractor.check_regex`, `_apply_regex` |
| Huge or hostile pages | 10 MB page cap, 2,000 items per page; browser: no downloads, no service workers, pop-ups closed, fresh profile | `fetcher._check_size`, `app/browser.py` |
| Prompt injection via scraped HTML | AI output is constrained to a JSON schema of selectors; every recipe is validated and previewed on the real page before use | `recipe_builder`, `extractor.normalize_recipe` |
| Personal data sent to the AI | Phones and emails always masked in prompts; pages fetched with a login session are reduced to their text *shape*; AI can be turned off per site | `privacy.redact_html`, site setting *Allow the AI* |
| Collecting personal data without a reason | Phones / emails masked before storing unless you tick the field and give a purpose; hidden in the UI and exports unless you choose to show them | `privacy`, `jobs._run`, `/api/jobs/{id}/records` |
| Account bans | Per-site daily page budget (500, or 200 when logged in), adaptive backoff, `Retry-After` honoured, cool-down after 429 / repeated challenges | `fetcher` |
| Scraping a site you are not allowed to | Site rules card (Terms URL, basis, purpose) must be confirmed before the first fetch; logging in needs two extra confirmations; do-not-scrape list | `main._require_policy`, `fetcher.is_opted_out` |
| Secrets in logs | Log filter redacts API keys, cookies, bearer tokens, `?token=` parameters | `app/logsafe.py` |
| Secrets committed to git | `.githooks/pre-commit` runs `tools/secret_scan.py`; `.env`, `data/`, `*.db`, `*.bin` are ignored and refused | `tools/secret_scan.py` |
| Vulnerable dependencies | Exact versions in `requirements.lock`; `pip-audit` in `check.py` | `check.py` |

## Routine checks

```bash
python check.py
```

Runs the test suite, the secret scan, `pip-audit`, checks that `.env` / `data/` are not in git, that
the pre-commit hook is on, and reports BitLocker status.

## If something goes wrong

| Situation | What to do |
|---|---|
| An API key was shared, pasted or committed | Revoke it in the provider console (Groq: console.groq.com/keys), create a new one, update `.env`. If it was committed, rewrite the history too. |
| You think a session leaked | *Sites & sessions → Forget session*, then log out of all sessions on that website (most sites have "log out everywhere") and change the password there. |
| You want to wipe everything for a site | *Sites & sessions → Delete data* (jobs, results, cache, session). |
| You want to wipe everything | Stop the app, delete `data/`, and remove the `UniversalScraper` entry in Windows Credential Manager. |
| A site owner asks you to stop | Mark the site *Block* (do-not-scrape), delete its data. |
