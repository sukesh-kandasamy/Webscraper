// Universal Scraper UI (Alpine.js). One component drives the whole app shell.

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
  { id: "products", icon: "🛒", name: "Products & prices", hint: "Shops, marketplaces, B2B catalogues",
    columns: ["name", "price", "rating", "reviews", "seller", "url"] },
  { id: "jobs", icon: "💼", name: "Job listings", hint: "Job boards, career pages",
    columns: ["title", "company", "location", "salary", "experience", "posted", "url"] },
  { id: "real_estate", icon: "🏠", name: "Real estate", hint: "Flats, houses, rentals",
    columns: ["title", "price", "location", "area_sqft", "bedrooms", "url"] },
  { id: "stocks", icon: "📈", name: "Stock & market tables", hint: "Tickers, prices, indices",
    columns: ["symbol", "name", "price", "change", "change_percent", "volume"] },
  { id: "directory", icon: "🏢", name: "Business directory", hint: "Suppliers, companies, shops",
    columns: ["name", "category", "location", "rating", "url"] },
  { id: "news", icon: "📰", name: "News & articles", hint: "Headlines, blogs, press",
    columns: ["title", "summary", "published", "url"] },
  { id: "auto", icon: "✨", name: "Anything else", hint: "Let the AI decide the columns", columns: [] },
];

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
  scrape_saved: "Scrape saved", scrape_deleted: "Scrape deleted",
};

function store(key, value) {
  try {
    if (value === undefined) return JSON.parse(localStorage.getItem("us:" + key));
    localStorage.setItem("us:" + key, JSON.stringify(value));
  } catch { return undefined; }
}

function scraper() {
  return {
    // ---- app shell ----
    view: "home",
    menuOpen: false,
    advanced: store("advanced") === true,
    status: null,
    error: null,                 // {title, hint, raw}
    notice: "",
    noticeTimer: null,
    busy: "",

    // ---- wizard ----
    step: 1,
    template: null,
    urlMain: "",
    urlsExtra: "",
    showExtra: false,
    fieldsText: "",
    autoColumns: true,
    analysis: null,
    recipeText: "",
    newColumn: "",
    scrapeName: "",
    maxPages: 3,
    editingScrapeId: null,
    personalChoice: {},
    personalPurpose: "",
    settings: {},
    blockedPage: null,

    // ---- data ----
    jobs: [],
    scrapes: [],
    sites: [],
    activity: [],

    // ---- results ----
    selectedJob: null,
    records: [],
    showPersonal: false,
    search: "",
    sortKey: "",
    sortAsc: true,
    hiddenCols: [],
    colMenu: false,
    pollTimer: null,

    // ---- browser window dialog ----
    win: null,
    winTimer: null,

    templates: TEMPLATES,

    async init() {
      try {
        this.status = await api("GET", "/api/status");
        this.maxPages = Math.min(3, this.status.max_pages);
      } catch (e) { this.showError(e); }
      await Promise.all([this.loadJobs(), this.loadScrapes(), this.loadSites()]);
      this.$watch("advanced", v => store("advanced", v));
    },

    // ---------------------------------------------------------------- helpers
    go(view) {
      this.view = view; this.menuOpen = false; this.error = null;
      if (view === "settings") this.loadSites();
      if (view === "scrapes") this.loadScrapes();
      window.scrollTo({ top: 0 });
    },
    toast(msg) {
      this.notice = msg;
      clearTimeout(this.noticeTimer);
      this.noticeTimer = setTimeout(() => { this.notice = ""; }, 6000);
    },
    urls() {
      return [this.urlMain, ...this.urlsExtra.split("\n")].map(s => s.trim()).filter(Boolean);
    },
    fields() { return this.fieldsText.split(",").map(s => s.trim()).filter(Boolean); },
    hostOf(u) { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } },
    isUrl(v) { return typeof v === "string" && /^https?:\/\//.test(v); },
    isImage(v) { return this.isUrl(v) && /\.(jpe?g|png|webp|gif|avif)(\?|$)/i.test(v); },
    pageTypeLabel(t) { return PAGE_TYPES[t] || ""; },
    eventLabel(e) { return EVENT_LABELS[e] || e; },
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
      for (const r of rows || []) for (const k of Object.keys(r)) if (!cols.includes(k)) cols.push(k);
      return cols;
    },
    pretty(col) { return col.replace(/_/g, " "); },
    needsYouJobs() { return this.jobs.filter(j => j.status === "needs_you"); },
    activeJobs() { return this.jobs.filter(j => ["queued", "running"].includes(j.status)); },
    jobTitle(j) { return j.scrape_name || this.hostOf(j.urls?.[0]) || ("Run #" + j.id); },

    // Turn API errors into a title and a "what to do" hint.
    explain(e) {
      const msg = e.message || "";
      if (e.status === 0) return { title: "Can't reach the app", hint: "Start it again with  python run.py  and refresh this page." };
      if (/robots\.txt/i.test(msg)) return { title: "This page is off-limits for scrapers",
        hint: "The site's crawling rules block it (search pages often are). Try a category or listing page from the same site.", raw: msg };
      if (/CERTIFICATE|SSL/i.test(msg)) return { title: "Your network blocked this site",
        hint: "A company firewall is intercepting the connection. Try another network, or ask IT to allow the site.", raw: msg };
      if (e.status === 429 || /budget/i.test(msg)) return { title: "Daily limit reached for this site",
        hint: "It resets tomorrow. You can raise the limit in Settings → Sites (Advanced mode).", raw: msg };
      if (/cooling down/i.test(msg)) return { title: "This site is paused for a while",
        hint: "It recently limited our visits. Wait a bit, or end the pause in Settings → Sites.", raw: msg };
      if (/needs a login/i.test(msg)) return { title: "This page needs a login",
        hint: "If it's your own account, use Settings → Sites → Log in, then try again.", raw: msg };
      if (/too large/i.test(msg)) return { title: "This page is too big for the AI",
        hint: "Try a page with fewer items, or set up the columns by hand in Advanced mode.", raw: msg };
      if (/repeated listing|Couldn't find any repeated/i.test(msg)) return { title: "No list of items found",
        hint: "Use a page that shows many similar items (search results, a category, a table). Single product pages won't work.", raw: msg };
      if (/no AI|AI is turned off|API key/i.test(msg)) return { title: "The AI is not available",
        hint: "Add an AI key to .env, or set up the columns by hand in Advanced mode.", raw: msg };
      if (/rate-limited/i.test(msg)) return { title: "The AI is busy", hint: "Wait a minute and try again.", raw: msg };
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

    // ---------------------------------------------------------------- wizard
    startWizard(template = null, url = "") {
      this.go("new");
      this.step = 1; this.template = template; this.editingScrapeId = null;
      this.analysis = null; this.recipeText = ""; this.blockedPage = null;
      this.urlMain = url || ""; this.urlsExtra = ""; this.showExtra = false;
      this.fieldsText = template ? template.columns.join(", ") : "";
      this.autoColumns = true; this.scrapeName = ""; this.personalChoice = {}; this.personalPurpose = "";
      this.maxPages = Math.min(3, this.status?.max_pages || 3);
      if (url) this.analyse(false);
    },

    pickTemplate(t) {
      this.template = this.template?.id === t.id ? null : t;
      this.fieldsText = this.template ? this.template.columns.join(", ") : "";
    },

    trySample() { this.startWizard(TEMPLATES[0], "https://books.toscrape.com/"); },

    async analyse(forceAi) {
      if (!this.urlMain.trim()) { this.error = { title: "Paste a link first", hint: "Copy the address of a page that lists items." }; return; }
      this.error = null; this.blockedPage = null; this.busy = forceAi ? "ai" : "analyse";
      try {
        this.analysis = await api("POST", "/api/analyse",
          { url: this.urls()[0], fields: this.fields(), auto: this.autoColumns, force_ai: forceAi });
        this.afterAnalysis();
        this.step = 2;
      } catch (e) {
        if (e.detail?.starter_recipe) {
          this.showError(e);
          if (this.advanced) {
            this.analysis = { domain: e.detail.domain, source: "starter", rows: [], row_count: 0, report: {},
                              personal_fields: [], settings: {} };
            this.recipeText = JSON.stringify(e.detail.starter_recipe, null, 2);
            this.step = 2;
          }
        } else { this.handleError(e); }
      } finally { this.busy = ""; }
    },

    afterAnalysis() {
      this.recipeText = JSON.stringify(this.analysis.recipe, null, 2);
      this.applySettings(this.analysis.settings);
      this.personalChoice = Object.fromEntries((this.analysis.personal_fields || []).map(f => [f, false]));
      if (!this.scrapeName) {
        const kind = this.pageTypeLabel(this.analysis.page_type) || "Data";
        this.scrapeName = `${kind} – ${this.hostOf(this.analysis.url || this.urlMain)}`;
      }
      this.loadSites();
    },

    parseRecipe() {
      try { return JSON.parse(this.recipeText); }
      catch (e) { throw new Error("The column setup (JSON) has a mistake: " + e.message); }
    },

    async previewRecipe() {
      this.error = null; this.busy = "preview";
      try {
        this.analysis = await api("POST", "/api/preview", { url: this.urls()[0], recipe: this.parseRecipe() });
        this.afterAnalysis();
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },

    recipeColumns() {
      try { return (JSON.parse(this.recipeText).fields || []).map(f => f.name); } catch { return []; }
    },
    fillOf(col) { return this.analysis?.report?.[col] ?? null; },
    fillClass(col) {
      const v = this.fillOf(col);
      if (v === null) return "bg-slate-300";
      return v >= 0.8 ? "bg-emerald-500" : v >= 0.4 ? "bg-amber-400" : "bg-rose-400";
    },

    async removeColumn(name) {
      try {
        const r = this.parseRecipe();
        r.fields = (r.fields || []).filter(f => f.name !== name);
        if (!r.fields.length) { this.error = { title: "Keep at least one column" }; return; }
        this.recipeText = JSON.stringify(r, null, 2);
        await this.previewRecipe();
      } catch (e) { this.showError(e); }
    },

    async renameColumn(name) {
      const next = prompt(`New name for "${this.pretty(name)}":`, name);
      if (!next || next.trim() === name) return;
      const clean = next.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
      if (!clean) return;
      try {
        const r = this.parseRecipe();
        if (r.fields.some(f => f.name === clean)) { this.error = { title: "That column name is already used" }; return; }
        r.fields.forEach(f => { if (f.name === name) f.name = clean; });
        this.recipeText = JSON.stringify(r, null, 2);
        await this.previewRecipe();
      } catch (e) { this.showError(e); }
    },

    async addColumn() {
      const name = this.newColumn.trim();
      if (!name) return;
      const wanted = [...new Set([...this.recipeColumns(), ...name.split(",").map(s => s.trim()).filter(Boolean)])];
      this.error = null; this.busy = "add";
      try {
        this.analysis = await api("POST", "/api/analyse",
          { url: this.urls()[0], fields: wanted, auto: false, force_ai: true });
        this.afterAnalysis();
        this.newColumn = "";
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },

    personalFieldsChosen() { return Object.entries(this.personalChoice).filter(([, v]) => v).map(([k]) => k); },

    paginationHint() {
      const t = this.analysis?.recipe?.pagination?.type;
      if (t === "next_link" || t === "url_param") return "The site has more pages; each one adds more rows.";
      if (t === "scroll") return "The site loads more items as you scroll; each 'page' is one scroll.";
      if (t === "click") return "The site has a 'Load more' button; each 'page' is one click.";
      return "Only this page seems to have items. Add more links in step 1 to collect more.";
    },

    async saveScrape(run) {
      this.error = null; this.busy = run ? "run" : "save";
      try {
        const chosen = this.personalFieldsChosen();
        if (chosen.length && this.personalPurpose.trim().length < 10) {
          throw new Error("Say why you need the personal data (at least 10 characters), or untick it.");
        }
        const recipe = this.parseRecipe();
        let jobId = null;
        if (this.editingScrapeId) {
          await api("PUT", `/api/scrapes/${this.editingScrapeId}`,
            { name: this.scrapeName, urls: this.urls(), recipe, max_pages: this.maxPages });
          if (run) jobId = (await api("POST", `/api/scrapes/${this.editingScrapeId}/run`,
            { personal_fields: chosen, personal_purpose: this.personalPurpose })).job_id;
        } else {
          const r = await api("POST", "/api/scrapes", {
            name: this.scrapeName || "My scrape", urls: this.urls(), recipe, max_pages: this.maxPages,
            page_type: this.analysis?.page_type || "", run,
            personal_fields: chosen, personal_purpose: this.personalPurpose,
          });
          jobId = r.job_id; this.editingScrapeId = r.scrape_id;
        }
        await Promise.all([this.loadScrapes(), this.loadJobs()]);
        if (run && jobId) {
          this.go("runs");
          await this.openJob(this.jobs.find(j => j.id === jobId));
          this.toast("Started. You can leave this page; it keeps running.");
        } else {
          this.go("scrapes");
          this.toast(`Saved “${this.scrapeName}”. Run it any time with Run again.`);
        }
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },

    // ---------------------------------------------------------------- saved scrapes
    async loadScrapes() {
      try { this.scrapes = await api("GET", "/api/scrapes"); } catch (e) { /* shown elsewhere */ }
    },

    async runScrape(s) {
      this.error = null;
      try {
        const { job_id } = await api("POST", `/api/scrapes/${s.id}/run`, {});
        await this.loadJobs();
        this.go("runs");
        await this.openJob(this.jobs.find(j => j.id === job_id));
        this.toast(`Running “${s.name}” again.`);
      } catch (e) { this.handleError(e); }
    },

    async editScrape(s) {
      try {
        const full = await api("GET", `/api/scrapes/${s.id}`);
        this.startWizard();
        this.editingScrapeId = full.id; this.scrapeName = full.name; this.maxPages = full.max_pages;
        this.urlMain = full.urls[0] || ""; this.urlsExtra = full.urls.slice(1).join("\n");
        this.showExtra = full.urls.length > 1;
        this.recipeText = JSON.stringify(full.recipe, null, 2);
        this.step = 2;
        await this.previewRecipe();
      } catch (e) { this.showError(e); }
    },

    async duplicateScrape(s) {
      try { await api("POST", `/api/scrapes/${s.id}/duplicate`); await this.loadScrapes(); this.toast("Copy created."); }
      catch (e) { this.showError(e); }
    },

    async renameScrape(s) {
      const name = prompt("Name:", s.name);
      if (!name || name === s.name) return;
      try { await api("PUT", `/api/scrapes/${s.id}`, { name }); await this.loadScrapes(); }
      catch (e) { this.showError(e); }
    },

    async deleteScrape(s) {
      if (!confirm(`Delete “${s.name}”? Its past results stay under Results until you delete them.`)) return;
      try { await api("DELETE", `/api/scrapes/${s.id}`); await this.loadScrapes(); }
      catch (e) { this.showError(e); }
    },

    async openLatestRun(s) {
      if (!s.last_run) return;
      await this.loadJobs();
      this.go("runs");
      await this.openJob(this.jobs.find(j => j.id === s.last_run.id));
    },

    // ---------------------------------------------------------------- runs / results
    async loadJobs() {
      const wasActive = this.activeJobs().map(j => j.id);
      try { this.jobs = await api("GET", "/api/jobs?limit=100"); } catch (e) { /* server may be restarting */ }
      const active = this.activeJobs().length > 0;
      clearTimeout(this.pollTimer);
      this.pollTimer = setTimeout(() => this.loadJobs(), active ? 2000 : 8000);
      if (wasActive.some(id => !this.activeJobs().find(j => j.id === id))) this.loadScrapes();
      if (this.selectedJob) {
        const fresh = this.jobs.find(j => j.id === this.selectedJob.id);
        if (fresh && (fresh.rows !== this.selectedJob.rows || fresh.status !== this.selectedJob.status)) {
          this.selectedJob = fresh;
          await this.loadRecords();
        } else if (fresh) { this.selectedJob = fresh; }
      }
    },

    async openJob(j) {
      if (!j) return;
      this.selectedJob = j; this.search = ""; this.sortKey = ""; this.showPersonal = false;
      this.hiddenCols = store("hidden:" + (j.scrape_id || j.id)) || [];
      await this.loadRecords();
    },

    async loadRecords() {
      try {
        this.records = await api("GET", `/api/jobs/${this.selectedJob.id}/records?show_personal=${this.showPersonal}`);
      } catch (e) { this.showError(e); }
    },

    allColumns() { return this.columns(this.records); },
    visibleColumns() { return this.allColumns().filter(c => !this.hiddenCols.includes(c)); },
    toggleCol(c) {
      this.hiddenCols = this.hiddenCols.includes(c) ? this.hiddenCols.filter(x => x !== c) : [...this.hiddenCols, c];
      store("hidden:" + (this.selectedJob.scrape_id || this.selectedJob.id), this.hiddenCols);
    },
    exportUrl(fmt) {
      const cols = this.visibleColumns().join(",");
      const name = encodeURIComponent(this.jobTitle(this.selectedJob || {}));
      return `/api/jobs/${this.selectedJob?.id}/export?fmt=${fmt}&show_personal=${this.showPersonal}&columns=${encodeURIComponent(cols)}&name=${name}`;
    },

    async stopJob(j) {
      await api("POST", `/api/jobs/${j.id}/stop`).catch(e => this.showError(e));
      this.loadJobs();
    },
    async resumeJob(j) {
      try { await api("POST", `/api/jobs/${j.id}/resume`); this.loadJobs(); } catch (e) { this.showError(e); }
    },
    async toggleKeep(j) {
      try { await api("POST", `/api/jobs/${j.id}/keep`, { keep: !j.keep }); j.keep = !j.keep; } catch (e) { this.showError(e); }
    },
    async deleteJob(j) {
      if (!confirm(`Delete this run and its ${j.rows} rows?`)) return;
      try {
        await api("DELETE", `/api/jobs/${j.id}`);
        if (this.selectedJob?.id === j.id) { this.selectedJob = null; this.records = []; }
        await Promise.all([this.loadJobs(), this.loadScrapes()]);
      } catch (e) { this.showError(e); }
    },
    async runAgainFromJob(j) {
      const s = this.scrapes.find(x => x.id === j.scrape_id);
      if (s) return this.runScrape(s);
    },

    sortBy(c) { if (this.sortKey === c) this.sortAsc = !this.sortAsc; else { this.sortKey = c; this.sortAsc = true; } },
    filtered() {
      const q = this.search.toLowerCase();
      let rows = q ? this.records.filter(r => Object.values(r).some(v => String(v).toLowerCase().includes(q)))
                   : this.records.slice();
      if (this.sortKey) {
        const k = this.sortKey, dir = this.sortAsc ? 1 : -1;
        const num = v => parseFloat(String(v).replace(/[^0-9.\-]/g, ""));
        rows.sort((a, b) => {
          const x = num(a[k]), y = num(b[k]);
          if (!isNaN(x) && !isNaN(y)) return (x - y) * dir;
          return String(a[k] ?? "").localeCompare(String(b[k] ?? "")) * dir;
        });
      }
      return rows;
    },
    progressPct(j) {
      if (!j) return 0;
      if (j.status === "done") return 100;
      return j.pages_total ? Math.max(5, Math.round(100 * j.pages_done / j.pages_total)) : 5;
    },

    // ---------------------------------------------------------------- site settings (advanced)
    settingsSummary() {
      const s = this.settings, d = this.status?.delay || [];
      const lo = s.min_delay ?? d[0], hi = s.max_delay ?? d[1];
      const bits = [`wait ${lo}–${hi}s between pages`, s.render === "always" ? "loads like a browser" : s.render === "never" ? "simple loading" : "automatic loading"];
      if (s.daily_budget) bits.push(`limit ${s.daily_budget} pages/day`);
      return bits.join(" · ");
    },
    applySettings(s) {
      s = s || {};
      this.settings = {
        min_delay: s.min_delay ?? null, max_delay: s.max_delay ?? null, render: s.render || "auto",
        daily_budget: s.daily_budget ?? null, honest_ua: !!s.honest_ua,
        ai_allowed: s.ai_allowed !== false, use_session: s.use_session !== false, blocked: !!s.blocked,
      };
    },
    async saveSettings() {
      try {
        const s = this.settings;
        const body = { render: s.render, honest_ua: s.honest_ua, ai_allowed: s.ai_allowed,
                       use_session: s.use_session, blocked: s.blocked };
        for (const k of ["min_delay", "max_delay", "daily_budget"]) if (s[k] !== null && s[k] !== "") body[k] = s[k];
        this.applySettings(await api("PUT", `/api/sites/${this.analysis.domain}/settings`, body));
        this.toast("Site settings saved.");
        this.loadSites();
      } catch (e) { this.showError(e); }
    },
    async saveRecipeForSite() {
      try {
        await api("PUT", `/api/sites/${this.analysis.domain}/recipe`, { recipe: this.parseRecipe() });
        this.toast("Column setup saved for " + this.analysis.domain + ".");
      } catch (e) { this.showError(e); }
    },

    // ---------------------------------------------------------------- sites & logins
    async loadSites() { try { this.sites = await api("GET", "/api/sites"); } catch (e) { /* not fatal */ } },
    async forgetSession(site) {
      if (!confirm(`Remove the saved login for ${site.session.domain}?`)) return;
      try { await api("DELETE", `/api/sessions/${site.session.domain}`); this.loadSites(); } catch (e) { this.showError(e); }
    },
    async clearCooldown(site) {
      try { await api("DELETE", `/api/sites/${site.domain}/cooldown`); this.loadSites(); } catch (e) { this.showError(e); }
    },
    async toggleBlocked(site) {
      try { await api("PUT", `/api/sites/${site.domain}/settings`, { blocked: !site.settings?.blocked }); this.loadSites(); }
      catch (e) { this.showError(e); }
    },
    async deleteSiteData(site) {
      if (!confirm(`Delete all runs, results, cached pages and the saved login for ${site.domain}?`)) return;
      try {
        const r = await api("DELETE", `/api/sites/${site.domain}/data`);
        this.toast(`Deleted ${r.jobs} run(s) and ${r.cache_pages} cached page(s).`);
        if (this.selectedJob && !this.jobs.find(j => j.id === this.selectedJob.id)) this.selectedJob = null;
        await Promise.all([this.loadSites(), this.loadJobs(), this.loadScrapes()]);
      } catch (e) { this.showError(e); }
    },

    // ---------------------------------------------------------------- visible browser window
    async startLogin(site) {
      const url = prompt(`Login page for ${site.domain}:`, `https://${site.domain}/`);
      if (!url) return;
      if (!confirm(`Log in to ${site.domain}?\n\nOnly continue if this is your own account and the site allows automated access while logged in.`)) return;
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
          this.toast(`Thanks. ${w.domain} is open again; try Find the data once more.`);
          this.blockedPage = null;
        } else {
          await api("POST", `/api/jobs/${w.jobId}/solve/finish`, { account: w.account });
          this.toast("The scrape continues.");
        }
        this.win = null;
        await Promise.all([this.loadSites(), this.loadJobs()]);
      } catch (e) { this.showError(e); }
      finally { this.busy = ""; }
    },
    async cancelWindow() {
      const w = this.win;
      this.win = null; clearTimeout(this.winTimer);
      if (w?.mode === "login") await api("POST", `/api/sites/${w.domain}/login/cancel`).catch(() => {});
    },

    // ---------------------------------------------------------------- activity
    async loadActivity() { try { this.activity = await api("GET", "/api/audit?limit=150"); } catch (e) { this.showError(e); } },
    eventDetail(a) {
      const parts = [];
      if (a.job_id) parts.push("run #" + a.job_id);
      if (a.url) parts.push(a.url); else if (a.domain) parts.push(a.domain);
      if (a.status && a.status !== 200) parts.push("HTTP " + a.status);
      if (a.rendered) parts.push("browser");
      if (a.fmt) parts.push(a.fmt.toUpperCase() + ", " + a.rows + " rows");
      if (a.reason) parts.push(a.reason);
      return parts.join(" · ");
    },
  };
}
