// First-run welcome, the sidebar checklist and the guided tour.
// The tour spotlights real controls and moves on when the app state says a step is done,
// so shortcuts (Add & update, a note typed early) skip ahead on their own.
window.DJ = window.DJ || {};

window.DJ.tourMixin = {
  welcomeStep: 0,   // 0 closed, 1 disclaimer, 2 how to start
  tour: { active: false, i: 0, artistId: null, sawRun: false, error: "", rect: null, pop: null, base: 0 },
  _tourTimer: null,
  _tourStartIds: [],

  _openWelcome() {
    const needsOnboarding = this.settings.onboarded !== "true" && !this.artists.length;
    if (this.settings.accepted_disclaimer !== "true") this.welcomeStep = 1;
    else if (needsOnboarding) this.welcomeStep = 2;
  },
  async acceptDisclaimer() {
    this.settings.accepted_disclaimer = "true";
    try { await api.patchSettings({ accepted_disclaimer: "true" }); } catch (e) {}
    this.welcomeStep = this.settings.onboarded !== "true" && !this.artists.length ? 2 : 0;
  },
  // mode: "tour" | "video" | "explore"
  finishOnboarding(mode) {
    this.settings.onboarded = "true";
    this.settings.checklist = "active";
    this.settings.video_nudge = "done";  // onboarding already offers the video
    api.patchSettings({ onboarded: "true", checklist: "active", video_nudge: "done" }).catch(() => {});
    this.welcomeStep = 0;
    if (mode === "video" && this.meta.tutorial_url) window.open(this.meta.tutorial_url, "_blank");
    if (mode === "tour") this.$nextTick(() => this.startTour("add"));
  },

  checklistSteps() {
    const s = this.stats || {};
    return [
      { key: "add", label: "Add your first artist", done: this.artists.length > 0, step: "add" },
      { key: "note", label: "Give it a note", done: this.artists.some((a) => (a.notes || "").trim()), step: "note" },
      { key: "update", label: "Run your first update", done: (s.tracks_total || 0) > 0, step: "update" },
      { key: "review", label: "Review a track", done: (s.listened || 0) + (s.revisit || 0) > 0, step: "open-track" },
    ];
  },
  checklistCount() { return this.checklistSteps().filter((x) => x.done).length; },
  showChecklist() {
    if (this.settings.checklist !== "active") return false;
    if (this.checklistCount() < 4) { DJ._checklistDoneAt = 0; return true; }
    // All four done: keep "All set" up for a moment, then retire the card.
    if (!DJ._checklistDoneAt) {
      DJ._checklistDoneAt = Date.now();
      setTimeout(() => this.dismissChecklist(), 2600);
    }
    return true;
  },
  dismissChecklist() {
    if (this.settings.checklist === "hidden") return;
    this.settings.checklist = "hidden";
    api.patchSettings({ checklist: "hidden" }).catch(() => {});
  },

  _tourArtist() { return this.artists.find((a) => a.id === this.tour.artistId) || null; },
  _tourRowSel(extra = "") { return `.artist[data-k="${this.tour.artistId}"]${extra}`; },
  _tourIndex(id) { return Math.max(0, this.tourSteps().findIndex((s) => s.id === id)); },
  tourNext() { const s = this.tourStep(); if (s) this.tour.skip = { ...this.tour.skip, [s.id]: true }; },

  tourSteps() {
    const t = this.tour, a = () => this._tourArtist();
    const hasNote = () => !!(a() && (a().notes || "").trim());
    const run = () => this.run;
    const skipped = (id) => !!(t.skip && t.skip[id]);
    const row = '.track-list .track[data-k]:not(.checked):not(.revisit)';
    return [
      { id: "add", title: "Add an artist", text: "This opens a box for their SoundCloud link.",
        target: '[data-tour="add-artist"]',
        done: () => this.showAddArtist || !!t.artistId },
      { id: "url", title: "Paste or finish the link", text: "Finish it with the name from their SoundCloud URL, which can differ from the name on their page. Then press Add.",
        target: '[data-tour="add-url"]',
        enter: () => {
          if (!this.addArtistUrl) this.addArtistUrl = "soundcloud.com/";
          this.$nextTick(() => {
            const el = document.querySelector('[data-tour="add-url"] input');
            if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
          });
        },
        done: () => !!t.artistId },
      { id: "settings", title: "Open its settings", text: "Right-click the artist to open its settings.",
        target: () => this._tourRowSel(),
        action: { label: "Open its settings", run: () => a() && this.openArtistPanel(a()) },
        done: () => (this.artistPanel && this.artistPanel.id === t.artistId) || hasNote() || t.sawRun },
      { id: "note", title: "Give it a note", text: "Notes help with filtering and updating later. Write ukg here, and typing ukg in the artist filter shows only artists whose notes contain it, while Update > \"notes contain\" updates only those. Press Save when done.",
        target: ['[data-tour="notes"]', '[data-tour="notes-save"]'],
        action: { label: "Open its settings", run: () => a() && this.openArtistPanel(a()) },
        done: () => hasNote() || t.sawRun },
      { id: "update", title: "Update this artist", text: "Reads their SoundCloud page and lists their tracks.",
        target: () => this._tourRowSel(' [data-tour="artist-update"]'),
        action: { label: "Update this artist", run: () => a() && this.updateSingleArtist(a()) },
        done: () => t.sawRun },
      { id: "updating", title: "Updating", text: "A browser opens in the background and reads their page. In a few seconds the strip starts counting the tracks checked so far. Usually under a minute; keep the app open until it finishes.",
        target: '[data-tour="bg-strip"]', wait: true,
        // Guards against a reflex click on End tour the moment this appears.
        enter: () => { t.lock = true; setTimeout(() => { t.lock = false; }, 3000); },
        done: () => t.sawRun && !(run() && run().active) && !t.error },
      { id: "show-new", title: "See what it found", text: "Shows every new track from this update in one list.",
        target: '[data-tour="show-new"]',
        action: { label: "Show their tracks", run: () => { if (a()) { this.closeRunPanel(); this.selectedArtistIds = [a().id]; this.setStatus("new"); } } },
        done: () => !!this.runSet || (this.selectedArtistIds.includes(t.artistId) && !this.run) },
      { id: "open-track", title: "Open a track", text: "Click a track's name to open it on SoundCloud in your browser. The name underlines when the pointer is on it. The arrow at the end of the row does the same.",
        target: row + ' .track-name .tname', next: true, try: true,
        done: () => t.opened || skipped("open-track") },
      { id: "select", title: "Select tracks", text: "Click the row anywhere except the name, for example the artist or the empty space after the name. Ctrl+click adds more rows, Shift+click selects a range.",
        target: row, next: true, try: true,
        done: () => this.selectedTrackIds.length > 0 || skipped("select") },
      { id: "bulk", title: "Act on the selection", text: "These buttons apply to every selected track: mark listened, revisit, delete and more. Ctrl+Z undoes the last change.",
        target: '.bulk-actions', next: true,
        done: () => skipped("bulk") },
      { id: "review", title: "Review a track", text: "Heard marks it done. Later keeps it for another listen. Space marks the focused row.",
        target: row + ' .row-marks',
        enter: () => { t.base = (this.stats.listened || 0) + (this.stats.revisit || 0); },
        done: () => (this.stats.listened || 0) + (this.stats.revisit || 0) > t.base },
      { id: "filters", title: "Filter the list", text: "New is what you haven't heard yet. Listened and Revisit hold the rest. The counts follow the selected artist.",
        target: '.filterbar .chip-group[data-pill]', next: true,
        done: () => skipped("filters") },
      { id: "priority", title: "Set a priority", text: "Stars rank your artists. The sidebar sorts by them, and Update > Priority update runs only the ones at or above your threshold.",
        target: () => this._tourRowSel(" .stars"), next: true, try: true,
        enter: () => { t.prio = a() ? a().priority : 0; },
        done: () => skipped("priority") || !!(a() && a().priority !== t.prio) },
      { id: "update-menu", title: "Update in small batches", text: "Full update reads every active artist one by one, a few seconds each, so a big library can take an hour or more. Update a group instead: type a note word in this box, like ukg, to update only the artists whose notes contain it. Priority update and each artist's refresh button work too.",
        target: ['[data-tour="update-menu"]', '[data-tour="notes-update"]'], next: true,
        enter: () => { this.loadRunResults(); this.showUpdateMenu = true; },
        done: () => skipped("update-menu") },
      { id: "end", title: "That's the basics", text: "Add more artists one at a time, or use Update > Import new artists to bring in everyone you follow. You can replay this tour any time from About (the i button in the top bar).",
        target: null, final: true, enter: () => { this.showUpdateMenu = false; }, done: () => false },
    ];
  },

  startTour(start = 0) {
    this.endTour();
    const i = typeof start === "string" ? this._tourIndex(start) : start;
    this._tourStartIds = this.artists.map((a) => a.id);
    this.tour = { active: true, i: -1, artistId: null, sawRun: false, error: "", rect: null, pop: null,
      base: 0, start: i, skip: {}, opened: false, lock: false };
    this._tourGo(i);
    this._tourTimer = setInterval(() => this._tourTick(), 250);
    this._tourTick();
  },
  endTour() {
    clearInterval(this._tourTimer);
    this._tourTimer = null;
    if (!this.tour.active) return;
    this.tour = { ...this.tour, active: false };
    // Leave nothing half-done behind: the untouched prefill and the menu the tour opened.
    if (this.addArtistUrl === "soundcloud.com/") { this.addArtistUrl = ""; this.showAddArtist = false; }
    this.showUpdateMenu = false;
  },
  // Nothing found usually means a misspelled link, so that goes back to adding the
  // artist. Any other failure retries the update.
  tourRetry() {
    const relink = this.tour.nothing;
    this.tour.error = "";
    this.tour.nothing = false;
    this.tour.sawRun = false;
    this.closeRunPanel();
    if (relink) {
      this.tour.artistId = null;
      this._tourStartIds = this.artists.map((a) => a.id);
      this._tourGo(0);
    } else {
      this._tourGo(this._tourIndex("update"));
    }
  },
  _tourGo(i) {
    this.tour.i = i;
    const step = this.tourSteps()[i];
    if (step && step.enter) step.enter();
    this.$nextTick(() => DJ.anim.fx(document.querySelector(".tour-pop"), "fx-tour", 1));
  },

  _tourTick() {
    const t = this.tour;
    if (!t.active) return;
    if (!t.artistId) {
      const added = this.artists.find((a) => !this._tourStartIds.includes(a.id));
      if (added) t.artistId = added.id;
      else if (t.start > 1 && this.artists.length) {
        t.artistId = (this.artists.find((a) => !(a.notes || "").trim()) || this.artists[0]).id;
      }
    }
    const r = this.run;
    if (r && r.active && t.i >= this._tourIndex("settings")) t.sawRun = true;
    // A finished run that failed or found nothing stops the tour on that step.
    if (t.sawRun && r && !r.active && !t.error && t.i <= this._tourIndex("show-new")) {
      if (r.errors.length || (r.status && r.status !== "complete")) {
        t.error = (r.errors[0] && r.errors[0].message) || "The update stopped before it finished.";
      } else if (!r.added && !r.newCount && !((this._tourArtist() || {}).tracks_total)) {
        t.error = "Nothing found. Check the spelling of the link, then add the artist again. Right-click the wrong one to delete it.";
        t.nothing = true;
      }
    }
    const steps = this.tourSteps();
    // Each step's enter() runs before its done() is checked (it may set a baseline).
    while (!t.error && t.i < steps.length - 1 && steps[t.i].done()) this._tourGo(t.i + 1);
    this._tourPlace(steps[t.i]);
  },

  // A step can spotlight several elements at once; the hole covers all of them.
  _tourBox(step) {
    if (!step || this.tour.error) return null;
    let sels = typeof step.target === "function" ? step.target() : step.target;
    if (!sels) return null;
    if (!Array.isArray(sels)) sels = [sels];
    let box = null;
    for (const sel of sels) {
      const el = document.querySelector(sel);
      if (!el || el.offsetParent === null) continue;
      const r = el.getBoundingClientRect();
      if (!r.width) continue;
      box = box ? { left: Math.min(box.left, r.left), top: Math.min(box.top, r.top),
        right: Math.max(box.right, r.right), bottom: Math.max(box.bottom, r.bottom) }
        : { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
    }
    return box && { left: box.left, top: box.top, width: box.right - box.left, height: box.bottom - box.top };
  },

  _tourPlace(step) {
    const z = parseFloat(document.documentElement.style.zoom) || 1;
    const box = this._tourBox(step);
    const pop = document.querySelector(".tour-pop");
    const pw = (pop ? pop.offsetWidth : 300), ph = (pop ? pop.offsetHeight : 140);
    const vw = window.innerWidth / z, vh = window.innerHeight / z, pad = 6, gap = 12, edge = 10;
    if (!box || !box.width) {
      this.tour.rect = null;
      this.tour.pop = { x: (vw - pw) / 2, y: (vh - ph) / 2, side: "none", ax: 0 };
      return;
    }
    const r = { x: box.left / z - pad, y: box.top / z - pad, w: box.width / z + pad * 2, h: box.height / z + pad * 2 };
    this.tour.rect = r;
    let side = "below", y = r.y + r.h + gap;
    if (y + ph > vh - edge && r.y - gap - ph >= edge) { side = "above"; y = r.y - gap - ph; }
    y = Math.max(edge, Math.min(y, vh - ph - edge));
    const cx = r.x + r.w / 2;
    const x = Math.max(edge, Math.min(cx - pw / 2, vw - pw - edge));
    this.tour.pop = { x, y, side, ax: Math.max(16, Math.min(cx - x, pw - 16)) };
  },

  tourStep() { return this.tour.active ? this.tourSteps()[this.tour.i] : null; },
  tourHasTarget() { return !!this.tour.rect; },
};
