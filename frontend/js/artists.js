// Artist-sidebar behaviour: loading, priority, filtering, per-artist settings.
window.DJ = window.DJ || {};
window.DJ.artistsMixin = {
  artists: [],
  artistSearch: "",
  artistSort: "priority",   // priority | new | name | last_scraped
  artistUnscraped: false,   // show only artists never scraped (added, not yet scraped)
  selectedArtistIds: [],
  artistPanel: null, // artist object shown in the detail flyout

  // Typing a filter keeps every artist on screen: matches move to the top, the rest
  // stay below dimmed and inert. Rows glide to their new spots (FLIP).
  // opts.sort staggers the glide, opts.glow highlights one row after it lands.
  async loadArtists(opts = {}) {
    const seq = (this._artSeq = (this._artSeq || 0) + 1);
    const A = DJ.anim;
    try {
      const base = { include_deleted: this.filters.showDeleted, sort: this.artistSort,
        unscraped: this.artistUnscraped || undefined };
      const q = this.artistSearch.trim();
      let rows;
      if (q) {
        const [hits, all] = await Promise.all([api.listArtists({ ...base, search: q }), api.listArtists(base)]);
        const ids = new Set(hits.map((a) => a.id));
        rows = hits.concat(all.filter((a) => !ids.has(a.id)).map((a) => ({ ...a, _dim: true })));
      } else {
        rows = await api.listArtists(base);
      }
      if (seq !== this._artSeq) return;
      const list = document.querySelector(".artist-list");
      const next = new Set(rows.map((a) => a.id));
      const had = new Set(this.artists.map((a) => a.id));
      const added = rows.filter((a) => !had.has(a.id)).length;
      const gone = A.visibleRows(list, ".artist[data-k]").filter((el) => !next.has(+el.dataset.k));
      if (gone.length && gone.length <= 3) {
        const anims = A.collapse(gone);
        await A.settle(anims);
        if (seq !== this._artSeq) { anims.forEach((a) => a.cancel()); return; }
      }
      const fresh = had.size > 0 && added <= 3;
      await A.flip(list, ".artist[data-k]", () => { this.artists = rows; }, {
        stagger: opts.sort ? 60 : 0,
        onAdd: (el) => { if (fresh) A.expand(el, { flash: true }); },
      });
      if (opts.glow) A.glow(list && list.querySelector(`.artist[data-k="${opts.glow}"]`));
    } catch (e) { this.toast(e.message, "err"); }
  },
  artistMatchCount() { return this.artists.filter((a) => !a._dim).length; },
  toggleUnscraped() { this.artistUnscraped = !this.artistUnscraped; this.loadArtists(); },

  artistFilterChips() {
    const out = [];
    if (this.artistSearch.trim()) out.push({ key: "search", label: "“" + this.artistSearch.trim() + "”" });
    if (this.artistUnscraped) out.push({ key: "unscraped", label: "Never updated" });
    return out;
  },
  removeArtistChip(c) {
    if (c.key === "search") this.artistSearch = "";
    if (c.key === "unscraped") this.artistUnscraped = false;
    this.loadArtists();
  },

  artistFilterActive(id) { return this.selectedArtistIds.includes(id); },
  filterByArtist(a, additive) {
    if (additive) {
      const i = this.selectedArtistIds.indexOf(a.id);
      if (i >= 0) this.selectedArtistIds.splice(i, 1); else this.selectedArtistIds.push(a.id);
    } else {
      this.selectedArtistIds = this.artistFilterActive(a.id) && this.selectedArtistIds.length === 1 ? [] : [a.id];
    }
    this.loadTracks();
  },
  clearArtistFilter() {
    this.selectedArtistIds = [];
    this.artistSearch = "";   // also clear the "filter artists" text field
    this.loadArtists();       // reload the full sidebar (drops the search filtering)
    this.loadTracks();
  },

  async setPriority(a, n) {
    const nv = a.priority === n ? 0 : n; // click active star to clear
    a.priority = nv;
    try {
      await api.patchArtist(a.id, { priority: nv });
      this.loadStats();
      if (this.artistSort.startsWith("priority")) this.loadArtists({ glow: a.id });
    }
    catch (e) { this.toast(e.message, "err"); this.loadArtists(); }
  },

  async toggleActive(a) {
    const nv = a.is_active ? 0 : 1; a.is_active = nv;
    try { await api.patchArtist(a.id, { is_active: nv }); }
    catch (e) { this.toast(e.message, "err"); }
  },

  async deleteArtist(a) {
    if (!confirm(`Hide "${a.name}" and their tracks? (restorable via Show deleted)`)) return;
    try { await api.deleteArtist(a.id); this.closeCtx(); this.loadArtists(); this.loadTracks(); this.loadStats(); }
    catch (e) { this.toast(e.message, "err"); }
  },
  async restoreArtist(a) {
    try { await api.patchArtist(a.id, { is_deleted: 0 }); this.loadArtists(); this.loadTracks(); }
    catch (e) { this.toast(e.message, "err"); }
  },

  openArtistPanel(a) {
    // Edit aliases as a comma-separated string in a scratch field.
    this.artistPanel = { ...a, _aliasText: (a.aliases || []).join(", ") };
  },
  closeArtistPanel() { this.artistPanel = null; },
  async saveArtistPanel() {
    const a = this.artistPanel;
    const aliases = (a._aliasText || "").split(",").map((s) => s.trim()).filter(Boolean);
    try {
      await api.patchArtist(a.id, {
        include_reposts: parseInt(a.include_reposts),
        repost_limit: parseInt(a.repost_limit),
        notes: a.notes || "",
        aliases,
      });
      this.toast("Artist updated"); this.closeArtistPanel(); this.loadArtists();
    } catch (e) { this.toast(e.message, "err"); }
  },

  // "+ Add artist" - paste a single URL, optionally scrape now.
  addArtistUrl: "",
  showAddArtist: false,
  async submitAddArtist(scrapeNow) {
    const url = this.addArtistUrl.trim();
    if (!url) return;
    try {
      const a = await api.addArtist({ url });
      this.addArtistUrl = ""; this.showAddArtist = false;
      this.loadArtists(); this.loadStats();
      if (scrapeNow) this.runUpdate("single", [a.id]);
      else this.toast(`Added ${a.name}`);
    } catch (e) { this.toast(e.message.replace(/^\d+: /, ""), "err"); }
  },

  // [⟳] update a single artist. If a background update is already running, add this
  // artist to its queue instead of starting a competing run (which would cancel it).
  async updateSingleArtist(a) {
    // A run started a moment ago isn't live yet; wait for it so a quick second
    // click joins that queue instead of starting a run that replaces it.
    for (let i = 0; i < 100 && this._startingRun; i++) {
      await new Promise((res) => setTimeout(res, 50));
    }
    const r = this.run;
    if (r && r.active && r.background && !r.interactive) {
      try {
        const res = await api.enqueueUpdate(r.sid, [a.id]);
        if (res.enqueued) { this.toast(`Queued ${a.name} for update`); return; }
      } catch (e) { /* fall through to a fresh run */ }
    }
    this.runUpdate("single", [a.id]);
  },
};
