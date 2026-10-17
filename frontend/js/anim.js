// Motion. The level lives on <html data-anim="0..3">: 0 Off, 1 Low, 2 Mid, 3 All.
// CSS gates on the attribute; anything driven from here checks on(n) first.
window.DJ = window.DJ || {};

(function () {
  const root = document.documentElement;
  const SPRING = "cubic-bezier(.34,1.56,.64,1)";
  const EASE = "cubic-bezier(.2,.7,.3,1)";
  const level = () => (root.dataset.anim === undefined ? 2 : +root.dataset.anim);
  const on = (n) => level() >= n;
  const ease = () => (on(2) ? SPRING : EASE);
  // UI scale is CSS zoom on <html>: rects come back zoomed, transforms are not.
  const zoom = () => parseFloat(root.style.zoom) || 1;
  const tick = () => new Promise((r) => (window.Alpine ? Alpine.nextTick(r) : setTimeout(r)));
  const cssVar = (n) => getComputedStyle(root).getPropertyValue(n).trim();

  // Replay a one-shot CSS animation class.
  function fx(el, cls, min = 2) {
    if (!el || !on(min)) return;
    el.classList.remove(cls);
    void el.offsetWidth;
    el.classList.add(cls);
    const done = (e) => { if (e.target !== el) return; el.classList.remove(cls); el.removeEventListener("animationend", done); };
    el.addEventListener("animationend", done);
  }

  function inView(container, el) {
    const b = container.getBoundingClientRect(), r = el.getBoundingClientRect();
    return r.bottom > b.top && r.top < b.bottom;
  }
  function visibleRows(container, sel) {
    if (!container) return [];
    return [...container.querySelectorAll(sel)].filter((el) => el.offsetParent && inView(container, el));
  }

  // Rows shrink to nothing. Returns the animations so a stale caller can cancel them.
  function collapse(els, stagger = 0) {
    if (!on(1) || !els.length) return [];
    return els.map((el, i) => {
      el.style.overflow = "hidden";
      return el.animate(
        [{ height: el.offsetHeight / zoom() + "px", opacity: 1 },
         { height: "0px", opacity: 0, paddingTop: "0px", paddingBottom: "0px", borderBottomWidth: "0px" }],
        { duration: 240, easing: EASE, delay: i * stagger, fill: "forwards" });
    });
  }
  const settle = (anims) => Promise.all(anims.map((a) => a.finished.catch(() => {})));

  function expand(el, { drop = false, flash = false } = {}) {
    if (!el || !on(1)) return;
    const h = el.offsetHeight / zoom();
    el.style.overflow = "hidden";
    const a = el.animate(
      [{ height: "0px", opacity: 0, transform: drop ? "translateY(-10px)" : "none" },
       { height: h + "px", opacity: 1, transform: "none" }],
      { duration: 300, easing: ease() });
    a.finished.then(() => { el.style.overflow = ""; }).catch(() => {});
    if (flash) glow(el);
  }

  function glow(el) {
    if (!el || !on(2)) return;
    el.animate([{ backgroundColor: cssVar("--accent-soft") || "rgba(95,211,95,.14)" }, { backgroundColor: "transparent" }],
      { duration: 1100, easing: "ease-out" });
  }

  function pop(el) {
    if (!el || !on(2)) return;
    el.animate([{ transform: "scale(.97)" }, { transform: "none" }], { duration: 260, easing: SPRING });
  }

  function cascade(els, { step = 25, max = 15, y = 4 } = {}) {
    if (!on(2)) return;
    els.slice(0, max).forEach((el, i) => el.animate(
      [{ opacity: 0, transform: `translateY(${y}px)` }, { opacity: 1, transform: "none" }],
      { duration: 220, easing: EASE, delay: i * step, fill: "backwards" }));
  }

  // FLIP a keyed list ([data-k]): record, mutate, then glide moved rows from where they were.
  async function flip(container, sel, mutate, { stagger = 0, onAdd } = {}) {
    if (!container || !on(1)) { await mutate(); return; }
    const before = new Map();
    for (const el of container.querySelectorAll(sel)) before.set(el.dataset.k, el.getBoundingClientRect().top);
    await mutate();
    await tick();
    const box = container.getBoundingClientRect(), z = zoom();
    let i = 0;
    for (const el of container.querySelectorAll(sel)) {
      if (!el.offsetParent || !inView(container, el)) continue;
      const top = el.getBoundingClientRect().top;
      if (!before.has(el.dataset.k)) { onAdd && onAdd(el); continue; }
      let dy = before.get(el.dataset.k) - top;
      if (Math.abs(dy) < 1) continue;
      dy = Math.max(-box.height, Math.min(box.height, dy)) / z;
      el.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }],
        { duration: 340, easing: ease(), delay: stagger * i++, fill: "backwards" });
    }
  }

  // Dialogs grow out of whatever was pressed to open them.
  let origin = null;
  document.addEventListener("pointerdown", (e) => {
    const b = e.target.closest && e.target.closest("button, .chip, .artist, .track, .swatch");
    if (!b) return;
    const r = b.getBoundingClientRect();
    origin = { x: r.left + r.width / 2, y: r.top + r.height / 2, t: Date.now() };
  }, true);

  function dialogIn(overlay) {
    const modal = overlay.querySelector(".modal");
    if (!modal || !on(1)) return;
    overlay.animate([{ backgroundColor: "rgba(0,0,0,0)" }, { backgroundColor: "rgba(0,0,0,.6)" }],
      { duration: 200, easing: "ease-out" });
    if (!on(2)) {
      modal.animate([{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }],
        { duration: 200, easing: EASE });
      return;
    }
    const r = modal.getBoundingClientRect(), z = zoom();
    const o = origin && Date.now() - origin.t < 1500 ? origin : null;
    modal.style.transformOrigin = o ? `${(o.x - r.left) / z}px ${(o.y - r.top) / z}px` : "50% 50%";
    modal.animate([{ opacity: 0, transform: "scale(.5)" }, { opacity: 1, transform: "none" }],
      { duration: 300, easing: SPRING });
    if (modal.classList.contains("stagger-in") && on(3)) {
      const parts = [...modal.querySelectorAll(".body > *, .foot")].filter((el) => el.offsetParent);
      parts.forEach((el, i) => el.animate(
        [{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "none" }],
        { duration: 320, easing: SPRING, delay: 140 + i * 90, fill: "backwards" }));
    }
  }

  // Counters: any [data-roll] whose number changes rolls in from the direction it moved.
  function roll(el) {
    const v = parseInt((el.textContent || "").replace(/[^\d-]/g, ""), 10);
    const prev = el._rv;
    el._rv = v;
    if (prev === undefined || isNaN(v) || isNaN(prev) || v === prev) return;
    if (on(2)) {
      const up = v > prev;
      el.animate([{ transform: `translateY(${up ? 55 : -55}%)`, opacity: 0 }, { transform: "none", opacity: 1 }],
        { duration: 300, easing: SPRING });
    }
    el.dispatchEvent(new CustomEvent("roll", { bubbles: true, detail: { prev, v } }));
  }

  // Click-driven icon moves: [what was clicked, which icon, class, only when].
  const CLICK = [
    [".artist .mini", ".ic-arrows-clockwise", "fx-spin"],
    [".mark.heard", ".chk", "fx-draw", (el) => !el.classList.contains("on")],
    [".mark.later", ".mark-box", "fx-ring", (el) => !el.classList.contains("on")],
    [".mark.later", ".ic", "fx-rewind", (el) => !el.classList.contains("on")],
    [".star", ".ic", "fx-bounce"],
    [".chip", ".ic-download-simple", "fx-drop"],
    [".chip", ".ic-shopping-cart-simple", "fx-roll"],
    ["button", ".ic-copy", "fx-pop"],
    ["button", ".ic-arrow-u-up-left", "fx-swing"],
    [".bulk-actions button", ".ic-clock-counter-clockwise", "fx-rewind"],
    ["button", ".ic-pause", "fx-squash"],
    ["button", ".ic-stop", "fx-squash"],
    [".track .tname, .track .ext", (el) => el.closest(".track").querySelector(".ext .ic"), "fx-nudge"],
  ];
  document.addEventListener("click", (e) => {
    if (!on(2) || !e.target.closest) return;
    for (const [sel, ic, cls, when] of CLICK) {
      const el = e.target.closest(sel);
      if (!el || (when && !when(el))) continue;
      fx(typeof ic === "function" ? ic(el) : el.querySelector(ic), cls);
    }
  }, true);

  // Theme and accent: circle out from the control at All, a crossfade below that.
  async function reveal(from, apply) {
    if (!on(1) || !document.startViewTransition) { apply(); return; }
    const circle = on(3) && from && from.getBoundingClientRect;
    if (circle) {
      const r = from.getBoundingClientRect();
      root.style.setProperty("--vt-x", ((r.left + r.width / 2) / innerWidth) * 100 + "%");
      root.style.setProperty("--vt-y", ((r.top + r.height / 2) / innerHeight) * 100 + "%");
      root.classList.add("vt-circle");
    }
    try {
      const vt = document.startViewTransition(async () => { apply(); await tick(); });
      await vt.finished;
    } catch (e) {}
    root.classList.remove("vt-circle");
  }

  // A few sparkles thrown off an element.
  function burst(el, n = 7) {
    if (!el || !on(3)) return;
    const r = el.getBoundingClientRect(), z = zoom();
    for (let i = 0; i < n; i++) {
      const s = document.createElement("i");
      s.className = "ic ic-sparkle burst";
      s.style.left = (r.left + r.width * (0.55 + Math.random() * 0.45)) / z + "px";
      s.style.top = (r.top + r.height / 2) / z + "px";
      document.body.appendChild(s);
      const a = Math.random() * Math.PI * 2, d = 14 + Math.random() * 22;
      s.animate([{ transform: "translate(-50%,-50%) scale(.3)", opacity: 1 },
                 { transform: `translate(calc(-50% + ${Math.cos(a) * d}px), calc(-50% + ${Math.sin(a) * d}px)) scale(1) rotate(60deg)`, opacity: 0 }],
        { duration: 700 + Math.random() * 300, easing: EASE, delay: i * 30 }).finished.then(() => s.remove(), () => s.remove());
    }
  }

  function floatUp(el, text) {
    if (!el || !on(3)) return;
    const r = el.getBoundingClientRect(), z = zoom();
    const f = document.createElement("span");
    f.className = "float-up";
    f.textContent = text;
    f.style.left = (r.left + r.width / 2) / z + "px";
    f.style.top = r.top / z + "px";
    document.body.appendChild(f);
    f.animate([{ transform: "translate(-50%,0)", opacity: 1 }, { transform: "translate(-50%,-18px)", opacity: 0 }],
      { duration: 900, easing: EASE }).finished.then(() => f.remove(), () => f.remove());
  }

  // Sliding highlight for single-choice chip groups (.chip-group[data-pill]).
  function pills(scope) {
    for (const g of scope.querySelectorAll(".chip-group[data-pill]")) {
      let p = g.querySelector(":scope > .pill");
      if (!on(1)) { if (p) p.remove(); g.classList.remove("has-pill"); continue; }
      if (!p) { p = document.createElement("span"); p.className = "pill"; g.prepend(p); }
      g.classList.add("has-pill");
      const act = [...g.querySelectorAll(":scope > .chip.on:not(.no-pill)")].filter((c) => c.offsetParent);
      if (act.length !== 1) { p.style.opacity = "0"; p.dataset.shown = ""; continue; }
      const c = act[0], first = !p.dataset.shown;
      if (first) p.style.transition = "none";
      Object.assign(p.style, { left: c.offsetLeft + "px", top: c.offsetTop + "px",
        width: c.offsetWidth + "px", height: c.offsetHeight + "px", opacity: "1" });
      p.dataset.shown = "1";
      if (first) { void p.offsetWidth; p.style.transition = ""; }
    }
  }
  function watchPills(scope) {
    let raf = 0;
    const run = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => pills(scope)); };
    new MutationObserver((ms) => { if (ms.some((m) => !m.target.classList?.contains("pill"))) run(); })
      .observe(scope, { subtree: true, attributes: true, attributeFilter: ["class", "style"], childList: true });
    new ResizeObserver(run).observe(scope);
    run();
  }

  // Watches the app root for dialogs opening and counters changing.
  function watch(app) {
    new MutationObserver((ms) => {
      for (const m of ms) {
        if (m.target === app) {
          for (const n of m.addedNodes) if (n.classList && n.classList.contains("overlay")) dialogIn(n);
        } else if (m.target.dataset && m.target.dataset.roll !== undefined) roll(m.target);
      }
    }).observe(app, { childList: true, subtree: true });
    for (const el of app.querySelectorAll("[data-roll]")) roll(el);
  }

  window.DJ.anim = { on, level, fx, collapse, settle, expand, glow, pop, cascade, flip, reveal,
    burst, floatUp, visibleRows, watch, watchPills, tick };
})();

window.DJ.animMixin = {
  animLevel: 3,
  animLabels: ["Off", "Low", "Mid", "All"],
  animHints: [
    "Nothing moves. Spinners and loading rows stay.",
    "Only motion that shows what changed: lists, dialogs, progress bars.",
    "Adds springy feedback on clicks, counters and icons.",
    "Adds hover effects, cascades and the theme reveal.",
  ],
  runSeen: 0,

  _applyAnim() {
    const v = this.settings.animation_level;
    const n = v === undefined || v === null || v === "" ? 3 : parseInt(v);
    this.animLevel = Math.max(0, Math.min(3, isNaN(n) ? 3 : n));
    document.documentElement.dataset.anim = String(this.animLevel);
  },
  setAnimLevel(v) {
    this.settings.animation_level = String(parseInt(v) || 0);
    this._applyAnim();
    api.patchSettings({ animation_level: this.settings.animation_level }).catch(() => {});
  },

  _initAnim() {
    const root = document.documentElement, A = DJ.anim;
    root.classList.add("anim-intro");
    setTimeout(() => root.classList.add("anim-ready"), 800);
    setTimeout(() => root.classList.remove("anim-intro"), 1500);
    A.watch(this.$root);
    const fb = document.querySelector(".filterbar");
    if (fb) A.watchPills(fb);
    try { this.runSeen = parseInt(localStorage.getItem("dj_run_seen") || "0") || 0; } catch (e) {}
    this.$watch("showUpdateMenu", (open) => {
      if (open || !this.runResults.length) return;
      this.runSeen = Math.max(this.runSeen, ...this.runResults.map((r) => r.id));
      try { localStorage.setItem("dj_run_seen", String(this.runSeen)); } catch (e) {}
    });
    let prevSel = new Set();
    this.$watch("selectedTrackIds", () => {
      const cur = this.selectedTrackIds;
      const added = cur.filter((id) => !prevSel.has(id));
      prevSel = new Set(cur);
      if (!added.length || added.length > 20 || !A.on(2)) return;
      this.$nextTick(() => {
        for (const id of added) A.pop(document.querySelector(`.track[data-k="${id}"]`));
      });
    });
  },

  // Sidebar refresh button keeps turning while that artist is being read.
  isUpdatingArtist(a) {
    const r = this.run;
    return !!(r && r.active && r.bgActive && r.bgName === a.name);
  },

  onTodayRoll(e) {
    const { prev, v } = e.detail || {};
    if (!(v > prev) || !e.target.offsetParent) return;
    const wrap = e.target.closest("[data-today]");
    DJ.anim.fx(wrap && wrap.querySelector(".ic-headphones"), "fx-headbob");
    DJ.anim.floatUp(e.target, "+" + (v - prev));
  },
};
