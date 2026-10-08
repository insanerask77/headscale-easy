"""Headscale Easy — the web UI for Headscale.

  - Members see and manage only THEIR machines and keys.
  - Admins manage everything: machines, users, routes, tags, access control
    policy, DNS and API keys.

Sign-in:
  - OIDC (any provider: Authentik, Keycloak, Pocket ID, Google...), authorization code + PKCE. The role
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
import shutil
import signal
import time
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("headscale-easy")

import admin_pages  # noqa: E402
from handlers import shared as sh  # noqa: E402
from handlers.access import AccessHandlers  # noqa: E402
from handlers.devices import DevicesHandlers  # noqa: E402
from handlers.operations import OperationsHandlers  # noqa: E402
from handlers.policy import PolicyHandlers  # noqa: E402
from handlers.users import UsersHandlers  # noqa: E402
# Names the tests (and other callers) reach as app.X. Values that tests patch live in `sh` only.
from handlers.shared import (  # noqa: E402,F401
    CTX, PUBLIC_URL, bulk_tags_from_form, can_edit_network, exit_nodes_for, is_auditor, iso_in, lines, my_user,
    node_for, role_of, sign, to_machines, unsign, visible_nodes,
)
from http_base import HttpHelpers  # noqa: E402
from config import Settings, csv_set, oidc_scope  # noqa: E402,F401
import docker_tab  # noqa: E402
import local_accounts as lac  # noqa: E402
import derp  # noqa: E402
import derp_pages  # noqa: E402
import status as server_status  # noqa: E402
import status_pages  # noqa: E402
import backup_pages  # noqa: E402
import multipart  # noqa: E402

BACKUP_UPLOAD_MAX = backup_pages.UPLOAD_MAX_MB * 1024 * 1024  # bytes of a backup file the console accepts
import headscale as hs  # noqa: E402
import live  # noqa: E402
import mailer  # noqa: E402
import apikey  # noqa: E402
import expiry  # noqa: E402
import audit  # noqa: E402
import naming  # noqa: E402
import notify  # noqa: E402
import pages  # noqa: E402
import policy  # noqa: E402
import sessions  # noqa: E402
import signup  # noqa: E402
from i18n import LANGUAGES, _, pick_lang, set_lang  # noqa: E402
from ui import BASE, message_page  # noqa: E402
from version import VERSION  # noqa: E402

# -----------------------------------------------------------------------------
# HTTP
# -----------------------------------------------------------------------------

_NOT_PUBLIC = object()  # what _get_public/_post_public return for a path that needs a session


class Handler(AccessHandlers, DevicesHandlers, UsersHandlers, PolicyHandlers, OperationsHandlers,
              HttpHelpers, BaseHTTPRequestHandler):
    server_version = "headscale-easy"
    sys_version = ""

    # --- responses ---
    def fail(self, status: int, title: str, text: str):
        self.send(status, message_page(title, text))

    @staticmethod
    def set_cookie(name: str, value: str, max_age: int) -> tuple[str, str]:
        attrs = [f"{name}={value}", f"Path={BASE}", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
        if sh.SECURE_COOKIES:
            attrs.append("Secure")
        return ("Set-Cookie", "; ".join(attrs))

    def session(self) -> dict | None:
        """The signed-in session, or None. The cookie must name a live session
        in the server-side table (sessions.py): revoked ones, and cookies from
        before sessions were revocable (no sid), are rejected."""
        data = sh.unsign(self.cookie("hse_session"))
        if not (data and sessions.validate(data)):
            return None
        if data.get("kind") == "local":  # live, so the 2FA suggestion goes away once it is enabled
            try:
                account = lac.get_account(id=int(str(data.get("sub", "")).split(":")[-1]))
            except (ValueError, TypeError):
                account = None
            data["totp_on"] = bool(account and account.get("totp_confirmed"))
            data["must_change"] = bool(account and account.get("must_change"))
        return data

    def rate_limited(self, bucket: str) -> int:
        """Seconds to wait when this client IP has too many sign-in attempts, else 0
        (and the event is logged)."""
        ip = audit.client_ip(self)
        wait = sessions.blocked(f"{bucket}:{ip}")
        if wait:
            sh.log.warning("sign-in rate limit reached for %s (%s)", ip, bucket)
            audit.request_event(self, None, "auth.rate_limited", "", {"method": bucket, "retry_after": wait}, actor="")
        return wait

    def too_many(self, wait: int, page: str | None = None):
        body = page or message_page(_("Too many attempts"), _("Too many sign-in attempts. Try again in a few minutes."))
        self.send(429, body, headers=[("Retry-After", str(wait))])

    def log_message(self, fmt, *args):
        sh.log.info("%s %s", self.address_string(), fmt % args)

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
            handled = self._get_public(path, params)
            if handled is not _NOT_PUBLIC:
                return handled
            if path == f"{BASE}/events":
                return self.events_stream()
            return self._get_signed_in(path, params, flash)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                # The web UI's API key expired (the server was off during the
                # renewal window, or someone expired it by hand)
                sh.log.error("Headscale rejected the web UI's API key on GET %s", path)
                return self.fail(503, _("The web UI cannot reach Headscale"),
                                 _("Its API key has expired or was revoked. Delete /data/console/api-key and restart the container: it creates a new one."))
            sh.log.exception("error on GET %s", path)
            self.fail(500, _("Something went wrong"), _("The operation could not be completed. Try again in a few seconds."))
        except Exception:  # noqa: BLE001 - never show tracebacks in the UI
            sh.log.exception("error on GET %s", path)
            self.fail(500, _("Something went wrong"), _("The operation could not be completed. Try again in a few seconds."))


    def _get_public(self, path: str, params: dict):
        """Routes that need no session. Returns _NOT_PUBLIC when the path is not one of them."""
        if path == f"{BASE}/healthz":
            return self.send(200, "ok", "text/plain")
        if path == f"{BASE}/restore-status":
            return self.restore_status(params.get("id", ""))
        if path.startswith(f"{BASE}/static/"):
            return self.static(path[len(f"{BASE}/static/"):])
        if path == f"{BASE}/login":
            # After signing out, say so instead of starting a new sign-in
            if params.get("m") == "signed-out":
                return self.send(200, admin_pages.login_page(sh.SSO, sh.API_KEY_LOGIN, info=_("You have signed out.")))
            if params.get("m") == "signed-up":
                return self.send(200, admin_pages.login_page(sh.SSO, sh.API_KEY_LOGIN, info=_("Account created. Sign in.")))
            if sh.SSO and not sh.API_KEY_LOGIN:
                return self.start_sso()
            return self.send(200, admin_pages.login_page(sh.SSO, sh.API_KEY_LOGIN))
        if path == f"{BASE}/signup":
            return self.signup_form()
        if path == f"{BASE}/login/sso" and sh.SSO:
            return self.start_sso()
        if path == f"{BASE}/login/totp":
            return self.totp_verify_page()
        if path == f"{BASE}/callback" and sh.SSO:
            return self.callback(params)
        # Invitation and password reset (public routes)
        accept_match = re.fullmatch(rf"{BASE}/accept/([A-Za-z0-9_-]+)", path)
        if accept_match:
            return self.accept_invitation_page(accept_match.group(1))
        reset_match = re.fullmatch(rf"{BASE}/reset/([A-Za-z0-9_-]+)", path)
        if reset_match:
            return self.reset_password_page(reset_match.group(1))
        return _NOT_PUBLIC

    def _get_signed_in(self, path: str, params: dict, flash: str):
        """Everything behind the sign-in: redirects to /login without a session."""
        session = self.session()
        register = sh.REGISTER_PATH_RE.fullmatch(path)
        if not session:
            # Come back to this device's approval page after signing in
            after = [self.set_cookie("hse_next", sh.sign({"path": path, "exp": time.time() + 600}), 600)] if register else None
            return self.redirect(f"{BASE}/login", after)
        admin = session.get("admin")
        if session.get("must_change") and path not in (f"{BASE}/settings/account", f"{BASE}/logout"):
            return self.redirect(f"{BASE}/settings/account?m=must-change")
        if register:
            return self.register_view(session, register.group(1))

        if path in (BASE, f"{BASE}/"):
            return self.redirect(f"{BASE}/machines")
        sees_all = admin or sh.is_auditor(session)
        if path == f"{BASE}/machines":
            users = hs.all_users() if sees_all else None
            has_user = True if sees_all else sh.my_user(session) is not None
            return self.send(200, pages.machines_page(session, sh.CTX, sh.to_machines(sh.visible_nodes(session)),
                                                      has_user, flash, users))
        if path == f"{BASE}/machines.csv":
            data = pages.machines_csv(sh.to_machines(sh.visible_nodes(session)))
            return self.send(200, data, "text/csv; charset=utf-8",
                             [("Content-Disposition", 'attachment; filename="machines.csv"')])
        m = re.fullmatch(rf"{BASE}/machines/(\d+)", path)
        if m:
            node = sh.node_for(session, m.group(1))
            if node is None:
                return self.redirect(f"{BASE}/machines?m=not-found")
            return self.send(200, pages.machine_page(session, sh.CTX, sh.to_machines([node])[0], flash))
        if path == f"{BASE}/add":
            return self.send(200, pages.add_page(session, sh.CTX, users=hs.all_users() if session.get("admin") else None,
                                                      exit_nodes=sh.exit_nodes_for(session)))
        if path == f"{BASE}/dns":
            return self.send(200, pages.dns_page(session, sh.dns_ctx() if sh.can_edit_network(session) else sh.CTX,
                                                 hs.dns_config(), sh.to_machines(sh.visible_nodes(session)), flash=flash))
        if path in (f"{BASE}/settings", f"{BASE}/settings/"):
            return self.redirect(f"{BASE}/settings/general")
        if path == f"{BASE}/settings/general":
            return self.send(200, pages.general_page(session, sh.CTX, flash,
                                                     key_expiry=hs.key_expiry_days() if admin else None,
                                                     extra=signup.mode_card(session, signup.mode())
                                                     if admin else ""))
        if path == f"{BASE}/settings/keys":
            return self.keys_view(session, flash, preselect=params.get("user", ""))
        if path == f"{BASE}/settings/sessions":
            return self.send(200, pages.sessions_page(
                session, sh.CTX, sessions.list_all() if sees_all else sessions.list_for(session), flash))
        if path == f"{BASE}/settings/account":
            # Account settings for local accounts
            if session.get("kind") != "local":
                return self.redirect(f"{BASE}/settings/general")
            account_id = int(session.get("sub", "").split(":")[-1])
            account = lac.get_account(id=account_id)
            if not account:
                return self.redirect(f"{BASE}/settings/general")
            return self.send(200, pages.account_settings_page(session, sh.CTX, account, flash))
        if path == f"{BASE}/settings/account/totp/enroll":
            # Start TOTP enrollment
            if session.get("kind") != "local":
                return self.redirect(f"{BASE}/settings/general")
            account_id = int(session.get("sub", "").split(":")[-1])
            account = lac.get_account(id=account_id)
            if not account:
                return self.redirect(f"{BASE}/settings/general")
            if account.get('totp_confirmed'):
                return self.redirect(f"{BASE}/settings/account?m=totp-already-enabled")
            # Generate secret and QR code
            secret, qr_data = lac.enroll_totp(account_id)
            return self.send(200, pages.totp_enroll_page(session, sh.CTX, secret, qr_data))

        # --- admins only (Access controls: also network admins and auditors;
        # Users/Logs: also auditors, view only -- see can_edit_network()/is_auditor()) ---
        if path == f"{BASE}/acl" and not (sh.can_edit_network(session) or sh.is_auditor(session)):
            return self.fail(403, _("No permission"), _("This section is for admins only."))
        if path in (f"{BASE}/users", f"{BASE}/logs", f"{BASE}/logs.csv") and not sees_all:
            return self.fail(403, _("No permission"), _("This section is for admins only."))
        if path == f"{BASE}/users":
            return self.users_view(session, flash=flash)
        if path == f"{BASE}/acl":
            return self.send(200, admin_pages.acl_page(session, sh.CTX, hs.get_policy(), hs.all_nodes(), hs.all_users(),
                                                       flash, active=params.get("tab") or "rules"))
        if path == f"{BASE}/derp":
            if not sees_all:
                return self.fail(403, _("No permission"), _("This section is for admins only."))
            return self.send(200, self.derp_view(session, flash))
        if path == f"{BASE}/settings/status":
            if not sees_all:
                return self.fail(403, _("No permission"), _("This section is for admins only."))
            return self.send(200, status_pages.status_page(session, sh.CTX, server_status.collect(), flash))
        if path == f"{BASE}/backups":
            if not session.get("admin"):
                return self.fail(403, _("No permission"), _("This section is for admins only."))
            if flash == "backup-restore-done":  # the page that waited for the restore sends us here
                flash = "backup-restore-ok" if (hs.restore_result() or {}).get("ok") else "backup-restore-failed"
            return self.send(200, backup_pages.backups_page(session, sh.CTX, (hs.control_status() or {}).get("backup"), flash))
        if path == f"{BASE}/logs":
            return self.send(200, audit.page(session, sh.CTX, params))
        if path == f"{BASE}/logs.csv":
            return self.send(200, audit.csv_export(params), "text/csv; charset=utf-8",
                             [("Content-Disposition", 'attachment; filename="activity-log.csv"')])
        self.fail(404, _("Not found"), _("That page does not exist."))

    def events_stream(self):
        """Server-Sent Events: one message per device change this session may see.
        The page re-fetches its own (already filtered) HTML when one arrives."""
        session = self.session()
        if not session:
            return self.send(401, "Unauthorized", "text/plain")
        if session.get("must_change"):
            return self.send(403, "Forbidden", "text/plain")
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host", ""):
            return self.send(403, "Forbidden", "text/plain")  # a page on another site
        everyone = session.get("admin") or sh.is_auditor(session)

        def own_user() -> str:
            user = None if everyone else sh.my_user(session)
            return str(user["id"]) if user else ""

        try:
            sub = sh.HUB.subscribe(session.get("sid", ""), None if everyone else own_user())
        except live.TooMany:
            return self.send(429, "Too many open streams", "text/plain", [("Retry-After", "30")])
        self.close_connection = True
        try:
            self.send_response(200)
            for k, v in (("Content-Type", "text/event-stream"), ("Cache-Control", "no-store"),
                         ("X-Accel-Buffering", "no"), ("X-Content-Type-Options", "nosniff"),
                         ("Referrer-Policy", "same-origin")):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b"retry: 3000\n: connected\n\n")
            self.wfile.flush()
            while not sub.closed.is_set():
                event = sub.get(live.HEARTBEAT)
                if sub.closed.is_set():
                    break
                if event is None:
                    if not sessions.validate(session):  # signed out or revoked meanwhile
                        break
                    if not everyone and not sub.user:  # a member who had no devices yet
                        sub.user = own_user()
                        if sub.user:
                            self.wfile.write(live.format_event({"type": "added"}))
                    self.wfile.write(b": ping\n\n")
                else:
                    self.wfile.write(live.format_event(event))
                self.wfile.flush()
        except OSError:  # the browser went away
            pass
        finally:
            sh.HUB.unsubscribe(sub)

    def keys_view(self, session: dict, flash: str, new_key: dict | None = None, new_apikey: str = "",
                  preselect: str = ""):
        if session.get("admin"):
            page = pages.keys_page(session, sh.CTX, hs.all_keys(), flash, new_key, users=hs.all_users(),
                                   apikeys=hs.api_keys(), own_prefix=hs.own_api_key_prefix(),
                                   new_apikey=new_apikey, preselect=preselect)
        else:
            user = sh.my_user(session)
            page = pages.keys_page(session, sh.CTX, hs.user_keys(user) if user else None, flash, new_key)
        self.send(200, page)

    # --- POST ---
    def do_POST(self):
        self.set_request_lang()
        path = self.path.partition("?")[0]
        try:
            handled = self._post_public(path)
            if handled is not _NOT_PUBLIC:
                return handled
            session = self.session()
            if not session:
                return self.redirect(f"{BASE}/login")
            if path == f"{BASE}/backups/upload":  # multipart: streamed to disk, its CSRF field is checked on the way
                return self.backup_upload(session)
            form = self.form()
            # CSRF: the form token must match the session's
            if not hmac.compare_digest(str(form.get("csrf", "")), session["csrf"]):
                return self.fail(403, _("Session expired"), _("Reload the page and try again."))
            if session.get("must_change") and path not in (
                    f"{BASE}/logout", f"{BASE}/settings/account/password", f"{BASE}/settings/language"):
                return self.redirect(f"{BASE}/settings/account?m=must-change")
            if sh.DEMO and (sh.DEMO_BLOCKED.fullmatch(path) or (path == f"{BASE}/acl" and form.get("action") == "save")):
                return self.fail(403, _("Not available in the demo"),
                                 _("This action is disabled in the demo environment."))

            if path == f"{BASE}/logout":
                return self.logout(session)
            if path == f"{BASE}/settings/sessions/revoke":
                return self.revoke_session(session, form)
            if path == f"{BASE}/settings/sessions/revoke-all":
                return self.revoke_all_sessions(session, form)
            if path == f"{BASE}/settings/account/password":
                return self.change_password(session, form)
            if path == f"{BASE}/settings/account/totp/confirm":
                return self.confirm_totp_enrollment(session, form)
            if path == f"{BASE}/settings/account/totp/disable":
                return self.disable_totp_account(session, form)
            if path == f"{BASE}/settings/account/totp/recovery/reset":
                return self.reset_totp_recovery(session, form)
            if path == f"{BASE}/settings/language":
                lang = str(form.get("lang", ""))
                back = self.headers.get("Referer", "")
                dest = urllib.parse.urlparse(back).path if back.startswith(sh.PUBLIC_URL) else f"{BASE}/settings/general"
                if not dest.startswith(BASE):
                    dest = f"{BASE}/settings/general"
                return self.redirect(dest, [self.set_cookie("hse_lang", lang if lang in LANGUAGES else "", 31536000)])
            if path == f"{BASE}/keys":
                return self.create_key(session, form)
            if path == f"{BASE}/add/docker":
                return self.add_docker(session, form)
            m = re.fullmatch(rf"{BASE}/keys/(\d+)/revoke", path)
            if m:
                return self.revoke_key(session, m.group(1))
            m = re.fullmatch(rf"{BASE}/machines/(\d+)/(rename|delete|expire|expiry|routes|approve-routes|tags)", path)
            if m:
                return self.machine_action(session, m.group(1), m.group(2), form)
            m = sh.REGISTER_PATH_RE.fullmatch(path)
            if m:
                return self.register_device(session, m.group(1), form)

            # --- ACL and DNS: admins and network admins (auditors: test/simulate only) ---
            if path == f"{BASE}/acl/test":
                if not (sh.can_edit_network(session) or sh.is_auditor(session)):
                    return self.fail(403, _("No permission"), _("This action is for admins only."))
                return self.acl_test(session, form)
            if path == f"{BASE}/acl" or path.startswith(f"{BASE}/acl/") or path == f"{BASE}/dns":
                if not sh.can_edit_network(session):
                    return self.fail(403, _("No permission"), _("This action is for admins only."))
                if path == f"{BASE}/dns":
                    return self.save_dns(session, form)
                if path == f"{BASE}/acl":
                    return self.save_acl(session, form)
                acl_kinds = {f"{BASE}/acl/rules": "rule", f"{BASE}/acl/groups": "group", f"{BASE}/acl/tags": "tag",
                            f"{BASE}/acl/autoapprove/routes": "auto_route", f"{BASE}/acl/autoapprove/exit-node": "auto_exit",
                            f"{BASE}/acl/ssh": "ssh_rule"}
                if path in acl_kinds:
                    return self.acl_edit(session, form, acl_kinds[path])
                return self.fail(404, _("Not found"), "")

            # --- admins only ---
            if not session.get("admin"):
                return self.fail(403, _("No permission"), _("This action is for admins only."))
            for pattern, handler in _ADMIN_POST_ROUTES:
                m = pattern.fullmatch(path)
                if m:
                    return handler(self, session, form, m)
            self.send(404, "Not found", "text/plain")
        except Exception:  # noqa: BLE001
            sh.log.exception("error on POST %s", path)
            self.redirect(f"{BASE}/machines?m=failed")

    def _post_public(self, path: str):
        """Routes that need no session. Returns _NOT_PUBLIC when the path is not one of them."""
        if path == f"{BASE}/login/apikey" and sh.API_KEY_LOGIN:
            return self.apikey_login(self.form())
        if path == f"{BASE}/login/local":
            return self.local_login(self.form())
        if path == f"{BASE}/signup":
            return self.signup_submit(self.form())
        if path == f"{BASE}/login/totp":
            return self.verify_totp_login(self.form())
        # Invitation and password reset (public routes)
        accept_match = re.fullmatch(rf"{BASE}/accept/([A-Za-z0-9_-]+)", path)
        if accept_match:
            return self.accept_invitation(accept_match.group(1), self.form())
        reset_match = re.fullmatch(rf"{BASE}/reset/([A-Za-z0-9_-]+)", path)
        if reset_match:
            return self.reset_password(reset_match.group(1), self.form())
        return _NOT_PUBLIC

    def backup_now(self, session: dict):
        """Status page > Back up now (admins only): asks the all-in-one supervisor for a manual backup."""
        if not session.get("admin"):
            return self.fail(403, _("No permission"), _("This action is for admins only."))
        result = hs.control_backup()
        if result in ("started", "busy"):
            audit.request_event(self, session, "backup.run", _("Backup"), {"result": result})
        return self.redirect(f"{BASE}/backups?m=backup-{result}")

    def backup_settings(self, session: dict, form: dict):
        """Status page > Backup settings (admins only): scheduled backups on/off, schedule, days to keep."""
        if not session.get("admin"):
            return self.fail(403, _("No permission"), _("This action is for admins only."))
        enabled = form.get("backup_enabled", "on") != "off"
        schedule = (form.get("backup_schedule") or "").strip()
        keep = (form.get("backup_keep_days") or "").strip()
        result, detail = hs.control_backup_settings(enabled, schedule, keep)
        if result == "saved":
            sh.log.info("%s set scheduled backups to %s", session["username"], f"{schedule!r}, {keep} days" if enabled else "off")
            audit.request_event(self, session, "backup.settings", _("Backup settings"),
                                {"enabled": enabled, "schedule": schedule if enabled else None, "keep_days": keep})
        elif result == "invalid":
            sh.log.info("%s sent invalid backup settings: %s", session["username"], detail)
        return self.redirect(f"{BASE}/backups?m=backup-settings-{result}")

    def backup_upload(self, session: dict):
        """Backups > Upload a backup (admins only): the file is streamed to a work file in the backups directory
        (never held in memory), the supervisor checks it as it would a restore and keeps it under a safe name;
        with ``action=restore`` and the typed confirmation it is restored right away."""
        self.close_connection = True  # a big body that is not read to the end must not poison the connection
        if session.get("must_change"):
            return self.redirect(f"{BASE}/settings/account?m=must-change")
        if not session.get("admin"):
            return self.fail(403, _("No permission"), _("This action is for admins only."))
        directory = os.environ.get("BACKUP_DIR", "")
        if not directory or not os.path.isdir(directory):
            return self.fail(404, _("Not found"), _("Backups cannot be uploaded here."))
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return self.redirect(f"{BASE}/backups?m=backup-upload-none")
        if length > sh.BACKUP_UPLOAD_MAX + (1 << 20):
            return self.redirect(f"{BASE}/backups?m=backup-upload-toolarge")
        for stale in os.listdir(directory):  # work files of uploads that died half way
            path = os.path.join(directory, stale)
            if re.fullmatch(r"\.upload-[0-9a-f]{24}\.part", stale) and time.time() - os.path.getmtime(path) > 3600:
                os.unlink(path)
        tmp_name = ".upload-" + secrets.token_hex(12) + ".part"
        tmp_path = os.path.join(directory, tmp_name)
        sink = []

        def on_file(name, filename, fields):
            if name != "file":
                raise multipart.MultipartError("unexpected file")
            if not hmac.compare_digest(str(fields.get("csrf", "")), session["csrf"]):
                raise multipart.MultipartError("session", 403)  # nothing is written for a forged request
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            sink.append(os.fdopen(fd, "wb"))
            return sink[0]

        def discard():
            for fh in sink:
                fh.close()
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        try:
            fields, files = multipart.read_form(self.rfile, self.headers.get("Content-Type", ""), length, on_file,
                                                max_file=sh.BACKUP_UPLOAD_MAX)
        except multipart.MultipartError as exc:
            discard()
            if exc.status == 403:
                return self.fail(403, _("Session expired"), _("Reload the page and try again."))
            sh.log.info("%s sent a bad backup upload: %s", session["username"], exc)
            return self.redirect(f"{BASE}/backups?m=backup-upload-" + ("toolarge" if exc.status == 413 else "error"))
        except OSError as exc:  # no space left, or the backups directory is gone
            discard()
            sh.log.error("could not store an uploaded backup: %s", exc)
            return self.redirect(f"{BASE}/backups?m=backup-upload-error")
        for fh in sink:
            fh.close()
        if not hmac.compare_digest(str(fields.get("csrf", "")), session["csrf"]):
            discard()
            return self.fail(403, _("Session expired"), _("Reload the page and try again."))
        if "file" not in files or files["file"][1] == 0:
            discard()
            return self.redirect(f"{BASE}/backups?m=backup-upload-none")
        restore = fields.get("action") == "restore"
        if restore and fields.get("confirm") != "RESTORE":
            discard()
            return self.redirect(f"{BASE}/backups?m=backup-restore-confirm")
        original = re.sub(r"[^\x20-\x7e]", "?", os.path.basename(files["file"][0].replace("\\", "/")))[:120]
        result, detail = hs.control_backup_upload(tmp_name, original)
        if result != "saved":
            discard()  # (the supervisor already deleted a file it refused)
            sh.log.info("%s uploaded %r and it was refused: %s %s", session["username"], original, result, detail)
            return self.redirect(f"{BASE}/backups?m=backup-upload-{result}")
        audit.request_event(self, session, "backup.upload", detail,
                            {"file": detail, "original": original, "size": files["file"][1]})
        sh.log.warning("%s uploaded the backup %s (%s bytes)", session["username"], detail, files["file"][1])
        if not restore:
            return self.redirect(f"{BASE}/backups?m=backup-uploaded")
        started, rid = hs.control_restore(detail)
        if started != "started":  # the file stays in the list: it can be restored from there
            return self.redirect(f"{BASE}/backups?m=backup-restore-{started}")
        audit.request_event(self, session, "backup.restore", detail, {"file": detail})
        return self.send(200, backup_pages.restoring_page(rid))

    def backup_restore(self, session: dict, form: dict):
        """Status page > Restore (admins only, typed confirmation): the supervisor restores one backup and restarts us."""
        if not session.get("admin"):
            return self.fail(403, _("No permission"), _("This action is for admins only."))
        if form.get("confirm") != "RESTORE":
            return self.redirect(f"{BASE}/backups?m=backup-restore-confirm")
        name = form.get("name", "")
        result, detail = hs.control_restore(name)
        if result != "started":
            sh.log.info("%s could not restore %r: %s %s", session["username"], name, result, detail)
            return self.redirect(f"{BASE}/backups?m=backup-restore-{result}")
        sh.log.warning("%s restores the backup %s", session["username"], name)
        audit.request_event(self, session, "backup.restore", name, {"file": name})
        return self.send(200, backup_pages.restoring_page(detail))

    def backup_download(self, session: dict, form: dict):
        """Status page > Download (admins only, POST with the CSRF token): the archive as an attachment."""
        if not session.get("admin"):
            return self.fail(403, _("No permission"), _("This action is for admins only."))
        name = form.get("name", "")
        opened = server_status.open_backup(name)
        if opened is None:
            return self.fail(404, _("Not found"), _("That backup does not exist."))
        fh, size = opened
        with fh:
            audit.request_event(self, session, "backup.download", name, {"file": name, "size": size})
            sh.log.warning("%s downloads the backup %s", session["username"], name)
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.end_headers()
            shutil.copyfileobj(fh, self.wfile, 1 << 20)

    def restore_status(self, restore_id: str):
        """GET /console/restore-status?id=...: has the restore with that id finished? No session needed (the restore
        restarts the console and may change the session secret); it only says done / ok for an id only the requester has."""
        done = ok = False
        result = hs.restore_result()
        try:
            if result is not None and float(restore_id) == float(result.get("requested")):
                done, ok = True, bool(result.get("ok"))
        except (TypeError, ValueError):
            pass
        return self.send(200, json.dumps({"done": done, "ok": ok}), "application/json",
                         [("Cache-Control", "no-store")])

    def notify_test(self, session: dict):
        if not session.get("admin"):
            return self.fail(403, _("No permission"), _("This section is for admins only."))
        results = notify.send_test()
        audit.request_event(self, session, "settings.notify_test", _("Webhook notifications"),
                            {"destinations": [label for label, _ok in results],
                             "failed": [label for label, ok in results if not ok]})
        if not results:
            code = "notify-none"
        else:
            code = "notify-test-ok" if all(ok for _label, ok in results) else "notify-test-failed"
        return self.redirect(f"{BASE}/settings/general?m={code}")

    # --- static files ---
    def static(self, name: str):
        target = (sh.STATIC_DIR / name).resolve()
        if sh.STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return self.send(404, "Not found", "text/plain")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".woff2":
            ctype = "font/woff2"
        # URLs carry ?v=<content hash>: safe to cache forever
        self.send(200, target.read_bytes(), ctype, [("Cache-Control", "public, max-age=31536000, immutable")])

    # --- sign in ---
    def start_sso(self):
        wait = self.rate_limited("sso")
        if wait:
            return self.too_many(wait)
        sessions.hit(f"sso:{audit.client_ip(self)}")
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)
        url = sh.discovery()["authorization_endpoint"] + "?" + urllib.parse.urlencode({
            "response_type": "code",
            "client_id": sh.OIDC_CLIENT_ID,
            "redirect_uri": sh.REDIRECT_URI,
            "scope": sh.OIDC_SCOPE,
            "state": state,
            "nonce": secrets.token_urlsafe(24),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        tx = sh.sign({"state": state, "verifier": verifier, "exp": time.time() + 600})
        self.redirect(url, [self.set_cookie("hse_oidc", tx, 600)])

    def callback(self, params: dict):
        tx = sh.unsign(self.cookie("hse_oidc"))
        wait = self.rate_limited("sso")
        if wait:
            return self.too_many(wait)
        if not tx or not params.get("code") or not hmac.compare_digest(params.get("state", ""), tx["state"]):
            sessions.hit(f"sso:{audit.client_ip(self)}")
            audit.request_event(self, None, "auth.signin_failed", "", {"method": "oidc", "reason": params.get("error", "invalid state")[:100]})
            return self.fail(400, _("Could not sign in"), _("The sign-in expired or is not valid. Please try again."))

        d = sh.discovery()
        basic = base64.b64encode(f"{sh.OIDC_CLIENT_ID}:{sh.OIDC_CLIENT_SECRET}".encode()).decode()
        token = hs.http_json("POST", d["token_endpoint"], headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }, body=urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": params["code"],
            "redirect_uri": sh.REDIRECT_URI,
            "code_verifier": tx["verifier"],
        }).encode())
        # Identity comes from userinfo, requested directly from the provider
        # with the access token: no need to verify the ID token signature.
        info = hs.http_json("GET", d["userinfo_endpoint"], headers={"Authorization": f"Bearer {token['access_token']}"})
        groups = sorted(info.get("groups") or [])
        email = (info.get("email") or "").lower()
        # PORTAL_ADMIN_EMAILS (your own OIDC provider): never trust an email
        # the provider says it has not verified, or anyone who can set that
        # address on their account would become an admin
        verified = info.get("email_verified") is not False
        if not verified and email in sh.ADMIN_EMAILS:
            sh.log.warning("%s is in PORTAL_ADMIN_EMAILS but the provider says the email is not verified: "
                        "not made an admin by email", email)
        role = sh.role_of(groups, email, verified)
        self.start_session({
            "kind": "oidc",
            "sub": info["sub"],
            "username": info.get("preferred_username", ""),
            "name": info.get("name", ""),
            "email": info.get("email", ""),
            "groups": groups,
            "admin": role == "admin",
            "role": role,
            "idt": token.get("id_token", ""),
        })

    def apikey_login(self, form: dict):
        wait = self.rate_limited("apikey")
        if wait:
            return self.too_many(wait, admin_pages.login_page(
                sh.SSO, sh.API_KEY_LOGIN, _("Too many sign-in attempts. Try again in a few minutes.")))
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
            sessions.hit(f"apikey:{audit.client_ip(self)}")
            time.sleep(1)  # slow down guessing
            sh.log.warning("API key sign-in rejected from %s", self.address_string())
            audit.request_event(self, None, "auth.signin_failed", "", {"method": "apikey", "prefix": hs.api_key_prefix(key)}, actor="")
            return self.send(401, admin_pages.login_page(sh.SSO, sh.API_KEY_LOGIN, _("Invalid or expired API key.")))
        sessions.reset(f"apikey:{audit.client_ip(self)}")
        sh.log.info("sign-in with API key (%s…)", key[:14])
        self.start_session({"kind": "apikey", "sub": "", "username": "", "name": _("Administrator"),
                            "email": "", "groups": [], "admin": True, "role": "admin", "key": hs.api_key_prefix(key)})

    def local_login(self, form: dict):
        """Sign in with a local account (username + password)."""
        wait = self.rate_limited("local")
        if wait:
            return self.too_many(wait, admin_pages.login_page(
                sh.SSO, sh.API_KEY_LOGIN, _("Too many sign-in attempts. Try again in a few minutes.")))

        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))

        if not username or not password:
            sessions.hit(f"local:{audit.client_ip(self)}")
            return self.send(401, admin_pages.login_page(
                sh.SSO, sh.API_KEY_LOGIN, _("Username and password are required.")))

        # Get account
        account = lac.get_account(username=username)
        if not account:
            sessions.hit(f"local:{audit.client_ip(self)}")
            time.sleep(1)  # slow down enumeration
            sh.log.warning("Local sign-in rejected: unknown user '%s' from %s", username, self.address_string())
            audit.request_event(self, None, "auth.signin_failed", "",
                               {"method": "local", "username": username, "reason": "unknown_user"}, actor="")
            return self.send(401, admin_pages.login_page(
                sh.SSO, sh.API_KEY_LOGIN, _("Wrong username or password.")))

        # Verify password
        if not lac.verify_password(password, account['pw_hash']):
            sessions.hit(f"local:{audit.client_ip(self)}")
            time.sleep(1)  # slow down guessing
            sh.log.warning("Local sign-in rejected: wrong password for '%s' from %s", username, self.address_string())
            audit.request_event(self, None, "auth.signin_failed", "",
                               {"method": "local", "username": username, "reason": "wrong_password"}, actor="")
            return self.send(401, admin_pages.login_page(
                sh.SSO, sh.API_KEY_LOGIN, _("Wrong username or password.")))

        # Check if account is disabled
        if account['disabled']:
            sessions.hit(f"local:{audit.client_ip(self)}")
            sh.log.warning("Local sign-in rejected: disabled account '%s' from %s", username, self.address_string())
            audit.request_event(self, None, "auth.signin_failed", "",
                               {"method": "local", "username": username, "reason": "disabled"}, actor="")
            return self.send(403, admin_pages.login_page(
                sh.SSO, sh.API_KEY_LOGIN, _("This account is disabled.")))

        # Check if TOTP is required
        totp_required = self.totp_required_for(account)
        if totp_required and account.get('totp_confirmed'):
            # TOTP is enabled and confirmed: redirect to second step
            sessions.reset(f"local:{audit.client_ip(self)}")
            # Create a temporary "pending TOTP" token (expires in 5 minutes)
            pending = {
                "account_id": account['id'],
                "username": username,
                "exp": time.time() + 300,  # 5 minutes
            }
            return self.redirect(f"{BASE}/login/totp", [
                self.set_cookie("hse_totp_pending", sh.sign(pending), 300)
            ])
        elif totp_required and not account.get('totp_confirmed'):
            # TOTP is required but not enrolled: force enrollment after login
            sessions.reset(f"local:{audit.client_ip(self)}")
            self.start_session({
                "kind": "local",
                "sub": f"local:{account['id']}",
                "username": username,
                "name": account.get('email', username),
                "email": account.get('email', ''),
                "groups": [],
                "admin": account['role'] == 'admin',
                "role": account['role'],
                "totp_enrollment_required": True,
            })
            return  # start_session redirects

        # Success (password-only login)
        sessions.reset(f"local:{audit.client_ip(self)}")
        sh.log.info("Local sign-in: %s", username)

        # Create session
        self.start_session({
            "kind": "local",
            "sub": f"local:{account['id']}",
            "username": username,
            "name": account.get('email', username),
            "email": account.get('email', ''),
            "groups": [],
            "admin": account['role'] == 'admin',
            "role": account['role'],
        })

    def totp_required_for(self, account: dict) -> bool:
        """Check if TOTP is required for this account based on MFA_REQUIRED setting."""
        if sh.MFA_REQUIRED == "everyone":
            return True
        elif sh.MFA_REQUIRED == "admins":
            return account['role'] == 'admin'
        else:  # optional
            return False

    def totp_verify_page(self):
        """Show the TOTP verification page (second step after password)."""
        pending = sh.unsign(self.cookie("hse_totp_pending"))
        if not pending:
            return self.redirect(f"{BASE}/login")

        username = pending.get("username", "")
        return self.send(200, pages.totp_verify_page(username, error=None))

    def verify_totp_login(self, form: dict):
        """Verify TOTP code during login (second step)."""
        pending = sh.unsign(self.cookie("hse_totp_pending"))
        if not pending:
            return self.redirect(f"{BASE}/login")

        account_id = pending.get("account_id")
        username = pending.get("username", "")

        account = lac.get_account(id=account_id)
        if not account:
            return self.redirect(f"{BASE}/login")

        code = str(form.get("code", "")).strip()
        use_recovery = form.get("use_recovery") == "1"

        if not code:
            return self.send(401, pages.totp_verify_page(username, error=_("Code is required.")))

        valid = False
        if use_recovery:
            # Verify recovery code
            recovery_codes = account.get('recovery_codes', '')
            valid, remaining = lac.verify_recovery_code(recovery_codes, code)
            if valid:
                # Update the account with remaining codes
                with lac._db() as db:
                    db.execute("UPDATE accounts SET recovery_codes = ?, updated = ? WHERE id = ?",
                             (remaining, lac._now(), account_id))
                sh.log.info("Recovery code used for account %s (%s)", account_id, username)
        else:
            # Verify TOTP code
            secret = account.get('totp_secret', '')
            last_step = account.get('totp_last_step')
            valid, new_step = lac.verify_totp(secret, code, last_step)
            if valid:
                # Update last_step to prevent replay
                with lac._db() as db:
                    db.execute("UPDATE accounts SET totp_last_step = ?, updated = ? WHERE id = ?",
                             (new_step, lac._now(), account_id))

        if not valid:
            sessions.hit(f"totp:{audit.client_ip(self)}")
            sh.log.warning("TOTP verification failed for '%s' from %s", username, self.address_string())
            audit.request_event(self, None, "auth.signin_failed", "",
                               {"method": "local+totp", "username": username, "reason": "wrong_totp"}, actor="")
            return self.send(401, pages.totp_verify_page(username, error=_("Invalid code. Try again.")))

        # Success
        sessions.reset(f"totp:{audit.client_ip(self)}")
        sh.log.info("TOTP verified for: %s", username)

        # Create session
        self.start_session({
            "kind": "local",
            "sub": f"local:{account['id']}",
            "username": username,
            "name": account.get('email', username),
            "email": account.get('email', ''),
            "groups": [],
            "admin": account['role'] == 'admin',
            "role": account['role'],
        })
        # Clear the pending cookie
        return  # start_session handles redirect

    def accept_invitation_page(self, token: str):
        """Show the invitation acceptance page (GET /accept/{token})."""
        # Check the token (don't consume it yet)
        data = lac.check_token(token, kind='invite')
        if not data:
            return self.send(400, pages.invitation_page(token, "", "", _("This invitation link is invalid or has expired.")))

        return self.send(200, pages.invitation_page(token, data['email'], data['role'], ""))

    def accept_invitation(self, token: str, form: dict):
        """Accept an invitation and create account (POST /accept/{token})."""
        # Verify and consume the token
        data = lac.verify_token(token, kind='invite')
        if not data:
            return self.send(400, pages.invitation_page(token, "", "", _("This invitation link is invalid or has expired.")))

        email = data['email']
        role = data['role']

        # Get form data
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        password2 = str(form.get("password2", ""))

        # Validate inputs
        if not username or not password:
            return self.send(400, pages.invitation_page(token, email, role, _("Username and password are required.")))

        if password != password2:
            return self.send(400, pages.invitation_page(token, email, role, _("Passwords do not match.")))

        # Validate username format (3-32 chars, alphanumeric + - and _)
        if not re.fullmatch(r"[a-zA-Z0-9_-]{3,32}", username):
            return self.send(400, pages.invitation_page(token, email, role,
                _("Username must be 3-32 characters: letters, numbers, - and _")))

        # Create Headscale user
        try:
            hs.api("POST", "/user", {"name": username})
            sh.log.info("Created Headscale user for invitation: %s", username)
        except Exception as e:
            sh.log.error("Failed to create Headscale user '%s': %s", username, e)
            return self.send(500, pages.invitation_page(token, email, role,
                _("Failed to create user. The username may already exist.")))

        # Create local account
        try:
            account_id = lac.create_account(username, email, password, role=role, headscale_user=username)
            sh.log.info("Created local account from invitation: %s (role=%s)", username, role)
        except ValueError as e:
            # If account creation fails, try to delete the Headscale user
            try:
                hs.api("DELETE", f"/user/{username}")
            except Exception:
                pass
            return self.send(400, pages.invitation_page(token, email, role, str(e)))

        # Sign in automatically
        self.start_session({
            "kind": "local",
            "sub": f"local:{account_id}",
            "username": username,
            "name": email,
            "email": email,
            "groups": [],
            "admin": role == 'admin',
            "role": role,
        })

    def reset_password_page(self, token: str):
        """Show the password reset page (GET /reset/{token})."""
        # Check the token (don't consume it yet)
        data = lac.check_token(token, kind='reset')
        if not data:
            return self.send(400, pages.reset_password_page(token, "", _("This password reset link is invalid or has expired.")))

        # Get the account
        account = lac.get_account(id=data['account_id'])
        if not account:
            return self.send(400, pages.reset_password_page(token, "", _("Account not found.")))

        return self.send(200, pages.reset_password_page(token, account['username'], ""))

    def reset_password(self, token: str, form: dict):
        """Reset password (POST /reset/{token})."""
        # Verify and consume the token
        data = lac.verify_token(token, kind='reset')
        if not data:
            return self.send(400, pages.reset_password_page(token, "", _("This password reset link is invalid or has expired.")))

        # Get the account
        account = lac.get_account(id=data['account_id'])
        if not account:
            return self.send(400, pages.reset_password_page(token, "", _("Account not found.")))

        username = account['username']

        # Get form data
        password = str(form.get("password", ""))
        password2 = str(form.get("password2", ""))

        # Validate inputs
        if not password:
            return self.send(400, pages.reset_password_page(token, username, _("Password is required.")))

        if password != password2:
            return self.send(400, pages.reset_password_page(token, username, _("Passwords do not match.")))

        # Update password
        try:
            lac.update_password(data['account_id'], password)
            sh.log.info("Password reset for account: %s", username)
        except ValueError as e:
            return self.send(400, pages.reset_password_page(token, username, str(e)))

        # Sign in automatically
        self.start_session({
            "kind": "local",
            "sub": f"local:{data['account_id']}",
            "username": username,
            "name": account['email'],
            "email": account['email'],
            "groups": [],
            "admin": account['role'] == 'admin',
            "role": account['role'],
        })

    def start_session(self, data: dict):
        data.update(csrf=secrets.token_urlsafe(24), exp=time.time() + sh.SESSION_TTL)
        role = data.get("role") or ("admin" if data["admin"] else "member")
        data["sid"] = sessions.create(data, audit.client_ip(self), self.headers.get("User-Agent", ""), sh.SESSION_TTL)
        audit.request_event(self, data, "auth.signin", "", {"method": data["kind"], "role": role})
        sh.log.info("sign-in: %s (%s, %s)", data["username"] or data["name"], data["kind"], role)
        # Only a device approval page can be the destination (no open redirect)
        nxt = sh.unsign(self.cookie("hse_next")) or {}
        dest = nxt.get("path", "") if sh.REGISTER_PATH_RE.fullmatch(str(nxt.get("path", ""))) else f"{BASE}/machines"
        self.redirect(dest, [
            self.set_cookie("hse_session", sh.sign(data), sh.SESSION_TTL),
            self.set_cookie("hse_oidc", "", 0),
            self.set_cookie("hse_next", "", 0),
            self.set_cookie("hse_totp_pending", "", 0),  # Clear TOTP pending cookie
        ])

    def logout(self, session: dict):
        # With OIDC, also end the provider session; otherwise "Log out" would
        # not let another user sign in on the same browser.
        target = f"{BASE}/login?m=signed-out"
        if session.get("kind") == "oidc" and sh.SSO:
            end = sh.discovery().get("end_session_endpoint")
            if end:
                target = end + "?" + urllib.parse.urlencode({
                    "id_token_hint": session.get("idt", ""),
                    "post_logout_redirect_uri": f"{sh.PUBLIC_URL}{BASE}/",
                    "client_id": sh.OIDC_CLIENT_ID,
                })
        sessions.revoke(session.get("sid", ""))
        audit.request_event(self, session, "auth.signout")
        self.redirect(target, [self.set_cookie("hse_session", "", 0)])

    def revoke_session(self, session: dict, form: dict):
        """Sign out one session: your own, or (admins) anyone's."""
        sid = str(form.get("sid", ""))
        target = sessions.get(sid)
        own = sid == session.get("sid")
        mine = bool(target) and (target["sub"] == session.get("sub") if session.get("sub") else own)
        if not target or not (mine or session.get("admin")):
            return self.redirect(f"{BASE}/settings/sessions?m=session-not-found")
        sessions.revoke(sid)
        audit.request_event(self, session, "auth.session_revoked", target["name"], {"kind": target["kind"], "ip": target["ip"]})
        if own:
            return self.redirect(f"{BASE}/login?m=signed-out", [self.set_cookie("hse_session", "", 0)])
        return self.redirect(f"{BASE}/settings/sessions?m=session-revoked")

    def revoke_all_sessions(self, session: dict, form: dict):
        """Sign out everywhere: all of the caller's sessions (including this one),
        or, for admins with scope=everyone, everybody else's."""
        if form.get("scope") == "everyone":
            if not session.get("admin"):
                return self.fail(403, _("No permission"), _("This action is for admins only."))
            count = sessions.revoke_everyone(keep=session.get("sid", ""))
            audit.request_event(self, session, "auth.sessions_revoked_all", "", {"scope": "everyone", "count": count})
            return self.redirect(f"{BASE}/settings/sessions?m=sessions-revoked")
        if session.get("sub"):
            count = sessions.revoke_user(session["sub"])
        else:
            count = int(sessions.revoke(session.get("sid", "")))
        audit.request_event(self, session, "auth.sessions_revoked_all", "", {"scope": "mine", "count": count})
        return self.redirect(f"{BASE}/login?m=signed-out", [self.set_cookie("hse_session", "", 0)])

    # --- TOTP and account settings (Block 3.3) ---
    def change_password(self, session: dict, form: dict):
        """Change password for local account."""
        if session.get("kind") != "local":
            return self.fail(403, _("No permission"), _("This action is for local accounts only."))

        account_id = int(session.get("sub", "").split(":")[-1])
        account = lac.get_account(id=account_id)
        if not account:
            return self.fail(404, _("Not found"), _("Account not found."))

        old_password = str(form.get("old_password", ""))
        new_password = str(form.get("new_password", ""))
        new_password2 = str(form.get("new_password2", ""))

        if not old_password or not new_password:
            return self.redirect(f"{BASE}/settings/account?m=password-required")

        if new_password != new_password2:
            return self.redirect(f"{BASE}/settings/account?m=password-mismatch")

        # Verify old password
        if not lac.verify_password(old_password, account['pw_hash']):
            return self.redirect(f"{BASE}/settings/account?m=wrong-password")

        # Update password
        try:
            lac.update_password(account_id, new_password)
            audit.request_event(self, session, "account.password_changed", "", {})
            sh.log.info("Password changed for account %s (%s)", account_id, account['username'])
            return self.redirect(f"{BASE}/settings/account?m=password-changed")
        except ValueError as e:
            return self.redirect(f"{BASE}/settings/account?m=" + urllib.parse.quote(str(e)))

    def confirm_totp_enrollment(self, session: dict, form: dict):
        """Confirm TOTP enrollment with a valid code."""
        if session.get("kind") != "local":
            return self.fail(403, _("No permission"), _("This action is for local accounts only."))

        account_id = int(session.get("sub", "").split(":")[-1])
        account = lac.get_account(id=account_id)
        if not account:
            return self.fail(404, _("Not found"), _("Account not found."))

        code = str(form.get("code", "")).strip()
        if not code:
            return self.redirect(f"{BASE}/settings/account/totp/enroll?m=code-required")

        # Confirm TOTP
        valid = lac.confirm_totp(account_id, code)
        if not valid:
            return self.redirect(f"{BASE}/settings/account/totp/enroll?m=invalid-code")

        audit.request_event(self, session, "account.totp_enabled", "", {})
        sh.log.info("TOTP enabled for account %s (%s)", account_id, account['username'])
        return self.redirect(f"{BASE}/settings/account?m=totp-enabled")

    def disable_totp_account(self, session: dict, form: dict):
        """Disable TOTP for the account."""
        if session.get("kind") != "local":
            return self.fail(403, _("No permission"), _("This action is for local accounts only."))

        account_id = int(session.get("sub", "").split(":")[-1])
        account = lac.get_account(id=account_id)
        if not account:
            return self.fail(404, _("Not found"), _("Account not found."))

        # Disable TOTP
        lac.disable_totp(account_id)
        audit.request_event(self, session, "account.totp_disabled", "", {})
        sh.log.info("TOTP disabled for account %s (%s)", account_id, account['username'])
        return self.redirect(f"{BASE}/settings/account?m=totp-disabled")

    def reset_totp_recovery(self, session: dict, form: dict):
        """Generate new recovery codes."""
        if session.get("kind") != "local":
            return self.fail(403, _("No permission"), _("This action is for local accounts only."))

        account_id = int(session.get("sub", "").split(":")[-1])
        account = lac.get_account(id=account_id)
        if not account:
            return self.fail(404, _("Not found"), _("Account not found."))

        if not account.get('totp_confirmed'):
            return self.redirect(f"{BASE}/settings/account?m=totp-not-enabled")

        # Generate new recovery codes
        new_codes = lac.reset_recovery_codes(account_id)
        audit.request_event(self, session, "account.recovery_codes_reset", "", {})
        sh.log.info("Recovery codes reset for account %s (%s)", account_id, account['username'])

        # Show the new codes to the user
        return self.send(200, pages.recovery_codes_page(session, sh.CTX, new_codes))

    # --- machines ---
    def machine_action(self, session: dict, node_id: str, action: str, form: dict):
        # Go back to where the user was (list or detail)
        back = str(form.get("back", "machines"))
        if not re.fullmatch(r"machines(/\d+)?", back) or action == "delete":
            back = "machines"
        dest = f"{BASE}/{back}"

        node = sh.node_for(session, node_id)
        if node is None:
            sh.log.warning("%s tried '%s' on node %s, which they cannot manage", session["username"], action, node_id)
            return self.redirect(f"{BASE}/machines?m=not-found")
        if sh.is_auditor(session):
            # node_for() resolves any node for an auditor so they can view it;
            # never let that translate into a write, on their own devices or anyone else's
            return self.redirect(f"{dest}?m=forbidden")
        if action in {"expiry", "routes", "approve-routes", "tags"} and not session.get("admin"):
            return self.redirect(f"{dest}?m=forbidden")
        try:
            if action == "rename":
                name = str(form.get("name", "")).strip().lower()
                if not sh.NODE_NAME_RE.fullmatch(name):
                    return self.redirect(f"{dest}?m=bad-name")
                hs.api("POST", f"/node/{node_id}/rename/{urllib.parse.quote(name)}")
                audit.request_event(self, session, "machine.rename", name, {"from": node.get("givenName"), "to": name}, f"node:{node_id}")
                return self.redirect(f"{dest}?m=renamed")
            if action == "expire":
                hs.api("POST", f"/node/{node_id}/expire")
                audit.request_event(self, session, "machine.expire", node.get("givenName"), ref=f"node:{node_id}")
                return self.redirect(f"{dest}?m=expired")
            if action == "expiry":
                if form.get("disable") == "1":
                    hs.api("POST", f"/node/{node_id}/expire?disableExpiry=true")
                    audit.request_event(self, session, "machine.expiry_disable", node.get("givenName"), ref=f"node:{node_id}")
                    return self.redirect(f"{dest}?m=expiry-off")
                # Re-enable: the tailnet's key expiry (Settings → General), or
                # Tailscale's 180 days when devices are set to never expire
                days = hs.key_expiry_days() or 180
                hs.api("POST", f"/node/{node_id}/expire?" + urllib.parse.urlencode({"expiry": sh.iso_in(days)}))
                audit.request_event(self, session, "machine.expiry_enable", node.get("givenName"), {"days": days}, f"node:{node_id}")
                return self.redirect(f"{dest}?m=expiry-on")
            if action == "routes":
                # Only routes the node advertises can be approved
                available = set(node.get("availableRoutes") or [])
                chosen = form.get("route") or []
                routes = [r for r in chosen if r in available]
                if "exit" in chosen:
                    routes += [r for r in sh.EXIT_ROUTES if r in available]
                hs.api("POST", f"/node/{node_id}/approve_routes", {"routes": sorted(set(routes))})
                sh.log.info("%s approved routes %s on node %s", session["username"], routes, node_id)
                audit.request_event(self, session, "machine.routes", node.get("givenName"), {"from": sorted(node.get("approvedRoutes") or []), "to": sorted(set(routes))}, f"node:{node_id}")
                return self.redirect(f"{dest}?m=routes")
            if action == "approve-routes":
                # Approve everything the node advertises, keeping what is already approved
                available = set(node.get("availableRoutes") or [])
                before = set(node.get("approvedRoutes") or [])
                routes = sorted(before | available)
                hs.api("POST", f"/node/{node_id}/approve_routes", {"routes": routes})
                audit.request_event(self, session, "machine.routes", node.get("givenName"), {"from": sorted(before), "to": routes}, f"node:{node_id}")
                return self.redirect(f"{dest}?m=routes")
            if action == "tags":
                tags = [t.lower() for t in sh.lines(str(form.get("tags", "")))]
                if any(not sh.TAG_RE.fullmatch(t) for t in tags):
                    return self.redirect(f"{dest}?m=failed")
                hs.api("POST", f"/node/{node_id}/tags", {"tags": tags})
                audit.request_event(self, session, "machine.tags", node.get("givenName"), {"from": sorted(node.get("tags") or []), "to": sorted(tags)}, f"node:{node_id}")
                return self.redirect(f"{dest}?m=tags")
            hs.api("DELETE", f"/node/{node_id}")
            sh.log.info("%s removed node %s", session["username"], node_id)
            audit.request_event(self, session, "machine.delete", node.get("givenName"), {"user": (node.get("user") or {}).get("name")}, f"node:{node_id}")
            return self.redirect(f"{BASE}/machines?m=removed")
        except urllib.error.HTTPError as exc:
            msg = hs.api_error(exc)
            sh.log.warning("headscale rejected '%s' on %s: %s", action, node_id, msg)
            if action == "tags":
                # The reason (e.g. a tag without an owner in tagOwners) is useful
                return self.send(400, pages.machine_page(session, sh.CTX, sh.to_machines([node])[0], "",
                                                         error=_("Could not save the tags: {reason}", reason=msg)))
            return self.redirect(f"{dest}?m=failed")

    def register_node(self, session: dict, form: dict):
        # Accept the full URL printed by 'tailscale up'
        auth_id = str(form.get("auth_id", "")).strip().rstrip("/").rsplit("/", 1)[-1]
        user = str(form.get("user", ""))
        if not sh.AUTH_ID_RE.fullmatch(auth_id) or user not in {u["name"] for u in hs.all_users()}:
            return self.redirect(f"{BASE}/machines?m=failed")
        try:
            self.register_auth_id(session, user, auth_id)
        except urllib.error.HTTPError as exc:
            return self.send(400, pages.machines_page(session, sh.CTX, sh.to_machines(sh.visible_nodes(session)), True, "",
                                                      hs.all_users(), error=_("Could not register: {reason}",
                                                                              reason=hs.api_error(exc))))
        return self.redirect(f"{BASE}/machines?m=registered")

    def register_auth_id(self, session: dict, user: str, auth_id: str) -> dict:
        """Register a pending 'tailscale up' (Auth ID) to user; the new node."""
        try:
            node = hs.api("POST", "/auth/register", {"user": user, "authId": auth_id}).get("node") or {}
        except urllib.error.HTTPError as exc:
            sh.log.warning("Auth ID registration rejected: %s", hs.api_error(exc))
            raise
        audit.request_event(self, session, "machine.register", user, {"user": user, "auth_id": audit.prefix(auth_id)})
        return node

    def register_owner(self, session: dict) -> tuple[list[dict] | None, dict | None]:
        """(users to choose from, default owner) on the approval page: admins
        pick any user (their own preselected); members and network admins can
        only add devices to their own user."""
        own = sh.my_user(session)
        return (hs.all_users(), own) if session.get("admin") else (None, own)

    def register_view(self, session: dict, auth_id: str):
        if sh.is_auditor(session):
            return self.fail(403, _("No permission"), _("Auditors cannot add devices."))
        users, owner = self.register_owner(session)
        self.send(200, pages.register_page(session, sh.CTX, auth_id, users, owner))

    def register_device(self, session: dict, auth_id: str, form: dict):
        if sh.is_auditor(session):
            return self.fail(403, _("No permission"), _("Auditors cannot add devices."))
        users, owner = self.register_owner(session)
        if users is not None:
            user = str(form.get("user", ""))
            if user not in {u["name"] for u in users}:
                return self.redirect(f"{BASE}/machines?m=failed")
        elif owner:
            user = owner["name"]  # never from the form: a member only adds to themselves
        else:
            return self.redirect(f"{BASE}/register/{auth_id}")
        try:
            node = self.register_auth_id(session, user, auth_id)
        except urllib.error.HTTPError as exc:
            return self.send(400, pages.register_page(session, sh.CTX, auth_id, users, owner,
                                                      error=_("Could not register: {reason}", reason=hs.api_error(exc))))
        dest = f"{BASE}/machines/{node['id']}" if str(node.get("id", "")).isdigit() else f"{BASE}/machines"
        return self.redirect(f"{dest}?m=registered")

    def remove_inactive(self, session: dict, form: dict):
        """Admin: remove the ticked machines of "Remove inactive machines…".
        Checked again now: a machine that came back online (or was seen within
        INACTIVE_DAYS) since the dialog was opened is kept."""
        wanted = expiry.selected_ids(form)
        removed, failed = [], []
        for node in expiry.inactive_nodes(hs.all_nodes()):
            node_id = str(node.get("id"))
            if node_id not in wanted:
                continue
            try:
                hs.api("DELETE", f"/node/{node_id}")
                removed.append(node.get("givenName") or node.get("name") or node_id)
            except urllib.error.HTTPError as exc:
                sh.log.warning("headscale rejected removing inactive node %s: %s", node_id, hs.api_error(exc))
                failed.append(node_id)
        sh.log.info("%s removed %d inactive machine(s): %s", session["username"], len(removed), ", ".join(removed))
        if removed:
            audit.request_event(self, session, "machines.remove_inactive", "", {"nodes": removed})
        return self.redirect(f"{BASE}/machines?m={expiry.remove_result(len(removed), len(failed))}")

    def bulk_machines(self, session: dict, form: dict, op: str):
        """Admin: act on every machine ticked in the Machines table's bulk
        selection at once (expire keys, remove, or add a tag)."""
        wanted = expiry.selected_ids(form)
        nodes = {str(n.get("id")): n for n in hs.all_nodes()}

        tags_to_add: list[str] = []
        if op == "tags":
            tags_to_add, error = sh.bulk_tags_from_form(form)
            if error:
                return self.redirect(f"{BASE}/machines?m=failed")

        done, failed = [], []
        for node_id in wanted:
            node = nodes.get(node_id)
            if node is None:
                continue
            try:
                if op == "expire":
                    hs.api("POST", f"/node/{node_id}/expire")
                elif op == "remove":
                    hs.api("DELETE", f"/node/{node_id}")
                else:
                    current = set(node.get("tags") or [])
                    hs.api("POST", f"/node/{node_id}/tags", {"tags": sorted(current | set(tags_to_add))})
                done.append(node.get("givenName") or node.get("name") or node_id)
            except urllib.error.HTTPError as exc:
                sh.log.warning("headscale rejected bulk %s on node %s: %s", op, node_id, hs.api_error(exc))
                failed.append(node_id)
        sh.log.info("%s bulk-%s %d machine(s): %s", session["username"], op, len(done), ", ".join(done))
        if done:
            details = {"nodes": done, "tags": tags_to_add} if op == "tags" else {"nodes": done}
            audit.request_event(self, session, f"machines.bulk_{op}", "", details)
        flash_op = {"expire": "expired", "remove": "removed", "tags": "tagged"}[op]
        code = f"bulk-{flash_op}-{len(done)}" if done else "failed"
        return self.redirect(f"{BASE}/machines?m={code}")

    # --- keys ---
    def create_key(self, session: dict, form: dict):
        if session.get("admin"):
            uid = str(form.get("user_id", ""))
            user = next((u for u in hs.all_users() if str(u["id"]) == uid), None)
        else:
            user = sh.my_user(session)
        if not user:
            return self.redirect(f"{BASE}/settings/keys?m=no-user")
        days = str(form.get("days", "90"))
        days = days if days in sh.KEY_DAYS else "90"
        key = hs.api("POST", "/preauthkey", {
            "user": str(user["id"]),
            "reusable": form.get("reusable") == "1",
            "ephemeral": form.get("ephemeral") == "1",
            "expiration": sh.iso_in(int(days)),
        })["preAuthKey"]
        sh.log.info("%s generated an auth key for %s (reusable=%s, ephemeral=%s, %s d)", session["username"],
                 user["name"], key.get("reusable"), key.get("ephemeral"), days)
        audit.request_event(self, session, "authkey.create", user["name"], {"key": key.get("key"), "reusable": key.get("reusable"), "ephemeral": key.get("ephemeral"), "days": int(days)}, f"user:{user['id']}")
        # Shown in this very response: Headscale never returns it again
        self.keys_view(session, "", new_key=key)

    def add_docker(self, session: dict, form: dict):
        """Docker tab of Add device: validate the form, optionally make a single-use auth key,
        and show the page again with the snippets filled in."""
        if sh.is_auditor(session):
            return self.fail(403, _("No permission"), _("Auditors cannot add devices."))
        values, error = docker_tab.parse(form)
        admin = bool(session.get("admin"))
        users = hs.all_users() if admin else None
        exit_nodes = sh.exit_nodes_for(session)
        if not error and values["use_exit"] and values["use_exit"] not in {n["ip"] for n in exit_nodes}:
            error = _("Invalid exit node.")
        key = ""
        if not error and values["generate"]:
            if admin:
                user = next((u for u in users if str(u["id"]) == values["user_id"]), None)
            else:
                user = sh.my_user(session)
            if not user:
                error = _("There is no Headscale user to own the key.")
            else:
                created = hs.api("POST", "/preauthkey", {
                    "user": str(user["id"]), "reusable": False, "ephemeral": False,
                    "expiration": sh.iso_in(int(values["days"]))})["preAuthKey"]
                key = created.get("key") or ""
                sh.log.info("%s generated a single-use auth key for %s (Docker tab, %s d)", session["username"],
                         user["name"], values["days"])
                # Never the key itself
                audit.request_event(self, session, "authkey.create", user["name"],
                                    {"reusable": False, "ephemeral": False, "days": int(values["days"]), "source": "docker"},
                                    f"user:{user['id']}")
        page = pages.add_page(session, sh.CTX, docker={"values": values, "key": key, "error": error, "users": users,
                                                       "exit_nodes": exit_nodes})
        return self.send(400 if error else 200, page)

    def revoke_key(self, session: dict, key_id: str):
        if session.get("admin"):
            ok = any(str(k.get("id")) == key_id for k in hs.all_keys())
        else:
            ok = hs.owned_key(sh.my_user(session), key_id) is not None
        if not ok:
            sh.log.warning("%s tried to revoke key %s, which they cannot manage", session["username"], key_id)
            return self.redirect(f"{BASE}/settings/keys?m=not-found")
        hs.api("POST", "/preauthkey/expire", {"id": key_id})
        audit.request_event(self, session, "authkey.revoke", f"#{key_id}", ref=f"authkey:{key_id}")
        return self.redirect(f"{BASE}/settings/keys?m=key-revoked")

    def create_apikey(self, session: dict, form: dict):
        days = str(form.get("days", "90"))
        days = days if days in sh.APIKEY_DAYS else "90"
        key = hs.api("POST", "/apikey", {"expiration": sh.iso_in(int(days))})["apiKey"]
        sh.log.info("%s created an API key (%s d)", session["username"], days)
        audit.request_event(self, session, "apikey.create", hs.api_key_prefix(key), {"days": int(days)}, f"apikey:{hs.api_key_prefix(key)}")
        self.keys_view(session, "", new_apikey=key)

    def expire_apikey(self, key_id: str, session: dict | None = None):
        key = next((k for k in hs.api_keys() if str(k.get("id")) == key_id), None)
        if key is None:
            return self.redirect(f"{BASE}/settings/keys?m=not-found")
        own = hs.own_api_key_prefix()
        if own and own in (key.get("prefix") or ""):
            return self.redirect(f"{BASE}/settings/keys?m=apikey-own")
        hs.api("POST", "/apikey/expire", {"id": key_id})
        audit.request_event(self, session, "apikey.expire", hs.api_key_prefix(key.get("prefix") or "") or f"#{key_id}", ref=f"apikey:{hs.api_key_prefix(key.get('prefix') or '') or key_id}")
        return self.redirect(f"{BASE}/settings/keys?m=apikey-expired")

    # --- users ---
    def users_view(self, session: dict, status: int = 200, flash: str = "", error: str = "",
                   result: dict | None = None, new_key: tuple | None = None):
        users = hs.all_users()
        signins = {a["headscale_user"]: a for a in lac.list_accounts() if a["headscale_user"]}
        extra = (signup.key_result_box(*new_key) if new_key else "") + \
            signup.keys_section(session, lac.list_signup_keys(), signup.mode())
        return self.send(status, admin_pages.users_page(session, sh.CTX, users, hs.all_nodes(), flash, error=error,
                                                        result=result, signins=signins, extra=extra,
                                                        invites=lac.list_active_invitations(),
                                                        can_mail=mailer.enabled()))

    def users_error(self, session: dict, msg: str):
        return self.users_view(session, 400, error=msg)

    def create_user(self, session: dict, form: dict):
        name = str(form.get("name", "")).strip().lower()
        if not sh.USER_NAME_RE.fullmatch(name):
            return self.redirect(f"{BASE}/users?m=bad-user")
        body = {"name": name}
        if form.get("display_name"):
            body["displayName"] = str(form["display_name"]).strip()[:100]
        password = str(form.get("password", ""))
        email = str(form.get("email", "")).strip().lower()
        role = str(form.get("role", "member"))
        must_change = form.get("must_change") == "1"
        with_account = bool(password)
        if with_account:  # a person who can sign in, not only a Headscale user
            if not signup.USERNAME_RE.fullmatch(name):
                return self.users_error(session, _("The user name of an account has 3-32 characters: lowercase letters, numbers, - and _"))
            if not signup.EMAIL_RE.fullmatch(email):
                return self.users_error(session, _("Enter a valid email address."))
            if role not in ("admin", "network_admin", "auditor", "member"):
                return self.users_error(session, _("Choose one of the options."))
            if len(password) < lac.MIN_PASSWORD_LENGTH:
                return self.users_error(session, _("The password must have at least {n} characters.", n=lac.MIN_PASSWORD_LENGTH))
            if lac.get_account(username=name) or lac.get_account(email=email):
                return self.users_error(session, _("That user name or email is already in use."))
        try:
            hs.api("POST", "/user", body)
        except urllib.error.HTTPError as exc:
            return self.users_error(session, _("Could not create it: {reason}", reason=hs.api_error(exc)))
        if with_account:
            try:
                lac.create_account(name, email, password, role=role, headscale_user=name, must_change=must_change)
            except ValueError as exc:
                self.drop_headscale_user(name)
                return self.users_error(session, str(exc))
        sh.log.info("%s created %s %s", session["username"], "account" if with_account else "local user", name)
        # Never the password
        audit.request_event(self, session, "user.create", name, {
            "display_name": body.get("displayName", ""), **({"account": True, "role": role, "must_change": must_change} if with_account else {})})
        return self.redirect(f"{BASE}/users?m=user-created")

    def drop_headscale_user(self, name: str):
        """Undo a Headscale user created a moment ago (the account behind it failed)."""
        try:
            user = next((u for u in hs.all_users() if u.get("name") == name), None)
            if user:
                hs.api("DELETE", f"/user/{user['id']}")
        except Exception:  # noqa: BLE001
            sh.log.warning("could not remove the Headscale user %s after a failed account", name)

    def set_user_password(self, session: dict, user_id: str, form: dict):
        user = next((u for u in hs.all_users() if str(u["id"]) == user_id), None)
        account = lac.get_account_by_headscale_user(user["name"]) if user else None
        if not account:
            return self.redirect(f"{BASE}/users?m=not-found")
        password = str(form.get("password", ""))
        if len(password) < lac.MIN_PASSWORD_LENGTH:
            return self.users_error(session, _("The password must have at least {n} characters.", n=lac.MIN_PASSWORD_LENGTH))
        lac.update_password(account["id"], password, must_change=form.get("must_change") == "1")
        # Their open sessions end: whoever had the old password is signed out
        sessions.revoke_user(f"local:{account['id']}", keep=session.get("sid", ""))
        audit.request_event(self, session, "user.password_set", account["username"],
                            {"must_change": form.get("must_change") == "1"}, f"user:{user_id}")
        return self.redirect(f"{BASE}/users?m=password-set")

    def create_reset_link(self, session: dict, user_id: str):
        """A single-use password reset link for a person with a local account (shown once)."""
        user = next((u for u in hs.all_users() if str(u["id"]) == user_id), None)
        account = lac.get_account_by_headscale_user(user["name"]) if user else None
        if not account:
            return self.redirect(f"{BASE}/users?m=not-found")
        hours = 24
        token = lac.create_reset_token(account["id"], expires_hours=hours)
        expires = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        audit.request_event(self, session, "user.reset_link", account["username"], {}, f"user:{user_id}")
        return self.users_view(session, result={
            "kind": "reset", "link": f"{sh.PUBLIC_URL}{BASE}/reset/{token}", "expires": expires,
            "email": account.get("email") or "", "sent": False})

    def send_link_mail(self, session: dict, form: dict):
        """"Send by e-mail" on a link that was just shown: only when SMTP is set, only on a click,
        and only for a link of ours (the link comes back in the form, so it is checked)."""
        kind = str(form.get("kind", ""))
        link = str(form.get("link", ""))
        to = str(form.get("to", "")).strip()
        expires = str(form.get("expires", ""))
        prefix = {"invite": f"{sh.PUBLIC_URL}{BASE}/accept/", "reset": f"{sh.PUBLIC_URL}{BASE}/reset/"}.get(kind)
        if not mailer.enabled() or not prefix or not link.startswith(prefix) \
                or not re.fullmatch(r"[A-Za-z0-9_-]+", link[len(prefix):]):
            return self.redirect(f"{BASE}/users?m=not-found")
        result = {"kind": kind, "link": link, "expires": expires, "email": to, "sent": False}
        if kind == "invite":
            subject = _("You are invited to {name}", name=sh.TAILNET_NAME)
            body = _("You have been invited to join {name}. Open this link to choose your user name and password "
                     "(it works once and expires on {when}):", name=sh.TAILNET_NAME, when=expires[:16].replace("T", " ") + " UTC")
        else:
            subject = _("Reset your password on {name}", name=sh.TAILNET_NAME)
            body = _("Open this link to choose a new password (it works once and expires on {when}):",
                     name=sh.TAILNET_NAME, when=expires[:16].replace("T", " ") + " UTC")
        try:
            mailer.send(to, subject, f"{body}\n\n{link}\n")
        except mailer.MailError as exc:
            return self.users_view(session, 400, error=_("The e-mail was not sent: {why}", why=str(exc)), result=result)
        audit.request_event(self, session, "link.email", to, {"kind": kind})
        result["sent"] = True
        return self.users_view(session, result=result)

    # --- self-registration ---
    def signup_open(self) -> str:
        """The active sign-up mode."""
        return signup.mode()

    def signup_form(self):
        mode = self.signup_open()
        if mode == "off":
            return self.send(404, "Not found", "text/plain")
        token = secrets.token_urlsafe(24)
        return self.send(200, signup.signup_page(mode, token),
                         headers=[self.set_cookie("hse_signup", token, 3600)])

    def signup_submit(self, form: dict):
        mode = self.signup_open()
        if mode == "off":
            return self.send(404, "Not found", "text/plain")
        ip = audit.client_ip(self)
        wait = self.rate_limited("signup")
        if wait:
            return self.too_many(wait)
        token = self.cookie("hse_signup") or ""
        if not token or not hmac.compare_digest(str(form.get("csrf", "")), token):
            return self.fail(403, _("Session expired"), _("Reload the page and try again."))
        sessions.hit(f"signup:{ip}")  # every attempt counts, successful or not
        username = str(form.get("username", "")).strip().lower()
        email = str(form.get("email", "")).strip().lower()
        password, password2 = str(form.get("password", "")), str(form.get("password2", ""))
        values = {"username": username, "email": email}

        def again(message: str, status: int = 400):
            return self.send(status, signup.signup_page(mode, token, message, values))

        if not signup.USERNAME_RE.fullmatch(username):
            return again(_("The user name has 3-32 characters: lowercase letters, numbers, - and _"))
        if not signup.EMAIL_RE.fullmatch(email):
            return again(_("Enter a valid email address."))
        if len(password) < lac.MIN_PASSWORD_LENGTH:
            return again(_("The password must have at least {n} characters.", n=lac.MIN_PASSWORD_LENGTH))
        if password != password2:
            return again(_("Passwords do not match."))
        key_id = None
        if mode == "invite":
            key_id = lac.use_signup_key(str(form.get("key", "")).strip())
            if key_id is None:  # wrong, expired, revoked or used up: one message for all
                audit.request_event(self, None, "signup.rejected", "", {"reason": "key"}, actor="")
                return again(_("The invitation key is not valid."))

        def give_back():
            if key_id is not None:
                lac.release_signup_key(key_id)

        if lac.get_account(username=username) or lac.get_account(email=email):
            give_back()
            return again(_("That user name or email is already in use."))
        try:
            hs.api("POST", "/user", {"name": username})
        except Exception:  # noqa: BLE001
            give_back()
            return again(_("That user name or email is already in use."))
        try:
            lac.create_account(username, email, password, role="member", headscale_user=username)
        except ValueError:
            give_back()
            self.drop_headscale_user(username)
            return again(_("That user name or email is already in use."))
        sh.log.info("sign-up: %s (%s)", username, mode)
        audit.request_event(self, None, "signup.create", username, {"mode": mode}, actor=username)
        return self.redirect(f"{BASE}/login?m=signed-up", [self.set_cookie("hse_signup", "", 0)])

    def save_signup_mode(self, session: dict, form: dict):
        value = str(form.get("mode", ""))
        if value not in signup.MODES:
            return self.redirect(f"{BASE}/settings/general?m=bad-signup")
        if signup.set_mode(value):
            audit.request_event(self, session, "settings.signup", _("Sign-up"), {"to": value})
        return self.redirect(f"{BASE}/settings/general?m=signup-saved")

    def create_signup_key(self, session: dict, form: dict):
        uses, days = str(form.get("uses", "1")), str(form.get("days", "7"))
        if uses not in signup.KEY_USES or days not in signup.KEY_DAYS:
            return self.users_error(session, _("Choose one of the options."))
        label = str(form.get("label", "")).strip()[:60]
        key = lac.create_signup_key(label, int(uses), int(days) * 24 if days != "0" else None)
        # Never the key
        audit.request_event(self, session, "signup_key.create", label, {"uses": uses, "days": days})
        return self.users_view(session, new_key=(key, label))

    def revoke_signup_key(self, session: dict, key_id: int):
        if lac.revoke_signup_key(key_id):
            audit.request_event(self, session, "signup_key.revoke", str(key_id))
            return self.redirect(f"{BASE}/users?m=signup-key-revoked")
        return self.redirect(f"{BASE}/users?m=not-found")

    def user_action(self, session: dict, user_id: str, action: str, form: dict):
        user = next((u for u in hs.all_users() if str(u["id"]) == user_id), None)
        if user is None:
            return self.redirect(f"{BASE}/users?m=not-found")
        try:
            if action == "rename":
                name = str(form.get("name", "")).strip().lower()
                if not sh.USER_NAME_RE.fullmatch(name):
                    return self.redirect(f"{BASE}/users?m=bad-user")
                hs.api("POST", f"/user/{user_id}/rename/{urllib.parse.quote(name)}")
                audit.request_event(self, session, "user.rename", name, {"from": user.get("name"), "to": name}, f"user:{user_id}")
                return self.redirect(f"{BASE}/users?m=user-renamed")
            if any(str((n.get("user") or {}).get("id")) == user_id for n in hs.all_nodes()):
                return self.redirect(f"{BASE}/users?m=user-has-nodes")
            hs.api("DELETE", f"/user/{user_id}")
            sh.log.info("%s deleted user %s", session["username"], user_id)
            audit.request_event(self, session, "user.delete", user.get("name"), ref=f"user:{user_id}")
            sessions.revoke_named(user.get("name") or "")
            return self.redirect(f"{BASE}/users?m=user-deleted")
        except urllib.error.HTTPError as exc:
            return self.users_error(session, hs.api_error(exc))

    # --- invitations ---
    def create_invitation(self, session: dict, form: dict):
        role = str(form.get("role", ""))
        email = str(form.get("email", "")).strip()
        days = str(form.get("days", "7"))
        days = days if days in sh.INVITE_DAYS else "7"
        who = session["username"] or session["name"]
        try:
            if role not in ("admin", "network_admin", "auditor", "member"):
                raise ValueError(f"Invalid role: {role}")
            expires_hours = int(days) * 24
            token = lac.create_invitation(email, role=role, expires_hours=expires_hours)
            link = f"{sh.PUBLIC_URL}{BASE}/accept/{token}"
            expires = (datetime.now(timezone.utc) + timedelta(hours=expires_hours)).isoformat()
            sh.log.info("%s created an invitation (%s, %s, %s days)", who, role, email, days)
            audit.request_event(self, session, "invite.create", email, {"role": role, "days": days})
            return self.users_view(session, error="", result={
                "kind": "invite", "link": link, "expires": expires, "email": email, "sent": False})
        except ValueError as exc:
            sh.log.warning("%s tried to create an invitation: %s", who, exc)
            return self.users_error(session, str(exc))

    def revoke_invitation(self, session: dict, pk: str):
        # pk is the token hash
        if lac.revoke_token_by_hash(pk):
            sh.log.info("%s revoked an invitation", session["username"] or session["name"])
            audit.request_event(self, session, "invite.revoke", "")
            return self.redirect(f"{BASE}/users?m=invite-revoked")
        return self.redirect(f"{BASE}/users?m=not-found")

def bootstrap_admin() -> None:
    """Bootstrap the first admin account on first run.

    If no accounts exist:
    - If HSE_ADMIN_EMAIL and HSE_ADMIN_PASSWORD are set: create admin account
    - If HSE_ADMIN_EMAIL is set but not password: create invitation token and log it
    - Otherwise: do nothing (the admin is created by the setup wizard)
    """
    accounts = lac.list_accounts()
    if accounts:
        return  # Accounts already exist, nothing to bootstrap

    admin_email = os.environ.get("HSE_ADMIN_EMAIL", "").strip()
    if not admin_email:
        sh.log.info("No accounts exist yet. Set HSE_ADMIN_EMAIL to bootstrap the first admin.")
        return

    admin_password = os.environ.get("HSE_ADMIN_PASSWORD", "").strip()

    if admin_password:
        # Create admin account with the given credentials
        username = admin_email.split("@")[0]  # Use email prefix as username
        try:
            lac.create_account(
                username=username,
                email=admin_email,
                password=admin_password,
                role="admin",
                headscale_user=username
            )
            sh.log.info("✓ Bootstrap: Created admin account '%s' (%s)", username, admin_email)

            # Create corresponding Headscale user
            try:
                hs.create_user(username)
                sh.log.info("✓ Bootstrap: Created Headscale user '%s'", username)
            except Exception as e:
                sh.log.warning("Failed to create Headscale user '%s': %s (will retry on first login)", username, e)
        except Exception as e:
            sh.log.error("Failed to create bootstrap admin account: %s", e)
    else:
        # Create invitation token and log it
        try:
            token = lac.create_invitation(email=admin_email, role="admin", expires_hours=168)
            invite_url = f"{sh.PUBLIC_URL}{BASE}/accept/{token}"
            sh.log.info("=" * 80)
            sh.log.info("Bootstrap invitation created for admin: %s", admin_email)
            sh.log.info("Invitation URL (valid for 7 days):")
            sh.log.info("")
            sh.log.info("    %s", invite_url)
            sh.log.info("")
            sh.log.info("=" * 80)
        except Exception as e:
            sh.log.error("Failed to create bootstrap invitation: %s", e)


# POST routes that only admins reach, in the order they were matched. Each handler gets (handler, session,
# form, match); patterns are matched whole.
def _route(pattern: str):
    return re.compile(rf"{BASE}{pattern}")


_ADMIN_POST_ROUTES = [
    (_route("/machines/register"), lambda h, s, f, m: h.register_node(s, f)),
    (_route("/machines/remove-inactive"), lambda h, s, f, m: h.remove_inactive(s, f)),
    (_route("/machines/bulk/(expire|remove|tags)"), lambda h, s, f, m: h.bulk_machines(s, f, m.group(1))),
    (_route("/settings/key-expiry"), lambda h, s, f, m: h.save_key_expiry(s, f)),
    (_route("/derp"), lambda h, s, f, m: h.save_derp(s, f)),
    (_route("/settings/signup"), lambda h, s, f, m: h.save_signup_mode(s, f)),
    (_route("/signup/keys"), lambda h, s, f, m: h.create_signup_key(s, f)),
    (_route(r"/signup/keys/(\d+)/revoke"), lambda h, s, f, m: h.revoke_signup_key(s, int(m.group(1)))),
    (_route(r"/users/(\d+)/password"), lambda h, s, f, m: h.set_user_password(s, m.group(1), f)),
    (_route(r"/users/(\d+)/reset-link"), lambda h, s, f, m: h.create_reset_link(s, m.group(1))),
    (_route("/users/send-link"), lambda h, s, f, m: h.send_link_mail(s, f)),
    (_route("/settings/notify-test"), lambda h, s, f, m: h.notify_test(s)),
    (_route("/backups/run"), lambda h, s, f, m: h.backup_now(s)),
    (_route("/backups/settings"), lambda h, s, f, m: h.backup_settings(s, f)),
    (_route("/backups/restore"), lambda h, s, f, m: h.backup_restore(s, f)),
    (_route("/backups/download"), lambda h, s, f, m: h.backup_download(s, f)),
    (_route("/users"), lambda h, s, f, m: h.create_user(s, f)),
    (_route(r"/users/(\d+)/(rename|delete)"), lambda h, s, f, m: h.user_action(s, m.group(1), m.group(2), f)),
    (_route("/invitations"), lambda h, s, f, m: h.create_invitation(s, f)),
    # Revoke invitation: the token hash (64 hex chars)
    (_route("/invitations/([0-9a-f-]{32,})/revoke"), lambda h, s, f, m: h.revoke_invitation(s, m.group(1))),
    (_route("/apikeys"), lambda h, s, f, m: h.create_apikey(s, f)),
    (_route(r"/apikeys/(\d+)/expire"), lambda h, s, f, m: h.expire_apikey(m.group(1), s)),
]


def main():
    port = int(os.environ.get("PORT", "8000"))
    sh.log.info("Headscale Easy %s listening on :%d (public: %s%s, SSO=%s, API key sign-in=%s%s)",
             VERSION, port, sh.PUBLIC_URL, BASE, sh.SSO, sh.API_KEY_LOGIN, ", DEMO MODE" if sh.DEMO else "")

    # Initialize local accounts database
    lac.configure(os.environ.get("ACCOUNTS_DB", "/data/console/accounts.db"))

    # Bootstrap first admin if no accounts exist
    bootstrap_admin()

    apikey.start()
    audit.start()
    naming.start()
    notify.start()

    def stop(*_):  # end the open event streams, then exit like the default SIGTERM would
        sh.HUB.close_all()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
