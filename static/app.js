const EXAMPLES = {
  indiamart: "https://dir.indiamart.com/impcat/pvc-pipes.html",
  tradeindia: "https://www.tradeindia.com/manufacturers/upvc-pipe-fittings.html",
};

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(typeof data.detail === "string" ? data.detail
      : data.detail?.message || `Request failed (${res.status})`);
    err.detail = data.detail;
    err.status = res.status;
    throw err;
  }
  return data;
}

const EVENT_LABELS = {
  fetch: "Fetched", job_started: "Job started", job_paused: "Job paused", job_blocked: "Job blocked",
  job_deleted: "Job deleted", export: "Exported", session_saved: "Session saved",
  session_forgotten: "Session forgotten", login_confirmed: "Own account confirmed",
  settings_changed: "Settings changed", cooldown: "Cool-down started", cooldown_cleared: "Cool-down cleared",
  challenge_solved: "Check solved by you", window_opened: "Browser window opened",
  recipe_built: "Recipe built by AI", recipe_saved: "Recipe saved", retention_purge: "Old data removed",
  site_data_deleted: "Site data deleted", retry_after: "Waited (Retry-After)",
};

function scraper() {
  return {
    status: null,
    urlsText: "",
    fieldsText: "",
    autoColumns: true,
    newColumn: "",
    maxPages: 3,
    busy: "",
    error: "",
    notice: "",
    analysis: null,
    recipeText: "",
    settings: {},
    jobs: [],
    selectedJob: null,
    records: [],
    showPersonal: false,
    search: "",
    sortKey: "",
    sortAsc: true,
    pollTimer: null,
    sites: [],
    activity: [],
    activityOpen: false,
    personalChoice: {},          // field -> collect raw?
    personalPurpose: "",
    blockedPage: null,           // {domain, url, kind, message} when analyse hit a challenge
    // Visible browser window dialog
    win: null,                   // {mode: login|site|job, domain, jobId, url, status, message, account, started}
    winTimer: null,

    async init() {
      try {
        this.status = await api("GET", "/api/status");
        this.fieldsText = this.status.default_fields.join(", ");
        this.maxPages = Math.min(3, this.status.max_pages);
      } catch (e) { this.error = e.message; }
      await Promise.all([this.loadJobs(), this.loadSites()]);
    },

    // ---- small helpers -------------------------------------------------------------
    urls() { return this.urlsText.split("\n").map(s => s.trim()).filter(Boolean); },
    fields() { return this.fieldsText.split(",").map(s => s.trim()).filter(Boolean); },
    useExample(k) { this.urlsText = EXAMPLES[k]; },
    isUrl(v) { return typeof v === "string" && /^https?:\/\//.test(v); },
    hostOf(u) { try { return new URL(u).hostname; } catch { return ""; } },
    statusChip(s) {
      return { done: "chip-green", running: "chip-blue", queued: "chip-amber", stopped: "chip-gray",
               blocked: "chip-red", error: "chip-red", needs_you: "chip-amber" }[s] || "chip-gray";
    },
    statusLabel(s) { return s === "needs_you" ? "needs you" : s; },
    columns(rows) {
      const cols = [];
      for (const r of rows || []) for (const k of Object.keys(r)) if (!cols.includes(k)) cols.push(k);
      return cols;
    },
    timeAgo(ts) {
      const s = Math.max(0, Date.now() / 1000 - ts);
      if (s < 60) return Math.round(s) + "s ago";
      if (s < 3600) return Math.round(s / 60) + "m ago";
      if (s < 86400) return Math.round(s / 3600) + "h ago";
      return Math.round(s / 86400) + "d ago";
    },
    eventLabel(e) { return EVENT_LABELS[e] || e; },
    eventDetail(a) {
      const parts = [];
      if (a.job_id) parts.push("job #" + a.job_id);
      if (a.url) parts.push(a.url);
      else if (a.domain) parts.push(a.domain);
      if (a.status) parts.push("HTTP " + a.status);
      if (a.logged_in) parts.push("logged in");
      if (a.rendered) parts.push("browser");
      if (a.kind) parts.push(a.kind);
      if (a.fmt) parts.push(a.fmt.toUpperCase() + ", " + a.rows + " rows" + (a.personal ? ", with personal data" : ""));
      if (a.reason) parts.push(a.reason);
      if (a.minutes) parts.push(a.minutes + " min");
      if (a.account) parts.push(a.account);
      return parts.join(" · ");
    },
    needsYouJobs() { return this.jobs.filter(j => j.status === "needs_you"); },

    parseRecipe() {
      try { return JSON.parse(this.recipeText); }
      catch (e) { throw new Error("Recipe JSON is invalid: " + e.message); }
    },

    // Central error handling: "needs a person" pages get their own banner.
    handleError(e, retry) {
      const d = e.detail || {};
      if (e.status === 409 && d.code === "needs_human") {
        this.blockedPage = d;
        this.error = "";
        return;
      }
      this.error = e.message;
    },

    // ---- analyse / preview ------------------------------------------------------------
    async analyse(forceAi) {
      if (!this.urls().length) { this.error = "Enter at least one URL"; return; }
      this.error = ""; this.notice = ""; this.blockedPage = null; this.busy = forceAi ? "ai" : "analyse";
      try {
        this.analysis = await api("POST", "/api/analyse",
          { url: this.urls()[0], fields: this.fields(), auto: this.autoColumns, force_ai: forceAi });
        this.afterAnalysis();
      } catch (e) {
        if (e.detail?.starter_recipe) {
          this.error = e.message;
          this.analysis = { domain: e.detail.domain, source: "starter", rows: [], row_count: 0, report: {},
                            personal_fields: [], settings: {} };
          this.recipeText = JSON.stringify(e.detail.starter_recipe, null, 2);
        } else {
          this.handleError(e, () => this.analyse(forceAi));
        }
      } finally { this.busy = ""; }
    },

    afterAnalysis() {
      this.recipeText = JSON.stringify(this.analysis.recipe, null, 2);
      this.applySettings(this.analysis.settings);
      this.personalChoice = Object.fromEntries((this.analysis.personal_fields || []).map(f => [f, false]));
      this.personalPurpose = "";
      this.loadSites();
    },

    pageTypeLabel(t) {
      return { ecommerce: "E-commerce", jobs: "Jobs", real_estate: "Real estate",
               stocks_finance: "Stocks / finance", directory: "Directory", news_articles: "News / articles",
               events: "Events", other: "Other listing" }[t] || t;
    },

    recipeColumns() {
      try { return (JSON.parse(this.recipeText).fields || []).map(f => f.name); } catch { return []; }
    },

    // Remove a column: edit the recipe and re-run the preview (no AI needed).
    async removeColumn(name) {
      try {
        const r = this.parseRecipe();
        r.fields = (r.fields || []).filter(f => f.name !== name);
        if (!r.fields.length) { this.error = "Keep at least one column."; return; }
        this.recipeText = JSON.stringify(r, null, 2);
        await this.previewRecipe();
      } catch (e) { this.error = e.message; }
    },

    // Add a column: ask the AI to rebuild with the current columns plus the new one.
    async addColumn() {
      const name = this.newColumn.trim();
      if (!name) return;
      const wanted = [...new Set([...this.recipeColumns(), ...name.split(",").map(s => s.trim()).filter(Boolean)])];
      this.error = ""; this.busy = "add";
      try {
        this.analysis = await api("POST", "/api/analyse",
          { url: this.urls()[0], fields: wanted, auto: false, force_ai: true });
        this.afterAnalysis();
        this.newColumn = "";
      } catch (e) { this.handleError(e, () => this.addColumn()); }
      finally { this.busy = ""; }
    },

    async previewRecipe() {
      this.error = ""; this.busy = "preview";
      try {
        this.analysis = await api("POST", "/api/preview", { url: this.urls()[0], recipe: this.parseRecipe() });
        this.afterAnalysis();
      } catch (e) { this.handleError(e, () => this.previewRecipe()); }
      finally { this.busy = ""; }
    },

    // ---- site settings ----------------------------------------------------------------
    settingsSummary() {
      const s = this.settings, d = this.status?.delay || [];
      const lo = s.min_delay ?? d[0], hi = s.max_delay ?? d[1];
      const bits = [`delay ${lo}–${hi}s`, `rendering: ${s.render || "auto"}`];
      if (s.daily_budget) bits.push(`budget ${s.daily_budget}/day`);
      if (s.ai_allowed === false) bits.push("no AI");
      if (s.blocked) bits.push("blocked");
      return "· " + bits.join(" · ");
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
      this.error = "";
      try {
        const s = this.settings;
        const body = { render: s.render, honest_ua: s.honest_ua, ai_allowed: s.ai_allowed,
                       use_session: s.use_session, blocked: s.blocked };
        for (const k of ["min_delay", "max_delay", "daily_budget"]) {
          if (s[k] !== null && s[k] !== "") body[k] = s[k];
        }
        this.applySettings(await api("PUT", `/api/sites/${this.analysis.domain}/settings`, body));
        this.notice = "Site settings saved.";
        this.loadSites();
      } catch (e) { this.error = e.message; }
    },

    async saveRecipe() {
      this.error = "";
      try {
        await api("PUT", `/api/sites/${this.analysis.domain}/recipe`, { recipe: this.parseRecipe() });
        this.analysis.source = "manual";
        this.notice = "Recipe saved for " + this.analysis.domain + ".";
      } catch (e) { this.error = e.message; }
    },

    // ---- jobs -------------------------------------------------------------------------
    personalFieldsChosen() { return Object.entries(this.personalChoice).filter(([, v]) => v).map(([k]) => k); },

    async runJob() {
      this.error = ""; this.busy = "run";
      try {
        const chosen = this.personalFieldsChosen();
        if (chosen.length && this.personalPurpose.trim().length < 10) {
          throw new Error("Say why you need the personal data (at least 10 characters), or untick it.");
        }
        const { job_id } = await api("POST", "/api/jobs", {
          urls: this.urls(), recipe: this.parseRecipe(), max_pages: this.maxPages,
          recipe_source: this.analysis?.source === "ai" ? "ai" : "manual",
          personal_fields: chosen, personal_purpose: this.personalPurpose,
        });
        await this.loadJobs();
        this.openJob(this.jobs.find(j => j.id === job_id));
      } catch (e) { this.handleError(e, () => this.runJob()); }
      finally { this.busy = ""; }
    },

    async loadJobs() {
      try { this.jobs = await api("GET", "/api/jobs"); } catch (e) { this.error = e.message; }
      const active = this.jobs.some(j => ["queued", "running"].includes(j.status));
      clearTimeout(this.pollTimer);
      this.pollTimer = setTimeout(() => this.loadJobs(), active ? 2000 : 8000);
      if (this.selectedJob) {
        const fresh = this.jobs.find(j => j.id === this.selectedJob.id);
        if (fresh && (fresh.rows !== this.selectedJob.rows || fresh.status !== this.selectedJob.status)) {
          this.selectedJob = fresh;
          await this.loadRecords();
        }
      }
    },

    async openJob(j) {
      if (!j) return;
      this.selectedJob = j; this.search = ""; this.sortKey = ""; this.showPersonal = false;
      await this.loadRecords();
    },

    async loadRecords() {
      try {
        this.records = await api("GET", `/api/jobs/${this.selectedJob.id}/records?show_personal=${this.showPersonal}`);
      } catch (e) { this.error = e.message; }
    },

    exportUrl(fmt) {
      return `/api/jobs/${this.selectedJob?.id}/export?fmt=${fmt}&show_personal=${this.showPersonal}`;
    },

    async stopJob(j) {
      await api("POST", `/api/jobs/${j.id}/stop`).catch(e => this.error = e.message);
      this.loadJobs();
    },

    async resumeJob(j) {
      try { await api("POST", `/api/jobs/${j.id}/resume`); this.loadJobs(); }
      catch (e) { this.error = e.message; }
    },

    async toggleKeep(j) {
      try { await api("POST", `/api/jobs/${j.id}/keep`, { keep: !j.keep }); j.keep = !j.keep; }
      catch (e) { this.error = e.message; }
    },

    async deleteJob(j) {
      if (!confirm(`Delete job #${j.id} and its ${j.rows} rows?`)) return;
      try {
        await api("DELETE", `/api/jobs/${j.id}`);
        if (this.selectedJob?.id === j.id) { this.selectedJob = null; this.records = []; }
        await this.loadJobs();
      } catch (e) { this.error = e.message; }
    },

    sortBy(c) {
      if (this.sortKey === c) this.sortAsc = !this.sortAsc; else { this.sortKey = c; this.sortAsc = true; }
    },

    filtered() {
      const q = this.search.toLowerCase();
      let rows = q ? this.records.filter(r => Object.values(r).some(v => String(v).toLowerCase().includes(q)))
                   : this.records.slice();
      if (this.sortKey) {
        const k = this.sortKey, dir = this.sortAsc ? 1 : -1;
        const num = v => parseFloat(String(v).replace(/,/g, ""));
        rows.sort((a, b) => {
          const x = num(a[k]), y = num(b[k]);
          if (!isNaN(x) && !isNaN(y)) return (x - y) * dir;
          return String(a[k] ?? "").localeCompare(String(b[k] ?? "")) * dir;
        });
      }
      return rows;
    },

    // ---- sites & sessions -----------------------------------------------------------
    async loadSites() {
      try { this.sites = await api("GET", "/api/sites"); } catch (e) { /* not fatal */ }
    },

    async forgetSession(site) {
      if (!confirm(`Forget the saved session for ${site.session.domain}? You will need to log in again.`)) return;
      try { await api("DELETE", `/api/sessions/${site.session.domain}`); this.loadSites(); }
      catch (e) { this.error = e.message; }
    },

    async clearCooldown(site) {
      try { await api("DELETE", `/api/sites/${site.domain}/cooldown`); this.loadSites(); }
      catch (e) { this.error = e.message; }
    },

    async toggleBlocked(site) {
      try {
        await api("PUT", `/api/sites/${site.domain}/settings`, { blocked: !site.settings?.blocked });
        this.loadSites();
      } catch (e) { this.error = e.message; }
    },

    async deleteSiteData(site) {
      if (!confirm(`Delete all jobs, results, cached pages and the saved session for ${site.domain}?`)) return;
      try {
        const r = await api("DELETE", `/api/sites/${site.domain}/data`);
        this.notice = `Deleted ${r.jobs} job(s), ${r.cache_pages} cached page(s)` + (r.session ? " and the session." : ".");
        if (this.selectedJob && !this.jobs.find(j => j.id === this.selectedJob.id)) this.selectedJob = null;
        await Promise.all([this.loadSites(), this.loadJobs()]);
      } catch (e) { this.error = e.message; }
    },

    // ---- visible browser windows (log in / solve a check) ---------------------------
    async startLogin(site) {
      const url = prompt(`Login page for ${site.domain}:`, `https://${site.domain}/`);
      if (!url) return;
      if (!confirm(`Log in to ${site.domain}?\n\nOnly continue if this is your own account and the site allows automated access while logged in.`)) return;
      this.win = { mode: "login", domain: site.domain, url, status: "starting", message: "", account: site.session?.account || "" };
      try {
        const w = await api("POST", `/api/sites/${site.domain}/login`, { url, own_account: true });
        this.trackWindow(`login:${site.domain}`, w);
      } catch (e) {
        this.win = null;
        this.handleError(e, () => this.startLogin(site));
      }
    },

    async startSiteSolve() {
      const d = this.blockedPage;
      this.win = { mode: "site", domain: d.domain, url: d.url, status: "starting", message: "", account: "" };
      try {
        const w = await api("POST", `/api/sites/${d.domain}/solve`, { url: d.url });
        this.trackWindow(`site:${d.domain}`, w);
      } catch (e) { this.win = null; this.handleError(e, () => this.startSiteSolve()); }
    },

    async startJobSolve(j) {
      this.win = { mode: "job", jobId: j.id, domain: this.hostOf(j.paused_url), url: j.paused_url,
                   kind: j.paused_kind, status: "starting", message: "", account: "" };
      try {
        const w = await api("POST", `/api/jobs/${j.id}/solve`);
        this.trackWindow(`job:${j.id}`, w);
      } catch (e) { this.win = null; this.handleError(e, () => this.startJobSolve(j)); }
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
          this.notice = `Logged-in session saved for ${r.domain}` + (r.account ? ` (${r.account})` : "") +
                        `. It is encrypted and expires in ${Math.round(r.expires_in_hours / 24)} days.` +
                        (r.warning ? " " + r.warning : "");
        } else if (w.mode === "site") {
          await api("POST", `/api/sites/${w.domain}/solve/finish`, { account: w.account });
          this.notice = `Thanks. The check on ${w.domain} is passed; try Analyse again.`;
          this.blockedPage = null;
        } else {
          await api("POST", `/api/jobs/${w.jobId}/solve/finish`, { account: w.account });
          this.notice = `Job #${w.jobId} resumed.`;
        }
        this.win = null;
        await Promise.all([this.loadSites(), this.loadJobs()]);
      } catch (e) { this.error = e.message; }
      finally { this.busy = ""; }
    },

    async cancelWindow() {
      const w = this.win;
      this.win = null;
      clearTimeout(this.winTimer);
      if (w?.mode === "login") await api("POST", `/api/sites/${w.domain}/login/cancel`).catch(() => {});
    },

    // ---- activity log -----------------------------------------------------------------
    async loadActivity() {
      try { this.activity = await api("GET", "/api/audit?limit=150"); } catch (e) { this.error = e.message; }
    },
  };
}
