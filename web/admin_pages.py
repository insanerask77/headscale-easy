"""Admin-only pages (Users, Access controls) and the sign-in page.
app.py checks the role before rendering any of them."""

from __future__ import annotations

from i18n import _, ngettext
from pages import dialog
from ui import (BASE, LOGO, badge, bare_page, csrf_input, esc, flash_html, icon, initials, layout, notice,
                page_head, time_tag, user_label)



# -----------------------------------------------------------------------------
# Users
# -----------------------------------------------------------------------------

def users_page(session: dict, ctx: dict, users: list[dict], nodes: list[dict], flash: str, error: str = "") -> str:
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
        dialogs.append(dialog(f"del-user-{uid}", _("Delete {name}?", name=u.get("name")),
                              esc(_("The user is deleted from Headscale. Their sign-in account is not touched: "
                                    "if they sign in again, the user is created again.")),
                              f"{BASE}/users/{uid}/delete", session, submit=_("Delete user"), danger=True))

    actions = f'<button class="btn" type="button" data-open="new-user">{esc(_("Create local user"))}</button>'
    if ctx.get("authentik"):
        actions += f'<a class="btn primary" href="{esc(ctx["public_url"])}/add-user">{esc(_("Add user"))}</a>'
    sub = _("Users of the tailnet. They are created automatically the first time someone connects a device by signing in.")
    if ctx.get("authentik"):
        sub += " " + _("Accounts (user name and password) are created in Authentik.")
    body = page_head(_("Users"), esc(sub), actions) + flash_html(flash) + (notice("error", error) if error else "") + f"""
    <div class="toolbar">
      <label class="search">{icon("search")}<input type="search" placeholder="{esc(_("Search users…"))}" data-filter aria-label="{esc(_("Search users"))}"></label>
    </div>
    <span class="pill" data-count data-one="{esc(_("1 user"))}" data-many="{esc(_("{n} users"))}">{esc(ngettext("{n} user", "{n} users", len(users)))}</span>
    <div class="table-wrap">
      <table class="machines users">
        <thead><tr><th>{esc(_("User"))}</th><th>{esc(_("Headscale name"))}</th><th>{esc(_("Sign-in"))}</th>
          <th>{esc(_("Machines"))}</th><th class="hide-sm">{esc(_("Joined"))}</th><th></th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
    </div>
    <p class="no-results muted" hidden>{esc(_("No users match the search."))}</p>
    {dialog("new-user", _("Create local user"),
            esc(_("A Headscale user without sign-in, for servers or devices that connect with auth keys. For people, create an account instead.")),
            f"{BASE}/users", session, submit=_("Create"),
            fields=f'<label class="field">{esc(_("Name"))}<input name="name" required placeholder="servers" autocomplete="off" spellcheck="false"></label>'
                   f'<label class="field">{esc(_("Display name (optional)"))}<input name="display_name" autocomplete="off"></label>')}
    {"".join(dialogs)}"""
    return layout(_("Users"), "users", body, session, ctx)


# -----------------------------------------------------------------------------
# Access controls (ACL policy)
# -----------------------------------------------------------------------------

ISOLATION = """{
  // Each user can only reach their own machines
  "acls": [
    {"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:self:*"]}
  ]
}"""


def acl_page(session: dict, ctx: dict, policy: dict, flash: str, draft: str | None = None,
             result: tuple[str, str] | None = None) -> str:
    text = draft if draft is not None else (policy.get("policy") or "")
    res = ""
    if result:
        kind, msg = result
        res = f'<div class="notice {kind}" role="status"><pre class="plainpre">{esc(msg)}</pre></div>'
    updated = policy.get("updatedAt")
    body = page_head(_("Policy editor"),
                     esc(_("Who can connect to what inside the tailnet. The policy is HuJSON (JSON with comments), same as in Tailscale.")),
                     link=(_("Learn more"), "https://headscale.net/stable/ref/acls/")) + flash_html(flash) + res + f"""
    <form method="post" action="{BASE}/acl" class="card stack">
      {csrf_input(session)}
      <div class="card-title"><h2>{esc(_("Policy file"))}</h2>
        <span class="muted small">{(esc(_("Updated")) + " " + time_tag(updated)) if updated else esc(_("No policy: every machine can reach every other one"))}</span></div>
      <textarea name="policy" class="code-editor" spellcheck="false" rows="22" data-tab-indent>{esc(text)}</textarea>
      <div class="form-foot">
        <button class="btn" type="submit" name="action" value="check">{esc(_("Check"))}</button>
        <button class="btn primary" type="submit" name="action" value="save">{esc(_("Save"))}</button>
      </div>
    </form>
    <section class="card">
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
    </section>"""
    return layout(_("Policy editor"), "acl", body, session, ctx)


# -----------------------------------------------------------------------------
# Sign in
# -----------------------------------------------------------------------------

def login_page(sso: bool, apikey: bool, error: str = "") -> str:
    sso_html = f'<a class="btn primary wide" href="{BASE}/login/sso">{esc(_("Sign in"))}</a>' if sso else ""
    sep = f'<div class="sep"><span>{esc(_("or"))}</span></div>' if sso and apikey else ""
    key_html = ""
    if apikey:
        key_html = f"""
      <form method="post" action="{BASE}/login/apikey" class="stack">
        <label class="field">{esc(_("Headscale API key"))}<input name="api_key" type="password" required
          placeholder="hskey-api-…" autocomplete="off" spellcheck="false"></label>
        <button class="btn wide {"" if sso else "primary"}" type="submit">{esc(_("Sign in with API key"))}</button>
        <p class="muted small">{esc(_("Gives admin access. Create one with:"))} <code>docker exec headscale headscale apikeys create</code></p>
      </form>"""
    return bare_page(_("Sign in"), f"""
    <section class="card narrow center login">
      <div class="big-logo">{LOGO}</div>
      <h1>Headscale Easy</h1>
      <p class="muted">{esc(_("Sign in to manage your tailnet."))}</p>
      {notice("error", error) if error else ""}{sso_html}{sep}{key_html}
    </section>""")
