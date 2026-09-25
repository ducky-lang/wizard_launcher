(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let lang = "en";
  const t = (key, vars = {}) => {
    let s = (I18N[lang] && I18N[lang][key]) || I18N.en[key] || key;
    for (const [k, v] of Object.entries(vars)) s = s.split("{" + k + "}").join(String(v));
    return s;
  };
  const fmtBytes = (b) => {
    if (!b) return "0 MB";
    if (b >= 1 << 30) return (b / (1 << 30)).toFixed(2) + " GB";
    if (b >= 1 << 20) return (b / (1 << 20)).toFixed(1) + " MB";
    return Math.max(1, Math.round(b / 1024)) + " KB";
  };
  const ago = (ms) => {
    if (!ms) return t("time.never");
    const m = Math.floor((Date.now() - ms) / 60000);
    if (m < 1) return t("time.now");
    if (m < 60) return t("time.min", { n: m });
    if (m < 1440) return t("time.hour", { n: Math.floor(m / 60) });
    return t("time.day", { n: Math.floor(m / 1440) });
  };
  const debounce = (fn, ms) => { let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); }; };

  const state = {
    info: null, settings: null, accounts: [], selected: null,
    game: { state: "idle", fraction: null, message: "", step: -1 },
    page: "home", libTab: "mods", libQuery: "", logs: [], follow: true, filter: "",
    shots: [], shotIndex: 0,
  };
  const PAGES = [["home", "home"], ["library", "library"], ["screenshots", "image"], ["world", "castle"], ["console", "terminal"], ["settings", "settings"]];
  const rendered = {};

  async function call(cmd, args) {
    try {
      return await WL.call(cmd, args);
    } catch (e) {
      toast("err", t("toast.error"), e.message);
      throw e;
    }
  }

  function toast(kind, title, body = "") {
    const icon = kind === "ok" ? ICONS.check : kind === "err" ? ICONS.alert : ICONS.info;
    const el = document.createElement("div");
    el.className = "toast " + kind;
    const life = kind === "err" ? 7000 : 3600;
    el.innerHTML = `${icon}<div><b>${esc(title)}</b>${body ? `<span>${esc(body)}</span>` : ""}</div><i class="life" style="animation-duration:${life}ms"></i>`;
    $("#toasts").appendChild(el);
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 320); }, life);
  }

  function modal(html, { wide = false, onClose } = {}) {
    const root = $("#modalRoot");
    const backdrop = document.createElement("div");
    backdrop.className = "backdrop";
    const box = document.createElement("div");
    box.className = "modal" + (wide ? " wide" : "");
    box.innerHTML = html;
    root.append(backdrop, box);
    const close = () => {
      if (box.dataset.closing) return;
      box.dataset.closing = "1";
      box.classList.add("closing"); backdrop.classList.add("closing");
      setTimeout(() => { box.remove(); backdrop.remove(); }, 230);
      if (onClose) onClose();
    };
    backdrop.addEventListener("click", close);
    box.close = close;
    return box;
  }

  function confirmBox(title, body, danger = false) {
    return new Promise((resolve) => {
      let answered = false;
      const m = modal(`<h2>${esc(title)}</h2><p class="sub">${esc(body)}</p><div class="actions"><button class="ghost" data-a="no">${t("btn.cancel")}</button><button class="btn ${danger ? "arcane" : ""}" data-a="yes">${t("btn.confirm")}</button></div>`,
        { onClose: () => { if (!answered) resolve(false); } });
      $$("[data-a]", m).forEach((b) => b.addEventListener("click", () => { answered = true; resolve(b.dataset.a === "yes"); m.close(); }));
    });
  }

  function closeTopLayer() {
    const lb = $(".lightbox");
    if (lb) { lb.remove(); return true; }
    const pop = $("#popoverRoot .popover");
    if (pop) { closePopover(); return true; }
    const modals = $$("#modalRoot .modal:not(.closing)");
    if (modals.length) { modals[modals.length - 1].close(); return true; }
    return false;
  }

  function avatar(acct, large = false) {
    const cls = "avatar" + (large ? " lg" : "");
    if (!acct) return `<div class="${cls}">?</div>`;
    const initial = esc((acct.name || "?")[0].toUpperCase());
    return `<div class="${cls}" data-initial="${initial}"><img src="/media/avatar/${encodeURIComponent(acct.uuid)}" alt=""></div>`;
  }

  function buildNav() {
    const nav = $("#nav");
    nav.querySelectorAll("button").forEach((b) => b.remove());
    PAGES.forEach(([id, icon], i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.page = id;
      b.innerHTML = `${ICONS[icon]}<span>${t("nav." + id)}</span>`;
      b.title = `${t("nav." + id)}  (Ctrl+${i + 1})`;
      b.style.setProperty("--i", i);
      b.addEventListener("click", () => go(id));
      nav.appendChild(b);
    });
    const pages = $("#pages");
    pages.innerHTML = PAGES.map(([id]) => `<section class="page" id="page-${id}"></section>`).join("");
    Object.keys(rendered).forEach((k) => delete rendered[k]);
  }

  function moveIndicator() {
    const active = $(`#nav button[data-page="${state.page}"]`);
    const ind = $("#navIndicator");
    if (active && ind) ind.style.transform = `translateY(${active.offsetTop}px)`;
  }

  function go(page) {
    const prev = state.page;
    const order = PAGES.map(([id]) => id);
    const changed = prev !== page;
    state.page = page;
    $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
    $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-" + page));
    $("#pageTitle").textContent = t("nav." + page);
    $("#pageSubtitle").textContent = t("sub." + page);
    const el = $("#page-" + page);
    const seen = !!rendered[page];
    if (changed && el) {
      el.style.setProperty("--dir", prev && order.indexOf(page) < order.indexOf(prev) ? -1 : 1);
      FX.restart(el, "entering");
      FX.restart($(".title-wrap"), "swap");
    }
    moveIndicator();
    render(page);
    if (changed && seen) FX.replay(el);
  }

  function render(page, force = false) {
    const el = $("#page-" + page);
    if (!el) return;
    if (page === "home") renderHome(el, force);
    else if (page === "library") renderLibrary(el);
    else if (page === "screenshots") renderScreenshots(el);
    else if (page === "world") renderWorld(el);
    else if (page === "console") renderConsole(el, force);
    else if (page === "settings") renderSettings(el, force);
  }

  function renderChips() {
    const info = state.info || {};
    const chips = [];
    if (state.game.state === "playing") chips.push(`<span class="chip ok"><span class="dot"></span>${t("chip.playing")}</span>`);
    chips.push(state.settings && state.settings.offline_only
      ? `<span class="chip warn"><span class="dot"></span>${t("chip.offline")}</span>`
      : `<span class="chip ${info.online ? "ok" : ""}"><span class="dot"></span>${t("chip.online")}</span>`);
    chips.push(info.offlineReady
      ? `<span class="chip ok"><span class="dot"></span>${t("chip.ready")}</span>`
      : `<span class="chip warn"><span class="dot"></span>${t("chip.notReady")}</span>`);
    if (info.update && info.update.newer) chips.push(`<span class="chip gold" id="updateChip">${ICONS.sparkle}${t("chip.update", { v: esc(info.update.latest) })}</span>`);
    $("#chips").innerHTML = chips.join("");
    const u = $("#updateChip");
    if (u) u.addEventListener("click", () => call("system.openUrl", { url: info.update.url }));
  }

  function renderAccountChip() {
    const a = state.selected;
    $("#accountChip").innerHTML = `${avatar(a)}<div class="who"><b>${esc(a ? a.name : t("acct.none"))}</b><span>${a ? (a.type === "msa" ? t("acct.ms") : t("acct.offline")) : t("acct.add.offline")}</span></div>${ICONS.chevron}`;
  }

  function bindHeroArt() {
    const art = $("#heroArt");
    if (!art) return;
    art.addEventListener("load", () => art.classList.add("loaded"), { once: true });
    art.addEventListener("error", () => art.remove(), { once: true });
  }

  function retryHeroArt() {
    const hero = $(".hero");
    if (!hero || $("#heroArt", hero)) return;
    hero.insertAdjacentHTML("afterbegin", `<img class="hero-art" id="heroArt" src="/media/hero.jpg?${Date.now()}" alt="">`);
    bindHeroArt();
  }

  function playLabel() {
    const g = state.game.state;
    if (g === "playing") return t("play.playing");
    if (g === "installing" || g === "launching") return t("play.launching");
    return state.info && state.info.offlineReady ? t("play") : t("play.install");
  }

  function renderHome(el, force) {
    if (!rendered.home || force) {
      const info = state.info || {};
      el.innerHTML = `
        <section class="hero">
          <img class="hero-art" id="heroArt" src="/media/hero.jpg" alt="">
          <div class="hero-shade"></div>
          <div class="hero-copy">
            <div class="eyebrow">${t("hero.eyebrow")}</div>
            <h2>${t("hero.title")}</h2>
            <p class="lead">${t("hero.lead")}</p>
            <div class="meta">
              <span>${ICONS.cube}Minecraft ${esc(info.clientVersion || "1.20.1")}</span>
              <span>${ICONS.feather}Fabric</span>
              <span>${ICONS.zap}${esc(info.modpack || "Fabulously Optimized")}</span>
              <span>${ICONS.castle}${t("card.world")} ${esc(info.serverVersion || "1.16.5")}</span>
            </div>
            <div class="play-row">
              <button class="play" id="playBtn" type="button"><span class="pi">${ICONS.play}</span><span class="pl"></span></button>
              <button class="ghost danger" id="stopBtn" type="button" hidden>${ICONS.stop}${t("play.stop")}</button>
            </div>
            <p class="muted" id="playHint" style="margin:12px 0 0"></p>
            <div class="launch" id="launch">
              <div class="bar fx" id="launchBar"><i></i></div>
              <div class="launch-status"><b id="launchMsg"></b><span id="launchPct"></span></div>
              <div class="steps" id="steps">${["prepare", "game", "mods", "portal", "launch"].map((s) => `<span>${t("step." + s)}</span>`).join("")}</div>
            </div>
          </div>
        </section>
        <div class="grid cols-3 stagger" id="homeCards"></div>`;
      rendered.homeCards = false;
      bindHeroArt();
      $("#playBtn").addEventListener("click", onPlay);
      $("#stopBtn").addEventListener("click", () => call("game.stop"));
      rendered.home = true;
    }
    updatePlay();
    renderHomeCards();
  }

  async function renderHomeCards() {
    const box = $("#homeCards");
    if (!box) return;
    const info = state.info || {};
    const world = info.world || {};
    const mem = info.memory || {};
    const total = mem.totalMb || 8192;
    const pct = (v) => Math.min(100, Math.round((v / total) * 100));
    const intro = !rendered.homeCards;
    rendered.homeCards = true;
    box.classList.toggle("settled", !intro);
    box.innerHTML = `
      <div class="card hover">
        <h3>${ICONS.castle}${t("card.world")}</h3>
        <div class="stat"><b id="worldSize">${world.installed ? fmtBytes(world.sizeBytes) : "—"}</b><span>${world.installed ? t("world.installed") : t("world.notInstalled")}</span></div>
        <dl class="kv" style="margin-top:14px">
          <dt>${t("world.last")}</dt><dd>${ago(world.lastPlayed)}</dd>
          <dt>${t("world.backups")}</dt><dd>${(world.backups || []).length}</dd>
        </dl>
        <div class="row" style="margin-top:16px"><button class="ghost" id="homeBackup">${ICONS.archive}${t("world.backup")}</button></div>
      </div>
      <div class="card hover">
        <h3>${ICONS.memory}${t("card.performance")}</h3>
        <div class="mem${intro ? " intro" : ""}">
          <div class="mem-row"><span>${t("perf.game")}</span><div class="bar"><i style="width:${pct(mem.clientMb)}%"></i></div><em data-num="client" data-to="${mem.clientMb || 0}">${mem.clientMb || 0} MB</em></div>
          <div class="mem-row"><span>${t("perf.world")}</span><div class="bar"><i style="width:${pct(mem.serverMb)}%;background:linear-gradient(90deg,#6f5ce0,#b3a6ff)"></i></div><em data-num="server" data-to="${mem.serverMb || 0}">${mem.serverMb || 0} MB</em></div>
          <div class="mem-row"><span>${t("perf.system")}</span><div class="bar"><i style="width:100%;background:rgba(255,255,255,.18);box-shadow:none"></i></div><em>${Math.round(total / 1024)} GB</em></div>
        </div>
        <div class="row" style="margin-top:14px"><span class="tag gold">${t("perf.profile")}: ${t("set.profile." + String((state.settings || {}).memory_profile || "BALANCED").toLowerCase())}</span></div>
      </div>
      <div class="card hover">
        <h3>${ICONS.palette}${t("card.legacy")}</h3>
        <p class="muted" style="margin:0 0 14px">${t("legacy.body")}</p>
        <span class="tag ${info.legacySupport ? "ok" : "warn"}">${info.legacySupport ? t("legacy.active") : t("legacy.missing")}</span>
      </div>
      <div class="card hover news" style="grid-column: 1 / -1">
        <h3>${ICONS.sparkle}${t("card.news")} · ${esc(info.version || "")}</h3>
        <ul>${[1, 2, 3, 4, 5].map((i) => `<li>${ICONS.check}<span>${t("news." + i)}</span></li>`).join("")}</ul>
      </div>`;
    $$(".card.hover", box).forEach(FX.spotlight);
    $$("[data-num]", box).forEach((e) => FX.countUp(e, "mem." + e.dataset.num, Number(e.dataset.to), (v) => v + " MB"));
    $("#homeBackup").addEventListener("click", async () => {
      await call("worlds.backup"); toast("ok", t("toast.backup")); await refreshInfo();
    });
  }

  function updatePlay() {
    const btn = $("#playBtn");
    if (!btn) return;
    const g = state.game;
    const busy = g.state === "installing" || g.state === "launching";
    btn.classList.toggle("busy", busy);
    btn.classList.toggle("playing", g.state === "playing");
    const label = playLabel();
    const iconKind = busy ? "busy" : g.state === "playing" ? "playing" : "idle";
    if ($(".pl", btn).textContent !== label) {
      $(".pl", btn).textContent = label;
      FX.restart($(".pl", btn), "swap");
    }
    if (btn.dataset.icon !== iconKind) {
      btn.dataset.icon = iconKind;
      $(".pi", btn).innerHTML = busy ? '<span class="spinner"></span>' : g.state === "playing" ? ICONS.check : ICONS.play;
      FX.restart($(".pi", btn), "swap");
    }
    $("#stopBtn").hidden = !(g.state === "playing" || busy);
    const launch = $("#launch");
    launch.classList.toggle("show", busy);
    if (busy) {
      const bar = $("#launchBar");
      bar.classList.toggle("indeterminate", g.fraction == null);
      if (g.fraction != null) $("i", bar).style.setProperty("--f", Math.max(0, Math.min(1, g.fraction)).toFixed(4));
      $("#launchMsg").textContent = g.message || "";
      $("#launchPct").textContent = g.fraction != null ? Math.round(g.fraction * 100) + "%" : "";
      $$("#steps span").forEach((s, i) => { s.classList.toggle("on", i === g.step); s.classList.toggle("done", i < g.step); });
    }
    const info = state.info || {};
    $("#playHint").textContent = g.state === "idle" ? (info.offlineReady ? t("play.hint.ready") : t("play.hint.first", { gb: info.downloadGb || 3 })) : "";
  }

  async function onPlay(e) {
    if (state.game.state !== "idle" && state.game.state !== "error") return;
    FX.ripple($("#playBtn"), e);
    if (!state.selected) { openAccountMenu(); return; }
    state.game = { state: "launching", fraction: null, message: "", step: 0 };
    updatePlay();
    try { await call("game.play"); } catch (err) { state.game = { state: "idle" }; updatePlay(); }
  }

  async function renderLibrary(el) {
    if (!rendered.library) {
      el.innerHTML = `
        <div class="toolbar">
          <div class="tabs" id="libTabs"><span class="tab-pill"></span>
            <button data-tab="mods">${ICONS.cube}${t("lib.mods")} <span class="count" data-count="mods"></span></button>
            <button data-tab="packs">${ICONS.palette}${t("lib.packs")} <span class="count" data-count="packs"></span></button>
            <button data-tab="shaders">${ICONS.sun}${t("lib.shaders")} <span class="count" data-count="shaders"></span></button>
          </div>
          <div class="search">${ICONS.search}<input id="libSearch" placeholder="${t("lib.search")}"></div>
          <span class="spacer"></span>
          <button class="ghost" id="libFolder">${ICONS.folder}${t("lib.folder")}</button>
          <button class="btn" id="libImport">${ICONS.plus}${t("lib.import")}</button>
        </div>
        <div class="list" id="libList"></div>`;
      $$("#libTabs button").forEach((b) => b.addEventListener("click", () => { state.libTab = b.dataset.tab; renderLibraryList(); }));
      $("#libSearch").addEventListener("input", debounce((e) => { state.libQuery = e.target.value.toLowerCase(); renderLibraryList(false); }, 120));
      $("#libFolder").addEventListener("click", () => call(state.libTab + ".openFolder"));
      $("#libImport").addEventListener("click", async () => {
        const r = await call(state.libTab + ".import");
        if (r && r.count) { toast("ok", t("toast.imported", { n: r.count })); renderLibraryList(); }
      });
      rendered.library = true;
    }
    renderLibraryList();
  }

  function moveTabPill() {
    const active = $(`#libTabs button[data-tab="${state.libTab}"]`);
    const pill = $("#libTabs .tab-pill");
    if (!active || !pill) return;
    pill.style.width = active.offsetWidth + "px";
    pill.style.transform = `translateX(${active.offsetLeft - 4}px)`;
    pill.style.left = "4px";
  }

  const libCache = {};
  async function renderLibraryList(reload = true) {
    $$("#libTabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === state.libTab));
    moveTabPill();
    const list = $("#libList");
    if (reload || !libCache[state.libTab]) {
      if (!libCache[state.libTab]) list.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
      try { libCache[state.libTab] = await WL.call(state.libTab + ".list"); } catch (e) { libCache[state.libTab] = []; }
      const c = $(`[data-count="${state.libTab}"]`);
      if (c) c.textContent = libCache[state.libTab].length;
    }
    const q = state.libQuery;
    const items = libCache[state.libTab].filter((i) => !q || JSON.stringify(i).toLowerCase().includes(q));
    if (!items.length) {
      list.innerHTML = `<div class="empty">${ICONS.sparkle}<div>${t("lib.empty." + state.libTab)}</div></div>`;
      return;
    }
    const fresh = list.dataset.tab !== state.libTab;
    list.dataset.tab = state.libTab;
    list.className = "list stagger" + (fresh ? "" : " settled");
    if (state.libTab === "mods") {
      list.innerHTML = items.map((m) => `
        <div class="item ${m.enabled ? "" : "off"}">
          <div class="ic">${m.hasIcon ? `<img src="/media/modicon/${encodeURIComponent(m.file)}" alt="">` : ICONS.cube}</div>
          <div class="body"><div class="name">${esc(m.name)} <small>${esc(m.version)}</small> <span class="tag ${m.managed ? "gold" : "arcane"}">${m.managed ? t("lib.managed") : t("lib.yours")}</span></div>
          <div class="desc">${esc(m.description || (m.authors || []).join(", "))}</div></div>
          ${m.managed ? "" : `<button class="iconbtn danger" data-remove="${esc(m.file)}" title="${t("lib.remove")}">${ICONS.trash}</button>`}
          <label class="switch"><input type="checkbox" data-toggle="${esc(m.file)}" ${m.enabled ? "checked" : ""}><span></span></label>
        </div>`).join("");
    } else if (state.libTab === "packs") {
      list.innerHTML = items.map((p) => `
        <div class="item ${p.enabled ? "" : "off"}">
          <div class="ic">${p.hasIcon ? `<img src="/media/packicon/${encodeURIComponent(p.name)}" alt="">` : ICONS.palette}</div>
          <div class="body"><div class="name">${esc(p.name)} ${p.legacy ? `<span class="tag ok">${t("lib.legacy")}</span>` : `<span class="tag">${t("lib.format", { f: p.format })}</span>`}</div>
          <div class="desc">${esc(p.description)}</div></div>
          ${p.legacy ? `<button class="iconbtn" data-export="${esc(p.name)}" title="${t("lib.export")}">${ICONS.upload}</button>` : ""}
          <button class="iconbtn danger" data-remove="${esc(p.name)}" title="${t("lib.remove")}">${ICONS.trash}</button>
          <label class="switch"><input type="checkbox" data-toggle="${esc(p.name)}" ${p.enabled ? "checked" : ""}><span></span></label>
        </div>`).join("");
    } else {
      list.innerHTML = items.map((s) => `
        <div class="item"><div class="ic">${ICONS.sun}</div>
          <div class="body"><div class="name">${esc(s.name)}</div><div class="desc">${s.sizeBytes ? fmtBytes(s.sizeBytes) : ""}</div></div>
          <button class="iconbtn danger" data-remove="${esc(s.name)}" title="${t("lib.remove")}">${ICONS.trash}</button>
        </div>`).join("");
    }
    $$("[data-toggle]", list).forEach((i) => i.addEventListener("change", async () => {
      i.closest(".item").classList.toggle("off", !i.checked);
      await call(state.libTab + ".toggle", { name: i.dataset.toggle, enabled: i.checked });
      renderLibraryList();
    }));
    $$("[data-remove]", list).forEach((b) => b.addEventListener("click", async () => {
      if (!(await confirmBox(t("lib.removeConfirm", { name: b.dataset.remove }), "", true))) return;
      await call(state.libTab + ".remove", { name: b.dataset.remove });
      FX.leave(b.closest(".item"), () => renderLibraryList());
    }));
    $$("[data-export]", list).forEach((b) => b.addEventListener("click", async () => {
      const r = await call("packs.export", { name: b.dataset.export });
      if (r && r.path) toast("ok", t("toast.exported", { path: r.path }));
    }));
  }

  async function renderScreenshots(el) {
    el.innerHTML = `<div class="toolbar"><span class="spacer"></span><button class="ghost" id="shotFolder">${ICONS.folder}${t("shots.folder")}</button></div><div class="shots stagger" id="shotGrid"></div>`;
    $("#shotFolder").addEventListener("click", () => call("screenshots.openFolder"));
    const grid = $("#shotGrid");
    grid.innerHTML = '<div class="skeleton" style="height:150px"></div>'.repeat(4);
    state.shots = await WL.call("screenshots.list").catch(() => []);
    if (!state.shots.length) {
      grid.outerHTML = `<div class="empty">${ICONS.image}<div>${t("shots.empty")}</div></div>`;
      return;
    }
    grid.innerHTML = state.shots.map((s, i) => `
      <figure class="shot" data-i="${i}" style="margin:0"><img loading="lazy" src="/media/thumb/${encodeURIComponent(s.file)}" alt="">
      <figcaption>${esc(new Date(s.takenAt).toLocaleString(lang))}</figcaption></figure>`).join("");
    $$(".shot", grid).forEach((f) => f.addEventListener("click", () => openLightbox(+f.dataset.i)));
  }

  function openLightbox(index) {
    $(".lightbox")?.remove();
    state.shotIndex = (index + state.shots.length) % state.shots.length;
    const s = state.shots[state.shotIndex];
    const lb = document.createElement("div");
    lb.className = "lightbox";
    lb.innerHTML = `<img src="/media/shot/${encodeURIComponent(s.file)}" alt="">
      <button class="nav-l">${ICONS.left}</button><button class="nav-r">${ICONS.right}</button>
      <div class="lb-bar"><button class="ghost" data-a="open">${ICONS.external}${t("shots.open")}</button><button class="ghost danger" data-a="del">${ICONS.trash}${t("shots.delete")}</button><button class="ghost" data-a="close">${ICONS.x}</button></div>`;
    document.body.appendChild(lb);
    lb.addEventListener("click", (e) => { if (e.target === lb) lb.remove(); });
    $(".nav-l", lb).addEventListener("click", () => openLightbox(state.shotIndex - 1));
    $(".nav-r", lb).addEventListener("click", () => openLightbox(state.shotIndex + 1));
    $('[data-a="close"]', lb).addEventListener("click", () => lb.remove());
    $('[data-a="open"]', lb).addEventListener("click", () => call("screenshots.open", { name: s.file }));
    $('[data-a="del"]', lb).addEventListener("click", async () => {
      await call("screenshots.delete", { name: s.file });
      lb.remove();
      render("screenshots");
    });
  }

  async function renderWorld(el) {
    const w = await WL.call("worlds.info").catch(() => ({ installed: false, backups: [] }));
    el.innerHTML = `
      <div class="grid cols-2 stagger" style="margin-top:0">
        <div class="card">
          <h3>${ICONS.castle}${t("card.world")}</h3>
          <div class="stat"><b id="wSize">${w.installed ? fmtBytes(w.sizeBytes) : "—"}</b><span>${w.installed ? t("world.installed") : t("world.notInstalled")}</span></div>
          <dl class="kv" style="margin-top:14px"><dt>${t("world.last")}</dt><dd>${ago(w.lastPlayed)}</dd><dt>${t("world.backups")}</dt><dd>${w.backups.length}</dd></dl>
          <div class="row wrap" style="margin-top:18px">
            <button class="btn" id="wBackup">${ICONS.archive}${t("world.backup")}</button>
            <button class="ghost" id="wTest">${ICONS.pulse}${t("world.test")}</button>
            <button class="ghost" id="wFolder">${ICONS.folder}${t("world.folder")}</button>
            <button class="ghost danger" id="wReset">${ICONS.refresh}${t("world.reset")}</button>
          </div>
          <pre class="report" id="wReport" hidden style="margin-top:16px"></pre>
        </div>
        <div class="card">
          <h3>${ICONS.history}${t("world.backups")}</h3>
          <div class="list" id="wBackups">${w.backups.length ? w.backups.map((b) => `
            <div class="item"><div class="ic">${ICONS.archive}</div>
              <div class="body"><div class="name">${esc(new Date(b.createdAt).toLocaleString(lang))}</div><div class="desc">${fmtBytes(b.sizeBytes)} · ${esc(b.name)}</div></div>
              <button class="ghost" data-restore="${esc(b.name)}">${ICONS.history}${t("world.restore")}</button>
              <button class="iconbtn danger" data-del="${esc(b.name)}">${ICONS.trash}</button>
            </div>`).join("") : `<div class="empty">${ICONS.archive}<div>${t("world.noBackups")}</div></div>`}</div>
        </div>
      </div>`;
    $("#wBackup").addEventListener("click", async () => { await call("worlds.backup"); toast("ok", t("toast.backup")); renderWorld(el); });
    $("#wFolder").addEventListener("click", () => call("worlds.openFolder"));
    $("#wReset").addEventListener("click", async () => {
      if (!(await confirmBox(t("world.resetTitle"), t("world.resetBody"), true))) return;
      await call("worlds.reset"); toast("ok", t("toast.reset")); renderWorld(el);
    });
    $("#wTest").addEventListener("click", async (e) => {
      const b = e.currentTarget; b.disabled = true;
      const pre = $("#wReport"); pre.hidden = false; pre.textContent = "…";
      try { const r = await call("worlds.selftest"); pre.textContent = r.report; } catch (err) { pre.textContent = err.message; }
      b.disabled = false;
    });
    $$("[data-restore]", el).forEach((b) => b.addEventListener("click", async () => {
      if (!(await confirmBox(t("world.restoreTitle"), t("world.restoreBody", { name: b.dataset.restore }), true))) return;
      await call("worlds.restore", { name: b.dataset.restore }); toast("ok", t("toast.restored")); renderWorld(el);
    }));
    $$("[data-del]", el).forEach((b) => b.addEventListener("click", async () => {
      if (!(await confirmBox(t("lib.removeConfirm", { name: b.dataset.del }), "", true))) return;
      await call("worlds.deleteBackup", { name: b.dataset.del }); renderWorld(el);
    }));
  }

  function logClass(line) {
    if (/ERROR|Exception|FATAL/.test(line)) return "l-err";
    if (/WARN/.test(line)) return "l-warn";
    if (line.includes("[SERVER]") || line.includes("[PROXY]")) return "l-srv";
    return "";
  }

  function renderConsole(el, force) {
    if (!rendered.console || force) {
      el.innerHTML = `
        <div class="toolbar">
          <div class="search">${ICONS.search}<input id="logFilter" placeholder="${t("console.filter")}"></div>
          <span class="spacer"></span>
          <label class="row muted" style="gap:8px">${t("console.follow")}<span class="switch"><input type="checkbox" id="logFollow" ${state.follow ? "checked" : ""}><span></span></span></label>
          <button class="ghost" id="logCopy">${ICONS.copy}${t("console.copy")}</button>
          <button class="ghost" id="logFolder">${ICONS.folder}${t("console.folder")}</button>
        </div>
        <div class="console" id="console"></div>`;
      $("#logFilter").addEventListener("input", debounce((e) => { state.filter = e.target.value.toLowerCase(); paintLogs(); }, 120));
      $("#logFollow").addEventListener("change", (e) => { state.follow = e.target.checked; });
      $("#logCopy").addEventListener("click", async () => { await call("system.copy", { text: state.logs.join("\n") }); toast("ok", t("toast.copied")); });
      $("#logFolder").addEventListener("click", () => call("logs.openFolder"));
      rendered.console = true;
    }
    paintLogs();
  }

  function paintLogs() {
    const box = $("#console");
    if (!box) return;
    const lines = state.logs.filter((l) => !state.filter || l.toLowerCase().includes(state.filter)).slice(-1500);
    box.innerHTML = lines.map((l) => `<div class="${logClass(l)}">${esc(l)}</div>`).join("");
    if (state.follow) box.scrollTop = box.scrollHeight;
  }

  function appendLogs(lines) {
    state.logs.push(...lines);
    if (state.logs.length > 4000) state.logs.splice(0, state.logs.length - 4000);
    const box = $("#console");
    if (!box || state.page !== "console") return;
    const frag = document.createDocumentFragment();
    for (const line of lines) {
      if (state.filter && !line.toLowerCase().includes(state.filter)) continue;
      const div = document.createElement("div");
      div.className = logClass(line);
      div.textContent = line;
      frag.appendChild(div);
    }
    if (!frag.childNodes.length) return;
    box.appendChild(frag);
    let extra = box.childElementCount - 1500;
    while (extra-- > 0) box.firstElementChild.remove();
    if (state.follow) box.scrollTop = box.scrollHeight;
  }

  const saveSettings = debounce(async (patch) => {
    state.settings = await call("settings.set", patch);
    state.info = await WL.call("app.info").catch(() => state.info);
    toast("ok", t("set.saved"));
    renderChips();
  }, 450);
  let pending = {};
  function setSetting(key, value) {
    state.settings[key] = value;
    pending[key] = value;
    const patch = pending;
    saveSettings(patch);
    setTimeout(() => { if (pending === patch) pending = {}; }, 500);
  }

  function renderSettings(el, force) {
    if (rendered.settings && !force) return;
    const s = state.settings;
    const info = state.info || {};
    const mem = info.memory || {};
    const maxRam = Math.max(2048, Math.floor(((mem.totalMb || 8192) * 0.75) / 256) * 256);
    const toggle = (key) => `<label class="switch"><input type="checkbox" data-set="${key}" ${s[key] ? "checked" : ""}><span></span></label>`;
    const seg = (key, opts) => `<div class="seg" data-seg="${key}">${opts.map(([v, label]) => `<button data-v="${v}" class="${String(s[key]) === String(v) ? "active" : ""}">${label}</button>`).join("")}</div>`;
    const ram = (key, auto) => `<div class="range"><input type="range" data-ram="${key}" min="0" max="${maxRam}" step="256" value="${s[key] || 0}"><output data-out="${key}"></output></div>`;
    const res = `${s.game_width || 0}x${s.game_height || 0}`;
    const group = (id, title, body) => `<div class="card setting-group" id="sg-${id}"><h4>${title}</h4>${body}</div>`;
    const row = (title, desc, control) => `<div class="setting"><div class="s-text"><b>${title}</b><span>${desc}</span></div>${control}</div>`;
    const action = (id, icon, title, desc) => row(title, desc, `<button class="ghost" data-action="${id}">${ICONS[icon]}${t("btn.open")}</button>`);
    el.innerHTML = `
      <div class="settings">
        <nav class="settings-nav" id="setNav">
          ${["general", "game", "network", "appearance", "advanced", "maintenance"].map((g, i) => `<a data-to="${g}" class="${i === 0 ? "active" : ""}">${t("set." + g)}</a>`).join("")}
        </nav>
        <div class="stagger" style="display:flex;flex-direction:column;gap:18px">
          ${group("general", t("set.general"),
            row(t("set.language"), t("set.language.d"), seg("language", [["en", "English"], ["vi", "Tiếng Việt"]])) +
            row(t("set.after"), t("set.after.d"), seg("after_launch", [["KEEP_OPEN", t("set.after.keep")], ["MINIMIZE", t("set.after.min")], ["CLOSE", t("set.after.close")]])) +
            row(t("set.updates"), t("set.updates.d"), toggle("check_updates")))}
          ${group("game", t("set.game"),
            row(t("set.profile"), t("set.profile.d"), seg("memory_profile", [["LOW", t("set.profile.low")], ["BALANCED", t("set.profile.balanced")], ["HIGH", t("set.profile.high")]])) +
            row(t("set.clientRam"), "", ram("client_ram_mb")) +
            row(t("set.serverRam"), "", ram("server_ram_mb")) +
            row(t("set.resolution"), t("set.resolution.d"), `<select class="field" id="resSel">${[["0x0", t("set.res.default")], ["1280x720", "1280 × 720"], ["1600x900", "1600 × 900"], ["1920x1080", "1920 × 1080"], ["2560x1440", "2560 × 1440"]].map(([v, l]) => `<option value="${v}" ${v === res ? "selected" : ""}>${l}</option>`).join("")}</select>`) +
            row(t("set.fullscreen"), t("set.fullscreen.d"), toggle("fullscreen")) +
            row(t("set.java"), t("set.java.d"), `<input class="field wide" id="javaPath" value="${esc(s.java_path)}" placeholder="Java 17 (bundled)">`))}
          ${group("network", t("set.network"),
            row(t("set.offline"), t("set.offline.d"), toggle("offline_only")) +
            row(t("set.lan"), t("set.lan.d"), toggle("allow_lan")))}
          ${group("appearance", t("set.appearance"),
            row(t("set.animations"), t("set.animations.d"), toggle("animations")) +
            row(t("set.gpu"), t("set.gpu.d"), toggle("hardware_acceleration")))}
          ${group("advanced", t("set.advanced"),
            row(t("set.autorestart"), t("set.autorestart.d"), toggle("auto_restart_server")) +
            row(t("set.logs"), t("set.logs.d"), `<select class="field" data-num="keep_log_days">${[3, 7, 14, 30, 90].map((d) => `<option value="${d}" ${d === s.keep_log_days ? "selected" : ""}>${d}</option>`).join("")}</select>`))}
          ${group("maintenance", t("set.maintenance"),
            row(t("set.repair"), t("set.repair.d"), `<button class="ghost" data-action="repair">${ICONS.refresh}${t("set.repair")}</button>`) +
            row(t("set.bundleOut"), t("set.bundleOut.d"), `<button class="ghost" data-action="bundleOut">${ICONS.upload}${t("set.bundleOut")}</button>`) +
            row(t("set.bundleIn"), t("set.bundleIn.d"), `<button class="ghost" data-action="bundleIn">${ICONS.download}${t("set.bundleIn")}</button>`) +
            action("rules", "code", t("set.rules"), t("set.rules.d")) +
            action("data", "folder", t("set.data"), esc(info.dataDir || "")))}
        </div>
      </div>`;
    rendered.settings = true;
    $$("[data-set]", el).forEach((i) => i.addEventListener("change", () => {
      setSetting(i.dataset.set, i.checked);
      if (i.dataset.set === "animations") FX.setEnabled(i.checked);
    }));
    $$("[data-seg]", el).forEach((g) => $$("button", g).forEach((b) => b.addEventListener("click", () => {
      $$("button", g).forEach((x) => x.classList.toggle("active", x === b));
      setSetting(g.dataset.seg, b.dataset.v);
      if (g.dataset.seg === "language") { lang = b.dataset.v; relabel(); }
    })));
    $$("[data-ram]", el).forEach((r) => {
      const paint = () => {
        const v = +r.value;
        r.style.setProperty("--p", (v / +r.max) * 100 + "%");
        const autoMb = r.dataset.ram === "client_ram_mb" ? mem.clientMb : mem.serverMb;
        $(`[data-out="${r.dataset.ram}"]`, el).textContent = v < 512 ? t("set.ram.auto", { mb: autoMb || "" }) : v + " MB";
      };
      paint();
      r.addEventListener("input", paint);
      r.addEventListener("change", () => setSetting(r.dataset.ram, +r.value < 512 ? 0 : +r.value));
    });
    $("#resSel").addEventListener("change", (e) => {
      const [w, h] = e.target.value.split("x").map(Number);
      setSetting("game_width", w); setSetting("game_height", h);
    });
    $$("[data-num]", el).forEach((sel) => sel.addEventListener("change", () => setSetting(sel.dataset.num, +sel.value)));
    $("#javaPath").addEventListener("change", (e) => setSetting("java_path", e.target.value.trim()));
    $$("[data-action]", el).forEach((b) => b.addEventListener("click", () => runAction(b.dataset.action, b)));
    $$("#setNav a", el).forEach((a) => a.addEventListener("click", () => {
      $$("#setNav a", el).forEach((x) => x.classList.toggle("active", x === a));
      $("#sg-" + a.dataset.to).scrollIntoView({ behavior: s.animations ? "smooth" : "auto", block: "start" });
    }));
  }

  async function runAction(id, btn) {
    btn.disabled = true;
    try {
      if (id === "repair") { await call("tools.repair"); toast("ok", t("toast.repaired")); }
      else if (id === "bundleOut") { const r = await call("tools.exportBundle"); if (r && r.path) toast("ok", t("toast.exported", { path: r.path })); }
      else if (id === "bundleIn") { const r = await call("tools.importBundle"); if (r && r.done) { toast("ok", t("toast.repaired")); await refreshInfo(); } }
      else if (id === "rules") await call("tools.stateRules");
      else if (id === "data") await call("tools.openData");
    } finally { btn.disabled = false; }
  }

  function closePopover() {
    const pop = $("#popoverRoot .popover");
    if (!pop) return;
    pop.classList.add("closing");
    setTimeout(() => { $("#popoverRoot").innerHTML = ""; $("#popoverRoot").style.pointerEvents = "none"; }, 180);
  }

  function openAccountMenu() {
    const root = $("#popoverRoot");
    if ($(".popover", root)) { closePopover(); return; }
    const chip = $("#accountChip").getBoundingClientRect();
    root.style.pointerEvents = "auto";
    root.innerHTML = `<div class="popover" style="left:${chip.left}px;bottom:${window.innerHeight - chip.top + 10}px">
      <div class="muted" style="padding:4px 8px 8px;font-size:11px;letter-spacing:.2em;text-transform:uppercase">${t("acct.title")}</div>
      ${state.accounts.map((a) => `<div class="acct ${state.selected && a.id === state.selected.id ? "sel" : ""}" data-id="${esc(a.id)}">${avatar(a)}<div class="who"><b>${esc(a.name)}</b><span>${a.type === "msa" ? t("acct.ms") : t("acct.offline")}</span></div><button class="iconbtn danger" data-rm="${esc(a.id)}" title="${t("acct.remove")}">${ICONS.logout}</button></div>`).join("")}
      ${state.accounts.length ? '<div class="pop-sep"></div>' : ""}
      ${state.info && state.info.microsoftAvailable ? `<button class="pop-item" data-add="ms">${ICONS.microsoft}${t("acct.add.ms")}</button>` : ""}
      <button class="pop-item" data-add="offline">${ICONS.userPlus}${t("acct.add.offline")}</button>
    </div>`;
    root.addEventListener("click", (e) => { if (e.target === root) closePopover(); }, { once: true });
    $$(".acct", root).forEach((row) => row.addEventListener("click", async (e) => {
      if (e.target.closest("[data-rm]")) return;
      await call("accounts.select", { id: row.dataset.id });
      await refreshAccounts(); closePopover();
    }));
    $$("[data-rm]", root).forEach((b) => b.addEventListener("click", async () => {
      await call("accounts.remove", { id: b.dataset.rm });
      await refreshAccounts(); closePopover();
    }));
    $$("[data-add]", root).forEach((b) => b.addEventListener("click", () => {
      closePopover();
      if (b.dataset.add === "ms") microsoftLogin(); else offlineLogin();
    }));
  }

  function offlineLogin(onDone) {
    const m = modal(`<h2>${t("off.title")}</h2><p class="sub">${t("off.body")}</p>
      <input class="field" id="offName" style="width:100%;height:46px;font-size:16px" maxlength="16" placeholder="${t("off.placeholder")}" autofocus>
      <p class="muted" id="offErr" style="min-height:20px;margin:8px 0 0;color:var(--danger)"></p>
      <div class="actions"><button class="ghost" data-a="c">${t("btn.cancel")}</button><button class="btn" data-a="ok">${ICONS.check}${t("btn.save")}</button></div>`);
    const input = $("#offName", m);
    setTimeout(() => input.focus(), 50);
    const submit = async () => {
      const name = input.value.trim();
      if (!/^[A-Za-z0-9_]{3,16}$/.test(name)) { $("#offErr", m).textContent = t("off.invalid"); input.animate([{ transform: "translateX(-6px)" }, { transform: "translateX(6px)" }, { transform: "none" }], { duration: 260 }); return; }
      await call("accounts.addOffline", { name });
      await refreshAccounts();
      m.close();
      if (onDone) onDone();
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
    $('[data-a="ok"]', m).addEventListener("click", submit);
    $('[data-a="c"]', m).addEventListener("click", () => m.close());
  }

  async function microsoftLogin(onDone) {
    const m = modal(`<h2>${ICONS.microsoft.replace("<svg", '<svg style="width:22px;height:22px;vertical-align:-4px;margin-right:8px"')}${t("ms.title")}</h2><p class="sub">${t("ms.body")}</p>
      <div class="code-box"><span class="spinner"></span></div>
      <div class="row muted" id="msWait" style="gap:10px"><span class="spinner"></span>${t("ms.waiting")}</div>
      <div class="actions"><button class="ghost" data-a="c">${t("btn.cancel")}</button><button class="btn" data-a="copy" disabled>${ICONS.external}${t("ms.copy")}</button></div>`,
      { onClose: () => WL.call("accounts.msCancel").catch(() => {}) });
    $('[data-a="c"]', m).addEventListener("click", () => m.close());
    try {
      const r = await WL.call("accounts.msBegin");
      $(".code-box", m).innerHTML = `<b>${esc(r.code)}</b>`;
      const copy = $('[data-a="copy"]', m);
      copy.disabled = false;
      copy.addEventListener("click", async () => {
        await call("system.copy", { text: r.code });
        await call("system.openUrl", { url: r.uri });
      });
      m.msDone = onDone;
      msModal = m;
    } catch (e) {
      $(".code-box", m).innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`;
      $("#msWait", m).remove();
    }
  }
  let msModal = null;

  function crashDialog(crash) {
    const m = modal(`<h2>${t("crash.title")}</h2><p class="sub">${esc(crash.summary || "")}</p><p class="muted">${t("crash.body")}</p>
      <pre class="report">${esc(crash.text || "")}</pre>
      <div class="actions"><button class="ghost" data-a="folder">${ICONS.folder}${t("console.folder")}</button><button class="ghost" data-a="copy">${ICONS.copy}${t("btn.copy")}</button><button class="btn" data-a="c">${t("btn.close")}</button></div>`, { wide: true });
    $('[data-a="c"]', m).addEventListener("click", () => m.close());
    $('[data-a="copy"]', m).addEventListener("click", async () => { await call("system.copy", { text: crash.text || "" }); toast("ok", t("toast.copied")); });
    $('[data-a="folder"]', m).addEventListener("click", () => call("crash.openFolder"));
  }

  function onboarding() {
    let step = 0;
    let profile = state.settings.memory_profile || "BALANCED";
    const m = modal('<div class="onb" id="onb"></div>', { onClose: () => call("settings.set", { onboarding_done: true }) });
    const icons = ["wand", "globe", "user", "memory", "sparkle"];
    const draw = (dir = 0) => {
      const n = step + 1;
      let extra = "";
      if (step === 1) extra = `<div class="choice">${[["en", "English", "The castle speaks English"], ["vi", "Tiếng Việt", "Lâu đài nói tiếng Việt"]].map(([v, a, b]) => `<button data-lang="${v}" class="${lang === v ? "sel" : ""}">${ICONS.globe}<div><b>${a}</b><span>${b}</span></div></button>`).join("")}</div>`;
      if (step === 2) extra = `<div class="choice">${state.info.microsoftAvailable ? `<button data-acct="ms">${ICONS.microsoft}<div><b>${t("acct.add.ms")}</b><span>${t("ms.body").split(".")[0]}.</span></div></button>` : ""}<button data-acct="offline">${ICONS.userPlus}<div><b>${t("acct.add.offline")}</b><span>${t("off.body").split(".")[0]}.</span></div></button></div>${state.selected ? `<p class="muted" style="margin-top:12px">${ICONS.check.replace("<svg", '<svg style="width:14px;height:14px;vertical-align:-2px;color:var(--ok)"')} ${esc(state.selected.name)}</p>` : ""}`;
      if (step === 3) extra = `<div class="choice">${[["LOW", t("set.profile.low"), "~1.5 GB + 0.8 GB"], ["BALANCED", t("set.profile.balanced"), "~2-3 GB + 1.3 GB"], ["HIGH", t("set.profile.high"), "~3-4 GB + 2 GB"]].map(([v, a, b]) => `<button data-prof="${v}" class="${profile === v ? "sel" : ""}">${ICONS.memory}<div><b>${a}</b><span>${b}</span></div></button>`).join("")}</div>`;
      $("#onb", m).innerHTML = `<div class="onb-art">${ICONS[icons[step]]}</div><h2>${t("onb." + n + ".t")}</h2><p class="sub">${t("onb." + n + ".b")}</p>${extra}
        <div class="actions" style="justify-content:space-between"><button class="ghost" data-nav="back" ${step === 0 ? "style='visibility:hidden'" : ""}>${t("btn.back")}</button>
        <button class="btn" data-nav="next">${step === 4 ? t("btn.start") : t("btn.next")}</button></div>
        <div class="dots">${[0, 1, 2, 3, 4].map((i) => `<i class="${i === step ? "on" : ""}"></i>`).join("")}</div>`;
      if (dir) FX.restart($("#onb", m), dir > 0 ? "next" : "back");
      $$("[data-lang]", m).forEach((b) => b.addEventListener("click", () => { lang = b.dataset.lang; call("settings.set", { language: lang }); relabel(); draw(); }));
      $$("[data-acct]", m).forEach((b) => b.addEventListener("click", () => (b.dataset.acct === "ms" ? microsoftLogin(draw) : offlineLogin(draw))));
      $$("[data-prof]", m).forEach((b) => b.addEventListener("click", () => { profile = b.dataset.prof; call("settings.set", { memory_profile: profile }); draw(); }));
      $('[data-nav="back"]', m).addEventListener("click", () => { step = Math.max(0, step - 1); draw(-1); });
      $('[data-nav="next"]', m).addEventListener("click", async () => {
        if (step === 4) {
          m.close();
          state.settings = await WL.call("settings.get");
          state.info = await WL.call("app.info");
          render("home", true);
          return;
        }
        step++; draw(1);
      });
    };
    draw();
  }

  function relabel() {
    document.documentElement.lang = lang;
    const page = state.page;
    buildNav();
    go(page);
    renderChips();
    renderAccountChip();
  }

  async function refreshAccounts() {
    const r = await WL.call("accounts.list").catch(() => ({ accounts: [], selected: null }));
    state.accounts = r.accounts || [];
    state.selected = state.accounts.find((a) => a.id === r.selected) || null;
    renderAccountChip();
  }

  async function refreshInfo() {
    state.info = await WL.call("app.info").catch(() => state.info);
    renderChips();
    if (state.page === "home") { renderHomeCards(); retryHeroArt(); }
  }

  function bindEvents() {
    WL.on("launch.state", (d) => {
      const prev = state.game.state;
      state.game = Object.assign({}, state.game, d);
      if (d.state === "playing" && prev !== "playing") FX.pulse($("#playBtn"));
      if (d.state === "idle" && prev === "playing") toast("info", t("toast.gameClosed"));
      if (d.state === "error" && d.message) { toast("err", t("toast.error"), d.message); state.game.state = "idle"; }
      updatePlay(); renderChips();
      if (d.state === "idle") refreshInfo();
    });
    WL.on("launch.progress", (d) => {
      state.game.fraction = d.fraction; state.game.message = d.message;
      if (d.step != null && d.step >= 0) state.game.step = d.step;
      if (state.game.state === "idle") state.game.state = "launching";
      updatePlay();
    });
    WL.on("logs", (d) => appendLogs(d.lines || []));
    WL.on("game.crash", (d) => crashDialog(d));
    WL.on("accounts.msResult", async (d) => {
      if (d.ok) {
        toast("ok", t("ms.done", { name: d.name }));
        await refreshAccounts();
        if (msModal) { const cb = msModal.msDone; msModal.close(); msModal = null; if (cb) cb(); }
      } else if (msModal && d.error) {
        $(".code-box", msModal).innerHTML = `<span style="color:var(--danger)">${esc(d.error)}</span>`;
      }
    });
    WL.on("info.changed", () => refreshInfo());
    WL.on("navigate", (d) => go(d.page));
    $("#accountChip").addEventListener("click", openAccountMenu);
    window.addEventListener("resize", debounce(() => { moveIndicator(); moveTabPill(); }, 80));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && closeTopLayer()) { e.preventDefault(); return; }
      if ($(".lightbox")) {
        if (e.key === "ArrowLeft") openLightbox(state.shotIndex - 1);
        if (e.key === "ArrowRight") openLightbox(state.shotIndex + 1);
      }
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key === "Enter") { e.preventDefault(); go("home"); $("#playBtn")?.click(); }
      if (mod && /^[1-6]$/.test(e.key)) { e.preventDefault(); go(PAGES[+e.key - 1][0]); }
    });
    document.addEventListener("error", (e) => {
      const img = e.target;
      if (img.tagName !== "IMG") return;
      const holder = img.closest("[data-initial]");
      if (holder) img.replaceWith(document.createTextNode(holder.dataset.initial));
      else if (img.closest(".ic")) img.remove();
    }, true);
    document.addEventListener("load", (e) => { if (e.target.tagName === "IMG" && e.target.closest(".shot")) e.target.classList.add("loaded"); }, true);
    document.addEventListener("contextmenu", (e) => { if (!e.target.closest("input, textarea, .console, pre")) e.preventDefault(); });
  }

  async function boot() {
    try {
      state.settings = await WL.call("settings.get");
      lang = state.settings.language || "en";
      state.info = await WL.call("app.info");
      const logs = await WL.call("logs.recent").catch(() => ({ lines: [] }));
      state.logs = logs.lines || [];
      const g = await WL.call("game.status").catch(() => null);
      if (g) state.game = Object.assign(state.game, g);
    } catch (e) {
      state.settings = state.settings || { language: "en", animations: true };
      state.info = state.info || {};
    }
    FX.setEnabled(state.settings.animations !== false);
    document.documentElement.lang = lang;
    buildNav();
    bindEvents();
    await refreshAccounts();
    renderChips();
    go("home");
    requestAnimationFrame(() => {
      $("#app").classList.add("ready");
      $("#boot").classList.add("gone");
      setTimeout(() => $("#boot")?.remove(), 700);
      moveIndicator();
    });
    if (!state.settings.onboarding_done) setTimeout(onboarding, 650);
    WL.call("ui.ready").catch(() => {});
  }

  window.addEventListener("DOMContentLoaded", boot);
})();
