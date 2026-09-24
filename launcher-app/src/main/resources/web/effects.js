const FX = (() => {
  let enabled = true;
  let canvas, ctx, w = 0, h = 0, dpr = 1, particles = [], raf = 0, last = 0, mouseX = 0.5, mouseY = 0.5;
  const COLORS = ["255,224,138", "242,193,78", "179,166,255", "255,255,255", "125,227,176"];

  function init() {
    canvas = document.getElementById("sky");
    ctx = canvas.getContext("2d", { alpha: true });
    resize();
    window.addEventListener("resize", resize);
    window.addEventListener("mousemove", (e) => { mouseX = e.clientX / w; mouseY = e.clientY / h; }, { passive: true });
    document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); else start(); });
    start();
  }

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = window.innerWidth; h = window.innerHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const target = Math.min(90, Math.round((w * h) / 22000));
    while (particles.length < target) particles.push(spawn(true));
    particles.length = target;
  }

  function spawn(anywhere) {
    return {
      x: Math.random() * w,
      y: anywhere ? Math.random() * h : h + 10,
      r: Math.random() * 1.6 + 0.4,
      vy: -(Math.random() * 0.18 + 0.05),
      vx: (Math.random() - 0.5) * 0.08,
      tw: Math.random() * Math.PI * 2,
      ts: Math.random() * 0.02 + 0.005,
      depth: Math.random() * 0.8 + 0.2,
      c: COLORS[(Math.random() * COLORS.length) | 0],
    };
  }

  function frame(t) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(48, t - (last || t));
    last = t;
    ctx.clearRect(0, 0, w, h);
    const px = (mouseX - 0.5) * 18, py = (mouseY - 0.5) * 12;
    for (const p of particles) {
      p.x += p.vx * dt * 0.06; p.y += p.vy * dt * 0.06; p.tw += p.ts * dt * 0.06;
      if (p.y < -10) Object.assign(p, spawn(false));
      const a = 0.25 + 0.55 * (0.5 + 0.5 * Math.sin(p.tw));
      const x = p.x + px * p.depth, y = p.y + py * p.depth;
      ctx.beginPath();
      ctx.fillStyle = `rgba(${p.c},${a * p.depth})`;
      ctx.arc(x, y, p.r * (0.6 + p.depth), 0, Math.PI * 2);
      ctx.fill();
      if (p.r > 1.5) {
        ctx.fillStyle = `rgba(${p.c},${a * 0.12})`;
        ctx.beginPath(); ctx.arc(x, y, p.r * 5, 0, Math.PI * 2); ctx.fill();
      }
    }
  }

  function start() { if (enabled && !raf) { last = 0; raf = requestAnimationFrame(frame); } }
  function stop() { if (raf) cancelAnimationFrame(raf); raf = 0; if (ctx) ctx.clearRect(0, 0, w, h); }

  function setEnabled(on) {
    enabled = on;
    document.documentElement.dataset.motion = on ? "full" : "reduced";
    if (on) start(); else stop();
  }

  function burst(x, y, count = 22) {
    if (!enabled) return;
    const root = document.createElement("div");
    root.className = "burst";
    root.style.left = x + "px"; root.style.top = y + "px";
    for (let i = 0; i < count; i++) {
      const dot = document.createElement("i");
      const angle = (Math.PI * 2 * i) / count + Math.random() * 0.4;
      const dist = 40 + Math.random() * 70;
      dot.style.setProperty("--x", Math.cos(angle) * dist + "px");
      dot.style.setProperty("--y", Math.sin(angle) * dist + "px");
      dot.style.animationDelay = Math.random() * 0.08 + "s";
      if (i % 3 === 0) dot.style.background = "#b3a6ff";
      root.appendChild(dot);
    }
    document.body.appendChild(root);
    setTimeout(() => root.remove(), 1200);
  }

  function ripple(button, event) {
    const rect = button.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height);
    const r = document.createElement("span");
    r.className = "ripple";
    r.style.width = r.style.height = size + "px";
    r.style.left = (event.clientX - rect.left - size / 2) + "px";
    r.style.top = (event.clientY - rect.top - size / 2) + "px";
    button.appendChild(r);
    setTimeout(() => r.remove(), 800);
  }

  function countUp(el, to, suffix = "", ms = 900) {
    if (!enabled) { el.textContent = to + suffix; return; }
    const from = 0, start = performance.now();
    const step = (t) => {
      const k = Math.min(1, (t - start) / ms);
      const e = 1 - Math.pow(1 - k, 3);
      el.textContent = Math.round(from + (to - from) * e) + suffix;
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  return { init, setEnabled, burst, ripple, countUp, pause: stop, resume: start };
})();
