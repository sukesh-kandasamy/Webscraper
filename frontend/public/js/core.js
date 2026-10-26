// Universal Scraper UI: what every page shares (Alpine.js). Each page adds its own script in js/pages/.

// Apply the saved theme straight away so the page doesn't flash the wrong colours.
function applyTheme(theme) {
  const dark = theme === "dark" || (theme !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}
applyTheme((() => { try { return JSON.parse(localStorage.getItem("us:theme")) || "system"; } catch { return "system"; } })());

// Google Material Symbols (Rounded) from icons.js. The UI's short names map to Material names; any
// Material name in icons.js works too. Static strings only; rendered with x-html.
const ICON_NAMES = {
  plus: "add", folder: "folder", table: "table_chart", cog: "settings", cart: "shopping_cart", briefcase: "work",
  building: "storefront", house: "apartment", chart: "monitoring", news: "newspaper", sparkles: "wand_stars",
  agent: "smart_toy", play: "play_arrow", pencil: "edit", trash: "delete", copy: "content_copy",
  external: "open_in_new", dots: "more_vert", warn: "warning", sun: "light_mode", moon: "dark_mode",
  monitor: "brightness_auto", x: "close", chevron: "chevron_right", left: "chevron_left", rows: "view_list",
  clock: "schedule",
};
function iconSvg(name, cls = "h-5 w-5") {
  const key = ICON_NAMES[name] || name;
  const d = MATERIAL_ICONS[key] || MATERIAL_ICONS.info;
  return `<svg class="${cls}" viewBox="0 -960 960 960" fill="currentColor" aria-hidden="true"><path d="${d}"/></svg>`;
}

async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    const err = new Error("The app's server is not responding. Is it still running?");
    err.status = 0;
    throw err;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(typeof data.detail === "string" ? data.detail
      : data.detail?.message || (Array.isArray(data.detail) ? "Some input is not valid." : `Request failed (${res.status})`));
    err.detail = data.detail;
    err.status = res.status;
    throw err;
  }
  return data;
}

const TEMPLATES = [
  { id: "products", icon: "cart", name: "Products & prices", hint: "Shops, marketplaces, B2B catalogues",
    columns: ["name", "price", "rating", "reviews", "seller", "url"],
    prompt: "I need the product name, image, price, rating and number of reviews of each product." },
  { id: "jobs", icon: "briefcase", name: "Job listings", hint: "Job boards, career pages",
    columns: ["title", "company", "location", "salary", "experience", "posted", "url"],
    prompt: "I need the job title, company, location, salary, experience and posted date of each job." },
  { id: "real_estate", icon: "house", name: "Real estate", hint: "Flats, houses, rentals",
    columns: ["title", "price", "location", "area_sqft", "bedrooms", "url"],
    prompt: "I need the title, price, location, area in sq.ft and number of bedrooms of each property." },
  { id: "stocks", icon: "chart", name: "Stock & market tables", hint: "Tickers, prices, indices",
    columns: ["symbol", "name", "price", "change", "change_percent", "volume"],
    prompt: "I need the symbol, company name, price, change, change % and volume of each stock." },
  { id: "directory", icon: "building", name: "Business directory", hint: "Suppliers, companies, shops",
    columns: ["name", "category", "location", "rating", "url"],
    prompt: "I need the business name, category, location and rating of each listing." },
  { id: "news", icon: "news", name: "News & articles", hint: "Headlines, blogs, press",
    columns: ["title", "summary", "published", "url"],
    prompt: "I need the headline, summary and published date of each article." },
  { id: "auto", icon: "sparkles", name: "Anything else", hint: "Let the AI decide the columns", columns: [],
    prompt: "Collect all the important details of each item." },
];

const AGENT_EXAMPLES = [
  { label: "Mystery books", site: "books.toscrape.com",
    goal: "Mystery books: title, price, rating and availability. Only books under £20, all pages." },
  { label: "Quotes by tag", site: "quotes.toscrape.com",
    goal: "Quotes tagged 'inspirational': the quote text, author and tags." },
  { label: "Remote jobs", site: "weworkremotely.com",
    goal: "Remote programming jobs: job title, company, region and posted date. Newest first." },
];

const TYPE_LABELS = { money: "price", number: "number", percent: "%", rating: "rating", date: "date", url: "link", image: "image" };
const OP_LABELS = { lt: "<", lte: "≤", gt: ">", gte: "≥", eq: "=", ne: "≠", contains: "contains", not_contains: "doesn't contain" };

const PAGE_TYPES = {
  ecommerce: "Products & prices", jobs: "Job listings", real_estate: "Real estate",
  stocks_finance: "Stock & market table", directory: "Business directory", news_articles: "News & articles",
  events: "Events", other: "Listing",
};

const EVENT_LABELS = {
  fetch: "Opened a page", job_started: "Scrape started", job_paused: "Scrape paused", job_blocked: "Scrape stopped by site",
  job_deleted: "Run deleted", export: "Downloaded results", session_saved: "Login saved",
  session_forgotten: "Login removed", login_confirmed: "Own account confirmed", settings_changed: "Site settings changed",
  cooldown: "Site paused for a while", cooldown_cleared: "Site pause ended", challenge_solved: "Human check solved by you",
  window_opened: "Browser window opened", recipe_built: "AI set up the columns", recipe_saved: "Column setup saved",
  retention_purge: "Old data cleaned up", site_data_deleted: "Site data deleted", retry_after: "Waited as the site asked",
  scrape_saved: "Scrape saved", scrape_deleted: "Scrape deleted", detail_recipe_built: "AI set up item pages",
  scrape_repaired: "Columns re-detected", agent_planned: "Agent made a plan", agent_started: "Agent started",
  agent_refused: "Agent action refused", agent_handoff: "Agent handed results over", agent_finished: "Agent stopped",
  agent_rerecord: "Agent re-recording a route", agent_fix: "Agent fixed a problem",
};

function store(key, value) {
  try {
    if (value === undefined) return JSON.parse(localStorage.getItem("us:" + key));
    localStorage.setItem("us:" + key, JSON.stringify(value));
  } catch { return undefined; }
}

// A message to show on the next page (after an action that moves to another address).
function flash(msg, type = "success") {
  try { sessionStorage.setItem("us:flash", JSON.stringify({ msg, type })); } catch { /* private mode */ }
}
function takeFlash() {
  try {
    const f = JSON.parse(sessionStorage.getItem("us:flash"));
    sessionStorage.removeItem("us:flash");
    return f;
  } catch { return null; }
}
function goTo(path, msg) { if (msg) flash(msg); window.location.href = path; }

const PATHS = { home: "/", new: "/scrapes/new", scrapes: "/scrapes", runs: "/results", results: "/results",
                settings: "/settings", agent: "/agent" };

// Shared by every page: theme, dialogs, toasts, status, error messages, formatting, the visible browser
// window for logins and human checks. Each page adds its own state and methods (see page()).
function shell() {
  return {
    theme: store("theme") || "system",
    toasts: [],
    dialog: null,                // {title, message, input, value, confirmText, danger}
    dialogOpen: false,           // shown or not; dialog keeps its contents while it closes
    menuOpen: false,
    advanced: store("advanced") === true,
    agentShown: store("agentShown") !== false,
    status: null,
    error: null,                 // {title, hint, raw}
    busy: "",
    jobs: [],                    // recent runs (pages that show them, and the needs-you banner)
    usesJobs: false,             // a page sets this to keep the run list fresh
    blockedPage: null,           // {domain, url} of a page that asked for a human check
    win: null,                   // the visible browser window (login / solve)
    winTimer: null,
    statusTimer: null,
    pollTimer: null,
    templates: TEMPLATES,
    data: document.body.dataset, // page data from the server (ids, active page)

    async initShell() {
      await this.loadStatus();
      this.$watch("advanced", v => store("advanced", v));
      this.$watch("agentShown", v => store("agentShown", v));
      this.$watch("theme", v => { store("theme", v); applyTheme(v); });
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme(this.theme));
      document.addEventListener("keydown", e => {
        if (e.key === "Escape" && this.dialogOpen) this.closeDialog(false);
        if (e.key === "Escape") { this.menuOpen = false; this.colMenu = false; }
      });
      const f = takeFlash();
      if (f) this.toast(f.msg, f.type);
      if (this.usesJobs || this.status?.needs_you?.length) await this.loadJobs();
    },

    // Status (counters for the navigation) every few seconds, faster while something runs.
    async loadStatus() {
      try { this.status = await api("GET", "/api/status"); } catch (e) { if (!this.status) this.showError(e); }
      const busy = this.status && (this.status.active_jobs || this.status.active_agent_runs);
      clearTimeout(this.statusTimer);
      this.statusTimer = setTimeout(async () => {
        await this.loadStatus();
        if (!this.usesJobs && this.status?.needs_you?.length && !this.pollTimer) this.loadJobs();
      }, busy ? 3000 : 10000);
    },

    async loadJobs() {
      try { this.jobs = await api("GET", "/api/jobs?limit=100"); } catch { /* server may be restarting */ }
      clearTimeout(this.pollTimer);
      const keepPolling = this.usesJobs || this.needsYouJobs().length;
      this.pollTimer = keepPolling ? setTimeout(() => this.loadJobs(), this.activeJobs().length ? 2000 : 8000) : null;
      if (this.afterJobs) await this.afterJobs();
    },

    // ---------------------------------------------------------------- helpers
    icon(name, cls) { return iconSvg(name, cls); },
    go(view) { goTo(PATHS[view] || "/"); },
    cycleTheme() { this.theme = { system: "light", light: "dark", dark: "system" }[this.theme] || "system"; },
    themeIcon() { return { system: "monitor", light: "sun", dark: "moon" }[this.theme]; },
    themeLabel() { return { system: "System theme", light: "Light theme", dark: "Dark theme" }[this.theme]; },
    agentEnabled() { return !!this.status?.agent?.enabled && this.agentShown; },

    // Styled dialogs instead of the browser's confirm() / prompt().
    ask(opts) {
      return new Promise(resolve => {
        this.dialog = { title: "", message: "", input: false, value: "", confirmText: "OK", danger: false, ...opts, resolve };
        this.dialogOpen = true;
        this.$nextTick(() => document.getElementById(this.dialog.input ? "dialog-input" : "dialog-ok")?.focus());
      });
    },
    confirmDialog(title, message, confirmText = "Delete", danger = true) {
      return this.ask({ title, message, confirmText, danger });
    },
    promptDialog(title, label, value = "", confirmText = "Save") {
      return this.ask({ title, message: label, input: true, value, confirmText });
    },
    closeDialog(ok) {
      const d = this.dialog;
      if (!d || !this.dialogOpen) return;
      this.dialogOpen = false;
      d.resolve(ok ? (d.input ? (d.value || "").trim() : true) : (d.input ? null : false));
    },
    toast(msg, type = "success") {
      const id = Date.now() + Math.random();
      this.toasts.push({ id, msg, type });
      setTimeout(() => { this.toasts = this.toasts.filter(t => t.id !== id); }, 5500);
    },

    fmt(n) { return (n || 0).toLocaleString(); },
    hostOf(u) { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } },
    isUrl(v) { return typeof v === "string" && /^https?:\/\//.test(v); },
    isImage(v) { return this.isUrl(v) && /\.(jpe?g|png|webp|gif|avif)(\?|$)/i.test(v); },
    pageTypeLabel(t) { return PAGE_TYPES[t] || ""; },
    eventLabel(e) { return EVENT_LABELS[e] || e; },
    typeLabel(t) { return TYPE_LABELS[t] || ""; },
    filterLabel(f) { return `${this.pretty(f.column)} ${OP_LABELS[f.op] || f.op} ${f.value}`; },
    statusChip(s) {
      return { done: "chip-green", running: "chip-blue", queued: "chip-amber", stopped: "chip-gray",
               blocked: "chip-red", error: "chip-red", needs_you: "chip-amber" }[s] || "chip-gray";
    },
    statusLabel(s) {
      return { done: "Finished", running: "Collecting", queued: "Waiting", stopped: "Stopped",
               blocked: "Stopped by site", error: "Failed", needs_you: "Needs you" }[s] || s;
    },
    timeAgo(ts) {
      if (!ts) return "";
      const s = Math.max(0, Date.now() / 1000 - ts);
      if (s < 60) return "just now";
      if (s < 3600) return Math.round(s / 60) + " min ago";
      if (s < 86400) return Math.round(s / 3600) + " h ago";
      return Math.round(s / 86400) + " d ago";
    },
    columns(rows) {
      const cols = [];
      // keys starting with "_" are the app's own (row id, edited columns), not data columns
      for (const r of rows || []) for (const k of Object.keys(r)) if (!k.startsWith("_") && !cols.includes(k)) cols.push(k);
      return cols;
    },
    pretty(col) { return col.replace(/_/g, " "); },
    cellText(v) {
      if (typeof v === "number") return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
      return v;
    },
    shortText(v) { const s = String(this.cellText(v) ?? ""); return s.length > 40 ? s.slice(0, 38) + "…" : s; },
    needsYouJobs() { return this.jobs.filter(j => j.status === "needs_you"); },
    activeJobs() { return this.jobs.filter(j => ["queued", "running"].includes(j.status)); },
    jobTitle(j) { return j.scrape_name || this.hostOf(j.urls?.[0]) || j.uid || "Run"; },
    async copyText(text) {
      try { await navigator.clipboard.writeText(text); this.toast("Copied."); }
      catch { this.toast("Couldn't copy. Select the text and copy it instead."); }
    },
    progressPct(j) {
      if (!j) return 0;
      if (j.status === "done") return 100;
      return j.pages_total ? Math.max(5, Math.round(100 * j.pages_done / j.pages_total)) : 5;
    },

    // Turn API errors into a title and a "what to do" hint.
    explain(e) {
      const msg = e.message || "";
      if (e.status === 0) return { title: "Can't reach the app", hint: "Start it again with  python run.py  and refresh this page." };
      if (/robots\.txt/i.test(msg)) return { title: "This page is off-limits for scrapers",
        hint: "The site's crawling rules block it (search pages often are). Try a category or listing page from the same site.", raw: msg };
      if (/CERTIFICATE|SSL/i.test(msg)) return { title: "Your network blocked this site",
        hint: "A company firewall is intercepting the connection. Try another network, or ask IT to allow the site.", raw: msg };
      if (e.status === 429 || /budget/i.test(msg)) return { title: "Daily limit reached for this site",
        hint: "It resets tomorrow. You can raise the limit in Settings → Sites (turn on Advanced mode in Settings).", raw: msg };
      if (/cooling down/i.test(msg)) return { title: "This site is paused for a while",
        hint: "It recently limited our visits. Wait a bit, or end the pause in Settings → Sites.", raw: msg };
      if (/needs a login/i.test(msg)) return { title: "This page needs a login",
        hint: "If it's your own account, use Settings → Sites → Log in, then try again.", raw: msg };
      if (/too large/i.test(msg)) return { title: "This page is too big for the AI",
        hint: "Try a page with fewer items, or set up the columns by hand (turn on Advanced mode in Settings).", raw: msg };
      if (/repeated listing|Couldn't find any repeated/i.test(msg)) return { title: "No list of items found",
        hint: "Use a page that shows many similar items (search results, a category, a table). Single product pages won't work.", raw: msg };
      if (/no AI|AI is turned off|API key/i.test(msg)) return { title: "The AI is not available",
        hint: "Add an AI key to .env, or set up the columns by hand (turn on Advanced mode in Settings).", raw: msg };
      if (/rate-limited/i.test(msg)) {
        const m = msg.match(/wait (about \d+ minutes|\d+ seconds)/);
        return { title: "The AI is busy", hint: m ? `Its provider asks to wait ${m[1]}. Try again after that.` : "Wait a minute and try again.", raw: msg };
      }
      if (e.status === 502) return { title: "Couldn't open that page", hint: "Check the link in your browser. The site may be down or blocking automated visits.", raw: msg };
      if (e.status === 400 && /local|private|http\(s\)/i.test(msg)) return { title: "That link can't be used", hint: msg };
      return { title: "Something went wrong", hint: msg };
    },
    showError(e) { this.error = this.explain(e); },
    handleError(e) {
      const d = e.detail || {};
      if (e.status === 409 && d.code === "needs_human") { this.blockedPage = d; this.error = null; return; }
      this.showError(e);
    },

    // ---------------------------------------------------------------- actions that open another page
    startWizard(template = null, url = "") {
      const q = new URLSearchParams();
      if (template?.id) q.set("template", template.id);
      if (url) q.set("url", url);
      goTo("/scrapes/new" + (q.toString() ? "?" + q : ""));
    },
    trySample() { this.startWizard(TEMPLATES[0], "https://books.toscrape.com/"); },
    openJob(j) { if (j) goTo(`/results/${j.uid || j.id}`); },
    openLatestRun(s) { if (s.last_run) goTo(`/results/${s.last_run.uid || s.last_run.id}`); },
    editScrape(s) { goTo(`/scrapes/${s.id}/edit`); },
    async runScrape(s) {
      this.error = null;
      try {
        const { job_id, job_uid } = await api("POST", `/api/scrapes/${s.id}/run`, {});
        goTo(`/results/${job_uid || job_id}`, `Running “${s.name}” again.`);
      } catch (e) { this.handleError(e); }
    },
    async runAgainFromJob(j) {
      if (!j?.scrape_id) return;
      this.error = null;
      try {
        const { job_id, job_uid } = await api("POST", `/api/scrapes/${j.scrape_id}/run`, {});
        goTo(`/results/${job_uid || job_id}`, `Running “${this.jobTitle(j)}” again.`);
      } catch (e) { this.handleError(e); }
    },
    async rerecordScrape(scrapeId) {
      if (!scrapeId) return;
      this.error = null; this.busy = "agent-start";
      try {
        const run = await api("POST", `/api/agent/rerecord/${scrapeId}`, this.status?.agent?.limits);
        goTo(`/agent/runs/${run.uid || run.id}`, "The agent is finding the way again. The saved scrape is updated when it's done.");
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },
    async stopJob(j) {
      await api("POST", `/api/jobs/${j.id}/stop`).catch(e => this.showError(e));
      this.loadJobs();
    },

    // ---------------------------------------------------------------- visible browser window
    async startLogin(site) {
      const url = await this.promptDialog(`Log in to ${site.domain}`, "Login page address", `https://${site.domain}/`, "Next");
      if (!url) return;
      if (!await this.confirmDialog("Is this your own account?", "Only continue if it is your own account and the site allows automated access while logged in. You'll type your password in a browser window; the app never sees it.", "Continue", false)) return;
      this.win = { mode: "login", domain: site.domain, url, status: "starting", message: "", account: site.session?.account || "" };
      try { this.trackWindow(`login:${site.domain}`, await api("POST", `/api/sites/${site.domain}/login`, { url, own_account: true })); }
      catch (e) { this.win = null; this.showError(e); }
    },
    async startSiteSolve() {
      const d = this.blockedPage;
      this.win = { mode: "site", domain: d.domain, url: d.url, status: "starting", message: "", account: "" };
      try { this.trackWindow(`site:${d.domain}`, await api("POST", `/api/sites/${d.domain}/solve`, { url: d.url })); }
      catch (e) { this.win = null; this.showError(e); }
    },
    async startJobSolve(j) {
      this.win = { mode: "job", jobId: j.id, domain: this.hostOf(j.paused_url), url: j.paused_url,
                   kind: j.paused_kind, status: "starting", message: "", account: "" };
      try { this.trackWindow(`job:${j.id}`, await api("POST", `/api/jobs/${j.id}/solve`)); }
      catch (e) { this.win = null; this.showError(e); }
    },
    trackWindow(key, w) {
      Object.assign(this.win, { key, status: w.status, message: w.message });
      clearTimeout(this.winTimer);
      const poll = async () => {
        if (!this.win || this.win.key !== key) return;
        try {
          const s = await api("GET", `/api/windows/${encodeURIComponent(key)}`);
          Object.assign(this.win, { status: s.status, message: s.message, title: s.title });
          if (["starting", "open"].includes(s.status)) this.winTimer = setTimeout(poll, 1500);
        } catch { /* window gone */ }
      };
      this.winTimer = setTimeout(poll, 1200);
    },
    async finishWindow() {
      const w = this.win;
      this.busy = "window";
      try {
        if (w.mode === "login") {
          const r = await api("POST", `/api/sites/${w.domain}/login/finish`, { account: w.account });
          this.toast(`Login saved for ${r.domain}. It's encrypted and lasts ${Math.round(r.expires_in_hours / 24)} days.` + (r.warning ? " " + r.warning : ""));
        } else if (w.mode === "site") {
          await api("POST", `/api/sites/${w.domain}/solve/finish`, { account: w.account });
          this.toast(`Thanks. ${w.domain} is open again; try once more.`);
          this.blockedPage = null;
        } else {
          await api("POST", `/api/jobs/${w.jobId}/solve/finish`, { account: w.account });
          this.toast("The scrape continues.");
        }
        this.win = null;
        await this.loadJobs();
        if (this.afterWindow) await this.afterWindow();
      } catch (e) { this.showError(e); }
      finally { this.busy = ""; }
    },
    async cancelWindow() {
      const w = this.win;
      this.win = null; clearTimeout(this.winTimer);
      if (w?.mode === "login") await api("POST", `/api/sites/${w.domain}/login/cancel`).catch(() => {});
    },
  };
}

// One page = the shared shell + that page's own state and methods. The page's start() runs after the
// shell has loaded the status. Getters are copied as getters.
function page(own) {
  const out = shell();
  Object.defineProperties(out, Object.getOwnPropertyDescriptors(own));
  out.init = async function () {
    await this.initShell();
    if (this.start) await this.start();
  };
  return out;
}
