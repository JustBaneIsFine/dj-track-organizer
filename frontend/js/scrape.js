// Import / update orchestration + live SSE progress handling.
window.DJ = window.DJ || {};
window.DJ.scrapeMixin = {
  // import dialog
  showImport: false,
  importMode: "interactive",
  importSourceUrl: "",
  importPasted: "",

  // live run state
  run: null, // { sid, active, total, index, currentName, processed, skipped, added, paused, captcha, whatsNew, errors }
  _es: null,
  _preRunTrackIds: [],
  importRescan: true,  // re-scan follow list for new artists (off = reuse last saved list)

  openImport() {
    this.importSourceUrl = this.settings.source_url || "";
    this.showImport = true;
  },

  async startImport() {
    const body = { mode: this.importMode, rescan: this.importRescan };
    const pasted = this.importPasted.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
    if (pasted.length) body.artist_urls = pasted;
    else if (this.importSourceUrl.trim()) {
      body.source_url = this.importSourceUrl.trim();
      api.patchSettings({ source_url: body.source_url }).catch(() => {});
    } else { this.toast("Paste a source URL or artist URLs", "warn"); return; }

    this.showImport = false;
    try {
      const r = await api.startImport(body);
      this._beginRun(r.session_id, this.importMode === "interactive");
    } catch (e) { this.toast(e.message, "err"); }
  },

  async runUpdate(mode, artistIds, notesQuery) {
    if (mode === "notes" && !(notesQuery || "").trim()) { this.toast("Type a word to match in notes", "warn"); return; }
    // Heads-up before the slow bulk modes (single/selected start immediately).
    if (["full", "priority", "notes"].includes(mode)) {
      let all = [];
      try { all = await api.listArtists({}); } catch (e) { all = this.artists; }
      const active = all.filter((a) => a.is_active && !a.is_deleted);
      const thr = parseInt(this.settings.priority_update_threshold || 2);
      // Word match (mirror backend notes_terms): every word must appear in the notes.
      const terms = (notesQuery || "").toLowerCase().split(/[,\s]+/).filter(Boolean);
      let n, label;
      if (mode === "full") { n = active.length; label = "all active artists"; }
      else if (mode === "priority") { n = active.filter((a) => a.priority >= thr).length; label = `priority ≥ ${thr}★ artists`; }
      else { n = active.filter((a) => { const notes = (a.notes || "").toLowerCase(); return terms.every((t) => notes.includes(t)); }).length; label = `artists with “${notesQuery.trim()}” in notes`; }
      if (!n) { this.toast("No matching artists to update", "warn"); return; }
      const mins = Math.max(1, Math.round(n * 9 / 60));
      const ok = await this.uiConfirm(`Update ${n} ${label}? Scraping runs slowly on purpose - a few seconds per artist to stay under SoundCloud's radar - so this can take a while (roughly ${mins} min). You can pause or stop it once it's running.`);
      if (!ok) return;
    }
    try {
      const r = await api.startUpdate({ mode, artist_ids: artistIds || null,
        threshold: mode === "priority" ? parseInt(this.settings.priority_update_threshold || 2) : null,
        notes_query: mode === "notes" ? notesQuery.trim() : null });
      this.showUpdateMenu = false;
      // All update runs go to the background strip so you can keep browsing while
      // scraping proceeds - never the blocking run overlay.
      this._beginRun(r.session_id, false, true);
      this.toast("Updating in the background - see the top strip");
    } catch (e) { this.toast(e.message, "err"); }
  },

  _beginRun(sid, interactive, background = false) {
    if (this._es) { this._es.close(); this._es = null; }  // drop any prior stream (e.g. on resume)
    this._preRunTrackIds = this.tracks.map((t) => t.id);
    this.run = { sid, active: true, total: 0, index: 0, currentName: "", processed: 0,
      skipped: 0, added: 0, paused: false, captcha: false, interactive, background, whatsNew: [], errors: [],
      curCount: 0, curPhase: "tracks", listCount: 0,
      // Interactive imports scrape in the background while you triage: this view
      // tracks the worker independently of the decision card.
      queued: 0, bgName: "", bgCount: 0, bgPhase: "tracks", bgActive: false };
    this.interactive = null;
    this._es = api.progress(sid, (ev) => this._onEvent(ev));
  },

  _onEvent(ev) {
    const r = this.run; if (!r) return;
    switch (ev.type) {
      case "list_start": r.currentName = "Reading follow list…"; r.listCount = 0; break;
      case "list_progress": r.listCount = ev.count; break;
      case "list_done": r.listCount = ev.count; this.toast(`Found ${ev.count} artists`); break;
      case "list_filtered":
        this.toast(`${ev.skipped_known} already in your library - skipped. ${ev.remaining} new to review.`);
        break;
      case "run_start": r.total = ev.total; break;
      case "artist_start":
        r.index = ev.index; r.currentName = ev.name; r.captcha = false; r.curCount = 0; r.curPhase = "tracks";
        // A scrape is starting (the background worker, in interactive mode).
        r.bgName = ev.name; r.bgCount = 0; r.bgPhase = "tracks"; r.bgActive = true;
        break;
      case "artist_progress":
        r.curCount = ev.count; r.curPhase = ev.phase;
        r.bgCount = ev.count; r.bgPhase = ev.phase;
        break;
      case "await_decision": this._presentCard(ev); break;
      case "artist_queued":
        // An "add & scrape" decision was handed to the background worker.
        if (ev.queued_remaining !== undefined) r.queued = ev.queued_remaining;
        break;
      case "artist_skipped": r.skipped++; break;
      case "artist_done":
        // NOTE: do NOT clear the decision card here - scraping is decoupled from
        // triage now, so a background artist finishing must not wipe the card the
        // user is currently filling in.
        r.processed++;
        if (ev.queued_remaining !== undefined) r.queued = ev.queued_remaining;
        if (!r.queued) { r.bgActive = false; r.bgName = ""; }
        if (ev.tracks_added > 0) {
          r.added += ev.tracks_added;
          r.whatsNew.unshift({ name: ev.name, n: ev.tracks_added, artist_id: ev.artist_id });
          if (r.whatsNew.length > 100) r.whatsNew.length = 100;  // keep it bounded (newest first)
        }
        if (ev.error) r.errors.push({ name: ev.name, message: ev.error });
        if (r.processed % 3 === 0) { this.loadArtists(); this.loadStats(); }
        break;
      case "rate_limited": this.toast(`SoundCloud slowed us down - waiting ${ev.cooldown_s}s`, "warn"); break;
      case "retry": this.toast(`Hiccup, retrying in ${ev.wait_s}s`, "warn"); break;
      case "captcha": r.captcha = true; this.toast(ev.message, "warn"); break;
      case "complete": this._finishRun(ev); break;
      case "error": this.toast("Run error: " + ev.message, "err"); r.active = false; break;
      case "stream_end": if (this._es) { this._es.close(); this._es = null; } break;
    }
  },

  async _finishRun(ev) {
    const r = this.run;
    r.active = false; r.status = ev.status;
    r.bgActive = false; r.bgName = ""; r.queued = 0;  // clear the background strip
    await this.loadArtists(); await this.loadStats(); await this.loadTracks();
    const pre = new Set(this._preRunTrackIds);
    this.newTrackIds = this.tracks.filter((t) => !pre.has(t.id)).map((t) => t.id);
    setTimeout(() => { this.newTrackIds = []; }, 2000);
    if (ev.status === "complete") {
      this.toast(`Update done: +${ev.tracks_added} new track(s)` + (ev.errors.length ? `, ${ev.errors.length} error(s)` : ""));
    }
  },

  // run controls
  async pauseRun() { if (!this.run) return; await api.pause(this.run.sid); this.run.paused = true; },
  async resumeRun() { if (!this.run) return; await api.resume(this.run.sid); this.run.paused = false; },
  async abandonRun() {
    if (!this.run) return;
    await api.abandon(this.run.sid); this.run.active = false; this.interactive = null;
    this.toast("Run discarded");
  },
  // Stop reviewing but keep resumable. For interactive imports the background
  // worker keeps draining what you already chose - keep the run + SSE alive so the
  // top strip shows that progress until it finishes; the un-reviewed rest resumes
  // later via "Continue last import".
  async stopSaveRun() {
    if (!this.run) return;
    await api.stopSave(this.run.sid);
    this.interactive = null;  // close the decision card
    this.resumableExists = true;
    if (this.run.interactive && (this.run.bgActive || this.run.queued)) {
      this.toast("Triage stopped - finishing the queued scrapes in the background");
    } else {
      this.run.active = false;
      this.toast("Saved - resume via Update ▾ → Continue last import");
    }
  },
  // Stop the background scrape worker too (from the top strip). Queued + un-reviewed
  // artists are saved for "Continue last import".
  async stopBgScrape() {
    if (!this.run) return;
    await api.stopBg(this.run.sid);
    this.resumableExists = true;
    this.toast("Stopping background scraping - remaining artists saved for later");
  },
  closeRunPanel() { if (this._es) { this._es.close(); this._es = null; } this.run = null; },

  jumpToNew(item) {
    // Peek at a finished artist's new tracks. During a LIVE run, never tear down
    // the panel/SSE - that would orphan the run (the next decision card never
    // arrives and the backend waits forever). Only close it once the run is done.
    this.selectedArtistIds = [item.artist_id];
    this.filters.status = "new";
    if (!(this.run && this.run.active)) this.closeRunPanel();
    this.loadTracks();
  },
};
