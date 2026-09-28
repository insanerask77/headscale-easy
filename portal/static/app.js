// Mi VPN: interacción de la UI. Sin dependencias.

(function () {
  "use strict";

  // ---- Tema -----------------------------------------------------------------
  // Guardado: "dark" | "light"; sin guardar = seguir al sistema (ver theme.js).
  function applyTheme(choice) {
    try {
      if (choice === "system") localStorage.removeItem("mivpn-theme");
      else localStorage.setItem("mivpn-theme", choice);
    } catch (e) {}
    var light = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
    document.documentElement.dataset.theme = choice === "system" ? (light ? "light" : "dark") : choice;
    markThemeChoice();
  }

  function markThemeChoice() {
    var saved = null;
    try { saved = localStorage.getItem("mivpn-theme"); } catch (e) {}
    document.querySelectorAll("[data-theme-set]").forEach(function (b) {
      b.classList.toggle("active", b.dataset.themeSet === (saved || "system"));
    });
  }

  // Si se sigue al sistema, reaccionar a sus cambios en vivo
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", function () {
      var saved = null;
      try { saved = localStorage.getItem("mivpn-theme"); } catch (e) {}
      if (!saved) applyTheme("system");
    });
  }

  // ---- Copiar ---------------------------------------------------------------
  // navigator.clipboard sólo existe con HTTPS (o localhost): por HTTP se copia
  // con un textarea temporal.
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) {}
      document.body.removeChild(ta);
      ok ? resolve() : reject();
    });
  }

  function toast(text) {
    var t = document.createElement("div");
    t.className = "toast";
    t.textContent = text;
    document.body.appendChild(t);
    setTimeout(function () { t.classList.add("out"); }, 1300);
    setTimeout(function () { t.remove(); }, 1700);
  }

  // ---- Clics ----------------------------------------------------------------
  document.addEventListener("click", function (ev) {
    var t = ev.target;

    var el = t.closest("[data-theme-toggle]");
    if (el) {
      applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
      return;
    }
    el = t.closest("[data-theme-set]");
    if (el) { applyTheme(el.dataset.themeSet); return; }

    el = t.closest("[data-copy]");
    if (el) {
      ev.preventDefault();
      ev.stopPropagation();
      copyText(el.dataset.copy).then(function () { toast("Copiado"); });
      closeMenus();
      return;
    }

    el = t.closest("[data-open]");
    if (el) {
      var dlg = document.getElementById(el.dataset.open);
      closeMenus();
      if (dlg) {
        dlg.showModal();
        var first = dlg.querySelector("input:not([type=hidden]), select");
        if (first) { first.focus(); if (first.select) first.select(); }
      }
      return;
    }
    el = t.closest("[data-close]");
    if (el) { el.closest("dialog").close(); return; }
    // Clic en el fondo del diálogo: cerrar
    if (t.tagName === "DIALOG") { t.close(); return; }

    el = t.closest("[data-tab]");
    if (el) {
      var box = el.closest(".card");
      box.querySelectorAll("[data-tab]").forEach(function (b) { b.classList.toggle("active", b === el); });
      box.querySelectorAll("[data-panel]").forEach(function (p) { p.hidden = p.dataset.panel !== el.dataset.tab; });
      return;
    }

    el = t.closest("[data-status-filter]");
    if (el) {
      el.parentNode.querySelectorAll("button").forEach(function (b) { b.classList.toggle("active", b === el); });
      filterRows();
      return;
    }

    // Fila de la tabla de dispositivos: abrir el detalle salvo si el clic fue
    // en un control (enlace, botón, menú)
    var row = t.closest("tr[data-href]");
    if (row && !t.closest("a, button, details, summary, input, form")) {
      window.location = row.dataset.href;
      return;
    }

    // Cerrar menús desplegables al hacer clic fuera
    document.querySelectorAll("details.dropdown[open]").forEach(function (d) {
      if (!d.contains(t)) d.removeAttribute("open");
    });
  });

  function closeMenus() {
    document.querySelectorAll("details.dropdown[open]").forEach(function (d) { d.removeAttribute("open"); });
  }

  // ---- Menús flotantes ------------------------------------------------------
  // El menú no puede ser absolute dentro de su celda: la tabla va en un
  // contenedor con overflow (para desplazarla en móvil) que lo recortaría.
  // Al abrirlo se coloca con position: fixed en coordenadas de pantalla, junto
  // a su botón: por debajo si cabe, por encima si no, y sin salirse por los
  // lados.
  var GAP = 6, MARGIN = 8;

  function placeMenu(d) {
    var body = d.querySelector(".dropdown-body");
    var anchor = d.querySelector("summary");
    if (!body || !anchor) return;
    body.classList.add("floating");
    var a = anchor.getBoundingClientRect();
    var w = body.offsetWidth, h = body.offsetHeight;
    var vw = document.documentElement.clientWidth, vh = window.innerHeight;

    var left = body.classList.contains("right") ? a.right - w : a.left;
    left = Math.max(MARGIN, Math.min(left, vw - w - MARGIN));

    var top = a.bottom + GAP;
    if (top + h > vh - MARGIN && a.top - GAP - h >= MARGIN) top = a.top - GAP - h;
    top = Math.max(MARGIN, Math.min(top, vh - h - MARGIN));

    body.style.left = left + "px";
    body.style.top = top + "px";
  }

  // Sólo un menú abierto a la vez, y colocado al abrirse
  document.addEventListener("toggle", function (ev) {
    var d = ev.target;
    if (!(d.matches && d.matches("details.dropdown"))) return;
    if (d.open) {
      document.querySelectorAll("details.dropdown[open]").forEach(function (o) { if (o !== d) o.removeAttribute("open"); });
      placeMenu(d);
    }
  }, true);

  // Un menú fijo se quedaría flotando al desplazar la página: se cierra, como
  // en la consola de Tailscale. Los desplazamientos dentro del propio menú no
  // cuentan.
  window.addEventListener("scroll", function (ev) {
    if (ev.target.closest && ev.target.closest(".dropdown-body")) return;
    closeMenus();
  }, true);
  window.addEventListener("resize", closeMenus);

  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") closeMenus();
    // "/" enfoca la búsqueda, como en la consola de Tailscale
    if (ev.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      var s = document.querySelector("[data-filter]");
      if (s) { ev.preventDefault(); s.focus(); }
    }
  });

  // ---- Búsqueda y filtro de dispositivos -------------------------------------
  function filterRows() {
    var input = document.querySelector("[data-filter]");
    if (!input) return;
    var q = input.value.trim().toLowerCase();
    var active = document.querySelector("[data-status-filter].active");
    var status = active ? active.dataset.statusFilter : "all";
    var ownerSel = document.querySelector("[data-owner-filter]");
    var owner = ownerSel ? ownerSel.value : "";
    var shown = 0;
    document.querySelectorAll("tr[data-search]").forEach(function (r) {
      var ok = (!q || r.dataset.search.indexOf(q) !== -1) &&
               (status === "all" || !r.dataset.status || r.dataset.status === status) &&
               (!owner || r.dataset.owner === owner);
      r.hidden = !ok;
      if (ok) shown++;
    });
    var empty = document.querySelector(".no-results");
    if (empty) empty.hidden = shown !== 0;
  }
  document.addEventListener("input", function (ev) {
    if (ev.target.matches("[data-filter]")) filterRows();
  });
  document.addEventListener("change", function (ev) {
    if (ev.target.matches("[data-owner-filter]")) filterRows();
  });

  // Filtro por usuario desde la URL (?owner=<id>), p. ej. desde Usuarios
  (function () {
    var sel = document.querySelector("[data-owner-filter]");
    var owner = new URLSearchParams(location.search).get("owner");
    if (sel && owner) { sel.value = owner; filterRows(); }
  })();

  // Formularios lentos (guardar DNS reinicia Headscale): bloquear el botón y
  // avisar de que está trabajando
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!form.dataset.busy) return;
    form.querySelectorAll("button[type=submit]").forEach(function (b) {
      b.disabled = true;
      b.textContent = form.dataset.busy + "…";
    });
  });

  // Tab en el editor de ACL: sangrar en vez de saltar de campo
  document.addEventListener("keydown", function (ev) {
    var ta = ev.target;
    if (ev.key !== "Tab" || !ta.matches || !ta.matches("[data-tab-indent]")) return;
    ev.preventDefault();
    var start = ta.selectionStart, end = ta.selectionEnd;
    ta.value = ta.value.slice(0, start) + "  " + ta.value.slice(end);
    ta.selectionStart = ta.selectionEnd = start + 2;
  });

  // ---- Fechas en hora local ---------------------------------------------------
  var fmt = new Intl.DateTimeFormat(navigator.language || "es-ES", { dateStyle: "medium", timeStyle: "short" });
  document.querySelectorAll("time[data-local]").forEach(function (el) {
    var d = new Date(el.getAttribute("datetime"));
    if (!isNaN(d)) { el.title = el.textContent; el.textContent = fmt.format(d); }
  });

  markThemeChoice();
})();
