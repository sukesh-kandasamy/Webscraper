// Settings: appearance, AI models, Advanced and Agent switches, sites (limits, pauses, logins), activity log.
function settingsPage() {
  return {
    sites: [],
    activity: [],
    ai: null,                          // GET /api/ai: what is in use, which providers have a key
    aiForm: { ai: { provider: "", model: "" }, agent: { provider: "", model: "" } },
    aiModels: {},                      // provider -> {models, suggested, live}
    aiBusy: "",
    aiRoles: [
      { id: "ai", title: "Building scrapes", help: "Finds the columns on a page, reads your request and plans agent runs." },
      { id: "agent", title: "Agent", help: "Drives the browser step by step in Agent mode." },
    ],

    async start() { await Promise.all([this.loadSites(), this.loadAi()]); },

    async loadAi() {
      try {
        this.ai = await api("GET", "/api/ai");
        for (const r of ["ai", "agent"]) this.aiForm[r] = { provider: this.ai[r].provider, model: this.ai[r].model };
        await Promise.all([...new Set([this.ai.ai.provider, this.ai.agent.provider])].map(p => this.loadModels(p)));
      } catch (e) { this.showError(e); }
    },
    async loadModels(p) {
      if (this.aiModels[p]) return;
      try { this.aiModels = { ...this.aiModels, [p]: await api("GET", `/api/ai/models/${p}`) }; }
      catch { /* the model in use still shows */ }
    },
    modelGroups(role) {
      const f = this.aiForm[role], list = this.aiModels[f.provider] || {};
      const all = list.models || [];
      const rec = (list.suggested || []).filter(m => all.includes(m));
      const rest = all.filter(m => !rec.includes(m));
      if (f.model && !all.includes(f.model)) rest.unshift(f.model);
      return { rec, rest };
    },
    async pickProvider(role, p) {
      const f = this.aiForm[role];
      if (f.provider === p) return;
      f.provider = p;
      await this.loadModels(p);
      f.model = this.ai[role].provider === p ? this.ai[role].model : (this.aiModels[p]?.models || [])[0] || "";
    },
    aiDirty() {
      return !!this.ai && ["ai", "agent"].some(r => this.aiForm[r].provider !== this.ai[r].provider || this.aiForm[r].model !== this.ai[r].model);
    },
    agentSeesText() {
      return ["anthropic", "openai"].includes(this.aiForm.agent.provider)
        ? "Sees screenshots of the page."
        : "Text only: it reads the page but can't see screenshots, so it may need more steps.";
    },
    async saveAi() {
      this.aiBusy = "save";
      try {
        this.ai = await api("PUT", "/api/ai", this.aiForm);
        this.toast("Saved. The app now uses these models.");
        this.loadStatus();
      } catch (e) { this.showError(e); }
      finally { this.aiBusy = ""; }
    },
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
      if (a.job) parts.push(a.job);
      if (a.url) parts.push(a.url); else if (a.domain) parts.push(a.domain);
      if (a.status && a.status !== 200) parts.push("HTTP " + a.status);
      if (a.rendered) parts.push("browser");
      if (a.fmt) parts.push(a.fmt.toUpperCase() + ", " + a.rows + " rows");
      if (a.reason) parts.push(a.reason);
      return parts.join(" · ");
    },
  };
}
