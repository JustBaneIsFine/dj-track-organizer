// "Check folder for tracks I already own."
// Only file NAMES are collected and matched; the app never modifies files. A match
// can be handed to the OS to play in the user's default player on request.
window.DJ = window.DJ || {};
window.DJ.ownedMixin = {
  ownedReview: null,   // { scanned, matches:[], threshold }
  ownedBusy: false,
  ownedScan: null,     // { phase: 'reading'|'matching'|'native', count } - drives the progress modal

  // audio extensions we care about (names only)
  _audioExts: [".mp3", ".flac", ".wav", ".aif", ".aiff", ".m4a", ".aac", ".ogg", ".wma", ".alac"],

  // Pick a folder WITHOUT the browser's scary "upload" dialog.
  // Native OS dialog first: the server walks names AND remembers paths, so a match
  // can be opened in the default player. The sandboxed File System Access picker
  // stays as a fallback (names only, no paths).
  // opts: { artistId, floor } - set for the per-artist "Check folder" (scoped pool,
  // more lenient floor); omitted for the whole-library scan.
  async pickFolder(opts) {
    opts = opts || {};
    try {
      await this._pickViaNative(opts);
    } catch (err) {
      if (window.showDirectoryPicker) await this._pickViaFsApi(opts);
      else this.toast(err.message, "err");
    }
  },

  // Per-artist Check folder: only this artist's tracks, lower floor.
  async checkArtistFolder(a) {
    if (!a) return;
    const floor = parseInt(this.settings.owned_match_floor_artist || 88);
    const artistId = a.id;
    this.closeArtistPanel();  // so the scan/review isn't hidden behind the panel
    await this.pickFolder({ artistId, floor });
  },

  // Poll matching progress (a determinate bar) while the scan runs off the event
  // loop on the server. Returns a stop() fn; safe to call even if the scan is quick.
  _startProgressPoll(token) {
    const id = setInterval(async () => {
      try {
        const r = await api.ownedProgress(token);
        if (this.ownedScan && this.ownedScan.phase === "matching") this.ownedScan.percent = r.percent;
      } catch (e) {}
    }, 300);
    return () => clearInterval(id);
  },
  _newScanToken() { return "scan-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8); },

  async _pickViaFsApi(opts) {
    let dir;
    try {
      dir = await window.showDirectoryPicker();
    } catch (err) {
      return; // user cancelled (AbortError) - do nothing
    }
    const names = [];
    const collect = async (handle) => {
      for await (const entry of handle.values()) {
        if (entry.kind === "file") {
          names.push(entry.name);                                   // name only
          if (names.length % 200 === 0) { this.ownedScan.count = names.length; await new Promise(r => setTimeout(r)); }
        } else if (entry.kind === "directory") await collect(entry); // never getFile()
      }
    };
    this.ownedBusy = true;
    this.ownedScan = { phase: "reading", count: 0 };
    try {
      await collect(dir);
      const audio = names.filter((n) => this._audioExts.some((x) => n.toLowerCase().endsWith(x)));
      if (!audio.length) { this.toast("No audio files found in that folder", "warn"); return; }
      const token = this._newScanToken();
      this.ownedScan = { phase: "matching", count: audio.length, percent: 0 };
      await new Promise(r => setTimeout(r));                         // let the modal paint before the blocking call
      const floor = opts.floor != null ? opts.floor : parseInt(this.settings.owned_match_floor || 90);
      const stopPoll = this._startProgressPoll(token);
      let r;
      try { r = await api.scanOwned(audio, floor, opts.artistId, token); }
      finally { stopPoll(); }
      this._openReview(r, floor);
    } catch (err) { this.toast(err.message, "err"); }
    finally { this.ownedBusy = false; this.ownedScan = null; }
  },

  // Two steps: open the folder dialog, then match. Splitting them lets the UI show
  // "choose a folder" and then the matching progress bar. A dialog-open failure
  // throws so pickFolder can fall back to the browser picker; a matching failure
  // just toasts (the folder was already chosen, no point re-picking).
  async _pickViaNative(opts) {
    this.ownedBusy = true;
    this.ownedScan = { phase: "native", count: 0 };
    let picked;
    try {
      picked = await api.pickFolderNative();
    } catch (err) {
      this.ownedBusy = false; this.ownedScan = null;
      throw err;  // let pickFolder fall back to the File System Access picker
    }
    if (picked.cancelled) { this.ownedBusy = false; this.ownedScan = null; return; }
    try {
      const floor = opts.floor != null ? opts.floor : null;
      const token = this._newScanToken();
      this.ownedScan = { phase: "matching", count: picked.count, percent: 0 };
      await new Promise(r => setTimeout(r));  // let the progress modal paint
      const stopPoll = this._startProgressPoll(token);
      let r;
      try { r = await api.scanPending(picked.pending, floor, opts.artistId, token); }
      finally { stopPoll(); }
      r.has_paths = picked.has_paths;
      this._openReview(r, floor);
    } catch (err) { this.toast(err.message, "err"); }
    finally { this.ownedBusy = false; this.ownedScan = null; }
  },

  // For an artist-scoped scan the review slider starts at the (lower) scan floor so
  // all returned matches are visible; the library scan keeps the saved strictness.
  _openReview(r, floor) {
    const dflt = parseInt(this.settings.owned_match_threshold || 98);
    const scoped = floor != null && floor < parseInt(this.settings.owned_match_floor || 90);
    (r.matches || []).forEach((m) => { m.sel = true; });
    this.ownedReview = {
      scanned: r.scanned,
      matches: r.matches,
      hasPaths: !!r.has_paths,               // native scan → matches can be played locally
      threshold: (floor != null && floor < dflt) ? floor : dflt,
      floor: floor != null ? floor : null,  // slider min for this scan
      scoped,                                // artist-scoped → don't persist global strictness
    };
  },

  ownedFloor() { return parseInt(this.settings.owned_match_floor || 90); },
  ownedDisplayed() {
    if (!this.ownedReview) return [];
    return this.ownedReview.matches.filter((m) => m.score >= this.ownedReview.threshold);
  },
  ownedSelected() { return this.ownedDisplayed().filter((m) => m.sel); },
  setAllMatches(v) { this.ownedDisplayed().forEach((m) => { m.sel = v; }); },

  async openLocalFile(m) {
    try { await api.openOwnedFile(m.filename); }
    catch (err) { this.toast("Could not open the file: " + err.message, "err"); }
  },

  async applyOwned(action) {
    const ids = this.ownedSelected().map((m) => m.track_id);
    // Unticked matches are "not the same song" - remember them so they never match again.
    const rejects = this.ownedDisplayed().filter((m) => !m.sel)
      .map((m) => ({ track_id: m.track_id, filename: m.filename }));
    if (!ids.length && !rejects.length) { this.toast("Nothing to apply", "warn"); return; }
    const fields = action === "delete" ? { is_deleted: 1 } : { is_owned: 1 };
    try {
      if (ids.length) await api.bulkTracks({ ids, ...fields });
      if (rejects.length) await api.rejectMatches(rejects);
      // Remember the chosen strictness for next time - but only for the whole-library
      // scan; a lenient artist-scoped pass shouldn't lower the global default.
      if (!this.ownedReview.scoped) {
        api.patchSettings({ owned_match_threshold: String(this.ownedReview.threshold) }).catch(() => {});
        this.settings.owned_match_threshold = String(this.ownedReview.threshold);
      }
      const parts = [];
      if (ids.length) parts.push(action === "delete" ? `soft-deleted ${ids.length}` : `tagged ${ids.length} as owned`);
      if (rejects.length) parts.push(`remembered ${rejects.length} as not a match`);
      this.toast(parts.join(" · ").replace(/^./, (c) => c.toUpperCase()));
      this.ownedReview = null;
      this.loadTracks(); this.loadArtists(); this.loadStats();
    } catch (err) { this.toast(err.message, "err"); }
  },

  closeOwnedReview() { this.ownedReview = null; },
};
