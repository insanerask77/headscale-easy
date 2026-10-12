"""Sign-up pages: the public form, the mode setting and the sign-up keys section (rules in signup.py)."""

from __future__ import annotations

from i18n import _
from ui import BASE, LOGO, bare_page, copy_btn, csrf_input, esc, notice, time_tag
import signup


def mode_label(value: str) -> str:
    return {"off": _("Off"), "invite": _("With an invitation key"), "open": _("Open")}.get(value, value)


def signup_page(current_mode: str, csrf: str, error: str = "", values: dict | None = None) -> str:
    v = values or {}
    key = (f'<label class="field">{esc(_("Invitation key"))}<input name="key" type="password" required '
           f'autocomplete="off" spellcheck="false"></label>') if current_mode == "invite" else ""
    return bare_page(_("Create an account"), f"""
    <section class="card narrow center login">
      <div class="big-logo">{LOGO}</div>
      <h1>{esc(_("Create an account"))}</h1>
      {notice("error", error) if error else ""}
      <form method="post" action="{BASE}/signup" class="stack">
        <input type="hidden" name="csrf" value="{esc(csrf)}">
        <label class="field">{esc(_("Username"))}<input name="username" required autocomplete="username"
          spellcheck="false" pattern="[a-z0-9_-]{{3,32}}" title="{esc(_("3-32 characters: lowercase letters, numbers, - and _"))}"
          value="{esc(v.get("username", ""))}" autofocus></label>
        <label class="field">{esc(_("Email"))}<input name="email" type="email" required autocomplete="email"
          value="{esc(v.get("email", ""))}"></label>
        <label class="field">{esc(_("Password"))}<input name="password" type="password" required minlength="8"
          autocomplete="new-password"></label>
        <label class="field">{esc(_("Confirm password"))}<input name="password2" type="password" required minlength="8"
          autocomplete="new-password"></label>
        {key}
        <button class="btn wide primary" type="submit">{esc(_("Create account"))}</button>
      </form>
      <p class="muted small"><a href="{BASE}/login">{esc(_("Back to sign in"))}</a></p>
    </section>""")


def mode_hint(value: str) -> str:
    return {
        "off": _("Nobody can register. Admins create the accounts."),
        "invite": _("Anyone with an invitation key can register."),
        "open": _("Anyone who can reach this page can register. They get the Member role."),
    }.get(value, "")


def mode_radios(current: str, name: str = "mode") -> str:
    return "".join(
        f'<label class="check"><input type="radio" name="{name}" value="{k}"{" checked" if k == current else ""}>'
        f'<span><b>{esc(mode_label(k))}</b><br><span class="muted small">{esc(mode_hint(k))}</span></span></label>'
        for k in signup.MODES)


def mode_card(session: dict, current: str) -> str:
    radios = mode_radios(current)
    return f"""
    <section class="card">
      <h2>{esc(_("Sign-up"))}</h2>
      <p class="muted">{esc(_("Let people create their own account from the sign-in page."))}</p>
      <form method="post" action="{BASE}/settings/signup" class="stack">{csrf_input(session)}
        {radios}
        <div class="form-foot"><button class="btn primary" type="submit">{esc(_("Save"))}</button></div>
      </form>
    </section>"""


def key_result_box(key: str, label: str) -> str:
    return f"""
    <section class="card keybox inv-result">
      <h2>{esc(_("Invitation key created"))}</h2>
      <p>{esc(_("Copy it now: it is not shown again."))}{" " + esc(label) if label else ""}</p>
      <div class="code"><code>{esc(key)}</code>{copy_btn(key)}</div>
    </section>"""


def _limits(key: dict) -> str:
    uses = _("{used} of {max} uses", used=key["uses"], max=key["max_uses"]) if key["max_uses"] else \
        _("{used} uses (unlimited)", used=key["uses"])
    return uses


def keys_section(session: dict, keys: list[dict], current: str) -> str:
    rows = ""
    for k in keys:
        state = (_("Revoked") if k["revoked"] else _("Active") if k["active"] else _("Used up or expired"))
        expires = time_tag(k["expires"]) if k["expires"] else esc(_("Never"))
        revoke = (f'<form method="post" action="{BASE}/signup/keys/{k["id"]}/revoke">{csrf_input(session)}'
                  f'<button class="btn" type="submit">{esc(_("Revoke"))}</button></form>') if k["active"] else ""
        rows += (f"<tr><td>{esc(k['label'] or '—')}</td><td>{esc(_limits(k))}</td><td>{expires}</td>"
                 f"<td>{esc(state)}</td><td class='actions'>{revoke}</td></tr>")
    table = (f'<div class="table-wrap"><table class="simple"><thead><tr><th>{esc(_("Label"))}</th>'
             f'<th>{esc(_("Uses"))}</th><th>{esc(_("Expires"))}</th><th>{esc(_("State"))}</th><th></th></tr></thead>'
             f'<tbody>{rows}</tbody></table></div>') if keys else \
        f'<p class="muted">{esc(_("No invitation keys yet."))}</p>'
    uses = "".join(f'<option value="{u}">{esc(_("Unlimited") if u == "0" else _("{n} uses", n=u) if u != "1" else _("Single use"))}</option>'
                   for u in signup.KEY_USES)
    days = "".join(f'<option value="{d}"{" selected" if d == "7" else ""}>{esc(_("Never") if d == "0" else _("{n} days", n=d))}</option>'
                   for d in signup.KEY_DAYS)
    note = "" if current == "invite" else notice(
        "warn", _("Sign-up is not set to “With an invitation key”, so these keys do nothing yet. Change it in Settings."))
    return f"""
    <section class="card">
      <h2>{esc(_("Invitation keys"))}</h2>
      <p class="muted">{esc(_("People with a key can create their own account at the sign-up page."))}</p>
      {note}{table}
      <form method="post" action="{BASE}/signup/keys" class="stack">{csrf_input(session)}
        <label class="field">{esc(_("Label (optional)"))}<input name="label" maxlength="60" autocomplete="off"></label>
        <label class="field">{esc(_("Uses"))}<select name="uses">{uses}</select></label>
        <label class="field">{esc(_("Expires"))}<select name="days">{days}</select></label>
        <div class="form-foot"><button class="btn primary" type="submit">{esc(_("Create key"))}</button></div>
      </form>
    </section>"""
