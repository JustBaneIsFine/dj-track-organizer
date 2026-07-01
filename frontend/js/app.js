// Root Alpine component. Composes the feature mixins and owns shared state:
// settings, stats, filters, sorting, density, keyboard, toasts, view routing.
window.DJ = window.DJ || {};

function djApp() {
  const base = {
    ready: false,
    view: "tracks", // 'tracks' | 'discover'
    settings: {},
    stats: {},          // global (drives the sidebar header)
    chipStats: {},      // filter-aware (drives the chip counts; = stats when no artist selected)
    sort: "priority_new_first",
    density: "comfortable",
    uiScale: 100,
    trackPageSize: 500,
    filters: { status: "all", stars: [], type: "all", showDeleted: false, showOwned: false, search: "" },

    // ui chrome
    showOnboarding: false,
    showSettings: false,
    showAbout: false,
    showDisclaimer: false,
    meta: {},
    updateAvailable: null,
    showUpdateMenu: false,
    notesUpdateQuery: "",
    showLog: false,
    confirmBox: null,   // { message, _resolve } - in-app confirm dialog
    logs: [],
    ctx: null,            // context menu { x, y, track }
    toasts: [],
    _toastId: 0,
    _searchTimer: null,
    _persistTimer: null,

    // init
    async init() {
      try {
        this.settings = await api.getSettings();
        this._applyUiFromSettings();
        try { this.meta = await api.meta(); } catch (e) {}
        await Promise.all([this.loadArtists(), this.loadTracks(), this.loadStats()]);
        await this.checkResumable();
        if (this.settings.accepted_disclaimer !== "true") this.showDisclaimer = true;
        else if (this.settings.onboarded !== "true" && !this.artists.length) this.showOnboarding = true;
        this._checkUpdate();
      } catch (e) { this.toast("Startup error: " + e.message, "err"); }
      this._installKeyboard();
      this._startHeartbeat();
      this.ready = true;
    },

    // Browser-mode only: keep the server alive while the tab is open; let it shut
    // down when the tab closes. The native window handles its own shutdown, so
    // skip there (the server's watchdog is only armed in browser mode anyway).
    _startHeartbeat() {
      if (window.pywebview) return;
      const ping = () => fetch("/api/heartbeat", { method: "POST" }).catch(() => {});
      ping();
      setInterval(ping, 10000);
      window.addEventListener("beforeunload", () => {
        try { navigator.sendBeacon("/api/heartbeat?bye=1"); } catch (e) {}
      });
    },

    async finishOnboarding(openImport) {
      try {
        await api.patchSettings({
          onboarded: "true",
          browser_profile_path: this.settings.browser_profile_path || "",
          source_url: this.settings.source_url || "",
        });
      } catch (e) {}
      this.showOnboarding = false;
      if (openImport) this.openImport();
    },

    theme: "dark",
    accent: "#00e0c8",
    accentListened: "",        // "" = follow accent
    accentRevisit: "#e0b84a",
    accentPresets: ["#00e0c8", "#7aa2f7", "#e0556a", "#e0b84a", "#9d7cd8", "#5fd35f"],

    _applyUiFromSettings() {
      this.sort = this.settings.default_sort || "priority_new_first";
      this.density = this.settings.ui_density || "comfortable";
      this.uiScale = parseInt(this.settings.ui_scale || "100") || 100;
      this._applyUiScale();
      this.trackPageSize = parseInt(this.settings.track_page_size || "500") || 500;
      this.theme = this.settings.theme || "dark";
      this.accent = this.settings.accent_color || "#00e0c8";
      this.accentListened = this.settings.accent_listened || "";
      this.accentRevisit = this.settings.accent_revisit || "#e0b84a";
      this._applyTheme();
      this.filters.showDeleted = this.settings.show_deleted === "true";
      if (this.settings.show_reposts === "false") this.filters.type = "originals";
      try {
        const saved = JSON.parse(this.settings.ui_state || "{}");
        if (saved.filters) Object.assign(this.filters, saved.filters);
        if (saved.sort) this.sort = saved.sort;
        if (saved.density) this.density = saved.density;
      } catch (e) {}
    },

    _persistUi() {
      clearTimeout(this._persistTimer);
      this._persistTimer = setTimeout(() => {
        api.patchSettings({
          ui_state: JSON.stringify({ filters: this.filters, sort: this.sort, density: this.density }),
          ui_density: this.density, default_sort: this.sort,
          show_deleted: String(this.filters.showDeleted),
        }).catch(() => {});
      }, 400);
    },

    // filters / sort / density
    reload() { this.loadTracks(); this._persistUi(); },
    setStatus(s) { this.filters.status = s; this.reload(); },
    setType(t) { this.filters.type = t; this.reload(); },
    // Combinable exact star levels (0 = no stars). Empty set = show all.
    toggleStar(n) {
      const i = this.filters.stars.indexOf(n);
      if (i >= 0) this.filters.stars.splice(i, 1); else this.filters.stars.push(n);
      this.reload();
    },
    starActive(n) { return this.filters.stars.includes(n); },
    toggleShowDeleted() { this.filters.showDeleted = !this.filters.showDeleted; this.loadArtists(); this.reload(); },
    setSort(s) { this.sort = s; this.reload(); },
    setDensity(d) { this.density = d; this._persistUi(); },
    _applyUiScale() { document.documentElement.style.zoom = (this.uiScale || 100) / 100; },
    setUiScale(v) {
      this.uiScale = parseInt(v) || 100;
      this.settings.ui_scale = String(this.uiScale);
      this._applyUiScale();
      api.patchSettings({ ui_scale: String(this.uiScale) }).catch(() => {});
    },
    // Max tracks fetched/rendered per view. Lower = smoother; you reach tracks by
    // filtering (artist/status), so the cap rarely bites. Hard-bounded 100-1000.
    setTrackPageSize(v) {
      let n = parseInt(v) || 500;
      n = Math.max(100, Math.min(1000, n));
      this.trackPageSize = n;
      this.settings.track_page_size = String(n);
      api.patchSettings({ track_page_size: String(n) }).catch(() => {});
      this.loadTracks();
    },

    // theming
    _darken(hex, f = 0.82) {
      const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
      if (!m) return hex;
      const n = parseInt(m[1], 16);
      const r = Math.round(((n >> 16) & 255) * f);
      const g = Math.round(((n >> 8) & 255) * f);
      const b = Math.round((n & 255) * f);
      return `#${((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1)}`;
    },
    _rgba(hex, a) {
      const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
      if (!m) return `rgba(0,224,200,${a})`;
      const n = parseInt(m[1], 16);
      return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
    },
    _applyTheme() {
      document.documentElement.dataset.theme = this.theme;
      const s = document.documentElement.style;
      s.setProperty("--accent", this.accent);
      s.setProperty("--accent-dim", this._darken(this.accent));
      s.setProperty("--accent-soft", this._rgba(this.accent, 0.14));
      s.setProperty("--star", this.accent);
      // Listened follows the accent unless the user picked a distinct colour.
      s.setProperty("--listened", this.accentListened || this.accent);
      s.setProperty("--revisit", this.accentRevisit || "#e0b84a");
    },
    toggleTheme() {
      this.theme = this.theme === "dark" ? "light" : "dark";
      this.settings.theme = this.theme;          // keep in sync so Save won't revert
      this._applyTheme();
      api.patchSettings({ theme: this.theme }).catch(() => {});
    },
    setAccent(hex) {
      this.accent = hex;
      this.settings.accent_color = hex;          // keep in sync so Save won't revert
      this._applyTheme();
      api.patchSettings({ accent_color: hex }).catch(() => {});
    },
    setAccentListened(hex) {
      this.accentListened = hex;
      this.settings.accent_listened = hex;
      this._applyTheme();
      api.patchSettings({ accent_listened: hex }).catch(() => {});
    },
    setAccentRevisit(hex) {
      this.accentRevisit = hex;
      this.settings.accent_revisit = hex;
      this._applyTheme();
      api.patchSettings({ accent_revisit: hex }).catch(() => {});
    },
    onSearchInput() {
      clearTimeout(this._searchTimer);
      this._searchTimer = setTimeout(() => this.reload(), 250);
    },
    toggleShowOwned() {
      this.filters.showOwned = !this.filters.showOwned;
      // Every owned track is listened (backend invariant), so showing owned under
      // New/All is misleading - jump the status filter to Listened when enabling.
      if (this.filters.showOwned && this.filters.status !== "listened") this.filters.status = "listened";
      this.reload();
    },
    clearFilters() {
      this.filters = { status: "all", stars: [], type: "all", showDeleted: false, showOwned: false, search: "" };
      this.selectedArtistIds = [];
      this.artistSearch = "";       // also reset the artist sidebar search…
      this.artistUnscraped = false; // …and the unscraped-only toggle
      this.loadArtists();           // refresh the sidebar to match the cleared search
      this.reload();
    },

    // stats
    async loadStats() {
      try { this.stats = await api.getStats(); } catch (e) {}
      await this._refreshChipStats();
    },
    // Chip counts reflect the selected artist(s); fall back to global otherwise.
    async _refreshChipStats() {
      if (this.selectedArtistIds && this.selectedArtistIds.length) {
        try { this.chipStats = await api.getStats(this.selectedArtistIds.join(",")); }
        catch (e) { this.chipStats = this.stats; }
      } else {
        this.chipStats = this.stats;
      }
    },

    // in-app confirm (replaces native confirm so no "127.0.0.1 says")
    uiConfirm(message) {
      return new Promise((resolve) => { this.confirmBox = { message, _resolve: resolve }; });
    },
    _confirmYes() { const b = this.confirmBox; this.confirmBox = null; b && b._resolve(true); },
    _confirmNo() { const b = this.confirmBox; this.confirmBox = null; b && b._resolve(false); },

    // context menu
    openCtx(e, track) { e.preventDefault(); this.ctx = { x: e.clientX, y: e.clientY, track }; },
    closeCtx() { this.ctx = null; },

    // toasts
    toast(msg, type = "ok") {
      const id = ++this._toastId;
      this.toasts.push({ id, msg, type });
      setTimeout(() => { this.toasts = this.toasts.filter((t) => t.id !== id); }, 4200);
    },

    // settings modal
    openSettings() { this.showSettings = true; },
    openAbout() { this.showAbout = true; },

    async acceptDisclaimer() {
      this.showDisclaimer = false;
      try { await api.patchSettings({ accepted_disclaimer: "true" }); } catch (e) {}
      this.settings.accepted_disclaimer = "true";
      if (this.settings.onboarded !== "true" && !this.artists.length) this.showOnboarding = true;
    },

    async _checkUpdate() {
      if (this.settings.update_check === "false" || !this.meta.repo) return;
      try {
        const res = await fetch(`https://api.github.com/repos/${this.meta.repo}/releases/latest`,
          { headers: { Accept: "application/vnd.github+json" } });
        if (!res.ok) return;
        const rel = await res.json();
        const latest = (rel.tag_name || "").replace(/^v/, "");
        if (latest && this._verGreater(latest, this.meta.version || "0.0.0"))
          this.updateAvailable = { version: latest, url: rel.html_url || this.meta.releases_url };
      } catch (e) {}
    },

    _verGreater(a, b) {
      const pa = String(a).split("."), pb = String(b).split(".");
      for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
        const x = parseInt(pa[i]) || 0, y = parseInt(pb[i]) || 0;
        if (x !== y) return x > y;
      }
      return false;
    },
    async saveSettings() {
      try {
        await api.patchSettings(this.settings);
        this.toast("Settings saved");
        this.showSettings = false;
        this._applyUiFromSettings();
        this.loadTracks();
      } catch (e) { this.toast(e.message, "err"); }
    },
    async doExport() {
      try {
        const data = await api.exportAll();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "dj-organizer-backup.json";
        a.click();
      } catch (e) { this.toast(e.message, "err"); }
    },
    importBackup(ev) {
      const file = ev.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = async () => {
        if (!confirm("Restore from this backup? It replaces all current data.")) return;
        try {
          const data = JSON.parse(reader.result);
          const r = await api.importAll(data);
          this.toast("Backup restored");
          this.settings = await api.getSettings();
          this._applyUiFromSettings();
          this.loadArtists(); this.loadTracks(); this.loadStats();
        } catch (e) { this.toast("Import failed: " + e.message, "err"); }
      };
      reader.readAsText(file);
      ev.target.value = "";
    },
    async resetAll() {
      if (!confirm("Reset EVERYTHING? All artists, tracks and history will be erased.")) return;
      if (!confirm("Are you absolutely sure? This cannot be undone.")) return;
      try {
        await api.resetAll();
        this.settings = await api.getSettings();
        this._applyUiFromSettings();
        this.selectedArtistIds = [];
        this.loadArtists(); this.loadTracks(); this.loadStats();
        this.toast("Everything reset");
      } catch (e) { this.toast(e.message, "err"); }
    },
    async purgeDeleted() {
      if (!confirm("Permanently delete all soft-deleted items? This cannot be undone.")) return;
      try { const r = await api.purgeDeleted(); this.toast(`Purged ${r.tracks_purged} tracks, ${r.artists_purged} artists`); this.loadTracks(); this.loadArtists(); }
      catch (e) { this.toast(e.message, "err"); }
    },
    async openLog() { this.showLog = true; try { this.logs = await api.log(); } catch (e) {} },

    // keyboard
    _installKeyboard() {
      window.addEventListener("keydown", (e) => {
        const tag = (e.target.tagName || "").toLowerCase();
        const typing = tag === "input" || tag === "textarea" || tag === "select";
        // In-app confirm dialog: Enter = Yes, Escape = No (intercept first, before
        // the global Escape/filter handling, regardless of where focus is).
        if (this.confirmBox) {
          if (e.key === "Enter") { e.preventDefault(); this._confirmYes(); return; }
          if (e.key === "Escape") { e.preventDefault(); this._confirmNo(); return; }
        }
        if (e.key === "/" && !typing) { e.preventDefault(); document.getElementById("search")?.focus(); return; }
        if (e.key === "Escape") { this.closeCtx(); if (this.showSettings || this.showLog) { this.showSettings = false; this.showLog = false; } else this.clearFilters(); return; }
        if (typing) return;
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a" && this.view === "tracks") {
          e.preventDefault(); this.selectAllVisible(); return;
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && this.view === "tracks") {
          e.preventDefault(); this.undo(); return;
        }
        if (this.interactive) { // interactive card shortcuts
          if (e.key >= "1" && e.key <= "4") { this.cardSetPriority(parseInt(e.key)); e.preventDefault(); }
          else if (e.key === "Enter") { this.decideAdd(); }
          else if (e.key.toLowerCase() === "s") { this.decideSkip(); }
          return;
        }
        if (e.key === "ArrowDown") { e.preventDefault(); this.focusedIndex = Math.min(this.focusedIndex + 1, this.tracks.length - 1); this._scrollToFocused(); }
        else if (e.key === "ArrowUp") { e.preventDefault(); this.focusedIndex = Math.max(this.focusedIndex - 1, 0); this._scrollToFocused(); }
        else if (e.key === " ") { const t = this.tracks[this.focusedIndex]; if (t) { e.preventDefault(); this.toggleCheck(t); } }
        else if (e.key.toLowerCase() === "n") { this.setStatus(this.filters.status === "new" ? "all" : "new"); }
        else if (e.key.toLowerCase() === "d") { const t = this.tracks[this.focusedIndex]; if (t) this.deleteTrack(t); }
        else if (e.key.toLowerCase() === "u") { this.showUpdateMenu = !this.showUpdateMenu; }
      });
    },
    _scrollToFocused() {
      this.$nextTick(() => document.querySelector(".track.focused")?.scrollIntoView({ block: "nearest" }));
    },

    // helpers for templates
    starsArr: [1, 2, 3, 4],
    // Cheap display-only stars (avoids a nested x-for per row - big render win).
    starStr(n) { return "★".repeat(Math.max(0, Math.min(4, n || 0))); },
    // Segment width (% of an artist's total tracks) for the unified progress bar.
    segPct(a, key) { const t = a.tracks_total || 0; return t ? Math.round((100 * (a[key] || 0)) / t) : 0; },
    isNew(t) { return this.newTrackIds.includes(t.id); },
    pctListened() { return this.stats.listened_pct || 0; },
    fmtDate(s) { if (!s) return "never"; const d = new Date(s); return d.toLocaleDateString(); },
  };

  return Object.assign(
    base,
    window.DJ.tracksMixin,
    window.DJ.artistsMixin,
    window.DJ.scrapeMixin,
    window.DJ.sessionMixin,
    window.DJ.ownedMixin
  );
}
window.djApp = djApp;
