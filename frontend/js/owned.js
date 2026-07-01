// "Check folder for tracks I already own."
// The browser folder picker exposes only file NAMES - never contents, never write
// access - so the app cannot read, move, or modify any of the user's files.
window.DJ = window.DJ || {};
window.DJ.ownedMixin = {
  ownedReview: null,   // { scanned, matches:[], threshold }
  ownedBusy: false,
  ownedScan: null,     // { phase: 'reading'|'matching'|'native', count } - drives the progress modal

  // audio extensions we care about (names only)
  _audioExts: [".mp3", ".flac", ".wav", ".aif", ".aiff", ".m4a", ".aac", ".ogg", ".wma", ".alac"],

  // Pick a folder WITHOUT the browser's scary "upload" dialog.
  // Chromium: File System Access API (reads names only, sandboxed).
  // Other browsers: native OS folder dialog on the local app.
  // opts: { artistId, floor } - set for the per-artist "Check folder" (scoped pool,
  // more lenient floor); omitted for the whole-library scan.
  async pickFolder(opts) {
    opts = opts || {};
    if (window.showDirectoryPicker) {
      await this._pickViaFsApi(opts);
    } else {
      await this._pickViaNative(opts);
    }
  },

  // Per-artist Check folder: only this artist's tracks, lower floor.
  async checkArtistFolder(a) {
    if (!a) return;
    const floor = parseInt(this.settings.owned_match_floor_artist || 88);
    await this.pickFolder({ artistId: a.id, floor });
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

  async _pickViaNative(opts) {
    this.ownedBusy = true;
    this.ownedScan = { phase: "native", count: 0 };
    try {
      const floor = opts.floor != null ? opts.floor : null;
      const r = await api.pickFolderNative(opts.artistId, floor);
      if (r.cancelled) return;
      this._openReview(r, floor);
    } catch (err) { this.toast(err.message, "err"); }
    finally { this.ownedBusy = false; this.ownedScan = null; }
  },

  // For an artist-scoped scan the review slider starts at the (lower) scan floor so
  // all returned matches are visible; the library scan keeps the saved strictness.
  _openReview(r, floor) {
    const dflt = parseInt(this.settings.owned_match_threshold || 98);
    const scoped = floor != null && floor < parseInt(this.settings.owned_match_floor || 90);
    this.ownedReview = {
      scanned: r.scanned,
      matches: r.matches,
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

  async applyOwned(action) {
    const ids = this.ownedDisplayed().map((m) => m.track_id);
    if (!ids.length) { this.toast("No matches at this strictness", "warn"); return; }
    const fields = action === "delete" ? { is_deleted: 1 } : { is_owned: 1 };
    try {
      await api.bulkTracks({ ids, ...fields });
      // Remember the chosen strictness for next time - but only for the whole-library
      // scan; a lenient artist-scoped pass shouldn't lower the global default.
      if (!this.ownedReview.scoped) {
        api.patchSettings({ owned_match_threshold: String(this.ownedReview.threshold) }).catch(() => {});
        this.settings.owned_match_threshold = String(this.ownedReview.threshold);
      }
      this.toast(action === "delete"
        ? `Soft-deleted ${ids.length} track(s) you own`
        : `Tagged ${ids.length} track(s) as owned`);
      this.ownedReview = null;
      this.loadTracks(); this.loadArtists(); this.loadStats();
    } catch (err) { this.toast(err.message, "err"); }
  },

  closeOwnedReview() { this.ownedReview = null; },
};
