"""Admin-only pages (Users, Access controls) and the sign-in page.
app.py checks the role before rendering any of them."""

from __future__ import annotations

import acl_pages
import policy
from i18n import _, ngettext
from pages import dialog
from ui import (BASE, LOGO, badge, bare_page, csrf_input, esc, flash_html, icon, initials, layout, notice,
                page_head, time_tag, user_label)



# -----------------------------------------------------------------------------
# Users
# -----------------------------------------------------------------------------

signup_open = lambda: False  # noqa: E731 - app.py replaces it: is self-registration on?

def _password_fields() -> str:
    return (f'<label class="field">{esc(_("New password"))}<input name="password" type="password" required minlength="8" '
            f'autocomplete="new-password"></label>'
            f'<label class="check"><input type="checkbox" name="must_change" value="1" checked>'
            f'<span>{esc(_("They must choose another password when they sign in"))}</span></label>')


def _account_fields() -> str:
    roles = (("member", _("Member")), ("auditor", _("Auditor")), ("network_admin", _("Network admin")), ("admin", _("Admin")))
    options = "".join(f'<option value="{k}">{esc(v)}</option>' for k, v in roles)
    return (f'<p class="muted small">{esc(_("To let this person sign in, fill in the rest:"))}</p>'
            f'<label class="field">{esc(_("Email"))}<input name="email" type="email" autocomplete="off"></label>'
            f'<label class="field">{esc(_("Password"))}<input name="password" type="password" minlength="8" '
            f'autocomplete="new-password"></label>'
            f'<label class="field">{esc(_("Role"))}<select name="role">{options}</select></label>'
            f'<label class="check"><input type="checkbox" name="must_change" value="1" checked>'
            f'<span>{esc(_("They must choose another password when they sign in"))}</span></label>')


def users_page(session: dict, ctx: dict, users: list[dict], nodes: list[dict], flash: str, error: str = "",
               result: dict | None = None, signins: dict | None = None, extra: str = "") -> str:
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
        oidc = (u.get("provider") or "").lower() == "oidc"
        name = u.get("displayName") or u.get("name")
        delete = (f'<button type="button" class="danger" data-open="del-user-{uid}">{esc(_("Delete user…"))}</button>'
                  if n == 0 else f'<span class="menu-note">{esc(_("Remove their machines to delete this user"))}</span>')
        machines = ngettext("{n} machine", "{n} machines", n)
        rows.append(f"""
        <tr data-search="{esc((user_label(u) + ' ' + (u.get('name') or '')).lower())}">
          <td><div class="user-cell"><span class="avatar sm">{initials(name)}</span>
            <div><b>{esc(name)}</b><div class="owner">{esc(u.get("email") or u.get("name"))}</div></div></div></td>
          <td><code>{esc(u.get("name"))}</code></td>
          <td>{badge(_("SSO"), "blue") if oidc else badge(_("Local"))}</td>
          <td><a class="link" href="{BASE}/machines?owner={uid}">{esc(machines)}</a>
            {f'<span class="muted small"> · {esc(ngettext("{n} connected", "{n} connected", online.get(str(u["id"]), 0)))}</span>' if n else ""}</td>
          <td class="hide-sm">{time_tag(u.get("createdAt"), fmt="short")}</td>
          <td class="actions">
            <details class="dropdown">
              <summary class="icon-btn" aria-label="{esc(_("Actions"))}">{icon("more")}</summary>
              <div class="dropdown-body right">
                <a href="{BASE}/machines?owner={uid}">{esc(_("View machines"))}</a>
                <a href="{BASE}/settings/keys?user={uid}#new">{esc(_("Generate auth key for this user"))}</a>
                <button type="button" data-open="ren-user-{uid}">{esc(_("Rename…"))}</button>
                {f'<button type="button" data-open="pw-user-{uid}">{esc(_("Set password…"))}</button>' if u.get("name") in (signins or {}) else ""}
                <hr>{delete}
              </div>
            </details>
          </td>
        </tr>""")
        hint = esc(_("If they sign in with SSO, the identity provider may change it back on their next sign-in.")) if oidc else ""
        dialogs.append(dialog(f"ren-user-{uid}", _("Rename {name}", name=u.get("name")),
                              esc(_("Their user name in Headscale.")) + " " + hint,
                              f"{BASE}/users/{uid}/rename", session, submit=_("Save"),
                              fields=f'<label class="field">{esc(_("Name"))}<input name="name" value="{esc(u.get("name"))}" '
                                     f'required autocomplete="off" spellcheck="false"></label>'))
        if u.get("name") in (signins or {}):
            dialogs.append(dialog(f"pw-user-{uid}", _("Set the password of {name}", name=u.get("name")),
                                  esc(_("Their open sessions are signed out.")),
                                  f"{BASE}/users/{uid}/password", session, submit=_("Set password"),
                                  fields=_password_fields()))
        dialogs.append(dialog(f"del-user-{uid}", _("Delete {name}?", name=u.get("name")),
                              esc(_("The user is deleted from Headscale. Their sign-in account is not touched: "
                                    "if they sign in again, the user is created again.")),
                              f"{BASE}/users/{uid}/delete", session, submit=_("Delete user"), danger=True))

    actions = f'<button class="btn" type="button" data-open="new-user">{esc(_("Create local user"))}</button>'
    sub = _("Users of the tailnet. They are created automatically the first time someone connects a device by signing in.")
    body = page_head(_("Users"), esc(sub), actions) + flash_html(flash) + (notice("error", error) if error else "")
    body += f"""
    <div class="toolbar">
      <label class="search">{icon("search")}<input type="search" placeholder="{esc(_("Search users…"))}" data-filter aria-label="{esc(_("Search users"))}"></label>
    </div>
    <span class="pill" data-count data-one="{esc(_("1 user"))}" data-many="{esc(_("{n} users"))}">{esc(ngettext("{n} user", "{n} users", len(users)))}</span>
    <div class="table-wrap">
      <table class="machines users">
        <thead><tr><th>{esc(_("User"))}</th><th>{esc(_("Headscale name"))}</th><th>{esc(_("Sign-in"))}</th>
          <th>{esc(_("Machines"))}</th><th class="hide-sm">{esc(_("Joined"))}</th><th></th></tr></thead>
        <tbody data-live="rows" data-stream="{BASE}/events">{"".join(rows)}</tbody>
      </table>
    </div>
    <p class="no-results muted" hidden>{esc(_("No users match the search."))}</p>
    {dialog("new-user", _("Create local user"),
            esc(_("Without a password: a Headscale user without sign-in, for servers or devices that connect with auth keys. "
                  "With a password: an account the person can sign in with.")),
            f"{BASE}/users", session, submit=_("Create"),
            fields=f'<label class="field">{esc(_("Name"))}<input name="name" required placeholder="servers" autocomplete="off" spellcheck="false"></label>'
                   f'<label class="field">{esc(_("Display name (optional)"))}<input name="display_name" autocomplete="off"></label>'
                   + _account_fields())}
    <div data-live="dialogs">{"".join(dialogs)}</div>"""
    return layout(_("Users"), "users", body + extra, session, ctx)


# -----------------------------------------------------------------------------
# Access controls (ACL policy)
# -----------------------------------------------------------------------------

ISOLATION = """{
  // Each user can only reach their own machines, and may use exit nodes
  "acls": [
    {"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:self:*"]},
    {"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:internet:*"]}
  ]
}"""


def _raw_panel(session: dict, text: str, updated: str | None, result: tuple[str, str] | None,
               can_edit: bool = True) -> str:
    res = ""
    if result:
        kind, msg = result
        res = f'<div class="notice {kind}" role="status"><pre class="plainpre">{esc(msg)}</pre></div>'
    buttons = f"""<div class="form-foot">
        <button class="btn" type="submit" name="action" value="check">{esc(_("Check"))}</button>
        <button class="btn primary" type="submit" name="action" value="save">{esc(_("Save"))}</button>
      </div>""" if can_edit else ""
    return res + f"""
    <form method="post" action="{BASE}/acl" class="stack">
      {csrf_input(session)}
      <div class="card-title"><h2>{esc(_("Policy file"))}</h2>
        <span class="muted small">{(esc(_("Updated")) + " " + time_tag(updated)) if updated else esc(_("No policy: every machine can reach every other one"))}</span></div>
      <textarea name="policy" class="code-editor" spellcheck="false" rows="22" data-tab-indent {"readonly" if not can_edit else ""}>{esc(text)}</textarea>
      {buttons}
    </form>
    <hr>
    <div class="stack">
      <h2>{esc(_("Quick reference"))}</h2>
      <dl class="kvs">
        <div class="kv"><dt><code>autogroup:member</code></dt><dd>{esc(_("Machines of any user (without tags)."))}</dd></div>
        <div class="kv"><dt><code>autogroup:self</code></dt><dd>{esc(_("The machines of the same user as the source."))}</dd></div>
        <div class="kv"><dt><code>autogroup:internet</code></dt><dd>{esc(_("Internet access through an exit node."))}</dd></div>
        <div class="kv"><dt><code>tag:name</code></dt><dd>{esc(_("Machines with that tag; the tag needs an owner in tagOwners."))}</dd></div>
        <div class="kv"><dt><code>user@</code></dt><dd>{esc(_("The machines of one user."))}</dd></div>
      </dl>
      <p class="muted small">{esc(_("Per-user isolation (the installer's default policy):"))}</p>
      <div class="code"><code class="pre">{esc(ISOLATION)}</code></div>
    </div>"""


_TAB_KEYS = ("rules", "groups", "auto", "ssh", "test", "raw")


def _tab_label(key: str) -> str:
    return {
        "rules": _("Rules"),
        "groups": _("Groups & tags"),
        "auto": _("Auto-approval"),
        "ssh": _("SSH rules"),
        "test": _("Test access"),
        "raw": _("Advanced (HuJSON)"),
    }[key]


def acl_page(session: dict, ctx: dict, policy_data: dict, nodes: list[dict], users: list[dict], flash: str,
            draft: str | None = None, result: tuple[str, str] | None = None, test: tuple | None = None,
            active: str = "rules") -> str:
    text = draft if draft is not None else (policy_data.get("policy") or "")
    broken = ""
    try:
        parsed = policy.parse(text)
    except policy.PolicyError as exc:
        parsed, broken = {}, str(exc)
    vocab = policy.vocabulary(parsed, nodes, users)
    if active not in _TAB_KEYS:
        active = "rules"
    can_edit = bool(session.get("admin")) or session.get("role") == "network_admin"

    tabs = "".join(
        f'<button type="button" role="tab" data-tab="{k}" class="{"active" if k == active else ""}">{esc(_tab_label(k))}</button>'
        for k in _TAB_KEYS)

    if broken:
        note = (f'<div class="notice error" role="status">'
               f'{esc(_("This policy could not be read as HuJSON ({error}). Fix it in Advanced.", error=broken))}</div>')
        rules_body = groups_body = auto_body = ssh_body = note
    else:
        rules_body = acl_pages.rules_panel(session, parsed, can_edit)
        groups_body = acl_pages.groups_panel(session, parsed, can_edit)
        auto_body = acl_pages.auto_approve_panel(session, parsed, can_edit)
        ssh_body = acl_pages.ssh_panel(session, parsed, can_edit)
    panels_by_key = {
        "rules": rules_body,
        "groups": groups_body,
        "auto": auto_body,
        "ssh": ssh_body,
        "test": acl_pages.test_panel(session, test),
        "raw": _raw_panel(session, text, policy_data.get("updatedAt"), result, can_edit),
    }
    panels = "".join(f'<div class="tab-panel" data-panel="{k}" {"" if k == active else "hidden"}>{panels_by_key[k]}</div>'
                     for k in _TAB_KEYS)

    body = page_head(_("Access controls"),
                     esc(_("Who can connect to what inside the tailnet.")),
                     link=(_("Learn more"), "https://headscale.net/stable/ref/acls/")) + flash_html(flash) + \
           acl_pages.datalist(vocab) + acl_pages.ssh_users_datalist(vocab) + \
           f'<section class="card acl-editor"><div class="ostabs" role="tablist">{tabs}</div>{panels}</section>'
    return layout(_("Access controls"), "acl", body, session, ctx)


# -----------------------------------------------------------------------------
# Sign in
# -----------------------------------------------------------------------------

def login_page(sso: bool, apikey: bool, error: str = "", info: str = "", local: bool = True) -> str:
    # Local account sign-in (username + password)
    local_html = ""
    if local:
        local_html = f"""
      <form method="post" action="{BASE}/login/local" class="stack">
        <label class="field">{esc(_("Username"))}<input name="username" type="text" required
          autocomplete="username" spellcheck="false" autofocus></label>
        <label class="field">{esc(_("Password"))}<input name="password" type="password" required
          autocomplete="current-password"></label>
        <button class="btn wide primary" type="submit">{esc(_("Sign in"))}</button>
      </form>"""

    signup_html = (f'<p class="muted small"><a href="{BASE}/signup">{esc(_("Create an account"))}</a></p>'
                   if local and signup_open() else "")
    sso_html = f'<a class="btn wide" href="{BASE}/login/sso">{esc(_("Sign in with SSO"))}</a>' if sso else ""

    # Separators
    sep1 = f'<div class="sep"><span>{esc(_("or"))}</span></div>' if local and (sso or apikey) else ""
    sep2 = f'<div class="sep"><span>{esc(_("or"))}</span></div>' if sso and apikey else ""

    key_html = ""
    if apikey:
        key_html = f"""
      <form method="post" action="{BASE}/login/apikey" class="stack">
        <label class="field">{esc(_("Headscale API key"))}<input name="api_key" type="password" required
          placeholder="hskey-api-…" autocomplete="off" spellcheck="false"></label>
        <button class="btn wide" type="submit">{esc(_("Sign in with API key"))}</button>
        <p class="muted small">{esc(_("Gives admin access. Create one with:"))} <code>docker exec headscale headscale apikeys create</code></p>
      </form>"""

    return bare_page(_("Sign in"), f"""
    <section class="card narrow center login">
      <div class="big-logo">{LOGO}</div>
      <h1>Headscale Easy</h1>
      <p class="muted">{esc(_("Sign in to manage your tailnet."))}</p>
      {notice("error", error) if error else ""}{notice("ok", info) if info else ""}{local_html}{signup_html}{sep1}{sso_html}{sep2}{key_html}
    </section>""")
