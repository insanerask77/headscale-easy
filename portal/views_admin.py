"""Páginas de administración de Mi VPN (sólo admins) y pantalla de login.

Sustituyen a lo que antes hacía Headplane: usuarios de Headscale y la
política de Control de acceso. app.py comprueba el rol antes de llamarlas."""

from __future__ import annotations

from views import (BASE, LOGO, V, csrf_input, esc, flash_html, layout, page_head, time_tag, user_label)

PROVIDERS = {"oidc": "Inicio de sesión (OIDC)", "cli": "Local"}


# -----------------------------------------------------------------------------
# Usuarios
# -----------------------------------------------------------------------------

def users_page(session: dict, ctx: dict, users: list[dict], nodes: list[dict], flash: str,
               error: str = "") -> str:
    counts: dict[str, int] = {}
    online: dict[str, int] = {}
    for n in nodes:
        uid = str((n.get("user") or {}).get("id"))
        counts[uid] = counts.get(uid, 0) + 1
        online[uid] = online.get(uid, 0) + (1 if n.get("online") else 0)

    rows, dialogs = [], []
    for u in sorted(users, key=lambda u: user_label(u).lower()):
        uid = esc(u["id"])
        n = counts.get(str(u["id"]), 0)
        provider = PROVIDERS.get((u.get("provider") or "").lower(), u.get("provider") or "Local")
        delete = (f'<button type="button" class="danger" data-open="del-user-{uid}">Eliminar…</button>'
                  if n == 0 else '<span class="muted small menu-note">Para eliminarlo, quita antes sus dispositivos</span>')
        rows.append(f"""
        <tr data-search="{esc((user_label(u) + ' ' + (u.get('name') or '')).lower())}">
          <td><div class="user-cell"><span class="avatar sm">{esc((u.get("displayName") or u.get("name") or "?")[:1].upper())}</span>
            <div><b>{esc(u.get("displayName") or u.get("name"))}</b>
            <div class="muted small">{esc(u.get("email") or u.get("name"))}</div></div></div></td>
          <td><code>{esc(u.get("name"))}</code></td>
          <td>{esc(provider)}</td>
          <td><a class="link" href="{BASE}/machines?owner={uid}">{n} dispositivo{"s" if n != 1 else ""}</a>
            {f'<span class="muted small"> · {online.get(str(u["id"]), 0)} conectados</span>' if n else ""}</td>
          <td class="hide-sm">{time_tag(u.get("createdAt"))}</td>
          <td class="actions">
            <details class="dropdown">
              <summary class="icon-btn" aria-label="Acciones">···</summary>
              <div class="dropdown-body right">
                <a href="{BASE}/machines?owner={uid}">Ver dispositivos</a>
                <a href="{BASE}/settings/keys?user={uid}">Generar clave para este usuario</a>
                <button type="button" data-open="ren-user-{uid}">Renombrar…</button>
                <hr>{delete}
              </div>
            </details>
          </td>
        </tr>""")
        dialogs.append(f"""
    <dialog id="ren-user-{uid}">
      <form method="post" action="{BASE}/users/{uid}/rename">{csrf_input(session)}
        <h3>Renombrar a {esc(u.get("name"))}</h3>
        <p class="muted">Es el nombre del usuario en Headscale.
          {"Si entra por OIDC, su proveedor puede volver a cambiarlo en el siguiente inicio de sesión." if (u.get("provider") or "").lower() == "oidc" else ""}</p>
        <label class="field">Nombre<input name="name" value="{esc(u.get("name"))}" required autocomplete="off" spellcheck="false"></label>
        <div class="dialog-actions"><button type="button" class="btn" data-close>Cancelar</button>
          <button class="btn primary" type="submit">Guardar</button></div>
      </form>
    </dialog>
    <dialog id="del-user-{uid}">
      <form method="post" action="{BASE}/users/{uid}/delete">{csrf_input(session)}
        <h3>¿Eliminar a {esc(u.get("name"))}?</h3>
        <p class="muted">Se borra de Headscale. Su cuenta de inicio de sesión (Authentik) no se toca:
          si vuelve a entrar, se creará de nuevo.</p>
        <div class="dialog-actions"><button type="button" class="btn" data-close>Cancelar</button>
          <button class="btn danger-solid" type="submit">Eliminar</button></div>
      </form>
    </dialog>""")

    alta = (f'<a class="btn primary" href="{esc(ctx["public_url"])}/alta-usuario">Dar de alta una cuenta</a>'
            if ctx.get("authentik") else "")
    head = page_head(
        "Usuarios",
        "Usuarios de la VPN. Se crean solos la primera vez que alguien conecta un dispositivo iniciando sesión."
        + (" Las cuentas (usuario y contraseña) se dan de alta en Authentik." if ctx.get("authentik") else ""),
        f'<div class="head-actions"><button class="btn" type="button" data-open="new-user">Crear usuario local</button>{alta}</div>')
    err = f'<div class="notice error" role="alert">{esc(error)}</div>' if error else ""
    body = head + flash_html(flash) + err + f"""
    <div class="toolbar">
      <input type="search" class="search" placeholder="Buscar usuarios…" data-filter aria-label="Buscar usuarios">
    </div>
    <section class="card flush">
      <div class="table-wrap">
      <table class="machines users">
        <thead><tr><th>Usuario</th><th>Nombre en Headscale</th><th>Tipo</th><th>Dispositivos</th><th class="hide-sm">Creado</th><th></th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
      </div>
      <p class="no-results muted" hidden>Ningún usuario coincide con la búsqueda.</p>
    </section>
    <dialog id="new-user">
      <form method="post" action="{BASE}/users">{csrf_input(session)}
        <h3>Crear usuario local</h3>
        <p class="muted">Un usuario de Headscale sin inicio de sesión, para servidores o equipos que se conectan
          con claves de autenticación. Para personas, mejor una cuenta de Authentik.</p>
        <label class="field">Nombre<input name="name" required placeholder="servidores" autocomplete="off" spellcheck="false"></label>
        <label class="field">Nombre visible (opcional)<input name="display_name" autocomplete="off"></label>
        <div class="dialog-actions"><button type="button" class="btn" data-close>Cancelar</button>
          <button class="btn primary" type="submit">Crear</button></div>
      </form>
    </dialog>
    {"".join(dialogs)}"""
    return layout("Usuarios", "users", body, session, ctx)


# -----------------------------------------------------------------------------
# Control de acceso (política ACL)
# -----------------------------------------------------------------------------

ISOLATION = """{
  // Cada usuario sólo alcanza sus propios dispositivos
  "acls": [
    {"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:self:*"]}
  ]
}"""


def acl_page(session: dict, ctx: dict, policy: dict, flash: str, draft: str | None = None,
             result: tuple[str, str] | None = None) -> str:
    text = draft if draft is not None else (policy.get("policy") or "")
    notice = ""
    if result:
        kind, msg = result
        notice = f'<div class="notice {kind}" role="status"><pre class="plainpre">{esc(msg)}</pre></div>'
    updated = policy.get("updatedAt")
    head = page_head(
        "Control de acceso",
        "Quién puede conectarse con qué dentro de la VPN. Política en formato HuJSON (JSON con comentarios), "
        "igual que en Tailscale.")
    body = head + flash_html(flash) + notice + f"""
    <form method="post" action="{BASE}/acl" class="card acl">
      {csrf_input(session)}
      <div class="card-title-row"><h2>Política</h2>
        <span class="muted small">{("Actualizada " + time_tag(updated)) if updated else "Sin política: todos alcanzan a todos"}</span></div>
      <textarea name="policy" class="code-editor" spellcheck="false" rows="22" data-tab-indent>{esc(text)}</textarea>
      <div class="form-foot">
        <button class="btn" type="submit" name="action" value="check">Comprobar</button>
        <button class="btn primary" type="submit" name="action" value="save">Guardar</button>
      </div>
    </form>
    <section class="card">
      <h2>Referencia rápida</h2>
      <dl class="kvs">
        <div class="kv"><dt><code>autogroup:member</code></dt><dd>Dispositivos de cualquier usuario (sin tags).</dd></div>
        <div class="kv"><dt><code>autogroup:self</code></dt><dd>Los dispositivos del mismo usuario que el origen.</dd></div>
        <div class="kv"><dt><code>autogroup:internet</code></dt><dd>Salida a internet por un nodo de salida.</dd></div>
        <div class="kv"><dt><code>tag:nombre</code></dt><dd>Dispositivos con ese tag; el tag necesita dueño en <code>tagOwners</code>.</dd></div>
        <div class="kv"><dt><code>usuario@</code></dt><dd>Los dispositivos de un usuario concreto.</dd></div>
      </dl>
      <p class="muted small">Aislamiento por usuario (la política por defecto del instalador):</p>
      <div class="code"><code class="pre">{esc(ISOLATION)}</code></div>
      <p class="muted small">Documentación: <a class="link" href="https://headscale.net/stable/ref/acls/" target="_blank" rel="noopener">headscale.net/stable/ref/acls</a></p>
    </section>"""
    return layout("Control de acceso", "acl", body, session, ctx)


# -----------------------------------------------------------------------------
# Login (con API key y/o SSO)
# -----------------------------------------------------------------------------

def login_page(sso: bool, apikey: bool, error: str = "") -> str:
    err = f'<div class="notice error" role="alert">{esc(error)}</div>' if error else ""
    sso_html = (f'<a class="btn primary wide" href="{BASE}/login/sso">Iniciar sesión</a>' if sso else "")
    sep = '<div class="sep"><span>o</span></div>' if sso and apikey else ""
    key_html = ""
    if apikey:
        key_html = f"""
      <form method="post" action="{BASE}/login/apikey" class="login-key">
        <label class="field">API key de Headscale<input name="api_key" type="password" required
          placeholder="hskey-api-…" autocomplete="off" spellcheck="false"></label>
        <button class="btn wide" type="submit">Entrar con API key</button>
        <p class="muted small">Da acceso como administrador. Genera una con
          <code>docker exec headscale headscale apikeys create</code>.</p>
      </form>"""
    return f"""<!doctype html>
<html lang="es" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Iniciar sesión · Headscale Dashboard</title>
<link rel="icon" href="{BASE}/static/favicon.svg?v={V}" type="image/svg+xml">
<script src="{BASE}/static/theme.js?v={V}"></script><link rel="stylesheet" href="{BASE}/static/style.css?v={V}"></head>
<body class="login-body"><main class="container">
<section class="card narrow login">
  <div class="empty-icon">{LOGO}</div>
  <h1>Headscale Dashboard</h1>
  <p class="muted">Inicia sesión para gestionar tu VPN.</p>
  {err}{sso_html}{sep}{key_html}
</section></main></body></html>"""
