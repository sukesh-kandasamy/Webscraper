// Home: start a scrape, numbers for the week, saved scrapes and recent results.
function homePage() {
  return {
    usesJobs: true,
    scrapes: [],
    urlMain: "",

    async start() { await this.loadScrapes(); },
    async loadScrapes() { try { this.scrapes = await api("GET", `/api/scrapes?mode=${this.pageMode()}`); } catch { /* shown elsewhere */ } },

    stats() {
      const now = Date.now() / 1000, day = 86400;
      const recent = this.jobs.filter(j => now - j.created_at < 7 * day);
      return {
        scrapes: this.scrapes.length,
        rowsWeek: recent.reduce((n, j) => n + (j.rows || 0), 0),
        runsWeek: recent.length,
        active: this.activeJobs().length,
      };
    },
  };
}
