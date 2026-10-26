// Agent mode: describe the goal (the AI suggests a site if you name none), approve the plan, watch the
// agent think and act, open the results.
function agentPage() {
  let shown = { id: null, secs: 0 };   // last time shown, so the clock never steps back when a poll lands
  const offsets = new WeakMap();       // each run object from the server -> this page's clock minus the server's
  return {
    agentGoal: "",
    agentSite: "",
    showSiteBox: false,          // the optional site box in the composer
    agentCandidates: null,       // the "Which site?" answer: {goal, candidates, dropped, question}
    agentAnswer: "",             // your answer to the agent's question
    agentOtherSite: "",
    agentRun: null,              // the run on screen (with its steps)
    agentRuns: [],
    agentNewCol: "",
    agentLimits: { steps: 40, minutes: 10, ai_calls: 60 },
    agentTimer: null,
    agentNow: Date.now(),        // ticks 4 times a second while a run is on screen
    agentExamples: AGENT_EXAMPLES,
    historyOpen: false,

    async start() {
      if (this.status?.agent?.limits) this.agentLimits = { ...this.status.agent.limits };
      setInterval(() => { if (this.agentActive()) this.agentNow = Date.now(); }, 250);
      await this.loadAgentRuns();
      window.addEventListener("popstate", () => this.openFromAddress());
      await this.openFromAddress();
    },
    // /agent/runs/RUN-7K3QX9 opens that run; /agent shows a new one.
    async openFromAddress() {
      const m = location.pathname.match(/^\/agent\/runs\/([A-Za-z0-9-]+)/);
      if (m) await this.openAgentRun(m[1].toUpperCase(), false);
      else { clearTimeout(this.agentTimer); this.agentRun = null; }
    },

    agentProblem() {
      const a = this.status?.agent;
      if (!a) return "";
      if (!a.key_set) return `It needs an AI key for ${a.provider} in .env.`;
      if (a.engine === "react") return "";       // thinks in the app; the browser worker is only for sites that need clicking
      if (!a.installed) return "The agent's environment isn't installed. In the project folder run:  python -m venv .venv-agent  then  .venv-agent\\Scripts\\pip install -r agent_worker\\requirements.txt";
      if (!a.browser) return "It needs Microsoft Edge or Google Chrome installed.";
      return "";
    },
    agentBusyCount() { return this.agentRuns.filter(r => AGENT_ACTIVE.includes(r.status)).length; },
    runTitle(r) { return r?.title || r?.plan?.summary || r?.goal || ""; },
    async renameAgentRun(r) {
      const title = await this.promptDialog("Rename this run", "Name (leave empty to use the plan's summary)", this.runTitle(r), "Rename");
      if (title === null) return;
      try {
        const res = await api("PUT", `/api/agent/runs/${r.uid}/title`, { title });
        for (const x of [r, this.agentRun]) if (x && x.id === r.id) x.title = res.title;
      } catch (e) { this.showError(e); }
    },
    async deleteAgentRun(r) {
      if (AGENT_ACTIVE.includes(r.status)) {
        this.toast("Stop the run before deleting it.", "error"); return;
      }
      if (!await this.confirmDialog("Delete this agent run?",
        `“${this.runTitle(r)}” and its steps and screenshots are removed. Results it collected and saved scrapes stay.`)) return;
      try {
        await api("DELETE", `/api/agent/runs/${r.uid}`);
        if (this.agentRun?.id === r.id) this.newAgentRun();
        this.toast("Agent run deleted.");
        await this.loadAgentRuns();
      } catch (e) { this.showError(e); }
    },
    agentActive() { return AGENT_ACTIVE.includes(this.agentRun?.status); },
    // The plan's checklist (editable while it is a draft) or the live ticks of a run.
    checklist() { return (this.agentRun?.status === "draft" ? this.agentRun?.plan?.checklist : this.agentRun?.checklist) || []; },
    musts() { return this.checklist().filter(c => c.kind === "must"); },
    appItems() { return this.checklist().filter(c => c.kind === "app"); },
    addMust() {
      const list = this.agentRun.plan.checklist;
      list.splice(this.musts().length, 0, { id: `must_${this.musts().length + 1}`, kind: "must", text: "" });
    },
    removeMust(id) { this.agentRun.plan.checklist = this.agentRun.plan.checklist.filter(c => c.id !== id); },
    fixLabel(kind) {
      return { item_pages: "Item pages", more_pages: "More pages", pagination: "Next-page control",
               renavigate: "Found the way again" }[kind] || kind;
    },
    agentStatusLabel(s) {
      return { draft: "Plan ready", queued: "Starting", thinking: "Thinking", browsing: "Using the browser",
               question: "Waiting for your answer", navigating: "Finding the results", collecting: "Collecting",
               fixing: "Checking & fixing",
               done: "Finished", stopped: "Stopped", needs_you: "Needs you", limit: "Hit a limit", error: "Failed" }[s] || s;
    },
    agentChip(s) {
      return { done: "chip-green", navigating: "chip-blue", thinking: "chip-blue", browsing: "chip-blue", collecting: "chip-blue",
               fixing: "chip-blue", queued: "chip-amber", draft: "chip-gray", question: "chip-amber",
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
               thinking: "bg-primary animate-pulse", browsing: "bg-primary animate-pulse", question: "bg-warning",
               collecting: "bg-primary animate-pulse", fixing: "bg-primary animate-pulse", needs_you: "bg-warning",
               limit: "bg-warning", error: "bg-error", stopped: "bg-outline" }[s] || "bg-outline";
    },
    stepTone(kind) {
      return { action: "bg-primary-container text-on-primary-container", thought: "bg-surface-container-high text-on-surface-variant",
               observation: "bg-surface-container text-on-surface-variant",
               info: "bg-success-container text-on-success-container", warn: "bg-warning-container text-on-warning-container" }[kind]
             || "bg-surface-container-high text-on-surface-variant";
    },
    agentTiles() {
      const r = this.agentRun || {};
      const secs = this.agentSeconds();
      return [
        { label: "Agent steps", value: `${r.steps || 0} / ${r.limits?.steps || "—"}` },
        { label: "AI calls", value: `${r.ai_calls || 0} / ${r.limits?.ai_calls || "—"}`,
          note: r.tokens ? `${r.tokens >= 1000 ? (r.tokens / 1000).toFixed(1) + "k" : r.tokens} tokens` : "" },
        { label: "Time", value: secs >= 60 ? `${Math.floor(secs / 60)}m ${secs % 60}s` : `${secs}s` },
        { label: "Rows", value: this.fmt(r.job?.rows || 0) },
      ];
    },
    // The server sends the run every 2 s. In between, the page runs the clock itself from the run's start
    // time (corrected for any difference between the two clocks), so each second shows for a whole second.
    agentSeconds() {
      const r = this.agentRun || {};
      if (!this.agentActive() || !r.started_at || !r.server_now) return r.elapsed_s || 0;
      void this.agentNow;            // redraw on every tick
      const raw = Alpine.raw(r);
      if (!offsets.has(raw)) offsets.set(raw, Date.now() / 1000 - r.server_now);
      let secs = Math.max(0, Math.floor(Date.now() / 1000 - offsets.get(raw) - r.started_at));
      if (shown.id === r.id) secs = Math.max(secs, shown.secs);
      shown = { id: r.id, secs };
      return secs;
    },
    async loadAgentRuns() {
      if (!this.status?.agent?.enabled) return;
      try { this.agentRuns = await api("GET", "/api/agent/runs"); } catch { /* not fatal */ }
    },
    newAgentRun() {
      clearTimeout(this.agentTimer); this.agentRun = null; this.error = null;
      if (location.pathname !== "/agent") history.pushState({}, "", "/agent");
    },
    siteLooksOk() { return /^(https?:\/\/)?([a-z0-9-]+\.)+[a-z]{2,}(:\d+)?(\/\S*)?$/i.test(this.agentSite.trim()); },
    agentMissing() {
      if (!this.agentGoal.trim()) return "Describe what to collect.";
      if (this.agentSite.trim() && !this.siteLooksOk()) return "That doesn't look like a website address.";
      return "";
    },
    // Make the plan. Without a site (typed or named in the goal) the app answers with sites to choose from.
    async planAgent(site) {
      if (!this.agentGoal.trim()) { this.error = { title: "Say what to collect", hint: "Describe the data, or click an example." }; return; }
      this.error = null; this.busy = "agent-plan";
      try {
        const res = await api("POST", "/api/agent/plan", { goal: this.agentGoal.trim(), site: (site ?? this.agentSite).trim() });
        if (res.needs_site) { this.agentCandidates = res; this.agentOtherSite = ""; return; }
        this.agentCandidates = null;
        this.agentRun = res;
        history.pushState({}, "", `/agent/runs/${this.agentRun.uid}`);
        this.agentLimits = { ...(this.status?.agent?.limits || this.agentLimits) };
        this.loadAgentRuns();
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },
    useExample(ex) {
      this.agentGoal = ex.goal; this.agentSite = ex.site || ""; this.showSiteBox = !!ex.site;
      this.$nextTick(() => document.getElementById("agent-goal").focus());
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
        r.plan.checklist = (r.plan.checklist || []).filter(c => c.kind !== "must" || c.text.trim());
        await api("PUT", `/api/agent/runs/${r.id}/plan`, { plan: r.plan });
        this.agentRun = await api("POST", `/api/agent/runs/${r.id}/start`, this.agentLimits);
        this.loadAgentRuns();
        this.pollAgent();
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
    },
    async openAgentRun(uid, push = true) {
      clearTimeout(this.agentTimer);
      if (push) history.pushState({}, "", `/agent/runs/${uid}`);
      try {
        this.agentRun = await api("GET", `/api/agent/runs/${encodeURIComponent(uid)}`);
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
    async answerAgent() {
      const r = this.agentRun, answer = this.agentAnswer.trim();
      if (!r || !answer) return;
      this.busy = "agent-answer";
      try {
        this.agentRun = await api("POST", `/api/agent/runs/${r.uid}/answer`, { answer });
        this.agentAnswer = "";
        this.loadAgentRuns();
        this.pollAgent();
      } catch (e) { this.handleError(e); }
      finally { this.busy = ""; }
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
    openAgentResults() { if (this.agentRun?.job_id) goTo(`/results/${this.agentRun.job_uid || this.agentRun.job_id}`); },
    async runAgentScrapeAgain() {
      const r = this.agentRun;
      if (r?.scrape_id) await this.runScrape({ id: r.scrape_id, name: r.plan?.summary || r.goal });
    },
  };
}
