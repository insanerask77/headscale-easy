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

import hmac
import logging
import mimetypes
import os
import re
import signal
import time
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("headscale-easy")

import admin_pages  # noqa: E402
from handlers import shared as sh  # noqa: E402
from handlers.access import AccessHandlers, bootstrap_admin  # noqa: E402
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
import local_accounts as lac  # noqa: E402
import status as server_status  # noqa: E402
import status_pages  # noqa: E402
import backup_pages  # noqa: E402

BACKUP_UPLOAD_MAX = backup_pages.UPLOAD_MAX_MB * 1024 * 1024  # bytes of a backup file the console accepts
import headscale as hs  # noqa: E402
import apikey  # noqa: E402
import audit  # noqa: E402
import audit_pages  # noqa: E402
import naming  # noqa: E402
import notify  # noqa: E402
import pages  # noqa: E402
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
            return self.send(200, audit_pages.page(session, sh.CTX, params))
        if path == f"{BASE}/logs.csv":
            return self.send(200, audit_pages.csv_export(params), "text/csv; charset=utf-8",
                             [("Content-Disposition", 'attachment; filename="activity-log.csv"')])
        self.fail(404, _("Not found"), _("That page does not exist."))

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

    audit.subscribe(notify.on_audit_event)
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
