// Track-list behaviour: loading, filtering, per-row + bulk actions.
window.DJ = window.DJ || {};
window.DJ.tracksMixin = {
  tracks: [],
  tracksTotal: 0,
  tracksLoading: false,
  selectedTrackIds: [],
  focusedIndex: -1,
  newTrackIds: [],
  openDownloadLink: false,  // revisit view toggle: open the track's download link instead of its SC page
  _selectAnchor: null,
  undoStack: [],        // recent track mutations, for Ctrl+Z / Undo
  _UNDO_CAP: 25,

  // Snapshot the full status-field state of the affected rows BEFORE a mutation,
  // so undo can restore them verbatim. Storing all four fields sidesteps the
  // backend's cross-field rules (owned⇒listened, revisit/listened exclusivity),
  // which only auto-adjust a field when it isn't sent explicitly.
  _pushUndo(rows, group, label) {
    const snaps = rows.map((t) => ({
      id: t.id, is_checked: t.is_checked ? 1 : 0, is_revisit: t.is_revisit ? 1 : 0,
      is_owned: t.is_owned ? 1 : 0, is_deleted: t.is_deleted ? 1 : 0,
    }));
    if (!snaps.length) return;
    this.undoStack.push({ label, group: !!group, snaps });
    if (this.undoStack.length > this._UNDO_CAP) this.undoStack.shift();
  },

  async undo() {
    const entry = this.undoStack.pop();
    if (!entry) { this.toast("Nothing to undo", "warn"); return; }
    // Restore exact prior state: group ids by identical field-tuple, one bulk per group.
    const groups = new Map();
    for (const s of entry.snaps) {
      const key = `${s.is_checked}|${s.is_revisit}|${s.is_owned}|${s.is_deleted}`;
      (groups.get(key) || groups.set(key, []).get(key)).push(s);
    }
    try {
      for (const arr of groups.values()) {
        const f = arr[0];
        const r = await api.bulkTracks({
          ids: arr.map((s) => s.id), group: entry.group,
          is_checked: f.is_checked, is_revisit: f.is_revisit,
          is_owned: f.is_owned, is_deleted: f.is_deleted,
        });
        this._applyAffectedArtists(r);
      }
      this.loadTracks(); this.loadStats();
      this.toast(`Undone: ${entry.label}`);
    } catch (e) { this.toast(e.message, "err"); }
  },

  // Standard file-manager row selection: plain = single, ctrl/⌘ = toggle,
  // shift = contiguous range from the anchor.
  rowClick(t, i, e) {
    if (e.shiftKey && this._selectAnchor !== null) {
      const a = Math.min(this._selectAnchor, i);
      const b = Math.max(this._selectAnchor, i);
      this.selectedTrackIds = this.tracks.slice(a, b + 1).map((x) => x.id);
    } else if (e.ctrlKey || e.metaKey) {
      this.toggleSelect(t.id);
      this._selectAnchor = i;
    } else {
      this.selectedTrackIds = [t.id];
      this._selectAnchor = i;
    }
    this.focusedIndex = i;
  },

  _trackQuery() {
    const f = this.filters;
    const q = { sort: this.sort, limit: this.trackPageSize || 500 };
    if (this.selectedArtistIds.length) q.artist_id = this.selectedArtistIds.join(",");
    if (f.status === "new") { q.is_checked = 0; q.is_revisit = 0; }
    if (f.status === "listened") q.is_checked = 1;
    if (f.status === "revisit") q.is_revisit = 1;
    if (f.type === "originals") q.is_repost = 0;
    if (f.type === "reposts") q.is_repost = 1;
    if (f.stars && f.stars.length) q.priority_in = f.stars.join(",");
    if (f.showDeleted) q.is_deleted = 1;  // show ONLY deleted
    if (f.showOwned) q.is_owned = 1;      // show ONLY owned
    if (f.search.trim()) q.search = f.search.trim();
    return q;
  },

  async loadTracks() {
    this.tracksLoading = true;
    try {
      const r = await api.listTracks(this._trackQuery());
      this.tracks = r.items;
      this.tracksTotal = r.total;
      // Selection refers to the previous view; clear it on (re)load.
      this.selectedTrackIds = [];
      this._selectAnchor = null;
      if (this.focusedIndex >= this.tracks.length) this.focusedIndex = this.tracks.length - 1;
      this._refreshChipStats();  // keep chip counts in sync with the artist selection
    } catch (e) { this.toast(e.message, "err"); }
    this.tracksLoading = false;
  },

  isSelected(id) { return this.selectedTrackIds.includes(id); },
  toggleSelect(id) {
    const i = this.selectedTrackIds.indexOf(id);
    if (i >= 0) this.selectedTrackIds.splice(i, 1);
    else this.selectedTrackIds.push(id);
  },
  selectAllVisible() { this.selectedTrackIds = this.tracks.map((t) => t.id); },
  clearSelection() { this.selectedTrackIds = []; },
  allVisibleSelected() { return this.tracks.length > 0 && this.selectedTrackIds.length >= this.tracks.length; },
  toggleSelectAll() { this.allVisibleSelected() ? this.clearSelection() : this.selectAllVisible(); },
  invertSelection() {
    const cur = new Set(this.selectedTrackIds);
    this.selectedTrackIds = this.tracks.filter((t) => !cur.has(t.id)).map((t) => t.id);
  },

  // Merged view is active when no specific artist is selected; actions on a
  // merged row apply to every copy of the song (group-wide).
  _merged() { return this.selectedArtistIds.length === 0; },

  // A mark only changes the touched artists' counts, so the patch/bulk responses
  // carry just those refreshed sidebar rows - splice them in instead of
  // rescanning every artist (keeps clicks instant as the artist count grows).
  _applyAffectedArtists(resp) {
    const list = resp && resp.affected_artists;
    if (!list || !list.length) return;
    const byId = new Map(list.map((a) => [a.id, a]));
    this.artists = this.artists.map((a) => byId.get(a.id) || a);
    if (this.artistPanel && byId.has(this.artistPanel.id)) {
      this.artistPanel = { ...this.artistPanel, ...byId.get(this.artistPanel.id) };
    }
  },

  async toggleCheck(t) {
    this._pushUndo([t], this._merged(), t.is_checked ? "mark new" : "mark listened");
    const nv = t.is_checked ? 0 : 1;
    t.is_checked = nv; // optimistic
    if (nv) t.is_revisit = 0; // listened & revisit are mutually exclusive (mirror backend)
    try { const r = await api.patchTrack(t.id, { is_checked: nv, group: this._merged() }); this.loadStats(); this._applyAffectedArtists(r); }
    catch (e) { t.is_checked = nv ? 0 : 1; this.toast(e.message, "err"); }
    if (nv && this.filters.status === "revisit") this.loadTracks();
  },

  // Mark/unmark "revisit later" - the one-click sibling of the listened check.
  async toggleRevisit(t) {
    this._pushUndo([t], this._merged(), t.is_revisit ? "unmark revisit" : "mark revisit");
    const nv = t.is_revisit ? 0 : 1;
    t.is_revisit = nv; // optimistic
    if (nv) t.is_checked = 0; // exclusivity (mirror backend)
    try { const r = await api.patchTrack(t.id, { is_revisit: nv, group: this._merged() }); this.loadStats(); this._applyAffectedArtists(r); }
    catch (e) { t.is_revisit = nv ? 0 : 1; this.toast(e.message, "err"); }
    // A newly-revisited track leaves the New/Listened views.
    if (nv && (this.filters.status === "new" || this.filters.status === "listened")) this.loadTracks();
  },

  async toggleOwned(t) {
    this._pushUndo([t], this._merged(), t.is_owned ? "unmark owned" : "mark owned");
    const nv = t.is_owned ? 0 : 1;
    t.is_owned = nv; // optimistic
    if (nv) t.is_checked = 1; // owned ⇒ listened (mirror backend)
    try { const r = await api.patchTrack(t.id, { is_owned: nv, group: this._merged() }); this.loadStats(); this._applyAffectedArtists(r); }
    catch (e) { t.is_owned = nv ? 0 : 1; this.toast(e.message, "err"); }
    // If we're not showing owned, a newly-owned track should drop out of view.
    if (nv && !this.filters.showOwned) this.loadTracks();
  },

  async deleteTrack(t) {
    this._pushUndo([t], this._merged(), "delete track");
    try { const r = await api.patchTrack(t.id, { is_deleted: 1, group: this._merged() }); this.toast("Track deleted (restorable)"); this.loadTracks(); this.loadStats(); this._applyAffectedArtists(r); }
    catch (e) { this.toast(e.message, "err"); }
  },
  async restoreTrack(t) {
    this._pushUndo([t], this._merged(), "restore track");
    try { const r = await api.patchTrack(t.id, { is_deleted: 0, group: this._merged() }); this.loadTracks(); this._applyAffectedArtists(r); }
    catch (e) { this.toast(e.message, "err"); }
  },
  // SoundCloud wraps external buy/download links in a "gate.sc/?url=..." redirect
  // (its "leaving SoundCloud" page). Unwrap it so we open the real destination
  // (hypeddit / gaterush / bandcamp / …) instead of a SoundCloud-branded page.
  _unwrapDl(url) {
    try {
      const u = new URL(url);
      if (u.hostname === "gate.sc" || u.hostname.endsWith(".gate.sc")) {
        const inner = u.searchParams.get("url");
        if (inner) return inner;
      }
    } catch (e) {}
    return url;
  },
  // Which URL a track opens: in the revisit view with the toggle on, prefer the
  // download/buy link when the track has one; otherwise the SoundCloud page.
  _openUrlFor(t) {
    const useDl = this.openDownloadLink && this.filters.status === "revisit" && t.purchase_url;
    return useDl ? this._unwrapDl(t.purchase_url) : t.url;
  },
  openTrack(t) { const u = this._openUrlFor(t); if (u) window.open(u, "_blank"); },
  // "Go to artist": filter the sidebar to just this artist (by name) so they're easy
  // to find, and scope the track list to them. (Merged rows have no single artist.)
  goToArtist(t) {
    this.selectedArtistIds = [t.artist_id];
    this.artistSearch = (t.group_size > 1) ? "" : (t.artist_name || "");
    this.artistUnscraped = false;
    this.loadArtists();
    this.loadTracks();
    this.closeCtx();
  },
  // Open every selected track in visible (top-to-bottom) order, staggered so the
  // browser keeps the tabs in order rather than racing them open.
  async openSelectedLinks() {
    const sel = new Set(this.selectedTrackIds);
    const urls = this.tracks.filter((t) => sel.has(t.id)).map((t) => this._openUrlFor(t)).filter(Boolean);
    this.closeCtx();
    if (!urls.length) { this.toast("No links in selection", "warn"); return; }
    if (urls.length > 15 && !(await this.uiConfirm(`Open ${urls.length} links in your browser?`))) return;
    urls.forEach((u, i) => setTimeout(() => window.open(u, "_blank"), i * 300));
    this.toast(`Opening ${urls.length} link(s)…`);
  },
  copyLink(t) { if (t.url) navigator.clipboard?.writeText(t.url); this.toast("Link copied"); this.closeCtx(); },

  async bulk(fields) {
    if (!this.selectedTrackIds.length) return;
    const sel = new Set(this.selectedTrackIds);
    this._pushUndo(this.tracks.filter((t) => sel.has(t.id)), this._merged(), "bulk change");
    try {
      const r = await api.bulkTracks({ ids: this.selectedTrackIds, group: this._merged(), ...fields });
      this.toast(`Updated ${r.updated} track(s)`);
      this.clearSelection();
      this.loadTracks(); this.loadStats(); this._applyAffectedArtists(r);
    } catch (e) { this.toast(e.message, "err"); }
  },
  bulkListened() { this.bulk({ is_checked: 1 }); },
  bulkRevisit() { this.bulk({ is_revisit: 1 }); },
  bulkUnlistened() { this.bulk({ is_checked: 0 }); },
  bulkDelete() { this.bulk({ is_deleted: 1 }); },
  bulkRestore() { this.bulk({ is_deleted: 0 }); },
  bulkOwned() { this.bulk({ is_owned: 1 }); },
  bulkUnowned() { this.bulk({ is_owned: 0 }); },

  // A track has a "usable download link" if it carries a buy/DL link that is NOT a
  // paid store (Bandcamp / Beatport) - i.e. Hypeddit / PumpYourSound / anything else
  // the user can grab by hand. Paid-store links are treated like "no free download".
  _hasUsableDl(t) {
    const u = t.purchase_url || "";
    return !!u && !/bandcamp\.com|beatport\.com/i.test(u);
  },
  // Selected revisit workflow: tracks with no buy link OR a Bandcamp buy link can't
  // be free-downloaded, so copy their SoundCloud URLs to the clipboard (one per line,
  // to paste into a downloader) and mark them listened. Tracks with a usable DL link
  // (Hypeddit / PumpYourSound / other) are left for the user - and re-selected after,
  // so they're highlighted and ready to deal with.
  async copyNoDownloadSelected() {
    if (!this.selectedTrackIds.length) return;
    const sel = new Set(this.selectedTrackIds);
    const chosen = this.tracks.filter((t) => sel.has(t.id));
    const copySet = chosen.filter((t) => !this._hasUsableDl(t));
    const leaveSet = chosen.filter((t) => this._hasUsableDl(t));
    const urls = copySet.map((t) => t.url).filter(Boolean);
    if (!copySet.length) {
      this.toast("All selected tracks have a download link - nothing to copy", "warn");
      return;
    }
    try { await navigator.clipboard.writeText(urls.join("\n")); }
    catch (e) { this.toast("Clipboard unavailable in this browser", "err"); return; }
    // Mark the copied set listened (clears revisit). Snapshot first for Ctrl+Z.
    this._pushUndo(copySet, this._merged(), "copy links + mark listened");
    try {
      const r = await api.bulkTracks({ ids: copySet.map((t) => t.id), group: this._merged(), is_checked: 1 });
      this._applyAffectedArtists(r);
    } catch (e) { this.toast(e.message, "err"); return; }
    this.clearSelection();
    await this.loadTracks(); this.loadStats();
    // Re-select the left-behind (real download link) tracks so they stand out.
    const leaveIds = new Set(leaveSet.map((t) => t.id));
    this.selectedTrackIds = this.tracks.filter((t) => leaveIds.has(t.id)).map((t) => t.id);
    const left = leaveSet.length ? ` · ${leaveSet.length} left for you (download links)` : "";
    this.toast(`Copied ${urls.length} link(s) & marked listened${left}`);
  },

  trackStars(t) { return t.artist_priority || 0; },
};
