// New scrape (and Edit): 1 link + what you need, 2 columns and preview, 3 name and collect.
function newScrapePage() {
  return {
    step: 1,
    template: null,
    urlMain: "",
    urlsExtra: "",
    showExtra: false,
    fieldsText: "",
    autoColumns: true,
    prompt: "",
    request: null,               // what the AI understood from the prompt (editable)
    requestFor: "",              // the prompt text that request belongs to
    listMode: false,             // list columns instead of describing the data
    newReqColumn: "",
    allPages: false,
    analysis: null,
    recipeText: "",
    newColumn: "",
    scrapeName: "",
    maxPages: 3,
    editingScrapeId: null,
    personalChoice: {},
    personalPurpose: "",
    settings: {},
    loadStep: 0,
    loadTimer: null,

    async start() {
      this.maxPages = Math.min(3, this.status?.max_pages || 3);
      this.$watch("busy", v => this.trackLoading(v));
      if (this.data.scrapeId) return this.loadForEdit(+this.data.scrapeId);
      const t = TEMPLATES.find(x => x.id === this.data.template);
      if (t) this.pickTemplate(t);
      if (this.data.url) { this.urlMain = this.data.url; this.next(); }
    },
    async loadForEdit(id) {
      try {
        const full = await api("GET", `/api/scrapes/${id}`);
        this.editingScrapeId = full.id; this.scrapeName = full.name;
        this.allPages = full.max_pages >= 50; this.maxPages = Math.min(full.max_pages, Math.min(50, this.status?.max_pages || 10));
        const req = full.recipe.request;
        this.request = req || null; this.prompt = req?.prompt || ""; this.requestFor = this.prompt.trim();
        this.listMode = !req; this.fieldsText = req ? "" : (full.recipe.fields || []).map(f => f.name).join(", ");
        this.urlMain = full.urls[0] || ""; this.urlsExtra = full.urls.slice(1).join("\n");
        this.showExtra = full.urls.length > 1;
        this.recipeText = JSON.stringify(full.recipe, null, 2);
        this.step = 2;
        await this.previewRecipe();
      } catch (e) { this.showError(e); }
    },
    urls() {
      return [this.urlMain, ...this.urlsExtra.split("\n")].map(s => s.trim()).filter(Boolean);
    },
    fields() { return this.fieldsText.split(",").map(s => s.trim()).filter(Boolean); },

    // Step-by-step messages while the page is being read, so waiting doesn't feel stuck.
    loadingSteps() {
      if (this.busy === "add") return ["Opening the page…", "Looking for the new column…", "Checking it on every item…"];
      if (this.busy === "details") return ["Opening the first item's page…", "Finding the extra details…", "Checking them…"];
      return ["Opening the page…", "Finding the list of items…",
              this.request ? "Matching the columns you described…" : "Choosing the important columns…", "Checking the result…"];
    },
    trackLoading(v) {
      clearInterval(this.loadTimer);
      this.loadStep = 0;
      if (["analyse", "ai", "add", "details"].includes(v)) {
        this.loadTimer = setInterval(() => { this.loadStep = Math.min(this.loadStep + 1, this.loadingSteps().length - 1); }, 2600);
      }
    },

    // ---------------------------------------------------------------- wizard
    pickTemplate(t) {
      const prev = this.template;
      this.template = prev?.id === t.id ? null : t;
      this.fieldsText = this.template ? this.template.columns.join(", ") : "";
      // Replace the description only if it is empty or still the previous example.
      if (!this.prompt.trim() || this.prompt === prev?.prompt) this.prompt = this.template?.prompt || "";
    },

    promptPlaceholder() {
      return "Example: I need the image, price and brand of each shoe. Only men's shoes under ₹3,000, cheapest first.";
    },
    needsUnderstanding() {
      return !this.listMode && !!this.prompt.trim() && (!this.request || this.requestFor !== this.prompt.trim());
    },
    // Step 1's main button: read the description first, then find the data.
    async next() {
      if (!this.urlMain.trim()) { this.error = { title: "Paste a link first", hint: "Copy the address of a page that lists items." }; return; }
      if (this.needsUnderstanding()) return this.understand();
      return this.analyse(false);
    },
    async understand() {
      this.error = null; this.busy = "understand";
      try {
        this.request = await api("POST", "/api/understand", { prompt: this.prompt.trim(), url: this.urls()[0] || "" });
        this.requestFor = this.prompt.trim();
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },
    removeReqColumn(name) {
      this.request.columns = this.request.columns.filter(c => c.name !== name);
      this.request.filters = this.request.filters.filter(f => f.column !== name);
      if (this.request.sort?.column === name) this.request.sort = { column: "", desc: false };
      if (!this.request.columns.length) this.request.auto = true;
    },
    async renameReqColumn(c) {
      const next = await this.promptDialog("Rename column", `New name for “${this.pretty(c.name)}”`, c.name, "Rename");
      const clean = (next || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
      if (!clean || clean === c.name || this.request.columns.some(x => x.name === clean)) return;
      this.request.filters.forEach(f => { if (f.column === c.name) f.column = clean; });
      if (this.request.sort?.column === c.name) this.request.sort.column = clean;
      c.name = clean; delete c.auto_added;
    },
    addReqColumn() {
      for (const raw of this.newReqColumn.split(",")) {
        const name = raw.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
        if (name && !this.request.columns.some(c => c.name === name)) {
          this.request.columns.push({ name, meaning: raw.trim(), type: "", required: true });
        }
      }
      this.newReqColumn = "";
    },
    removeFilter(i) { this.request.filters.splice(i, 1); },
    sortWords() {
      const s = this.request?.sort || {};
      const kind = (this.request?.columns || []).find(c => c.name === s.column)?.type;
      if (kind === "date") return s.desc ? "newest first" : "oldest first";
      if (["text", "url", "image"].includes(kind)) return s.desc ? "Z → A" : "A → Z";
      return s.desc ? "high → low" : "low → high";
    },
    async analyse(forceAi) {
      if (!this.urlMain.trim()) { this.error = { title: "Paste a link first", hint: "Copy the address of a page that lists items." }; return; }
      this.error = null; this.blockedPage = null; this.busy = forceAi ? "ai" : "analyse";
      try {
        const useRequest = !this.listMode && this.request && this.requestFor === this.prompt.trim();
        this.analysis = await api("POST", "/api/analyse",
          { url: this.urls()[0], fields: this.fields(), auto: this.autoColumns, force_ai: forceAi,
            request: useRequest ? this.request : null });
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
        if (r.detail) {
          r.detail.fields = r.detail.fields.filter(f => f.name !== name);
          if (!r.detail.fields.length) delete r.detail;
        }
        if (r.request) {
          r.request.columns = r.request.columns.filter(c => c.name !== name);
          r.request.filters = r.request.filters.filter(f => f.column !== name);
          if (r.request.sort?.column === name) r.request.sort = { column: "", desc: false };
          this.request = r.request;
        }
        if (r.column_types) delete r.column_types[name];
        this.recipeText = JSON.stringify(r, null, 2);
        await this.previewRecipe();
      } catch (e) { this.showError(e); }
    },

    async renameColumn(name) {
      const next = await this.promptDialog("Rename column", `New name for “${this.pretty(name)}”`, name, "Rename");
      if (!next || next.trim() === name) return;
      const clean = next.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
      if (!clean) return;
      try {
        const r = this.parseRecipe();
        if (r.fields.some(f => f.name === clean)) { this.error = { title: "That column name is already used" }; return; }
        r.fields.forEach(f => { if (f.name === name) f.name = clean; });
        (r.detail?.fields || []).forEach(f => { if (f.name === name) f.name = clean; });
        if (r.detail?.url_field === name) r.detail.url_field = clean;
        if (r.request) {
          r.request.columns.forEach(c => { if (c.name === name) c.name = clean; });
          r.request.filters.forEach(f => { if (f.column === name) f.column = clean; });
          if (r.request.sort?.column === name) r.request.sort.column = clean;
          this.request = r.request;
        }
        if (r.column_types && name in r.column_types) { r.column_types[clean] = r.column_types[name]; delete r.column_types[name]; }
        this.recipeText = JSON.stringify(r, null, 2);
        await this.previewRecipe();
      } catch (e) { this.showError(e); }
    },

    async addColumn() {
      const name = this.newColumn.trim();
      if (!name) return;
      const added = name.split(",").map(s => s.trim()).filter(Boolean);
      const wanted = [...new Set([...this.recipeColumns(), ...added])];
      let request = null;
      try { request = this.parseRecipe().request || null; } catch { request = null; }
      if (request) {
        request = JSON.parse(JSON.stringify(request));
        for (const raw of added) {
          const n = raw.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
          if (n && !request.columns.some(c => c.name === n)) request.columns.push({ name: n, meaning: raw, type: "", required: true });
        }
        request.auto = false;
      }
      this.error = null; this.busy = "add";
      try {
        this.analysis = await api("POST", "/api/analyse",
          { url: this.urls()[0], fields: wanted, auto: false, force_ai: true, request });
        if (request) { this.request = request; }
        this.afterAnalysis();
        this.newColumn = "";
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },

    personalFieldsChosen() { return Object.entries(this.personalChoice).filter(([, v]) => v).map(([k]) => k); },

    // ---- item pages (optional per scrape)
    detailOn() { try { return !!JSON.parse(this.recipeText).detail; } catch { return false; } },
    detailColumns() { try { return (JSON.parse(this.recipeText).detail?.fields || []).map(f => f.name); } catch { return []; } },
    detailSample() { return this.analysis?.detail_row || {}; },
    detailEstimate() {
      const n = this.analysis?.row_count || 0;
      const d = this.status?.delay || [2, 5];
      const per = (d[0] + d[1]) / 2 + 1;
      const mins = Math.max(1, Math.round(n * per / 60));
      return `About ${mins} extra minute${mins === 1 ? "" : "s"} for every page of ${n} items.`;
    },
    async toggleDetails() {
      let r;
      try { r = this.parseRecipe(); } catch (e) { return this.showError(e); }
      if (r.detail) {
        delete r.detail;
        this.recipeText = JSON.stringify(r, null, 2);
        return this.previewRecipe();
      }
      // Ask for the columns the list page doesn't have (from the description), plus anything useful.
      const have = new Set((r.fields || []).map(f => f.name));
      const wanted = (r.request?.columns || []).filter(c => !have.has(c.name));
      for (const name of this.analysis?.absent || []) if (!wanted.some(c => c.name === name)) wanted.push({ name, meaning: "", type: "" });
      this.error = null; this.busy = "details";
      try {
        this.analysis = await api("POST", "/api/details",
          { url: this.urls()[0], recipe: r, columns: wanted, auto: !r.request || r.request.auto || !wanted.length });
        this.afterAnalysis();
        this.toast("Item pages are on. Each run will open every item's page for these details.");
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },
    hasPaging() { return ["next_link", "url_param", "scroll", "click"].includes(this.analysis?.recipe?.pagination?.type); },
    pagesToCollect() { return this.allPages && this.hasPaging() ? 50 : this.maxPages; },

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
            { name: this.scrapeName, urls: this.urls(), recipe, max_pages: this.pagesToCollect() });
          if (run) jobId = (await api("POST", `/api/scrapes/${this.editingScrapeId}/run`,
            { personal_fields: chosen, personal_purpose: this.personalPurpose })).job_id;
        } else {
          const r = await api("POST", "/api/scrapes", {
            name: this.scrapeName || "My scrape", urls: this.urls(), recipe, max_pages: this.pagesToCollect(),
            page_type: this.analysis?.page_type || "", run,
            personal_fields: chosen, personal_purpose: this.personalPurpose,
          });
          jobId = r.job_id; this.editingScrapeId = r.scrape_id;
        }
        if (run && jobId) goTo(`/results/${jobId}`, "Started. You can leave this page; it keeps running.");
        else goTo("/scrapes", `Saved “${this.scrapeName}”. Run it any time with Run again.`);
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },

    // ---- site settings (Advanced mode)
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
      } catch (e) { this.showError(e); }
    },
    async saveRecipeForSite() {
      try {
        await api("PUT", `/api/sites/${this.analysis.domain}/recipe`, { recipe: this.parseRecipe() });
        this.toast("Column setup saved for " + this.analysis.domain + ".");
      } catch (e) { this.showError(e); }
    },
  };
}
