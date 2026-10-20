/* Inkwell PDF interface. Talks to Python through window.pywebview.api (desktop)
   or /api/<method> (browser dev mode). */
(() => {
  "use strict";

  // ------------------------------------------------------------- bridge
  const isDesktop = () => !window.INKWELL_DEV && !!(window.pywebview && window.pywebview.api);
  async function call(name, ...args) {
    if (isDesktop()) return window.pywebview.api[name](...args);
    const r = await fetch(`/api/${name}`, { method: "POST", body: JSON.stringify(args) });
    return r.json();
  }
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
  const MOD = isMac ? "⌘" : "Ctrl+";

  // ------------------------------------------------------------- helpers
  const $ = (s, el = document) => el.querySelector(s);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("fill", "none"); svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-linecap", "round"); svg.setAttribute("stroke-linejoin", "round"); svg.setAttribute("aria-hidden", "true");
    svg.classList.add("ic");
    svg.innerHTML = (window.ICONS || {})[name] || "";
    return svg;
  }
  function hydrateIcons(root = document) {
    root.querySelectorAll("i[data-icon]").forEach(i => i.replaceWith(icon(i.dataset.icon)));
    root.querySelectorAll("[data-mod]").forEach(k => { k.textContent = isMac ? "⌘" : (k.tagName === "KBD" ? "Ctrl" : "Ctrl "); });
  }
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  function toast(msg, kind = "ok", ms = 3200, action = null) {
    const t = el("div", `toast ${kind}`);
    t.append(icon(kind === "error" ? "alert-triangle" : kind === "warn" ? "alert-triangle" : "check"), el("span", null, msg));
    if (action) {
      const b = el("button", "toast-action", action.label);
      b.onclick = () => { action.run(); t.remove(); };
      t.append(b);
    }
    $("#toasts").append(t);
    setTimeout(() => t.remove(), ms);
  }

  // ------------------------------------------------------------- state
  const S = {
    doc: null,          // state from Python
    zoom: 1,
    fitMode: true,
    pageEls: [],        // { root, img, runsLayer, runs, imgZoom, version }
    version: {},        // page -> render version (bumped after edits)
    editing: null,      // { page, run, live, chip, cancelled, busy }
    loadedFonts: new Set(),
    find: { hits: [], idx: -1, case: false },
    current: 0,
  };
  const dpr = () => Math.min(window.devicePixelRatio || 1, 3);

  // ------------------------------------------------------------- open / welcome
  async function showWelcome(state) {
    $("#editor-ui").hidden = true;
    $("#welcome").hidden = false;
    const recent = (state && state.recent) || [];
    const list = $("#recent-list");
    list.replaceChildren();
    recent.slice(0, 6).forEach(r => {
      const b = el("button");
      b.append(icon("file-text"), el("span", null, r.name), el("span", "path", r.path));
      b.onclick = () => openPath(r.path);
      const li = el("li"); li.append(b); list.append(li);
    });
    $("#recent").hidden = recent.length === 0;
  }

  async function chooseFile() {
    if (isDesktop()) {
      const st = await call("open_dialog");
      if (!st.cancelled) opened(st);
    } else {
      $("#file-input").click();
    }
  }

  async function openPath(path) { opened(await call("open_path", path)); }

  async function openBlob(file) {
    const buf = await file.arrayBuffer();
    let bin = ""; const bytes = new Uint8Array(buf);
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    opened(await call("open_upload", file.name, btoa(bin)));
  }

  function opened(st) {
    if (!st) return;
    if (st.error) { toast(st.error, "error", 5000); return; }
    if (!st.open) return;
    S.doc = st; S.version = {}; S.find = { hits: [], idx: -1, case: false };
    $("#welcome").hidden = true;
    $("#editor-ui").hidden = false;
    buildPages();
    buildSidebar();
    S.fitMode = true;
    fitWidth();
    updateChrome();
    $("#canvas").scrollTop = 0;
  }

  // ------------------------------------------------------------- pages
  let io;
  function buildPages() {
    const host = $("#pages");
    host.replaceChildren();
    S.pageEls = S.doc.pages.map((p, i) => {
      const root = el("div", "page");
      root.dataset.page = i;
      const loading = el("div", "loading", `Page ${i + 1}`);
      const runsLayer = el("div", "runs");
      root.append(loading, runsLayer);
      host.append(root);
      return { root, img: null, loading, runsLayer, runs: null, imgZoom: 0, w: p.w, h: p.h };
    });
    if (io) io.disconnect();
    io = new IntersectionObserver(entries => {
      entries.forEach(e => { if (e.isIntersecting) loadPage(+e.target.dataset.page); });
    }, { root: $("#canvas"), rootMargin: "800px 0px" });
    S.pageEls.forEach(p => io.observe(p.root));
    layoutPages();
  }

  function layoutPages() {
    const z = S.zoom;
    S.pageEls.forEach(p => {
      p.root.style.width = `${p.w * z}px`;
      p.root.style.height = `${p.h * z}px`;
      if (p.runs) placeRuns(p);
    });
    drawHits();
  }

  async function loadPage(i, force = false) {
    const p = S.pageEls[i];
    if (!p) return;
    const want = Math.round(S.zoom * dpr() * 100) / 100;
    const ver = S.version[i] || 0;
    if (!force && p.imgZoom === want && p.ver === ver) return;
    p.imgZoom = want; p.ver = ver;
    const [src, runs] = await Promise.all([call("page_image", i, want), call("page_runs", i)]);
    if (p.imgZoom !== want || p.ver !== ver) return; // a newer request superseded this one
    const img = p.img || el("img");
    img.alt = ""; img.draggable = false;
    img.src = src;
    if (!p.img) { p.root.prepend(img); p.img = img; }
    p.loading.remove();
    p.runs = runs;
    placeRuns(p);
    drawHits();
  }

  function placeRuns(p) {
    const z = S.zoom;
    const layer = p.runsLayer;
    layer.replaceChildren();
    p.runs.forEach(r => {
      const d = el("div", "run" + (r.editable ? "" : " locked"));
      d.style.left = `${r.x0 * z - 2}px`;
      d.style.top = `${r.y0 * z - 1}px`;
      d.style.width = `${(r.x1 - r.x0) * z + 4}px`;
      d.style.height = `${(r.y1 - r.y0) * z + 2}px`;
      d.dataset.id = r.id;
      d.title = r.editable ? "" : (r.lockReason || "This text can't be edited");
      d.addEventListener("mousedown", ev => {
        if (!r.editable) return;
        ev.preventDefault();
        beginEdit(+p.root.dataset.page, r, { x: ev.clientX, y: ev.clientY });
      });
      layer.append(d);
    });
  }

  function isNear(elm, container, margin) {
    const c = container.getBoundingClientRect(), r = elm.getBoundingClientRect();
    return r.bottom > c.top - margin && r.top < c.bottom + margin;
  }
  // Re-draw changed pages that are on screen now; the rest re-draw when scrolled to.
  async function rerender(pages) {
    const canvas = $("#canvas"), sb = $("#sidebar");
    const jobs = [];
    for (const i of pages) {
      S.version[i] = (S.version[i] || 0) + 1;
      const p = S.pageEls[i];
      if (!p) continue;
      if (isNear(p.root, canvas, 800)) jobs.push(loadPage(i, true));
      if (p.thumb && !sb.classList.contains("collapsed") && isNear(p.thumb, sb, 400)) jobs.push(refreshThumb(i));
    }
    await Promise.all(jobs);
    markEditedThumbs();
  }

  // ------------------------------------------------------------- zoom
  function fitWidth() {
    const canvas = $("#canvas");
    const maxW = Math.max(...S.doc.pages.map(p => p.w));
    const z = Math.min(1.35, (canvas.clientWidth - 80) / maxW);
    setZoom(z, true);
  }
  function setZoom(z, fit = false) {
    if (!S.doc) return;
    const canvas = $("#canvas");
    const ratio = canvas.scrollTop / Math.max(1, canvas.scrollHeight);
    if (S.editing) commitEdit();
    S.fitMode = fit;
    S.zoom = Math.max(0.4, Math.min(4, z));
    layoutPages();
    canvas.scrollTop = ratio * canvas.scrollHeight;
    $("#btn-zoom-fit").textContent = `${Math.round(S.zoom * 100)}%`;
    reloadVisible();
  }
  // Load or refresh any page (and thumbnail) near the viewport; cheap when nothing changed.
  const reloadVisible = debounce(() => {
    if (!S.doc) return;
    const canvas = $("#canvas"), sb = $("#sidebar");
    S.pageEls.forEach((p, i) => {
      if (isNear(p.root, canvas, 800)) loadPage(i);
      if (p.thumb && !sb.classList.contains("collapsed") && isNear(p.thumb, sb, 400)) refreshThumb(i);
    });
  }, 120);

  // ------------------------------------------------------------- sidebar
  let thumbIO;
  function buildSidebar() {
    const sb = $("#sidebar");
    sb.replaceChildren();
    if (thumbIO) thumbIO.disconnect();
    thumbIO = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) refreshThumb(+e.target.dataset.page); }),
      { root: sb, rootMargin: "400px 0px" });
    S.doc.pages.forEach((p, i) => {
      const t = el("button", "thumb");
      t.dataset.page = i;
      const frame = el("div", "tframe");
      frame.style.aspectRatio = `${p.w} / ${p.h}`;
      t.append(frame, el("span", "tnum", String(i + 1)));
      t.onclick = () => scrollToPage(i);
      sb.append(t);
      thumbIO.observe(t);
      S.pageEls[i].thumb = t;
      S.pageEls[i].thumbVer = -1;
    });
  }
  async function refreshThumb(i) {
    const p = S.pageEls[i];
    if (!p || !p.thumb) return;
    const ver = S.version[i] || 0;
    if (p.thumbVer === ver) return;
    p.thumbVer = ver;
    const src = await call("page_image", i, 0.3 * dpr());
    const frame = p.thumb.querySelector(".tframe");
    let img = frame.querySelector("img");
    if (!img) { img = el("img"); img.alt = `Page ${i + 1}`; frame.append(img); }
    img.src = src;
    markEditedThumbs();
  }
  function markEditedThumbs() {
    const edited = new Set((S.doc && S.doc.editedPages) || []);
    S.pageEls.forEach((p, i) => {
      if (!p.thumb) return;
      const has = p.thumb.querySelector(".tdot");
      if (edited.has(i) && !has) p.thumb.append(el("span", "tdot"));
      if (!edited.has(i) && has) has.remove();
    });
  }
  function scrollToPage(i) {
    const p = S.pageEls[i];
    if (p) $("#canvas").scrollTo({ top: p.root.offsetTop - 24, behavior: "smooth" });
  }
  function trackCurrentPage() {
    const c = $("#canvas");
    const mid = c.scrollTop + c.clientHeight * 0.35;
    let cur = 0;
    S.pageEls.forEach((p, i) => { if (p.root.offsetTop <= mid) cur = i; });
    if (cur !== S.current || !document.querySelector(".thumb.current")) {
      S.current = cur;
      S.pageEls.forEach((p, i) => p.thumb && p.thumb.classList.toggle("current", i === cur));
      const t = S.pageEls[cur] && S.pageEls[cur].thumb;
      if (t) {
        const sb = $("#sidebar");
        if (t.offsetTop < sb.scrollTop || t.offsetTop + t.offsetHeight > sb.scrollTop + sb.clientHeight)
          sb.scrollTo({ top: t.offsetTop - 40 });
      }
    }
  }

  // ------------------------------------------------------------- chrome
  function updateChrome() {
    const d = S.doc;
    if (!d) return;
    $("#doc-name").textContent = d.name;
    $("#doc-status").textContent = d.edits === 0 ? "" : d.dirty
      ? `${d.edits} ${d.edits === 1 ? "edit" : "edits"} · not saved` : "Saved";
    $("#btn-undo").disabled = !d.canUndo;
    $("#btn-redo").disabled = !d.canRedo;
    $("#save-label").textContent = d.savePath && d.dirty ? "Save" : "Save copy";
    document.title = `${d.dirty ? "• " : ""}${d.name} — Inkwell PDF`;
    markEditedThumbs();
  }

  // ------------------------------------------------------------- live editing
  async function ensureFont(css) {
    if (!css || !css.alias || S.loadedFonts.has(css.alias)) return;
    const b64 = await call("font_data", css.alias);
    if (!b64) return;
    const bin = atob(b64); const buf = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
    try {
      const face = new FontFace(css.alias, buf.buffer);
      await face.load();
      document.fonts.add(face);
      S.loadedFonts.add(css.alias);
    } catch (e) { /* the browser couldn't read this font; the system font stands in while typing */ }
  }

  function applyFont(live, run, font) {
    const z = S.zoom;
    const css = font.css;
    live.style.fontFamily = `${css.family}, ${run.italic ? "serif" : "sans-serif"}`;
    live.style.fontWeight = css.alias ? "normal" : css.weight;
    live.style.fontStyle = css.alias ? "normal" : css.style;
    const fs = run.size * z;
    live.style.fontSize = `${fs}px`;
    // put the CSS baseline on the PDF baseline: with line-height 1 the baseline
    // sits (1 + ascender - |descender|) / 2 em below the line box top.
    const a = font.ascender || 0.9, d = Math.abs(font.descender || 0.25);
    const pad = 0.12 * fs;
    live.style.left = `${run.ox * z - 2}px`;
    live.style.top = `${run.oy * z - fs * (1 + a - d) / 2 - pad}px`;
    live.style.minWidth = `${(run.x1 - run.ox) * z + 4}px`;
  }

  function renderChip(chip, run, font) {
    chip.className = "chip" + (font.missing ? " missing" : (font.status === "fallback" || font.standIn) ? " fallback" : "");
    chip.replaceChildren();
    const label = font.missing ? `No font has “${font.missing}”`
      : font.standIn ? `${run.font} · “${font.standIn}” from another font`
      : font.status === "fallback" ? `Different font · ${font.label}` : run.font;
    chip.append(el("span", "dot"), el("span", "label", label), el("span", "bar"),
      el("span", "meta", `${+run.size.toFixed(1)} pt`));
    const sw = el("span", "swatch"); sw.style.background = run.color;
    chip.append(sw, el("span", "bar"), el("span", "meta", "Esc to cancel"));
  }

  // Keep the chip above the line and inside the page; flip below if there's no room above.
  function positionChip(chip, live) {
    const pageW = live.parentElement.clientWidth;
    const left = Math.min(parseFloat(live.style.left), pageW - chip.offsetWidth - 8);
    chip.style.left = `${Math.max(8, left)}px`;
    const top = parseFloat(live.style.top);
    chip.style.top = top - 36 >= 4 ? `${top - 36}px` : `${top + live.offsetHeight + 8}px`;
  }

  async function beginEdit(pageNo, run, point) {
    if (S.editing) {
      const ok = await commitEdit();
      if (!ok) return;
    }
    const p = S.pageEls[pageNo];
    const info = await call("begin_edit", pageNo, run.id);
    if (info.error) { toast(info.error, "warn"); return; }
    await ensureFont(info.font.css);

    const live = el("div", "live");
    live.contentEditable = "plaintext-only";
    if (live.contentEditable !== "plaintext-only") live.contentEditable = "true";
    live.spellcheck = false;
    live.textContent = run.text;
    live.style.color = run.color;
    live.style.background = info.bg;
    applyFont(live, run, info.font);
    const chip = el("div");
    renderChip(chip, run, info.font);
    p.root.append(live, chip);
    positionChip(chip, live);

    S.editing = { page: pageNo, run, live, chip, alias: info.font.css.alias, cancelled: false, busy: false, missing: "" };
    live.focus();
    placeCaret(live, point);

    const preview = debounce(async () => {
      const ed = S.editing;
      if (!ed || ed.live !== live) return;
      const font = await call("preview", pageNo, run.id, live.textContent);
      if (!S.editing || S.editing.live !== live || font.error) return;
      await ensureFont(font.css);
      applyFont(live, run, font);
      renderChip(chip, run, font);
      positionChip(chip, live);
      S.editing.missing = font.missing;
    }, 140);
    live.addEventListener("input", preview);
    live.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); commitEdit(); }
      else if (e.key === "Escape") { e.preventDefault(); cancelEdit(); }
      else if (e.key === "Tab") { e.preventDefault(); tabTo(pageNo, run, e.shiftKey ? -1 : 1); }
    });
    live.addEventListener("blur", () => setTimeout(() => {
      if (S.editing && S.editing.live === live && document.activeElement !== live) commitEdit();
    }, 0));
    live.addEventListener("paste", e => {
      e.preventDefault();
      const t = (e.clipboardData.getData("text/plain") || "").replace(/\s*\n\s*/g, " ");
      document.execCommand("insertText", false, t);
    });
  }

  function placeCaret(live, point) {
    const sel = window.getSelection();
    let range = null;
    if (point && document.caretRangeFromPoint) {
      range = document.caretRangeFromPoint(point.x, point.y);
      if (range && !live.contains(range.startContainer)) range = null;
    }
    if (!range) { range = document.createRange(); range.selectNodeContents(live); range.collapse(false); }
    sel.removeAllRanges(); sel.addRange(range);
  }

  function endEditUI() {
    const ed = S.editing;
    if (!ed) return;
    S.editing = null;
    ed.live.remove(); ed.chip.remove();
  }

  function cancelEdit() {
    if (!S.editing) return;
    S.editing.cancelled = true;
    endEditUI();
  }

  async function commitEdit() {
    const ed = S.editing;
    if (!ed) return true;
    if (ed.busy) return false;
    const text = ed.live.textContent.replace(/\n/g, " ");
    if (text === ed.run.text) { endEditUI(); return true; }
    ed.busy = true;
    const res = await call("commit", ed.page, ed.run.id, text);
    ed.busy = false;
    if (!res.ok) {
      toast(res.error || "That edit couldn't be made.", "error", 4500);
      if (S.editing === ed) ed.live.focus();
      return false;
    }
    S.doc = res.state;
    endEditUI();
    updateChrome();
    await rerender([ed.page]);
    (res.warnings || []).forEach(w => toast(w, "warn", 5000));
    if (S.find.hits.length) runFind();
    return true;
  }

  async function tabTo(pageNo, run, dir) {
    const ok = await commitEdit();
    if (!ok) return;
    let pg = pageNo, idx = run.id + dir;
    let runs = S.pageEls[pg].runs || [];
    // after an edit the run list is refreshed; find the run nearest the old position
    const near = runs.findIndex(r => Math.abs(r.oy - run.oy) < 0.5 && Math.abs(r.ox - run.ox) < 0.5);
    if (near >= 0) idx = near + dir;
    while (idx < 0 || idx >= runs.length) {
      pg += dir;
      if (pg < 0 || pg >= S.pageEls.length) return;
      await loadPage(pg);
      runs = S.pageEls[pg].runs || [];
      idx = dir > 0 ? 0 : runs.length - 1;
    }
    const next = runs[idx];
    if (next && next.editable) {
      S.pageEls[pg].root.scrollIntoView({ block: "nearest" });
      beginEdit(pg, next, null);
    }
  }

  // ------------------------------------------------------------- undo / save
  async function undo() {
    if (S.editing) { cancelEdit(); return; }
    const r = await call("undo");
    if (!r.ok) return;
    S.doc = r.state; updateChrome(); await rerender(r.pages);
  }
  async function redo() {
    if (S.editing) return;
    const r = await call("redo");
    if (!r.ok) return;
    S.doc = r.state; updateChrome(); await rerender(r.pages);
  }
  async function save(choose = false) {
    if (!S.doc) return;
    if (S.editing && !(await commitEdit())) return;
    const r = await call("save", choose);
    if (r.cancelled) return;
    if (r.error) { toast(r.error, "error", 5000); return; }
    S.doc = r.state; updateChrome();
    toast(`Saved as ${r.name}`, "ok", 6000, isDesktop() ? { label: "Show", run: () => call("reveal") } : null);
  }

  // ------------------------------------------------------------- find
  function openFind() {
    if (!S.doc) return;
    $("#findbar").hidden = false;
    const f = $("#find-input");
    const sel = window.getSelection().toString();
    if (sel && !S.editing) f.value = sel;
    f.focus(); f.select();
    if (f.value) runFind();
  }
  function closeFind() {
    $("#findbar").hidden = true;
    S.find.hits = []; S.find.idx = -1;
    drawHits();
  }
  const runFind = debounce(async () => {
    const q = $("#find-input").value;
    if (!q) { S.find.hits = []; S.find.idx = -1; $("#find-count").textContent = ""; drawHits(); return; }
    const r = await call("find", q, S.find.case);
    S.find.hits = r.hits; S.find.idx = r.hits.length ? 0 : -1;
    $("#find-count").textContent = r.total ? `${r.total} ${r.total === 1 ? "match" : "matches"}` : "No matches";
    drawHits();
    if (r.hits.length) goToHit(0);
  }, 150);
  function drawHits() {
    document.querySelectorAll(".hit").forEach(h => h.remove());
    const z = S.zoom;
    S.find.hits.forEach((h, i) => {
      const p = S.pageEls[h.page];
      if (!p) return;
      const d = el("div", "hit" + (i === S.find.idx ? " current" : ""));
      d.style.left = `${h.x0 * z - 1}px`; d.style.top = `${h.y0 * z}px`;
      d.style.width = `${(h.x1 - h.x0) * z + 2}px`; d.style.height = `${(h.y1 - h.y0) * z}px`;
      p.runsLayer.after(d);
    });
  }
  function goToHit(i) {
    const n = S.find.hits.length;
    if (!n) return;
    S.find.idx = (i + n) % n;
    drawHits();
    const h = S.find.hits[S.find.idx];
    const p = S.pageEls[h.page];
    const c = $("#canvas");
    c.scrollTo({ top: p.root.offsetTop + h.y0 * S.zoom - c.clientHeight / 3, behavior: "smooth" });
  }
  async function replaceAll() {
    const q = $("#find-input").value;
    if (!q) { $("#find-input").focus(); toast("Type the text to find first.", "warn"); return; }
    const r = await call("replace_all", q, $("#replace-input").value, S.find.case);
    if (!r.count) { toast("No matches to replace.", "warn"); return; }
    S.doc = r.state; updateChrome();
    await rerender(r.pages);
    toast(`Replaced ${r.count} ${r.count === 1 ? "match" : "matches"}`, "ok");
    r.warnings.slice(0, 3).forEach(w => toast(w, "warn", 5000));
    runFind();
  }

  // ------------------------------------------------------------- command palette
  function commands() {
    const open = !!S.doc;
    const list = [
      { name: "Open a PDF", icon: "file-upload", key: `${MOD}O`, run: chooseFile },
    ];
    if (open) list.push(
      { name: S.doc.savePath ? "Save" : "Save a copy", icon: "download", key: `${MOD}S`, run: () => save(false) },
      { name: "Save a copy as…", icon: "download", key: `⇧${MOD}S`, run: () => save(true) },
      { name: "Find and replace", icon: "search", key: `${MOD}F`, run: openFind },
      { name: "Undo", icon: "arrow-back-up", key: `${MOD}Z`, run: undo },
      { name: "Redo", icon: "arrow-forward-up", key: `⇧${MOD}Z`, run: redo },
      { name: "Zoom in", icon: "zoom-in", key: `${MOD}+`, run: () => setZoom(S.zoom * 1.2) },
      { name: "Zoom out", icon: "zoom-out", key: `${MOD}−`, run: () => setZoom(S.zoom / 1.2) },
      { name: "Fit to width", icon: "zoom-in", key: `${MOD}0`, run: fitWidth },
      { name: "Show or hide pages", icon: "layout-sidebar-left-collapse", key: `${MOD}\\`, run: toggleSidebar },
      { name: "Close this PDF", icon: "x", key: "", run: closeDoc },
    );
    return list;
  }
  let palSel = 0, palItems = [];
  function openPalette() {
    $("#palette").hidden = false;
    const inp = $("#palette-input");
    inp.value = ""; renderPalette(); inp.focus();
  }
  function closePalette() { $("#palette").hidden = true; }
  function renderPalette() {
    const q = $("#palette-input").value.trim().toLowerCase();
    palItems = commands().filter(c => !q || c.name.toLowerCase().includes(q));
    const num = parseInt(q, 10);
    if (S.doc && /^\d+$/.test(q) && num >= 1 && num <= S.doc.pages.length)
      palItems.unshift({ name: `Go to page ${num}`, icon: "file-text", key: "↵", run: () => scrollToPage(num - 1) });
    palSel = Math.min(palSel, Math.max(0, palItems.length - 1));
    const ul = $("#palette-list");
    ul.replaceChildren();
    if (!palItems.length) { ul.append(el("li", "empty", "No matching commands")); return; }
    palItems.forEach((c, i) => {
      const li = el("li", i === palSel ? "sel" : "");
      li.setAttribute("role", "option");
      li.append(icon(c.icon), el("span", null, c.name), el("span", "k", c.key));
      li.onmouseenter = () => { palSel = i; ul.querySelectorAll("li").forEach((x, j) => x.classList.toggle("sel", j === i)); };
      li.onclick = () => { closePalette(); c.run(); };
      ul.append(li);
    });
  }

  // ------------------------------------------------------------- misc actions
  function toggleSidebar() { $("#sidebar").classList.toggle("collapsed"); if (S.fitMode) setTimeout(fitWidth, 0); }
  async function closeDoc() {
    if (S.doc && S.doc.dirty) {
      const ok = isDesktop()
        ? await call("confirm", "Close without saving?", "Your edits haven't been saved to a copy yet.")
        : window.confirm("Close without saving? Your edits haven't been saved to a copy yet.");
      if (!ok) return;
    }
    showWelcome(await call("close"));
    S.doc = null;
  }

  // ------------------------------------------------------------- wiring
  function wire() {
    hydrateIcons();
    $("#dropzone").onclick = chooseFile;
    $("#file-input").onchange = e => { const f = e.target.files[0]; if (f) openBlob(f); e.target.value = ""; };
    $("#btn-sidebar").onclick = toggleSidebar;
    $("#btn-undo").onclick = undo;
    $("#btn-redo").onclick = redo;
    $("#btn-find").onclick = () => ($("#findbar").hidden ? openFind() : closeFind());
    $("#btn-zoom-in").onclick = () => setZoom(S.zoom * 1.2);
    $("#btn-zoom-out").onclick = () => setZoom(S.zoom / 1.2);
    $("#btn-zoom-fit").onclick = fitWidth;
    $("#btn-cmdk").onclick = openPalette;
    $("#btn-save").onclick = () => save(false);
    $("#find-input").addEventListener("input", runFind);
    $("#find-input").addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); goToHit(S.find.idx + (e.shiftKey ? -1 : 1)); }
      if (e.key === "Escape") closeFind();
    });
    $("#replace-input").addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); replaceAll(); }
      if (e.key === "Escape") closeFind();
    });
    $("#find-next").onclick = () => goToHit(S.find.idx + 1);
    $("#find-prev").onclick = () => goToHit(S.find.idx - 1);
    $("#find-close").onclick = closeFind;
    $("#replace-all").onclick = replaceAll;
    $("#find-case").onclick = e => {
      S.find.case = !S.find.case;
      e.currentTarget.setAttribute("aria-pressed", String(S.find.case));
      runFind();
    };
    $("#palette").addEventListener("mousedown", e => { if (e.target.id === "palette") closePalette(); });
    $("#palette-input").addEventListener("input", () => { palSel = 0; renderPalette(); });
    $("#palette-input").addEventListener("keydown", e => {
      if (e.key === "ArrowDown") { e.preventDefault(); palSel = Math.min(palSel + 1, palItems.length - 1); renderPalette(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); palSel = Math.max(palSel - 1, 0); renderPalette(); }
      else if (e.key === "Enter") { e.preventDefault(); const c = palItems[palSel]; if (c) { closePalette(); c.run(); } }
      else if (e.key === "Escape") { e.preventDefault(); closePalette(); }
    });

    const canvas = $("#canvas");
    canvas.addEventListener("scroll", () => { trackCurrentPage(); reloadVisible(); }, { passive: true });
    $("#sidebar").addEventListener("scroll", () => reloadVisible(), { passive: true });
    canvas.addEventListener("mousedown", e => {
      if (S.editing && !e.target.closest(".live")) commitEdit();
    });
    window.addEventListener("resize", debounce(() => { if (S.doc && S.fitMode) fitWidth(); }, 150));

    // drag and drop anywhere
    let dragDepth = 0;
    window.addEventListener("dragenter", e => { e.preventDefault(); dragDepth++; $("#drop-overlay").hidden = false; });
    window.addEventListener("dragleave", e => { e.preventDefault(); if (--dragDepth <= 0) { dragDepth = 0; $("#drop-overlay").hidden = true; } });
    window.addEventListener("dragover", e => e.preventDefault());
    window.addEventListener("drop", e => {
      e.preventDefault(); dragDepth = 0; $("#drop-overlay").hidden = true;
      if (isDesktop()) return; // the desktop shell opens dropped files itself
      const f = [...e.dataTransfer.files].find(f => /\.pdf$/i.test(f.name));
      if (f) openBlob(f); else toast("Drop a PDF file.", "warn");
    });

    // keyboard shortcuts
    window.addEventListener("keydown", e => {
      const mod = isMac ? e.metaKey : e.ctrlKey;
      const k = e.key.toLowerCase();
      const typing = S.editing || ["INPUT", "TEXTAREA"].includes(document.activeElement.tagName);
      if (mod && k === "k") { e.preventDefault(); $("#palette").hidden ? openPalette() : closePalette(); return; }
      if (mod && k === "o") { e.preventDefault(); chooseFile(); return; }
      if (!S.doc) return;
      if (mod && k === "s") { e.preventDefault(); save(e.shiftKey); return; }
      if (mod && k === "f") { e.preventDefault(); openFind(); return; }
      if (mod && (k === "=" || k === "+")) { e.preventDefault(); setZoom(S.zoom * 1.2); return; }
      if (mod && k === "-") { e.preventDefault(); setZoom(S.zoom / 1.2); return; }
      if (mod && k === "0") { e.preventDefault(); fitWidth(); return; }
      if (mod && k === "\\") { e.preventDefault(); toggleSidebar(); return; }
      if (typing) return;
      if (mod && k === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
      if (mod && k === "y") { e.preventDefault(); redo(); return; }
      if (e.key === "PageDown") scrollToPage(Math.min(S.current + 1, S.pageEls.length - 1));
      if (e.key === "PageUp") scrollToPage(Math.max(S.current - 1, 0));
    });
  }

  async function start() {
    wire();
    const st = await call("state");
    if (st.open) opened(st); else showWelcome(st);
  }

  window.Inkwell = { opened, state: S };
  // Desktop: wait for pywebview to inject its API. Browser dev mode: dev.js sets INKWELL_DEV.
  if (window.INKWELL_DEV || window.pywebview) start();
  else window.addEventListener("pywebviewready", start, { once: true });
})();
