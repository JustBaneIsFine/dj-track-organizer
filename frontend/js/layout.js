// Compact-layout customisation. "Comfortable" always shows everything; "Compact"
// shows a minimal set the user can edit in the layout panel. Row heights and
// text sizes are the same in both - compact only removes chrome.
window.DJ = window.DJ || {};

// Every toggleable piece, grouped the way the editor shows them. `on` is the
// compact default. Anything not listed here is always visible.
window.DJ.LAYOUT_SECTIONS = [
  {
    key: "filter", title: "Filter bar",
    note: "Sort and layout dropdowns always stay, so you can get back out of compact.",
    groups: [
      { label: "Status", items: [
        { key: "filter.all", label: "All", on: true },
        { key: "filter.new", label: "New", on: true },
        { key: "filter.listened", label: "Listened", on: false },
        { key: "filter.revisit", label: "⟳ Revisit", on: true },
        { key: "filter.openmode", label: "⬇ Opens: link (revisit only)", on: true },
      ] },
      { label: "Type", items: [
        { key: "filter.type", label: "All types / Originals / ↻ Reposts", on: false },
      ] },
      { label: "Stars", items: [
        { key: "filter.stars", label: "☆ 1★ 2★ 3★ 4★", on: false },
      ] },
      { label: "Extras", items: [
        { key: "filter.deleted", label: "Show deleted", on: false },
        { key: "filter.owned", label: "Show owned", on: false },
      ] },
    ],
  },
  {
    key: "overview", title: "Overview",
    note: "The block above the artist list.",
    groups: [
      { label: "", items: [
        { key: "ov.totals", label: "N artists · N tracks", on: false },
        { key: "ov.today", label: "🎧 N today", on: true },
        { key: "ov.bar", label: "Progress bar", on: true },
        { key: "ov.legend", label: "N heard · N new · N revisit", on: true },
        { key: "ov.origreposts", label: "orig 12/40 · ↻ 3/9", on: false },
        { key: "ov.meta", label: "N priority · N owned · last update", on: false },
      ] },
    ],
  },
  {
    key: "artist", title: "Artist rows",
    note: "The artist name is always shown.",
    groups: [
      { label: "", items: [
        { key: "ar.stars", label: "★★★★ priority", on: true },
        { key: "ar.update", label: "⟳ update button", on: true },
        { key: "ar.orig", label: "N orig", on: false },
        { key: "ar.reposts", label: "N ↻", on: false },
        { key: "ar.new", label: "N new", on: true },
        { key: "ar.revisit", label: "N ⟳", on: true },
        { key: "ar.bar", label: "Progress bar", on: true },
        { key: "ar.done", label: "✓ all heard", on: true },
      ] },
    ],
  },
];

window.DJ.layoutMixin = {
  showLayoutPanel: false,
  compactLayout: {},          // key -> bool, overrides the defaults above

  _layoutDefaults() {
    const d = {};
    for (const s of window.DJ.LAYOUT_SECTIONS)
      for (const g of s.groups) for (const it of g.items) d[it.key] = it.on;
    return d;
  },
  _loadLayout() {
    let saved = {};
    try { saved = JSON.parse(this.settings.compact_layout || "{}") || {}; } catch (e) {}
    this.compactLayout = Object.assign(this._layoutDefaults(), saved);
  },
  // Is this piece visible right now? Comfortable shows everything.
  lay(key) {
    if (this.density !== "compact") return true;
    const v = this.compactLayout[key];
    return v === undefined ? true : v;
  },
  toggleLay(key) {
    this.compactLayout[key] = !this.lay2(key);
    this._saveLayout();
    this._dropHiddenFilters();
  },

  // A filter you can't see is a filter you can't clear, so switching a control
  // off (or switching into compact) resets whatever it was holding.
  _dropHiddenFilters() {
    if (this.density !== "compact") return;
    const f = this.filters;
    let changed = false;
    const statusKey = { all: "filter.all", new: "filter.new", listened: "filter.listened", revisit: "filter.revisit" };
    if (!this.lay(statusKey[f.status] || "filter.all")) {
      const fallback = ["all", "new", "listened", "revisit"].find((s) => this.lay(statusKey[s]));
      f.status = fallback || "all";
      changed = true;
    }
    if (!this.lay("filter.type") && f.type !== "all") { f.type = "all"; changed = true; }
    if (!this.lay("filter.stars") && f.stars.length) { f.stars = []; changed = true; }
    if (!this.lay("filter.deleted") && f.showDeleted) { f.showDeleted = false; changed = true; }
    if (!this.lay("filter.owned") && f.showOwned) { f.showOwned = false; changed = true; }
    if (!this.lay("filter.openmode") && this.openDownloadLink) this.openDownloadLink = false;
    if (changed) { this.loadArtists(); this.reload(); }
  },
  // Editor state, independent of which density is active.
  lay2(key) {
    const v = this.compactLayout[key];
    return v === undefined ? true : v;
  },
  _saveLayout() {
    api.patchSettings({ compact_layout: JSON.stringify(this.compactLayout) }).catch(() => {});
  },
  resetLayout() {
    this.compactLayout = this._layoutDefaults();
    this._saveLayout();
    this._dropHiddenFilters();
  },
  openLayoutPanel() { this.showLayoutPanel = true; },
  layoutSections() { return window.DJ.LAYOUT_SECTIONS; },
};
