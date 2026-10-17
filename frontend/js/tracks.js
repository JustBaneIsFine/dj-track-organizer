// Track-list behaviour: loading, filtering, per-row + bulk actions.
window.DJ = window.DJ || {};
window.DJ.tracksMixin = {
  tracks: [],
  tracksTotal: 0,
  tracksLoading: false,
  selectedTrackIds: [],
  focusedIndex: -1,
  newTrackIds: [],
  runSet: null,        // { id, label, count } - viewing what one saved run found new
  runResults: [],      // recent saved runs, for the Update menu
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
      if (this.runSet) await this._runSetRefresh(); else this.loadTracks();
      this.loadStats();
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
    if (f.link === "free" || f.link === "buy") q.link = f.link;
    if (f.stars && f.stars.length) q.priority_in = f.stars.join(",");
    if (f.showDeleted) q.is_deleted = 1;  // show ONLY deleted
    if (f.showOwned) q.is_owned = 1;      // show ONLY owned
    if (f.search.trim()) q.search = f.search.trim();
    if (this.runSet) q.run_id = this.runSet.id;  // only what that run found new
    return q;
  },

  // Show everything one run found new, across all its artists, so the "what's new"
  // list isn't lost the moment you open one artist.
  async showRunTracks(rs) {
    this.runSet = rs;
    this.selectedArtistIds = [];
    // Start on what's left to review: leftover filters (owned-only, stars, a search)
    // would silently hide most of it.
    this.filters.status = "new";
    this.filters.showOwned = false;
    this.filters.showDeleted = false;
    this.filters.type = "all";
    this.filters.link = "all";
    this.filters.stars = [];
    this.filters.search = "";
    this.view = "tracks";
    this.showUpdateMenu = false;
    if (this.run && !this.run.active) this.closeRunPanel();
    await this.loadTracks();
    // Retires a run with nothing left to review (all marked, deleted artist, etc.).
    if (!(await this._runSetPending())) {
      this._leaveRunSet("That run has nothing left to review");
      await this.loadTracks();
    }
    await this._refreshChipStats();
    await this.loadArtists();
  },
  clearRunSet() { this.runSet = null; this.loadTracks(); this._refreshChipStats(); },

  // Unreviewed tracks left in the open run, whatever filter is showing. Also keeps
  // the banner count current.
  async _runSetPending() {
    if (!this.runSet) return 0;
    try {
      const s = await api.getStats("", this.runSet.id);
      if (this.runSet) this.runSet.count = s.new || 0;
    } catch (e) { return this.runSet.count; }
    return this.runSet ? this.runSet.count : 0;
  },
  _leaveRunSet(msg) {
    this.runSet = null;
    this.toast(msg);
    this.loadRunResults();
    this._refreshChipStats();
  },

  // Any mark, here or elsewhere in the app, reloads the run. Once nothing in it is
  // left to review it closes on its own.
  async _runSetRefresh() {
    if (!this.runSet) return;
    await this.loadTracks();
    if (!(await this._runSetPending())) {
      this._leaveRunSet("That run is all caught up");
      await this.loadTracks();
    } else {
      await this._refreshChipStats();
    }
  },

  async loadRunResults() {
    try { this.runResults = await api.runResults(); } catch (e) { this.runResults = []; }
  },

  // Same query as last time = a refresh after a mark: leaving rows collapse, returning
  // rows drop in. A new query (filter, sort, artist) fades the first rows in instead.
  tracksLoaded: false,
  _collapseStagger: 0,
  async loadTracks() {
    const seq = (this._trkSeq = (this._trkSeq || 0) + 1);
    const A = DJ.anim;
    if (this._collapsing) { this._collapsing.forEach((a) => a.cancel()); this._collapsing = null; }
    this.tracksLoading = true;
    try {
      const q = this._trackQuery();
      const r = await api.listTracks(q);
      if (seq !== this._trkSeq) return;
      const key = JSON.stringify(q);
      const same = this.tracksLoaded && key === this._trkKey;
      this._trkKey = key;
      const list = document.querySelector(".track-list");
      const prevIds = new Set(this.tracks.map((t) => t.id));
      if (same) {
        const next = new Set(r.items.map((t) => t.id));
        const gone = A.visibleRows(list, ".track[data-k]").filter((el) => !next.has(+el.dataset.k));
        const step = gone.length > 1 ? Math.min(this._collapseStagger, 900 / gone.length) : 0;
        this._collapsing = A.collapse(gone, step);
        await A.settle(this._collapsing);
        if (seq !== this._trkSeq) return;
        this._collapsing = null;
      }
      this._collapseStagger = 0;
      this.tracks = r.items;
      this.tracksTotal = r.total;
      this.tracksLoaded = true;
      // Selection refers to the previous view; clear it on (re)load.
      this.selectedTrackIds = [];
      this._selectAnchor = null;
      if (this.focusedIndex >= this.tracks.length) this.focusedIndex = this.tracks.length - 1;
      this._refreshChipStats();  // keep chip counts in sync with the artist selection
      this.$nextTick(() => {
        if (same) {
          const added = r.items.filter((t) => !prevIds.has(t.id));
          if (added.length && added.length <= 10) {
            for (const t of added) A.expand(list.querySelector(`.track[data-k="${t.id}"]`), { drop: true });
          }
        } else {
          A.cascade(A.visibleRows(list, ".track[data-k]"));
        }
      });
    } catch (e) { this.toast(e.message, "err"); }
    finally { if (seq === this._trkSeq) this.tracksLoading = false; }
  },

  // Empty-state chips: one per active filter, each clears only itself.
  activeFilterChips() {
    const f = this.filters, out = [];
    const names = { new: "New", listened: "Listened", revisit: "Revisit", originals: "Originals",
      reposts: "Reposts", free: "Free", buy: "Buy" };
    if (this.runSet) out.push({ key: "run", label: this.runSet.label || "Update results" });
    if (f.status !== "all") out.push({ key: "status", label: names[f.status] || f.status });
    if (f.type !== "all") out.push({ key: "type", label: names[f.type] || f.type });
    if (f.link !== "all") out.push({ key: "link", label: names[f.link] || f.link });
    if (f.stars && f.stars.length) {
      const lv = [...f.stars].sort().map((n) => n === 0 ? "none" : n);
      out.push({ key: "stars", label: "Stars: " + lv.join(", ") });
    }
    for (const id of this.selectedArtistIds) {
      const a = this.artists.find((x) => x.id === id);
      out.push({ key: "artist", arg: id, label: a ? a.name : "Artist" });
    }
    if (f.search.trim()) out.push({ key: "search", label: "“" + f.search.trim() + "”" });
    if (f.showOwned) out.push({ key: "owned", label: "Owned only" });
    if (f.showDeleted) out.push({ key: "deleted", label: "Deleted only" });
    return out;
  },
  removeFilterChip(c) {
    const f = this.filters;
    switch (c.key) {
      case "run": return this.clearRunSet();
      case "status": f.status = "all"; break;
      case "type": f.type = "all"; break;
      case "link": f.link = "all"; break;
      case "stars": f.stars = []; break;
      case "artist": this.selectedArtistIds = this.selectedArtistIds.filter((x) => x !== c.arg); break;
      case "search": f.search = ""; break;
      case "owned": f.showOwned = false; break;
      case "deleted": f.showDeleted = false; this.loadArtists(); break;
    }
    this.reload();
    this._refreshChipStats();
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
    const finished = [];
    this.artists = this.artists.map((a) => {
      const n = byId.get(a.id);
      if (!n) return a;
      const was = a.tracks_total && (a.new_count || a.revisit_count);
      const now = n.tracks_total && !n.new_count && !n.revisit_count;
      if (was && now && DJ.anim.on(2)) { finished.push(n.id); return { ...n, _fin: true }; }
      return n;
    });
    // Just finished: the bar fills to 100%, then "all heard" pops in.
    if (finished.length) setTimeout(() => {
      for (const a of this.artists) if (finished.includes(a.id)) { a._fin = false; a._pop = true; }
    }, 550);
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
    if (this.runSet) await this._runSetRefresh();
    else if (nv && this.filters.status === "revisit") this.loadTracks();
  },

  // Mark/unmark "revisit later" - the one-click sibling of the listened check.
  async toggleRevisit(t) {
    this._pushUndo([t], this._merged(), t.is_revisit ? "unmark revisit" : "mark revisit");
    const nv = t.is_revisit ? 0 : 1;
    t.is_revisit = nv; // optimistic
    if (nv) t.is_checked = 0; // exclusivity (mirror backend)
    try { const r = await api.patchTrack(t.id, { is_revisit: nv, group: this._merged() }); this.loadStats(); this._applyAffectedArtists(r); }
    catch (e) { t.is_revisit = nv ? 0 : 1; this.toast(e.message, "err"); }
    if (this.runSet) await this._runSetRefresh();  // revisit counts as reviewed
    // A newly-revisited track leaves the New/Listened views.
    else if (nv && (this.filters.status === "new" || this.filters.status === "listened")) this.loadTracks();
  },

  async toggleOwned(t) {
    this._pushUndo([t], this._merged(), t.is_owned ? "unmark owned" : "mark owned");
    const nv = t.is_owned ? 0 : 1;
    t.is_owned = nv; // optimistic
    if (nv) t.is_checked = 1; // owned ⇒ listened (mirror backend)
    try { const r = await api.patchTrack(t.id, { is_owned: nv, group: this._merged() }); this.loadStats(); this._applyAffectedArtists(r); }
    catch (e) { t.is_owned = nv ? 0 : 1; this.toast(e.message, "err"); }
    if (this.runSet) await this._runSetRefresh();  // owned counts as reviewed
    // If we're not showing owned, a newly-owned track should drop out of view.
    else if (nv && !this.filters.showOwned) this.loadTracks();
  },

  async deleteTrack(t) {
    this._pushUndo([t], this._merged(), "delete track");
    try {
      const r = await api.patchTrack(t.id, { is_deleted: 1, group: this._merged() });
      this.toast("Track deleted (restorable)");
      this.loadStats(); this._applyAffectedArtists(r);
      if (this.runSet) await this._runSetRefresh(); else this.loadTracks();
    } catch (e) { this.toast(e.message, "err"); }
  },
  async restoreTrack(t) {
    this._pushUndo([t], this._merged(), "restore track");
    try {
      const r = await api.patchTrack(t.id, { is_deleted: 0, group: this._merged() });
      this._applyAffectedArtists(r);
      if (this.runSet) await this._runSetRefresh(); else this.loadTracks();
    } catch (e) { this.toast(e.message, "err"); }
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
  openTrack(t) {
    const u = this._openUrlFor(t);
    if (u) window.open(u, "_blank");
    if (this.tour && this.tour.active) this.tour.opened = true;
  },
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

  async bulk(fields, afterSnapshot) {
    if (!this.selectedTrackIds.length) return;
    const sel = new Set(this.selectedTrackIds);
    this._pushUndo(this.tracks.filter((t) => sel.has(t.id)), this._merged(), "bulk change");
    if (afterSnapshot) afterSnapshot(sel);
    const ids = [...this.selectedTrackIds];
    try {
      const r = await api.bulkTracks({ ids, group: this._merged(), ...fields });
      this.toast(`Updated ${r.updated} track(s)`);
      this.clearSelection();
      this.loadStats(); this._applyAffectedArtists(r);
      if (this.runSet) await this._runSetRefresh(); else this.loadTracks();
    } catch (e) { this.toast(e.message, "err"); }
  },
  // Checks tick, then the rows leave one by one.
  bulkListened() {
    if (!DJ.anim.on(2)) return this.bulk({ is_checked: 1 });
    this._collapseStagger = 90;
    this.bulk({ is_checked: 1 }, (sel) => {
      for (const id of sel) {
        DJ.anim.fx(document.querySelector(`.track[data-k="${id}"] .mark.heard:not(.on) .chk`), "fx-draw");
      }
      for (const t of this.tracks) if (sel.has(t.id)) { t.is_checked = 1; t.is_revisit = 0; }
    });
  },
  bulkRevisit() { this.bulk({ is_revisit: 1 }); },
  bulkUnlistened() { this.bulk({ is_checked: 0 }); },
  bulkDelete() { this.bulk({ is_deleted: 1 }); },
  bulkRestore() { this.bulk({ is_deleted: 0 }); },
  bulkOwned() { this.bulk({ is_owned: 1 }); },
  bulkUnowned() { this.bulk({ is_owned: 0 }); },

  // A track has a "usable download link" if it carries a buy/DL link that is NOT a
  // paid store (Bandcamp / Beatport) - i.e. Hypeddit / PumpYourSound / anything else
  // the user can grab by hand. Paid-store links are treated like "no free download".
  _hasUsableDl(t) { return this.linkKind(t) === "free"; },
  // "free" | "buy" | null. Shared by the row pill and the copy button; LINK_SQL in
  // db/queries.py applies the same rule for the Free / Buy chips.
  linkKind(t) {
    if (!t.purchase_url) return null;
    return /bandcamp\.com|beatport\.com/i.test(t.purchase_url) ? "buy" : "free";
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
    if (this.runSet) await this._runSetRefresh(); else await this.loadTracks();
    this.loadStats();
    // Re-select the left-behind (real download link) tracks so they stand out.
    const leaveIds = new Set(leaveSet.map((t) => t.id));
    this.selectedTrackIds = this.tracks.filter((t) => leaveIds.has(t.id)).map((t) => t.id);
    const left = leaveSet.length ? ` · ${leaveSet.length} left for you (download links)` : "";
    this.toast(`Copied ${urls.length} link(s) & marked listened${left}`);
  },

  trackStars(t) { return t.artist_priority || 0; },
};
