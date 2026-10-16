# Universal Scraper: Safe Scraping Plan

How to scrape, including pages behind your own login, **without putting your machine, your accounts,
your data or the websites at risk**. This plan adds security features to the existing app in six
phases. Each phase can be built and used on its own.

"Safe" means two things here:

1. **Protecting you:** your passwords, login sessions, API keys, scraped data, your computer, and your
   accounts' standing with the sites you use.
2. **Protecting the sites and the people in the data:** no getting around access controls, following
   each site's rules, collecting only what you need, and handling personal data lawfully.

The app never solves CAPTCHAs automatically, rotates IPs to dodge blocks, fakes browser fingerprints
or ignores robots.txt. Those defeat a site's security, which is the opposite of this plan's goal.

---

## 1. What can go wrong (threat model)

| # | Risk | Where it comes from today | Impact | Fixed in |
|---|---|---|---|---|
| T1 | **API key leaks** | `.env` holds the Groq key; the key was also pasted into a chat | Someone uses your quota or bills | S0, S1 |
| T2 | **Login sessions stolen** | (new) saved cookies for logged-in sites | Anyone with the file can act as you on that site | S2 |
| T3 | **Password exposure** | (new) if the app ever typed or stored passwords | Account takeover | S2: the app never handles passwords |
| T4 | **The app is used to reach your local network (SSRF)** | The fetcher fetches any URL, including `http://192.168.1.1/` or `http://localhost:…`, and follows redirects there | Router admin pages, local services or cloud metadata read through the app | S1 |
| T5 | **Other websites control your local app** | A page open in your normal browser sends requests to `localhost:8000` (CSRF, DNS rebinding) | Jobs started or data read without you knowing | S1 |
| T6 | **Malicious content in scraped data** | Values like `=HYPERLINK(...)` or `=cmd\|…` land in CSV / Excel | Formula runs when the export is opened in Excel | S1 |
| T7 | **Hostile pages in the headless browser** | Rendered pages can try downloads, pop-ups, service workers | Unwanted files on disk, tracking | S1 |
| T8 | **Prompt injection into the AI** | Scraped HTML is sent to Groq / Claude and can contain "ignore your instructions…" | Bad recipe; slow regex (ReDoS) freezes a job | S1, S4 |
| T9 | **Personal data leaves your machine** | Logged-in pages (names, phones, emails) sent to a cloud AI | Privacy breach, DPDP Act exposure | S4 |
| T10 | **Scraped personal data kept too long or shared** | SQLite file and exports hold everything forever | Data breach if the laptop or file leaks | S4 |
| T11 | **Your account gets banned** | Too many requests while logged in | Lose access to a site you rely on | S3 |
| T12 | **Breaking a site's rules or the law** | Scraping without checking terms; collecting personal data | Legal trouble, IP or account bans | S5 |
| T13 | **Vulnerable dependencies** | FastAPI, Playwright, httpx and others get security fixes | Known exploits | S1 |
| T14 | **Secrets in logs** | Cookies or headers printed in errors | Session theft from log files | S1, S2 |

---

## 2. Phases

| Phase | Goal | Size |
|---|---|---|
| S0 | Immediate hygiene | 15 min |
| S1 | Lock down the app itself | ½–1 day |
| S2 | Safe logged-in sessions | 1 day |
| S3 | Handle blockers without circumventing them | ½–1 day |
| S4 | Protect personal data | 1 day |
| S5 | Compliance workflow per site | ½ day |
| S6 | Security tests and checklist | ½ day |

---

### S0: Immediate hygiene (do now)

| # | Action | Done when |
|---|---|---|
| S0.1 | **Rotate the Groq API key**: it was pasted into a chat. Create a new key in the Groq console, delete the old one, update `.env`. | The old key returns "invalid API key" |
| S0.2 | Confirm `.env`, `data/` and any session files are git-ignored (they are) and were never committed. | `git log --all -- .env data/` is empty |
| S0.3 | Turn on **BitLocker** (Windows disk encryption) for the drive holding `data/`. Protects everything at rest if the laptop is lost. | BitLocker shows "On" for D: |

---

### S1: Lock down the app

| # | Change | How | Done when |
|---|---|---|---|
| S1.1 | **Block local and private network targets (SSRF guard)** | Before every request, and after every redirect, resolve the host and refuse loopback (127.0.0.0/8, ::1), private (10/8, 172.16/12, 192.168/16, fc00::/7), link-local (169.254/16, including cloud metadata), and non-http(s) schemes. Same check inside Playwright via request routing. An explicit allow-list in `.env` covers intentional local targets. | Fetching `http://localhost:8000/api/jobs` or `http://192.168.1.1/` through the app is refused, including via a redirect |
| S1.2 | **Only answer requests meant for this app** | `TrustedHostMiddleware` (only `localhost` / `127.0.0.1` host headers, which stops DNS rebinding); refuse cross-site requests by checking the `Origin` header on POST/PUT/DELETE; no CORS headers. Optional: a random access token created at first run, which `run.py` puts in the URL it opens. | A test page on another origin cannot start a job or delete data |
| S1.3 | **Never listen on the network by accident** | `run.py --host 0.0.0.0` prints a warning and requires `--i-understand-this-exposes-the-app`. | Default stays 127.0.0.1 |
| S1.4 | **Safe exports** | Prefix cells that start with `= + - @ \t \r` with `'` in CSV and Excel (OWASP CSV-injection guidance). Keep numbers numeric. | A value `=HYPERLINK("http://x")` opens as plain text in Excel |
| S1.5 | **Regex and parsing time limits** | Run recipe regexes with the `regex` module's `timeout=` (e.g. 50 ms per value); reject patterns with nested quantifiers. Cap HTML size per page (e.g. 10 MB) and items per page. | A catastrophic regex fails the field, not the job |
| S1.6 | **Harden the headless browser** | `accept_downloads=False`, `service_workers="block"`, no pop-ups (close new pages), a fresh context per site, never the user's real Chrome profile, no extensions, Chromium sandbox left on. Block requests to private IPs (S1.1). | A page that triggers a download saves nothing |
| S1.7 | **No secrets in logs or errors** | A log filter that redacts `cookie`, `authorization`, `set-cookie`, `api_key`, `gsk_…`, `sk-ant-…`. API errors show a short message, not raw headers. | Grepping logs for "cookie" finds nothing |
| S1.8 | **Dependency hygiene** | Pin exact versions in `requirements.lock` (`pip freeze`); run `pip-audit` (in tests or a `check.py`); keep Playwright / Chromium updated. | `pip-audit` reports no known vulnerabilities |
| S1.9 | **Secret scanning before commits** | A git pre-commit hook (or `detect-secrets`) blocks commits that contain API keys or cookies. | Committing a file with `gsk_…` is refused |

---

### S2: Safe logged-in sessions

**Principle: the app never sees your password.** You log in yourself in a real, visible browser window;
the app only keeps the resulting session cookie, encrypted, and uses it at a polite pace.

| # | Change | How | Done when |
|---|---|---|---|
| S2.1 | **"Log in to this site" flow** | The UI button opens a **visible** Chromium window (Playwright, `headless=False`) on the site's login page. You log in (password, OTP, 2FA) yourself, then click "I'm logged in" in the app. The app saves only that window's `storage_state` (cookies + local storage) for that domain. | You can log in to a site with 2FA, and the password never touches the app's code, logs or database |
| S2.2 | **Encrypt sessions at rest** | Encrypt each session with `cryptography` (Fernet / AES); the key lives in **Windows Credential Manager** via `keyring`, not on disk next to the data. Files in `data/sessions/<domain>.bin`, git-ignored. | Copying `data/` to another PC does not give working sessions |
| S2.3 | **Scope sessions tightly** | A session is used only for its own domain (and subdomains you confirm), only for jobs you start, in an isolated browser context. Plain-HTTP fetches attach only that domain's cookies. | A logged-in IndiaMART session is never sent to any other site |
| S2.4 | **Expiry and logout** | Detect logged-out pages (login form present, redirect to /login): pause the job and ask you to log in again. Sessions older than N days (default 7) are dropped. A "Forget session" button deletes the file and the key entry. | An expired session pauses the job instead of scraping the login page |
| S2.5 | **Use the right account** | Show which account a session belongs to (from the page, e.g. "Logged in as …"). Recommend a separate account for automation where the site allows it, so a ban never hits your main account. | The site card shows the logged-in account name |
| S2.6 | **Sessions never go to the AI** | Recipe building on logged-in pages uses redacted HTML (S4.2), and no cookie or header is ever part of a prompt. | Prompt logs contain no cookies and no personal data |

---

### S3: Handle blockers without circumventing them

| # | Change | How | Done when |
|---|---|---|---|
| S3.1 | **Detect challenge pages reliably** | Extend detection (already in `fetcher.py` / `browser.py`) for reCAPTCHA, hCaptcha, Turnstile, Cloudflare and Akamai challenge pages, login walls, and "access denied" pages. | Each type is recognised in tests with saved samples |
| S3.2 | **Pause, don't fail: human in the loop** | A job that hits a challenge goes to status **"needs you"**. The UI shows an "Action needed" banner; one click opens that page in a visible window where **you** solve it, then the job resumes with the same browser context. A second challenge within 10 minutes stops the job and puts the site on cool-down. | Solving a CAPTCHA by hand lets a paused job continue; repeated challenges stop it |
| S3.3 | **Adaptive politeness** | Delays grow automatically on slow responses, 429s or challenges, and recover slowly. `Retry-After` is honoured. | After a 429 the next request waits at least the server's `Retry-After` |
| S3.4 | **Per-site budgets** | Daily page limit per site (default 500, editable), and a stricter default for logged-in sessions (e.g. 200). A site in cool-down (after a block) can't be scraped until the time passes. | The 501st page of the day for a site waits until tomorrow |
| S3.5 | **Honest identification (optional)** | Per-site option to send an honest user agent, `UniversalScraper/0.2 (+contact email)`, for sites that welcome identified bots. | Setting saved per site |

**Explicitly not built:** CAPTCHA-solving services or models, proxy or IP rotation to avoid bans,
fingerprint spoofing or "stealth" browser plugins, ignoring robots.txt, creating accounts automatically.

---

### S4: Protect personal data

| # | Change | How | Done when |
|---|---|---|---|
| S4.1 | **Collect less (data minimisation)** | Each job shows the fields it collects. Fields that look personal (phone, email, person name, address) are **off by default** and must be ticked with a purpose ("contacting suppliers I have a relationship with"). | A recipe with a phone field shows a warning and needs an explicit opt-in |
| S4.2 | **Redact before the AI** | For logged-in pages, or when personal data is detected, replace text with placeholders before sending HTML to Groq or Claude (`<span class="phone">[TEXT 12 chars]</span>`): the AI only needs structure to write selectors. Phone and email patterns are always masked. An option to use only local recipe building (no AI) for sensitive sites. | Captured prompts for a logged-in page contain no phone numbers or emails |
| S4.3 | **Mask in the UI and exports** | Detected phone numbers and emails are masked (`98******21`) unless "show personal data" is on for that job. Exports carry a header row note when personal data is included. | Default exports have masked contacts |
| S4.4 | **Retention** | Page cache auto-deleted after 24 h (already a TTL; add actual deletion). Job data auto-deleted after N days (default 30, per job "keep"). A "Delete all data for this site" button. | Old cache rows disappear without manual clean-up |
| S4.5 | **Encryption at rest for sensitive jobs** | Optional: records of logged-in jobs stored encrypted with the same keyring key as sessions (S2.2). BitLocker (S0.3) covers the rest. | The records of an encrypted job are unreadable in the raw SQLite file |
| S4.6 | **Audit log** | An append-only log of what was fetched, when, by which job, logged in or not, and what was exported. Viewable in the UI. | You can answer "what did we collect from site X last month?" |

---

### S5: Compliance workflow per site

| # | Change | How | Done when |
|---|---|---|---|
| S5.1 | **Site policy card** | The first time a domain is used, a card asks you to confirm: link to its Terms of Use (opened for you), "Terms allow automated access / I have permission / personal use only", purpose of the scrape, whether it involves logging in. Stored with the site. | A new site can't run a job until the card is filled in |
| S5.2 | **Show robots.txt and API options** | The card shows robots.txt rules for the URL, and a reminder to check for an official API or data feed. | The card lists allowed / blocked paths for the URL |
| S5.3 | **Logged-in scraping needs a stronger yes** | Using a session requires ticking "This is my own account and I'm allowed to access this data automatically". | Saving a session without this tick is refused |
| S5.4 | **Opt-out list** | Domains you (or a site owner who contacted you) mark as "do not scrape" are refused by the fetcher. | A blocked domain fails at fetch time with a clear message |

---

### S6: Security tests and checklist

| # | Change | Done when |
|---|---|---|
| S6.1 | Tests: SSRF guard (localhost, private IPs, IPv6, redirect to private, DNS names resolving to private), Host and Origin checks, CSV-injection escaping, regex timeout, redaction of phones / emails, session encryption round-trip, session never sent to another domain | `pytest` covers each threat T1–T14 that has code |
| S6.2 | `python check.py`: runs tests, `pip-audit`, the secret scan, and checks BitLocker status and that `.env` is not tracked | One command prints a pass / fail security report |
| S6.3 | `SECURITY.md`: what the app does and doesn't do, where secrets and data live, how to rotate keys and wipe data | File exists and is linked from the README |

---

## 3. Build order

```
S0 (now: rotate key, BitLocker)
 └─► S1 lock down the app ──► S6.1 tests for S1
       └─► S2 safe sessions ──► S3 pause-and-resume on challenges
             └─► S4 personal data protection ──► S5 site policy card ──► S6.2/S6.3
```

S1 comes first because S2 adds valuable secrets (sessions); the app must be locked down before it holds
them. S3 depends on S2's visible-browser plumbing. S4 and S5 can be built in parallel after S2.

## 4. Where things will live

| Item | Location | Protection |
|---|---|---|
| AI API keys | `.env` | git-ignored; S1.7 redaction; rotate on exposure |
| Session encryption key | Windows Credential Manager (`keyring`) | Tied to your Windows login |
| Login sessions | `data/sessions/*.bin` | Encrypted (S2.2), git-ignored |
| Scraped data, cache | `data/scraper.db` | Retention (S4.4), optional encryption (S4.5), BitLocker |
| Passwords | **Nowhere**: you type them into the site yourself | — |
| Audit log | `data/audit.log` | Append-only, git-ignored |

## 5. Open questions

1. **Which sites need logins?** The S2 flow differs a little between simple cookie logins and sites with
   OTP / SSO.
2. **Personal data:** do you need contact details at all? If not, S4.1 can block personal fields entirely.
3. **AI on logged-in pages:** OK with redacted HTML going to Groq, or should logged-in sites use manual
   recipes only?
4. **Retention defaults:** 24 h for cache and 30 days for job data, or different?
