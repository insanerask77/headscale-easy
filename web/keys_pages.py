"""The Keys page: pre-auth keys and API keys."""

from __future__ import annotations

from datetime import datetime, timezone

from i18n import _
from qr import qr_figure
from ui import (BASE, badge, copy_btn, csrf_input, dialog, esc, flash_html, layout, page_head, parse_time, time_tag, user_label)


def key_state(k: dict) -> tuple[str, str]:
    exp = parse_time(k.get("expiration"))
    if exp is not None and exp <= datetime.now(timezone.utc):
        return _("Expired"), ""
    if k.get("used") and not k.get("reusable"):
        return _("Used"), ""
    return _("Active"), "green"


def user_select(users: list[dict] | None, selected: str = "") -> str:
    opts = "".join(f'<option value="{esc(u["id"])}" {"selected" if str(u["id"]) == selected else ""}>{esc(user_label(u))}</option>'
                   for u in sorted(users or [], key=lambda u: user_label(u).lower()))
    return f'<label class="field">{esc(_("User"))}<select name="user_id" required>{opts}</select></label>'


def apikeys_section(session: dict, apikeys: list[dict], own_prefix: str, new_apikey: str) -> str:
    new_html = ""
    if new_apikey:
        new_html = f"""<div class="keybox"><p><b>{esc(_("API key created."))}</b> {esc(_("Copy it now: it will not be shown again."))}</p>
          <div class="code"><code>{esc(new_apikey)}</code>{copy_btn(new_apikey)}</div></div>"""
    rows = []
    now = datetime.now(timezone.utc)
    for k in sorted(apikeys, key=lambda k: k.get("createdAt") or "", reverse=True):
        exp = parse_time(k.get("expiration"))
        active = exp is None or exp > now
        own = own_prefix and own_prefix in (k.get("prefix") or "")
        state = badge(_("Active"), "green") if active else badge(_("Expired"))
        if own:
            state += " " + badge(_("Used by Headscale Easy"), "blue")
        action = ""
        if active and not own:
            action = f"""<form method="post" action="{BASE}/apikeys/{esc(k['id'])}/expire">{csrf_input(session)}
              <button class="btn small" type="submit">{esc(_("Expire"))}</button></form>"""
        rows.append(f"""<tr><td><code>{esc(k.get("prefix"))}</code></td><td>{time_tag(k.get("createdAt"))}</td>
          <td>{time_tag(k.get("expiration"), _("Never"))}</td><td>{time_tag(k.get("lastSeen"), _("Never"))}</td>
          <td>{state}</td><td class="actions">{action}</td></tr>""")
    table = (f"""<div class="table-wrap"><table class="simple"><thead><tr><th>{esc(_("Prefix"))}</th><th>{esc(_("Created"))}</th>
        <th>{esc(_("Expires"))}</th><th>{esc(_("Last used"))}</th><th>{esc(_("Status"))}</th><th></th></tr></thead>
        <tbody>{"".join(rows)}</tbody></table></div>""" if rows else f'<p class="muted">{esc(_("No API keys."))}</p>')
    return f"""
    <section class="card">
      <div class="card-title"><h2>{esc(_("API keys"))}</h2>
        <button class="btn" type="button" data-open="new-apikey">{esc(_("Generate API key…"))}</button></div>
      <p class="muted">{esc(_("Full admin access to the Headscale API. Use them for automation and never share them."))}</p>
      {new_html}{table}
    </section>
    {dialog("new-apikey", _("Generate API key"), "", f"{BASE}/apikeys", session, submit=_("Generate key"),
            fields=f'<label class="field">{esc(_("Expiration"))}<select name="days"><option value="30">{esc(_("30 days"))}</option>'
                   f'<option value="90" selected>{esc(_("90 days"))}</option><option value="365">{esc(_("365 days"))}</option></select></label>')}"""


def keys_page(session: dict, ctx: dict, keys: list[dict] | None, flash: str, new_key: dict | None = None,
              users: list[dict] | None = None, apikeys: list[dict] | None = None, own_prefix: str = "",
              new_apikey: str = "", preselect: str = "") -> str:
    url = ctx["public_url"]
    admin = session.get("admin")
    by_id = {str(u["id"]): u for u in users or []}

    new_html = ""
    if new_key:
        cmd = f"tailscale up --login-server={url} --authkey={new_key['key']}"
        new_html = f"""
    <section class="card keybox">
      <h2>{esc(_("Auth key generated"))}</h2>
      <p>{esc(_("Copy it now: it will not be shown again."))}</p>
      <div class="code"><code>{esc(new_key["key"])}</code>{copy_btn(new_key["key"])}</div>
      <p class="muted small">{esc(_("To connect a device with it:"))}</p>
      <div class="code"><code>{esc(cmd)}</code>{copy_btn(cmd)}</div>
      <details class="qr-details">
        <summary>{esc(_("Show as QR codes"))}</summary>
        <div class="qr-pair">
          {qr_figure(new_key["key"], _("auth key"), _("Auth key"))}
          {qr_figure(cmd, _("command to connect a Linux device"), _("Linux command"))}
        </div>
        <p class="muted small">{esc(_("Most phone cameras can copy the text of a QR code. On Android, paste the key in the Tailscale app with Use an auth key (⋮ menu, after setting the server)."))}</p>
      </details>
    </section>"""

    if keys is None and not admin:
        table = f'<p class="muted">{esc(_("Auth keys belong to your tailnet user, which is created the first time you connect a device by signing in from it."))} <a class="link" href="{BASE}/add">{esc(_("Add device"))}</a></p>'
        can_create = False
    elif not keys:
        table = f'<p class="muted">{esc(_("No auth keys yet."))}</p>'
        can_create = bool(users) if admin else True
    else:
        can_create = True
        rows = []
        for k in sorted(keys, key=lambda k: k.get("createdAt") or "", reverse=True):
            state, kind = key_state(k)
            flags = [_("Reusable") if k.get("reusable") else _("One-off")]
            if k.get("ephemeral"):
                flags.append(_("Ephemeral"))
            owner = ""
            if admin:
                u = by_id.get(str((k.get("user") or {}).get("id")), k.get("user") or {})
                owner = f"<td>{esc(user_label(u))}</td>"
            revoke = ""
            if state == _("Active"):
                revoke = f"""<form method="post" action="{BASE}/keys/{esc(k['id'])}/revoke">{csrf_input(session)}
                  <button class="btn small" type="submit">{esc(_("Revoke"))}</button></form>"""
            rows.append(f"""<tr><td><code>{esc(k.get("key"))}</code></td>{owner}<td>{esc(" · ".join(flags))}</td>
              <td>{time_tag(k.get("createdAt"))}</td><td>{time_tag(k.get("expiration"))}</td>
              <td>{badge(state, kind)}</td><td class="actions">{revoke}</td></tr>""")
        table = f"""<div class="table-wrap"><table class="simple"><thead><tr><th>{esc(_("Key"))}</th>
          {f"<th>{esc(_('User'))}</th>" if admin else ""}<th>{esc(_("Type"))}</th><th>{esc(_("Created"))}</th>
          <th>{esc(_("Expires"))}</th><th>{esc(_("Status"))}</th><th></th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>"""

    create = (f'<button class="btn primary" type="button" data-open="new">{esc(_("Generate auth key…"))}</button>'
              if can_create else "")
    fields = (user_select(users, preselect) if admin else "") + f"""
        <label class="check"><input type="checkbox" name="reusable" value="1">
          <span><b>{esc(_("Reusable"))}</b><span class="muted">{esc(_("Use it for several machines. Otherwise it is invalidated after the first one."))}</span></span></label>
        <label class="check"><input type="checkbox" name="ephemeral" value="1">
          <span><b>{esc(_("Ephemeral"))}</b><span class="muted">{esc(_("Machines are removed automatically when they go offline. For containers and CI."))}</span></span></label>
        <label class="field">{esc(_("Expiration"))}<select name="days">
          <option value="1">{esc(_("1 day"))}</option><option value="7">{esc(_("7 days"))}</option>
          <option value="30">{esc(_("30 days"))}</option><option value="90" selected>{esc(_("90 days"))}</option></select>
          <span class="muted small">{esc(_("Until when the key can add devices. The devices themselves follow the key expiry in Settings → General."))}</span></label>"""
    body = page_head(_("Keys"), esc(_("Auth keys connect devices without signing in on them: servers, containers or headless devices. The machines belong to the key's user."))) \
        + flash_html(flash) + new_html + f"""
    <section class="card">
      <div class="card-title"><h2>{esc(_("Auth keys"))}</h2>{create}</div>
      {table}
    </section>
    {dialog("new", _("Generate auth key"), "", f"{BASE}/keys", session, fields=fields, submit=_("Generate key"))}
    {apikeys_section(session, apikeys or [], own_prefix, new_apikey) if admin else ""}"""
    return layout(_("Keys"), "keys", body, session, ctx)
