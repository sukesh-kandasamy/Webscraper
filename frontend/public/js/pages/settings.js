// Settings: appearance, AI models, Advanced and Agent switches, sites (limits, pauses, logins), activity log.
function settingsPage() {
  return {
    sites: [],
    activity: [],
    ai: null,                          // GET /api/ai: what is in use, which providers have a key
    aiForm: { ai: { provider: "", model: "" }, agent: { provider: "", model: "" }, fallback: { provider: "", model: "" } },
    aiModels: {},                      // provider -> {models, suggested, live}
    aiBusy: "",
    aiError: "",                       // why the last save was refused, shown next to Save
    aiRoles: [
      { id: "ai", title: "Building scrapes", help: "Finds the columns on a page, reads your request and plans agent runs." },
      { id: "agent", title: "Agent", help: "Drives the browser step by step in Agent mode." },
      { id: "fallback", title: "Agent backup", help: "Takes over when the agent's AI hits a rate or daily limit, runs out of credit or refuses the key." },
    ],

    async start() {
      this.$watch("aiForm", () => { this.aiError = ""; });
      await Promise.all([this.loadSites(), this.loadAi()]);
    },

    async loadAi() {
      try {
        this.ai = await api("GET", "/api/ai");
        for (const r of ["ai", "agent"]) this.aiForm[r] = { provider: this.ai[r].provider, model: this.ai[r].model };
        this.aiForm.fallback = { ...this.savedBackup() };
        const used = [this.ai.ai.provider, this.ai.agent.provider, this.aiForm.fallback.provider].filter(Boolean);
        await Promise.all([...new Set(used)].map(p => this.loadModels(p)));
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
    savedBackup() { return this.ai?.agent_fallback || { provider: "", model: "" }; },
    saved(role) { return role === "fallback" ? this.savedBackup() : this.ai[role]; },
    async pickProvider(role, p) {
      const f = this.aiForm[role];
      if (f.provider === p) return;
      f.provider = p;
      if (!p) { f.model = ""; return; }                  // the backup's "None"
      await this.loadModels(p);
      const before = this.saved(role);
      f.model = before.provider === p ? before.model : (this.aiModels[p]?.models || [])[0] || "";
    },
    aiDirty() {
      return !!this.ai && ["ai", "agent", "fallback"].some(r =>
        this.aiForm[r].provider !== this.saved(r).provider || this.aiForm[r].model !== this.saved(r).model);
    },
    // A choice that can't be saved, found before asking the app.
    aiProblem() {
      const { agent, fallback } = this.aiForm;
      if (fallback.provider && fallback.provider === agent.provider && fallback.model === agent.model)
        return "The agent backup is the same model as the agent. Pick a different backup model, or None.";
      return "";
    },
    async saveAi() {
      this.aiBusy = "save"; this.aiError = "";
      try {
        const { ai, agent, fallback } = this.aiForm;
        this.ai = await api("PUT", "/api/ai", { ai, agent, agent_fallback: fallback.provider ? fallback : null });
        this.toast("Saved. The app now uses these models.");
        this.loadStatus();
      } catch (e) { this.aiError = "Not saved: " + (e.message || "the app refused this choice."); }
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
    // Site policy (approved / needs approval / never scrape), the terms review and a note: recorded in the activity log.
    async setPolicy(site, policy) {
      try { await api("PUT", `/api/sites/${site.domain}/settings`, { policy }); this.loadSites(); }
      catch (e) { this.showError(e); this.loadSites(); }
    },
    async setTerms(site, terms) {
      try { await api("PUT", `/api/sites/${site.domain}/settings`, { terms }); this.loadSites(); }
      catch (e) { this.showError(e); this.loadSites(); }
    },
    async editSiteNote(site) {
      const note = await this.promptDialog(`Note for ${site.domain}`, "Which terms you checked, why the data is needed, or limits to respect.",
                                           site.review?.note || "", "Save note");
      if (note === null) return;
      try { await api("PUT", `/api/sites/${site.domain}/settings`, { note }); this.loadSites(); }
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
      if (a.run) parts.push(a.run);
      if (a.url) parts.push(a.url); else if (a.domain) parts.push(a.domain);
      if (a.status && a.status !== 200) parts.push("HTTP " + a.status);
      if (a.rendered) parts.push("browser");
      if (a.fmt) parts.push(a.fmt.toUpperCase() + ", " + a.rows + " rows");
      if (a.reason) parts.push(a.reason);
      return parts.join(" · ");
    },
  };
}
