const FX = (() => {
  const counts = new Map();
  const reduced = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;

  function setEnabled(on) {
    document.documentElement.dataset.motion = on ? "full" : "reduced";
  }

  function enabled() {
    return document.documentElement.dataset.motion !== "reduced" && !(reduced && reduced.matches);
  }

  function restart(el, cls) {
    if (!el) return;
    el.classList.remove(cls);
    void el.offsetWidth;
    el.classList.add(cls);
  }

  function replay(root) {
    if (!root || !enabled()) return;
    const groups = root.classList && root.classList.contains("stagger") ? [root] : [...root.querySelectorAll(".stagger")];
    groups.forEach((g) => { g.classList.remove("settled"); restart(g, "stagger-go"); });
  }

  function ripple(target, event) {
    if (!target || !enabled()) return;
    const rect = target.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height) * 2.2;
    const x = event && event.clientX ? event.clientX - rect.left : rect.width / 2;
    const y = event && event.clientY ? event.clientY - rect.top : rect.height / 2;
    const dot = document.createElement("span");
    dot.className = "fx-ripple";
    dot.style.cssText = `width:${size}px;height:${size}px;left:${x - size / 2}px;top:${y - size / 2}px`;
    target.appendChild(dot);
    dot.addEventListener("animationend", () => dot.remove(), { once: true });
  }

  function pulse(el) {
    if (enabled()) restart(el, "fx-pulse");
  }

  function countUp(el, key, to, format = (v) => String(v), ms = 900) {
    if (!el) return;
    const from = counts.has(key) ? counts.get(key) : 0;
    counts.set(key, to);
    if (!enabled() || from === to) { el.textContent = format(to); return; }
    const start = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - start) / ms);
      const eased = 1 - Math.pow(1 - k, 4);
      el.textContent = format(Math.round(from + (to - from) * eased));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function leave(el, then) {
    if (!el) { if (then) then(); return; }
    if (!enabled()) { el.remove(); if (then) then(); return; }
    el.style.height = el.offsetHeight + "px";
    el.classList.add("fx-leave");
    requestAnimationFrame(() => { el.style.height = "0px"; });
    setTimeout(() => { el.remove(); if (then) then(); }, 280);
  }

  function spotlight(card) {
    if (!card || card.dataset.spot) return;
    card.dataset.spot = "1";
    let frame = 0;
    card.addEventListener("pointermove", (e) => {
      if (!enabled() || frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const r = card.getBoundingClientRect();
        card.style.setProperty("--mx", ((e.clientX - r.left) / r.width * 100).toFixed(1) + "%");
        card.style.setProperty("--my", ((e.clientY - r.top) / r.height * 100).toFixed(1) + "%");
      });
    }, { passive: true });
  }

  return { setEnabled, enabled, restart, replay, ripple, pulse, countUp, leave, spotlight };
})();
