// Agent mode: describe the goal, approve the plan, watch the agent work, open the results.
function agentPage() {
  return {
    agentGoal: "",
    agentSite: "",
    agentRun: null,              // the run on screen (with its steps)
    agentRuns: [],
    agentNewCol: "",
    agentLimits: { steps: 40, minutes: 10, ai_calls: 60 },
    agentTimer: null,
    agentExamples: AGENT_EXAMPLES,
    historyOpen: false,

    async start() {
      if (this.status?.agent?.limits) this.agentLimits = { ...this.status.agent.limits };
      await this.loadAgentRuns();
      window.addEventListener("popstate", () => this.openFromAddress());
      await this.openFromAddress();
    },
    // /agent/runs/12 opens that run; /agent shows a new one.
    async openFromAddress() {
      const m = location.pathname.match(/^\/agent\/runs\/(\d+)/);
      if (m) await this.openAgentRun(+m[1], false);
      else { clearTimeout(this.agentTimer); this.agentRun = null; }
    },

    agentProblem() {
      const a = this.status?.agent;
      if (!a) return "";
      if (!a.installed) return "The agent's environment isn't installed. In the project folder run:  python -m venv .venv-agent  then  .venv-agent\\Scripts\\pip install -r agent_worker\\requirements.txt";
      if (!a.key_set) return `It needs an AI key for ${a.provider} in .env.`;
      if (!a.browser) return "It needs Microsoft Edge or Google Chrome installed.";
      return "";
    },
    agentBusyCount() { return this.agentRuns.filter(r => ["queued", "navigating", "collecting", "fixing"].includes(r.status)).length; },
    agentActive() { return ["queued", "navigating", "collecting", "fixing"].includes(this.agentRun?.status); },
    fixLabel(kind) {
      return { item_pages: "Item pages", more_pages: "More pages", pagination: "Next-page control",
               renavigate: "Found the way again" }[kind] || kind;
    },
    agentStatusLabel(s) {
      return { draft: "Plan ready", queued: "Starting", navigating: "Finding the results", collecting: "Collecting",
               fixing: "Checking & fixing",
               done: "Finished", stopped: "Stopped", needs_you: "Needs you", limit: "Hit a limit", error: "Failed" }[s] || s;
    },
    agentChip(s) {
      return { done: "chip-green", navigating: "chip-blue", collecting: "chip-blue", fixing: "chip-blue", queued: "chip-amber", draft: "chip-gray",
               needs_you: "chip-amber", limit: "chip-amber", stopped: "chip-gray", error: "chip-red" }[s] || "chip-gray";
    },
    agentSteps() { return this.agentRun?.steps_list || []; },
    // Run history grouped like Google's apps: Today, then Earlier.
    agentGroups() {
      const today = new Date(); today.setHours(0, 0, 0, 0);
      const t = today.getTime() / 1000;
      return [{ label: "Today", runs: this.agentRuns.filter(r => r.created_at >= t) },
              { label: "Earlier", runs: this.agentRuns.filter(r => r.created_at < t) }].filter(g => g.runs.length);
    },
    runDot(s) {
      return { done: "bg-success", draft: "bg-outline", queued: "bg-warning", navigating: "bg-primary animate-pulse",
               collecting: "bg-primary animate-pulse", fixing: "bg-primary animate-pulse", needs_you: "bg-warning",
               limit: "bg-warning", error: "bg-error", stopped: "bg-outline" }[s] || "bg-outline";
    },
    stepTone(kind) {
      return { action: "bg-primary-container text-on-primary-container", thought: "bg-surface-container-high text-on-surface-variant",
               info: "bg-success-container text-on-success-container", warn: "bg-warning-container text-on-warning-container" }[kind]
             || "bg-surface-container-high text-on-surface-variant";
    },
    agentTiles() {
      const r = this.agentRun || {};
      const secs = r.elapsed_s || 0;
      return [
        { label: "Agent steps", value: `${r.steps || 0} / ${r.limits?.steps || "—"}` },
        { label: "AI calls", value: `${r.ai_calls || 0} / ${r.limits?.ai_calls || "—"}` },
        { label: "Time", value: secs >= 60 ? `${Math.floor(secs / 60)}m ${secs % 60}s` : `${secs}s` },
        { label: "Rows", value: this.fmt(r.job?.rows || 0) },
      ];
    },
    async loadAgentRuns() {
      if (!this.status?.agent?.enabled) return;
      try { this.agentRuns = await api("GET", "/api/agent/runs"); } catch { /* not fatal */ }
    },
    newAgentRun() {
      clearTimeout(this.agentTimer); this.agentRun = null; this.error = null;
      if (location.pathname !== "/agent") history.pushState({}, "", "/agent");
    },
    async planAgent() {
      if (!this.agentGoal.trim() || !this.agentSite.trim()) {
        this.error = { title: "Say what to collect and on which site", hint: "Fill in both boxes, or click an example." }; return;
      }
      this.error = null; this.busy = "agent-plan";
      try {
        this.agentRun = await api("POST", "/api/agent/plan", { goal: this.agentGoal.trim(), site: this.agentSite.trim() });
        history.pushState({}, "", `/agent/runs/${this.agentRun.id}`);
        this.agentLimits = { ...(this.status?.agent?.limits || this.agentLimits) };
        this.loadAgentRuns();
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },
    agentRemoveColumn(name) {
      const req = this.agentRun.plan.request;
      req.columns = req.columns.filter(c => c.name !== name);
      req.filters = req.filters.filter(f => f.column !== name);
      if (req.sort.column === name) req.sort = { column: "", desc: false };
    },
    agentAddColumn() {
      const req = this.agentRun.plan.request;
      for (const raw of this.agentNewCol.split(",")) {
        const name = raw.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
        if (name && !req.columns.some(c => c.name === name)) req.columns.push({ name, meaning: raw.trim(), type: "", required: true });
      }
      this.agentNewCol = "";
    },
    async discardAgentPlan() {
      const r = this.agentRun;
      this.agentRun = null;
      history.pushState({}, "", "/agent");
      if (r?.status === "draft") { await api("DELETE", `/api/agent/runs/${r.id}`).catch(() => {}); this.loadAgentRuns(); }
    },
    async startAgent() {
      const r = this.agentRun;
      this.error = null; this.busy = "agent-start";
      try {
        r.plan.navigation = r.plan.navigation.map(s => s.trim()).filter(Boolean);
        await api("PUT", `/api/agent/runs/${r.id}/plan`, { plan: r.plan });
        this.agentRun = await api("POST", `/api/agent/runs/${r.id}/start`, this.agentLimits);
        this.loadAgentRuns();
        this.pollAgent();
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },
    async openAgentRun(id, push = true) {
      clearTimeout(this.agentTimer);
      if (push) history.pushState({}, "", `/agent/runs/${id}`);
      try {
        this.agentRun = await api("GET", `/api/agent/runs/${id}`);
        if (this.agentActive()) this.pollAgent();
      } catch (e) { this.showError(e); }
    },
    async pollAgent() {
      clearTimeout(this.agentTimer);
      const r = this.agentRun;
      if (!r) return;
      try {
        const last = r.steps_list?.length ? r.steps_list[r.steps_list.length - 1].n : 0;
        const fresh = await api("GET", `/api/agent/runs/${r.id}?after=${last}`);
        if (this.agentRun?.id !== r.id) return;
        fresh.steps_list = [...(r.steps_list || []), ...fresh.steps_list];
        this.agentRun = fresh;
        const was = this.agentRuns.find(x => x.id === fresh.id);
        if (was && was.status !== fresh.status) this.loadAgentRuns();
      } catch { /* server busy; try again */ }
      if (this.agentActive()) this.agentTimer = setTimeout(() => this.pollAgent(), 2000);
      else { this.loadAgentRuns(); this.loadStatus(); }
    },
    async stopAgent() {
      try { this.agentRun = { ...this.agentRun, ...(await api("POST", `/api/agent/runs/${this.agentRun.id}/stop`)), steps_list: this.agentRun.steps_list }; }
      catch (e) { this.showError(e); }
    },
    async retryAgent() {
      const r = this.agentRun;
      this.agentGoal = r.goal; this.agentSite = r.start_url;
      this.agentRun = null;
      await this.planAgent();
    },
    // A human check stopped the agent: solve it yourself in a visible window (same flow as Guided mode);
    // the saved browser session is given to the agent's next run.
    async solveForAgent() {
      const r = this.agentRun;
      this.blockedPage = { domain: r.site, url: r.final_url || r.start_url };
      await this.startSiteSolve();
    },
    openAgentResults() { if (this.agentRun?.job_id) goTo(`/results/${this.agentRun.job_id}`); },
    async runAgentScrapeAgain() {
      const r = this.agentRun;
      if (r?.scrape_id) await this.runScrape({ id: r.scrape_id, name: r.plan?.summary || r.goal });
    },
  };
}
