// Artist-sidebar behaviour: loading, priority, filtering, per-artist settings.
window.DJ = window.DJ || {};
window.DJ.artistsMixin = {
  artists: [],
  artistSearch: "",
  artistSort: "priority",   // priority | new | name | last_scraped
  artistUnscraped: false,   // show only artists never scraped (added, not yet scraped)
  selectedArtistIds: [],
  artistPanel: null, // artist object shown in the detail flyout

  async loadArtists() {
    try {
      this.artists = await api.listArtists({
        search: this.artistSearch || undefined,
        include_deleted: this.filters.showDeleted,
        sort: this.artistSort,
        unscraped: this.artistUnscraped || undefined,
      });
    } catch (e) { this.toast(e.message, "err"); }
  },
  toggleUnscraped() { this.artistUnscraped = !this.artistUnscraped; this.loadArtists(); },

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
    try { await api.patchArtist(a.id, { priority: nv }); this.loadStats(); }
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
      this.loadArtists();
      if (scrapeNow) this.runUpdate("single", [a.id]);
      else this.toast(`Added ${a.name}`);
    } catch (e) { this.toast(e.message, "err"); }
  },

  // [⟳] update a single artist
  updateSingleArtist(a) { this.runUpdate("single", [a.id]); },
};
