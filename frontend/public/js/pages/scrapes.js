// Saved scrapes: run again, open results, edit, rename, duplicate, delete, re-record (agent scrapes).
function scrapesPage() {
  return {
    scrapes: [],
    scrapeQuery: "",

    async start() { await this.loadScrapes(); },
    async loadScrapes() { try { this.scrapes = await api("GET", "/api/scrapes"); } catch (e) { this.showError(e); } },

    filteredScrapes() {
      const q = this.scrapeQuery.trim().toLowerCase();
      return q ? this.scrapes.filter(s => (s.name + " " + s.domain + " " + s.columns.join(" ")).toLowerCase().includes(q)) : this.scrapes;
    },
    async duplicateScrape(s) {
      try { await api("POST", `/api/scrapes/${s.id}/duplicate`); await this.loadScrapes(); this.toast("Copy created."); }
      catch (e) { this.showError(e); }
    },
    async renameScrape(s) {
      const name = await this.promptDialog("Rename scrape", "Name", s.name, "Rename");
      if (!name || name === s.name) return;
      try { await api("PUT", `/api/scrapes/${s.id}`, { name }); await this.loadScrapes(); }
      catch (e) { this.showError(e); }
    },
    async deleteScrape(s) {
      if (!await this.confirmDialog(`Delete “${s.name}”?`, "The saved setup is removed. Its past results stay under Results until you delete them.")) return;
      try { await api("DELETE", `/api/scrapes/${s.id}`); await this.loadScrapes(); }
      catch (e) { this.showError(e); }
    },
  };
}
