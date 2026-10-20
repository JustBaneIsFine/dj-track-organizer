// "Check folder for tracks I already own."
// Only file NAMES are collected and matched; the app never modifies files. A scan
// is saved as a check whose matches are decided one at a time, so a review can be
// paused and picked up later. A match can be played in the user's default player.
window.DJ = window.DJ || {};
window.DJ.ownedMixin = {
  ownedBusy: false,
  ownedScan: null,     // { phase: 'reading'|'matching'|'native', count } - drives the progress modal
  showChecks: false,   // the check folder screen
  checks: [],          // saved checks, for the list view
  check: null,         // the open check: { id, root, items, tab, forced, history, changed }

  _audioExts: [".mp3", ".flac", ".wav", ".aif", ".aiff", ".m4a", ".aac", ".ogg", ".wma", ".alac"],
  checkKinds: [
    { key: "exact", label: "Exact", why: "Artist and title are both in the file name" },
    { key: "title", label: "Title only", why: "The title matches, but the file name has no artist" },
    { key: "fuzzy", label: "Fuzzy", why: "Close, but not an exact match" },
  ],

  // Top bar button: open the list of saved checks, or go straight to picking a
  // folder when there are none.
  async openChecks() {
    try { this.checks = await api.listChecks(); } catch (e) { this.checks = []; }
    if (!this.checks.length) { await this.pickFolder(); return; }
    this.check = null;
    this.showChecks = true;
  },

  // Pick a folder WITHOUT the browser's "upload" dialog. Native OS dialog first: the
  // server walks names AND paths, so a match can be played. The sandboxed File
  // System Access picker stays as a fallback (names only, nothing to play).
  // opts: { artistId, floor } for the per-artist check (scoped pool, lenient floor).
  async pickFolder(opts) {
    opts = opts || {};
    try {
      await this._pickViaNative(opts);
    } catch (err) {
      if (window.showDirectoryPicker) await this._pickViaFsApi(opts);
      else this.toast(err.message, "err");
    }
  },

  async checkArtistFolder(a) {
    if (!a) return;
    const floor = parseInt(this.settings.owned_match_floor_artist || 88);
    this.closeArtistPanel();
    await this.pickFolder({ artistId: a.id, floor });
  },

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
    try { dir = await window.showDirectoryPicker(); } catch (err) { return; }
    const names = [];
    const collect = async (handle) => {
      for await (const entry of handle.values()) {
        if (entry.kind === "file") {
          names.push(entry.name);
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
      await new Promise(r => setTimeout(r));
      const floor = opts.floor != null ? opts.floor : null;
      const stopPoll = this._startProgressPoll(token);
      let r;
      try { r = await api.scanOwned(audio, dir.name, floor, opts.artistId, token); }
      finally { stopPoll(); }
      await this._afterScan(r);
    } catch (err) { this.toast(err.message, "err"); }
    finally { this.ownedBusy = false; this.ownedScan = null; }
  },

  async _pickViaNative(opts) {
    this.ownedBusy = true;
    this.ownedScan = { phase: "native", count: 0 };
    let picked;
    try {
      picked = await api.pickFolderNative();
    } catch (err) {
      this.ownedBusy = false; this.ownedScan = null;
      throw err;
    }
    if (picked.cancelled) { this.ownedBusy = false; this.ownedScan = null; return; }
    try {
      const floor = opts.floor != null ? opts.floor : null;
      const token = this._newScanToken();
      this.ownedScan = { phase: "matching", count: picked.count, percent: 0 };
      await new Promise(r => setTimeout(r));
      const stopPoll = this._startProgressPoll(token);
      let r;
      try { r = await api.scanPending(picked.pending, floor, opts.artistId, token); }
      finally { stopPoll(); }
      await this._afterScan(r);
    } catch (err) { this.toast(err.message, "err"); }
    finally { this.ownedBusy = false; this.ownedScan = null; }
  },

  async _afterScan(r) {
    if (!r.check_id) { this.toast(`No matches in ${r.scanned.toLocaleString()} files`); return; }
    if (!r.added) this.toast("No new matches. Opening the check you already have for this folder.");
    await this.openCheck(r.check_id);
  },

  async openCheck(id) {
    try {
      const c = await api.getCheck(id);
      c.forced = null; c.history = []; c.changed = false;
      c.tab = (this.checkKinds.find((k) => this._left(c, k.key).length) || { key: "decided" }).key;
      this.check = c;
      this.showChecks = true;
    } catch (e) { this.toast(e.message, "err"); }
  },

  async removeCheck(c) {
    const left = c.left_count;
    const msg = left ? `Remove this check? ${left} undecided matches are dropped. Decisions already made stay.`
                     : "Remove this check from the list? Decisions already made stay.";
    if (!(await this.uiConfirm(msg))) return;
    try { await api.deleteCheck(c.id); this.checks = this.checks.filter((x) => x.id !== c.id); }
    catch (e) { this.toast(e.message, "err"); }
  },

  // Leave the screen. Every decision is already saved, so this is also "pause".
  async closeChecks() {
    const c = this.check;
    this.showChecks = false;
    this.check = null;
    if (c && this._leftAll(c) > 0) this.toast("Saved. Continue from Check folder.");
    if (c && c.changed) { this.loadTracks(); this.loadArtists(); this.loadStats(); }
  },
  async backToChecks() {
    const c = this.check;
    try { this.checks = await api.listChecks(); } catch (e) {}
    this.check = null;
    if (c && c.changed) { this.loadTracks(); this.loadArtists(); this.loadStats(); }
  },

  // Fuzzy matches under the slider are hidden, not rejected: they stay in the check.
  fuzzyFrom() { return parseInt(this.settings.owned_fuzzy_from || 90); },
  fuzzyDanger() { return this.fuzzyFrom() < 90; },
  fuzzyMax() { return parseInt(this.settings.owned_match_threshold || 98) - 1; },
  setFuzzyFrom(v, save) {
    this.settings.owned_fuzzy_from = String(v);
    if (this.check) this.check.forced = null;
    if (save) api.patchSettings({ owned_fuzzy_from: String(v) }).catch(() => {});
  },
  _shown(i) { return i.kind !== "fuzzy" || i.score >= this.fuzzyFrom(); },
  _undecided(i) { return i.state === "pending" || i.state === "skipped"; },

  // Undecided items of one kind: untouched ones first, skipped ones after, each by score.
  _left(c, kind) {
    return c.items.filter((i) => i.kind === kind && this._undecided(i) && this._shown(i))
      .sort((a, b) => (a.state === "skipped") - (b.state === "skipped") || b.score - a.score || a.id - b.id);
  },
  _leftAll(c) { return c.items.filter((i) => this._undecided(i)).length; },
  checkLeft(kind) { return this.check ? this._left(this.check, kind).length : 0; },
  checkLeftAll() { return this.check ? this.check.items.filter((i) => this._undecided(i) && this._shown(i)).length : 0; },
  checkHidden() { return this.check ? this.check.items.filter((i) => this._undecided(i) && !this._shown(i)).length : 0; },
  checkDecided() {
    if (!this.check) return [];
    return this.check.items.filter((i) => i.state === "same" || i.state === "different")
      .sort((a, b) => (b.decided_at || "").localeCompare(a.decided_at || "") || b.id - a.id);
  },
  checkPct() {
    const c = this.check;
    return c && c.items.length ? Math.round(100 * (c.items.length - this._leftAll(c)) / c.items.length) : 0;
  },
  checkKind(key) { return this.checkKinds.find((k) => k.key === key) || {}; },

  checkCurrent() {
    const c = this.check;
    if (!c || c.tab === "decided") return null;
    if (c.forced) {
      const f = c.items.find((i) => i.id === c.forced);
      if (f && f.kind === c.tab && (f.state === "pending" || f.state === "skipped")) return f;
    }
    return this._left(c, c.tab)[0] || null;
  },
  checkPosition() {
    const c = this.check, cur = this.checkCurrent();
    if (!cur) return "";
    const all = c.items.filter((i) => i.kind === c.tab && this._shown(i)).length;
    return `${all - this._left(c, c.tab).length + 1} of ${all}`;
  },

  setCheckTab(key) { if (this.check) { this.check.tab = key; this.check.forced = null; } },
  nextCheckTab() {
    const keys = ["exact", "title", "fuzzy", "decided"];
    const i = keys.indexOf(this.check.tab);
    this.setCheckTab(keys[(i + 1) % keys.length]);
  },

  async decideCurrent(decision) {
    const it = this.checkCurrent();
    if (!it) return;
    this._swipe(decision);
    await this._decide([it], decision);
  },

  // Every decision sends the match off to the right, tinted by the decision, and
  // the next one settles in underneath. Back brings it in again from the right.
  // Only the match slides; the buttons stay put.
  _swipe(kind) {
    if (!DJ.anim.on(1)) return;
    const src = document.querySelector(".cs-slide");
    const body = document.querySelector(".cs-body");
    if (kind !== "back" && src && body) {
      const ghost = src.cloneNode(true);
      ghost.setAttribute("x-ignore", "");
      ghost.classList.add("cs-ghost", "cs-ghost-" + kind);
      Object.assign(ghost.style, { top: src.offsetTop + "px", left: src.offsetLeft + "px", width: src.offsetWidth + "px" });
      body.appendChild(ghost);
      ghost.addEventListener("animationend", () => ghost.remove(), { once: true });
      setTimeout(() => ghost.remove(), 800);
    }
    this.$nextTick(() => {
      const el = document.querySelector(".cs-slide");
      if (el) el.classList.remove("cs-return", "cs-settle");
      DJ.anim.fx(el, kind === "back" ? "cs-return" : "cs-settle", 1);
    });
  },

  async _decide(items, decision) {
    const c = this.check;
    const before = items.map((i) => ({ i, state: i.state, action: i.action, decided_at: i.decided_at }));
    const ts = new Date().toISOString();
    items.forEach((i) => {
      i.state = decision;
      i.action = decision === "same" ? "owned" : null;
      i.decided_at = decision === "skipped" ? null : ts;
    });
    c.forced = null;
    c.history.push(items.map((i) => i.id));
    if (decision !== "skipped") c.changed = true;
    try { await api.decideItems(items.map((i) => i.id), decision); }
    catch (e) {
      before.forEach((b) => Object.assign(b.i, { state: b.state, action: b.action, decided_at: b.decided_at }));
      c.history.pop();
      this.toast(e.message, "err");
    }
  },

  async acceptAllExact() {
    const items = this._left(this.check, "exact");
    if (!items.length) return;
    if (!(await this.uiConfirm(`Mark all ${items.length} exact matches as the same track? They get tagged as owned. Back undoes it.`))) return;
    await this._decide(items, "same");
  },

  // Back: undo the last decision (or batch) and show that match again.
  async checkBack() {
    const c = this.check;
    if (!c || !c.history.length) return;
    const ids = c.history.pop();
    await this._undo(ids);
    const first = c.items.find((i) => i.id === ids[0]);
    if (first) { c.tab = first.kind; c.forced = ids.length === 1 ? first.id : null; }
    this._swipe("back");
  },
  async undoItem(it) {
    await this._undo([it.id]);
    this.check.history = this.check.history.map((h) => h.filter((id) => id !== it.id)).filter((h) => h.length);
  },
  async _undo(ids) {
    const c = this.check;
    const set = new Set(ids);
    const items = c.items.filter((i) => set.has(i.id));
    const before = items.map((i) => ({ i, state: i.state, action: i.action, decided_at: i.decided_at }));
    items.forEach((i) => { i.state = "pending"; i.action = null; i.decided_at = null; });
    c.changed = true;
    try { await api.undoItems(ids); }
    catch (e) {
      before.forEach((b) => Object.assign(b.i, { state: b.state, action: b.action, decided_at: b.decided_at }));
      this.toast(e.message, "err");
    }
  },

  async playCheckItem(it) {
    if (!it || !it.has_path) return;
    try { await api.openOwnedFile(it.id); }
    catch (err) { this.toast(err.message.replace(/^\d+: /, ""), "err"); }
  },
  openCheckLink(it) { if (it && it.url) window.open(it.url, "_blank"); },

  checkKey(e) {
    if (!this.showChecks || !this.check || this.confirmBox) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const k = e.key.toLowerCase();
    const cur = this.checkCurrent();
    if (e.key === "Tab") { e.preventDefault(); this.nextCheckTab(); }
    else if (k === "y" && cur) this.decideCurrent("same");
    else if (k === "n" && cur) this.decideCurrent("different");
    else if (k === "s" && cur) this.decideCurrent("skipped");
    else if (k === "u") this.checkBack();
    else if (k === "p" && cur) this.playCheckItem(cur);
    else if (k === "o" && cur) this.openCheckLink(cur);
    else return;
    e.stopPropagation();
  },

  // Words one side shares with the other, in bold. Returns escaped HTML.
  checkBold(text, other) {
    const norm = (w) => w.toLowerCase().normalize("NFKD").replace(/[^\p{L}\p{N}]/gu, "");
    const have = new Set(String(other || "").split(/[\s\-_()[\].,&]+/).map(norm).filter(Boolean));
    const esc = (s) => s.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    return String(text || "").split(/([\s\-_()[\].,&]+)/).map((part) => {
      const n = norm(part);
      return n && have.has(n) ? `<b>${esc(part)}</b>` : esc(part);
    }).join("");
  },
  checkScTitle(it) {
    const t = it.track_name || "";
    return it.artist_name && !t.toLowerCase().includes(it.artist_name.toLowerCase()) ? `${it.artist_name} - ${t}` : t;
  },
  checkLabel(c) { return c.artist_name ? `${c.root} · ${c.artist_name}` : c.root; },
};
