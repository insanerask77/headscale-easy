"""Shared UI building blocks for Headscale Easy: layout (sidebar + content,
modelled on the Tailscale admin console), icons, badges and formatting.

Every piece of user text goes through esc() and every UI string through _().
"""

from __future__ import annotations

import hashlib
import html
from datetime import datetime, timezone
from pathlib import Path

from i18n import LANGUAGES, _, get_lang, ngettext
from version import PROJECT_URL, SPONSOR_URL, VERSION

BASE = "/admin"


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _asset_version() -> str:
    """Hash of static/: goes into CSS/JS URLs so any change busts the cache."""
    h = hashlib.sha256()
    for f in sorted((Path(__file__).parent / "static").rglob("*")):
        if f.is_file():
            h.update(f.read_bytes())
    return h.hexdigest()[:10]


V = _asset_version()


# -----------------------------------------------------------------------------
# Icons (paths adapted from Lucide, ISC License — see THIRD_PARTY_NOTICES.md)
# -----------------------------------------------------------------------------

_ICONS = {
    "network": '<rect x="16" y="16" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="9" y="2" width="6" height="6" rx="1"/><path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3"/><path d="M12 12V8"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "settings": '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    "book": '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>',
    "coffee": '<path d="M10 2v2"/><path d="M14 2v2"/><path d="M16 8a1 1 0 0 1 1 1v8a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1h14a4 4 0 1 1 0 8h-1"/><path d="M6 2v2"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "filter": '<path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "update": '<circle cx="12" cy="12" r="10"/><path d="m16 12-4-4-4 4"/><path d="M12 16V8"/>',
    "more": '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    "menu": '<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    "moon": '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    "external": '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
}


def icon(name: str, cls: str = "") -> str:
    return (f'<svg class="icon {cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_ICONS[name]}</svg>')


LOGO = """<svg class="logo" viewBox="0 0 48 48" aria-hidden="true">
  <circle cx="8" cy="8" r="6"/><circle class="off" cx="24" cy="8" r="6"/><circle cx="40" cy="8" r="6"/>
  <circle cx="8" cy="24" r="6"/><circle cx="24" cy="24" r="6"/><circle cx="40" cy="24" r="6"/>
  <circle cx="8" cy="40" r="6"/><circle class="off" cx="24" cy="40" r="6"/><circle cx="40" cy="40" r="6"/>
</svg>"""


# -----------------------------------------------------------------------------
# Formatting
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
        return _("never")
    secs = (dt - datetime.now(timezone.utc)).total_seconds()
    if not future:
        secs = -secs
    if secs < 60:
        return _("in less than a minute") if future else _("just now")
    units = ((86400, lambda n: ngettext("{n} day", "{n} days", n)),
             (3600, lambda n: ngettext("{n} hour", "{n} hours", n)),
             (60, lambda n: ngettext("{n} minute", "{n} minutes", n)))
    for size, plural in units:
        if secs >= size:
            span = plural(int(secs // size))
            return _("in {span}", span=span) if future else _("{span} ago", span=span)
    return ""


def time_tag(value: str | None, empty: str | None = None, fmt: str = "long") -> str:
    """<time> with the UTC date; app.js renders it in the viewer's timezone
    and language (fmt: long = date and time, short = e.g. "May 19")."""
    dt = parse_time(value)
    if dt is None:
        return esc(empty if empty is not None else "—")
    return (f'<time datetime="{esc(dt.isoformat())}" data-local="{fmt}">'
            f'{esc(dt.strftime("%Y-%m-%d %H:%M UTC"))}</time>')


def initials(name: str) -> str:
    parts = [p for p in (name or "?").replace(".", " ").replace("@", " ").split() if p]
    return esc("".join(p[0] for p in parts[:2]).upper() or "?")


def user_label(u: dict) -> str:
    """Readable name of a Headscale user."""
    name = u.get("displayName") or u.get("name") or ""
    email = u.get("email") or ""
    return f"{name} ({email})" if email and email != name else name


# -----------------------------------------------------------------------------
# Small components
# -----------------------------------------------------------------------------

def badge(text: str, kind: str = "", tip: str = "") -> str:
    """kind: '' (grey), 'blue', 'orange', 'green', 'red'. A tip adds the (i)."""
    info = f' {icon("info", "i-xs")}' if tip else ""
    title = f' title="{esc(tip)}"' if tip else ""
    return f'<span class="badge {kind}"{title}>{esc(text)}{info}</span>'


def copy_btn(value: str, label: str | None = None) -> str:
    return (f'<button type="button" class="copy" data-copy="{esc(value)}" title="{esc(_("Copy"))}">'
            f'{esc(label or _("Copy"))}</button>')


def csrf_input(session: dict) -> str:
    return f'<input type="hidden" name="csrf" value="{esc(session["csrf"])}">'


def page_head(title: str, subtitle: str = "", actions: str = "", link: tuple[str, str] | None = None) -> str:
    more = (f' <a class="link" href="{esc(link[1])}" target="_blank" rel="noopener">{esc(link[0])}</a>'
            if link else "")
    return f"""
    <div class="page-head">
      <div><h1>{esc(title)}</h1>{f'<p class="muted">{subtitle}{more}</p>' if subtitle or more else ""}</div>
      {f'<div class="head-actions">{actions}</div>' if actions else ""}
    </div>"""


def notice(kind: str, text: str) -> str:
    return f'<div class="notice {kind}" role="status">{esc(text)}</div>'


def flash_html(code: str) -> str:
    messages = {
        "renamed": ("ok", _("Machine renamed.")),
        "removed": ("ok", _("Machine removed.")),
        "expired": ("ok", _("Machine key expired: the device must sign in again.")),
        "expiry-off": ("ok", _("Key expiry disabled.")),
        "expiry-on": ("ok", _("Key expiry enabled.")),
        "routes": ("ok", _("Routes updated.")),
        "tags": ("ok", _("Tags updated.")),
        "registered": ("ok", _("Machine registered.")),
        "key-revoked": ("ok", _("Key revoked.")),
        "user-created": ("ok", _("User created.")),
        "user-renamed": ("ok", _("User renamed.")),
        "user-deleted": ("ok", _("User deleted.")),
        "acl-saved": ("ok", _("Policy saved and applied.")),
        "dns-saved": ("ok", _("DNS saved. Headscale restarted with the new settings.")),
        "apikey-expired": ("ok", _("API key expired.")),
        "bad-name": ("error", _("Invalid name: lowercase letters, digits and dashes only (max. 63).")),
        "bad-user": ("error", _("Invalid user name: lowercase letters, digits, dots, dashes and @.")),
        "not-found": ("error", _("That item does not exist or is not yours.")),
        "forbidden": ("error", _("Only an admin can do that.")),
        "no-user": ("error", _("Connect a device first by signing in from it.")),
        "user-has-nodes": ("error", _("That user still has machines: remove them first.")),
        "apikey-own": ("error", _("Headscale Easy uses that API key: expiring it would cut its access to Headscale.")),
        "failed": ("error", _("Headscale rejected the operation. Please try again.")),
    }
    if code not in messages:
        return ""
    return notice(*messages[code])


# -----------------------------------------------------------------------------
# Layout
# -----------------------------------------------------------------------------

def _nav_item(path: str, label: str, active: str, key: str) -> str:
    return f'<a href="{BASE}/{path}" class="{"active" if active == key else ""}">{esc(label)}</a>'


def _nav_group(group_icon: str, label: str, items: list[tuple[str, str, str]], active: str) -> str:
    """Collapsible group (open when one of its items is active or by default)."""
    links = "".join(_nav_item(path, text, active, key) for key, path, text in items)
    return f"""
      <details class="nav-group" open>
        <summary>{icon(group_icon)}<span>{esc(label)}</span>{icon("chevron-down", "chev")}</summary>
        <div class="nav-items">{links}</div>
      </details>"""


def sidebar(active: str, session: dict, ctx: dict) -> str:
    admin = session.get("admin")
    groups = [_nav_group("network", _("Network"), [
        ("machines", "machines", _("Machines")),
        ("dns", "dns", _("DNS")),
    ], active)]
    if admin:
        groups.append(f"""
      <a class="nav-top {"active" if active == "users" else ""}" href="{BASE}/users">{icon("users")}<span>{esc(_("Users"))}</span></a>""")
        groups.append(_nav_group("lock", _("Access controls"), [
            ("acl", "acl", _("Policy editor")),
        ], active))
    groups.append(_nav_group("settings", _("Settings"), [
        ("general", "settings/general", _("General")),
        ("keys", "settings/keys", _("Keys")),
    ], active))
    groups.append(f"""
      <a class="nav-top" href="{PROJECT_URL}#readme" target="_blank" rel="noopener">{icon("book")}<span>{esc(_("Documentation"))}</span>{icon("external", "ext")}</a>
      <a class="nav-top" href="{SPONSOR_URL}" target="_blank" rel="noopener">{icon("coffee")}<span>{esc(_("Support the project"))}</span>{icon("external", "ext")}</a>""")

    name = session.get("name") or session.get("username") or _("Administrator")
    sub = session.get("email") or (_("API key session") if session.get("kind") == "apikey" else "")
    role = _("Admin") if admin else _("Member")
    langs = "".join(
        f'<button type="submit" name="lang" value="{code}" class="{"active" if get_lang() == code else ""}">{esc(label)}</button>'
        for code, label in LANGUAGES.items())
    return f"""
  <aside class="sidebar" id="sidebar">
    <div class="tailnet">
      <a class="brand" href="{BASE}/machines">{LOGO}<span class="tn-name" title="{esc(ctx.get('tailnet'))}">{esc(ctx.get("tailnet") or "Headscale Easy")}</span></a>
      <span class="plan">{esc(role)}</span>
    </div>
    <nav class="nav">{"".join(groups)}
    </nav>
    <div class="side-foot">
      <a class="made-by" href="{PROJECT_URL}" target="_blank" rel="noopener">Headscale Easy v{esc(VERSION)} · by Rafa Madolell</a>
      <details class="dropdown up usermenu">
        <summary class="user-card">
          <span class="avatar">{initials(name)}</span>
          <span class="uc-text"><b>{esc(name)}</b><span>{esc(sub)}</span></span>
        </summary>
        <div class="dropdown-body">
          <a href="{BASE}/settings/general">{esc(_("Settings"))}</a>
          <button type="button" data-theme-toggle>{icon("sun", "i-sun")}{icon("moon", "i-moon")} {esc(_("Toggle theme"))}</button>
          <form method="post" action="{BASE}/settings/language" class="lang-switch">{csrf_input(session)}{langs}</form>
          <hr>
          <form method="post" action="{BASE}/logout">{csrf_input(session)}<button type="submit">{esc(_("Log out"))}</button></form>
        </div>
      </details>
    </div>
  </aside>"""


def _head(title: str) -> str:
    return f"""<!doctype html>
<html lang="{get_lang()}" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} · Headscale Easy</title>
  <link rel="icon" href="{BASE}/static/favicon.svg?v={V}" type="image/svg+xml">
  <script src="{BASE}/static/theme.js?v={V}"></script>
  <link rel="stylesheet" href="{BASE}/static/style.css?v={V}">
</head>"""


def layout(title: str, active: str, body: str, session: dict, ctx: dict) -> str:
    return f"""{_head(title)}
<body data-copied="{esc(_("Copied"))}" data-working="{esc(_("Working"))}">
  <input type="checkbox" id="nav-toggle" class="nav-toggle" hidden>
  <header class="mobile-bar">
    <label for="nav-toggle" class="icon-btn" aria-label="{esc(_("Menu"))}">{icon("menu")}</label>
    <a class="brand" href="{BASE}/machines">{LOGO}<b>Headscale Easy</b></a>
  </header>
  <div class="shell">
    {sidebar(active, session, ctx)}
    <label for="nav-toggle" class="scrim" aria-hidden="true"></label>
    <main class="content">
{body}
    </main>
  </div>
  <script src="{BASE}/static/app.js?v={V}" defer></script>
</body>
</html>"""


def bare_page(title: str, body: str) -> str:
    """Page without the sidebar (login, errors)."""
    return f"""{_head(title)}
<body class="bare" data-copied="{esc(_("Copied"))}" data-working="{esc(_("Working"))}">
  <main class="bare-main">
{body}
    <p class="bare-foot"><a href="{PROJECT_URL}" target="_blank" rel="noopener">Headscale Easy</a> · by Rafa Madolell ·
      <a href="{SPONSOR_URL}" target="_blank" rel="noopener">{esc(_("Buy me a coffee"))}</a></p>
  </main>
  <script src="{BASE}/static/app.js?v={V}" defer></script>
</body>
</html>"""


def message_page(title: str, text: str) -> str:
    return bare_page(title, f"""
    <section class="card narrow center">
      <div class="big-logo">{LOGO}</div>
      <h1>{esc(title)}</h1>
      <p class="muted">{esc(text)}</p>
      <a class="btn primary" href="{BASE}/">{esc(_("Back"))}</a>
    </section>""")
