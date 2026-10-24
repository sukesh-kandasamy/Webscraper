// Results: every run, its data (search, sort, columns, download), quality report and changes since the last run.
function resultsPage() {
  return {
    usesJobs: true,
    selectedJob: null,
    records: [],
    showPersonal: false,
    search: "",
    sortKey: "",
    sortAsc: true,
    hiddenCols: [],
    colMenu: false,
    resultTab: "data",
    compare: null,
    compareError: "",
    compareView: "added",
    page: 1,
    pageSize: 50,
    notFound: false,

    async start() {
      this.$watch("search", () => { this.page = 1; });
      window.addEventListener("popstate", () => this.openFromAddress());
      await this.openFromAddress(this.data.tab);
    },
    // /results/42?tab=quality opens that run on that tab.
    async openFromAddress(tab) {
      const m = location.pathname.match(/^\/results\/(\d+)/);
      if (!m) { this.selectedJob = null; this.records = []; return; }
      let j = this.jobs.find(x => x.id === +m[1]);
      if (!j) {
        try { j = { progress: "", columns: [], ...(await api("GET", `/api/jobs/${m[1]}`)) }; }
        catch { this.notFound = true; return; }
      }
      await this.select(j, tab || new URLSearchParams(location.search).get("tab") || "data");
    },
    // Opening another run stays on this page and updates the address (back / forward work).
    async openJob(j) {
      if (!j) return;
      history.pushState({}, "", `/results/${j.id}`);
      await this.select(j, "data");
    },
    async select(j, tab = "data") {
      this.notFound = false;
      this.selectedJob = j; this.search = ""; this.sortKey = ""; this.showPersonal = false; this.page = 1;
      this.compare = null; this.compareError = "";
      this.hiddenCols = store("hidden:" + (j.scrape_id || j.id)) || [];
      this.resultTab = ["quality", "changes"].includes(tab) ? tab : "data";
      await this.loadRecords();
      if (this.resultTab === "changes") this.loadCompare();
    },
    // Keep the open run fresh while it collects.
    async afterJobs() {
      if (!this.selectedJob) return;
      const fresh = this.jobs.find(j => j.id === this.selectedJob.id);
      if (fresh && (fresh.rows !== this.selectedJob.rows || fresh.status !== this.selectedJob.status)) {
        this.selectedJob = fresh;
        await this.loadRecords();
      } else if (fresh) { this.selectedJob = fresh; }
    },
    async loadRecords() {
      try {
        this.records = await api("GET", `/api/jobs/${this.selectedJob.id}/records?show_personal=${this.showPersonal}`);
      } catch (e) { this.showError(e); }
    },
    async repairScrape(job) {
      this.busy = "repair";
      try {
        await api("POST", `/api/scrapes/${job.scrape_id}/repair`);
        if (await this.confirmDialog("Columns re-detected", "The AI looked at the page again and updated this scrape's setup. Run it again now?", "Run again", false)) {
          await this.runAgainFromJob(job);
        }
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
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

    async resumeJob(j) {
      try { await api("POST", `/api/jobs/${j.id}/resume`); this.loadJobs(); } catch (e) { this.showError(e); }
    },
    async toggleKeep(j) {
      try { await api("POST", `/api/jobs/${j.id}/keep`, { keep: !j.keep }); j.keep = !j.keep; } catch (e) { this.showError(e); }
    },
    async deleteJob(j) {
      if (!await this.confirmDialog("Delete this run?", `This removes the run and its ${this.fmt(j.rows)} rows. This can't be undone.`)) return;
      try {
        await api("DELETE", `/api/jobs/${j.id}`);
        if (this.selectedJob?.id === j.id) { this.selectedJob = null; this.records = []; history.pushState({}, "", "/results"); }
        await this.loadJobs();
      } catch (e) { this.showError(e); }
    },
    resultTabs() {
      const q = this.selectedJob?.quality || {};
      const warn = (q.warnings || []).length;
      const tabs = [{ id: "data", label: "Data", badge: this.fmt(this.selectedJob?.rows) },
                    { id: "quality", label: "Quality", badge: warn ? String(warn) : "", warn: !!warn }];
      if (this.selectedJob?.scrape_id) tabs.push({ id: "changes", label: "Changes" });
      return tabs;
    },
    openResultTab(id) {
      this.resultTab = id;
      if (this.selectedJob) history.replaceState({}, "", `/results/${this.selectedJob.id}` + (id === "data" ? "" : `?tab=${id}`));
      if (id === "changes" && !this.compare) this.loadCompare();
    },
    qualityTiles() {
      const q = this.selectedJob?.quality || {};
      const tiles = [
        { label: "Rows saved", value: this.fmt(q.rows) },
        { label: "Pages read", value: this.fmt(q.pages_ok), note: q.pages_failed?.length ? `${q.pages_failed.length} failed` : "", warn: !!q.pages_failed?.length },
        { label: "Duplicates removed", value: this.fmt(q.duplicates) },
        { label: "Didn't match request", value: this.fmt(q.excluded), note: q.trimmed ? `${this.fmt(q.trimmed)} over the limit` : "" },
      ];
      if (q.coverage !== null && q.coverage !== undefined) {
        tiles.push({ label: "Coverage", value: Math.round(q.coverage * 100) + "%", note: `of about ${this.fmt(q.expected_total)} on the site`, warn: q.coverage < 0.9 });
      }
      if (q.route_steps) {
        tiles.push({ label: "Steps replayed", value: this.fmt(q.route_steps), note: "recorded by the agent · no AI" });
      }
      if (q.details?.done || q.details?.failed) {
        tiles.push({ label: "Item pages read", value: this.fmt(q.details.done), note: q.details.failed ? `${q.details.failed} failed` : "", warn: !!q.details.failed });
      }
      return tiles;
    },
    async loadCompare() {
      this.busy = "compare"; this.compareError = ""; this.compare = null;
      try {
        this.compare = await api("GET", `/api/jobs/${this.selectedJob.id}/compare?show_personal=${this.showPersonal}`);
        this.compareView = this.compare.counts.added ? "added" : this.compare.counts.changed ? "changed" : "removed";
      } catch (e) { this.compareError = e.status === 404 ? "This is the first finished run of this scrape, so there is nothing to compare with yet." : this.explain(e).hint; }
      finally { this.busy = ""; }
    },
    compareRows() { return this.compare?.[this.compareView] || []; },
    rowOf(item) { return item.row || item; },
    compareColumns() { return this.columns(this.compareRows().slice(0, 50).map(i => this.rowOf(i))).filter(c => !/_(currency|unit)$/.test(c)).slice(0, 8); },

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
    totalPages() { return Math.max(1, Math.ceil(this.filtered().length / this.pageSize)); },
    pagedRows() {
      const p = Math.min(this.page, this.totalPages());
      return this.filtered().slice((p - 1) * this.pageSize, p * this.pageSize);
    },
    rangeText() {
      const n = this.filtered().length;
      if (!n) return "0 rows";
      const p = Math.min(this.page, this.totalPages());
      return `${this.fmt((p - 1) * this.pageSize + 1)}–${this.fmt(Math.min(p * this.pageSize, n))} of ${this.fmt(n)}`;
    },
    progressPct(j) {
      if (!j) return 0;
      if (j.status === "done") return 100;
      return j.pages_total ? Math.max(5, Math.round(100 * j.pages_done / j.pages_total)) : 5;
    },
  };
}
