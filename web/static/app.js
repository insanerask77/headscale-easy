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
    var flags = ["update", "routes", "expired", "expiring", "inactive"].filter(val);
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

  // Setup complete page: the console needs a moment to start. Wait until it answers (the wizard answers
  // the same path with a redirect, a proxy error means "not yet"), then go to the sign-in page.
  (function () {
    var box = document.querySelector("[data-await-console]");
    if (!box) return;
    var target = box.dataset.awaitConsole, probe = box.dataset.probe, started = Date.now();
    var waiting = box.querySelector("[data-await-waiting]"), slow = box.querySelector("[data-await-slow]");
    function poll() {
      fetch(probe, { cache: "no-store", redirect: "manual", credentials: "omit" }).then(function (r) {
        if (r.status === 200 && r.type === "basic") { location.replace(target); return; }
        again();
      }, again);
    }
    function again() {
      if (Date.now() - started > 120000) {
        if (waiting) waiting.hidden = true;
        if (slow) slow.hidden = false;
        return;
      }
      setTimeout(poll, 1500);
    }
    setTimeout(poll, 1000);
  })();

  // Restoring page: the console is stopped and started again by the restore. Poll until it answers that
  // this restore (its id is in the probe URL) is done; the connection errors in between just mean "not yet".
  (function () {
    var box = document.querySelector("[data-await-restore]");
    if (!box) return;
    var target = box.dataset.awaitRestore, probe = box.dataset.probe, started = Date.now();
    var waiting = box.querySelector("[data-await-waiting]"), slow = box.querySelector("[data-await-slow]");
    function poll() {
      fetch(probe, { cache: "no-store", credentials: "omit" }).then(function (r) {
        return r.status === 200 ? r.json() : null;
      }).then(function (j) {
        if (j && j.done) { location.replace(target); return; }
        again();
      }, again);
    }
    function again() {
      if (Date.now() - started > 300000) {
        if (waiting) waiting.hidden = true;
        if (slow) slow.hidden = false;
        return;
      }
      setTimeout(poll, 2000);
    }
    setTimeout(poll, 2000);
  })();

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
  var livePending = false;  // a change arrived while a menu was open: refresh as soon as it closes
  var lastRefresh = Date.now();
  var streamUp = false;     // the event stream is connected
  function liveRefresh() {
    if (liveBusy) return;
    if (liveBlocked()) { livePending = true; return; }
    livePending = false;
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
          var before = rowHtml(el);
          liveSrc[el.dataset.live] = fresh.innerHTML;
          el.innerHTML = fresh.innerHTML;
          highlightChanged(el, before);
          changed = true;
        });
        if (changed) { formatDates(document); filterRows(); }
        lastRefresh = Date.now();
      })
      .catch(function () {})
      .then(function () { liveBusy = false; });
  }

  // Rows that changed get a short highlight (CSS skips it for prefers-reduced-motion)
  function rowHtml(root) {
    var out = {};
    root.querySelectorAll("tr[data-href]").forEach(function (r) { out[r.dataset.href] = r.innerHTML; });
    return out;
  }
  function highlightChanged(root, before) {
    root.querySelectorAll("tr[data-href]").forEach(function (r) {
      if (before[r.dataset.href] !== r.innerHTML) r.classList.add("changed");
    });
  }

  // ---- Event stream: a device connected, disconnected, was added or removed ---------
  // The server pushes one small message per change; the page then re-fetches its own
  // (already filtered) HTML. While the stream is up the timer only acts as a safety net.
  var streamEl = document.querySelector("[data-stream]");
  var indicator = document.querySelector("[data-live-indicator]");
  function setIndicator(up) {
    streamUp = up;
    if (!indicator) return;
    indicator.hidden = false;
    indicator.classList.toggle("on", up);
    indicator.querySelector("b").textContent = up ? indicator.dataset.on : indicator.dataset.off;
  }
  if (streamEl && window.EventSource) {
    var debounce = null;
    var es = new EventSource(streamEl.dataset.stream);
    es.onopen = function () { setIndicator(true); liveRefresh(); };  // catch up with what was missed
    es.onerror = function () { setIndicator(false); };               // the browser reconnects by itself
    es.addEventListener("node", function () {
      clearTimeout(debounce);
      debounce = setTimeout(liveRefresh, 150);
    });
    window.addEventListener("beforeunload", function () { es.close(); });
  }

  if (Object.keys(liveSrc).length) {
    // Without the stream: every few seconds. With it: only when a refresh was postponed
    // or as a once-a-minute safety net.
    setInterval(function () {
      if (!streamUp || livePending || Date.now() - lastRefresh > 60000) liveRefresh();
    }, LIVE_EVERY);
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

/* ---- expiry ---- */
// "Show only these" in the expiry notice of Machines ticks the matching
// filter (data-f-apply="expiring" | "expired"); its change event reaches
// filterRows() above. ?filter=<name> in the URL does the same.
(function () {
  "use strict";
  function applyFilter(name) {
    var f = document.querySelector('input[type=checkbox][data-f="' + name + '"]');
    if (!f) return;
    f.checked = true;
    f.dispatchEvent(new Event("change", { bubbles: true }));
  }
  document.addEventListener("click", function (ev) {
    var el = ev.target.closest && ev.target.closest("[data-f-apply]");
    if (el) applyFilter(el.dataset.fApply);
  });
  var fromUrl = new URLSearchParams(location.search).get("filter");
  if (fromUrl) applyFilter(fromUrl);
})();

/* ---- dns ---- */
// DNS page lists (nameservers, split DNS, search domains, custom records):
// without JS each list has a blank row to add one entry per save. With JS the
// blank rows go away, "Add" buttons append a row from the list's <template>
// and each row gets a remove button.
(function () {
  "use strict";
  var lists = document.querySelectorAll("[data-dns-list]");
  if (!lists.length) return;
  lists.forEach(function (list) {
    list.querySelectorAll("[data-dns-blank]").forEach(function (row) { row.remove(); });
  });
  document.querySelectorAll("[data-dns-remove], [data-dns-add]").forEach(function (b) { b.hidden = false; });

  document.addEventListener("click", function (ev) {
    var el = ev.target.closest("[data-dns-add]");
    if (el) {
      var id = el.dataset.dnsAdd;
      var tpl = document.querySelector('[data-dns-template="' + id + '"]');
      var list = document.querySelector('[data-dns-list="' + id + '"]');
      if (!tpl || !list) return;
      var row = tpl.content.firstElementChild.cloneNode(true);
      row.querySelectorAll("[data-dns-remove]").forEach(function (b) { b.hidden = false; });
      list.appendChild(row);
      var first = row.querySelector("input");
      if (first) first.focus();
      return;
    }
    el = ev.target.closest("[data-dns-remove]");
    if (el) {
      var li = el.closest(".dns-row");
      var next = li.nextElementSibling || li.previousElementSibling;
      li.remove();
      var focus = next ? next.querySelector("input") : null;
      if (focus) focus.focus();
    }
  });
})();

/* ---- bulk ---- */
// Machines table: without JS the per-row and header checkboxes stay hidden
// (there is no way to submit "just the checked ones" without JS anyway). With
// JS they appear, a bar shows up once at least one is ticked, and the two
// dialog-based actions (Remove, Add tag) get the selection mirrored into
// their own <form> as hidden inputs, since a <dialog> is a separate form from
// the table's.
(function () {
  "use strict";
  var bar = document.querySelector("[data-bulk-bar]");
  if (!bar) return;
  document.querySelectorAll(".bulk-col").forEach(function (el) { el.hidden = false; });

  function items() {
    return Array.prototype.slice.call(document.querySelectorAll("[data-bulk-item]"));
  }

  function sync() {
    var checked = items().filter(function (i) { return i.checked; });
    bar.hidden = checked.length === 0;
    var count = document.querySelector("[data-bulk-count]");
    if (count) count.textContent = checked.length === 1 ? count.dataset.one : count.dataset.many.replace("{n}", checked.length);
    var all = document.querySelector("[data-bulk-all]");
    if (all) {
      all.checked = checked.length > 0 && checked.length === items().length;
      all.indeterminate = checked.length > 0 && checked.length < items().length;
    }
    document.querySelectorAll("[data-bulk-mirror]").forEach(function (target) {
      target.innerHTML = "";
      checked.forEach(function (i) {
        var hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = i.name;
        hidden.value = "1";
        target.appendChild(hidden);
      });
    });
  }

  document.addEventListener("change", function (ev) {
    if (ev.target.matches("[data-bulk-all]")) {
      var checked = ev.target.checked;
      items().forEach(function (i) { i.checked = checked; });
      sync();
    } else if (ev.target.matches("[data-bulk-item]")) {
      sync();
    }
  });

  document.addEventListener("click", function (ev) {
    var el = ev.target.closest("[data-bulk-clear]");
    if (!el) return;
    items().forEach(function (i) { i.checked = false; });
    sync();
  });
})();

// ---- Two-factor suggestion: once per browser session ------------------------------
(function () {
  var dlg = document.getElementById("nudge-2fa");
  if (!dlg || !dlg.showModal) return;
  try { if (sessionStorage.getItem("hse_nudge_2fa")) return; } catch (e) { /* storage blocked: show it */ }
  dlg.addEventListener("close", function () {
    try { sessionStorage.setItem("hse_nudge_2fa", "1"); } catch (e) { /* ignore */ }
  });
  dlg.querySelectorAll("a").forEach(function (a) {
    a.addEventListener("click", function () {
      try { sessionStorage.setItem("hse_nudge_2fa", "1"); } catch (e) { /* ignore */ }
    });
  });
  dlg.showModal();
})();
