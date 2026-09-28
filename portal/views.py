"""HTML del portal Mi VPN. Estructura calcada de la consola de Tailscale:
Dispositivos (lista y detalle), DNS, Ajustes (General y Claves) y la página
de Añadir dispositivo. Todo el texto de usuario pasa por esc()."""

from __future__ import annotations

import hashlib
import html
import ipaddress
from datetime import datetime, timezone
from pathlib import Path

BASE = "/mi-vpn"


def _asset_version() -> str:
    """Hash del contenido de static/: va en las URLs de CSS y JS para que un
    cambio invalide la caché del navegador al momento."""
    h = hashlib.sha256()
    for f in sorted((Path(__file__).parent / "static").glob("*")):
        h.update(f.read_bytes())
    return h.hexdigest()[:10]


V = _asset_version()


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


# -----------------------------------------------------------------------------
# Formato de datos
# -----------------------------------------------------------------------------

def parse_time(value: str | None) -> datetime | None:
    if not value or value.startswith("0001-"):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def relative(dt: datetime | None, future: bool = False) -> str:
    if dt is None:
        return "nunca"
    secs = (dt - datetime.now(timezone.utc)).total_seconds()
    if not future:
        secs = -secs
    if secs < 60:
        return "ahora mismo" if not future else "en menos de un minuto"
    for size, unit in ((86400, "d"), (3600, "h"), (60, "min")):
        if secs >= size:
            n = int(secs // size)
            return f"en {n} {unit}" if future else f"hace {n} {unit}"
    return ""


def time_tag(value: str | None, empty: str = "—") -> str:
    """<time> con la fecha en UTC; app.js la pasa a la hora local."""
    dt = parse_time(value)
    if dt is None:
        return esc(empty)
    iso = dt.isoformat()
    return f'<time datetime="{esc(iso)}" data-local>{esc(dt.strftime("%Y-%m-%d %H:%M UTC"))}</time>'


OS_NAMES = {"linux": "Linux", "windows": "Windows", "macos": "macOS", "ios": "iOS",
            "android": "Android", "freebsd": "FreeBSD", "openbsd": "OpenBSD", "tvos": "tvOS"}
REGISTER = {"REGISTER_METHOD_OIDC": "Inicio de sesión (navegador)",
            "REGISTER_METHOD_AUTH_KEY": "Clave de autenticación",
            "REGISTER_METHOD_CLI": "Línea de comandos (admin)"}
EXIT_ROUTES = {"0.0.0.0/0", "::/0"}


def version_tuple(v: str) -> tuple:
    return tuple(int(p) for p in v.split(".") if p.isdigit())


class Machine:
    """Vista de un nodo: une la API y el Hostinfo de la base de datos."""

    def __init__(self, node: dict, details: dict | None, dns: dict, latest: str, regions: dict):
        self.node = node
        hi = (details or {}).get("hostinfo") or {}
        self.hostinfo = hi
        self.endpoints = (details or {}).get("endpoints") or []
        self.id = str(node.get("id"))
        self.name = node.get("givenName") or node.get("name") or ""
        self.hostname = hi.get("Hostname") or node.get("name") or ""
        self.online = bool(node.get("online"))
        self.last_seen = parse_time(node.get("lastSeen"))
        self.created = node.get("createdAt")
        self.owner = node.get("user") or {}
        self.tags = node.get("tags") or []

        ips = node.get("ipAddresses") or []
        self.ipv4 = next((ip for ip in ips if ":" not in ip), "")
        self.ipv6 = next((ip for ip in ips if ":" in ip), "")
        self.fqdn = f"{self.name}.{dns['base_domain']}" if dns.get("magic_dns") and dns.get("base_domain") else ""

        expiry = parse_time(node.get("expiry"))
        self.expiry = expiry
        self.expiry_disabled = expiry is None
        self.expired = expiry is not None and expiry < datetime.now(timezone.utc)

        # SO y versión: sólo en el Hostinfo (la API v1 no los da)
        os_raw = (hi.get("OS") or "").lower()
        self.os = OS_NAMES.get(os_raw, hi.get("OS") or "")
        if os_raw == "linux" and hi.get("Distro"):
            self.os_detail = f"{hi['Distro'].capitalize()} {hi.get('DistroVersion', '')}".strip()
        else:
            self.os_detail = hi.get("OSVersion") or ""
        self.version = (hi.get("IPNVersion") or "").split("-")[0]
        self.update_available = bool(
            self.version and latest and version_tuple(self.version) < version_tuple(latest))
        self.latest = latest

        derp = (hi.get("NetInfo") or {}).get("PreferredDERP")
        self.derp = regions.get(derp, f"Región {derp}") if derp else ""

        available = set(node.get("availableRoutes") or [])
        approved = set(node.get("approvedRoutes") or [])
        self.exit_node = bool(available & EXIT_ROUTES)
        self.exit_node_approved = bool(approved & EXIT_ROUTES)
        self.subnets = sorted(available - EXIT_ROUTES)
        self.approved = approved
        self.ephemeral = bool((node.get("preAuthKey") or {}).get("ephemeral"))
        self.register = REGISTER.get(node.get("registerMethod"), "—")

    def search_text(self) -> str:
        return " ".join([self.name, self.hostname, self.ipv4, self.ipv6, self.os, self.os_detail,
                         self.version, *self.tags]).lower()

    def badges(self) -> str:
        out = []
        if self.expired:
            out.append('<span class="badge warn">Caducado</span>')
        elif self.expiry_disabled:
            out.append('<span class="badge">Caducidad desactivada</span>')
        if self.exit_node:
            out.append(f'<span class="badge {"" if self.exit_node_approved else "pending"}">Nodo de salida'
                       f'{"" if self.exit_node_approved else " · pendiente"}</span>')
        if self.subnets:
            pending = any(r not in self.approved for r in self.subnets)
            out.append(f'<span class="badge {"pending" if pending else ""}">Subredes{" · pendiente" if pending else ""}</span>')
        if self.ephemeral:
            out.append('<span class="badge">Efímero</span>')
        out += [f'<span class="badge tag">{esc(t)}</span>' for t in self.tags]
        return "".join(out)


# -----------------------------------------------------------------------------
# Piezas comunes
# -----------------------------------------------------------------------------

LOGO = """<svg class="logo" viewBox="0 0 48 48" aria-hidden="true">
  <circle cx="8" cy="8" r="6"/><circle class="off" cx="24" cy="8" r="6"/><circle cx="40" cy="8" r="6"/>
  <circle cx="8" cy="24" r="6"/><circle cx="24" cy="24" r="6"/><circle cx="40" cy="24" r="6"/>
  <circle cx="8" cy="40" r="6"/><circle class="off" cx="24" cy="40" r="6"/><circle cx="40" cy="40" r="6"/>
</svg>"""


def copy_btn(value: str, label: str = "Copiar") -> str:
    return f'<button type="button" class="copy" data-copy="{esc(value)}" title="Copiar">{esc(label)}</button>'


def csrf_input(session: dict) -> str:
    return f'<input type="hidden" name="csrf" value="{esc(session["csrf"])}">'


def initials(session: dict) -> str:
    base = (session.get("name") or session.get("username") or "?").strip()
    parts = [p for p in base.replace(".", " ").split() if p]
    return esc("".join(p[0] for p in parts[:2]).upper() or "?")


def layout(title: str, tab: str, body: str, session: dict, ctx: dict) -> str:
    items = [("machines", "machines", "Dispositivos")]
    if session.get("admin"):
        items += [("users", "users", "Usuarios"), ("acl", "acl", "Control de acceso")]
    items += [("dns", "dns", "DNS"), ("settings", "settings", "Ajustes")]
    tabs = "".join(
        f'<a href="{BASE}/{path}" class="{"active" if tab == key else ""}">{label}</a>'
        for key, path, label in items)
    return f"""<!doctype html>
<html lang="es" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} · Headscale Dashboard</title>
  <link rel="icon" href="{BASE}/static/favicon.svg?v={V}" type="image/svg+xml">
  <script src="{BASE}/static/theme.js?v={V}"></script>
  <link rel="stylesheet" href="{BASE}/static/style.css?v={V}">
</head>
<body>
  <header class="top">
    <div class="top-row">
      <a class="brand" href="{BASE}/machines">{LOGO}<b>Headscale</b></a>
      <span class="tailnet" title="Tu red">{esc(ctx.get("tailnet") or "")}</span>
      <div class="spacer"></div>
      <button class="icon-btn" type="button" data-theme-toggle aria-label="Cambiar tema" title="Cambiar tema claro/oscuro">
        <span class="i-sun">☀</span><span class="i-moon">☾</span>
      </button>
      <details class="dropdown usermenu">
        <summary class="avatar" aria-label="Menú de usuario">{initials(session)}</summary>
        <div class="dropdown-body right">
          <div class="who"><b>{esc(session.get("name") or session.get("username"))}</b>
            <span class="muted">{esc(session.get("email") or ("Administrador" if session.get("admin") else ""))}</span></div>
          <a href="{BASE}/settings">Ajustes</a>
          <form method="post" action="{BASE}/logout">{csrf_input(session)}<button type="submit">Cerrar sesión</button></form>
        </div>
      </details>
    </div>
    <nav class="tabs">{tabs}</nav>
  </header>
  <main class="container">
{body}
  </main>
  <script src="{BASE}/static/app.js?v={V}" defer></script>
</body>
</html>"""


FLASH = {
    "renamed": ("ok", "Dispositivo renombrado."),
    "removed": ("ok", "Dispositivo quitado de tu VPN."),
    "expired": ("ok", "Clave del dispositivo caducada: tendrá que volver a iniciar sesión."),
    "expiry-off": ("ok", "Caducidad de la clave desactivada."),
    "expiry-on": ("ok", "Caducidad de la clave reactivada."),
    "key-revoked": ("ok", "Clave revocada."),
    "bad-name": ("error", "Nombre no válido: sólo minúsculas, números y guiones (máx. 63)."),
    "not-found": ("error", "Ese elemento no existe o no es tuyo."),
    "forbidden": ("error", "Sólo un administrador puede hacer eso."),
    "no-user": ("error", "Primero conecta un dispositivo iniciando sesión en el navegador."),
    "failed": ("error", "Headscale rechazó la operación. Inténtalo de nuevo."),
    "routes": ("ok", "Rutas actualizadas."),
    "tags": ("ok", "Tags actualizados."),
    "registered": ("ok", "Dispositivo registrado."),
    "user-created": ("ok", "Usuario creado."),
    "user-renamed": ("ok", "Usuario renombrado."),
    "user-deleted": ("ok", "Usuario eliminado."),
    "user-has-nodes": ("error", "Ese usuario todavía tiene dispositivos: quítalos antes de eliminarlo."),
    "bad-user": ("error", "Nombre de usuario no válido: minúsculas, números, puntos, guiones y @."),
    "acl-saved": ("ok", "Política guardada y aplicada."),
    "dns-saved": ("ok", "DNS guardado. Headscale se ha reiniciado con la nueva configuración."),
    "apikey-expired": ("ok", "API key caducada."),
    "apikey-own": ("error", "Esa API key la usa Mi VPN: caducarla lo dejaría sin acceso a Headscale."),
}


def flash_html(code: str) -> str:
    if code not in FLASH:
        return ""
    kind, text = FLASH[code]
    return f'<div class="notice {kind}" role="status">{esc(text)}</div>'


def page_head(title: str, subtitle: str, action: str = "") -> str:
    return f"""
    <div class="page-head">
      <div><h1>{esc(title)}</h1><p class="muted">{subtitle}</p></div>
      {action}
    </div>"""


# -----------------------------------------------------------------------------
# Dispositivos
# -----------------------------------------------------------------------------

def machine_menu(m: Machine, session: dict, detail: bool = False) -> str:
    admin_expiry = ""
    if session.get("admin"):
        label = "Activar caducidad de la clave" if m.expiry_disabled else "Desactivar caducidad de la clave"
        admin_expiry = f"""
          <form method="post" action="{BASE}/machines/{m.id}/expiry">{csrf_input(session)}
            <input type="hidden" name="disable" value="{'0' if m.expiry_disabled else '1'}">
            <button type="submit">{label}</button></form>"""
    view = "" if detail else f'<a href="{BASE}/machines/{m.id}">Ver detalles</a>'
    tags = (f'<button type="button" data-open="tags-{m.id}">Editar tags…</button>'
            if session.get("admin") else "")
    return f"""
      <details class="dropdown">
        <summary class="icon-btn" aria-label="Acciones">···</summary>
        <div class="dropdown-body right">
          {view}
          <button type="button" data-open="rename-{m.id}">Editar nombre</button>
          {tags}
          <button type="button" data-copy="{esc(m.ipv4)}">Copiar IPv4</button>
          <button type="button" data-open="expire-{m.id}">Caducar clave…</button>{admin_expiry}
          <hr>
          <button type="button" class="danger" data-open="remove-{m.id}">Quitar…</button>
        </div>
      </details>"""


def machine_dialogs(m: Machine, session: dict, back: str) -> str:
    tags = ""
    if session.get("admin"):
        tags = f"""
    <dialog id="tags-{m.id}">
      <form method="post" action="{BASE}/machines/{m.id}/tags">{csrf_input(session)}
        <input type="hidden" name="back" value="{esc(back)}">
        <h3>Tags de {esc(m.name)}</h3>
        <p class="muted">Separados por comas, con el prefijo <code>tag:</code>. Cada tag tiene que tener
          dueño en <code>tagOwners</code> de la política de Control de acceso. Un dispositivo con tags
          deja de pertenecer a su usuario.</p>
        <label class="field">Tags<input name="tags" value="{esc(", ".join(m.tags))}" placeholder="tag:servidor, tag:prod"
          autocomplete="off" spellcheck="false"></label>
        <div class="dialog-actions"><button type="button" class="btn" data-close>Cancelar</button>
          <button class="btn primary" type="submit">Guardar</button></div>
      </form>
    </dialog>"""
    return tags + f"""
    <dialog id="rename-{m.id}">
      <form method="post" action="{BASE}/machines/{m.id}/rename">{csrf_input(session)}
        <input type="hidden" name="back" value="{esc(back)}">
        <h3>Editar nombre del dispositivo</h3>
        <p class="muted">Es el nombre en la VPN y en MagicDNS. Sólo minúsculas, números y guiones.</p>
        <label class="field">Nombre<input name="name" value="{esc(m.name)}" maxlength="63" required
          pattern="[a-z0-9]([a-z0-9\\-]*[a-z0-9])?" autocomplete="off" spellcheck="false"></label>
        <div class="dialog-actions"><button type="button" class="btn" data-close>Cancelar</button>
          <button class="btn primary" type="submit">Guardar</button></div>
      </form>
    </dialog>
    <dialog id="expire-{m.id}">
      <form method="post" action="{BASE}/machines/{m.id}/expire">{csrf_input(session)}
        <input type="hidden" name="back" value="{esc(back)}">
        <h3>¿Caducar la clave de {esc(m.name)}?</h3>
        <p class="muted">El dispositivo se desconectará de la VPN hasta que vuelvas a iniciar sesión en él.
          Útil si lo has perdido o quieres forzar un nuevo inicio de sesión.</p>
        <div class="dialog-actions"><button type="button" class="btn" data-close>Cancelar</button>
          <button class="btn danger-solid" type="submit">Caducar clave</button></div>
      </form>
    </dialog>
    <dialog id="remove-{m.id}">
      <form method="post" action="{BASE}/machines/{m.id}/delete">{csrf_input(session)}
        <h3>¿Quitar {esc(m.name)}?</h3>
        <p class="muted">Se borrará de tu VPN. Para volver a usarlo tendrás que conectarlo de nuevo.</p>
        <div class="dialog-actions"><button type="button" class="btn" data-close>Cancelar</button>
          <button class="btn danger-solid" type="submit">Quitar dispositivo</button></div>
      </form>
    </dialog>"""


def user_label(u: dict) -> str:
    """Nombre legible de un usuario de Headscale."""
    name = u.get("displayName") or u.get("name") or ""
    email = u.get("email") or ""
    return f"{name} ({email})" if email and email != name else name


def register_dialog(session: dict, users: list[dict] | None) -> str:
    opts = "".join(f'<option value="{esc(u["name"])}">{esc(user_label(u))}</option>'
                   for u in sorted(users or [], key=lambda u: user_label(u).lower()))
    return f"""
    <dialog id="register">
      <form method="post" action="{BASE}/machines/register">{csrf_input(session)}
        <h3>Registrar un dispositivo</h3>
        <p class="muted">Para dispositivos que ejecutaron <code>tailscale up</code> sin clave: copia el
          Auth ID (<code>hskey-authreq-…</code>) de la URL que imprimen y elige a quién pertenece.</p>
        <label class="field">Auth ID<input name="auth_id" placeholder="hskey-authreq-…" required
          autocomplete="off" spellcheck="false"></label>
        <label class="field">Usuario<select name="user" required>{opts}</select></label>
        <div class="dialog-actions"><button type="button" class="btn" data-close>Cancelar</button>
          <button class="btn primary" type="submit">Registrar</button></div>
      </form>
    </dialog>"""


def status_html(m: Machine) -> str:
    if m.online:
        return '<span class="status on"><i></i>Conectado</span>'
    return f'<span class="status off"><i></i>{esc(relative(m.last_seen))}</span>'


def addresses_dropdown(m: Machine) -> str:
    rows = [("IPv4", m.ipv4), ("IPv6", m.ipv6), ("MagicDNS", m.fqdn)]
    items = "".join(
        f'<div class="addr-row"><span class="muted small">{k}</span><code>{esc(v)}</code>{copy_btn(v)}</div>'
        for k, v in rows if v)
    return f"""
      <details class="dropdown addr">
        <summary><code>{esc(m.ipv4 or m.ipv6)}</code><span class="caret">▾</span></summary>
        <div class="dropdown-body wide">{items}</div>
      </details>"""


def machines_page(session: dict, ctx: dict, machines: list[Machine], has_user: bool, flash: str,
                  users: list[dict] | None = None, error: str = "") -> str:
    admin = session.get("admin")
    online = sum(m.online for m in machines)
    actions = f'<a class="btn primary" href="{BASE}/add">Añadir dispositivo</a>'
    if admin:
        actions = f'<button class="btn" type="button" data-open="register">Registrar con Auth ID</button>' + actions
    head = page_head(
        "Dispositivos",
        "Todos los dispositivos de la VPN." if admin else
        "Tus dispositivos conectados a la VPN. Sólo tú los ves y sólo pueden conectarse entre ellos.",
        f'<div class="head-actions">{actions}</div>')
    head += register_dialog(session, users) if admin else ""
    if error:
        head += f'<div class="notice error" role="alert">{esc(error)}</div>'

    if not machines:
        text = ("Conecta el primero: aparecerá aquí en cuanto inicies sesión desde él."
                if not has_user else "No tienes ningún dispositivo en la VPN ahora mismo.")
        return layout("Dispositivos", "machines", head + flash_html(flash) + f"""
    <section class="card empty">
      <div class="empty-icon">{LOGO}</div>
      <h3>Todavía no hay dispositivos</h3>
      <p class="muted">{text}</p>
      <a class="btn primary" href="{BASE}/add">Añadir dispositivo</a>
    </section>""", session, ctx)

    rows, dialogs = [], []
    for m in machines:
        version = ""
        if m.version:
            upd = (f'<span class="badge update" title="Última versión: {esc(m.latest)}">Actualización disponible</span>'
                   if m.update_available else "")
            version = f'<div>{esc(m.version)}</div><div class="muted small">{esc(m.os)} {esc(m.os_detail)}</div>{upd}'
        else:
            version = f'<span class="muted">{esc(m.os) or "—"}</span>'
        rows.append(f"""
        <tr data-href="{BASE}/machines/{m.id}" data-search="{esc(m.search_text())}" data-status="{'on' if m.online else 'off'}" data-owner="{esc(m.owner.get('id'))}">
          <td><a class="name" href="{BASE}/machines/{m.id}">{esc(m.name)}</a>
            <div class="muted small">{esc(m.owner.get("email") or m.owner.get("name"))}</div>
            <div class="badges">{m.badges()}</div></td>
          <td>{addresses_dropdown(m)}</td>
          <td class="hide-sm">{version}</td>
          <td>{status_html(m)}</td>
          <td class="actions">{machine_menu(m, session)}</td>
        </tr>""")
        dialogs.append(machine_dialogs(m, session, "machines"))

    owner_filter = ""
    if admin and users:
        opts = "".join(f'<option value="{esc(u["id"])}">{esc(user_label(u))}</option>'
                       for u in sorted(users, key=lambda u: user_label(u).lower()))
        owner_filter = (f'<select class="select" data-owner-filter aria-label="Filtrar por usuario">'
                        f'<option value="">Todos los usuarios</option>{opts}</select>')
    body = head + flash_html(flash) + f"""
    <div class="toolbar">
      <input type="search" class="search" placeholder="Buscar por nombre, IP, sistema…" data-filter aria-label="Buscar dispositivos">
      {owner_filter}
      <div class="segmented" role="group" aria-label="Filtrar por estado">
        <button type="button" class="active" data-status-filter="all">Todos <span>{len(machines)}</span></button>
        <button type="button" data-status-filter="on">Conectados <span>{online}</span></button>
        <button type="button" data-status-filter="off">Desconectados <span>{len(machines) - online}</span></button>
      </div>
    </div>
    <section class="card flush">
      <div class="table-wrap">
      <table class="machines">
        <thead><tr><th>Dispositivo</th><th>Direcciones</th><th class="hide-sm">Versión</th><th>Última conexión</th><th></th></tr></thead>
        <tbody>{"".join(rows)}
        </tbody>
      </table>
      </div>
      <p class="no-results muted" hidden>Ningún dispositivo coincide con la búsqueda.</p>
    </section>
    {"".join(dialogs)}"""
    return layout("Dispositivos", "machines", body, session, ctx)


def kv(label: str, value: str, copy: str | None = None) -> str:
    c = copy_btn(copy) if copy else ""
    return f'<div class="kv"><dt>{esc(label)}</dt><dd>{value}{c}</dd></div>'


def machine_page(session: dict, ctx: dict, m: Machine, flash: str) -> str:
    hi = m.hostinfo
    expiry = ("Desactivada" if m.expiry_disabled else
              f'{"Caducada" if m.expired else "Caduca " + esc(relative(m.expiry, future=True))} · {time_tag(m.node.get("expiry"))}')
    version = esc(m.version) or "—"
    if m.update_available:
        version += f' <span class="badge update">Actualización disponible: {esc(m.latest)}</span>'
    os_text = esc(" ".join(x for x in (m.os, m.os_detail) if x)) or "—"
    node_key = m.node.get("nodeKey") or ""

    details = "".join([
        kv("Propietario", esc(m.owner.get("email") or m.owner.get("name"))),
        kv("Nombre del dispositivo", f"<code>{esc(m.name)}</code>", m.name),
        kv("Nombre en el sistema", esc(m.hostname) or "—"),
        kv("Sistema operativo", os_text),
        kv("Versión de Tailscale", version),
        kv("Arquitectura", esc(hi.get("GoArch") or hi.get("Machine") or "—")),
        kv("Registrado mediante", esc(m.register)),
        kv("ID", f"<code>{esc(m.id)}</code>"),
        kv("Clave de nodo", f'<code class="trunc">{esc(node_key)}</code>', node_key) if node_key else "",
        kv("Creado", time_tag(m.created)),
        kv("Última conexión", "Conectado ahora" if m.online else f"{esc(relative(m.last_seen))} · {time_tag(m.node.get('lastSeen'))}"),
        kv("Caducidad de la clave", expiry),
    ])
    addresses = "".join([
        kv("IPv4", f"<code>{esc(m.ipv4)}</code>", m.ipv4) if m.ipv4 else "",
        kv("IPv6", f"<code>{esc(m.ipv6)}</code>", m.ipv6) if m.ipv6 else "",
        kv("Nombre corto", f"<code>{esc(m.name)}</code>", m.name),
        kv("Nombre MagicDNS", f"<code>{esc(m.fqdn)}</code>", m.fqdn) if m.fqdn else "",
    ])

    route_rows = []
    if m.exit_node:
        route_rows.append(("Nodo de salida", "0.0.0.0/0, ::/0", m.exit_node_approved))
    route_rows += [("Subred", r, r in m.approved) for r in m.subnets]
    if route_rows and session.get("admin"):
        checks = "".join(
            f'<label class="check route"><input type="checkbox" name="route" value="{esc(v)}" {"checked" if ok else ""}>'
            f'<span><b>{esc(kind)}</b><code>{esc(r)}</code></span></label>'
            for kind, r, ok, v in (
                [("Nodo de salida", "0.0.0.0/0, ::/0", m.exit_node_approved, "exit")] if m.exit_node else []) +
            [("Subred", r, r in m.approved, r) for r in m.subnets])
        routes_html = f"""
        <form method="post" action="{BASE}/machines/{m.id}/routes" class="routes-form">{csrf_input(session)}
          <p class="muted small">Marca las rutas que el dispositivo puede ofrecer a la red.</p>
          {checks}
          <div><button class="btn small primary" type="submit">Guardar rutas</button></div>
        </form>"""
    elif route_rows:
        approved_badge = '<span class="badge">Aprobada</span>'
        pending_badge = '<span class="badge pending">Pendiente de aprobación</span>'
        routes = "".join(
            f'<tr><td>{esc(kind)}</td><td><code>{esc(r)}</code></td>'
            f'<td>{approved_badge if ok else pending_badge}</td></tr>'
            for kind, r, ok in route_rows)
        routes_html = f"""
        <table class="simple"><thead><tr><th>Tipo</th><th>Ruta</th><th>Estado</th></tr></thead><tbody>{routes}</tbody></table>
        <p class="muted small">Las rutas anunciadas las aprueba un administrador.</p>"""
    else:
        routes_html = """<p class="muted">Este dispositivo no anuncia subredes ni funciona como nodo de salida.
          Para anunciarlas: <code>tailscale set --advertise-routes=10.0.0.0/24</code> o
          <code>tailscale set --advertise-exit-node</code>.</p>"""

    endpoints = "".join(f"<li><code>{esc(e)}</code></li>" for e in sorted(m.endpoints, key=_endpoint_key))
    connection = "".join([
        kv("Relay DERP preferido", esc(m.derp) or "—"),
        kv("Endpoints", f'<ul class="plain">{endpoints}</ul>' if endpoints else "—"),
    ])

    body = f"""
    <nav class="crumbs"><a href="{BASE}/machines">Dispositivos</a><span>/</span>{esc(m.name)}</nav>
    {flash_html(flash)}
    <div class="page-head machine-head">
      <div>
        <h1>{esc(m.name)}</h1>
        <div class="meta">{status_html(m)}<span class="muted">{esc(m.owner.get("email") or m.owner.get("name"))}</span>{m.badges()}</div>
      </div>
      <div class="head-actions">
        <button class="btn" type="button" data-open="rename-{m.id}">Editar nombre</button>
        {machine_menu(m, session, detail=True)}
      </div>
    </div>
    <div class="grid-2">
      <section class="card"><h2>Detalles del dispositivo</h2><dl class="kvs">{details}</dl></section>
      <div>
        <section class="card"><h2>Direcciones</h2><dl class="kvs">{addresses}</dl></section>
        <section class="card"><h2>Rutas</h2>{routes_html}</section>
        <section class="card"><h2>Conexión</h2><dl class="kvs">{connection}</dl>
          <p class="muted small">Endpoints: direcciones que el dispositivo anuncia para conexiones directas.
            Si no hay conexión directa, el tráfico pasa por el relay DERP.</p></section>
      </div>
    </div>
    {machine_dialogs(m, session, f"machines/{m.id}")}"""
    return layout(m.name, "machines", body, session, ctx)


def _endpoint_key(ep: str):
    host = ep.rsplit(":", 1)[0].strip("[]")
    try:
        ip = ipaddress.ip_address(host)
        return (0 if ip.is_global else 1, ip.version, ep)
    except ValueError:
        return (2, 0, ep)


# -----------------------------------------------------------------------------
# Añadir dispositivo
# -----------------------------------------------------------------------------

def add_page(session: dict, ctx: dict) -> str:
    url = ctx["public_url"]
    login = f"tailscale up --login-server={url}"

    def code(cmd: str) -> str:
        return f'<div class="code"><code>{esc(cmd)}</code>{copy_btn(cmd)}</div>'

    panels = {
        "linux": ("Linux", f"""
          <ol class="steps">
            <li>Instala Tailscale:{code("curl -fsSL https://tailscale.com/install.sh | sh")}</li>
            <li>Conéctalo a esta VPN. Se abrirá un enlace para iniciar sesión con tu usuario:{code("sudo " + login)}</li>
          </ol>"""),
        "windows": ("Windows", f"""
          <ol class="steps">
            <li>Descarga e instala Tailscale desde
              <a class="link" href="https://tailscale.com/download/windows" target="_blank" rel="noopener">tailscale.com/download/windows</a>.</li>
            <li>Abre PowerShell y ejecuta (se abrirá el navegador para iniciar sesión):{code(login)}</li>
          </ol>
          <p class="muted small">Guía de Headscale para Windows: <a class="link" href="{esc(url)}/windows" target="_blank">{esc(url)}/windows</a></p>"""),
        "macos": ("macOS", f"""
          <ol class="steps">
            <li>Instala Tailscale desde
              <a class="link" href="https://tailscale.com/download/mac" target="_blank" rel="noopener">tailscale.com/download/mac</a>.</li>
            <li>En Terminal (se abrirá el navegador para iniciar sesión):{code("/Applications/Tailscale.app/Contents/MacOS/Tailscale up --login-server=" + url)}</li>
          </ol>
          <p class="muted small">También desde el menú de la app. Guía de Headscale para Apple:
            <a class="link" href="{esc(url)}/apple" target="_blank">{esc(url)}/apple</a></p>"""),
        "ios": ("iOS", f"""
          <ol class="steps">
            <li>Instala Tailscale desde la App Store.</li>
            <li>Abre <b>Ajustes → Tailscale</b> y activa <b>Use Alternate Coordination Server</b>
              con esta URL:{code(url)}</li>
            <li>Abre la app de Tailscale e inicia sesión: se abrirá el login de tu cuenta.</li>
          </ol>
          <p class="muted small">Guía de Headscale para Apple:
            <a class="link" href="{esc(url)}/apple" target="_blank">{esc(url)}/apple</a></p>"""),
        "android": ("Android", f"""
          <ol class="steps">
            <li>Instala Tailscale desde Google Play.</li>
            <li>En la pantalla de inicio de sesión, abre el menú <b>⋮</b> y elige
              <b>Use an alternate server</b> (o <b>Change server</b>). Escribe:{code(url)}</li>
            <li>Inicia sesión con tu usuario.</li>
          </ol>"""),
    }
    tabs = "".join(f'<button type="button" role="tab" data-tab="{k}" class="{"active" if k == "linux" else ""}">{label}</button>'
                   for k, (label, _) in panels.items())
    bodies = "".join(f'<div class="tab-panel" data-panel="{k}" {"" if k == "linux" else "hidden"}>{content}</div>'
                     for k, (_, content) in panels.items())

    body = page_head("Añadir dispositivo", "Instala Tailscale y conéctalo a esta VPN en lugar de a la de Tailscale.") + f"""
    <section class="card">
      <div class="ostabs" role="tablist">{tabs}</div>
      {bodies}
    </section>
    <section class="card">
      <h2>¿Servidores o dispositivos sin navegador?</h2>
      <p class="muted">Genera una clave de autenticación en
        <a class="link" href="{BASE}/settings/keys">Ajustes → Claves</a> y úsala así:</p>
      {code(login + " --authkey=<tu-clave>")}
    </section>"""
    return layout("Añadir dispositivo", "machines", body, session, ctx)


# -----------------------------------------------------------------------------
# DNS
# -----------------------------------------------------------------------------

def dns_form(session: dict, ctx: dict, dns: dict, error: str) -> str:
    """Formulario de DNS para admins (ver headscale.apply_dns)."""
    if not ctx.get("dns_editable"):
        return f"""<div class="notice error">{esc(ctx.get("dns_reason") or "La edición de DNS no está disponible.")}</div>"""
    split = "\n".join(f"{d}: {', '.join(v)}" for d, v in (dns.get("split") or {}).items())
    err = f'<div class="notice error" role="alert">{esc(error)}</div>' if error else ""
    return f"""{err}
    <form method="post" action="{BASE}/dns" class="card dns-form" data-busy="Aplicando… Headscale se está reiniciando">
      {csrf_input(session)}
      <h2>Editar DNS</h2>
      <label class="check"><input type="checkbox" name="magic_dns" value="1" {"checked" if dns.get("magic_dns") else ""}>
        <span><b>MagicDNS</b><span class="muted">Llegar a los dispositivos por su nombre.</span></span></label>
      <label class="check"><input type="checkbox" name="override_local_dns" value="1" {"checked" if dns.get("override_local_dns") else ""}>
        <span><b>Usar estos servidores de nombres en los dispositivos</b>
          <span class="muted">Sustituye el DNS local de cada dispositivo por los de abajo ("Override local DNS").</span></span></label>
      <label class="field">Nombre de la red (base_domain)
        <input name="base_domain" value="{esc(dns.get("base_domain"))}" required spellcheck="false">
        <span class="muted small">Tiene que ser distinto del dominio del servidor ({esc(ctx.get("server_host", ""))}).</span></label>
      <label class="field">Servidores de nombres globales
        <textarea name="nameservers" rows="3" spellcheck="false" placeholder="1.1.1.1&#10;https://dns.nextdns.io/abc123">{esc(chr(10).join(dns.get("nameservers") or []))}</textarea>
        <span class="muted small">Uno por línea: IP o resolvedor DoH (https://…).</span></label>
      <label class="field">Split DNS
        <textarea name="split" rows="3" spellcheck="false" placeholder="empresa.lan: 10.0.0.53, 10.0.0.54">{esc(split)}</textarea>
        <span class="muted small">Una línea por dominio: <code>dominio: servidor, servidor</code>.</span></label>
      <label class="field">Dominios de búsqueda
        <textarea name="search_domains" rows="2" spellcheck="false" placeholder="empresa.lan">{esc(chr(10).join(dns.get("search_domains") or []))}</textarea>
        <span class="muted small">Uno por línea.</span></label>
      <div class="form-foot">
        <p class="muted small">Al guardar, Headscale valida la configuración y se reinicia (unos segundos sin
          servicio). Si algo falla, se restaura la anterior.</p>
        <button class="btn primary" type="submit">Guardar y aplicar</button>
      </div>
    </form>"""


def dns_page(session: dict, ctx: dict, dns: dict, machines: list[Machine], error: str = "", flash: str = "") -> str:
    base = dns.get("base_domain") or ""
    magic = dns.get("magic_dns")
    ns = "".join(f"<li><code>{esc(n)}</code></li>" for n in dns.get("nameservers") or [])
    search = "".join(f"<li><code>{esc(d)}</code></li>" for d in dns.get("search_domains") or [])
    names = "".join(
        f'<tr><td>{esc(m.name)}</td><td><code>{esc(m.fqdn)}</code></td><td>{copy_btn(m.fqdn)}</td></tr>'
        for m in machines if m.fqdn)
    admin = session.get("admin")
    note = "" if admin else " Sólo un administrador puede cambiarla."

    body = page_head("DNS", f"La configuración DNS es común a toda la VPN.{note}") + flash_html(flash) + f"""
    <section class="card">
      <h2>Nombre de la red</h2>
      <p class="muted">Cada dispositivo tiene un nombre dentro de este dominio.</p>
      <div class="code"><code>{esc(base) or "—"}</code>{copy_btn(base) if base else ""}</div>
    </section>
    <section class="card">
      <div class="card-title-row"><h2>MagicDNS</h2>
        <span class="badge {"ok" if magic else ""}">{"Activado" if magic else "Desactivado"}</span></div>
      <p class="muted">Con MagicDNS puedes llegar a tus dispositivos por su nombre (p. ej.
        <code>ssh usuario@mi-portatil</code>) en lugar de por su IP.</p>
      {"<table class='simple'><thead><tr><th>Dispositivo</th><th>Nombre completo</th><th></th></tr></thead><tbody>" + names + "</tbody></table>" if names and magic else ""}
    </section>
    <div class="grid-2">
      <section class="card"><h2>Servidores de nombres</h2>
        {f'<ul class="plain">{ns}</ul>' if ns else '<p class="muted">Los del sistema de cada dispositivo.</p>'}</section>
      <section class="card"><h2>Dominios de búsqueda</h2>
        {f'<ul class="plain">{search}</ul>' if search else '<p class="muted">Ninguno.</p>'}</section>
    </div>
    {split_html(dns)}
    {dns_form(session, ctx, dns, error) if admin else ""}"""
    return layout("DNS", "dns", body, session, ctx)


def split_html(dns: dict) -> str:
    split = dns.get("split") or {}
    if not split:
        return ""
    rows = "".join(f"<tr><td><code>{esc(d)}</code></td><td>{esc(', '.join(v))}</td></tr>" for d, v in split.items())
    return f"""<section class="card"><h2>Split DNS</h2>
      <table class="simple"><thead><tr><th>Dominio</th><th>Servidores</th></tr></thead><tbody>{rows}</tbody></table></section>"""


# -----------------------------------------------------------------------------
# Ajustes
# -----------------------------------------------------------------------------

def settings_layout(title: str, sub: str, content: str, session: dict, ctx: dict) -> str:
    nav = "".join(f'<a href="{BASE}/settings/{k}" class="{"active" if sub == k else ""}">{label}</a>'
                  for k, label in (("general", "General"), ("keys", "Claves")))
    body = f"""
    <div class="page-head"><div><h1>Ajustes</h1></div></div>
    <div class="settings">
      <nav class="side">{nav}</nav>
      <div class="settings-main">{content}</div>
    </div>"""
    return layout(title, "settings", body, session, ctx)


def general_page(session: dict, ctx: dict) -> str:
    role = "Administrador" if session.get("admin") else "Usuario"
    if session.get("kind") == "apikey":
        role = "Administrador (sesión con API key de Headscale)"
    groups = ", ".join(session.get("groups") or []) or "—"
    content = f"""
      <section class="card">
        <h2>Cuenta</h2>
        <div class="account">
          <span class="avatar big">{initials(session)}</span>
          <div><b>{esc(session.get("name") or session.get("username"))}</b>
            <div class="muted">{esc(session.get("email"))}</div></div>
        </div>
        <dl class="kvs">
          {kv("Usuario", f"<code>{esc(session.get('username'))}</code>")}
          {kv("Rol", esc(role))}
          {kv("Grupos", esc(groups))}
        </dl>
        {f'<a class="btn" href="{esc(ctx["public_url"])}/authentik/if/user/#/settings">Gestionar cuenta y contraseña</a>'
          if ctx.get("authentik") and session.get("kind") != "apikey" else ""}
      </section>
      <section class="card">
        <h2>Apariencia</h2>
        <p class="muted">Se guarda en este navegador.</p>
        <div class="segmented theme-choice" role="radiogroup" aria-label="Tema">
          <button type="button" data-theme-set="system">Sistema</button>
          <button type="button" data-theme-set="dark">Oscuro</button>
          <button type="button" data-theme-set="light">Claro</button>
        </div>
      </section>
      <section class="card">
        <h2>Sesión</h2>
        <p class="muted">Cierra la sesión aquí y en el resto de aplicaciones de la VPN.</p>
        <form method="post" action="{BASE}/logout">{csrf_input(session)}<button class="btn" type="submit">Cerrar sesión</button></form>
      </section>"""
    return settings_layout("Ajustes", "general", content, session, ctx)


def key_state(k: dict) -> tuple[str, str]:
    exp = parse_time(k.get("expiration"))
    if exp is not None and exp <= datetime.now(timezone.utc):
        return "Caducada", "muted"
    if k.get("used") and not k.get("reusable"):
        return "Usada", "muted"
    return "Activa", "ok"


def keys_page(session: dict, ctx: dict, keys: list[dict] | None, flash: str, new_key: dict | None = None,
              users: list[dict] | None = None, apikeys: list[dict] | None = None, own_prefix: str = "",
              new_apikey: str = "") -> str:
    url = ctx["public_url"]
    admin = session.get("admin")
    by_id = {str(u["id"]): u for u in users or []}
    new_html = ""
    if new_key:
        cmd = f"tailscale up --login-server={url} --authkey={new_key['key']}"
        new_html = f"""
      <section class="card keybox">
        <h2>Clave generada</h2>
        <p>Cópiala ahora: <b>no se volverá a mostrar</b>.</p>
        <div class="code"><code>{esc(new_key["key"])}</code>{copy_btn(new_key["key"])}</div>
        <p class="muted small">Para conectar un dispositivo con ella:</p>
        <div class="code"><code>{esc(cmd)}</code>{copy_btn(cmd)}</div>
      </section>"""

    if keys is None and not admin:
        table = '<p class="muted">Las claves se asocian a tu usuario de la VPN, que se crea la primera vez que conectas un dispositivo iniciando sesión en el navegador. Conecta uno desde <a class="link" href="' + BASE + '/add">Añadir dispositivo</a>.</p>'
        can_create = False
    elif not keys:
        table = '<p class="muted">Todavía no has generado ninguna clave.</p>'
        can_create = True if not admin else bool(users)
    else:
        can_create = True
        rows = []
        for k in sorted(keys or [], key=lambda k: k.get("createdAt") or "", reverse=True):
            state, cls = key_state(k)
            kind = ["Reutilizable" if k.get("reusable") else "Un solo uso"]
            if k.get("ephemeral"):
                kind.append("Efímera")
            revoke = ""
            if state == "Activa":
                revoke = f"""<form method="post" action="{BASE}/keys/{esc(k['id'])}/revoke">{csrf_input(session)}
                  <button class="btn small" type="submit">Revocar</button></form>"""
            owner = ""
            if admin:
                u = by_id.get(str((k.get("user") or {}).get("id")), k.get("user") or {})
                owner = f"<td>{esc(user_label(u))}</td>"
            rows.append(f"""<tr>
              <td><code>{esc(k.get("key"))}</code></td>{owner}
              <td>{esc(" · ".join(kind))}</td>
              <td>{time_tag(k.get("createdAt"))}</td>
              <td>{time_tag(k.get("expiration"))}</td>
              <td><span class="badge {cls}">{esc(state)}</span></td>
              <td class="actions">{revoke}</td></tr>""")
        table = f"""<div class="table-wrap"><table class="simple">
          <thead><tr><th>Clave</th>{"<th>Usuario</th>" if admin else ""}<th>Tipo</th><th>Creada</th><th>Caduca</th><th>Estado</th><th></th></tr></thead>
          <tbody>{"".join(rows)}</tbody></table></div>"""

    create_btn = '<button class="btn primary" type="button" data-open="new-key">Generar clave</button>' if can_create else ""
    content = flash_html(flash) + new_html + f"""
      <section class="card">
        <div class="card-title-row"><h2>Claves de autenticación</h2>{create_btn}</div>
        <p class="muted">Permiten conectar dispositivos a la VPN sin iniciar sesión en ellos: servidores,
          contenedores o equipos sin navegador. Los dispositivos quedan a nombre del usuario de la clave.</p>
        {table}
      </section>
      <dialog id="new-key">
        <form method="post" action="{BASE}/keys">{csrf_input(session)}
          <h3>Generar clave de autenticación</h3>
          {user_select(users) if admin else ""}
          <label class="check"><input type="checkbox" name="reusable" value="1">
            <span><b>Reutilizable</b><span class="muted">Sirve para varios dispositivos. Si no, se invalida tras el primero.</span></span></label>
          <label class="check"><input type="checkbox" name="ephemeral" value="1">
            <span><b>Efímera</b><span class="muted">Los dispositivos se quitan solos al desconectarse. Para contenedores y CI.</span></span></label>
          <label class="field">Caducidad
            <select name="days">
              <option value="1">1 día</option><option value="7">7 días</option>
              <option value="30">30 días</option><option value="90" selected>90 días</option>
            </select></label>
          <div class="dialog-actions"><button type="button" class="btn" data-close>Cancelar</button>
            <button class="btn primary" type="submit">Generar clave</button></div>
        </form>
      </dialog>"""
    if admin:
        content += apikeys_section(session, apikeys or [], own_prefix, new_apikey)
    return settings_layout("Claves", "keys", content, session, ctx)


def user_select(users: list[dict] | None, selected: str = "") -> str:
    opts = "".join(
        f'<option value="{esc(u["id"])}" {"selected" if str(u["id"]) == selected else ""}>{esc(user_label(u))}</option>'
        for u in sorted(users or [], key=lambda u: user_label(u).lower()))
    return f'<label class="field">Usuario<select name="user_id" required>{opts}</select></label>'


def apikeys_section(session: dict, apikeys: list[dict], own_prefix: str, new_apikey: str) -> str:
    new_html = ""
    if new_apikey:
        new_html = f"""
      <div class="keybox inline">
        <p><b>API key creada.</b> Cópiala ahora: no se volverá a mostrar.</p>
        <div class="code"><code>{esc(new_apikey)}</code>{copy_btn(new_apikey)}</div>
      </div>"""
    rows = []
    now = datetime.now(timezone.utc)
    for k in sorted(apikeys, key=lambda k: k.get("createdAt") or "", reverse=True):
        exp = parse_time(k.get("expiration"))
        active = exp is None or exp > now
        own = own_prefix and own_prefix in (k.get("prefix") or "")
        state = ('<span class="badge ok">Activa</span>' if active else '<span class="badge muted">Caducada</span>')
        if own:
            state += ' <span class="badge">En uso por Mi VPN</span>'
        action = ""
        if active and not own:
            action = f"""<form method="post" action="{BASE}/apikeys/{esc(k['id'])}/expire">{csrf_input(session)}
              <button class="btn small" type="submit">Caducar</button></form>"""
        rows.append(f"""<tr><td><code>{esc(k.get("prefix"))}</code></td><td>{time_tag(k.get("createdAt"))}</td>
          <td>{time_tag(k.get("expiration"), "Nunca")}</td><td>{time_tag(k.get("lastSeen"), "Nunca")}</td>
          <td>{state}</td><td class="actions">{action}</td></tr>""")
    table = (f"""<div class="table-wrap"><table class="simple"><thead><tr><th>Prefijo</th><th>Creada</th><th>Caduca</th>
        <th>Último uso</th><th>Estado</th><th></th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>"""
             if rows else '<p class="muted">No hay API keys.</p>')
    return f"""
      <section class="card">
        <div class="card-title-row"><h2>API keys</h2>
          <button class="btn" type="button" data-open="new-apikey">Crear API key</button></div>
        <p class="muted">Dan acceso completo a la API de Headscale, como un administrador. Úsalas para
          automatizar; no las compartas.</p>
        {new_html}{table}
      </section>
      <dialog id="new-apikey">
        <form method="post" action="{BASE}/apikeys">{csrf_input(session)}
          <h3>Crear API key</h3>
          <label class="field">Caducidad
            <select name="days"><option value="30">30 días</option><option value="90" selected>90 días</option>
              <option value="365">365 días</option></select></label>
          <div class="dialog-actions"><button type="button" class="btn" data-close>Cancelar</button>
            <button class="btn primary" type="submit">Crear</button></div>
        </form>
      </dialog>"""


def message_page(title: str, text: str) -> str:
    """Página suelta, sin sesión (errores de login)."""
    return f"""<!doctype html>
<html lang="es" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · Headscale Dashboard</title>
<script src="{BASE}/static/theme.js?v={V}"></script><link rel="stylesheet" href="{BASE}/static/style.css?v={V}"></head>
<body><main class="container"><section class="card narrow">
<div class="empty-icon">{LOGO}</div><h1>{esc(title)}</h1><p class="muted">{esc(text)}</p>
<p><a class="btn primary" href="{BASE}/">Volver</a></p></section></main></body></html>"""
