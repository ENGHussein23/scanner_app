(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);

  async function api(method, url, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const res = await fetch(url, opts);
    let data = {};
    try { data = await res.json(); } catch (_) { /* not JSON */ }
    if (!res.ok) throw new Error(data.error || `صار خطأ (${res.status})`);
    return data;
  }

  function toast(text) {
    const el = $("#toast");
    el.textContent = text; el.hidden = false;
    setTimeout(() => { el.hidden = true; }, 5000);
  }

  function fill(cfg) {
    $("#autostart").checked = !!cfg.autostart;
    $("#port").value = cfg.port;
    $("#origins").value = (cfg.allowed_origins || []).join("\n");
    const d = cfg.defaults || {};
    $("#d-mode").value = d.mode; $("#d-dpi").value = String(d.dpi);
    $("#d-duplex").checked = !!d.duplex; $("#d-color").value = d.color; $("#d-source").value = d.source; $("#d-host").value = d.host || "";
  }

  async function save() {
    const btn = $("#saveBtn");
    btn.disabled = true;
    try {
      const out = await api("POST", "/api/settings", {
        autostart: $("#autostart").checked,
        port: $("#port").value.trim(),
        allowed_origins: $("#origins").value.split("\n").map((l) => l.trim()).filter(Boolean),
        defaults: {
          mode: $("#d-mode").value, dpi: Number($("#d-dpi").value), color: $("#d-color").value,
          source: $("#d-source").value, duplex: $("#d-duplex").checked, host: $("#d-host").value.trim(),
        },
      });
      fill(out.settings);
      const msg = $("#msg");
      msg.textContent = out.restart_needed
        ? "انحفظت الإعدادات ✓ — غيّرت المنفذ، لازم تطلع من الخدمة (أيقونة الساعة ← خروج) وتشغّلها من جديد."
        : "انحفظت الإعدادات ✓";
      msg.hidden = false;
    } catch (err) { toast(err.message); } finally { btn.disabled = false; }
  }

  async function diag() {
    const out = $("#diag"), btn = $("#diagBtn");
    btn.disabled = true; out.hidden = false; out.textContent = "عم دوّر… (ممكن ياخد كم ثانية)";
    try {
      const host = encodeURIComponent($("#d-host").value.trim());
      const data = await api("GET", `/api/devices?host=${host}`);
      const lines = [data.devices.length ? "الأجهزة اللي انلقت:" : "ما انلقى أي سكانر."];
      for (const d of data.devices) lines.push(`  • ${d.name}  [${d.kind === "usb" ? "USB" : "شبكة"} / ${d.backend}]`);
      lines.push("", "طرق الاتصال:");
      for (const b of data.backends) lines.push(`  • ${b.name}: ${b.available ? "جاهز" : "غير متوفر"}${b.reason ? " — " + b.reason : ""}`);
      if (data.errors.length) lines.push("", "ملاحظات:", ...data.errors.map((e) => "  • " + e));
      out.textContent = lines.join("\n");
    } catch (err) { out.textContent = err.message; } finally { btn.disabled = false; }
  }

  async function init() {
    $("#saveBtn").addEventListener("click", save);
    $("#diagBtn").addEventListener("click", diag);
    try {
      fill(await api("GET", "/api/settings"));
      const h = await api("GET", "/api/health");
      $("#version").textContent = "v" + h.version;
    } catch (err) { toast(err.message); }
  }
  init();
})();
