// Interactive per-artist decision card + resume-on-launch banner.
window.DJ = window.DJ || {};
window.DJ.sessionMixin = {
  interactive: null,        // { index, name, priority }
  resumable: null,          // a paused session shown in the launch banner
  resumableExists: false,   // a paused session exists (drives Update ▾ entry; survives Dismiss)

  _presentCard(ev) {
    // Decision happens before scraping (anti-bot friendly: skipped artists are
    // never fetched). The user sets priority, optional aliases, and add/skip.
    this.interactive = { index: ev.index, name: ev.name, url: ev.url || "", priority: 0, aliasText: "", notes: "" };
  },

  cardSetPriority(n) {
    if (!this.interactive) return;
    this.interactive.priority = this.interactive.priority === n ? 0 : n;
  },

  async decideAdd(scrape = true) {
    if (!this.interactive) return;
    const p = this.interactive.priority;
    const aliases = (this.interactive.aliasText || "").split(",").map((s) => s.trim()).filter(Boolean);
    const notes = (this.interactive.notes || "").trim();
    this.interactive = null;
    await api.decide(this.run.sid, { action: "add", priority: p, aliases, notes, scrape });
  },
  // Add to the library now, scrape later (fast pass through a follow list).
  decideAddOnly() { return this.decideAdd(false); },
  async decideSkip() {
    if (!this.interactive) return;
    this.interactive = null;
    await api.decide(this.run.sid, { action: "skip" });
  },
  async decideSkipAllUnstarred() {
    if (!this.interactive) return;
    this.interactive = null;
    await api.decide(this.run.sid, { action: "skip", skip_all_unstarred: true });
    this.toast("Skipping all remaining unstarred artists");
  },

  // Resume banner
  async checkResumable() {
    try { this.resumable = await api.resumable(); this.resumableExists = !!this.resumable; } catch (e) {}
  },
  async resumeSession() {
    if (!this.resumable || this._resuming) return;
    this._resuming = true;
    const sid = this.resumable.id;
    const interactive = this.resumable.mode === "interactive";
    const processed = this.resumable.processed_count || 0;
    const total = this.resumable.total_artists || 0;
    this.resumable = null;
    this.resumableExists = false;  // now running again, not a pending paused session
    // Instant feedback: the backend resume call (re-opening the engine + re-queuing)
    // takes a couple seconds, so show the run panel in a "Resuming…" state right away
    // instead of leaving the click looking dead. _beginRun replaces this placeholder.
    this.run = { sid, active: true, interactive: false, background: false, total, processed,
      index: processed, currentName: "Resuming…", added: 0, skipped: 0, paused: false,
      captcha: false, whatsNew: [], errors: [], listCount: 0, curCount: 0, curPhase: "tracks",
      queued: 0, bgName: "", bgCount: 0, bgPhase: "tracks", bgActive: false };
    try {
      await api.resumeSession(sid);
      this._beginRun(sid, interactive);
    } catch (e) { this.toast(e.message, "err"); this.run = null; }
    finally { this._resuming = false; }
  },
  // Update ▾ → "Continue last import": resume the newest paused session.
  async continueLastImport() {
    if (!this.resumable) await this.checkResumable();
    if (!this.resumable) { this.toast("No paused import to continue", "warn"); return; }
    return this.resumeSession();
  },
  async abandonResumable() {
    if (!this.resumable) return;
    await api.abandon(this.resumable.id);
    this.resumable = null;
    this.resumableExists = false;  // discarded - drop the Update ▾ entry too
  },
  // Dismiss only hides the banner; the paused session stays resumable via Update ▾.
  dismissResumable() { this.resumable = null; },
};
