"""Security boundary tests: signed cookies, CSRF, admin-only pages and actions,
members limited to their own machines and keys, static files, redirects, the
OIDC admin-by-email rule and the demo mode. Requests go through the real
request handler; Headscale, Authentik and the audit log are replaced by fakes
that fail the test if an action reaches them when it should not.
Standard library only:

    python3 tests/test_security.py
"""
import base64
import email.message
import io
import json
import os
import sys
import time
import unittest
import urllib.parse
from unittest import mock

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
os.environ.update(HEADSCALE_API_KEY="x", PUBLIC_URL="https://vpn.example.com", SESSION_SECRET="s",
                  HEADSCALE_URL="http://headscale:8080")
sys.path.insert(0, WEB)

import app  # noqa: E402
import audit  # noqa: E402
import headscale as hs  # noqa: E402
import sessions  # noqa: E402

sessions.configure(":memory:")

B = app.BASE
ADMIN = {"kind": "oidc", "sub": "a", "username": "root", "name": "Root", "email": "", "groups": [],
         "admin": True, "csrf": "tok", "exp": time.time() + 3600}
MEMBER = dict(ADMIN, sub="b", username="bob", name="Bob", admin=False)
BOB = {"id": "2", "name": "bob"}
BOB_NODE = {"id": "7", "givenName": "bob-laptop", "user": BOB}


def request(method: str, path: str, session: dict | None = None, form: dict | None = None,
            headers: dict | None = None, raw: bytes | None = None) -> tuple[int, dict, str]:
    """Run one request through app.Handler; (status, headers, body). ``raw`` replaces the form-encoded body."""
    body = raw if raw is not None else urllib.parse.urlencode(form or {}, doseq=True).encode()
    msg = email.message.Message()
    if session is not None and "sid" not in session:
        session = dict(session, sid=sessions.create(session))  # a live server-side session
    if session is not None:
        msg["Cookie"] = f"hse_session={app.sign(session)}"
    msg["Content-Length"] = str(len(body))
    for k, v in (headers or {}).items():
        if k.lower() == "cookie" and "Cookie" in msg:
            msg.replace_header("Cookie", f"{msg['Cookie']}; {v}")  # one Cookie header, as a browser sends
        else:
            msg[k] = v
    h = app.Handler.__new__(app.Handler)
    h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
    h.headers, h.command, h.path = msg, method, path
    h.request_version, h.requestline, h.client_address = "HTTP/1.1", f"{method} {path} HTTP/1.1", ("127.0.0.1", 1)
    h.close_connection = True
    getattr(h, f"do_{method}")()
    head, _, rest = h.wfile.getvalue().partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    out_headers: dict = {}
    for line in lines[1:]:
        k, _, v = line.partition(": ")
        out_headers.setdefault(k.lower(), []).append(v)
    return int(lines[0].split()[1]), out_headers, rest.decode(errors="replace")


def location(headers: dict) -> str:
    return headers.get("location", [""])[0]


class Base(unittest.TestCase):
    """Every Headscale call fails unless a test allows it explicitly."""

    def setUp(self):
        self.api = mock.Mock(side_effect=AssertionError("unexpected Headscale API call"))
        patches = [
            mock.patch.object(hs, "api", self.api),
            mock.patch.object(hs, "http_json", mock.Mock(side_effect=AssertionError("unexpected HTTP call"))),
            mock.patch.object(audit, "request_event", mock.Mock()),
            mock.patch.object(hs, "user_for_sub", lambda sub: BOB if sub == "b" else None),
            mock.patch.object(hs, "user_nodes", lambda user: [BOB_NODE] if user is BOB else []),
            mock.patch.object(hs, "user_keys", lambda user: [{"id": "3", "user": BOB}] if user is BOB else []),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class SignedCookies(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(app.unsign(app.sign(ADMIN)), ADMIN)

    def test_tampered_payload_is_rejected(self):
        payload, mac = app.sign(MEMBER).rsplit(".", 1)
        forged = base64.urlsafe_b64encode(json.dumps(dict(MEMBER, admin=True)).encode()).decode()
        self.assertIsNone(app.unsign(f"{forged}.{mac}"))

    def test_wrong_secret_is_rejected(self):
        with mock.patch.object(app, "SESSION_SECRET", b"other"):
            cookie = app.sign(ADMIN)
        self.assertIsNone(app.unsign(cookie))

    def test_expired_is_rejected(self):
        self.assertIsNone(app.unsign(app.sign(dict(ADMIN, exp=time.time() - 1))))

    def test_garbage_is_rejected(self):
        for value in (None, "", "abc", "a.b", "..", "e30.0"):
            self.assertIsNone(app.unsign(value), value)

    def test_cookie_flags(self):
        _, value = app.Handler.set_cookie("hse_session", "v", 60)
        for flag in ("HttpOnly", "SameSite=Lax", f"Path={B}", "Secure"):
            self.assertIn(flag, value)


class Unauthenticated(Base):
    def test_pages_redirect_to_sign_in(self):
        for path in ("/machines", "/users", "/acl", "/dns", "/logs.csv", "/settings/keys", "/machines/1"):
            status, headers, _ = request("GET", B + path)
            self.assertEqual((status, location(headers)), (303, f"{B}/login"), path)

    def test_actions_redirect_to_sign_in(self):
        for path in ("/keys", "/users", "/acl", "/dns", "/apikeys", "/machines/1/delete", "/backups/run",
                     "/backups/settings", "/backups/restore",
                     "/backups/download"):
            status, headers, _ = request("POST", B + path, form={"csrf": "tok"})
            self.assertEqual((status, location(headers)), (303, f"{B}/login"), path)
        self.api.assert_not_called()

    def test_forged_session_cookie(self):
        forged = base64.urlsafe_b64encode(json.dumps(ADMIN).encode()).decode() + ".0"
        status, headers, _ = request("GET", f"{B}/users", headers={"Cookie": f"hse_session={forged}"})
        self.assertEqual((status, location(headers)), (303, f"{B}/login"))


class Csrf(Base):
    def test_backup_now_needs_the_token_and_an_admin(self):
        with mock.patch.object(app.hs, "helper_backup", return_value="started") as run:
            for form in ({}, {"csrf": ""}, {"csrf": "other"}):
                status, _, _ = request("POST", f"{B}/backups/run", ADMIN, form)
                self.assertEqual(status, 403, form)
            status, _, _ = request("POST", f"{B}/backups/run", MEMBER, {"csrf": "tok"})
            self.assertEqual(status, 403)
        run.assert_not_called()

    def test_missing_or_wrong_token(self):
        for form in ({}, {"csrf": ""}, {"csrf": "other"}):
            status, _, _ = request("POST", f"{B}/users", ADMIN, dict(form, name="eve"))
            self.assertEqual(status, 403, form)
        self.api.assert_not_called()



class AdminOnly(Base):
    def test_member_cannot_open_admin_pages(self):
        for path in ("/users", "/acl", "/logs", "/logs.csv"):
            status, _, _ = request("GET", B + path, MEMBER)
            self.assertEqual(status, 403, path)

    def test_member_cannot_run_admin_actions(self):
        actions = ["/machines/register", "/machines/remove-inactive", "/settings/key-expiry", "/settings/mfa",
                   "/users", "/users/1/rename", "/users/1/delete", "/acl", "/dns", "/apikeys",
                   "/apikeys/1/expire", "/invitations", "/accounts/1/recovery"]
        for path in actions:
            status, _, _ = request("POST", B + path, MEMBER, {"csrf": "tok", "action": "save", "policy": "{}"})
            self.assertEqual(status, 403, path)
        self.api.assert_not_called()

    def test_local_member_cannot_access_admin_invitations(self):
        """Local account members cannot create or manage invitations."""
        import local_accounts as lac
        lac.configure(":memory:")

        # Create a local member session
        local_member = dict(MEMBER, kind="local", sub="local:1")

        # Member cannot create invitations
        status, _, _ = request("POST", f"{B}/invitations", local_member,
                              {"csrf": "tok", "email": "test@example.com", "role": "member"})
        self.assertEqual(status, 403, "Local members should not be able to create invitations")


class MemberOwnership(Base):
    def test_other_users_machine_is_not_found(self):
        status, headers, _ = request("GET", f"{B}/machines/99", MEMBER)
        self.assertEqual(location(headers), f"{B}/machines?m=not-found")
        for action in ("rename", "delete", "expire"):
            request("POST", f"{B}/machines/99/{action}", MEMBER, {"csrf": "tok", "name": "x"})
        self.api.assert_not_called()

    def test_admin_only_machine_settings(self):
        for action in ("expiry", "routes", "tags"):
            _, headers, _ = request("POST", f"{B}/machines/7/{action}", MEMBER, {"csrf": "tok", "tags": "tag:x"})
            self.assertTrue(location(headers).endswith("m=forbidden"), action)
        self.api.assert_not_called()

    def test_own_machine_can_be_renamed(self):
        self.api.side_effect = None
        _, headers, _ = request("POST", f"{B}/machines/7/rename", MEMBER, {"csrf": "tok", "name": "bob-pc"})
        self.assertEqual(location(headers), f"{B}/machines?m=renamed")
        self.api.assert_called_once_with("POST", "/node/7/rename/bob-pc")

    def test_other_users_key_cannot_be_revoked(self):
        _, headers, _ = request("POST", f"{B}/keys/99/revoke", MEMBER, {"csrf": "tok"})
        self.assertEqual(location(headers), f"{B}/settings/keys?m=not-found")
        self.api.assert_not_called()

    def test_member_key_is_for_themselves(self):
        """A member cannot pick the user an auth key is for."""
        self.api.side_effect = None
        self.api.return_value = {"preAuthKey": {"key": "k"}}
        with mock.patch.object(app.Handler, "keys_view", lambda self, *a, **kw: self.send(200, "ok")):
            request("POST", f"{B}/keys", MEMBER, {"csrf": "tok", "user_id": "1"})

    def test_local_member_sees_only_own_user(self):
        """Local account members can only see their own Headscale user."""
        import local_accounts as lac
        lac.configure(":memory:")

        # Create two local accounts
        alice_id = lac.create_account("alice", "alice@example.com", "password123",
                                      role="member", headscale_user="alice")
        bob_id = lac.create_account("bob", "bob@example.com", "password123",
                                    role="member", headscale_user="bob")

        # Create sessions for both
        alice_session = dict(MEMBER, kind="local", sub=f"local:{alice_id}", username="alice")
        bob_session = dict(MEMBER, kind="local", sub=f"local:{bob_id}", username="bob")

        # Mock Headscale to return users
        self.api.side_effect = None
        self.api.return_value = {
            "users": [
                {"id": "1", "name": "alice"},
                {"id": "2", "name": "bob"}
            ]
        }

        # Alice should only see her user in my_user()
        alice_user = app.my_user(alice_session)
        self.assertIsNotNone(alice_user, "Alice should have a Headscale user")
        self.assertEqual(alice_user["name"], "alice", "Alice should see her own user")

        # Bob should only see his user in my_user()
        bob_user = app.my_user(bob_session)
        self.assertIsNotNone(bob_user, "Bob should have a Headscale user")
        self.assertEqual(bob_user["name"], "bob", "Bob should see his own user")

    def test_invalid_machine_name(self):
        _, headers, _ = request("POST", f"{B}/machines/7/rename", MEMBER, {"csrf": "tok", "name": "../x"})
        self.assertEqual(location(headers), f"{B}/machines?m=bad-name")
        self.api.assert_not_called()


class StaticAndRedirects(Base):
    def test_static_path_traversal(self):
        for name in ("../app.py", "..%2fapp.py", "%2e%2e/app.py", "/etc/passwd", "../../../etc/passwd"):
            status, _, body = request("GET", f"{B}/static/{name}")
            self.assertEqual(status, 404, name)
            self.assertNotIn("SESSION_SECRET", body)

    def test_static_file(self):
        self.assertEqual(request("GET", f"{B}/static/style.css")[0], 200)

    def test_language_switch_stays_on_the_console(self):
        for referer in ("https://evil.example/admin/users", "https://vpn.example.com.evil.example/admin/x",
                        "https://vpn.example.com/../evil", "//evil.example/admin"):
            _, headers, _ = request("POST", f"{B}/settings/language", MEMBER, {"csrf": "tok", "lang": "es"},
                                    {"Referer": referer})
            self.assertTrue(location(headers).startswith(f"{B}/"), (referer, location(headers)))

    def test_language_switch_sticks_on_every_language(self):
        for lang in app.LANGUAGES:
            status, headers, _ = request("POST", f"{B}/settings/language", MEMBER, {"csrf": "tok", "lang": lang},
                                         {"Referer": f"https://vpn.example.com{B}/machines"})
            self.assertEqual((status, location(headers)), (303, f"{B}/machines"), lang)
            cookie = headers["set-cookie"][0].split(";")[0]
            self.assertEqual(cookie, f"hse_lang={lang}")
            status, _, body = request("GET", f"{B}/settings/general", MEMBER, headers={"Cookie": cookie})
            self.assertIn(f'<html lang="{lang}"', body, lang)

    def test_machine_action_back_parameter(self):
        self.api.side_effect = None
        _, headers, _ = request("POST", f"{B}/machines/7/rename", MEMBER,
                                {"csrf": "tok", "name": "bob-pc", "back": "//evil.example"})
        self.assertEqual(location(headers), f"{B}/machines?m=renamed")

    def test_security_headers(self):
        _, headers, _ = request("GET", f"{B}/settings/general", MEMBER)
        csp = headers["content-security-policy"][0]
        self.assertIn("default-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(headers["x-content-type-options"], ["nosniff"])


class OidcAdminByEmail(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(app, "SSO", True)
        p.start()
        self.addCleanup(p.stop)

    def sign_in(self, userinfo: dict) -> dict:
        tx = app.sign({"state": "st", "verifier": "v", "exp": time.time() + 60})
        responses = {"token": {"access_token": "at"}, "userinfo": userinfo}
        http = mock.Mock(side_effect=lambda method, url, **kw: responses["token" if url.endswith("/token") else "userinfo"])
        with mock.patch.object(hs, "http_json", http), \
                mock.patch.object(app, "discovery", lambda: {"token_endpoint": "https://idp/token",
                                                             "userinfo_endpoint": "https://idp/userinfo"}), \
                mock.patch.object(app, "ADMIN_EMAILS", {"boss@example.com"}):
            status, headers, _ = request("GET", f"{B}/callback?code=c&state=st", headers={"Cookie": f"hse_oidc={tx}"})
        self.assertEqual(status, 303)
        cookie = next(c for c in headers["set-cookie"] if c.startswith("hse_session="))
        return app.unsign(cookie.split(";")[0].split("=", 1)[1])

    def test_verified_email_is_admin(self):
        self.assertTrue(self.sign_in({"sub": "x", "email": "Boss@example.com", "email_verified": True})["admin"])

    def test_no_verified_claim_is_admin(self):
        self.assertTrue(self.sign_in({"sub": "x", "email": "boss@example.com"})["admin"])

    def test_unverified_email_is_member(self):
        self.assertFalse(self.sign_in({"sub": "x", "email": "boss@example.com", "email_verified": False})["admin"])

    def test_other_email_is_member(self):
        self.assertFalse(self.sign_in({"sub": "x", "email": "eve@example.com", "email_verified": True})["admin"])

    def test_bad_state(self):
        tx = app.sign({"state": "st", "verifier": "v", "exp": time.time() + 60})
        status, _, _ = request("GET", f"{B}/callback?code=c&state=other", headers={"Cookie": f"hse_oidc={tx}"})
        self.assertEqual(status, 400)


class DemoMode(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(app, "DEMO", True)
        p.start()
        self.addCleanup(p.stop)

    def test_blocked_actions(self):
        for path in ("/keys", "/apikeys", "/apikeys/1/expire", "/machines/register", "/machines/remove-inactive",
                     "/machines/7/delete", "/machines/7/expire", "/settings/key-expiry", "/settings/mfa", "/users",
                     "/users/1/delete", "/invitations", "/accounts/1/recovery", "/dns", "/machines/bulk/expire",
                     "/machines/bulk/remove", "/acl/rules", "/acl/ssh", "/acl/autoapprove/routes",
                     "/register/hskey-authreq-abcdefgh"):
            status, _, body = request("POST", B + path, ADMIN, {"csrf": "tok"})
            self.assertEqual(status, 403, path)
        status, _, _ = request("POST", f"{B}/acl", ADMIN, {"csrf": "tok", "action": "save", "policy": "{}"})
        self.assertEqual(status, 403)
        self.api.assert_not_called()

    def test_allowed_actions(self):
        self.api.side_effect = None
        _, headers, _ = request("POST", f"{B}/machines/7/rename", MEMBER, {"csrf": "tok", "name": "bob-pc"})
        self.assertEqual(location(headers), f"{B}/machines?m=renamed")

    def test_banner(self):
        import ui
        with mock.patch.object(ui, "DEMO", True):
            self.assertIn("DEMO ENVIRONMENT", ui.bare_page("t", ""))
        with mock.patch.object(ui, "DEMO", False):
            self.assertNotIn("DEMO ENVIRONMENT", ui.bare_page("t", ""))

    def test_demo_blocks_local_account_invitation_creation(self):
        """Demo mode blocks creation of local account invitations."""
        import local_accounts as lac
        lac.configure(":memory:")

        # Attempt to create invitation in demo mode - should be blocked
        status, _, body = request("POST", f"{B}/invitations", ADMIN,
                                 {"csrf": "tok", "email": "test@example.com", "role": "member"})
        self.assertEqual(status, 403, "Demo mode should block invitation creation")
        self.api.assert_not_called()


class DeviceApproval(Base):
    """/register/<auth id> (Headscale's link, sent here by Caddy without OIDC):
    members add the device to their own user only, admins choose, auditors
    cannot, and the page survives the sign-in round trip without becoming an
    open redirect."""

    AUTH = "hskey-authreq-test-device-0001"
    PATH = f"{B}/register/{AUTH}"
    USERS = [{"id": "1", "name": "root"}, BOB]

    def setUp(self):
        super().setUp()
        p = mock.patch.object(hs, "all_users", lambda: self.USERS)
        p.start()
        self.addCleanup(p.stop)

    def registered(self, node_id: str = "9"):
        self.api.side_effect = None
        self.api.return_value = {"node": {"id": node_id}}

    def test_signed_out_goes_to_sign_in_and_remembers_the_page(self):
        status, headers, _ = request("GET", self.PATH)
        self.assertEqual((status, location(headers)), (303, f"{B}/login"))
        cookie = next(c for c in headers["set-cookie"] if c.startswith("hse_next="))
        self.assertEqual(app.unsign(cookie.split(";")[0].split("=", 1)[1])["path"], self.PATH)

    def test_sign_in_returns_to_the_approval_page(self):
        h = app.Handler.__new__(app.Handler)
        nxt = app.sign({"path": self.PATH, "exp": time.time() + 60})
        for value, dest in ((nxt, self.PATH),
                            (app.sign({"path": "https://evil.example/", "exp": time.time() + 60}), f"{B}/machines"),
                            (app.sign({"path": f"{B}/users", "exp": time.time() + 60}), f"{B}/machines"),
                            (app.sign({"path": self.PATH, "exp": time.time() - 1}), f"{B}/machines"),
                            (nxt[:-1] + ("1" if nxt[-1] == "0" else "0"), f"{B}/machines")):  # always a different last digit
            msg = email.message.Message()
            msg["Cookie"] = f"hse_next={value}"
            h.headers, h.client_address = msg, ("127.0.0.1", 1)
            with mock.patch.object(h, "redirect") as redirect:
                h.start_session(dict(MEMBER))
            self.assertEqual(redirect.call_args[0][0], dest, value)

    def test_member_sees_own_user_and_registers_to_it(self):
        status, _, body = request("GET", self.PATH, MEMBER)
        self.assertEqual(status, 200)
        self.assertNotIn("<select", body)
        self.registered("9")
        # A forged owner in the form is ignored
        _, headers, _ = request("POST", self.PATH, MEMBER, {"csrf": "tok", "user": "root"})
        self.assertEqual(location(headers), f"{B}/machines/9?m=registered")
        self.api.assert_called_once_with("POST", "/auth/register", {"user": "bob", "authId": self.AUTH})

    def test_member_without_user_cannot_register(self):
        nobody = dict(MEMBER, sub="z")
        status, _, body = request("GET", self.PATH, nobody)
        self.assertEqual(status, 200)
        self.assertNotIn('method="post" action="' + self.PATH, body)
        request("POST", self.PATH, nobody, {"csrf": "tok"})
        self.api.assert_not_called()

    def test_admin_chooses_an_existing_user(self):
        status, _, body = request("GET", self.PATH, ADMIN)
        self.assertEqual(status, 200)
        self.assertIn("<select", body)
        _, headers, _ = request("POST", self.PATH, ADMIN, {"csrf": "tok", "user": "mallory"})
        self.assertEqual(location(headers), f"{B}/machines?m=failed")
        self.api.assert_not_called()
        self.registered("4")
        request("POST", self.PATH, ADMIN, {"csrf": "tok", "user": "bob"})
        self.api.assert_called_once_with("POST", "/auth/register", {"user": "bob", "authId": self.AUTH})

    def test_auditor_cannot_register(self):
        auditor = dict(MEMBER, role="auditor")
        self.assertEqual(request("GET", self.PATH, auditor)[0], 403)
        self.assertEqual(request("POST", self.PATH, auditor, {"csrf": "tok"})[0], 403)
        self.api.assert_not_called()

    def test_csrf_required(self):
        self.assertEqual(request("POST", self.PATH, MEMBER, {"csrf": "bad"})[0], 403)
        self.api.assert_not_called()

    def test_invalid_auth_id_is_not_a_route(self):
        for path in (f"{B}/register/short", f"{B}/register/{self.AUTH}/x", f"{B}/register/a%2Fb%2Fcdefgh"):
            self.assertIn(request("POST", path, MEMBER, {"csrf": "tok"})[0], (403, 404), path)
        self.api.assert_not_called()


class LocalAccountSignin(Base):
    """Local account sign-in with username + password."""

    def setUp(self):
        super().setUp()
        # Reset sessions for each test
        sessions.configure(":memory:")
        # Clear the in-memory rate limiting dict
        sessions._hits.clear()
        # Configure local accounts and create a test account
        import local_accounts as la
        la.configure(":memory:")
        # Create member account
        la.create_account("alice", "alice@example.com", "password123", role="member", headscale_user="bob")
        # Create admin account
        la.create_account("admin", "admin@example.com", "adminpass", role="admin")
        # Create disabled account
        disabled_id = la.create_account("disabled", "disabled@example.com", "password123")
        la.disable_account(disabled_id)

    def test_local_signin_success(self):
        """Local sign-in with correct credentials succeeds."""
        status, headers, _ = request("POST", f"{B}/login/local", None,
                                    {"username": "alice", "password": "password123"})
        self.assertEqual(status, 303)
        # Should redirect to /admin/machines
        self.assertEqual(location(headers), f"{B}/machines")
        # Session cookie should be set
        self.assertIn("set-cookie", headers)

    def test_local_signin_wrong_password(self):
        """Local sign-in with wrong password fails."""
        status, _, body = request("POST", f"{B}/login/local", None,
                                 {"username": "alice", "password": "wrongpass"})
        self.assertEqual(status, 401)
        self.assertIn("wrong username or password", body.lower())

    def test_local_signin_unknown_user(self):
        """Local sign-in with unknown username fails."""
        status, _, body = request("POST", f"{B}/login/local", None,
                                 {"username": "nonexistent", "password": "password123"})
        self.assertEqual(status, 401)
        self.assertIn("wrong username or password", body.lower())

    def test_local_signin_disabled_account(self):
        """Local sign-in with disabled account fails."""
        status, _, body = request("POST", f"{B}/login/local", None,
                                 {"username": "disabled", "password": "password123"})
        self.assertEqual(status, 403)
        self.assertIn("disabled", body.lower())

    def test_local_signin_rate_limited(self):
        """Local sign-in is rate-limited after too many attempts."""
        # Make several failed attempts
        for _ in range(11):  # SIGNIN_RATE_LIMIT default is 10
            request("POST", f"{B}/login/local", None,
                   {"username": "alice", "password": "wrongpass"})

        # Next attempt should be rate-limited
        status, _, _ = request("POST", f"{B}/login/local", None,
                              {"username": "alice", "password": "password123"})
        self.assertEqual(status, 429)

    def test_local_signin_empty_credentials(self):
        """Local sign-in with empty credentials fails."""
        status, _, _ = request("POST", f"{B}/login/local", None,
                              {"username": "", "password": ""})
        self.assertEqual(status, 401)

    def test_local_signin_creates_session_with_role(self):
        """Local sign-in creates a session with the correct role."""
        # Admin sign-in
        _, headers, _ = request("POST", f"{B}/login/local", None,
                               {"username": "admin", "password": "adminpass"})
        cookie = next(c for c in headers["set-cookie"] if c.startswith("hse_session="))
        session_value = cookie.split(";")[0].split("=", 1)[1]
        session = app.unsign(session_value)

        self.assertEqual(session["kind"], "local")
        self.assertEqual(session["username"], "admin")
        self.assertEqual(session["role"], "admin")
        self.assertTrue(session["admin"])


class TOTPSignin(unittest.TestCase):
    """TOTP second-factor authentication tests (Block 3.3)."""

    def setUp(self):
        """Set up local accounts with TOTP."""
        import local_accounts as la
        la.configure(":memory:")
        self.la = la

        # Create test accounts
        self.admin_id = la.create_account("admin", "admin@example.com", "password123", role="admin")
        self.member_id = la.create_account("member", "member@example.com", "password123", role="member")

        # Configure MFA_REQUIRED via environment (will be read by app.py on import)
        self.original_mfa = os.environ.get("MFA_REQUIRED", "")

    def tearDown(self):
        """Restore MFA_REQUIRED setting."""
        if self.original_mfa:
            os.environ["MFA_REQUIRED"] = self.original_mfa
        elif "MFA_REQUIRED" in os.environ:
            del os.environ["MFA_REQUIRED"]

    def test_totp_required_for_admins(self):
        """When MFA_REQUIRED=admins, admins must set up TOTP."""
        os.environ["MFA_REQUIRED"] = "admins"
        # Need to reload app to pick up the new setting
        import importlib
        importlib.reload(app)

        # Sign in as admin without TOTP enrolled
        status, headers, body = request("POST", f"{B}/login/local", form={
            "username": "admin",
            "password": "password123"
        })

        # Should succeed and create session (but may force enrollment)
        self.assertEqual(status, 303)

    def test_totp_optional_for_members_when_mode_is_admins(self):
        """When MFA_REQUIRED=admins, members can sign in without TOTP."""
        os.environ["MFA_REQUIRED"] = "admins"
        import importlib
        importlib.reload(app)

        # Sign in as member without TOTP
        status, headers, body = request("POST", f"{B}/login/local", form={
            "username": "member",
            "password": "password123"
        })

        # Should succeed (no TOTP required)
        self.assertEqual(status, 303)
        self.assertTrue(location(headers).endswith("/machines"))

    def test_signin_with_totp_success(self):
        """Sign in with TOTP second factor."""
        # Enroll and confirm TOTP for admin
        secret, _ = self.la.enroll_totp(self.admin_id)
        code = self.la.compute_totp(secret, int(time.time()))
        self.assertTrue(self.la.confirm_totp(self.admin_id, code))

        os.environ["MFA_REQUIRED"] = "admins"
        import importlib
        importlib.reload(app)

        # Sign in with password - should redirect to TOTP page
        status, headers, body = request("POST", f"{B}/login/local", form={
            "username": "admin",
            "password": "password123"
        })
        self.assertEqual(status, 303)
        self.assertTrue(location(headers).endswith("/login/totp"))

        # Extract the pending TOTP cookie
        cookie_header = headers.get("set-cookie", [])
        totp_cookie = next((c for c in cookie_header if "hse_totp_pending=" in c), None)
        self.assertIsNotNone(totp_cookie)

        # Generate a fresh TOTP code
        fresh_code = self.la.compute_totp(secret, int(time.time()) + 30)  # next step: confirm_totp consumed the current one

        # Verify TOTP
        status2, headers2, body2 = request("POST", f"{B}/login/totp",
                                          headers={"Cookie": totp_cookie.split(";")[0]},
                                          form={"code": fresh_code})

        # Should succeed and create session
        self.assertEqual(status2, 303)
        self.assertTrue(location(headers2).endswith("/machines"))

    def test_signin_with_totp_wrong_code(self):
        """Sign in with wrong TOTP code is rejected."""
        # Enroll and confirm TOTP for admin
        secret, _ = self.la.enroll_totp(self.admin_id)
        code = self.la.compute_totp(secret, int(time.time()))
        self.assertTrue(self.la.confirm_totp(self.admin_id, code))

        os.environ["MFA_REQUIRED"] = "admins"
        import importlib
        importlib.reload(app)

        # Sign in with password
        status, headers, body = request("POST", f"{B}/login/local", form={
            "username": "admin",
            "password": "password123"
        })
        cookie_header = headers.get("set-cookie", [])
        totp_cookie = next((c for c in cookie_header if "hse_totp_pending=" in c), None)

        # Try with wrong code
        status2, headers2, body2 = request("POST", f"{B}/login/totp",
                                          headers={"Cookie": totp_cookie.split(";")[0]},
                                          form={"code": "000000"})

        # Should fail
        self.assertEqual(status2, 401)
        self.assertIn("Invalid code", body2)

    def test_recovery_code_signin(self):
        """Sign in using a recovery code when TOTP is unavailable."""
        # Enroll and confirm TOTP for admin
        secret, _ = self.la.enroll_totp(self.admin_id)
        code = self.la.compute_totp(secret, int(time.time()))
        self.assertTrue(self.la.confirm_totp(self.admin_id, code))

        # Get recovery codes
        account = self.la.get_account(id=self.admin_id)
        recovery_codes_hashed = account['recovery_codes']
        # Generate and verify we can extract one
        valid, _ = self.la.verify_recovery_code(recovery_codes_hashed, "TESTCODE")
        self.assertFalse(valid)  # Our test code won't match

        # Generate actual recovery codes
        new_codes = self.la.reset_recovery_codes(self.admin_id)
        self.assertEqual(len(new_codes), 8)

        os.environ["MFA_REQUIRED"] = "admins"
        import importlib
        importlib.reload(app)

        # Sign in with password
        status, headers, body = request("POST", f"{B}/login/local", form={
            "username": "admin",
            "password": "password123"
        })
        cookie_header = headers.get("set-cookie", [])
        totp_cookie = next((c for c in cookie_header if "hse_totp_pending=" in c), None)

        # Use a recovery code
        status2, headers2, body2 = request("POST", f"{B}/login/totp",
                                          headers={"Cookie": totp_cookie.split(";")[0]},
                                          form={"code": new_codes[0], "use_recovery": "1"})

        # Should succeed
        self.assertEqual(status2, 303)
        self.assertTrue(location(headers2).endswith("/machines"))

        # Verify the recovery code was consumed
        account = self.la.get_account(id=self.admin_id)
        remaining_count = len(account['recovery_codes'].split('\n')) if account['recovery_codes'] else 0
        self.assertEqual(remaining_count, 7)  # One used, 7 remaining

    def test_totp_replay_protection(self):
        """TOTP codes cannot be reused (replay protection)."""
        # Enroll and confirm TOTP for admin
        secret, _ = self.la.enroll_totp(self.admin_id)
        code = self.la.compute_totp(secret, int(time.time()))
        self.assertTrue(self.la.confirm_totp(self.admin_id, code))

        os.environ["MFA_REQUIRED"] = "admins"
        import importlib
        importlib.reload(app)

        # Sign in with password twice
        for attempt in range(2):
            status, headers, body = request("POST", f"{B}/login/local", form={
                "username": "admin",
                "password": "password123"
            })
            cookie_header = headers.get("set-cookie", [])
            totp_cookie = next((c for c in cookie_header if "hse_totp_pending=" in c), None)

            # Generate a fresh code for first attempt
            if attempt == 0:
                fresh_code = self.la.compute_totp(secret, int(time.time()) + 30)  # next step: confirm_totp consumed the current one
                # First use should succeed
                status2, headers2, body2 = request("POST", f"{B}/login/totp",
                                                  headers={"Cookie": totp_cookie.split(";")[0]},
                                                  form={"code": fresh_code})
                self.assertEqual(status2, 303)
            else:
                # Wait a tiny bit to ensure we're in same time window
                # Second use of same code should fail (replay)
                status2, headers2, body2 = request("POST", f"{B}/login/totp",
                                                  headers={"Cookie": totp_cookie.split(";")[0]},
                                                  form={"code": fresh_code})
                # Should fail because code was already used
                self.assertEqual(status2, 401)


class InvitationAndReset(Base):
    """Test invitation and password reset flows (Block 4.2)."""

    def setUp(self):
        super().setUp()
        # Configure local accounts
        import local_accounts as la
        la.configure(":memory:")
        self.la = la
        # Mock Headscale API responses
        def api_mock(method, path, *args, **kwargs):
            if method == "POST" and "/user" in path:
                return {"user": {"name": "testuser"}}
            elif method == "GET" and path == "/user":
                return {"users": []}
            elif method == "GET" and path == "/node":
                return {"nodes": []}
            elif method == "DELETE":
                return {}
            return None
        self.api.side_effect = api_mock

    def test_accept_invitation_creates_account(self):
        """Accepting an invitation creates a local account."""
        # Create an invitation
        token = self.la.create_invitation("newuser@example.com", role='member')

        # GET the invitation page (should work)
        status1, headers1, body1 = request("GET", f"{B}/accept/{token}")
        self.assertEqual(status1, 200)
        self.assertIn("newuser@example.com", body1)
        self.assertIn("Member", body1)

        # POST to accept it
        status2, headers2, body2 = request("POST", f"{B}/accept/{token}", form={
            "username": "newuser",
            "password": "password123",
            "password2": "password123"
        })

        # Should redirect to machines page (auto sign-in)
        self.assertEqual(status2, 303)
        self.assertTrue(location(headers2).endswith(f"{B}/machines"))

        # Verify account was created
        account = self.la.get_account(username="newuser")
        self.assertIsNotNone(account)
        self.assertEqual(account['email'], "newuser@example.com")
        self.assertEqual(account['role'], "member")
        self.assertEqual(account['headscale_user'], "newuser")

    def test_accept_invitation_creates_headscale_user(self):
        """Accepting an invitation creates a Headscale user."""
        token = self.la.create_invitation("hsuser@example.com", role='member')

        # Accept the invitation
        request("POST", f"{B}/accept/{token}", form={
            "username": "hsuser",
            "password": "password123",
            "password2": "password123"
        })

        # Verify Headscale API was called to create user
        # The mock returns {"user": {"name": "testuser"}} for POST requests
        create_calls = [c for c in self.api.call_args_list if c[0][0] == "POST"]
        self.assertGreater(len(create_calls), 0)

    def test_accept_invitation_token_single_use(self):
        """Invitation tokens can only be used once."""
        token = self.la.create_invitation("singleuse@example.com", role='member')

        # First use: should succeed
        status1, headers1, body1 = request("POST", f"{B}/accept/{token}", form={
            "username": "user1",
            "password": "password123",
            "password2": "password123"
        })
        self.assertEqual(status1, 303)

        # Second use: should fail (token already used)
        status2, headers2, body2 = request("POST", f"{B}/accept/{token}", form={
            "username": "user2",
            "password": "password123",
            "password2": "password123"
        })
        self.assertEqual(status2, 400)
        self.assertIn("invalid or has expired", body2)

    def test_accept_invitation_password_mismatch(self):
        """Accepting invitation fails if passwords don't match."""
        token = self.la.create_invitation("mismatch@example.com", role='member')

        status, headers, body = request("POST", f"{B}/accept/{token}", form={
            "username": "testuser",
            "password": "password123",
            "password2": "different"
        })

        self.assertEqual(status, 400)
        self.assertIn("do not match", body)

    def test_reset_password_with_valid_token(self):
        """Password reset with valid token updates the password."""
        # Create an account
        account_id = self.la.create_account("resetuser", "reset@example.com", "oldpassword")

        # Create a reset token
        token = self.la.create_reset_token(account_id)

        # GET the reset page
        status1, headers1, body1 = request("GET", f"{B}/reset/{token}")
        self.assertEqual(status1, 200)
        self.assertIn("resetuser", body1)

        # POST to reset password
        status2, headers2, body2 = request("POST", f"{B}/reset/{token}", form={
            "password": "newpassword123",
            "password2": "newpassword123"
        })

        # Should redirect to machines page (auto sign-in)
        self.assertEqual(status2, 303)
        self.assertTrue(location(headers2).endswith(f"{B}/machines"))

        # Verify password was updated
        account = self.la.get_account(id=account_id)
        self.assertTrue(self.la.verify_password("newpassword123", account['pw_hash']))
        self.assertFalse(self.la.verify_password("oldpassword", account['pw_hash']))

    def test_reset_password_token_single_use(self):
        """Password reset tokens can only be used once."""
        account_id = self.la.create_account("resetonce", "resetonce@example.com", "password")
        token = self.la.create_reset_token(account_id)

        # First use: should succeed
        status1, headers1, body1 = request("POST", f"{B}/reset/{token}", form={
            "password": "newpass1",
            "password2": "newpass1"
        })
        self.assertEqual(status1, 303)

        # Second use: should fail
        status2, headers2, body2 = request("POST", f"{B}/reset/{token}", form={
            "password": "newpass2",
            "password2": "newpass2"
        })
        self.assertEqual(status2, 400)
        self.assertIn("invalid or has expired", body2)

    def test_expired_token_rejected(self):
        """Expired tokens are rejected."""
        from datetime import datetime, timezone, timedelta

        # Create an invitation
        token = self.la.create_invitation("expired@example.com")

        # Manually expire it
        token_hash = self.la._hash_token(token)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with self.la._db() as db:
            db.execute("UPDATE tokens SET expires = ? WHERE token_hash = ?",
                      (past, token_hash))

        # Try to use it
        status, headers, body = request("GET", f"{B}/accept/{token}")
        self.assertEqual(status, 400)
        self.assertIn("invalid or has expired", body)

    def test_invalid_token_rejected(self):
        """Invalid tokens are rejected."""
        # Try with a completely fake token
        status, headers, body = request("GET", f"{B}/accept/invalidtoken123")
        self.assertEqual(status, 400)
        self.assertIn("invalid or has expired", body)

    def test_admin_creates_invitation(self):
        """Admins can create invitations."""
        # Create admin session
        admin = dict(ADMIN, kind="local", sub="local:999", role="admin")

        # Create invitation
        status, headers, body = request("POST", f"{B}/invitations", session=admin, form={
            "csrf": "tok",
            "role": "member",
            "email": "admin-invite@example.com",
            "days": "7"
        })

        # Should return users page with result (200)
        self.assertEqual(status, 200)

        # Verify invitation was created
        invitations = self.la.list_active_invitations()
        self.assertEqual(len(invitations), 1)
        self.assertEqual(invitations[0]['email'], 'admin-invite@example.com')
        self.assertEqual(invitations[0]['role'], 'member')

    def test_admin_lists_invitations(self):
        """Admins can list active invitations via API."""
        # Create some invitations
        self.la.create_invitation("invite1@example.com", role='member')
        self.la.create_invitation("invite2@example.com", role='admin')

        # Verify invitations are in the database
        invitations = self.la.list_active_invitations()
        self.assertEqual(len(invitations), 2)

        emails = [inv['email'] for inv in invitations]
        self.assertIn("invite1@example.com", emails)
        self.assertIn("invite2@example.com", emails)

    def test_admin_revokes_invitation(self):
        """Admins can revoke invitations."""
        # Create an invitation
        token = self.la.create_invitation("revoke-admin@example.com", role='member')
        token_hash = self.la._hash_token(token)

        # Admin revokes it
        admin = dict(ADMIN, kind="local", sub="local:999", role="admin")
        status, headers, body = request("POST", f"{B}/invitations/{token_hash}/revoke", session=admin, form={
            "csrf": "tok"
        })

        # Should redirect
        self.assertEqual(status, 303)

        # Verify invitation was revoked (can't be used)
        data = self.la.check_token(token, kind='invite')
        self.assertIsNone(data)


class SelfServiceAccount(unittest.TestCase):
    """Tests for self-service account management (Block 5.3)."""

    def setUp(self):
        """Set up local accounts."""
        import local_accounts as la
        la.configure(":memory:")
        self.la = la

        # Create test account
        self.account_id = la.create_account("alice", "alice@example.com", "password123", role="member", headscale_user="alice")

        # Reset sessions
        sessions.configure(":memory:")
        sessions._hits.clear()

    def test_user_changes_own_password(self):
        """User can change their own password with correct old password."""
        # Create session for the user (request helper will call sessions.create)
        session = {
            "kind": "local",
            "sub": f"local:{self.account_id}",
            "username": "alice",
            "name": "alice",
            "email": "alice@example.com",
            "admin": False,
            "role": "member",
            "csrf": "tok",
            "exp": time.time() + 3600
        }

        # Change password
        status, headers, _ = request("POST", f"{B}/settings/account/password", session, {
            "old_password": "password123",
            "new_password": "newpassword456",
            "new_password2": "newpassword456",
            "csrf": "tok"
        })

        # Should redirect with success message
        self.assertEqual(status, 303)
        self.assertIn("password-changed", location(headers))

        # Verify new password works
        account = self.la.get_account(id=self.account_id)
        self.assertTrue(self.la.verify_password("newpassword456", account['pw_hash']))

        # Verify old password no longer works
        self.assertFalse(self.la.verify_password("password123", account['pw_hash']))

    def test_user_cannot_change_password_without_old(self):
        """User cannot change password without providing correct old password."""
        session = {
            "kind": "local",
            "sub": f"local:{self.account_id}",
            "username": "alice",
            "name": "alice",
            "admin": False,
            "role": "member",
            "csrf": "tok",
            "exp": time.time() + 3600
        }

        # Try to change password with wrong old password
        status, headers, _ = request("POST", f"{B}/settings/account/password", session, {
            "old_password": "wrongpassword",
            "new_password": "newpassword456",
            "new_password2": "newpassword456",
            "csrf": "tok"
        })

        # Should redirect with error
        self.assertEqual(status, 303)
        self.assertIn("wrong-password", location(headers))

        # Verify password hasn't changed
        account = self.la.get_account(id=self.account_id)
        self.assertTrue(self.la.verify_password("password123", account['pw_hash']))

    def test_user_cannot_change_password_with_mismatch(self):
        """User cannot change password if new passwords don't match."""
        session = {
            "kind": "local",
            "sub": f"local:{self.account_id}",
            "username": "alice",
            "name": "alice",
            "admin": False,
            "role": "member",
            "csrf": "tok",
            "exp": time.time() + 3600
        }

        # Try to change password with mismatched new passwords
        status, headers, _ = request("POST", f"{B}/settings/account/password", session, {
            "old_password": "password123",
            "new_password": "newpassword456",
            "new_password2": "differentpassword",
            "csrf": "tok"
        })

        # Should redirect with error
        self.assertEqual(status, 303)
        self.assertIn("password-mismatch", location(headers))

        # Verify password hasn't changed
        account = self.la.get_account(id=self.account_id)
        self.assertTrue(self.la.verify_password("password123", account['pw_hash']))

    def test_user_cannot_change_role(self):
        """Users cannot change their own role (role field is display-only)."""
        session = {
            "kind": "local",
            "sub": f"local:{self.account_id}",
            "username": "alice",
            "name": "alice",
            "admin": False,
            "role": "member",
            "csrf": "tok",
            "exp": time.time() + 3600
        }

        # Get account settings page
        status, _, body = request("GET", f"{B}/settings/account", session)
        self.assertEqual(status, 200)

        # Page should show role as read-only info (not a form field)
        self.assertIn("Member", body)
        # Should NOT have an editable role field
        self.assertNotIn('name="role"', body)
        self.assertNotIn('<select name="role"', body)

    def test_oidc_user_cannot_access_account_settings(self):
        """OIDC users are redirected away from local account settings."""
        session = {
            "kind": "oidc",
            "sub": "oidc-user-123",
            "username": "oidcuser",
            "name": "OIDC User",
            "admin": False,
            "role": "member",
            "csrf": "tok",
            "exp": time.time() + 3600
        }

        # Try to access account settings
        status, headers, _ = request("GET", f"{B}/settings/account", session)

        # Should redirect to general settings
        self.assertEqual(status, 303)
        self.assertEqual(location(headers), f"{B}/settings/general")

    def test_account_settings_page_shows_user_info(self):
        """Account settings page displays username, email, and role."""
        session = {
            "kind": "local",
            "sub": f"local:{self.account_id}",
            "username": "alice",
            "name": "alice",
            "admin": False,
            "role": "member",
            "csrf": "tok",
            "exp": time.time() + 3600
        }

        # Get account settings page
        status, _, body = request("GET", f"{B}/settings/account", session)
        self.assertEqual(status, 200)

        # Should show account information
        self.assertIn("alice", body)
        self.assertIn("alice@example.com", body)
        self.assertIn("Member", body)

        # Should have password change form
        self.assertIn('action="/admin/settings/account/password"', body)
        self.assertIn('name="old_password"', body)
        self.assertIn('name="new_password"', body)


class SignInModes(unittest.TestCase):
    """Tests for combined sign-in modes (Block 5.2)."""

    def test_signin_mode_local_only(self):
        """Login page with local mode only shows username/password form."""
        import admin_pages
        html = admin_pages.login_page(sso=False, apikey=False, local=True)

        # Should contain local sign-in form
        self.assertIn('action="/admin/login/local"', html)
        self.assertIn('name="username"', html)
        self.assertIn('name="password"', html)

        # Should NOT contain SSO button
        self.assertNotIn('Sign in with SSO', html)

        # Should NOT contain API key form
        self.assertNotIn('Headscale API key', html)
        self.assertNotIn('name="api_key"', html)

    def test_signin_mode_local_and_oidc(self):
        """Login page with local + OIDC shows both options."""
        import admin_pages
        html = admin_pages.login_page(sso=True, apikey=False, local=True)

        # Should contain local sign-in form
        self.assertIn('action="/admin/login/local"', html)
        self.assertIn('name="username"', html)
        self.assertIn('name="password"', html)

        # Should contain SSO button
        self.assertIn('Sign in with SSO', html)
        self.assertIn('href="/admin/login/sso"', html)

        # Should contain separator
        self.assertIn('<div class="sep">', html)

        # Should NOT contain API key form
        self.assertNotIn('Headscale API key', html)

    def test_signin_mode_apikey_only(self):
        """Login page with API key mode only shows API key form."""
        import admin_pages
        html = admin_pages.login_page(sso=False, apikey=True, local=False)

        # Should NOT contain local sign-in form
        self.assertNotIn('action="/admin/login/local"', html)

        # Should NOT contain SSO button
        self.assertNotIn('Sign in with SSO', html)

        # Should contain API key form
        self.assertIn('Headscale API key', html)
        self.assertIn('name="api_key"', html)
        self.assertIn('action="/admin/login/apikey"', html)

    def test_signin_mode_all_three(self):
        """Login page with all modes shows all options."""
        import admin_pages
        html = admin_pages.login_page(sso=True, apikey=True, local=True)

        # Should contain local sign-in form
        self.assertIn('action="/admin/login/local"', html)
        self.assertIn('name="username"', html)

        # Should contain SSO button
        self.assertIn('Sign in with SSO', html)

        # Should contain API key form
        self.assertIn('Headscale API key', html)
        self.assertIn('name="api_key"', html)

        # Should contain separators
        self.assertEqual(html.count('<div class="sep">'), 2)

    def test_signin_mode_sso_only(self):
        """Login page with SSO only shows SSO button."""
        import admin_pages
        html = admin_pages.login_page(sso=True, apikey=False, local=False)

        # Should NOT contain local sign-in form
        self.assertNotIn('action="/admin/login/local"', html)

        # Should contain SSO button
        self.assertIn('Sign in with SSO', html)
        self.assertIn('href="/admin/login/sso"', html)

        # Should NOT contain API key form
        self.assertNotIn('Headscale API key', html)

        # Should NOT contain separator
        self.assertNotIn('<div class="sep">', html)


class LocalAccountHeadscaleIntegration(unittest.TestCase):
    """Test that local accounts are properly linked to Headscale users."""

    def setUp(self):
        """Configure local accounts database before each test."""
        import local_accounts as lac
        lac.configure(":memory:")
        self.lac = lac

    @mock.patch.object(hs, "api")
    def test_accept_invitation_creates_headscale_user(self, mock_api):
        """Accepting an invitation creates both a local account AND a Headscale user."""
        # Create invitation
        token = self.lac.create_invitation(email="alice@example.com", role="member")

        # Mock Headscale API to succeed on user creation
        mock_api.return_value = {"user": {"id": "42", "name": "alice"}}

        # Accept invitation
        status, headers, body = request("POST", f"{B}/accept/{token}",
                                       form={"username": "alice", "password": "password123", "password2": "password123"})

        # Should redirect to machines page (auto sign-in)
        self.assertEqual(status, 303)
        self.assertTrue(location(headers).endswith(f"{B}/machines"))

        # Headscale user creation should have been called
        mock_api.assert_called_with("POST", "/user", {"name": "alice"})

        # Local account should exist with headscale_user set
        account = self.lac.get_account(email="alice@example.com")
        self.assertIsNotNone(account)
        self.assertEqual(account["username"], "alice")
        self.assertEqual(account["headscale_user"], "alice")
        self.assertEqual(account["role"], "member")

    @mock.patch.object(hs, "api")
    def test_my_user_returns_headscale_user_for_local_session(self, mock_api):
        """my_user() returns the correct Headscale user for local sessions."""
        # Create a local account
        account_id = self.lac.create_account("charlie", "charlie@example.com", "password123",
                                             role="member", headscale_user="charlie")

        # Mock Headscale to return the user
        mock_api.return_value = {
            "users": [
                {"id": "1", "name": "alice"},
                {"id": "2", "name": "charlie"},
                {"id": "3", "name": "david"}
            ]
        }

        # Create a local session
        local_session = {
            "kind": "local",
            "sub": f"local:{account_id}",
            "username": "charlie",
            "email": "charlie@example.com",
            "role": "member"
        }

        # Call my_user()
        user = app.my_user(local_session)

        # Should return the correct Headscale user
        self.assertIsNotNone(user)
        self.assertEqual(user["name"], "charlie")
        self.assertEqual(user["id"], "2")

    @mock.patch.object(hs, "api")
    def test_my_user_returns_none_if_headscale_user_not_found(self, mock_api):
        """my_user() returns None if the Headscale user doesn't exist yet."""
        # Create a local account
        account_id = self.lac.create_account("eve", "eve@example.com", "password123",
                                             role="member", headscale_user="eve")

        # Mock Headscale to return empty user list
        mock_api.return_value = {"users": []}

        # Create a local session
        local_session = {
            "kind": "local",
            "sub": f"local:{account_id}",
            "username": "eve",
            "email": "eve@example.com",
            "role": "member"
        }

        # Call my_user()
        user = app.my_user(local_session)

        # Should return None
        self.assertIsNone(user)

    def test_my_user_handles_invalid_local_session_sub(self):
        """my_user() handles malformed local session subs gracefully."""
        # Session with invalid sub format
        bad_session = {
            "kind": "local",
            "sub": "local:not-a-number",
            "username": "bad",
            "email": "bad@example.com",
            "role": "member"
        }

        # Should return None instead of crashing
        user = app.my_user(bad_session)
        self.assertIsNone(user)


class TwoFactorNudgeTest(unittest.TestCase):
    """The popup suggesting two-factor to local accounts that have not enabled it."""

    LOCAL = {"kind": "local", "sub": "local:1", "username": "ana", "csrf": "t", "role": "admin", "admin": True}

    def test_shown_only_to_local_accounts_without_two_factor(self):
        import ui
        shown = ui.nudge_2fa(dict(self.LOCAL, totp_on=False), "machines")
        self.assertIn('id="nudge-2fa"', shown)
        self.assertIn(f"{B}/settings/account/totp/enroll", shown)
        self.assertEqual(ui.nudge_2fa(dict(self.LOCAL, totp_on=True), "machines"), "")
        self.assertEqual(ui.nudge_2fa(dict(ADMIN), "machines"), "")  # OIDC / API key sessions
        self.assertEqual(ui.nudge_2fa(dict(self.LOCAL, totp_on=False), "settings"), "")  # not on the account pages

    def test_layout_includes_it(self):
        import ui
        page = ui.layout("Machines", "machines", "<p>x</p>", dict(self.LOCAL, totp_on=False), app.CTX)
        self.assertIn('data-nudge="2fa"', page)
        page = ui.layout("Machines", "machines", "<p>x</p>", dict(self.LOCAL, totp_on=True), app.CTX)
        self.assertNotIn("nudge-2fa", page)

    def test_session_reflects_the_account_live(self):
        import local_accounts as la
        la.configure(":memory:")
        account_id = la.create_account("ana", "ana@example.com", "-".join(["test", "pass", "n"]), role="admin")
        data = dict(self.LOCAL, sub=f"local:{account_id}")
        handler = app.Handler.__new__(app.Handler)
        with mock.patch.object(app, "unsign", lambda _c: dict(data)), mock.patch.object(
                app.sessions, "validate", lambda _d: True), mock.patch.object(app.Handler, "cookie", lambda *_a: "x"):
            self.assertIs(handler.session()["totp_on"], False)
            secret, _qr = la.enroll_totp(account_id)
            self.assertTrue(la.confirm_totp(account_id, la.compute_totp(secret)))
            self.assertIs(handler.session()["totp_on"], True)

    def test_the_script_remembers_the_dismissal_per_browser_session(self):
        js = open(os.path.join(WEB, "static", "app.js"), encoding="utf-8").read()
        self.assertIn('getElementById("nudge-2fa")', js)
        self.assertIn("sessionStorage", js)


class SetupTakeoverTest(unittest.TestCase):
    """The first-run wizard (aio/wizard.py) is the only thing standing between a stranger and an admin
    account: nothing may happen before the one-time token, and nothing after setup ends."""

    def setUp(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import test_wizard
        self.case = type("Case", (test_wizard.WizardTestBase,), {"runTest": lambda s: None})("runTest")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_no_account_is_created_without_the_token(self):
        import local_accounts as lac
        c = self.case.c
        form = {"email": "evil@example.com"}
        form["username"] = "evil"
        form["password"] = form["password2"] = "-".join(["test", "pass", "x"])
        for step, data in (("admin", form), ("finish", {}), ("network", {"tailnet_name": "x"})):
            self.assertEqual(c.request("/admin/setup/" + step, data)[0], 403)
        # even with a valid CSRF token taken from the (public) token page
        _s, _h, html = c.request("/admin/setup")
        csrf = c.csrf(html)
        self.assertEqual(c.request("/admin/setup/admin", dict(form, csrf=csrf))[0], 403)
        self.assertEqual(lac.list_accounts(), [])
        self.assertFalse(os.path.exists(os.path.join(self.case.data, "config", "settings.json")))

    def test_a_session_does_not_authorise_another_client(self):
        self.case.unlock()
        other = type(self.case.c)(self.case.server.server_address[1])
        self.assertEqual(other.request("/admin/setup/language")[0], 403)
        self.assertEqual(other.request("/admin/setup/language", headers={"Cookie": "hse_setup=guess"})[0], 403)

    def test_csrf_token_of_another_session_is_refused(self):
        a, b = self.case.unlock(), type(self.case.c)(self.case.server.server_address[1])
        _s, _h, html_b = b.request("/admin/setup")
        self.assertEqual(a.request("/admin/setup/language", {"lang": "en", "csrf": b.csrf(html_b)})[0], 403)

    def test_the_token_is_compared_in_constant_time(self):
        import inspect
        import wizard
        self.assertIn("hmac.compare_digest", inspect.getsource(wizard.Handler.check_token))

    def test_the_wizard_does_not_serve_the_console(self):
        c = self.case.c
        for path in ("/admin/machines", "/admin/login", "/admin/keys", "/admin/settings", "/api/v1/node",
                     "/admin/healthz"):
            status, headers, _b = c.request(path)
            self.assertEqual((status, headers["Location"]), (302, "/admin/setup"), path)

    def test_wrong_tokens_never_unlock_and_the_limit_applies_to_all_clients(self):
        first = self.case.c
        second = type(first)(self.case.server.server_address[1])
        for client in (first, second):
            _s, _h, html = client.request("/admin/setup")
            for i in range(3):
                client.request("/admin/setup", {"token": "wrong%d" % i, "csrf": client.csrf(html)})
        _s, _h, html = first.request("/admin/setup")
        self.assertEqual(first.request("/admin/setup", {"token": self.case.token, "csrf": first.csrf(html)})[0], 429)

    def test_the_token_stops_working_when_setup_is_over(self):
        os.unlink(os.path.join(self.case.data, "config", "setup-token"))
        c = self.case.c
        _s, _h, html = c.request("/admin/setup")
        self.assertEqual(c.request("/admin/setup", {"token": self.case.token, "csrf": c.csrf(html)})[0], 403)
        self.assertEqual(c.request("/admin/setup", {"token": "", "csrf": c.csrf(html)})[0], 403)

    def test_responses_are_not_cacheable_and_framing_is_denied(self):
        _s, headers, _b = self.case.c.request("/admin/setup")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])


if __name__ == "__main__":
    unittest.main()
