"""The setup wizard's pages (HTML), built from the console's own components: card, field, button, notice."""

from __future__ import annotations

import datetime

import cron
import render
import signup_pages
from i18n import LANGUAGES, _
from ui import BASE, LOGO, bare_page, esc, notice
from wizard_checks import TLS_MODES

SETUP = BASE + "/setup"
STEPS = ("language", "server", "admin", "signup", "network", "derp", "backups", "finish")

# -----------------------------------------------------------------------------
# Pages (the console's own components: card, field, btn, notice)
# -----------------------------------------------------------------------------

def _csrf(sess: dict) -> str:
    return f'<input type="hidden" name="csrf" value="{esc(sess["csrf"])}">'


def _progress(step: str | None) -> str:
    if step not in STEPS:
        return ""
    at = STEPS.index(step)
    dots = "".join(f'<i class="{"done" if i < at else "now" if i == at else ""}"></i>' for i in range(len(STEPS)))
    label = _("Step {n} of {total}", n=at + 1, total=len(STEPS))
    return f'<div class="setup-steps" role="img" aria-label="{esc(label)}">{dots}</div><p class="muted small">{esc(label)}</p>'


def _card(title: str, intro: str, form_inner: str, sess: dict, step: str | None = None, error: str | None = None,
          submit: str | None = None, heading: str | None = None) -> str:
    action = SETUP + ("/" + step if step else "")
    return bare_page(title, f"""
    <section class="card narrow center login setup">
      <div class="big-logo">{LOGO}</div>
      <h1>{esc(heading or title)}</h1>
      {_progress(step)}
      <p class="muted">{esc(intro)}</p>
      {notice("error", error) if error else ""}
      <form method="post" action="{action}" class="stack">
        {_csrf(sess)}
        {form_inner}
        <button class="btn wide primary" type="submit">{esc(submit or _("Continue"))}</button>
      </form>
    </section>""")


def _field(label: str, control: str, hint: str = "") -> str:
    note = '<span class="muted">' + esc(hint) + "</span>" if hint else ""
    return f'<label class="field">{esc(label)}{control}{note}</label>'


def token_page(sess: dict, error: str | None = None) -> str:
    return _card(_("Set up Headscale Easy"),
                 _("Enter the setup token printed in the container logs (or stored in /data/config/setup-token)."),
                 _field(_("Setup token"), '<input type="password" name="token" required autofocus '
                        'autocomplete="off" spellcheck="false">'), sess, error=error, submit=_("Unlock"),
                 heading="Headscale Easy")


def language_page(sess: dict, current: str) -> str:
    opts = "".join(f'<option value="{esc(k)}"{" selected" if k == current else ""}>{esc(v)}</option>'
                   for k, v in LANGUAGES.items())
    return _card(_("Language"), _("Choose the language of the web interface."),
                 _field(_("Language"), f'<select name="lang">{opts}</select>'), sess, "language")


def server_page(sess: dict, v: dict, error: str | None = None) -> str:
    tls = v.get("tls", "auto")
    labels = {"auto": _("Automatic (Let's Encrypt)"), "internal": _("Internal certificate (self-signed)"),
              "off": _("No HTTPS here (plain HTTP, or HTTPS handled in front)")}
    opts = "".join(f'<option value="{k}"{" selected" if k == tls else ""}>{esc(labels[k])}</option>'
                   for k in TLS_MODES)
    inner = (_field(_("Public URL"), f'<input type="text" name="public_url" required autofocus spellcheck="false" '
                    f'value="{esc(v.get("public_url", ""))}" placeholder="https://hs.example.com">') +
             _field(_("HTTPS certificates"), f'<select name="tls">{opts}</select>') +
             _field(_("Email for Let's Encrypt (automatic certificates only)"),
                    f'<input type="email" name="acme_email" value="{esc(v.get("acme_email", ""))}">'))
    return _card(_("Server address"), _("The address your devices and browsers will use to reach this server."),
                 inner, sess, "server", error)


def admin_page(sess: dict, v: dict, error: str | None = None) -> str:
    inner = (_field(_("Email"), f'<input type="email" name="email" required autofocus autocomplete="email" '
                    f'value="{esc(v.get("email", ""))}">') +
             _field(_("Username"), f'<input type="text" name="username" required autocomplete="username" '
                    f'spellcheck="false" value="{esc(v.get("username", ""))}" pattern="[a-zA-Z0-9_-]{{3,32}}" '
                    f'title="{esc(_("3-32 characters: letters, numbers, - and _"))}">') +
             _field(_("Password"), f'<input type="password" name="password" required minlength="8" '
                    f'autocomplete="new-password" placeholder="{esc(_("At least 8 characters"))}">') +
             _field(_("Confirm password"), '<input type="password" name="password2" required '
                    'autocomplete="new-password">') +
             f'<p class="muted small">{esc(_("You can turn on two-factor authentication from the console after signing in."))}</p>')
    return _card(_("Administrator"), _("Create the first administrator account."), inner, sess, "admin", error)


def network_page(sess: dict, v: dict, error: str | None = None) -> str:
    name = v.get("tailnet_name", "myorg")
    iso = v.get("network_isolation", "true") == "true"
    inner = (_field(_("Tailnet name"), f'<input type="text" name="tailnet_name" required maxlength="32" '
                    f'spellcheck="false" value="{esc(name)}" pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?">') +
             _field(_("MagicDNS base domain"), f'<input type="text" name="base_domain" maxlength="253" '
                    f'spellcheck="false" placeholder="{esc(render.DEFAULT_BASE_DOMAIN)}" '
                    f'value="{esc(v.get("base_domain", ""))}">',
                    _("Devices get names like device.{name}. Leave it empty to use {default}.",
                      name="<base>", default=render.DEFAULT_BASE_DOMAIN)) +
             f'<label class="check"><input type="checkbox" name="isolation" value="1"{" checked" if iso else ""}>'
             f'<span>{esc(_("Isolate users: each user only reaches their own devices"))}</span></label>')
    return _card(_("Network"), _("Name your tailnet and choose how users see each other."), inner, sess,
                 "network", error)


def signup_page(sess: dict, v: dict, error: str | None = None) -> str:
    inner = (signup_pages.mode_radios(v.get("signup_mode") or "off") +
             f'<label class="check"><input type="checkbox" name="first_key" value="1"{" checked" if v.get("first_key") else ""}>'
             f'<span>{esc(_("With an invitation key: create a first key (single use, valid for 7 days) and show it when setup ends"))}</span></label>')
    return _card(_("Sign-up"), _("Let people create their own account from the sign-in page."), inner, sess,
                 "signup", error)


def derp_page(sess: dict, v: dict, error: str | None = None) -> str:
    mode = v.get("derp_mode") or "embedded"
    options = (("embedded", _("This server (recommended)"),
                _("Relays and STUN run in this container on UDP 3478. Nothing goes through third parties.")),
               ("public", _("Tailscale's public relays"),
                _("Adds Tailscale's public DERP map. More locations, but traffic may use their servers.")),
               ("custom", _("My own DERP map"), _("Use a DERP map you host. You can also upload one from the console.")))
    radios = "".join(
        f'<label class="check"><input type="radio" name="derp_mode" value="{k}"{" checked" if k == mode else ""}>'
        f'<span><b>{esc(title)}</b><br><span class="muted small">{esc(text)}</span></span></label>'
        for k, title, text in options)
    inner = radios + _field(_("DERP map URL (own map only)"), f'<input type="url" name="derp_url" '
                            f'spellcheck="false" placeholder="https://" value="{esc(v.get("derp_url", ""))}">')
    return _card(_("Relays (DERP)"), _("Devices that cannot connect directly relay their traffic through DERP."),
                 inner, sess, "derp", error)


def _next_run_text(schedule: str) -> str:
    try:
        if cron.is_off(schedule):
            return ""
        return cron.next_run(schedule, datetime.datetime.now()).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ""


def backups_page(sess: dict, v: dict, error: str | None = None) -> str:
    schedule = v.get("backup_schedule", "0 3 * * *") or "0 3 * * *"
    enabled = v.get("backup_enabled", "off" if schedule == "off" else "on") != "off"
    if schedule == "off":
        schedule = "0 3 * * *"  # what turning them back on would use
    nxt = _next_run_text(schedule) if enabled else ""
    options = "".join(f'<option value="{val}"{" selected" if (val == "on") == enabled else ""}>{esc(label)}</option>'
                      for val, label in (("on", _("Enabled (recommended)")), ("off", _("Disabled"))))
    hint = _("Next run: {when}", when=nxt) if nxt else _("Five cron fields, in the container's time zone (TZ).")
    inner = (_field(_("Scheduled backups"), f'<select name="backup_enabled">{options}</select>') +
             _field(_("Backup schedule (cron)"), f'<input type="text" name="backup_schedule" '
                    f'spellcheck="false" value="{esc(schedule)}">', hint) +
             _field(_("Days to keep backups"), f'<input type="number" name="backup_keep_days" required min="1" '
                    f'max="3650" value="{esc(v.get("backup_keep_days", "14"))}">'))
    return _card(_("Backups"), _("Every night a backup is written to /data/backups and old ones are deleted after the "
                                 "days you choose. Backups contain every secret (accounts, keys): keep them private."),
                 inner, sess, "backups", error)


def finish_page(sess: dict, data: dict, error: str | None = None) -> str:
    s = data["server"]
    rows = [(_("Public URL"), s["public_url"]), (_("HTTPS certificates"), s["tls"]),
            (_("Administrator"), data["admin"]["email"]), (_("Tailnet name"), data["network"]["tailnet_name"]),
            (_("Sign-up"), signup_pages.mode_label(data["signup"]["signup_mode"])),
            (_("Relays (DERP)"), data["derp"]["derp_mode"]),
            (_("Backups"), _("Disabled") if data["backups"]["backup_schedule"] == "off"
             else data["backups"]["backup_schedule"])]
    summary = "".join(f'<div class="kv"><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>' for k, v in rows)
    return _card(_("Review and finish"), _("Everything is ready. Finishing starts the server; this can take a minute."),
                 f'<dl class="kvs">{summary}</dl>', sess, "finish", error,
                 submit=_("Retry") if error else _("Finish setup"))


def done_page(public_url: str, first_key: str = "") -> str:
    url = public_url + BASE + "/login"
    key_html = (f'<div class="code"><code>{esc(first_key)}</code></div>'
                f'<p class="muted small">{esc(_("Invitation key: copy it now, it is not shown again."))}</p>') if first_key else ""
    return bare_page(_("Setup complete"), f"""
    <section class="card narrow center login setup" data-await-console="{esc(url)}" data-probe="{BASE}/healthz">
      <div class="big-logo">{LOGO}</div>
      <h1>{esc(_("Setup complete"))}</h1>
      <p class="muted">{esc(_("Headscale Easy is starting. Sign in with the administrator account you just created."))}</p>
      <p class="muted small">{esc(_("With automatic certificates the first load can take a few seconds."))}</p>
      {key_html}
      <p class="muted small" data-await-waiting>{esc(_("Waiting for the console to start; you will be taken to the sign-in page."))}</p>
      <p class="muted small" data-await-slow hidden>{esc(_("The console is taking longer than expected. Check the container logs, then open it with the button below."))}</p>
      <a class="btn wide primary" href="{esc(url)}">{esc(_("Open the console"))}</a>
    </section>""")
