// Settings: appearance, Advanced and Agent switches, sites (limits, pauses, logins), activity log, about.
function settingsPage() {
  return {
    sites: [],
    activity: [],

    async start() { await this.loadSites(); },
    async afterWindow() { await this.loadSites(); },
    async loadSites() { try { this.sites = await api("GET", "/api/sites"); } catch { /* not fatal */ } },

    async forgetSession(site) {
      if (!await this.confirmDialog("Remove saved login?", `You'll need to log in to ${site.session.domain} again before scraping pages that need it.`, "Remove")) return;
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
      if (!await this.confirmDialog(`Delete all data for ${site.domain}?`, "Runs, results, cached pages and the saved login for this site are removed. This can't be undone.")) return;
      try {
        const r = await api("DELETE", `/api/sites/${site.domain}/data`);
        this.toast(`Deleted ${r.jobs} run(s) and ${r.cache_pages} cached page(s).`);
        await this.loadSites();
        this.loadStatus();
      } catch (e) { this.showError(e); }
    },

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
