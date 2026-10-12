"""Settings pages: General, Sessions, Account and two-factor setup."""

from __future__ import annotations

from datetime import datetime, timezone

import notify
from i18n import LANGUAGES, _, get_lang
from qr import qr_figure
from ui import (BASE, badge, csrf_input, esc, flash_html, icon, initials, kv,
                layout, notice, page_head, time_tag)


def notify_section(session: dict) -> str:
    """Settings > General (admins): where notifications go, and "Send a test"."""
    dests = notify.destinations()
    if not dests:
        body = (f'<p class="muted">{esc(_("Get a message in Slack, Telegram, ntfy or any webhook when a device joins, is removed or its key expires."))}</p>'
                f'<p class="muted small">{esc(_("Set NOTIFY_URLS in .env and restart the container."))}</p>')
    else:
        labels = {"device.registered": _("New device"), "device.key_expired": _("Key expired"),
                  "device.expiring": _("Key expiring soon"), "device.removed": _("Device removed"),
                  "backup.failed": _("Backup failed")}
        chosen = notify.events()
        items = "".join(f"<li>{esc(d['label'])}</li>" for d in dests)
        evs = ", ".join(labels[e] for e in notify.ALL_EVENTS if e in chosen)
        body = (f'<ul>{items}</ul>'
                f'<p class="muted small">{esc(_("Events: {events}", events=evs))}</p>'
                f'<form method="post" action="{BASE}/settings/notify-test" data-busy>{csrf_input(session)}'
                f'<button class="btn" type="submit">{esc(_("Send a test"))}</button></form>')
    return f"""
    <section class="card">
      <h2>{esc(_("Notifications"))}</h2>
      {body}
    </section>"""


def general_page(session: dict, ctx: dict, flash: str = "", key_expiry: int | None = None,
                 error: str = "", extra: str = "") -> str:
    role = _("Admin") if session.get("admin") else _("Member")
    if session.get("kind") == "apikey":
        role = _("Admin (Headscale API key session)")
    groups = ", ".join(session.get("groups") or []) or "—"
    name = session.get("name") or session.get("username") or _("Administrator")
    langs = "".join(f'<button type="submit" name="lang" value="{code}" class="{"active" if get_lang() == code else ""}">{esc(label)}</button>'
                    for code, label in LANGUAGES.items())
    devices = ""
    if session.get("admin") and key_expiry is not None:
        never = key_expiry == 0
        devices = f"""
    <section class="card">
      <h2>{esc(_("Device management"))}</h2>
      <form method="post" action="{BASE}/settings/key-expiry" class="stack" data-busy>{csrf_input(session)}
        <label class="field">{esc(_("Key expiry"))}
          <span class="muted small">{esc(_("New devices must sign in again after this many days (Tailscale uses 180). Auth keys have their own expiry: it only limits until when a key can add devices."))}</span>
          <span class="inline"><input type="number" name="days" min="1" max="365" value="{key_expiry or 180}" class="short"> {esc(_("days"))}</span></label>
        <label class="check"><input type="checkbox" name="never" value="1" {"checked" if never else ""}>
          <span>{esc(_("Never expire (not recommended)"))}</span></label>
        <p class="muted small">{esc(_("Saving restarts Headscale: devices reconnect within a few seconds. Existing devices keep their current expiry; change it per machine."))}</p>
        <div><button class="btn primary" type="submit">{esc(_("Save"))}</button></div>
      </form>
    </section>"""
    notifications = notify_section(session) if session.get("admin") else ""
    body = page_head(_("General"), esc(_("Your account and how Headscale Easy looks for you."))) + flash_html(flash) + (notice("error", error) if error else "") + f"""
    <section class="card">
      <h2>{esc(_("Account"))}</h2>
      <div class="account"><span class="avatar big">{initials(name)}</span>
        <div><b>{esc(name)}</b><div class="muted">{esc(session.get("email"))}</div></div></div>
      <dl class="kvs">
        {kv(_("User name"), f"<code>{esc(session.get('username'))}</code>") if session.get("username") else ""}
        {kv(_("Role"), esc(role))}
        {kv(_("Groups"), esc(groups)) if session.get("kind") != "apikey" else ""}
      </dl>
    </section>
    {devices}
    {notifications}
    {extra}
    <section class="card">
      <h2>{esc(_("Appearance"))}</h2>
      <p class="muted">{esc(_("Saved in this browser."))}</p>
      <div class="segmented" role="radiogroup" aria-label="{esc(_("Theme"))}">
        <button type="button" data-theme-set="system">{esc(_("System"))}</button>
        <button type="button" data-theme-set="dark">{esc(_("Dark"))}</button>
        <button type="button" data-theme-set="light">{esc(_("Light"))}</button>
      </div>
    </section>
    <section class="card">
      <h2>{esc(_("Language"))}</h2>
      <p class="muted">{esc(_("By default, the language of your browser."))}</p>
      <form method="post" action="{BASE}/settings/language" class="segmented">{csrf_input(session)}{langs}</form>
    </section>
    <section class="card">
      <h2>{esc(_("Session"))}</h2>
      <p class="muted">{esc(_("Signs you out here and from the identity provider."))}</p>
      <form method="post" action="{BASE}/logout">{csrf_input(session)}<button class="btn" type="submit">{esc(_("Log out"))}</button></form>
    </section>"""
    return layout(_("General"), "general", body, session, ctx)


def sessions_page(session: dict, ctx: dict, rows: list[dict], flash: str = "") -> str:
    """Settings -> Sessions: your sessions; admins and auditors see everybody's."""
    admin = bool(session.get("admin"))
    sees_all = admin or session.get("role") == "auditor"
    body_rows = []
    for r in rows:
        current = r["sid"] == session.get("sid")
        who = f"<td>{esc(r['name'] or '—')}</td>" if sees_all else ""
        kind = _("API key") if r["kind"] == "apikey" else "OIDC"
        btn = f"""<form method="post" action="{BASE}/settings/sessions/revoke">{csrf_input(session)}
          <input type="hidden" name="sid" value="{esc(r['sid'])}">
          <button class="btn small" type="submit">{esc(_("Log out"))}</button></form>""" \
            if (admin or current or (session.get("sub") and r["sub"] == session.get("sub"))) else ""
        created = datetime.fromtimestamp(r["created"], timezone.utc).isoformat()
        seen = datetime.fromtimestamp(r["last_seen"], timezone.utc).isoformat()
        body_rows.append(f"""<tr>{who}<td>{esc(kind)}</td><td>{esc(r['role'])}</td><td><code>{esc(r['ip'] or '—')}</code></td>
          <td class="muted small">{esc((r['ua'] or '—')[:80])}</td><td>{time_tag(created)}</td><td>{time_tag(seen)}</td>
          <td>{badge(_("This session"), "blue") if current else ""}</td><td class="actions">{btn}</td></tr>""")
    if body_rows:
        table = f"""<div class="table-wrap"><table class="simple"><thead><tr>{f"<th>{esc(_('User'))}</th>" if sees_all else ""}
          <th>{esc(_("Type"))}</th><th>{esc(_("Role"))}</th><th>{esc(_("IP address"))}</th><th>{esc(_("Browser"))}</th>
          <th>{esc(_("Created"))}</th><th>{esc(_("Last activity"))}</th><th></th><th></th></tr></thead>
          <tbody>{"".join(body_rows)}</tbody></table></div>"""
    else:
        table = f'<p class="muted">{esc(_("No active sessions."))}</p>'
    everyone = f"""
      <form method="post" action="{BASE}/settings/sessions/revoke-all" class="inline">{csrf_input(session)}
        <input type="hidden" name="scope" value="everyone">
        <button class="btn" type="submit">{esc(_("Sign out everyone else"))}</button></form>""" if admin else ""
    body = page_head(_("Sessions"), esc(_("Where you are signed in. Signing a session out takes effect at once."))) + flash_html(flash) + f"""
    <section class="card">
      <div class="card-title"><h2>{esc(_("Active sessions"))}</h2>
        <form method="post" action="{BASE}/settings/sessions/revoke-all" class="inline">{csrf_input(session)}
          <button class="btn" type="submit">{esc(_("Sign out everywhere"))}</button></form>{everyone}</div>
      {table}
    </section>"""
    return layout(_("Sessions"), "sessions", body, session, ctx)


# --- TOTP and account settings (Block 3.3) ---


def account_settings_page(session: dict, ctx: dict, account: dict, flash: str = "") -> str:
    """Account settings page for local accounts."""
    username = account['username']
    email = account['email']
    role = account['role']
    totp_enabled = account.get('totp_confirmed', 0) == 1

    role_labels = {
        'admin': _("Administrator"),
        'network_admin': _("Network Administrator"),
        'auditor': _("Auditor"),
        'member': _("Member")
    }
    role_label = role_labels.get(role, role)

    totp_section = f"""
    <section class="card">
      <div class="card-title"><h2>{esc(_("Two-factor authentication"))}</h2></div>
      <p>{esc(_("Two-factor authentication (2FA) adds an extra layer of security to your account."))}</p>
      {"<p class='success'>" + esc(_("Two-factor authentication is enabled.")) + "</p>" if totp_enabled else ""}
      <div class="btn-group">
        {"" if totp_enabled else f'<a href="{BASE}/settings/account/totp/enroll" class="btn btn-primary">{esc(_("Enable 2FA"))}</a>'}
        {f'''<form method="post" action="{BASE}/settings/account/totp/disable" class="inline" onsubmit="return confirm('{esc(_("Are you sure you want to disable two-factor authentication?"))}')">{csrf_input(session)}
          <button type="submit" class="btn">{esc(_("Disable 2FA"))}</button></form>''' if totp_enabled else ""}
        {f'''<form method="post" action="{BASE}/settings/account/totp/recovery/reset" class="inline">{csrf_input(session)}
          <button type="submit" class="btn">{esc(_("Reset recovery codes"))}</button></form>''' if totp_enabled else ""}
      </div>
    </section>"""

    body = page_head(_("Account settings"), "") + flash_html(flash) + f"""
    <section class="card">
      <div class="card-title"><h2>{esc(_("Account information"))}</h2></div>
      <dl class="info-list">
        <dt>{esc(_("Username"))}</dt><dd>{esc(username)}</dd>
        <dt>{esc(_("Email"))}</dt><dd>{esc(email)}</dd>
        <dt>{esc(_("Role"))}</dt><dd>{esc(role_label)}</dd>
      </dl>
    </section>
    <section class="card">
      <div class="card-title"><h2>{esc(_("Change password"))}</h2></div>
      <form method="post" action="{BASE}/settings/account/password">
        {csrf_input(session)}
        <label>{esc(_("Current password"))}
          <input type="password" name="old_password" required autocomplete="current-password">
        </label>
        <label>{esc(_("New password"))}
          <input type="password" name="new_password" required autocomplete="new-password" minlength="8">
        </label>
        <label>{esc(_("Confirm new password"))}
          <input type="password" name="new_password2" required autocomplete="new-password" minlength="8">
        </label>
        <button type="submit" class="btn btn-primary">{esc(_("Change password"))}</button>
      </form>
    </section>
    {totp_section}"""
    return layout(_("Account settings"), "settings", body, session, ctx)


def totp_enroll_page(session: dict, ctx: dict, secret: str, qr_data: str) -> str:
    """TOTP enrollment page with QR code."""
    qr_svg = qr_figure(qr_data)

    # Format secret in groups of 4 for easier manual entry
    secret_formatted = " ".join([secret[i:i+4] for i in range(0, len(secret), 4)])

    body = page_head(_("Set up two-factor authentication"),
                    esc(_("Scan the QR code with your authenticator app, then enter a code to confirm."))) + f"""
    <section class="card">
      <div class="card-title"><h2>{esc(_("1. Scan QR code"))}</h2></div>
      <div style="text-align: center; padding: 1rem;">
        {qr_svg}
      </div>
      <details style="margin-top: 1rem">
        <summary>{esc(_("Can't scan the QR code?"))}</summary>
        <p style="margin-top: 0.5rem">{esc(_("Enter this code manually in your authenticator app:"))}</p>
        <code style="display: block; padding: 0.5rem; background: var(--bg-2); border-radius: 4px; font-size: 0.9rem; word-break: break-all;">
          {esc(secret_formatted)}
        </code>
      </details>
    </section>
    <section class="card">
      <div class="card-title"><h2>{esc(_("2. Verify"))}</h2></div>
      <p>{esc(_("Enter the 6-digit code from your authenticator app to confirm setup."))}</p>
      <form method="post" action="{BASE}/settings/account/totp/confirm">
        {csrf_input(session)}
        <label>{esc(_("Verification code"))}
          <input type="text" name="code" inputmode="numeric" pattern="[0-9]{{6}}"
                 autocomplete="off" required autofocus maxlength="6"
                 placeholder="000000">
        </label>
        <div class="btn-group">
          <button type="submit" class="btn btn-primary">{esc(_("Confirm and enable"))}</button>
          <a href="{BASE}/settings/account" class="btn">{esc(_("Cancel"))}</a>
        </div>
      </form>
    </section>
    <section class="card notice-info">
      <p><strong>{esc(_("Recommended authenticator apps:"))}</strong></p>
      <ul>
        <li>Google Authenticator (iOS, Android)</li>
        <li>Microsoft Authenticator (iOS, Android)</li>
        <li>Authy (iOS, Android, Desktop)</li>
        <li>1Password (all platforms)</li>
      </ul>
    </section>"""
    return layout(_("Set up 2FA"), "settings", body, session, ctx)


def recovery_codes_page(session: dict, ctx: dict, codes: list[str]) -> str:
    """Show recovery codes after generation/reset."""
    codes_html = "".join(f"<li><code>{esc(code)}</code></li>" for code in codes)

    body = page_head(_("Recovery codes"),
                    esc(_("Save these recovery codes in a safe place. Each code can only be used once."))) + f"""
    <section class="card notice-warning">
      <p><strong>{icon('alert-triangle')} {esc(_("Important:"))}</strong>
         {esc(_("These codes will not be shown again. Save them now."))}</p>
    </section>
    <section class="card">
      <div class="card-title"><h2>{esc(_("Your recovery codes"))}</h2></div>
      <ul class="recovery-codes">
        {codes_html}
      </ul>
      <p style="margin-top: 1rem; font-size: 0.9rem; color: var(--text-secondary);">
        {esc(_("Use a recovery code if you lose access to your authenticator app. Each code can only be used once."))}
      </p>
      <div class="btn-group" style="margin-top: 1rem">
        <button onclick="window.print()" class="btn">{icon('printer')} {esc(_("Print"))}</button>
        <a href="{BASE}/settings/account" class="btn btn-primary">{esc(_("Done"))}</a>
      </div>
    </section>
    <style>
      .recovery-codes {{ list-style: none; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.5rem; }}
      .recovery-codes li {{ background: var(--bg-2); padding: 0.75rem; border-radius: 4px; text-align: center; }}
      .recovery-codes code {{ font-size: 1.1rem; font-weight: 600; letter-spacing: 0.05em; }}
      @media print {{
        .nav, .btn-group {{ display: none; }}
        .recovery-codes {{ grid-template-columns: repeat(2, 1fr); }}
      }}
    </style>"""
    return layout(_("Recovery codes"), "settings", body, session, ctx)
