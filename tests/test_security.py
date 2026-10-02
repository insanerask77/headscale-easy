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
            headers: dict | None = None) -> tuple[int, dict, str]:
    """Run one request through app.Handler; (status, headers, body)."""
    body = urllib.parse.urlencode(form or {}, doseq=True).encode()
    msg = email.message.Message()
    if session is not None and "sid" not in session:
        session = dict(session, sid=sessions.create(session))  # a live server-side session
    if session is not None:
        msg["Cookie"] = f"hse_session={app.sign(session)}"
    msg["Content-Length"] = str(len(body))
    for k, v in (headers or {}).items():
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
        for path in ("/keys", "/users", "/acl", "/dns", "/apikeys", "/machines/1/delete"):
            status, headers, _ = request("POST", B + path, form={"csrf": "tok"})
            self.assertEqual((status, location(headers)), (303, f"{B}/login"), path)
        self.api.assert_not_called()

    def test_forged_session_cookie(self):
        forged = base64.urlsafe_b64encode(json.dumps(ADMIN).encode()).decode() + ".0"
        status, headers, _ = request("GET", f"{B}/users", headers={"Cookie": f"hse_session={forged}"})
        self.assertEqual((status, location(headers)), (303, f"{B}/login"))


class Csrf(Base):
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
        self.assertEqual(self.api.call_args.args[2]["user"], "2")

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
                            (nxt[:-1] + "0", f"{B}/machines")):
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


if __name__ == "__main__":
    unittest.main()
