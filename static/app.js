(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const SETTINGS_KEY = "scanner.settings";
  const SETTING_IDS = ["mode", "dpi", "color", "source", "host"];

  // ---- embedded mode (iframe in Flutter web / WebView2 in Flutter Windows) ----
  const params = new URLSearchParams(location.search);
  const EMBED = params.get("embed") === "1";
  const PARENT = params.get("parent") || "";
  let hostOrigin = null; // validated by the server before we ever post a message

  function sendToHost(msg) {
    const text = JSON.stringify(msg);
    if (window.chrome && window.chrome.webview) { // WebView2 (Flutter Windows)
      window.chrome.webview.postMessage(text);
      return true;
    }
    if (window.parent !== window && hostOrigin) { // iframe (Flutter web)
      window.parent.postMessage(text, hostOrigin);
      return true;
    }
    return false;
  }

  function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
  }

  const state = {
    pages: [],
    deselected: new Set(), // new pages are selected by default
    job: null,
    scanning: false,
    searching: false,
    pdf: null,
  };

  // ---------------------------------------------------------------- helpers
  async function api(method, url, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(url, opts);
    } catch (_) {
      throw new Error("انقطع الاتصال بالبرنامج. تأكد إنو نافذة البرنامج (الشاشة السودا) لسا شغّالة.");
    }
    let data = {};
    try { data = await res.json(); } catch (_) { /* not JSON */ }
    if (!res.ok) {
      const err = new Error(data.error || `صار خطأ (${res.status})`);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  let toastTimer = null;
  function toast(message, kind) {
    const el = $("#toast");
    el.className = "toast" + (kind === "ok" ? " ok" : "");
    el.replaceChildren(message);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 5000);
  }

  function formatSize(bytes) {
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function pagesWord(n) {
    if (n === 1) return "صفحة واحدة";
    if (n === 2) return "صفحتين";
    if (n >= 3 && n <= 10) return `${n} صفحات`;
    return `${n} صفحة`;
  }

  // --------------------------------------------------------------- settings
  async function loadServiceDefaults() {
    try {
      if (localStorage.getItem(SETTINGS_KEY)) return; // the user's own choice wins
    } catch (_) { /* storage unavailable */ }
    try {
      const { defaults } = await api("GET", "/api/config");
      for (const id of SETTING_IDS) {
        const el = $("#set-" + id);
        if (el && defaults[id] !== undefined) el.value = defaults[id];
      }
      $("#set-duplex").checked = !!defaults.duplex;
      syncDuplex();
    } catch (_) { /* defaults in the HTML are fine */ }
  }

  function loadSettings() {
    try {
      const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
      for (const id of SETTING_IDS) {
        const el = $("#set-" + id);
        if (el && saved[id] !== undefined) el.value = saved[id];
      }
      if (saved.duplex !== undefined) $("#set-duplex").checked = !!saved.duplex;
      savedDevice = saved.device || "";
    } catch (_) { /* storage unavailable: defaults are fine */ }
  }

  function readSettings() {
    return {
      mode: $("#set-mode").value,
      dpi: Number($("#set-dpi").value),
      color: $("#set-color").value,
      source: $("#set-source").value,
      host: $("#set-host").value.trim(),
      device: $("#set-device").value,
      duplex: $("#set-duplex").checked,
    };
  }

  function saveSettings() {
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(readSettings())); } catch (_) { /* ignore */ }
  }

  // ----------------------------------------------------------------- status
  function renderStatus() {
    const box = $("#status");
    const job = state.job;
    if (!job) { box.hidden = true; return; }
    box.hidden = false;
    box.dataset.stage = job.stage;
    const msg = $("#statusMsg");
    msg.textContent = job.error || job.message;
    box.querySelectorAll(".warn").forEach((n) => n.remove());
    if (job.warning) {
      const w = document.createElement("span");
      w.className = "warn";
      w.textContent = job.warning;
      msg.parentElement.after(w);
    }
    $("#statusBar").hidden = job.finished;
    if (job.finished && job.stage !== "error" && job.stage !== "cancelled") box.dataset.stage = "done";
  }

  let savedDevice = "";

  function updateScanButton() {
    $("#scanBtn").disabled = state.scanning || state.searching;
  }

  // The scan needs the feeder for duplex, so the paper-source choice follows the checkbox.
  function syncDuplex() {
    const on = $("#set-duplex").checked;
    const src = $("#set-source");
    if (on) src.value = "feeder";
    src.disabled = on;
  }

  function syncDevice() {
    $("#set-mode").disabled = !!$("#set-device").value; // a chosen scanner overrides "USB / network"
  }

  function renderDevices(preferred) {
    const sel = $("#set-device");
    sel.replaceChildren(new Option("تلقائي (USB ثم الشبكة)", ""));
    for (const [kind, label] of [["usb", "USB"], ["network", "الشبكة"]]) {
      const group = state.devices.filter((d) => d.kind === kind);
      if (!group.length) continue;
      const og = document.createElement("optgroup");
      og.label = label;
      for (const d of group) og.append(new Option(`${d.name} (${d.backend.toUpperCase()})`, d.id));
      sel.append(og);
    }
    const ids = state.devices.map((d) => d.id);
    sel.value = ids.includes(preferred) ? preferred : (ids[0] || "");
    syncDevice();
  }

  async function loadDevices() {
    if (state.scanning || state.searching) return;
    state.searching = true;
    updateScanButton();
    const info = $("#deviceInfo");
    const refresh = $("#refreshDevices");
    refresh.disabled = true;
    info.textContent = "عم دوّر على السكانرات (USB والشبكة)… ممكن ياخد كم ثانية.";
    try {
      const host = encodeURIComponent($("#set-host").value.trim());
      const data = await api("GET", `/api/scanners?host=${host}`);
      state.devices = data.devices;
      renderDevices($("#set-device").value || savedDevice);
      saveSettings();
      info.textContent = data.devices.length
        ? `لقيت ${data.devices.length === 1 ? "سكانر واحد" : data.devices.length + " سكانرات"}. اختار السكانر اللي بدك تسكان منو.`
        : "ما لقيت أي سكانر. تأكد إنو شغّال وموصول، أو اكتب عنوان الـ IP بـ «إعدادات السكان» ثم اضغط «تحديث القائمة».";
    } catch (err) {
      info.textContent = err.message;
    } finally {
      state.searching = false;
      refresh.disabled = false;
      updateScanButton();
    }
  }

  function setScanning(on) {
    state.scanning = on;
    updateScanButton();
    $("#cancelBtn").hidden = !on;
    $("#clearBtn").disabled = on;
    renderPages();
  }

  // ------------------------------------------------------------------ icons
  const ICONS = {
    trash: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v6M14 11v6"/></svg>',
    expand: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
    grip: '<svg viewBox="0 0 24 10" width="22" height="10" fill="currentColor" aria-hidden="true"><circle cx="5" cy="2" r="1.6"/><circle cx="12" cy="2" r="1.6"/><circle cx="19" cy="2" r="1.6"/><circle cx="5" cy="8" r="1.6"/><circle cx="12" cy="8" r="1.6"/><circle cx="19" cy="8" r="1.6"/></svg>',
    close: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M5 5l14 14M19 5L5 19"/></svg>',
    plus: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
    minus: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14"/></svg>',
    fit: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="5" width="16" height="14" rx="2"/><path d="M9 12h6"/></svg>',
    next: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>',
    prev: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg>',
  };

  function iconButton(className, label, icon, onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = className;
    b.title = label;
    b.setAttribute("aria-label", label);
    b.innerHTML = icon; // static, trusted SVG strings above
    b.addEventListener("click", (e) => { e.stopPropagation(); onClick(e); });
    return b;
  }

  // ------------------------------------------------------------------ pages
  async function loadPages() {
    const data = await api("GET", "/api/pages");
    state.pages = data.pages;
    const ids = new Set(state.pages.map((p) => p.id));
    for (const id of [...state.deselected]) if (!ids.has(id)) state.deselected.delete(id);
    renderPages();
  }

  const isSelected = (id) => !state.deselected.has(id);
  const selectedIds = () => state.pages.filter((p) => isSelected(p.id)).map((p) => p.id);

  function renderPages() {
    if (drag && drag.active) return; // never rebuild the grid under a dragged card
    const grid = $("#grid");
    grid.replaceChildren();

    state.pages.forEach((page, index) => {
      const card = document.createElement("div");
      card.className = "page" + (isSelected(page.id) ? " sel" : "");
      card.dataset.id = page.id;
      card.tabIndex = 0;
      card.setAttribute("aria-label", `صفحة ${index + 1} — Enter للتكبير، Delete للحذف، Alt+سهم لتغيير الترتيب`);

      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "check";
      check.checked = isSelected(page.id);
      check.setAttribute("aria-label", `تحديد الصفحة ${index + 1}`);
      check.addEventListener("change", () => {
        if (check.checked) state.deselected.delete(page.id); else state.deselected.add(page.id);
        renderPages();
      });

      const wrap = document.createElement("div");
      wrap.className = "thumb-wrap";
      const img = document.createElement("img");
      img.className = "thumb";
      img.alt = `الصفحة ${index + 1}`;
      img.loading = "lazy";
      img.draggable = false;
      img.src = `/api/pages/${page.id}/thumb?v=${page.rev}`;
      img.addEventListener("click", () => { if (!suppressClick) openViewer(index); });

      const handle = document.createElement("span");
      handle.className = "drag-handle";
      handle.title = "اسحب لتغيير الترتيب";
      handle.innerHTML = ICONS.grip;

      wrap.append(
        img,
        handle,
        iconButton("pbtn pbtn-del", `حذف الصفحة ${index + 1}`, ICONS.trash, () => trashPages([page.id])),
        iconButton("pbtn pbtn-full", `تكبير الصفحة ${index + 1}`, ICONS.expand, () => openViewer(index)),
      );

      const num = document.createElement("span");
      num.className = "num";
      num.textContent = `صفحة ${index + 1}`;

      card.append(check, wrap, num);
      grid.append(card);
    });

    // placeholder while the scanner is reading a page
    if (state.scanning && state.job && state.job.stage === "scanning" && !state.job.finished) {
      const ph = document.createElement("div");
      ph.className = "page loading";
      const box = document.createElement("div");
      const spin = document.createElement("div");
      spin.className = "spinner";
      const txt = document.createElement("div");
      txt.textContent = state.job.message;
      box.append(spin, txt);
      ph.append(box);
      grid.append(ph);
    }

    const total = state.pages.length;
    const chosen = selectedIds().length;
    $("#pageCount").textContent = String(total);
    $("#empty").hidden = total > 0 || state.scanning;
    $("#toolbar").hidden = total === 0;
    $("#selInfo").textContent = `المحدد: ${chosen} من ${total}`;
    $("#pdfBtn").disabled = chosen === 0 || state.scanning;
    $("#embedSave").disabled = chosen === 0 || state.scanning;
    $("#rotBtn").disabled = $("#delBtn").disabled = chosen === 0 || state.scanning;
    grid.classList.toggle("no-drag", state.scanning);
  }

  // ------------------------------------------------ delete with Undo (تراجع)
  let undoTimer = null;

  function hideUndo() {
    clearTimeout(undoTimer);
    $("#undoBar").hidden = true;
  }

  function showUndo(ids, label) {
    $("#undoText").textContent = label;
    const btn = $("#undoBtn");
    btn.onclick = async () => {
      hideUndo();
      try {
        const data = await api("POST", "/api/pages/restore", { ids });
        state.pages = data.pages;
        renderPages();
      } catch (err) { toast(err.message); }
    };
    $("#undoBar").hidden = false;
    clearTimeout(undoTimer);
    undoTimer = setTimeout(hideUndo, 6000);
  }

  async function trashPages(ids) {
    if (!ids.length) return;
    const numbers = ids.map((id) => state.pages.findIndex((p) => p.id === id) + 1).filter((n) => n > 0);
    try {
      const data = await api("POST", "/api/pages/trash", { ids });
      state.pages = data.pages;
      ids.forEach((id) => state.deselected.delete(id));
      renderPages();
      showUndo(ids, ids.length === 1 ? `تم حذف الصفحة ${numbers[0]}` : `تم حذف ${pagesWord(ids.length)}`);
    } catch (err) { toast(err.message); }
  }

  // ------------------------------------------------- drag to reorder pages
  // Pointer events: one code path for mouse, pen and touch. Mouse/pen drag from anywhere on the
  // card; touch drags from the grip handle only, so the page can still be scrolled with a finger.
  let drag = null;
  let suppressClick = false;
  let dragScroll = null;

  async function persistOrder() {
    try {
      const data = await api("POST", "/api/pages/reorder", { ids: state.pages.map((p) => p.id) });
      state.pages = data.pages;
    } catch (err) {
      toast(err.message);
      try { await loadPages(); } catch (_) { /* ignore */ }
    }
  }

  function endDrag(commit) {
    if (!drag) return;
    window.removeEventListener("pointermove", onDragMove);
    window.removeEventListener("pointerup", onDragUp);
    window.removeEventListener("pointercancel", onDragCancel);
    clearInterval(dragScroll);
    const d = drag;
    drag = null;
    if (d.ghost) d.ghost.remove();
    document.body.classList.remove("is-dragging");
    d.card.classList.remove("dragging");
    if (d.active) {
      suppressClick = true;
      setTimeout(() => { suppressClick = false; }, 80);
      if (commit) {
        const byId = new Map(state.pages.map((p) => [p.id, p]));
        const order = [...$("#grid").querySelectorAll(".page[data-id]")].map((el) => byId.get(el.dataset.id)).filter(Boolean);
        const changed = order.some((p, i) => p !== state.pages[i]);
        state.pages = order;
        renderPages();
        if (changed) persistOrder();
      } else {
        renderPages();
      }
    }
  }

  function onDragMove(e) {
    if (!drag || e.pointerId !== drag.pointerId) return;
    drag.x = e.clientX;
    drag.y = e.clientY;
    if (!drag.active) {
      if (Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) < 6) return;
      drag.active = true;
      const rect = drag.card.getBoundingClientRect();
      drag.offX = drag.startX - rect.left;
      drag.offY = drag.startY - rect.top;
      const ghost = drag.card.cloneNode(true);
      ghost.classList.add("ghost");
      ghost.removeAttribute("tabindex");
      ghost.style.width = rect.width + "px";
      ghost.style.height = rect.height + "px";
      document.body.append(ghost);
      drag.ghost = ghost;
      drag.card.classList.add("dragging");
      document.body.classList.add("is-dragging");
      dragScroll = setInterval(() => { // auto-scroll near the top / bottom edge
        if (!drag) return;
        const edge = 70;
        if (drag.y < edge) window.scrollBy(0, -14);
        else if (drag.y > window.innerHeight - edge) window.scrollBy(0, 14);
      }, 16);
    }
    e.preventDefault();
    drag.ghost.style.left = e.clientX - drag.offX + "px";
    drag.ghost.style.top = e.clientY - drag.offY + "px";

    const over = document.elementFromPoint(e.clientX, e.clientY);
    const target = over && over.closest(".page[data-id]");
    if (target && target !== drag.card) {
      const cards = [...$("#grid").children];
      // Direction-agnostic (works in RTL): moving "down the list" puts the card after the target.
      if (cards.indexOf(drag.card) < cards.indexOf(target)) target.after(drag.card);
      else target.before(drag.card);
    }
  }

  const onDragUp = (e) => { if (drag && e.pointerId === drag.pointerId) endDrag(true); };
  const onDragCancel = (e) => { if (drag && e.pointerId === drag.pointerId) endDrag(false); };

  function onGridPointerDown(e) {
    if (state.scanning || e.button !== 0 || drag) return;
    const card = e.target.closest(".page[data-id]");
    if (!card || e.target.closest("button, input")) return;
    const onHandle = !!e.target.closest(".drag-handle");
    if (e.pointerType !== "mouse" && !onHandle) return; // touch / pen: handle only
    drag = { card, pointerId: e.pointerId, startX: e.clientX, startY: e.clientY, x: e.clientX, y: e.clientY, active: false, ghost: null };
    window.addEventListener("pointermove", onDragMove, { passive: false });
    window.addEventListener("pointerup", onDragUp);
    window.addEventListener("pointercancel", onDragCancel);
  }

  // keyboard: Alt+arrow moves the focused page, Enter opens it, Delete removes it
  function onGridKeyDown(e) {
    const card = e.target.closest && e.target.closest(".page[data-id]");
    if (!card || e.target !== card) return;
    const index = state.pages.findIndex((p) => p.id === card.dataset.id);
    if (index < 0) return;
    if (e.key === "Enter") { e.preventDefault(); openViewer(index); return; }
    if (e.key === "Delete") { e.preventDefault(); trashPages([card.dataset.id]); return; }
    if (e.altKey && (e.key === "ArrowLeft" || e.key === "ArrowRight") && !state.scanning) {
      e.preventDefault();
      const rtl = getComputedStyle($("#grid")).direction === "rtl";
      const earlier = rtl ? e.key === "ArrowRight" : e.key === "ArrowLeft";
      const to = index + (earlier ? -1 : 1);
      if (to < 0 || to >= state.pages.length) return;
      const [moved] = state.pages.splice(index, 1);
      state.pages.splice(to, 0, moved);
      renderPages();
      const again = $("#grid").querySelector(`.page[data-id="${moved.id}"]`);
      if (again) again.focus();
      persistOrder();
    }
  }

  // ------------------------------------------------ full-size viewer (zoom)
  const V = { open: false, index: 0, scale: 1, fit: 1, x: 0, y: 0, nw: 1, nh: 1, pointers: new Map(), lastDist: 0, token: 0, opener: null };

  function stageSize() {
    const r = $("#vStage").getBoundingClientRect();
    return { w: r.width, h: r.height };
  }

  function clampPan() {
    const { w, h } = stageSize();
    const iw = V.nw * V.scale;
    const ih = V.nh * V.scale;
    V.x = iw <= w ? (w - iw) / 2 : Math.min(0, Math.max(w - iw, V.x));
    V.y = ih <= h ? (h - ih) / 2 : Math.min(0, Math.max(h - ih, V.y));
  }

  function applyView() {
    clampPan();
    $("#vImg").style.transform = `translate(${V.x}px, ${V.y}px) scale(${V.scale})`;
    $("#vZoom").textContent = Math.round((V.scale / V.fit) * 100) + "%";
  }

  function computeFit() {
    const { w, h } = stageSize();
    V.fit = Math.min(w / V.nw, h / V.nh) || 1;
  }

  function fitView() {
    computeFit();
    V.scale = V.fit;
    V.x = V.y = 0;
    applyView();
  }

  function zoomAt(cx, cy, factor) {
    const next = Math.min(V.fit * 8, Math.max(V.fit, V.scale * factor));
    const ratio = next / V.scale;
    V.x = cx - (cx - V.x) * ratio;
    V.y = cy - (cy - V.y) * ratio;
    V.scale = next;
    applyView();
  }

  function zoomCenter(factor) {
    const { w, h } = stageSize();
    zoomAt(w / 2, h / 2, factor);
  }

  function showViewerPage(index) {
    const page = state.pages[index];
    if (!page) return;
    V.index = index;
    V.nw = page.width || 1;
    V.nh = page.height || 1;
    const img = $("#vImg");
    img.style.width = V.nw + "px";
    img.style.height = V.nh + "px";
    const token = ++V.token;
    img.src = `/api/pages/${page.id}/thumb?v=${page.rev}`; // instant preview, sharp image follows
    const full = new Image();
    full.onload = () => { if (token === V.token) img.src = full.src; };
    full.src = `/api/pages/${page.id}/image?v=${page.rev}`;
    $("#vTitle").textContent = `صفحة ${index + 1} من ${state.pages.length}`;
    $("#vPrev").disabled = index === 0;
    $("#vNext").disabled = index === state.pages.length - 1;
    fitView();
  }

  function openViewer(index) {
    if (!state.pages[index]) return;
    V.opener = document.activeElement;
    V.open = true;
    $("#viewer").hidden = false;
    document.body.classList.add("modal-open");
    showViewerPage(index);
    $("#vClose").focus();
  }

  function closeViewer() {
    if (!V.open) return;
    V.open = false;
    V.token++;
    $("#viewer").hidden = true;
    $("#vImg").removeAttribute("src");
    document.body.classList.remove("modal-open");
    if (V.opener && V.opener.focus) V.opener.focus();
  }

  // In this right-to-left page, the "next" page is the one on the left.
  const viewerNext = () => showViewerPage(Math.min(state.pages.length - 1, V.index + 1));
  const viewerPrev = () => showViewerPage(Math.max(0, V.index - 1));

  function initViewer() {
    $("#vClose").innerHTML = ICONS.close;
    $("#vZoomIn").innerHTML = ICONS.plus;
    $("#vZoomOut").innerHTML = ICONS.minus;
    $("#vFit").innerHTML = ICONS.fit;
    $("#vNext").innerHTML = ICONS.next;
    $("#vPrev").innerHTML = ICONS.prev;
    $("#vClose").addEventListener("click", closeViewer);
    $("#vZoomIn").addEventListener("click", () => zoomCenter(1.4));
    $("#vZoomOut").addEventListener("click", () => zoomCenter(1 / 1.4));
    $("#vFit").addEventListener("click", fitView);
    $("#vNext").addEventListener("click", viewerNext);
    $("#vPrev").addEventListener("click", viewerPrev);

    const stage = $("#vStage");
    stage.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = stage.getBoundingClientRect();
      zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)));
    }, { passive: false });

    stage.addEventListener("pointerdown", (e) => {
      stage.setPointerCapture(e.pointerId);
      V.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (V.pointers.size === 2) {
        const [a, b] = [...V.pointers.values()];
        V.lastDist = Math.hypot(a.x - b.x, a.y - b.y);
      }
      stage.classList.add("grabbing");
    });
    stage.addEventListener("pointermove", (e) => {
      const p = V.pointers.get(e.pointerId);
      if (!p) return;
      const dx = e.clientX - p.x;
      const dy = e.clientY - p.y;
      p.x = e.clientX;
      p.y = e.clientY;
      if (V.pointers.size === 1) { // hold and move = pan
        V.x += dx;
        V.y += dy;
        applyView();
      } else if (V.pointers.size === 2) { // two fingers = pinch zoom
        const [a, b] = [...V.pointers.values()];
        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        const r = stage.getBoundingClientRect();
        if (V.lastDist) zoomAt((a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top, dist / V.lastDist);
        V.lastDist = dist;
      }
    });
    const release = (e) => {
      V.pointers.delete(e.pointerId);
      V.lastDist = 0;
      if (!V.pointers.size) stage.classList.remove("grabbing");
    };
    stage.addEventListener("pointerup", release);
    stage.addEventListener("pointercancel", release);

    stage.addEventListener("dblclick", (e) => {
      const r = stage.getBoundingClientRect();
      if (V.scale > V.fit * 1.05) fitView(); else zoomAt(e.clientX - r.left, e.clientY - r.top, 2.5);
    });

    window.addEventListener("resize", () => {
      if (!V.open) return;
      const wasFit = Math.abs(V.scale - V.fit) < 1e-6;
      computeFit();
      if (wasFit) V.scale = V.fit;
      applyView();
    });
  }

  // ----------------------------------------------- file name dialog (rename)
  const ILLEGAL = /[\\/:*?"<>|\u0000-\u001f\u007f]/g; // what Windows does not allow in a file name

  function cleanName(value) {
    let v = String(value || "").normalize("NFC").replace(ILLEGAL, "").replace(/\s+/g, " ").trim();
    v = v.replace(/\.pdf$/i, "").replace(/^[ .]+|[ .]+$/g, "").slice(0, 100).replace(/[ .]+$/g, "");
    return v;
  }

  function defaultName() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `scan_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}`;
  }

  function askName(title, okLabel) {
    return new Promise((resolve) => {
      const dlg = $("#nameDialog");
      const input = $("#nameInput");
      const hint = $("#nameHint");
      const opener = document.activeElement;
      $("#nameTitle").textContent = title;
      $("#nameOk").textContent = okLabel;
      input.value = state.lastName || defaultName();
      hint.textContent = "";
      dlg.hidden = false;
      input.focus();
      input.select();

      const done = (value) => {
        dlg.hidden = true;
        form.removeEventListener("submit", onSubmit);
        input.removeEventListener("input", onInput);
        $("#nameCancel").removeEventListener("click", onCancel);
        dlg.removeEventListener("pointerdown", onBackdrop);
        dlg.removeEventListener("keydown", onKey);
        if (opener && opener.focus) opener.focus();
        resolve(value);
      };
      const form = $("#nameForm");
      const onSubmit = (e) => {
        e.preventDefault();
        const name = cleanName(input.value);
        if (!name) { hint.textContent = "اكتب اسم للملف."; input.focus(); return; }
        state.lastName = name;
        done(name);
      };
      const onInput = () => {
        hint.textContent = ILLEGAL.test(input.value) ? "الرموز \\ / : * ? \" < > | ما بتنفع بأسماء الملفات، رح تنشال." : "";
        ILLEGAL.lastIndex = 0;
      };
      const onCancel = () => done(null);
      const onBackdrop = (e) => { if (e.target === dlg) done(null); };
      const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); done(null); } };
      form.addEventListener("submit", onSubmit);
      input.addEventListener("input", onInput);
      $("#nameCancel").addEventListener("click", onCancel);
      dlg.addEventListener("pointerdown", onBackdrop);
      dlg.addEventListener("keydown", onKey);
    });
  }

  // --------------------------------------------------------------- scanning
  async function follow(jobId) {
    let seen = -1;
    while (true) {
      const job = await api("GET", `/api/jobs/${jobId}`);
      state.job = job;
      if (job.page_count !== seen) {
        seen = job.page_count;
        await loadPages();
      }
      renderStatus();
      renderPages();
      if (job.finished) return;
      await sleep(500);
    }
  }

  async function startScan() {
    saveSettings();
    state.pdf = null;
    $("#pdfResult").hidden = true;
    state.job = { stage: "searching", message: "عم جهّز…", finished: false };
    renderStatus();
    setScanning(true);
    try {
      let jobId;
      try {
        ({ job_id: jobId } = await api("POST", "/api/scan", readSettings()));
      } catch (err) {
        if (err.status === 409 && err.data && err.data.job_id) jobId = err.data.job_id;
        else throw err;
      }
      state.currentJob = jobId;
      await follow(jobId);
    } catch (err) {
      state.job = { stage: "error", message: err.message, error: err.message, finished: true };
      renderStatus();
    } finally {
      state.currentJob = null;
      setScanning(false);
      try { await loadPages(); } catch (_) { /* server gone, status already shows it */ }
    }
  }

  async function cancelScan() {
    if (!state.currentJob) return;
    $("#cancelBtn").disabled = true;
    try { await api("POST", `/api/jobs/${state.currentJob}/cancel`); } catch (err) { toast(err.message); }
    await sleep(800);
    $("#cancelBtn").disabled = false;
  }

  // ------------------------------------------------------------ page actions
  async function rotateSelected() {
    const ids = selectedIds();
    if (!ids.length) return;
    try {
      const data = await api("POST", "/api/pages/rotate", { ids, degrees: 90 });
      state.pages = data.pages;
      renderPages();
    } catch (err) { toast(err.message); }
  }

  async function deleteSelected() {
    await trashPages(selectedIds());
  }

  async function clearAll() {
    if (!state.pages.length) return;
    if (!confirm("تمسح كل الصفحات الممسوحة؟")) return;
    try {
      await api("POST", "/api/pages/clear");
      state.deselected.clear();
      state.pdf = null;
      $("#pdfResult").hidden = true;
      await loadPages();
    } catch (err) { toast(err.message); }
  }

  // -------------------------------------------------------------------- PDF
  async function buildPdf(ids, name) {
    return api("POST", "/api/pdf", { ids, name });
  }

  function showPath(prefix, path) {
    const el = document.createElement("span");
    el.append(prefix + " ");
    const bdi = document.createElement("bdi");
    bdi.dir = "ltr";
    bdi.textContent = path;
    el.append(bdi);
    return el;
  }

  async function saveToHost() {
    const ids = selectedIds();
    if (!ids.length) return;
    const name = await askName("اسم الملف قبل الإرفاق", "حفظ وإرفاق");
    if (!name) return;
    const btn = $("#pdfBtn");
    btn.disabled = true;
    btn.textContent = "عم يجهّز الملف…";
    try {
      const pdf = await buildPdf(ids, name);
      const res = await fetch(pdf.download_url);
      if (!res.ok) throw new Error("ما قدرت أقرأ ملف الـ PDF.");
      const base64 = await blobToBase64(await res.blob());
      const sent = sendToHost({ type: "homsscan:pdf", name: pdf.display_name, pages: pdf.pages, size: pdf.size, base64 });
      if (!sent) throw new Error("ما قدرت أرسل الملف للتطبيق. تأكد إنو عنوان موقعك مضاف بإعدادات الخدمة (الأيقونة جنب الساعة ← الإعدادات).");
      btn.textContent = "تم الإرسال ✓";
    } catch (err) {
      toast(err.message);
      btn.textContent = "حفظ وإرفاق";
      renderPages();
    }
  }

  // "حفظ PDF على الجهاز": saves to the Desktop and STAYS on the page (the user may still attach it).
  async function saveEmbedToDesktop() {
    const ids = selectedIds();
    if (!ids.length) return;
    const name = await askName("حفظ PDF على سطح المكتب", "حفظ على الجهاز");
    if (!name) return;
    const btn = $("#embedSave");
    btn.disabled = true;
    try {
      const pdf = await buildPdf(ids, name);
      const res = await api("POST", `/api/pdf/${encodeURIComponent(pdf.file)}/desktop`, { name });
      toast(showPath("انحفظ على سطح المكتب:", res.path), "ok");
    } catch (err) {
      toast(err.message);
    } finally {
      renderPages();
    }
  }

  async function convertToPdf() {
    if (EMBED) return saveToHost();
    const ids = selectedIds();
    if (!ids.length) return;
    const name = await askName("اسم ملف الـ PDF", "تحويل");
    if (!name) return;
    const btn = $("#pdfBtn");
    btn.disabled = true;
    btn.textContent = "عم يحوّل…";
    try {
      const pdf = await buildPdf(ids, name);
      state.pdf = pdf;
      const info = $("#pdfInfo");
      info.replaceChildren();
      [pdf.display_name, pagesWord(pdf.pages), formatSize(pdf.size)].forEach((text, i) => {
        if (i) info.append(" — ");
        const part = document.createElement("bdi"); // keeps the English file name / size in order
        part.textContent = text;
        info.append(part);
      });
      const link = $("#pdfDownload");
      link.href = pdf.download_url + "?name=" + encodeURIComponent(pdf.display_name);
      link.download = pdf.display_name;
      $("#pdfSaved").hidden = true;
      $("#pdfResult").hidden = false;
      $("#pdfResult").scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (err) {
      toast(err.message);
    } finally {
      btn.textContent = "تحويل إلى PDF";
      renderPages();
    }
  }

  async function saveToDesktop() {
    if (!state.pdf) return;
    const btn = $("#pdfDesktop");
    btn.disabled = true;
    try {
      const res = await api("POST", `/api/pdf/${encodeURIComponent(state.pdf.file)}/desktop`, { name: state.pdf.display_name });
      const saved = $("#pdfSaved");
      saved.replaceChildren(showPath("انحفظ على سطح المكتب:", res.path));
      saved.hidden = false;
    } catch (err) {
      toast(err.message);
    } finally {
      btn.disabled = false;
    }
  }

  // ------------------------------------------------------------ diagnostics
  async function checkDevices() {
    const out = $("#diag");
    const btn = $("#diagBtn");
    btn.disabled = true;
    out.hidden = false;
    out.textContent = "عم دوّر… (ممكن ياخد كم ثانية)";
    try {
      const host = encodeURIComponent($("#set-host").value.trim());
      const data = await api("GET", `/api/devices?host=${host}`);
      const lines = [];
      lines.push(data.devices.length ? "الأجهزة اللي انلقت:" : "ما انلقى أي سكانر.");
      for (const d of data.devices) lines.push(`  • ${d.name}  [${d.kind === "usb" ? "USB" : "شبكة"} / ${d.backend}]`);
      lines.push("", "طرق الاتصال:");
      for (const b of data.backends) {
        const note = b.reason ? " — " + b.reason : "";
        lines.push(`  • ${b.name}: ${b.available ? "جاهز" : "غير متوفر"}${note}`);
      }
      if (data.errors.length) lines.push("", "ملاحظات:", ...data.errors.map((e) => "  • " + e));
      out.textContent = lines.join("\n");
    } catch (err) {
      out.textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  }

  // ------------------------------------------------------------------- init
  async function initEmbed() {
    document.body.classList.add("embed");
    $("#pdfBtn").textContent = "حفظ وإرفاق";
    const cancel = $("#embedCancel");
    cancel.hidden = false;
    cancel.addEventListener("click", () => sendToHost({ type: "homsscan:cancel" }));
    const save = $("#embedSave");
    save.hidden = false;
    save.addEventListener("click", saveEmbedToDesktop);
    if (PARENT) {
      try {
        const r = await api("GET", "/api/embed/check?origin=" + encodeURIComponent(PARENT));
        hostOrigin = r.allowed ? PARENT : null;
      } catch (_) { hostOrigin = null; }
    }
    let version = "";
    try { version = (await api("GET", "/api/health")).version; } catch (_) { /* ignore */ }
    sendToHost({ type: "homsscan:ready", version });
  }

  function init() {
    loadSettings();
    syncDuplex();
    loadServiceDefaults();
    if (EMBED) initEmbed();
    $("#refreshDevices").addEventListener("click", loadDevices);
    $("#set-device").addEventListener("change", () => { syncDevice(); saveSettings(); });
    $("#set-duplex").addEventListener("change", () => { syncDuplex(); saveSettings(); });
    loadDevices();
    $("#scanBtn").addEventListener("click", startScan);
    $("#cancelBtn").addEventListener("click", cancelScan);
    $("#selAll").addEventListener("click", () => { state.deselected.clear(); renderPages(); });
    $("#selNone").addEventListener("click", () => {
      state.pages.forEach((p) => state.deselected.add(p.id));
      renderPages();
    });
    $("#rotBtn").addEventListener("click", rotateSelected);
    $("#delBtn").addEventListener("click", deleteSelected);
    $("#clearBtn").addEventListener("click", clearAll);
    $("#pdfBtn").addEventListener("click", convertToPdf);
    $("#pdfDesktop").addEventListener("click", saveToDesktop);
    $("#diagBtn").addEventListener("click", checkDevices);
    initViewer();
    const grid = $("#grid");
    grid.addEventListener("pointerdown", onGridPointerDown);
    grid.addEventListener("keydown", onGridKeyDown);
    document.addEventListener("keydown", (e) => {
      if (!$("#nameDialog").hidden) return; // the dialog handles its own keys
      if (e.key === "Escape") {
        if (drag && drag.active) endDrag(false);
        else if (V.open) closeViewer();
      } else if (V.open) {
        if (e.key === "+" || e.key === "=") zoomCenter(1.4);
        else if (e.key === "-") zoomCenter(1 / 1.4);
        else if (e.key === "0") fitView();
        else if (e.key === "ArrowLeft") viewerNext();
        else if (e.key === "ArrowRight") viewerPrev();
        else if (e.key === "Tab") { // keep focus inside the viewer
          const items = [...$("#viewer").querySelectorAll("button:not(:disabled)")];
          if (!items.length) return;
          const first = items[0], last = items[items.length - 1];
          if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
          else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
      }
    });
    for (const id of SETTING_IDS) $("#set-" + id).addEventListener("change", saveSettings);

    loadPages().catch((err) => toast(err.message));
  }

  init();
})();
