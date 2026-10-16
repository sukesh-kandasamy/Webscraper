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
    throw err;
  }
  return data;
}

function scraper() {
  return {
    status: null,
    urlsText: "",
    fieldsText: "",
    maxPages: 3,
    busy: "",
    error: "",
    analysis: null,
    recipeText: "",
    jobs: [],
    selectedJob: null,
    records: [],
    search: "",
    sortKey: "",
    sortAsc: true,
    pollTimer: null,

    async init() {
      try {
        this.status = await api("GET", "/api/status");
        this.fieldsText = this.status.default_fields.join(", ");
        this.maxPages = Math.min(3, this.status.max_pages);
      } catch (e) { this.error = e.message; }
      await this.loadJobs();
    },

    urls() { return this.urlsText.split("\n").map(s => s.trim()).filter(Boolean); },
    fields() { return this.fieldsText.split(",").map(s => s.trim()).filter(Boolean); },
    useExample(k) { this.urlsText = EXAMPLES[k]; },
    isUrl(v) { return typeof v === "string" && /^https?:\/\//.test(v); },
    columns(rows) {
      const cols = [];
      for (const r of rows || []) for (const k of Object.keys(r)) if (!cols.includes(k)) cols.push(k);
      return cols;
    },

    parseRecipe() {
      try { return JSON.parse(this.recipeText); }
      catch (e) { throw new Error("Recipe JSON is invalid: " + e.message); }
    },

    async analyse(forceAi) {
      if (!this.urls().length) { this.error = "Enter at least one URL"; return; }
      this.error = ""; this.busy = forceAi ? "ai" : "analyse";
      try {
        this.analysis = await api("POST", "/api/analyse",
          { url: this.urls()[0], fields: this.fields(), force_ai: forceAi });
        this.recipeText = JSON.stringify(this.analysis.recipe, null, 2);
      } catch (e) {
        this.error = e.message;
        if (e.detail?.starter_recipe) {
          this.analysis = { domain: e.detail.domain, source: "starter", rows: [], row_count: 0, report: {} };
          this.recipeText = JSON.stringify(e.detail.starter_recipe, null, 2);
        }
      } finally { this.busy = ""; }
    },

    async previewRecipe() {
      this.error = ""; this.busy = "preview";
      try {
        this.analysis = await api("POST", "/api/preview", { url: this.urls()[0], recipe: this.parseRecipe() });
        this.recipeText = JSON.stringify(this.analysis.recipe, null, 2);
      } catch (e) { this.error = e.message; }
      finally { this.busy = ""; }
    },

    async saveRecipe() {
      this.error = "";
      try {
        await api("PUT", `/api/sites/${this.analysis.domain}/recipe`, { recipe: this.parseRecipe() });
        this.analysis.source = "manual";
      } catch (e) { this.error = e.message; }
    },

    async runJob() {
      this.error = ""; this.busy = "run";
      try {
        const { job_id } = await api("POST", "/api/jobs", {
          urls: this.urls(), recipe: this.parseRecipe(), max_pages: this.maxPages,
          recipe_source: this.analysis?.source === "ai" ? "ai" : "manual",
        });
        await this.loadJobs();
        this.openJob(this.jobs.find(j => j.id === job_id));
      } catch (e) { this.error = e.message; }
      finally { this.busy = ""; }
    },

    async loadJobs() {
      try { this.jobs = await api("GET", "/api/jobs"); } catch (e) { this.error = e.message; }
      const running = this.jobs.some(j => ["queued", "running"].includes(j.status));
      clearTimeout(this.pollTimer);
      if (running) this.pollTimer = setTimeout(() => this.loadJobs(), 2000);
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
      this.selectedJob = j; this.search = ""; this.sortKey = "";
      await this.loadRecords();
    },

    async loadRecords() {
      try { this.records = await api("GET", `/api/jobs/${this.selectedJob.id}/records`); }
      catch (e) { this.error = e.message; }
    },

    async stopJob(j) { await api("POST", `/api/jobs/${j.id}/stop`).catch(e => this.error = e.message); },

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
  };
}
