"""Architecture diagrams in docs/architecture/: drawn here as SVG, saved as PNG by headless Chromium (Playwright).

    python scripts/architecture_diagrams.py [name ...]    names: overview stack guided agent data (default: all)

Update the boxes and labels here when the architecture changes, then run it again.
"""
import sys
import tempfile
from html import escape
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "architecture"
FONT = (ROOT / "frontend/public/fonts/roboto-latin-wght-normal.woff2").as_uri()

INK, MUTED, HAIR = "#1d1d1f", "#5f6368", "#d5d8dd"
# role -> (fill, stroke, title colour)
ROLE = {
    "browser":  ("#f6f6f7", "#bfc3c9", "#2b2f36"),
    "web":      ("#eef3fb", "#9fb7e2", "#1f4fa3"),
    "engine":   ("#ffffff", "#c7ccd4", "#2b2f36"),
    "agent":    ("#f4f0fc", "#bba8e8", "#5b3cc4"),
    "security": ("#fdf2f1", "#ebb4ae", "#a8261c"),
    "storage":  ("#eff7f1", "#a9d3b4", "#1e7a3c"),
    "external": ("#fff6ea", "#f0c98e", "#9a5400"),
    "plain":    ("#f7f8fa", "#d5d8dd", "#2b2f36"),
}


def t(x, y, s, size=13, weight=400, color=INK, anchor="start", italic=False, mono=False):
    fam = "Consolas, 'Cascadia Mono', monospace" if mono else "Roboto, 'Segoe UI', Arial, sans-serif"
    style = " font-style='italic'" if italic else ""
    return (f"<text x='{x}' y='{y}' font-family=\"{fam}\" font-size='{size}' font-weight='{weight}' "
            f"fill='{color}' text-anchor='{anchor}'{style}>{escape(s)}</text>")


def box(x, y, w, h, role="engine", title="", lines=(), r=10, title_size=15, line_size=13, lh=19, dash=False,
        pad=14, header=None):
    fill, stroke, tcol = ROLE[role]
    d = " stroke-dasharray='6 5'" if dash else ""
    out = [f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='{r}' fill='{fill}' stroke='{stroke}' stroke-width='1.4'{d}/>"]
    cy = y + pad + title_size
    if header:                      # small uppercase label above the title
        out.append(t(x + pad, y + pad + 9, header.upper(), 10.5, 600, tcol))
        cy += 14
    if title:
        out.append(t(x + pad, cy, title, title_size, 600, tcol))
        cy += lh + 4
    for ln in lines:
        mono = ln.startswith("`")
        out.append(t(x + pad, cy, ln.strip("`"), line_size, 400, MUTED if not mono else INK, mono=mono))
        cy += lh
    return "".join(out)


def arrow(points, label="", lx=None, ly=None, color=MUTED, both=False, anchor="middle", dash=False, lsize=12):
    pts = " ".join(f"{x},{y}" for x, y in points)
    d = " stroke-dasharray='5 4'" if dash else ""
    start = " marker-start='url(#ah)'" if both else ""
    out = [f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='1.6'{d} marker-end='url(#ah)'{start}/>"]
    if label:
        for i, part in enumerate(label.split("\n")):
            out.append(t(lx, ly + i * 15, part, lsize, 500, INK, anchor))
    return "".join(out)


def svg(w, h, body, title, subtitle=""):
    head = t(40, 52, title, 26, 700) + (t(40, 80, subtitle, 14, 400, MUTED) if subtitle else "")
    defs = ("<defs><marker id='ah' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='7' markerHeight='7' "
            f"orient='auto-start-reverse'><path d='M0,0 L10,5 L0,10 z' fill='{MUTED}'/></marker></defs>")
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}' viewBox='0 0 {w} {h}'>"
            f"<rect width='{w}' height='{h}' fill='#ffffff'/>{defs}{head}{body}</svg>")


def page(svg_markup):
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>@font-face{{font-family:Roboto;"
            f"src:url('{FONT}') format('woff2');font-weight:100 900}} html,body{{margin:0;background:#fff}}</style>"
            f"</head><body>{svg_markup}</body></html>")


# ------------------------------------------------------------------------------------------- 1 · overview
def overview():
    W, H = 1930, 1190
    b = []
    # your browser
    b.append(box(40, 130, 300, 210, "browser", "Your browser", [
        "Edge / Chrome at localhost:8000",
        "Pages: Home, New scrape, Saved",
        "scrapes, Results, Settings, Agent",
        "Alpine.js 3.14 · Tailwind CSS 3.4",
        "Roboto · Material Symbols",
        "Light and dark themes"], header="You"))
    b.append(box(40, 380, 300, 150, "browser", "Visible browser window", [
        "Opens only when you log in to a",
        "site or solve a human check.",
        "Playwright (headed). The app never",
        "sees your password."], dash=True))

    # app process
    b.append(f"<rect x='390' y='110' width='900' height='770' rx='16' fill='#fafbfc' stroke='{HAIR}' stroke-width='1.6'/>")
    b.append(t(412, 140, "APP PROCESS", 11, 700, MUTED) + t(510, 140, "python run.py  ·  .venv  ·  Python 3.14  ·  one process, background threads", 13, 400, MUTED))
    # web layer
    b.append(box(410, 156, 860, 172, "web", "Web layer  ·  uvicorn 0.54 + FastAPI 0.143 (Starlette 1.8, Pydantic 2.14)", [], title_size=15))
    web_cols = [("Security middleware", ["Host + Origin checks", "CSP, frame-ancestors none", "API: no-store cache"]),
                ("Pages (app/main.py)", ["Jinja2 3.1 templates", "/ /scrapes /results/JOB-…", "/settings /agent/runs/RUN-…"]),
                ("JSON API", ["/api/jobs  /api/scrapes", "/api/sites  /api/sessions", "/api/ai  /api/agent/…"]),
                ("Static files", ["/static: CSS, JS, fonts,", "icons (no-cache, ETag)", "no CDN, works offline"])]
    for i, (ti, ls) in enumerate(web_cols):
        b.append(box(424 + i * 211, 200, 199, 114, "engine", ti, ls, title_size=13.5, line_size=12, lh=17, pad=11))

    # engines
    eng = [("Runs  ·  app/runs", ["Background jobs (threads,", "max 2 at once, rest queued)", "Pagination, resume, retry", "Quality report", "Export CSV / Excel", "(openpyxl 3.1)"], "engine"),
           ("Extraction  ·  app/extraction", ["selectolax 1.0 (HTML parse)", "JSON-LD + app state", "Recipe builder: AI writes", "CSS selectors, then checked", "Normalize ₹, %, dates", "Request parser · item pages"], "engine"),
           ("Fetching  ·  app/fetching", ["httpx 0.28 (polite GET)", "Playwright 1.64 (JS pages)", "robots.txt (wildcards)", "Block / CAPTCHA detection", "Per-site pace, daily budget", "Page cache (24 h)"], "engine"),
           ("Agent control  ·  app/agent", ["planner: goal → plan", "guard: checks every action", "runner: starts the worker", "handoff: list → engine", "routes: replay, no AI", "supervisor: check & fix"], "agent")]
    for i, (ti, ls, role) in enumerate(eng):
        b.append(box(410 + i * 220, 352, 200, 214, role, ti, ls, title_size=14, line_size=12.5, lh=18.5, pad=13))

    # security + AI settings
    b.append(box(410, 596, 640, 186, "security", "Security  ·  app/security", [], title_size=14))
    sec = [("vault", "Fernet encryption, key in Windows Credential Manager (keyring)"),
           ("sessions", "your logins, stored encrypted; visible window to log in"),
           ("netguard", "refuses localhost / private-network addresses (SSRF)"),
           ("privacy", "masks phones & emails; redacts pages before the AI sees them"),
           ("audit · logsafe", "append-only activity log · keys never in logs or errors")]
    for i, (k, v) in enumerate(sec):
        b.append(t(426, 646 + i * 25, k, 12.5, 600, ROLE["security"][2], mono=True) + t(548, 646 + i * 25, v, 12.5, 400, MUTED))
    b.append(box(1070, 596, 200, 186, "plain", "AI settings", ["app/ai_settings.py", "Provider + model for", "scrapes, agent, backup", "Test call before saving", "Keys stay in .env"], title_size=14, line_size=12.5, lh=18.5, pad=13))
    b.append(t(410, 812, "Every page fetch, AI call and export is written to the activity log. Records of logged-in runs are stored encrypted.", 12.5, 400, MUTED, italic=True))
    b.append(t(410, 836, "A data purge runs every hour: results older than 30 days (unless kept) and cached pages older than 24 h.", 12.5, 400, MUTED, italic=True))

    # agent worker process
    b.append(f"<rect x='1400' y='110' width='380' height='420' rx='16' fill='#fbfaff' stroke='{ROLE['agent'][1]}' stroke-width='1.6'/>")
    b.append(t(1420, 140, "AGENT WORKER PROCESS", 11, 700, ROLE["agent"][2]) + t(1574, 140, ".venv-agent", 13, 400, MUTED))
    b.append(box(1418, 156, 344, 196, "agent", "browser-use 0.13.11", [
        "Loop: read page → AI picks next",
        "action → app approves → act",
        "Tools limited: search, filter,",
        "sort, read; results_reached",
        "Provider errors named; backup AI",
        "Counts steps, AI calls, tokens"], title_size=14.5, line_size=12.5, lh=18.5))
    b.append(box(1418, 368, 344, 146, "plain", "Headless Edge / Chrome", [
        "Driven over CDP, one run at a time",
        "Only the planned site is allowed",
        "Screenshots each step (Pillow 12)",
        "Exits after 15 min idle"], title_size=14.5, line_size=12.5, lh=18.5))

    # external
    b.append(box(1400, 572, 380, 128, "external", "Websites you scrape", [
        "HTTPS · robots.txt respected", "Polite pauses, daily page budget", "No CAPTCHA solving, no stealth"], header="Internet", title_size=14.5))
    b.append(box(1400, 724, 380, 156, "external", "AI providers (your keys)", [
        "Groq · gpt-oss-120b / 20b", "OpenAI · gpt-5, gpt-5-mini", "Anthropic · Claude (optional)",
        "SDKs: groq 1.7, anthropic 1.15; httpx"], header="Internet", title_size=14.5))

    # build & checks (developer side)
    b.append(box(40, 572, 300, 572, "plain", "Build & checks", [
        "`pytest 9`", "206 tests, no network needed", "",
        "`scripts/check.py`", "pip-audit + security report", "",
        "`scripts/build_css.py`", "Tailwind 3.4 (pytailwindcss 0.4)", "",
        "`scripts/build_ui_assets.mjs`", "Node: Material colour roles,", "Roboto, Material Symbols", "(outputs committed; no npm", "needed to run the app)", "",
        "`.githooks/pre-commit`", "secret scan on every commit", "",
        "`requirements.lock`", "pinned versions, both venvs"], header="Developer", title_size=14.5, line_size=12.5, lh=18.5))

    # storage
    b.append(f"<rect x='390' y='930' width='1390' height='214' rx='16' fill='#f8fcf9' stroke='{ROLE['storage'][1]}' stroke-width='1.6'/>")
    b.append(t(1290, 960, "LOCAL DATA", 11, 700, ROLE["storage"][2]) + t(1380, 960, "data/  ·  on this computer only  ·  never committed to git", 13, 400, MUTED))
    st = [("scraper.db  ·  SQLite", ["10 tables: jobs, records, scrapes,", "sites, page_cache, agent_runs,", "agent_steps, site_usage, …", "Data purge every hour"], 290),
          ("sessions/", ["Login state per site", "Fernet-encrypted", "Expire after 7 days"], 196),
          ("audit.log", ["One JSON line per", "event: fetch, export,", "login, settings"], 196),
          ("agent_shots/", ["JPEG thumbnail per", "agent step", "Deleted with the run"], 196),
          (".env", ["API keys, settings", "Never shown in the", "UI or in logs"], 196),
          ("Credential Manager", ["Windows store with", "the encryption key,", "not next to the data"], 196)]
    x = 408
    for ti, ls, w in st:
        b.append(box(x, 978, w, 150, "storage", ti, ls, title_size=14, line_size=12.5, lh=18.5, pad=13))
        x += w + 16

    # arrows
    b.append(arrow([(340, 230), (388, 230)], "HTTP +\nJSON", 364, 256, both=True))
    b.append(arrow([(190, 340), (190, 378)], "", dash=True))
    b.append(arrow([(1290, 410), (1398, 410)], "starts it\n(token)", 1344, 386))
    b.append(arrow([(1398, 470), (1292, 470)], "local API:\nnext · check ·\nstep · arrived", 1344, 490))
    b.append(arrow([(950, 566), (950, 584), (1350, 584), (1350, 636), (1398, 636)], "HTTPS GET", 1150, 578))
    b.append(arrow([(1590, 514), (1590, 570)], "drives (CDP)", 1600, 548, anchor="start"))
    b.append(arrow([(1762, 254), (1810, 254), (1810, 802), (1782, 802)], "each step:\npage + task\n→ next action", 1818, 300, anchor="start"))
    b.append(arrow([(1290, 690), (1340, 690), (1340, 802), (1398, 802)], "recipes,\nrequests, plans", 1344, 826))
    for x, label in ((510, "SQL (sqlite3)"), (812, "encrypted"), (1024, "append"), (1236, "JPEG per step")):
        b.append(arrow([(x, 880), (x, 976)], label, x + 8, 922, anchor="start"))
    return svg(W, H, "".join(b), "Universal Scraper · system architecture",
               "One computer, one user: a FastAPI app and an agent worker (two Python processes), one SQLite database, "
               "and no cloud service except the websites and the AI providers you choose.")


# ------------------------------------------------------------------------------------------- 2 · tech stack
STACK = [
    ("Pages in your browser", "frontend/", "browser", [
        ("Alpine.js 3.14.1", "Reactive pages, one", "script per page"),
        ("Tailwind CSS 3.4", "Utility CSS, compiled", "to one file"),
        ("Jinja2 3.1.6", "Server-rendered page", "templates, no inline JS"),
        ("Material Symbols 0.48", "Icons as inline SVG", "paths (Apache-2.0)"),
        ("Roboto 5.3 (variable)", "Bundled font,", "works offline"),
        ("Material colour utils 0.4", "Light / dark colour", "roles (build time)")]),
    ("Web server", "app/main.py", "web", [
        ("Python 3.14.7", "Both processes,", "two virtual envs"),
        ("uvicorn 0.54", "ASGI server on", "localhost:8000"),
        ("FastAPI 0.143", "Pages, JSON API,", "routers"),
        ("Starlette 1.8", "Middleware, static", "files, templates"),
        ("Pydantic 2.14", "Request validation", "and limits"),
        ("python-dotenv 1.2", "Settings and keys", "from .env")]),
    ("Scraping engine", "app/fetching · extraction · runs", "engine", [
        ("httpx 0.28", "Polite HTTP fetching", "and AI calls"),
        ("Playwright 1.64", "JS pages, login windows,", "route replay"),
        ("selectolax 1.0", "Fast HTML parsing", "(Lexbor engine)"),
        ("regex 2026.9", "Patterns with", "timeouts"),
        ("pandas 3.0 + openpyxl 3.1", "CSV and Excel", "downloads"),
        ("robots.txt parser", "Own code: * and $", "wildcards")]),
    ("AI", "app/extraction · app/agent", "external", [
        ("groq 1.7", "Groq: gpt-oss-120b,", "gpt-oss-20b, qwen"),
        ("OpenAI over httpx", "gpt-5, gpt-5-mini,", "gpt-4.1 (no SDK)"),
        ("anthropic 1.15", "Claude", "(optional key)"),
        ("Structured output", "Strict JSON schemas,", "checked before use"),
        ("AI models setting", "Provider + model for", "scrapes, agent, backup"),
        ("Privacy redaction", "Phones / emails removed", "before AI sees a page")]),
    ("Agent worker", ".venv-agent · agent_worker/", "agent", [
        ("browser-use 0.13.11", "AI browser agent:", "look, decide, act"),
        ("Edge / Chrome", "Headless, installed", "browser, over CDP"),
        ("Pillow 12.3", "Step screenshots", "(thumbnails)"),
        ("Own virtual env", "~70 packages kept", "out of the app"),
        ("Local API + token", "The only link", "to the app"),
        ("Guard + backup AI", "Every action checked;", "backup on rate limits")]),
    ("Data and security", "app/db.py · app/security", "security", [
        ("SQLite (sqlite3)", "10 tables in one file:", "data/scraper.db"),
        ("cryptography 50", "Fernet: logins and", "logged-in results"),
        ("keyring 25.7", "Key in Windows", "Credential Manager"),
        ("netguard", "No localhost or", "private addresses"),
        ("Security headers", "CSP, Host / Origin", "checks, no-store API"),
        ("Audit log", "JSON line per fetch,", "export, login, change")]),
    ("Build, test and checks", "tests/ · scripts/ · .githooks/", "plain", [
        ("pytest 9.1", "206 tests,", "no network needed"),
        ("pip-audit 2.7", "Known-vulnerability", "check of packages"),
        ("pytailwindcss 0.4", "Tailwind CLI", "without Node"),
        ("Node 24 + npm", "Only for", "build_ui_assets.mjs"),
        ("Pre-commit hook", "Secret scan on", "every commit"),
        ("requirements.lock", "Pinned versions", "for both envs")]),
]


def stack():
    W, row_h, top = 1930, 112, 110
    H = top + len(STACK) * (row_h + 14) + 40
    b = []
    for i, (layer, where, role, items) in enumerate(STACK):
        y = top + i * (row_h + 14)
        fill, stroke, tcol = ROLE[role]
        b.append(f"<rect x='40' y='{y}' width='{W - 80}' height='{row_h}' rx='14' fill='{fill}' stroke='{stroke}' stroke-width='1.4'/>")
        b.append(t(62, y + 44, layer, 17, 700, tcol))
        b.append(t(62, y + 68, where, 12.5, 400, MUTED, mono=True))
        for j, (name, l1, l2) in enumerate(items):
            x = 330 + j * 258
            b.append(f"<rect x='{x}' y='{y + 14}' width='248' height='{row_h - 28}' rx='10' fill='#ffffff' stroke='{stroke}' stroke-width='1.1'/>")
            b.append(t(x + 14, y + 40, name, 14, 600, INK))
            b.append(t(x + 14, y + 61, l1, 12.5, 400, MUTED))
            b.append(t(x + 14, y + 78, l2, 12.5, 400, MUTED))
    return svg(W, H, "".join(b), "Universal Scraper · tech stack",
               "Every layer with its exact version and what it does in this project. Top: what you see; bottom: how it is built and checked.")


# ------------------------------------------------------------------------------------------- 3/4 · flows
def sequence(title, subtitle, parts, steps, W=1930):
    """parts: [(name, sub, role)]; steps: ("msg", a, b, label[, "ret"]) · ("self", a, label) ·
    ("section", label) · ("loop", label) ... ("end",)."""
    n = len(parts)
    left, right = 120, W - 120
    xs = [left + i * (right - left) / (n - 1) for i in range(n)]
    b, y, num, rows = [], 214, 0, []
    for st in steps:
        kind = st[0]
        if kind == "section":
            y += 18
            rows.append(("section", y, st[1])); y += 40
        elif kind == "loop":
            rows.append(("loop", y, st[1])); y += 32
        elif kind == "end":
            rows.append(("end", y)); y += 16
        else:
            num += 1
            rows.append((kind, y, num, st)); y += 46 if kind == "msg" else 58
    H = y + 70
    for i, (name, sub, role) in enumerate(parts):
        fill, stroke, tcol = ROLE[role]
        x = xs[i]
        b.append(f"<line x1='{x}' y1='176' x2='{x}' y2='{H - 40}' stroke='{HAIR}' stroke-width='1.4' stroke-dasharray='6 6'/>")
        b.append(f"<rect x='{x - 106}' y='110' width='212' height='66' rx='12' fill='{fill}' stroke='{stroke}' stroke-width='1.4'/>")
        b.append(t(x, 138, name, 14.5, 700, tcol, "middle") + t(x, 160, sub, 12, 400, MUTED, "middle"))
    loop_start = None
    for row in rows:
        kind = row[0]
        if kind == "section":
            _, yy, label = row
            b.append(f"<rect x='40' y='{yy}' width='{W - 80}' height='28' rx='8' fill='#f2f4f7'/>")
            b.append(t(56, yy + 19, label.upper(), 12, 700, INK))
        elif kind == "loop":
            loop_start = (row[1], row[2])
        elif kind == "end":
            ys, label = loop_start
            x0, x1 = xs[1] - 40, W - 60
            b.append(f"<rect x='{x0}' y='{ys}' width='{x1 - x0}' height='{row[1] - ys}' rx='10' fill='none' "
                     f"stroke='{ROLE['agent'][1]}' stroke-width='1.6' stroke-dasharray='7 5'/>")
            b.append(f"<rect x='{x0}' y='{ys}' width='{len(label) * 7.2 + 26}' height='24' rx='8' fill='{ROLE['agent'][0]}' stroke='{ROLE['agent'][1]}'/>")
            b.append(t(x0 + 12, ys + 16.5, label, 12, 600, ROLE["agent"][2]))
        elif kind == "msg":
            _, yy, k, st = row
            a, c, label = st[1], st[2], st[3]
            ret = len(st) > 4 and st[4] == "ret"
            x1, x2 = xs[a], xs[c]
            dash = " stroke-dasharray='6 5'" if ret else ""
            end = x2 - 8 if x2 > x1 else x2 + 8
            b.append(f"<line x1='{x1}' y1='{yy + 14}' x2='{end}' y2='{yy + 14}' stroke='{MUTED if ret else INK}' "
                     f"stroke-width='1.6'{dash} marker-end='url(#ah)'/>")
            lx = min(x1, x2) + 12
            b.append(f"<circle cx='{lx + 8}' cy='{yy + 2}' r='9' fill='{INK}'/>" + t(lx + 8, yy + 6, str(k), 10.5, 700, "#ffffff", "middle"))
            b.append(t(lx + 24, yy + 6, label, 13, 400, INK))
        elif kind == "self":
            _, yy, k, st = row
            a, label = st[1], st[2]
            x = xs[a]
            b.append(f"<polyline points='{x},{yy} {x + 46},{yy} {x + 46},{yy + 26} {x + 8},{yy + 26}' fill='none' stroke='{INK}' "
                     f"stroke-width='1.6' marker-end='url(#ah)'/>")
            b.append(f"<circle cx='{x + 66}' cy='{yy + 9}' r='9' fill='{INK}'/>" + t(x + 66, yy + 13, str(k), 10.5, 700, "#ffffff", "middle"))
            b.append(t(x + 82, yy + 13, label, 13, 400, INK))
    return svg(W, H, "".join(b), title, subtitle)


def guided():
    parts = [("You", "browser · New scrape", "browser"), ("Web layer", "FastAPI · /api/…", "web"),
             ("Extraction", "request parser · recipes", "engine"), ("AI provider", "Groq / OpenAI / Claude", "external"),
             ("Fetching", "httpx · Playwright", "engine"), ("Website", "the page you gave", "external"),
             ("Background job", "app/runs · thread", "engine"), ("SQLite", "data/scraper.db", "storage")]
    Y, WEB, EX, AI, FE, SITE, RUN, DB = range(8)
    steps = [
        ("section", "1 · Understand what you asked for"),
        ("msg", Y, WEB, "POST /api/understand  (your description)"),
        ("msg", WEB, EX, "request parser"),
        ("msg", EX, AI, "prompt → columns, filters, sort (strict JSON)"),
        ("section", "2 · Find the columns on the page"),
        ("msg", Y, WEB, "POST /api/analyse  (the link)"),
        ("msg", WEB, FE, "fetch the page"),
        ("msg", FE, SITE, "robots.txt + polite GET (Playwright if built by JS)"),
        ("msg", SITE, FE, "HTML (kept in the page cache 24 h)", "ret"),
        ("msg", FE, EX, "page · a CAPTCHA or login wall stops here"),
        ("self", EX, "JSON-LD / app state first; else page outline, personal data redacted"),
        ("msg", EX, AI, "outline + columns → recipe (CSS selectors)"),
        ("self", EX, "test the recipe on the page; fix empty columns; item pages if needed"),
        ("msg", WEB, Y, "columns + sample rows; you rename, add, remove", "ret"),
        ("section", "3 · Collect"),
        ("msg", Y, WEB, "POST /api/scrapes  (save + run)"),
        ("msg", WEB, DB, "save the scrape, create the job (JOB-7K3QX9)"),
        ("msg", WEB, RUN, "start (at most 2 jobs at once, others queued)"),
        ("msg", RUN, FE, "each page, then the next-page link"),
        ("msg", FE, SITE, "GET with pauses and a daily page budget"),
        ("msg", RUN, DB, "typed, filtered rows + quality"),
        ("msg", Y, WEB, "/results/JOB-… polls every 2 s → Data · Quality · Changes · Info"),
        ("msg", WEB, Y, "Download CSV / Excel (pandas + openpyxl)", "ret"),
    ]
    return sequence("Universal Scraper · guided scrape flow",
                    "From a link and a sentence to a table: the AI writes the recipe once, then plain code collects every page.",
                    parts, steps)


def agent():
    parts = [("You", "browser · Agent", "browser"), ("App", "agent API · guard", "agent"),
             ("Agent worker", "browser-use 0.13.11", "agent"), ("Headless browser", "Edge / Chrome · CDP", "plain"),
             ("Website", "the planned site only", "external"), ("AI provider", "planner · agent · backup", "external"),
             ("Engine", "jobs · quality · SQLite", "engine")]
    Y, APP, WK, BR, SITE, AI, ENG = range(7)
    steps = [
        ("section", "1 · Plan (you approve it first)"),
        ("msg", Y, APP, "POST /api/agent/plan  (goal, site)"),
        ("msg", APP, AI, "planner: goal → steps, columns, filters (strict JSON)"),
        ("msg", APP, Y, "plan card: edit steps, columns, pages, limits", "ret"),
        ("section", "2 · Find the results (the AI drives, the app approves every action)"),
        ("msg", Y, APP, "POST /api/agent/runs/RUN-…/start  (limits)"),
        ("msg", APP, WK, "start the worker process (one-time token, minimal environment)"),
        ("msg", WK, APP, "GET /worker/next → task, allowed site, limits", "ret"),
        ("msg", WK, BR, "open the start page"),
        ("msg", BR, SITE, "pages load; other sites are blocked"),
        ("loop", "each step · up to 40 steps, 10 min, 60 AI calls"),
        ("msg", WK, AI, "page elements (+ screenshot) + task → next action"),
        ("msg", WK, APP, "POST /check → guard: no buying, login, contact, personal data"),
        ("msg", WK, BR, "click · type · scroll · select (if allowed)"),
        ("msg", WK, APP, "POST /step (+ screenshot) → stop? human check, limit, Stop"),
        ("end",),
        ("msg", WK, APP, "POST /arrived  (results page HTML)"),
        ("section", "3 · Collect and check (no AI per page)"),
        ("msg", APP, ENG, "handoff: recipe from the page, steps recorded as a route"),
        ("msg", ENG, SITE, "collect pages: address filters or route replay"),
        ("self", APP, "supervisor: read the quality report, fix (up to 2 rounds)"),
        ("msg", APP, Y, "timeline + results JOB-…; saved scrape re-runs without AI", "ret"),
    ]
    return sequence("Universal Scraper · agent mode flow",
                    "When you only know the goal: an AI agent finds the results list, the app checks every action, and the normal engine collects.",
                    parts, steps)


# ------------------------------------------------------------------------------------------- 5 · data model
def table(x, y, w, name, desc, cols, role="storage"):
    """A table card; returns (svg, {column: y of its row centre}, bottom)."""
    fill, stroke, tcol = ROLE[role]
    rows_h = 22
    h = 64 + len(cols) * rows_h + 10
    out = [f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='12' fill='#ffffff' stroke='{stroke}' stroke-width='1.4'/>",
           f"<path d='M{x},{y + 12} a12,12 0 0 1 12,-12 h{w - 24} a12,12 0 0 1 12,12 v44 h-{w} z' fill='{fill}'/>",
           f"<line x1='{x}' y1='{y + 56}' x2='{x + w}' y2='{y + 56}' stroke='{stroke}'/>",
           t(x + 14, y + 25, name, 15, 700, tcol, mono=True), t(x + 14, y + 45, desc, 12, 400, MUTED)]
    ys = {}
    for i, (col, note, flag) in enumerate(cols):
        cy = y + 64 + i * rows_h + 14
        ys[col] = cy - 4
        if flag:
            colour = {"PK": "#9a5400", "FK": "#1f4fa3", "ID": "#5b3cc4"}[flag]
            out.append(f"<rect x='{x + 12}' y='{cy - 12}' width='26' height='16' rx='4' fill='none' stroke='{colour}'/>"
                       + t(x + 25, cy, flag, 9.5, 700, colour, "middle"))
        out.append(t(x + 46, cy, col, 12.5, 600 if flag else 400, INK, mono=True))
        out.append(t(x + w - 14, cy, note, 12, 400, MUTED, "end"))
    return "".join(out), ys, y + h


def data():
    W = 1930
    b = []
    sc, sc_y, sc_b = table(60, 150, 380, "scrapes", "Saved, re-runnable setups", [
        ("id", "integer", "PK"), ("name", "text", ""), ("urls", "JSON list", ""), ("recipe", "JSON: selectors, request", ""),
        ("max_pages", "integer", ""), ("page_type", "shop · jobs · …", ""), ("mode", "guided | agent", ""),
        ("route", "JSON: recorded steps", ""), ("created_at · updated_at", "time", "")])
    jb, jb_y, jb_b = table(520, 150, 420, "jobs", "One run of a scrape (shown as JOB-…)", [
        ("id", "integer, internal", "PK"), ("public_id", "JOB-7K3QX9, unique", "ID"), ("scrape_id", "→ scrapes.id", "FK"),
        ("urls · recipe", "JSON", ""), ("status", "queued … done · stopped", ""), ("max_pages", "integer", ""),
        ("pages_done · pages_total", "progress", ""), ("rows · message", "count · last line", ""),
        ("columns", "JSON: column types", ""), ("quality", "JSON: quality report", ""),
        ("personal", "JSON: fields + purpose", ""), ("logged_in · encrypted · keep", "flags", ""),
        ("paused_url · paused_kind", "needs you: login / check", ""), ("created_at · finished_at", "time", "")])
    rc, rc_y, rc_b = table(1020, 150, 380, "records", "Collected rows", [
        ("id", "integer", "PK"), ("job_id", "→ jobs.id, cascade", "FK"), ("page", "page address", ""),
        ("data", "JSON row, or enc:… if logged in", "")])
    jp, jp_y, jp_b = table(1020, rc_b + 30, 380, "job_pages", "Pages a job has read (resume, retry)", [
        ("job_id", "→ jobs.id, cascade", "FK"), ("url", "page address", "PK"), ("rows", "integer", ""), ("fetched_at", "time", "")])
    ar, ar_y, ar_b = table(1480, 150, 390, "agent_runs", "Agent mode runs (shown as RUN-…)", [
        ("id", "integer, internal", "PK"), ("public_id", "RUN-7K3QX9, unique", "ID"), ("title", "your name, or empty", ""),
        ("goal · site · start_url", "what you asked", ""), ("plan · limits", "JSON", ""),
        ("status · message · summary", "draft … done", ""), ("steps · ai_calls · tokens", "counters", ""),
        ("job_id", "→ jobs.id (collecting)", "FK"), ("scrape_id", "→ scrapes.id (saved)", "FK"),
        ("target_scrape_id", "re-record target", "FK"), ("fixes · fix_rounds", "check and fix", ""),
        ("final_url · stop_requested", "where it ended", ""), ("created · started · finished", "time", "")], role="agent")
    st, st_y, st_b = table(1480, ar_b + 30, 390, "agent_steps", "Timeline of a run", [
        ("run_id", "→ agent_runs.id, cascade", "FK"), ("n", "step number", "PK"), ("kind", "action · thought · info · warn", ""),
        ("text · url", "what happened, where", ""), ("detail", "JSON: recorded target", ""), ("shot", "has a screenshot", "")], role="agent")
    b += [sc, jb, rc, jp, ar, st]

    # per-site and app-wide tables
    y2 = max(jb_b, jp_b) + 70
    b.append(t(60, y2 - 22, "PER SITE AND APP-WIDE", 11.5, 700, MUTED))
    si, _, si_b = table(60, y2, 380, "sites", "Saved recipe + settings per site", [
        ("domain", "e.g. books.toscrape.com", "PK"), ("recipe", "JSON", ""), ("source", "builtin · ai · manual", ""),
        ("settings", "JSON: pace, render, blocked", ""), ("updated_at", "time", "")], role="plain")
    su, _, su_b = table(520, y2, 420, "site_usage", "Pages fetched per site per day", [
        ("domain · day", "budget key", "PK"), ("pages", "integer (daily budget)", "")], role="plain")
    pc, _, pc_b = table(520, su_b + 24, 420, "page_cache", "Fetched pages, reused for 24 h", [
        ("url", "page address", "PK"), ("status · final_url", "HTTP result", ""), ("body", "HTML", ""), ("fetched_at", "time", "")], role="plain")
    aps, _, aps_b = table(1020, y2, 380, "app_settings", "Choices made in Settings", [
        ("key", "e.g. ai", "PK"), ("value", "JSON: providers, models", "")], role="plain")
    b += [si, su, pc, aps]

    # relations (one → many)
    def rel(points, label, lx, ly, anchor="middle"):
        b.append(arrow(points, label, lx, ly, color=ROLE["web"][2], anchor=anchor, lsize=11.5))
    rel([(440, sc_y["id"]), (480, sc_y["id"]), (480, jb_y["scrape_id"]), (518, jb_y["scrape_id"])], "1 → many", 470, sc_y["id"] - 10)
    rel([(940, jb_y["id"]), (980, jb_y["id"]), (980, rc_y["job_id"]), (1018, rc_y["job_id"])], "1 → many", 978, jb_y["id"] - 10)
    rel([(980, rc_y["job_id"] + 0.1), (980, jp_y["job_id"]), (1018, jp_y["job_id"])], "", 0, 0)
    rel([(1478, ar_y["job_id"]), (1440, ar_y["job_id"]), (1440, jp_b + 34), (960, jp_b + 34), (960, jb_y["status"]), (942, jb_y["status"])],
        "agent_runs.job_id → jobs.id", 1200, jp_b + 26)
    rel([(1675, 150), (1675, 120), (250, 120), (250, 148)], "agent_runs.scrape_id → scrapes.id  (saved scrape it made or re-records)", 960, 112)
    rel([(1675, ar_b), (1675, ar_b + 28)], "1 → many", 1685, ar_b + 19, anchor="start")

    # files next to the database
    yf = max(si_b, pc_b, aps_b, st_b) + 60
    b.append(t(60, yf - 22, "FILES NEXT TO THE DATABASE  ·  data/", 11.5, 700, MUTED))
    files = [("sessions/<site>", "Login state, Fernet-encrypted; expires after 7 days"),
             ("audit.log", "Append-only JSON lines: fetch, export, login, settings, refusals"),
             ("agent_shots/<run>/<n>.jpg", "Screenshot for agent_steps.shot; deleted with the run"),
             ("agent_worker.log", "What the agent worker did (browser-use output)")]
    for i, (name, desc) in enumerate(files):
        x = 60 + i * 460
        b.append(box(x, yf, 440, 76, "storage", "", [], pad=12))
        b.append(t(x + 14, yf + 30, name, 13.5, 700, ROLE["storage"][2], mono=True) + t(x + 14, yf + 54, desc, 12, 400, MUTED))
    H = yf + 140
    b.append(t(60, H - 28, "Purge every hour: jobs older than 30 days (unless kept) with their records and pages; cached pages older than 24 h. "
                           "Deleting a site's data removes its jobs, cache and login.", 12.5, 400, MUTED, italic=True))
    return svg(W, H, "".join(b), "Universal Scraper · data model",
               "One SQLite file, data/scraper.db: 10 tables. Ids are internal; people see JOB-… and RUN-… IDs. "
               "Lists and settings are stored as JSON text.")


DIAGRAMS = {"overview": ("01-system-overview.png", overview),
            "stack": ("02-tech-stack.png", stack),
            "guided": ("03-guided-scrape-flow.png", guided),
            "agent": ("04-agent-mode-flow.png", agent),
            "data": ("05-data-model.png", data)}


def render(names):
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="architecture-"))
    with sync_playwright() as p:
        b = p.chromium.launch()
        for name in names:
            fname, fn = DIAGRAMS[name]
            markup = fn()
            w = int(markup.split("width='")[1].split("'")[0]); h = int(markup.split("height='")[1].split("'")[0])
            html = tmp / f"{name}.html"
            html.write_text(page(markup), encoding="utf8")
            pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
            pg.goto(html.as_uri()); pg.wait_for_timeout(300)
            pg.locator("svg").screenshot(path=str(OUT / fname))
            pg.close()
            print("wrote", OUT / fname)
        b.close()


if __name__ == "__main__":
    render(sys.argv[1:] or list(DIAGRAMS))
