// Headscale Easy — UI behaviour. No dependencies.
// https://github.com/insanerask77/headscale-easy
(function () {
  "use strict";

  var body = document.body;
  var T = { copied: body.dataset.copied || "Copied", working: body.dataset.working || "Working" };

  // ---- Theme -------------------------------------------------------------
  // Saved: "dark" | "light"; nothing saved = follow the system (see theme.js).
  function applyTheme(choice) {
    try {
      if (choice === "system") localStorage.removeItem("hse-theme");
      else localStorage.setItem("hse-theme", choice);
    } catch (e) {}
    var light = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
    document.documentElement.dataset.theme = choice === "system" ? (light ? "light" : "dark") : choice;
    markThemeChoice();
  }

  function markThemeChoice() {
    var saved = null;
    try { saved = localStorage.getItem("hse-theme"); } catch (e) {}
    document.querySelectorAll("[data-theme-set]").forEach(function (b) {
      b.classList.toggle("active", b.dataset.themeSet === (saved || "system"));
    });
  }

  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", function () {
      var saved = null;
      try { saved = localStorage.getItem("hse-theme"); } catch (e) {}
      if (!saved) applyTheme("system");
    });
  }

  // ---- Copy ----------------------------------------------------------------
  // navigator.clipboard only exists over HTTPS (or localhost); over HTTP a
  // temporary textarea does the job.
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) {}
      body.removeChild(ta);
      ok ? resolve() : reject();
    });
  }

  function toast(text) {
    var t = document.createElement("div");
    t.className = "toast";
    t.textContent = text;
    body.appendChild(t);
    setTimeout(function () { t.classList.add("out"); }, 1300);
    setTimeout(function () { t.remove(); }, 1700);
  }

  // ---- Floating menus ----------------------------------------------------------
  // A menu cannot be absolute inside a table cell: the table sits in a scroll
  // container that would clip it. When it opens it is placed with
  // position: fixed next to its button, below if it fits, above otherwise.
  var GAP = 6, MARGIN = 8;

  function placeMenu(d) {
    var menu = d.querySelector(".dropdown-body");
    var anchor = d.querySelector("summary");
    if (!menu || !anchor || d.classList.contains("up")) return;
    menu.classList.add("floating");
    var a = anchor.getBoundingClientRect();
    var w = menu.offsetWidth, h = menu.offsetHeight;
    var vw = document.documentElement.clientWidth, vh = window.innerHeight;
    var left = menu.classList.contains("right") ? a.right - w : a.left;
    left = Math.max(MARGIN, Math.min(left, vw - w - MARGIN));
    var top = a.bottom + GAP;
    if (top + h > vh - MARGIN && a.top - GAP - h >= MARGIN) top = a.top - GAP - h;
    top = Math.max(MARGIN, Math.min(top, vh - h - MARGIN));
    menu.style.left = left + "px";
    menu.style.top = top + "px";
  }

  function closeMenus(except) {
    document.querySelectorAll("details.dropdown[open]").forEach(function (d) {
      if (d !== except) d.removeAttribute("open");
    });
  }

  document.addEventListener("toggle", function (ev) {
    var d = ev.target;
    if (!(d.matches && d.matches("details.dropdown")) || !d.open) return;
    closeMenus(d);
    placeMenu(d);
  }, true);

  window.addEventListener("scroll", function (ev) {
    if (ev.target.closest && ev.target.closest(".dropdown-body")) return;
    closeMenus();
  }, true);
  window.addEventListener("resize", function () { closeMenus(); });

  // ---- Clicks --------------------------------------------------------------------
  document.addEventListener("click", function (ev) {
    var t = ev.target;
    var el = t.closest("[data-theme-toggle]");
    if (el) { applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"); return; }
    el = t.closest("[data-theme-set]");
    if (el) { applyTheme(el.dataset.themeSet); return; }

    el = t.closest("[data-copy]");
    if (el) {
      ev.preventDefault();
      ev.stopPropagation();
      copyText(el.dataset.copy).then(function () { toast(T.copied); });
      closeMenus();
      return;
    }

    el = t.closest("[data-open]");
    if (el) { openDialog(el.dataset.open); return; }
    el = t.closest("[data-close]");
    if (el) { el.closest("dialog").close(); return; }
    if (t.tagName === "DIALOG") { t.close(); return; }  // click on the backdrop

    el = t.closest("[data-tab]");
    if (el) {
      var box = el.closest(".card");
      box.querySelectorAll("[data-tab]").forEach(function (b) { b.classList.toggle("active", b === el); });
      box.querySelectorAll("[data-panel]").forEach(function (p) { p.hidden = p.dataset.panel !== el.dataset.tab; });
      return;
    }

    el = t.closest("[data-f-clear]");
    if (el) {
      document.querySelectorAll("[data-f]").forEach(function (f) {
        if (f.type === "checkbox") f.checked = false; else f.value = "";
      });
      filterRows();
      return;
    }

    // A table row opens the machine unless the click was on a control
    var row = t.closest("tr[data-href]");
    if (row && !t.closest("a, button, details, summary, input, form, select, label")) {
      window.location = row.dataset.href;
      return;
    }

    document.querySelectorAll("details.dropdown[open]").forEach(function (d) {
      if (!d.contains(t)) d.removeAttribute("open");
    });
  });

  function openDialog(id) {
    var dlg = document.getElementById(id);
    closeMenus();
    if (!dlg) return;
    dlg.showModal();
    var first = dlg.querySelector("input:not([type=hidden]):not([type=checkbox]), select");
    if (first) { first.focus(); if (first.select) first.select(); }
  }

  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") closeMenus();
    // "/" focuses the search, like in the Tailscale console
    if (ev.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      var s = document.querySelector("[data-filter]");
      if (s) { ev.preventDefault(); s.focus(); }
    }
    // Tab in the policy editor indents instead of leaving the field
    var ta = ev.target;
    if (ev.key === "Tab" && ta.matches && ta.matches("[data-tab-indent]")) {
      ev.preventDefault();
      var start = ta.selectionStart;
      ta.value = ta.value.slice(0, start) + "  " + ta.value.slice(ta.selectionEnd);
      ta.selectionStart = ta.selectionEnd = start + 2;
    }
  });

  // ---- Search and filters ---------------------------------------------------------
  function val(name) {
    var f = document.querySelector('[data-f="' + name + '"]');
    if (!f) return "";
    return f.type === "checkbox" ? (f.checked ? "1" : "") : f.value;
  }

  function filterRows() {
    var input = document.querySelector("[data-filter]");
    if (!input) return;
    var q = input.value.trim().toLowerCase();
    var status = val("status"), owner = val("owner");
    var flags = ["update", "routes", "expired"].filter(val);
    var shown = 0;
    document.querySelectorAll("tr[data-search]").forEach(function (r) {
      var ok = (!q || r.dataset.search.indexOf(q) !== -1) &&
               (!status || r.dataset.status === status) &&
               (!owner || r.dataset.owner === owner) &&
               flags.every(function (f) { return r.dataset[f] === "1"; });
      r.hidden = !ok;
      if (ok) shown++;
    });
    var empty = document.querySelector(".no-results");
    if (empty) empty.hidden = shown !== 0;
    var pill = document.querySelector("[data-count]");
    if (pill) pill.textContent = shown === 1 ? pill.dataset.one : pill.dataset.many.replace("{n}", shown);
    var active = [status, owner].filter(Boolean).length + flags.length;
    var badge = document.querySelector(".fcount");
    if (badge) { badge.hidden = !active; badge.textContent = active; }
  }

  document.addEventListener("input", function (ev) {
    if (ev.target.matches("[data-filter]")) filterRows();
  });
  document.addEventListener("change", function (ev) {
    if (ev.target.matches("[data-f]")) filterRows();
  });

  // Filter by owner from the URL (?owner=<id>), e.g. coming from Users
  (function () {
    var owner = new URLSearchParams(location.search).get("owner");
    var sel = document.querySelector('[data-f="owner"]');
    if (sel && owner) { sel.value = owner; filterRows(); }
  })();

  // "#new" opens the matching dialog (e.g. Add device → Generate auth key)
  if (location.hash === "#new") openDialog("new");

  // Slow forms (saving DNS restarts Headscale): lock the button while working
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!form.hasAttribute("data-busy")) return;
    form.querySelectorAll("button[type=submit]").forEach(function (b) {
      b.disabled = true;
      b.textContent = T.working + "…";
    });
  });

  // ---- Live updates -------------------------------------------------------------------
  // Parts marked data-live (machine rows, status, last seen...) are refreshed
  // every few seconds without reloading, so machines appear, connect and
  // disconnect on their own. Skipped while the tab is hidden or while a menu
  // or dialog INSIDE a live part is open (a machine's menu, its rename
  // dialog), so nothing moves under the pointer. Menus and dialogs outside
  // (Add device, a freshly generated key) do not stop it.
  var LIVE_EVERY = 5000;
  var liveSrc = {};  // server HTML of each part, before dates are formatted
  document.querySelectorAll("[data-live]").forEach(function (el) { liveSrc[el.dataset.live] = el.innerHTML; });

  function liveBlocked() {
    return document.hidden ||
      document.querySelector("[data-live] details.dropdown[open], [data-live] dialog[open]");
  }
  var liveBusy = false;
  function liveRefresh() {
    if (liveBusy || liveBlocked()) return;
    liveBusy = true;
    fetch(location.href, { credentials: "same-origin", cache: "no-store", headers: { "X-Live": "1" } })
      .then(function (r) {
        if (!r.ok || r.redirected) throw new Error("stale session");
        return r.text();
      })
      .then(function (html) {
        if (liveBlocked()) return;
        var doc = new DOMParser().parseFromString(html, "text/html");
        var changed = false;
        document.querySelectorAll("[data-live]").forEach(function (el) {
          var fresh = doc.querySelector('[data-live="' + el.dataset.live + '"]');
          if (!fresh || fresh.innerHTML === liveSrc[el.dataset.live]) return;
          liveSrc[el.dataset.live] = fresh.innerHTML;
          el.innerHTML = fresh.innerHTML;
          changed = true;
        });
        if (changed) { formatDates(document); filterRows(); }
      })
      .catch(function () {})
      .then(function () { liveBusy = false; });
  }
  if (Object.keys(liveSrc).length) {
    setInterval(liveRefresh, LIVE_EVERY);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) liveRefresh(); });
    window.addEventListener("focus", liveRefresh);
  }

  // ---- Dates in the viewer's timezone and language ----------------------------------
  var lang = document.documentElement.lang || navigator.language;
  var fmtLong = new Intl.DateTimeFormat(lang, { dateStyle: "medium", timeStyle: "short" });
  var fmtShort = new Intl.DateTimeFormat(lang, { month: "short", day: "numeric" });
  var fmtYear = new Intl.DateTimeFormat(lang, { dateStyle: "medium" });
  function formatDates(root) {
    root.querySelectorAll("time[data-local]").forEach(function (el) {
      var d = new Date(el.getAttribute("datetime"));
      if (isNaN(d)) return;
      el.title = fmtLong.format(d);
      if (el.dataset.local === "short") {
        el.textContent = d.getFullYear() === new Date().getFullYear() ? fmtShort.format(d) : fmtYear.format(d);
      } else {
        el.textContent = fmtLong.format(d);
      }
    });
  }
  formatDates(document);

  markThemeChoice();
})();
