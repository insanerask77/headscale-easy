"""Headscale Easy — the web UI for Headscale.

  - Members see and manage only THEIR machines and keys.
  - Admins manage everything: machines, users, routes, tags, access control
    policy, DNS and API keys.

Sign-in:
  - OIDC (Authentik or your own provider), authorization code + PKCE. The role
    comes from the groups (PORTAL_ADMIN_GROUPS) or the email
    (PORTAL_ADMIN_EMAILS).
  - Headscale API key (PORTAL_API_KEY_LOGIN=true): an admin session, for
    installs without OIDC or as emergency access.

Every action checks the role on the server and, for members, that the machine
or key is theirs. Standard library only.

Headscale Easy · https://github.com/insanerask77/headscale-easy
Made by Rafa Madolell (@insanerask77) · https://ko-fi.com/rafaelmadolell
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("headscale-easy")

import admin_pages  # noqa: E402  (after logging is configured)
import headscale as hs  # noqa: E402
import apikey  # noqa: E402
import mfa  # noqa: E402
import naming  # noqa: E402
import pages  # noqa: E402
from i18n import LANGUAGES, _, pick_lang, set_lang  # noqa: E402
from ui import BASE, esc, message_page  # noqa: E402
from version import VERSION  # noqa: E402

# -----------------------------------------------------------------------------
# Configuration (environment)
# -----------------------------------------------------------------------------


def _csv(name: str, default: str = "") -> set[str]:
    return {x.strip() for x in os.environ.get(name, default).split(",") if x.strip()}


PUBLIC_URL = os.environ["PUBLIC_URL"].rstrip("/")
OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
SSO = bool(OIDC_ISSUER and OIDC_CLIENT_ID)
# Built-in Authentik (issuer .../authentik/application/o/<app>/): sign out
# through the blueprint's flow, see logout()
AUTHENTIK_SIGN_OUT = (OIDC_ISSUER.split("/application/o/")[0] + "/if/flow/headscale-easy-sign-out/"
                      if "/authentik/application/o/" in OIDC_ISSUER else "")
API_KEY_LOGIN = os.environ.get("PORTAL_API_KEY_LOGIN", "false").lower() == "true" or not SSO
ADMIN_GROUPS = _csv("PORTAL_ADMIN_GROUPS", "vpn-admins,authentik Admins")
ADMIN_EMAILS = {e.lower() for e in _csv("PORTAL_ADMIN_EMAILS")}
SESSION_SECRET = os.environ["SESSION_SECRET"].encode()
TAILNET_NAME = os.environ.get("TAILNET_NAME", "")
AUTHENTIK = "/authentik/" in OIDC_ISSUER

SECURE_COOKIES = PUBLIC_URL.startswith("https://")
SESSION_TTL = 8 * 3600
STATIC_DIR = Path(__file__).parent / "static"
REDIRECT_URI = f"{PUBLIC_URL}{BASE}/callback"
SERVER_HOST = urllib.parse.urlparse(PUBLIC_URL).hostname or ""
CTX = {"public_url": PUBLIC_URL, "tailnet": TAILNET_NAME, "authentik": AUTHENTIK, "server_host": SERVER_HOST}

# Valid names: node given name (DNS label) and Headscale user name
NODE_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
USER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._@-]{0,62}$")
TAG_RE = re.compile(r"^tag:[a-z0-9][a-z0-9-]{0,62}$")
AUTH_ID_RE = re.compile(r"^[A-Za-z0-9_:-]{8,200}$")
KEY_DAYS = {"1", "7", "30", "90"}
APIKEY_DAYS = {"30", "90", "365"}
EXIT_ROUTES = ["0.0.0.0/0", "::/0"]


# -----------------------------------------------------------------------------
# Signed cookies
# -----------------------------------------------------------------------------

def sign(data: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
    mac = hmac.new(SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{mac}"


def unsign(value: str | None) -> dict | None:
    if not value or "." not in value:
        return None
    payload, mac = value.rsplit(".", 1)
    expected = hmac.new(SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except ValueError:
        return None
    return data if data.get("exp", 0) >= time.time() else None


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

def to_machines(nodes: list[dict]) -> list[pages.Machine]:
    details = hs.host_details([str(n["id"]) for n in nodes])
    dns = hs.dns_config()
    latest = hs.latest_tailscale_version()
    regions = hs.derp_regions()
    machines = [pages.Machine(n, details.get(str(n["id"])), dns, latest, regions) for n in nodes]
    return sorted(machines, key=lambda m: (not m.online, m.name))


def my_user(session: dict) -> dict | None:
    """The session's Headscale user (None for API key sessions)."""
    return hs.user_for_sub(session["sub"]) if session.get("sub") else None


def visible_nodes(session: dict) -> list[dict]:
    if session.get("admin"):
        return hs.all_nodes()
    user = my_user(session)
    return hs.user_nodes(user) if user else []


def node_for(session: dict, node_id: str) -> dict | None:
    """The node if the session may manage it: any node for an admin, only
    their own for a member. Every action on a node goes through here."""
    if session.get("admin"):
        return hs.get_node(node_id)
    return hs.owned_node(my_user(session), node_id)


def dns_ctx() -> dict:
    """Can DNS be edited from here? Needs the Docker socket and the marked DNS
    block in config.yaml."""
    ctx = dict(CTX)
    try:
        with open(hs.HEADSCALE_CONFIG, encoding="utf-8") as fh:
            marked = hs.dns_block_present(fh.read())
        writable = os.access(hs.HEADSCALE_CONFIG, os.W_OK)
    except OSError:
        marked = writable = False
    if not marked:
        ctx["dns_reason"] = _("config.yaml has no managed DNS block: run ./install.sh once to enable it.")
    elif not writable:
        ctx["dns_reason"] = _("Headscale Easy cannot write config.yaml.")
    elif not hs.docker_available():
        ctx["dns_reason"] = _("Headscale Easy has no access to Docker to restart Headscale.")
    ctx["dns_editable"] = "dns_reason" not in ctx
    return ctx


def mfa_state() -> dict | None:
    """Two-factor mode for Settings -> General (admins). None without the
    built-in Authentik: then two-factor is up to the identity provider."""
    if not AUTHENTIK:
        return None
    if not mfa.available():
        return {"mode": mfa.DEFAULT, "editable": False,
                "reason": _("Set when installing (MFA_REQUIRED). To change it from here, run ./install.sh once: "
                            "it gives the web UI access to Authentik.")}
    try:
        return {"mode": mfa.current(), "editable": True}
    except mfa.MfaError as exc:
        return {"mode": mfa.saved() or mfa.DEFAULT, "editable": False, "reason": str(exc)}


def lines(value: str) -> list[str]:
    return [x.strip() for x in re.split(r"[\n,]", value or "") if x.strip()]


def iso_in(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


# -----------------------------------------------------------------------------
# HTTP
# -----------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "headscale-easy"
    sys_version = ""

    # --- responses ---
    def send(self, status: int, body: str | bytes, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        if ctype.startswith("text/html"):
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'")
        for k, v in (headers or []):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def redirect(self, location: str, headers=None):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        for k, v in (headers or []):
            self.send_header(k, v)
        self.end_headers()

    def fail(self, status: int, title: str, text: str):
        self.send(status, message_page(title, text))

    def cookie(self, name: str) -> str | None:
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return jar[name].value if name in jar else None

    @staticmethod
    def set_cookie(name: str, value: str, max_age: int) -> tuple[str, str]:
        attrs = [f"{name}={value}", f"Path={BASE}", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
        if SECURE_COOKIES:
            attrs.append("Secure")
        return ("Set-Cookie", "; ".join(attrs))

    def session(self) -> dict | None:
        return unsign(self.cookie("hse_session"))

    def form(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 262144:
            return {}
        data = urllib.parse.parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
        # 'route' (checkboxes) and fields named "...[]" (rows) can repeat
        return {k: (v if k == "route" or k.endswith("[]") else v[0]) for k, v in data.items()}

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    def set_request_lang(self):
        set_lang(pick_lang(self.cookie("hse_lang"), self.headers.get("Accept-Language")))

    # --- GET ---
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.set_request_lang()
        path, _q, query = self.path.partition("?")
        params = dict(urllib.parse.parse_qsl(query))
        flash = params.get("m", "")
        try:
            if path == f"{BASE}/healthz":
                return self.send(200, "ok", "text/plain")
            if path.startswith(f"{BASE}/static/"):
                return self.static(path[len(f"{BASE}/static/"):])
            if path == f"{BASE}/login":
                # After signing out, say so instead of starting a new sign-in
                if params.get("m") == "signed-out":
                    return self.send(200, admin_pages.login_page(SSO, API_KEY_LOGIN, info=_("You have signed out.")))
                if SSO and not API_KEY_LOGIN:
                    return self.start_sso()
                return self.send(200, admin_pages.login_page(SSO, API_KEY_LOGIN))
            if path == f"{BASE}/login/sso" and SSO:
                return self.start_sso()
            if path == f"{BASE}/callback" and SSO:
                return self.callback(params)

            session = self.session()
            if not session:
                return self.redirect(f"{BASE}/login")
            admin = session.get("admin")

            if path in (BASE, f"{BASE}/"):
                return self.redirect(f"{BASE}/machines")
            if path == f"{BASE}/machines":
                users = hs.all_users() if admin else None
                has_user = True if admin else my_user(session) is not None
                return self.send(200, pages.machines_page(session, CTX, to_machines(visible_nodes(session)),
                                                          has_user, flash, users))
            if path == f"{BASE}/machines.csv":
                data = pages.machines_csv(to_machines(visible_nodes(session)))
                return self.send(200, data, "text/csv; charset=utf-8",
                                 [("Content-Disposition", 'attachment; filename="machines.csv"')])
            m = re.fullmatch(rf"{BASE}/machines/(\d+)", path)
            if m:
                node = node_for(session, m.group(1))
                if node is None:
                    return self.redirect(f"{BASE}/machines?m=not-found")
                return self.send(200, pages.machine_page(session, CTX, to_machines([node])[0], flash))
            if path == f"{BASE}/add":
                return self.send(200, pages.add_page(session, CTX))
            if path == f"{BASE}/dns":
                return self.send(200, pages.dns_page(session, dns_ctx() if admin else CTX, hs.dns_config(),
                                                     to_machines(visible_nodes(session)), flash=flash))
            if path in (f"{BASE}/settings", f"{BASE}/settings/"):
                return self.redirect(f"{BASE}/settings/general")
            if path == f"{BASE}/settings/general":
                return self.send(200, pages.general_page(session, CTX, flash,
                                                         key_expiry=hs.key_expiry_days() if admin else None,
                                                         mfa=mfa_state() if admin else None))
            if path == f"{BASE}/settings/keys":
                return self.keys_view(session, flash, preselect=params.get("user", ""))

            # --- admins only ---
            if path in (f"{BASE}/users", f"{BASE}/acl") and not admin:
                return self.fail(403, _("No permission"), _("This section is for admins only."))
            if path == f"{BASE}/users":
                return self.send(200, admin_pages.users_page(session, CTX, hs.all_users(), hs.all_nodes(), flash))
            if path == f"{BASE}/acl":
                return self.send(200, admin_pages.acl_page(session, CTX, hs.get_policy(), flash))
            self.fail(404, _("Not found"), _("That page does not exist."))
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                # The web UI's API key expired (the server was off during the
                # renewal window, or someone expired it by hand)
                log.error("Headscale rejected the web UI's API key on GET %s", path)
                return self.fail(503, _("The web UI cannot reach Headscale"),
                                 _("Its API key has expired or was revoked. On the server, run ./install.sh: it creates a new one."))
            log.exception("error on GET %s", path)
            self.fail(500, _("Something went wrong"), _("The operation could not be completed. Try again in a few seconds."))
        except Exception:  # noqa: BLE001 - never show tracebacks in the UI
            log.exception("error on GET %s", path)
            self.fail(500, _("Something went wrong"), _("The operation could not be completed. Try again in a few seconds."))

    def keys_view(self, session: dict, flash: str, new_key: dict | None = None, new_apikey: str = "",
                  preselect: str = ""):
        if session.get("admin"):
            page = pages.keys_page(session, CTX, hs.all_keys(), flash, new_key, users=hs.all_users(),
                                   apikeys=hs.api_keys(), own_prefix=hs.own_api_key_prefix(),
                                   new_apikey=new_apikey, preselect=preselect)
        else:
            user = my_user(session)
            page = pages.keys_page(session, CTX, hs.user_keys(user) if user else None, flash, new_key)
        self.send(200, page)

    # --- POST ---
    def do_POST(self):
        self.set_request_lang()
        path = self.path.partition("?")[0]
        try:
            if path == f"{BASE}/login/apikey" and API_KEY_LOGIN:
                return self.apikey_login(self.form())

            session = self.session()
            if not session:
                return self.redirect(f"{BASE}/login")
            form = self.form()
            # CSRF: the form token must match the session's
            if not hmac.compare_digest(str(form.get("csrf", "")), session["csrf"]):
                return self.fail(403, _("Session expired"), _("Reload the page and try again."))

            if path == f"{BASE}/logout":
                return self.logout(session)
            if path == f"{BASE}/settings/language":
                lang = str(form.get("lang", ""))
                back = self.headers.get("Referer", "")
                dest = urllib.parse.urlparse(back).path if back.startswith(PUBLIC_URL) else f"{BASE}/settings/general"
                if not dest.startswith(BASE):
                    dest = f"{BASE}/settings/general"
                return self.redirect(dest, [self.set_cookie("hse_lang", lang if lang in LANGUAGES else "", 31536000)])
            if path == f"{BASE}/keys":
                return self.create_key(session, form)
            m = re.fullmatch(rf"{BASE}/keys/(\d+)/revoke", path)
            if m:
                return self.revoke_key(session, m.group(1))
            m = re.fullmatch(rf"{BASE}/machines/(\d+)/(rename|delete|expire|expiry|routes|tags)", path)
            if m:
                return self.machine_action(session, m.group(1), m.group(2), form)

            # --- admins only ---
            if not session.get("admin"):
                return self.fail(403, _("No permission"), _("This action is for admins only."))
            if path == f"{BASE}/machines/register":
                return self.register_node(session, form)
            if path == f"{BASE}/settings/key-expiry":
                return self.save_key_expiry(session, form)
            if path == f"{BASE}/settings/mfa":
                return self.save_mfa(session, form)
            if path == f"{BASE}/users":
                return self.create_user(session, form)
            m = re.fullmatch(rf"{BASE}/users/(\d+)/(rename|delete)", path)
            if m:
                return self.user_action(session, m.group(1), m.group(2), form)
            if path == f"{BASE}/acl":
                return self.save_acl(session, form)
            if path == f"{BASE}/dns":
                return self.save_dns(session, form)
            if path == f"{BASE}/apikeys":
                return self.create_apikey(session, form)
            m = re.fullmatch(rf"{BASE}/apikeys/(\d+)/expire", path)
            if m:
                return self.expire_apikey(m.group(1))
            self.send(404, "Not found", "text/plain")
        except Exception:  # noqa: BLE001
            log.exception("error on POST %s", path)
            self.redirect(f"{BASE}/machines?m=failed")

    def save_key_expiry(self, session: dict, form: dict):
        days = _key_expiry_days(form)

        def again(error: str):
            return self.send(200, pages.general_page(session, CTX, key_expiry=hs.key_expiry_days(), error=error,
                                                     mfa=mfa_state()))

        if days is None:
            return again(_("Enter a number of days between 1 and {max}.", max=hs.KEY_EXPIRY_MAX_DAYS))
        ok, error = hs.apply_key_expiry(days)
        if not ok:
            log.warning("%s tried to change the key expiry: %s", session["username"], error)
            return again(error)
        log.info("%s set the device key expiry to %s and restarted Headscale", session["username"],
                 f"{days} days" if days else "never")
        return self.redirect(f"{BASE}/settings/general?m=key-expiry-saved")

    def save_mfa(self, session: dict, form: dict):
        mode = str(form.get("mode", ""))

        def again(error: str):
            return self.send(400, pages.general_page(session, CTX, key_expiry=hs.key_expiry_days(), error=error,
                                                     mfa=mfa_state()))

        if not AUTHENTIK or not mfa.available():
            return again(_("Two-factor authentication can only be changed here with the built-in Authentik, "
                           "once ./install.sh has given the web UI access to it."))
        if mode not in mfa.MODES:
            return again(_("Choose one of the options."))
        try:
            changed = mfa.set_mode(mode)
        except mfa.MfaError as exc:
            log.warning("%s tried to set two-factor to '%s': %s", session["username"] or session["name"], mode, exc)
            return again(str(exc))
        if changed:
            log.info("%s set two-factor authentication to '%s'", session["username"] or session["name"], mode)
        return self.redirect(f"{BASE}/settings/general?m=mfa-saved")

    # --- static files ---
    def static(self, name: str):
        target = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return self.send(404, "Not found", "text/plain")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".woff2":
            ctype = "font/woff2"
        # URLs carry ?v=<content hash>: safe to cache forever
        self.send(200, target.read_bytes(), ctype, [("Cache-Control", "public, max-age=31536000, immutable")])

    # --- sign in ---
    def start_sso(self):
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)
        url = discovery()["authorization_endpoint"] + "?" + urllib.parse.urlencode({
            "response_type": "code",
            "client_id": OIDC_CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": "openid profile email",
            "state": state,
            "nonce": secrets.token_urlsafe(24),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        tx = sign({"state": state, "verifier": verifier, "exp": time.time() + 600})
        self.redirect(url, [self.set_cookie("hse_oidc", tx, 600)])

    def callback(self, params: dict):
        tx = unsign(self.cookie("hse_oidc"))
        if not tx or not params.get("code") or not hmac.compare_digest(params.get("state", ""), tx["state"]):
            return self.fail(400, _("Could not sign in"), _("The sign-in expired or is not valid. Please try again."))

        d = discovery()
        basic = base64.b64encode(f"{OIDC_CLIENT_ID}:{OIDC_CLIENT_SECRET}".encode()).decode()
        token = hs.http_json("POST", d["token_endpoint"], headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }, body=urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": params["code"],
            "redirect_uri": REDIRECT_URI,
            "code_verifier": tx["verifier"],
        }).encode())
        # Identity comes from userinfo, requested directly from the provider
        # with the access token: no need to verify the ID token signature.
        info = hs.http_json("GET", d["userinfo_endpoint"], headers={"Authorization": f"Bearer {token['access_token']}"})
        groups = sorted(info.get("groups") or [])
        email = (info.get("email") or "").lower()
        self.start_session({
            "kind": "oidc",
            "sub": info["sub"],
            "username": info.get("preferred_username", ""),
            "name": info.get("name", ""),
            "email": info.get("email", ""),
            "groups": groups,
            "admin": bool(set(groups) & ADMIN_GROUPS) or bool(email and email in ADMIN_EMAILS),
            "idt": token.get("id_token", ""),
        })

    def apikey_login(self, form: dict):
        key = str(form.get("api_key", "")).strip()
        valid = False
        if key.startswith("hskey-api-") and len(key) < 200:
            try:
                hs.http_json("GET", f"{hs.HEADSCALE_URL}/api/v1/apikey",
                             headers={"Authorization": f"Bearer {key}"}, timeout=5)
                valid = True
            except (urllib.error.URLError, OSError):
                valid = False
        if not valid:
            time.sleep(1)  # slow down guessing
            log.warning("API key sign-in rejected from %s", self.address_string())
            return self.send(401, admin_pages.login_page(SSO, API_KEY_LOGIN, _("Invalid or expired API key.")))
        log.info("sign-in with API key (%s…)", key[:14])
        self.start_session({"kind": "apikey", "sub": "", "username": "", "name": _("Administrator"),
                            "email": "", "groups": [], "admin": True})

    def start_session(self, data: dict):
        data.update(csrf=secrets.token_urlsafe(24), exp=time.time() + SESSION_TTL)
        log.info("sign-in: %s (%s%s)", data["username"] or data["name"], data["kind"], ", admin" if data["admin"] else "")
        self.redirect(f"{BASE}/machines", [
            self.set_cookie("hse_session", sign(data), SESSION_TTL),
            self.set_cookie("hse_oidc", "", 0),
        ])

    def logout(self, session: dict):
        # With OIDC, also end the provider session; otherwise "Log out" would
        # not let another user sign in on the same browser.
        target = f"{BASE}/login?m=signed-out"
        if session.get("kind") == "oidc" and SSO and AUTHENTIK_SIGN_OUT:
            # Built-in Authentik: its own sign-out flow ends the session and
            # comes back here. The OIDC end-session endpoint needs a valid ID
            # token, which expires after an hour, and fails after that.
            target = AUTHENTIK_SIGN_OUT
        elif session.get("kind") == "oidc" and SSO:
            end = discovery().get("end_session_endpoint")
            if end:
                target = end + "?" + urllib.parse.urlencode({
                    "id_token_hint": session.get("idt", ""),
                    "post_logout_redirect_uri": f"{PUBLIC_URL}{BASE}/",
                    "client_id": OIDC_CLIENT_ID,
                })
        self.redirect(target, [self.set_cookie("hse_session", "", 0)])

    # --- machines ---
    def machine_action(self, session: dict, node_id: str, action: str, form: dict):
        # Go back to where the user was (list or detail)
        back = str(form.get("back", "machines"))
        if not re.fullmatch(r"machines(/\d+)?", back) or action == "delete":
            back = "machines"
        dest = f"{BASE}/{back}"

        node = node_for(session, node_id)
        if node is None:
            log.warning("%s tried '%s' on node %s, which they cannot manage", session["username"], action, node_id)
            return self.redirect(f"{BASE}/machines?m=not-found")
        if action in {"expiry", "routes", "tags"} and not session.get("admin"):
            return self.redirect(f"{dest}?m=forbidden")
        try:
            if action == "rename":
                name = str(form.get("name", "")).strip().lower()
                if not NODE_NAME_RE.fullmatch(name):
                    return self.redirect(f"{dest}?m=bad-name")
                hs.api("POST", f"/node/{node_id}/rename/{urllib.parse.quote(name)}")
                return self.redirect(f"{dest}?m=renamed")
            if action == "expire":
                hs.api("POST", f"/node/{node_id}/expire")
                return self.redirect(f"{dest}?m=expired")
            if action == "expiry":
                if form.get("disable") == "1":
                    hs.api("POST", f"/node/{node_id}/expire?disableExpiry=true")
                    return self.redirect(f"{dest}?m=expiry-off")
                # Re-enable: the tailnet's key expiry (Settings → General), or
                # Tailscale's 180 days when devices are set to never expire
                days = hs.key_expiry_days() or 180
                hs.api("POST", f"/node/{node_id}/expire?" + urllib.parse.urlencode({"expiry": iso_in(days)}))
                return self.redirect(f"{dest}?m=expiry-on")
            if action == "routes":
                # Only routes the node advertises can be approved
                available = set(node.get("availableRoutes") or [])
                chosen = form.get("route") or []
                routes = [r for r in chosen if r in available]
                if "exit" in chosen:
                    routes += [r for r in EXIT_ROUTES if r in available]
                hs.api("POST", f"/node/{node_id}/approve_routes", {"routes": sorted(set(routes))})
                log.info("%s approved routes %s on node %s", session["username"], routes, node_id)
                return self.redirect(f"{dest}?m=routes")
            if action == "tags":
                tags = [t.lower() for t in lines(str(form.get("tags", "")))]
                if any(not TAG_RE.fullmatch(t) for t in tags):
                    return self.redirect(f"{dest}?m=failed")
                hs.api("POST", f"/node/{node_id}/tags", {"tags": tags})
                return self.redirect(f"{dest}?m=tags")
            hs.api("DELETE", f"/node/{node_id}")
            log.info("%s removed node %s", session["username"], node_id)
            return self.redirect(f"{BASE}/machines?m=removed")
        except urllib.error.HTTPError as exc:
            msg = hs.api_error(exc)
            log.warning("headscale rejected '%s' on %s: %s", action, node_id, msg)
            if action == "tags":
                # The reason (e.g. a tag without an owner in tagOwners) is useful
                return self.send(400, pages.machine_page(session, CTX, to_machines([node])[0], "",
                                                         error=_("Could not save the tags: {reason}", reason=msg)))
            return self.redirect(f"{dest}?m=failed")

    def register_node(self, session: dict, form: dict):
        # Accept the full URL printed by 'tailscale up'
        auth_id = str(form.get("auth_id", "")).strip().rstrip("/").rsplit("/", 1)[-1]
        user = str(form.get("user", ""))
        if not AUTH_ID_RE.fullmatch(auth_id) or user not in {u["name"] for u in hs.all_users()}:
            return self.redirect(f"{BASE}/machines?m=failed")
        try:
            hs.api("POST", "/auth/register", {"user": user, "authId": auth_id})
        except urllib.error.HTTPError as exc:
            msg = hs.api_error(exc)
            log.warning("Auth ID registration rejected: %s", msg)
            return self.send(400, pages.machines_page(session, CTX, to_machines(visible_nodes(session)), True, "",
                                                      hs.all_users(), error=_("Could not register: {reason}", reason=msg)))
        return self.redirect(f"{BASE}/machines?m=registered")

    # --- keys ---
    def create_key(self, session: dict, form: dict):
        if session.get("admin"):
            uid = str(form.get("user_id", ""))
            user = next((u for u in hs.all_users() if str(u["id"]) == uid), None)
        else:
            user = my_user(session)
        if not user:
            return self.redirect(f"{BASE}/settings/keys?m=no-user")
        days = str(form.get("days", "90"))
        days = days if days in KEY_DAYS else "90"
        key = hs.api("POST", "/preauthkey", {
            "user": str(user["id"]),
            "reusable": form.get("reusable") == "1",
            "ephemeral": form.get("ephemeral") == "1",
            "expiration": iso_in(int(days)),
        })["preAuthKey"]
        log.info("%s generated an auth key for %s (reusable=%s, ephemeral=%s, %s d)", session["username"],
                 user["name"], key.get("reusable"), key.get("ephemeral"), days)
        # Shown in this very response: Headscale never returns it again
        self.keys_view(session, "", new_key=key)

    def revoke_key(self, session: dict, key_id: str):
        if session.get("admin"):
            ok = any(str(k.get("id")) == key_id for k in hs.all_keys())
        else:
            ok = hs.owned_key(my_user(session), key_id) is not None
        if not ok:
            log.warning("%s tried to revoke key %s, which they cannot manage", session["username"], key_id)
            return self.redirect(f"{BASE}/settings/keys?m=not-found")
        hs.api("POST", "/preauthkey/expire", {"id": key_id})
        return self.redirect(f"{BASE}/settings/keys?m=key-revoked")

    def create_apikey(self, session: dict, form: dict):
        days = str(form.get("days", "90"))
        days = days if days in APIKEY_DAYS else "90"
        key = hs.api("POST", "/apikey", {"expiration": iso_in(int(days))})["apiKey"]
        log.info("%s created an API key (%s d)", session["username"], days)
        self.keys_view(session, "", new_apikey=key)

    def expire_apikey(self, key_id: str):
        key = next((k for k in hs.api_keys() if str(k.get("id")) == key_id), None)
        if key is None:
            return self.redirect(f"{BASE}/settings/keys?m=not-found")
        own = hs.own_api_key_prefix()
        if own and own in (key.get("prefix") or ""):
            return self.redirect(f"{BASE}/settings/keys?m=apikey-own")
        hs.api("POST", "/apikey/expire", {"id": key_id})
        return self.redirect(f"{BASE}/settings/keys?m=apikey-expired")

    # --- users ---
    def users_error(self, session: dict, msg: str):
        return self.send(400, admin_pages.users_page(session, CTX, hs.all_users(), hs.all_nodes(), "", error=msg))

    def create_user(self, session: dict, form: dict):
        name = str(form.get("name", "")).strip().lower()
        if not USER_NAME_RE.fullmatch(name):
            return self.redirect(f"{BASE}/users?m=bad-user")
        body = {"name": name}
        if form.get("display_name"):
            body["displayName"] = str(form["display_name"]).strip()[:100]
        try:
            hs.api("POST", "/user", body)
        except urllib.error.HTTPError as exc:
            return self.users_error(session, _("Could not create it: {reason}", reason=hs.api_error(exc)))
        log.info("%s created local user %s", session["username"], name)
        return self.redirect(f"{BASE}/users?m=user-created")

    def user_action(self, session: dict, user_id: str, action: str, form: dict):
        if not any(str(u["id"]) == user_id for u in hs.all_users()):
            return self.redirect(f"{BASE}/users?m=not-found")
        try:
            if action == "rename":
                name = str(form.get("name", "")).strip().lower()
                if not USER_NAME_RE.fullmatch(name):
                    return self.redirect(f"{BASE}/users?m=bad-user")
                hs.api("POST", f"/user/{user_id}/rename/{urllib.parse.quote(name)}")
                return self.redirect(f"{BASE}/users?m=user-renamed")
            if any(str((n.get("user") or {}).get("id")) == user_id for n in hs.all_nodes()):
                return self.redirect(f"{BASE}/users?m=user-has-nodes")
            hs.api("DELETE", f"/user/{user_id}")
            log.info("%s deleted user %s", session["username"], user_id)
            return self.redirect(f"{BASE}/users?m=user-deleted")
        except urllib.error.HTTPError as exc:
            return self.users_error(session, hs.api_error(exc))

    # --- policy ---
    def save_acl(self, session: dict, form: dict):
        policy = str(form.get("policy", ""))
        try:
            if form.get("action") == "save":
                hs.api("PUT", "/policy", {"policy": policy})
                log.info("%s saved the ACL policy", session["username"])
                return self.redirect(f"{BASE}/acl?m=acl-saved")
            hs.api("POST", "/policy/check", {"policy": policy})
            result = ("ok", _("The policy is valid."))
        except urllib.error.HTTPError as exc:
            result = ("error", hs.api_error(exc))
        # Send the draft back as is so nothing typed is lost
        self.send(200, admin_pages.acl_page(session, CTX, hs.get_policy(), "", draft=policy, result=result))

    # --- DNS ---
    def save_dns(self, session: dict, form: dict):
        ctx = dns_ctx()
        current = hs.dns_config()
        cfg: dict | None = None

        def again(error: str):
            # Show what the admin typed again so it is not lost
            return self.send(400, pages.dns_page(session, ctx, cfg or current, to_machines(visible_nodes(session)),
                                                 error=error))

        if not ctx.get("dns_editable"):
            return again(ctx.get("dns_reason", _("Editing DNS is not available.")))

        cfg, error = dns_cfg_from_form(form, current)
        if error:
            return again(error)
        if not hs.valid_domain(cfg["base_domain"]):
            return again(_("The tailnet DNS name is not a valid domain."))
        if cfg["base_domain"] == SERVER_HOST or SERVER_HOST.endswith("." + cfg["base_domain"]):
            return again(_("The tailnet DNS name must differ from the server's domain."))
        bad = [ns for ns in cfg["nameservers"] + [s for v in cfg["split"].values() for s in v]
               if not hs.valid_nameserver(ns)]
        if bad:
            return again(_("Invalid nameserver: {value}", value=bad[0]))
        bad = [d for d in cfg["search_domains"] + list(cfg["split"]) if not hs.valid_domain(d)]
        if bad:
            return again(_("Invalid domain: {value}", value=bad[0]))
        if cfg["override_local_dns"] and not cfg["nameservers"]:
            return again(_("Turning off \"Use local DNS settings\" needs at least one global nameserver."))

        ok, error = hs.apply_dns(cfg)
        if not ok:
            log.warning("%s tried to change DNS: %s", session["username"], error)
            return again(error)
        log.info("%s changed DNS (%s) and restarted Headscale", session["username"], form.get("section") or "settings")
        return self.redirect(f"{BASE}/dns?m=dns-saved")


def dns_cfg_from_form(form: dict, current: dict) -> tuple[dict, str]:
    """New DNS settings from a DNS page form, over the current ones, and the
    first input error ("" if none). 'section' says which form: 'rename' (tailnet
    name), 'magic' (MagicDNS on/off) or the settings (nameservers, search
    domains, custom records, local DNS). Each list arrives as repeated "...[]"
    fields, one per row; blank rows are ignored. The result keeps what was typed
    so an error can show it again."""
    cfg = {"magic_dns": bool(current.get("magic_dns")), "base_domain": current.get("base_domain") or "",
           "override_local_dns": bool(current.get("override_local_dns")),
           "nameservers": list(current.get("nameservers") or []),
           "search_domains": list(current.get("search_domains") or []),
           "split": {d: list(v) for d, v in (current.get("split") or {}).items()},
           "extra_records": [dict(r) for r in current.get("extra_records") or []]}

    def rows(name: str) -> list[str]:
        value = form.get(name + "[]") or []
        return [str(v).strip() for v in (value if isinstance(value, list) else [value])]

    section = form.get("section")
    if section == "rename":
        cfg["base_domain"] = str(form.get("base_domain", "")).strip().lower().rstrip(".")
        return cfg, ""
    if section == "magic":
        cfg["magic_dns"] = form.get("magic_dns") == "1"
        return cfg, ""

    cfg["override_local_dns"] = form.get("use_local_dns") != "1"
    cfg["nameservers"] = [s for s in rows("ns") if s]
    cfg["search_domains"] = [d.lower().rstrip(".") for d in rows("search") if d]
    cfg["split"], cfg["extra_records"] = {}, []
    error = ""
    domains, servers = rows("split_domain"), rows("split_ns")
    for domain, server in zip(domains + [""] * (len(servers) - len(domains)),
                              servers + [""] * (len(domains) - len(servers))):
        domain = domain.lower().rstrip(".")
        if not domain and not server:
            continue
        if not server and not error:
            error = _("Split DNS: {domain} needs a nameserver.", domain=domain)
        if not domain and not error:
            error = _("Split DNS: nameserver {value} needs a domain.", value=server)
        servers_of = cfg["split"].setdefault(domain, [])
        if server and server not in servers_of:
            servers_of.append(server)

    names, values = rows("rec_name"), rows("rec_value")
    for name, value in zip(names + [""] * (len(values) - len(names)), values + [""] * (len(names) - len(values))):
        name = name.lower().rstrip(".")
        if not name and not value:
            continue
        record = {"name": name, "type": "", "value": value}
        cfg["extra_records"].append(record)
        if error:
            continue
        if not name or not value:
            error = _("Custom records: each record needs a name and an address.")
        elif not hs.valid_domain(name):
            error = _("Invalid domain: {value}", value=name)
        else:
            try:
                record["type"] = "AAAA" if ipaddress.ip_address(value).version == 6 else "A"
            except ValueError:
                error = _("Custom records: {value} is not an IP address.", value=value)
    return cfg, error


def _key_expiry_days(form: dict) -> int | None:
    """Days from the form: 0 = never; None = invalid."""
    if form.get("never") == "1":
        return 0
    raw = str(form.get("days", "")).strip()
    if not raw.isdigit() or not 1 <= int(raw) <= hs.KEY_EXPIRY_MAX_DAYS:
        return None
    return int(raw)


def main():
    port = int(os.environ.get("PORT", "8000"))
    log.info("Headscale Easy %s listening on :%d (public: %s%s, SSO=%s, API key sign-in=%s)",
             VERSION, port, PUBLIC_URL, BASE, SSO, API_KEY_LOGIN)
    apikey.start()
    naming.start()
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
