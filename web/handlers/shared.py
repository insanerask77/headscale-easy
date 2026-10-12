"""State and helpers shared by every part of the console's request handling.

Settings read from the environment (as module-level names, so tests can patch them in one place), the
signed-cookie helpers, the role rules and the lookups that depend on them. Handler code reaches these
through the module (``sh.SSO``), never by copying the value, so a patched value is seen everywhere.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import admin_pages
import backup_pages
import headscale as hs
import live
import local_accounts as lac
import machines_pages
import signing
import signup
from config import Settings, csv_set
from i18n import _
from ui import BASE, DEMO  # noqa: F401 - DEMO is read as sh.DEMO

log = logging.getLogger("headscale-easy")

BACKUP_UPLOAD_MAX = backup_pages.UPLOAD_MAX_MB * 1024 * 1024  # bytes of a backup file the console accepts

# -----------------------------------------------------------------------------
# Configuration (environment)
# -----------------------------------------------------------------------------


def _csv(name: str, default: str = "") -> set[str]:
    return csv_set(os.environ, name, default)


SETTINGS = Settings.from_env(os.environ)
# Module-level names kept for the code (and the tests) that read or patch them.
PUBLIC_URL = SETTINGS.public_url
OIDC_ISSUER = SETTINGS.oidc_issuer
OIDC_CLIENT_ID = SETTINGS.oidc_client_id
OIDC_CLIENT_SECRET = SETTINGS.oidc_client_secret
# The scopes asked at sign-in (the same OIDC_SCOPE Headscale gets). A provider that only sends the groups
# claim when asked for it (Pocket ID: "groups") needs it here for PORTAL_*_GROUPS to work.
OIDC_SCOPE = SETTINGS.oidc_scope
SSO = SETTINGS.sso
API_KEY_LOGIN = SETTINGS.api_key_login
ADMIN_GROUPS = set(SETTINGS.admin_groups)
ADMIN_EMAILS = set(SETTINGS.admin_emails)
# Two narrower roles, opt-in only (empty unless configured): a network admin
# edits the ACL policy and DNS; an auditor sees everything an admin sees but
# can never change anything. Both are provider-group based only -- unlike
# ADMIN_GROUPS there is no sensible default group name to grant them from.
NETWORK_ADMIN_GROUPS = set(SETTINGS.network_admin_groups)
AUDITOR_GROUPS = set(SETTINGS.auditor_groups)
SESSION_SECRET = SETTINGS.session_secret
TAILNET_NAME = SETTINGS.tailnet_name
# MFA requirement for local accounts: admins, everyone, or optional
MFA_REQUIRED = SETTINGS.mfa_required

SECURE_COOKIES = SETTINGS.secure_cookies
SESSION_TTL = 8 * 3600
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
REDIRECT_URI = f"{PUBLIC_URL}{BASE}/callback"
SERVER_HOST = SETTINGS.server_host
CTX = {"public_url": PUBLIC_URL, "tailnet": TAILNET_NAME, "server_host": SERVER_HOST}
# The sign-in page shows its "Create an account" link when sign-up is on (local accounts only)
HUB = live.Hub(lambda: hs.all_nodes())  # one poller for every open stream
admin_pages.signup_open = lambda: signup.mode() != "off"

# Valid names: node given name (DNS label) and Headscale user name
NODE_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
USER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._@-]{0,62}$")
TAG_RE = re.compile(r"^tag:[a-z0-9][a-z0-9-]{0,62}$")
AUTH_ID_RE = re.compile(r"^[A-Za-z0-9_:-]{8,200}$")
# Device approval page: Caddy sends Headscale's /register/<auth id> link here
REGISTER_PATH_RE = re.compile(rf"{BASE}/register/([A-Za-z0-9_:-]{{8,200}})")
KEY_DAYS = {"1", "7", "30", "90"}
APIKEY_DAYS = {"30", "90", "365"}
INVITE_DAYS = ("1", "7", "30")
EXIT_ROUTES = ["0.0.0.0/0", "::/0"]
# Public demo (DEMO_MODE=true): what visitors may not do. Anything that grants
# access (auth keys, API keys, invitations, reset links, registering devices),
# changes the network or sign-in for everyone (DNS, 2FA, key expiry, the ACL
# policy from the visual editor; saving it from Advanced is checked in do_POST)
# or removes data.
DEMO_BLOCKED = re.compile(
    rf"{BASE}/(keys|add/docker|apikeys(/\d+/expire)?|machines/(register|remove-inactive)|register/[^/]+|machines/\d+/(delete|expire)"
    rf"|machines/bulk/(expire|remove)|settings/(key-expiry|notify-test|sessions/revoke(-all)?)|users(/\d+/(rename|delete))?"
    rf"|invitations(/[0-9a-f-]+/revoke)?|dns|derp"
    rf"|acl/(rules|groups|tags|autoapprove/(routes|exit-node)|ssh))")


# -----------------------------------------------------------------------------
# Signed cookies
# -----------------------------------------------------------------------------

def sign(data: dict) -> str:
    return signing.sign(data, SESSION_SECRET)


def unsign(value: str | None) -> dict | None:
    return signing.unsign(value, SESSION_SECRET)


_discovery: dict | None = None


def discovery() -> dict:
    """OIDC configuration of the provider (cached after the first read)."""
    global _discovery
    if _discovery is None:
        _discovery = hs.http_json("GET", OIDC_ISSUER.rstrip("/") + "/.well-known/openid-configuration")
    return _discovery


# -----------------------------------------------------------------------------
# Data for the pages
# -----------------------------------------------------------------------------

def to_machines(nodes: list[dict]) -> list[machines_pages.Machine]:
    details = hs.host_details([str(n["id"]) for n in nodes])
    dns = hs.dns_config()
    latest = hs.latest_tailscale_version()
    regions = hs.derp_regions()
    machines = [machines_pages.Machine(n, details.get(str(n["id"])), dns, latest, regions) for n in nodes]
    return sorted(machines, key=lambda m: (not m.online, m.name))


def exit_nodes_for(session: dict) -> list[dict]:
    """Approved exit nodes the Docker tab may offer: all of them to admins, only their own devices to members."""
    me = None if session.get("admin") else my_user(session)
    out = []
    for n in hs.all_nodes():
        if not set(n.get("approvedRoutes") or []) & set(EXIT_ROUTES):
            continue
        if not session.get("admin") and (me is None or str(n.get("user", {}).get("id")) != str(me["id"])):
            continue
        ips = [i for i in n.get("ipAddresses") or [] if ":" not in i]
        if ips:
            out.append({"name": n.get("givenName") or n.get("name") or ips[0], "ip": ips[0]})
    return out


def my_user(session: dict) -> dict | None:
    """The session's Headscale user (None for API key sessions).

    For OIDC sessions: looks up by providerId.
    For local sessions: looks up by headscale_user from the local account.
    """
    sub = session.get("sub")
    if not sub:
        return None

    # Local account session: sub format is "local:{account_id}"
    if session.get("kind") == "local" and sub.startswith("local:"):
        try:
            account_id = int(sub.split(":", 1)[1])
            account = lac.get_account(id=account_id)
            if account and account.get("headscale_user"):
                return hs.user_by_name(account["headscale_user"])
        except (ValueError, IndexError):
            pass
        return None

    # OIDC session: look up by providerId
    return hs.user_for_sub(sub)


def role_of(groups: list[str], email: str, email_verified: bool = True) -> str:
    """The session role from an OIDC identity's groups/email: admin (from
    PORTAL_ADMIN_GROUPS or PORTAL_ADMIN_EMAILS) takes priority, then network
    admin, then auditor (both PORTAL_*_GROUPS only), else member. An email the
    provider reports as unverified never counts for PORTAL_ADMIN_EMAILS."""
    if bool(set(groups) & ADMIN_GROUPS) or bool(email_verified and email and email.lower() in ADMIN_EMAILS):
        return "admin"
    if set(groups) & NETWORK_ADMIN_GROUPS:
        return "network_admin"
    if set(groups) & AUDITOR_GROUPS:
        return "auditor"
    return "member"


def is_auditor(session: dict) -> bool:
    return session.get("role") == "auditor"


def can_edit_network(session: dict) -> bool:
    """Admins and network admins can edit the ACL policy and DNS -- the two
    surfaces roadmap item 12 scopes "Network admin" to."""
    return bool(session.get("admin")) or session.get("role") == "network_admin"


def visible_nodes(session: dict) -> list[dict]:
    if session.get("admin") or is_auditor(session):
        return hs.all_nodes()
    user = my_user(session)
    return hs.user_nodes(user) if user else []


def node_for(session: dict, node_id: str) -> dict | None:
    """The node if the session may manage it: any node for an admin or an
    auditor (read only -- machine_action() blocks auditors before any write),
    only their own for a member. Every action on a node goes through here."""
    if session.get("admin") or is_auditor(session):
        return hs.get_node(node_id)
    return hs.owned_node(my_user(session), node_id)


def dns_ctx() -> dict:
    """Can DNS be edited from here? Needs the supervisor's control socket and the marked
    DNS block in config.yaml."""
    ctx = dict(CTX)
    try:
        with open(hs.HEADSCALE_CONFIG, encoding="utf-8") as fh:
            marked = hs.dns_block_present(fh.read())
        writable = os.access(hs.HEADSCALE_CONFIG, os.W_OK)
    except OSError:
        marked = writable = False
    if not marked:
        ctx["dns_reason"] = _("config.yaml has no managed DNS block.")
    elif not writable:
        ctx["dns_reason"] = _("Headscale Easy cannot write config.yaml.")
    elif not hs.control_available():
        ctx["dns_reason"] = _("The supervisor is not answering, so Headscale cannot be validated and "
                              "restarted from here. Check the container with: docker compose ps")
    ctx["dns_editable"] = "dns_reason" not in ctx
    return ctx


def lines(value: str) -> list[str]:
    return [x.strip() for x in re.split(r"[\n,]", value or "") if x.strip()]


def bulk_tags_from_form(form: dict) -> tuple[list[str], str]:
    """(tags, error) for the Machines bulk "Add tag…" dialog."""
    tags = [t.lower() for t in lines(str(form.get("tags", "")))]
    if not tags:
        return [], _("Add at least one tag.")
    bad = [t for t in tags if not TAG_RE.fullmatch(t)]
    if bad:
        return [], _("Invalid tag: {value}", value=bad[0])
    return tags, ""


def iso_in(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
