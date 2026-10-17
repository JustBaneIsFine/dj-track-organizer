// Themed tooltips for any element with data-tip. One floating element, delegated events.
(function () {
  const DELAY = 300, GAP = 8, EDGE = 6;
  let tip = null, timer = null, target = null;

  function el() {
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "tip";
      tip.setAttribute("role", "tooltip");
      document.body.appendChild(tip);
    }
    return tip;
  }

  function place(t, anchor) {
    const r = anchor.getBoundingClientRect();
    const w = t.offsetWidth, h = t.offsetHeight;
    const vw = window.innerWidth, vh = window.innerHeight;
    // Below by default, above when it would run off the bottom.
    let top = r.bottom + GAP;
    let below = true;
    if (top + h > vh - EDGE && r.top - GAP - h >= EDGE) { top = r.top - GAP - h; below = false; }
    let left = r.left + r.width / 2 - w / 2;
    left = Math.max(EDGE, Math.min(left, vw - w - EDGE));
    t.style.left = Math.round(left) + "px";
    t.style.top = Math.round(top) + "px";
    t.dataset.side = below ? "below" : "above";
  }

  function show(anchor) {
    const text = anchor.getAttribute("data-tip");
    if (!text || !anchor.isConnected) return;
    const t = el();
    t.textContent = text;
    t.classList.remove("on");
    t.style.left = "-9999px";
    t.style.top = "0px";
    place(t, anchor);
    requestAnimationFrame(() => t.classList.add("on"));
  }

  function hide() {
    clearTimeout(timer);
    timer = null;
    target = null;
    if (tip) tip.classList.remove("on");
  }

  document.addEventListener("mouseover", (e) => {
    const a = e.target.closest && e.target.closest("[data-tip]");
    if (a === target) return;
    hide();
    if (!a || !a.getAttribute("data-tip")) return;
    target = a;
    timer = setTimeout(() => { if (target === a) show(a); }, DELAY);
  });
  document.addEventListener("mouseout", (e) => {
    if (!target) return;
    const to = e.relatedTarget;
    if (!to || !target.contains(to)) hide();
  });
  document.addEventListener("mousedown", hide, true);
  document.addEventListener("keydown", hide, true);
  window.addEventListener("scroll", hide, true);
  window.addEventListener("blur", hide);
})();
