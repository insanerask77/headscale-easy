"""Invitations and password reset links for the built-in Authentik.

Admins invite people from Users instead of making up their passwords: an
invitation is a link, used up when the account is created, to the blueprint's
flow
"headscale-easy-invitation", where the person chooses their user name and
password. The access (member or admin) and, optionally, the email are fixed
in the invitation (fixed_data), so the invited person cannot change them.

A password reset link is Authentik's recovery link for one account: its owner
opens it and sets a new password (flow "headscale-easy-recovery", the brand's
recovery flow). No email needed: the admin copies the link. With SMTP set up
(SMTP_HOST...), both can also be emailed from here.

Console users are Headscale users; sign-in accounts live in Authentik. They
are matched by the OIDC subject: Headscale stores providerId =
<issuer>/<sub>, and the sub of the Headscale provider is the Authentik
account's "uid" (sub_mode hashed_user_id).

All calls use the web UI's service account token (AUTHENTIK_API_TOKEN), which
the blueprint allows to view accounts, make reset links and manage
invitations. The token never leaves the server.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import smtplib
import ssl
import threading
import time
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import headscale as hs
from i18n import _, ngettext
from pages import dialog
from ui import BASE, badge, copy_btn, esc, notice, time_tag

log = logging.getLogger("headscale-easy")

AUTHENTIK_URL = os.environ.get("AUTHENTIK_URL", "http://authentik-server:9000/authentik").rstrip("/")
TOKEN = os.environ.get("AUTHENTIK_API_TOKEN", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
INVITE_FLOW = "headscale-easy-invitation"
# Access chosen by the admin -> Authentik group (same as the add-user form)
ROLES = {"member": "headscale-users", "admin": "vpn-admins"}
INVITE_DAYS = ("1", "7", "30")
RESET_HOURS = ("1", "24", "168")
# Accounts nobody may reset from here (they administer Authentik itself)
PROTECTED_GROUP = "authentik Admins"
UUID_RE = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Outgoing email (optional): the same server Authentik uses
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT") or 25)
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "false").lower() == "true"
SMTP_USE_SSL = os.environ.get("SMTP_USE_SSL", "false").lower() == "true"
SMTP_FROM = os.environ.get("SMTP_FROM", "")


class AccountsError(Exception):
    """A readable reason why an Authentik operation failed."""


def available() -> bool:
    """Is there a token to talk to Authentik with?"""
    return bool(TOKEN)


def email_enabled() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


# -----------------------------------------------------------------------------
# Authentik API
# -----------------------------------------------------------------------------

def _api(method: str, path: str, body: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    try:
        return hs.http_json(method, f"{AUTHENTIK_URL}/api/v3{path}", headers=headers, body=data, timeout=10)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode(errors="replace")[:300]
        except OSError:
            pass
        log.warning("Authentik API %s %s: HTTP %s %s", method, path.split("?")[0], exc.code, detail)
        if exc.code == 401:
            raise AccountsError(_("Authentik rejected the web UI's token. Run ./install.sh once to set it up again.")) from exc
        if exc.code == 403:
            raise AccountsError(_("The web UI may not do that in Authentik yet. Run ./install.sh once to update its permissions.")) from exc
        if exc.code == 404:
            raise AccountsError(_("It no longer exists in Authentik.")) from exc
        raise AccountsError(_("Authentik answered with an error (HTTP {code}).", code=exc.code)) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("Authentik API %s %s: %s", method, path.split("?")[0], exc)
        raise AccountsError(_("Could not reach Authentik. Try again in a few seconds.")) from exc


def _pages(path: str) -> list[dict]:
    """All results of a paginated list."""
    out: list[dict] = []
    page = 1
    sep = "&" if "?" in path else "?"
    while page and len(out) < 5000:
        data = _api("GET", f"{path}{sep}page_size=200&page={page}")
        out += data.get("results") or []
        page = (data.get("pagination") or {}).get("next") or 0
    return out


def public_link(link: str) -> str:
    """Authentik builds links from the host the web UI called it on
    (authentik-server:9000): make them point at the public URL."""
    parts = urllib.parse.urlsplit(link)
    return PUBLIC_URL + parts.path + (f"?{parts.query}" if parts.query else "")


# A few seconds of cache: the Users page refreshes itself every 5 s
_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 20


def _cached(key: str, fetch):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            return hit[1]
    value = fetch()
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


def _forget() -> None:
    with _cache_lock:
        _cache.clear()


# -----------------------------------------------------------------------------
# Accounts
# -----------------------------------------------------------------------------

def _account(u: dict) -> dict:
    groups = sorted(g.get("name", "") for g in (u.get("groups_obj") or []))
    return {
        "pk": u.get("pk"),
        "uid": u.get("uid") or "",
        "username": u.get("username") or "",
        "name": u.get("name") or "",
        "email": u.get("email") or "",
        "groups": groups,
        "active": bool(u.get("is_active")),
        "protected": bool(u.get("is_superuser")) or PROTECTED_GROUP in groups,
        "admin": "vpn-admins" in groups,
        "last_login": u.get("last_login"),
    }


def accounts() -> list[dict]:
    """People's sign-in accounts (internal users; no service accounts)."""
    return _cached("accounts", lambda: [
        _account(u) for u in _pages("/core/users/?type=internal&include_roles=false")])


def account(pk: str) -> dict:
    if not str(pk).isdigit():
        raise AccountsError(_("It no longer exists in Authentik."))
    return _account(_api("GET", f"/core/users/{pk}/?include_roles=false"))


def match(users: list[dict], accts: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    """Headscale user id -> account, and the accounts with no Headscale user
    yet (they never connected a device) that can use the tailnet."""
    by_uid = {a["uid"]: a for a in accts if a["uid"]}
    linked: dict[str, dict] = {}
    for u in users:
        provider = u.get("providerId") or ""
        uid = provider.rstrip("/").rsplit("/", 1)[-1] if "/" in provider else ""
        if uid in by_uid:
            linked[str(u["id"])] = by_uid[uid]
    used = {a["pk"] for a in linked.values()}
    tailnet = {"headscale-users", "vpn-admins", PROTECTED_GROUP}
    waiting = [a for a in accts if a["pk"] not in used and a["active"] and tailnet & set(a["groups"])]
    return linked, sorted(waiting, key=lambda a: (a["name"] or a["username"]).lower())


def recovery_link(pk: str, hours: int) -> tuple[dict, str]:
    """(account, link) for a new single-use password reset link."""
    acct = account(pk)
    if acct["protected"]:
        raise AccountsError(_("Authentik administrators reset their password in Authentik."))
    if not acct["active"]:
        raise AccountsError(_("That account is disabled in Authentik."))
    try:
        data = _api("POST", f"/core/users/{acct['pk']}/recovery/", {"token_duration": f"hours={hours}"})
    except AccountsError as exc:
        if "(HTTP 400)" in str(exc):
            raise AccountsError(_("Authentik has no password reset flow for this account. Run ./install.sh once "
                                  "to create it.")) from exc
        raise
    link = data.get("link") or ""
    if not link:
        raise AccountsError(_("Authentik did not return a link."))
    return acct, public_link(link)


# -----------------------------------------------------------------------------
# Invitations
# -----------------------------------------------------------------------------

def invite_link(pk: str) -> str:
    return f"{PUBLIC_URL}/authentik/if/flow/{INVITE_FLOW}/?itoken={pk}"


def _flow_pk() -> str:
    def fetch():
        found = _api("GET", "/flows/instances/?" + urllib.parse.urlencode({"slug": INVITE_FLOW}))
        for flow in found.get("results") or []:
            if flow.get("slug") == INVITE_FLOW:
                return flow["pk"]
        return ""
    pk = _cached("flow", fetch)
    if not pk:
        raise AccountsError(_("The invitation flow was not found in Authentik. Run ./install.sh once to create it."))
    return pk


def _invitation(i: dict) -> dict:
    fixed = i.get("fixed_data") or {}
    role = next((k for k, v in ROLES.items() if v == fixed.get("hse_group")), "")
    return {
        "pk": str(i.get("pk")),
        "email": fixed.get("email") or "",
        "role": role,
        "expires": i.get("expires"),
        "by": fixed.get("hse_invited_by") or ((i.get("created_by") or {}).get("username") or ""),
        "link": invite_link(str(i.get("pk"))),
    }


def invitations() -> list[dict]:
    """Pending (not used, not expired) invitations to Headscale Easy."""
    def fetch():
        now = datetime.now(timezone.utc)
        out = []
        for i in _pages("/stages/invitation/invitations/?" + urllib.parse.urlencode({"flow__slug": INVITE_FLOW})):
            exp = i.get("expires")
            try:
                if exp and datetime.fromisoformat(exp.replace("Z", "+00:00")) <= now:
                    continue
            except ValueError:
                pass
            out.append(_invitation(i))
        return sorted(out, key=lambda x: x["expires"] or "")
    return _cached("invitations", fetch)


def create_invitation(role: str, email: str, days: int, invited_by: str) -> dict:
    if role not in ROLES:
        raise AccountsError(_("Choose one of the options."))
    email = email.strip()
    if email and (len(email) > 254 or not EMAIL_RE.fullmatch(email)):
        raise AccountsError(_("That email address is not valid."))
    if email and any(a["email"].lower() == email.lower() for a in accounts()):
        raise AccountsError(_("A user with this email already exists."))
    base = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0].lower()).strip("-")[:30] if email else "link"
    fixed = {"hse_group": ROLES[role], "hse_invited_by": invited_by[:100]}
    if email:
        fixed["email"] = email
    expires = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    created = _api("POST", "/stages/invitation/invitations/", {
        "name": f"hse-{base or 'link'}-{secrets.token_hex(3)}",
        "expires": expires,
        # Used up by the flow when the account is created (not when the link
        # is opened): see "Headscale Easy: invitation group" in the blueprint
        "single_use": False,
        "flow": _flow_pk(),
        "fixed_data": fixed,
    })
    _forget()
    return _invitation(created)


def revoke_invitation(pk: str) -> dict | None:
    """Delete a pending invitation; returns it (None if it did not exist)."""
    if not UUID_RE.fullmatch(pk or ""):
        return None
    found = next((i for i in invitations() if i["pk"].replace("-", "") == pk.replace("-", "")), None)
    if found is None:
        return None
    try:
        _api("DELETE", f"/stages/invitation/invitations/{urllib.parse.quote(found['pk'])}/")
    finally:
        _forget()
    return found


# -----------------------------------------------------------------------------
# Email (optional)
# -----------------------------------------------------------------------------

def send_mail(to: str, subject: str, text: str) -> None:
    if not email_enabled():
        raise AccountsError(_("Email is not set up."))
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=(SMTP_FROM.rsplit("@", 1)[-1].strip("> ") or None))
    msg.set_content(text)
    try:
        if SMTP_USE_SSL:
            server: smtplib.SMTP = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15,
                                                    context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
        with server:
            if SMTP_USE_TLS and not SMTP_USE_SSL:
                server.starttls(context=ssl.create_default_context())
            if SMTP_USERNAME:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("could not send email to %s: %s", to, exc)
        raise AccountsError(_("The email could not be sent: {reason}", reason=str(exc)[:120])) from exc


def email_invitation(inv: dict, tailnet: str) -> None:
    name = tailnet or "Headscale Easy"
    role = _("admin") if inv["role"] == "admin" else _("member")
    send_mail(inv["email"], _("You are invited to {name}", name=name), _(
        "Hello,\n\n{by} invited you to the {name} network ({role}).\n\n"
        "Create your account with this link (it works once):\n{link}\n\n"
        "Then install Tailscale on your devices and connect them to {server} "
        "(the web console explains how).\n",
        by=inv["by"] or _("An admin"), name=name, role=role, link=inv["link"], server=PUBLIC_URL))


def email_reset(acct: dict, link: str, hours: int) -> None:
    send_mail(formataddr((acct["name"], acct["email"])), _("Reset your password"), _(
        "Hello {name},\n\nAn admin created a link for you to choose a new password:\n{link}\n\n"
        "It works once and expires in {span}. If you did not expect it, ignore this email.\n",
        name=acct["name"] or acct["username"], link=link, span=hours_label(hours)))


def hours_label(hours: int) -> str:
    if hours % 24 == 0:
        return ngettext("{n} day", "{n} days", hours // 24)
    return ngettext("{n} hour", "{n} hours", hours)


# -----------------------------------------------------------------------------
# Users page fragments
# -----------------------------------------------------------------------------

FLASH = {
    "invite-revoked": ("ok", lambda: _("Invitation revoked.")),
}


def flash_html(code: str) -> str:
    return notice(FLASH[code][0], FLASH[code][1]()) if code in FLASH else ""


def role_badge(role: str) -> str:
    return badge(_("Admin"), "blue") if role == "admin" else badge(_("Member"))


def invite_button() -> str:
    return f'<button class="btn primary" type="button" data-open="invite-user">{esc(_("Invite user"))}</button>'


def invite_dialog(session: dict) -> str:
    days = "".join(f'<option value="{d}" {"selected" if d == "7" else ""}>{esc(ngettext("{n} day", "{n} days", int(d)))}</option>'
                   for d in INVITE_DAYS)
    mail = (f'<label class="check"><input type="checkbox" name="send" value="1" checked>'
            f'<span>{esc(_("Email the link to that address"))}</span></label>' if email_enabled() else "")
    fields = f"""
      <fieldset class="field inv-roles"><legend>{esc(_("Access"))}</legend>
        <label class="check"><input type="radio" name="role" value="member" checked><span>{esc(_("Member: can connect their own devices"))}</span></label>
        <label class="check"><input type="radio" name="role" value="admin"><span>{esc(_("Admin: manages the whole tailnet"))}</span></label>
      </fieldset>
      <label class="field">{esc(_("Email (optional)"))}<input name="email" type="email" maxlength="254"
        placeholder="ada@example.com" autocomplete="off" spellcheck="false">
        <span class="muted small">{esc(_("If set, the account must use this email."))}</span></label>
      {mail}
      <label class="field">{esc(_("Link valid for"))}<select name="days">{days}</select></label>"""
    return dialog("invite-user", _("Invite user"),
                  esc(_("Creates a single-use link. Whoever opens it chooses their user name and password, "
                        "and gets the access you choose here.")),
                  f"{BASE}/invitations", session, fields=fields, submit=_("Create invitation"))


def reset_item(acct: dict | None) -> str:
    """Menu entry for a user row ("" when there is no account to reset)."""
    if not acct or acct["protected"] or not acct["active"]:
        return ""
    return f'<button type="button" data-open="reset-{esc(acct["pk"])}">{esc(_("Password reset link…"))}</button>'


def reset_dialog(session: dict, acct: dict) -> str:
    hours = "".join(f'<option value="{h}" {"selected" if h == "24" else ""}>{esc(hours_label(int(h)))}</option>'
                    for h in RESET_HOURS)
    mail = ""
    if email_enabled() and acct["email"]:
        mail = (f'<label class="check"><input type="checkbox" name="send" value="1">'
                f'<span>{esc(_("Also email it to {email}", email=acct["email"]))}</span></label>')
    fields = f'<label class="field">{esc(_("Link valid for"))}<select name="hours">{hours}</select></label>{mail}'
    who = acct["name"] or acct["username"]
    return dialog(f"reset-{esc(acct['pk'])}", _("Password reset link for {name}", name=who),
                  esc(_("Creates a single-use link where {user} chooses a new password. Their current password "
                        "keeps working until then. Send it to them privately.", user=acct["username"])),
                  f"{BASE}/accounts/{esc(acct['pk'])}/recovery", session, fields=fields, submit=_("Create link"))


def result_box(result: dict) -> str:
    """The link just created (invitation or reset), to copy."""
    link = result["link"]
    if result["kind"] == "invite":
        title = _("Invitation created")
        text = _("Send this link to the person you are inviting. It works once.")
    else:
        title = _("Password reset link created")
        text = _("Send this link to {user} privately. It works once.", user=result["username"])
    sent = _("Sent by email to {email}.", email=result["email"]) if result.get("sent") else ""
    return f"""
    <section class="card keybox inv-result">
      <h2>{esc(title)}</h2>
      <p>{esc(text)} {esc(_("Expires:"))} {time_tag(result.get("expires"))}</p>
      <div class="code"><code>{esc(link)}</code>{copy_btn(link)}</div>
      {f'<p class="muted small">{esc(sent)}</p>' if sent else ""}
    </section>"""


def sections(session: dict, data: dict) -> str:
    """Pending invitations and accounts without devices, below the users."""
    if data.get("error"):
        return f'<section class="inv-section">{notice("error", data["error"])}</section>'
    out = []
    invs = data.get("invitations") or []
    rows, dialogs = [], []
    for inv in invs:
        pk = esc(inv["pk"])
        rows.append(f"""
        <tr>
          <td>{esc(inv["email"]) if inv["email"] else f'<span class="muted">{esc(_("Anyone with the link"))}</span>'}</td>
          <td>{role_badge(inv["role"])}</td>
          <td>{time_tag(inv["expires"])}</td>
          <td class="hide-sm">{esc(inv["by"])}</td>
          <td class="actions inv-actions">{copy_btn(inv["link"], _("Copy link"))}
            <button type="button" class="btn small inv-danger" data-open="revoke-{pk}">{esc(_("Revoke"))}</button></td>
        </tr>""")
        dialogs.append(dialog(f"revoke-{pk}", _("Revoke this invitation?"),
                              esc(_("The link stops working. You can create a new one at any time.")),
                              f"{BASE}/invitations/{pk}/revoke", session, submit=_("Revoke"), danger=True))
    out.append(f"""
    <section class="inv-section">
      <h2>{esc(_("Pending invitations"))}</h2>
      {f'''<div class="table-wrap"><table class="machines inv-table">
        <thead><tr><th>{esc(_("Email"))}</th><th>{esc(_("Access"))}</th><th>{esc(_("Expires"))}</th>
          <th class="hide-sm">{esc(_("Invited by"))}</th><th></th></tr></thead>
        <tbody>{"".join(rows)}</tbody></table></div>''' if rows else
        f'<p class="muted">{esc(_("No pending invitations. Invite someone with Invite user."))}</p>'}
      {"".join(dialogs)}
    </section>""")

    waiting = data.get("waiting") or []
    if waiting:
        rows, dialogs = [], []
        for a in waiting:
            menu = reset_item(a)
            rows.append(f"""
        <tr>
          <td><b>{esc(a["name"] or a["username"])}</b><div class="owner">{esc(a["email"])}</div></td>
          <td><code>{esc(a["username"])}</code></td>
          <td>{role_badge("admin" if a["admin"] or a["protected"] else "member")}</td>
          <td class="actions">{menu.replace('type="button"', 'type="button" class="btn small"', 1) if menu else ""}</td>
        </tr>""")
            if menu:
                dialogs.append(reset_dialog(session, a))
        out.append(f"""
    <section class="inv-section">
      <h2>{esc(_("Accounts without devices"))}</h2>
      <p class="muted">{esc(_("They can sign in but have not connected a device yet, so they are not Headscale users yet."))}</p>
      <div class="table-wrap"><table class="machines inv-table">
        <thead><tr><th>{esc(_("Name"))}</th><th>{esc(_("User name"))}</th><th>{esc(_("Access"))}</th><th></th></tr></thead>
        <tbody>{"".join(rows)}</tbody></table></div>
      {"".join(dialogs)}
    </section>""")
    return "".join(out)


def page_data(users: list[dict]) -> dict:
    """Everything the Users page needs from Authentik; never raises."""
    if not available():
        return {"error": _("Invitations and password reset links need the web UI's access to Authentik: "
                           "run ./install.sh once to set it up.")}
    try:
        accts = accounts()
        linked, waiting = match(users, accts)
        return {"linked": linked, "waiting": waiting, "invitations": invitations()}
    except AccountsError as exc:
        return {"error": str(exc)}
