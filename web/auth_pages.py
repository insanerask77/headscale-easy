"""Pages reached before signing in: two-factor check, invitation and password reset."""

from __future__ import annotations


from i18n import _
from ui import (BASE, LOGO, bare_page, esc)


def totp_verify_page(username: str, error: str | None = None) -> str:
    """TOTP verification page (second step after password)."""
    error_html = f'<p class="error">{esc(error)}</p>' if error else ""
    body = f"""
    <div class="login-container">
      <div class="login-card">
        {LOGO}
        <h1>{esc(_("Two-factor authentication"))}</h1>
        <p>{esc(_("Enter the 6-digit code from your authenticator app."))}</p>
        {error_html}
        <form method="post" action="{BASE}/login/totp">
          <label>{esc(_("Code"))}
            <input type="text" name="code" inputmode="numeric" pattern="[0-9]{{6}}"
                   autocomplete="one-time-code" required autofocus maxlength="6">
          </label>
          <button type="submit" class="btn btn-primary">{esc(_("Verify"))}</button>
          <details style="margin-top: 1rem">
            <summary>{esc(_("Use a recovery code"))}</summary>
            <p style="margin-top: 0.5rem; font-size: 0.9rem">{esc(_("Enter one of your recovery codes if you don't have access to your authenticator app."))}</p>
            <input type="hidden" name="use_recovery" value="1">
          </details>
        </form>
        <p class="login-footer">{esc(_("Signed in as"))} <strong>{esc(username)}</strong></p>
      </div>
    </div>"""
    return bare_page(_("Two-factor authentication"), body)


def invitation_page(token: str, email: str, role: str, error: str = "") -> str:
    """Accept invitation page - choose username and password."""
    role_labels = {
        'admin': _("Administrator"),
        'network_admin': _("Network Administrator"),
        'auditor': _("Auditor"),
        'member': _("Member")
    }
    role_label = role_labels.get(role, role)

    error_html = f'<p class="error">{esc(error)}</p>' if error else ""

    body = f"""
    <div class="login-container">
      <div class="login-card">
        {LOGO}
        <h1>{esc(_("Accept invitation"))}</h1>
        <p>{esc(_("You've been invited to join this Tailscale network."))}</p>
        {error_html}
        <form method="post" action="{BASE}/accept/{esc(token)}">
          <div style="background: var(--bg-2); padding: 0.75rem; border-radius: 4px; margin-bottom: 1rem;">
            <div style="font-size: 0.9rem; color: var(--text-secondary);">{esc(_("Email"))}</div>
            <div style="font-weight: 600;">{esc(email)}</div>
            <div style="font-size: 0.9rem; color: var(--text-secondary); margin-top: 0.5rem;">{esc(_("Role"))}</div>
            <div style="font-weight: 600;">{esc(role_label)}</div>
          </div>
          <label>{esc(_("Username"))}
            <input type="text" name="username" required autofocus autocomplete="username"
                   pattern="[a-zA-Z0-9_-]{{3,32}}" title="{esc(_("3-32 characters: letters, numbers, - and _"))}"
                   placeholder="{esc(_("Choose a username"))}">
          </label>
          <label>{esc(_("Password"))}
            <input type="password" name="password" required autocomplete="new-password"
                   minlength="8" placeholder="{esc(_("At least 8 characters"))}">
          </label>
          <label>{esc(_("Confirm password"))}
            <input type="password" name="password2" required autocomplete="new-password"
                   placeholder="{esc(_("Re-enter your password"))}">
          </label>
          <button type="submit" class="btn btn-primary">{esc(_("Create account"))}</button>
        </form>
      </div>
    </div>"""
    return bare_page(_("Accept invitation"), body)


def reset_password_page(token: str, username: str, error: str = "") -> str:
    """Reset password page - choose new password."""
    error_html = f'<p class="error">{esc(error)}</p>' if error else ""

    body = f"""
    <div class="login-container">
      <div class="login-card">
        {LOGO}
        <h1>{esc(_("Reset password"))}</h1>
        <p>{esc(_("Choose a new password for your account."))}</p>
        {error_html}
        <form method="post" action="{BASE}/reset/{esc(token)}">
          <div style="background: var(--bg-2); padding: 0.75rem; border-radius: 4px; margin-bottom: 1rem;">
            <div style="font-size: 0.9rem; color: var(--text-secondary);">{esc(_("Username"))}</div>
            <div style="font-weight: 600;">{esc(username)}</div>
          </div>
          <label>{esc(_("New password"))}
            <input type="password" name="password" required autofocus autocomplete="new-password"
                   minlength="8" placeholder="{esc(_("At least 8 characters"))}">
          </label>
          <label>{esc(_("Confirm password"))}
            <input type="password" name="password2" required autocomplete="new-password"
                   placeholder="{esc(_("Re-enter your password"))}">
          </label>
          <button type="submit" class="btn btn-primary">{esc(_("Reset password"))}</button>
        </form>
        <p class="login-footer">
          <a href="{BASE}/login">{esc(_("Back to sign in"))}</a>
        </p>
      </div>
    </div>"""
    return bare_page(_("Reset password"), body)
