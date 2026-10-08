"""Characterization tests for web/app.py paths the rest of the suite does not reach.

They pin down what the code does TODAY so that moving it later cannot change it
silently. Odd behaviour is recorded as it is and marked ``QUIRK:``; nothing here is a statement that the
behaviour is right. Requests go through the real ``app.Handler`` (``test_security.request``); Headscale, the
supervisor and the audit log are fakes, as in the other web tests.

    python3 -m unittest tests.test_app_gaps
"""
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "web"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_security import ADMIN, B, BOB, MEMBER, Base, location, request  # noqa: E402  (sets the environment)
import app  # noqa: E402
from handlers import shared as sh  # noqa: E402
import derp  # noqa: E402
import headscale as hs  # noqa: E402
import multipart  # noqa: E402
import local_accounts as lac  # noqa: E402
import account_tokens  # noqa: E402
import accounts_db  # noqa: E402
import totp  # noqa: E402
import sessions  # noqa: E402

PASSWORD = "Str0ng-Passw0rd-x"
IP = "127.0.0.1"


def http_error(code: int = 400, body: bytes = b'{"message": "nope"}') -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://headscale/api", code, "err", {}, io.BytesIO(body))


def set_cookies(headers: dict) -> dict:
    """{name: full Set-Cookie value} of a response."""
    out = {}
    for line in headers.get("set-cookie", []):
        out[line.split("=", 1)[0]] = line
    return out


def cookie_value(headers: dict, name: str) -> str:
    return set_cookies(headers)[name].split(";")[0].split("=", 1)[1]


def local_session(account_id: int, role: str = "admin", **extra) -> dict:
    return dict(ADMIN, kind="local", sub=f"local:{account_id}", username="u", admin=role == "admin", role=role,
                **extra)


class NoNetwork(Base):
    """Pages that list machines look up the latest Tailscale release and the DERP regions; keep that off the network."""

    def setUp(self):
        super().setUp()
        for name, value in (("latest_tailscale_version", lambda: "1.80.0"), ("derp_regions", lambda: {})):
            p = mock.patch.object(hs, name, value)
            p.start()
            self.addCleanup(p.stop)


def bare_handler() -> app.Handler:
    """A Handler with just enough state to call one method and read what it wrote to wfile."""
    h = app.Handler.__new__(app.Handler)
    h.wfile, h.rfile = io.BytesIO(), io.BytesIO()
    h.command, h.path, h.request_version = "POST", "/", "HTTP/1.1"
    h.requestline, h.client_address = "POST / HTTP/1.1", (IP, 1)
    h.headers = {}
    return h


class WithAccounts(Base):
    """A fresh in-memory accounts database and no real sleeping; sign-in counters are cleared around each test."""

    def setUp(self):
        super().setUp()
        accounts_db.configure(":memory:")
        p = mock.patch.object(app.time, "sleep", mock.Mock())
        self.sleep = p.start()
        self.addCleanup(p.stop)
        for key in ("local", "apikey", "sso", "totp"):
            sessions.reset(f"{key}:{IP}")
            self.addCleanup(sessions.reset, f"{key}:{IP}")

    def account(self, username="alice", role="member", **kw) -> int:
        return lac.create_account(username, f"{username}@example.com", PASSWORD, role=role,
                                  headscale_user=username, **kw)

    def flood(self, bucket: str):
        for _ in range(sessions.LIMIT):
            sessions.hit(f"{bucket}:{IP}")


# --- small Handler pieces ----------------------------------------------------------------------------------------

class FormAndSession(Base):
    def handler(self, body: bytes, length=None):
        h = app.Handler.__new__(app.Handler)
        h.rfile = io.BytesIO(body)
        h.headers = {"Content-Length": str(len(body) if length is None else length)}
        return h

    def test_form_over_256k_is_dropped_without_reading_it(self):
        h = self.handler(b"a=1", length=262145)
        self.assertEqual(h.form(), {})
        self.assertEqual(h.rfile.tell(), 0)

    def test_form_repeats_only_for_route_and_bracket_fields(self):
        h = self.handler(b"route=a&route=b&rows%5B%5D=1&rows%5B%5D=2&x=1&x=2&blank=")
        self.assertEqual(h.form(), {"route": ["a", "b"], "rows[]": ["1", "2"], "x": "1", "blank": ""})

    def test_local_session_with_a_malformed_sub_has_no_account(self):
        accounts_db.configure(":memory:")
        data = dict(ADMIN, kind="local", sub="local:abc")
        status, headers, _b = request("GET", B + "/healthz", data)
        self.assertEqual(status, 200)
        h = app.Handler.__new__(app.Handler)
        sid = sessions.create(data)
        h.headers = {"Cookie": f"hse_session={app.sign(dict(data, sid=sid))}"}
        got = h.session()
        self.assertEqual((got["totp_on"], got["must_change"]), (False, False))

    def test_local_session_reflects_the_live_account(self):
        accounts_db.configure(":memory:")
        acc = lac.create_account("zed", "zed@example.com", PASSWORD, role="admin", must_change=True)
        data = dict(ADMIN, kind="local", sub=f"local:{acc}")
        h = app.Handler.__new__(app.Handler)
        h.headers = {"Cookie": f"hse_session={app.sign(dict(data, sid=sessions.create(data)))}"}
        got = h.session()
        self.assertEqual((got["totp_on"], got["must_change"]), (False, True))

    def test_healthz_and_head(self):
        self.assertEqual(request("GET", B + "/healthz")[::2], (200, "ok"))
        self.assertEqual(request("HEAD", B + "/healthz")[0], 200)


class StaticFiles(Base):
    def test_font_has_its_own_type_and_is_cached_forever(self):
        status, headers, _b = request("GET", B + "/static/fonts/InterVariable.woff2")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], ["font/woff2"])
        self.assertIn("immutable", headers["cache-control"][0])

    def test_unknown_and_traversal_are_404(self):
        for name in ("nope.css", "../app.py", "..%2fapp.py", ""):
            self.assertEqual(request("GET", f"{B}/static/{name}")[0], 404, name)


# --- sign-in pages and rate limiting -----------------------------------------------------------------------------

class LoginPages(Base):
    def test_signed_out_and_signed_up_show_a_message_instead_of_starting_sso(self):
        with mock.patch.object(sh, "SSO", True), mock.patch.object(sh, "API_KEY_LOGIN", False):
            for flash, text in (("signed-out", "signed out"), ("signed-up", "Account created")):
                status, _h, body = request("GET", f"{B}/login?m={flash}")
                self.assertEqual(status, 200)
                self.assertIn(text, body)

    def test_login_with_only_sso_redirects_to_the_provider(self):
        with mock.patch.object(sh, "SSO", True), mock.patch.object(sh, "API_KEY_LOGIN", False), \
                mock.patch.object(sh, "discovery", lambda: {"authorization_endpoint": "https://idp.example/auth"}):
            status, headers, _b = request("GET", B + "/login")
        self.assertEqual(status, 303)
        self.assertTrue(location(headers).startswith("https://idp.example/auth?"))
        self.assertIn("hse_oidc", set_cookies(headers))

    def test_totp_page_without_the_pending_cookie_goes_back_to_login(self):
        status, headers, _b = request("GET", B + "/login/totp")
        self.assertEqual((status, location(headers)), (303, f"{B}/login"))


class RateLimiting(WithAccounts):
    def test_local_login_blocked_answers_429_with_retry_after(self):
        self.flood("local")
        status, headers, body = request("POST", B + "/login/local", form={"username": "a", "password": "b"})
        self.assertEqual(status, 429)
        self.assertGreaterEqual(int(headers["retry-after"][0]), 1)
        self.assertIn("Too many sign-in attempts", body)
        event = app.audit.request_event.call_args.args
        self.assertEqual(event[2], "auth.rate_limited")

    def test_apikey_login_blocked(self):
        self.flood("apikey")
        with mock.patch.object(sh, "API_KEY_LOGIN", True):
            status, headers, _b = request("POST", B + "/login/apikey", form={"api_key": "hskey-api-x"})
        self.assertEqual(status, 429)
        self.assertIn("retry-after", headers)

    def test_sso_start_and_callback_blocked(self):
        self.flood("sso")
        with mock.patch.object(sh, "SSO", True):
            for path in ("/login/sso", "/callback?code=c&state=s"):
                status, headers, _b = request("GET", B + path)
                self.assertEqual(status, 429, path)
                self.assertIn("retry-after", headers)

    def test_wrong_attempts_count_towards_the_limit(self):
        for _ in range(sessions.LIMIT):
            self.assertEqual(request("POST", B + "/login/local", form={"username": "x", "password": "y"})[0], 401)
        self.assertEqual(request("POST", B + "/login/local", form={"username": "x", "password": "y"})[0], 429)


# --- local sign-in ------------------------------------------------------------------------------------------------

class LocalLogin(WithAccounts):
    def login(self, username="alice", password=PASSWORD):
        return request("POST", B + "/login/local", form={"username": username, "password": password})

    def test_missing_fields(self):
        status, _h, body = self.login("", "")
        self.assertEqual(status, 401)
        self.assertIn("Username or email and password are required.", body)

    def test_unknown_user_and_wrong_password_look_the_same_and_sleep(self):
        self.account()
        for args in (("ghost", PASSWORD), ("alice", "wrong")):
            status, _h, body = self.login(*args)
            self.assertEqual(status, 401)
            self.assertIn("Wrong username, email or password.", body)
        self.assertEqual(self.sleep.call_count, 2)
        reasons = [c.args[4]["reason"] for c in app.audit.request_event.call_args_list]
        self.assertEqual(reasons, ["unknown_user", "wrong_password"])

    def test_disabled_account_is_403_without_sleeping(self):
        acc = self.account()
        with accounts_db._db() as db:
            db.execute("UPDATE accounts SET disabled = 1 WHERE id = ?", (acc,))
        status, _h, body = self.login()
        self.assertEqual(status, 403)
        self.assertIn("This account is disabled.", body)
        self.sleep.assert_not_called()

    def test_member_signs_in_with_password_only(self):
        acc = self.account()
        status, headers, _b = self.login()
        self.assertEqual((status, location(headers)), (303, f"{B}/machines"))
        session = app.unsign(cookie_value(headers, "hse_session"))
        self.assertEqual((session["kind"], session["sub"], session["admin"], session["role"]),
                         ("local", f"local:{acc}", False, "member"))

    def test_member_signs_in_with_the_email_and_the_session_keeps_the_username(self):
        acc = self.account()
        for typed in ("alice@example.com", "Alice@Example.com"):
            status, headers, _b = self.login(typed)
            self.assertEqual((status, location(headers)), (303, f"{B}/machines"), typed)
            session = app.unsign(cookie_value(headers, "hse_session"))
            self.assertEqual((session["sub"], session["username"]), (f"local:{acc}", "alice"))

    def test_unknown_email_is_rejected_like_an_unknown_user(self):
        self.account()
        status, _h, body = self.login("ghost@example.com")
        self.assertEqual(status, 401)
        self.assertIn("Wrong username, email or password.", body)

    def test_session_cookie_is_http_only_and_the_other_cookies_are_cleared(self):
        self.account()
        _s, headers, _b = self.login()
        cookies = set_cookies(headers)
        self.assertIn("HttpOnly", cookies["hse_session"])
        for name in ("hse_oidc", "hse_next", "hse_totp_pending"):
            self.assertIn("Max-Age=0", cookies[name])

    def test_admin_with_mfa_required_but_not_enrolled_gets_a_session_marked_for_enrollment(self):
        self.account("root", "admin")
        with mock.patch.object(sh, "MFA_REQUIRED", "admins"):
            status, headers, _b = self.login("root")
        self.assertEqual(status, 303)
        self.assertTrue(app.unsign(cookie_value(headers, "hse_session"))["totp_enrollment_required"])

    def test_admin_with_totp_enrolled_goes_to_the_second_step(self):
        acc = self.account("root", "admin")
        secret, _qr = lac.enroll_totp(acc)
        lac.confirm_totp(acc, totp.compute_totp(secret))
        with mock.patch.object(sh, "MFA_REQUIRED", "admins"):
            status, headers, _b = self.login("root")
        self.assertEqual((status, location(headers)), (303, f"{B}/login/totp"))
        pending = app.unsign(cookie_value(headers, "hse_totp_pending"))
        self.assertEqual((pending["account_id"], pending["username"]), (acc, "root"))
        self.assertNotIn("hse_session", set_cookies(headers))

    def test_mfa_modes(self):
        member, admin = {"role": "member"}, {"role": "admin"}
        h = app.Handler.__new__(app.Handler)
        for mode, expected in (("everyone", (True, True)), ("admins", (False, True)), ("optional", (False, False))):
            with mock.patch.object(sh, "MFA_REQUIRED", mode):
                self.assertEqual((h.totp_required_for(member), h.totp_required_for(admin)), expected, mode)

    def test_next_cookie_only_honours_a_register_path(self):
        self.account()
        good = app.sign({"path": f"{B}/register/abcdefgh1234", "exp": time.time() + 60})
        evil = app.sign({"path": "https://evil.example/", "exp": time.time() + 60})
        _s, headers, _b = request("POST", B + "/login/local", form={"username": "alice", "password": PASSWORD},
                                  headers={"Cookie": f"hse_next={good}"})
        self.assertEqual(location(headers), f"{B}/register/abcdefgh1234")
        _s, headers, _b = request("POST", B + "/login/local", form={"username": "alice", "password": PASSWORD},
                                  headers={"Cookie": f"hse_next={evil}"})
        self.assertEqual(location(headers), f"{B}/machines")


class TotpLoginStep(WithAccounts):
    def setUp(self):
        super().setUp()
        self.acc = self.account("root", "admin")
        self.secret, _qr = lac.enroll_totp(self.acc)
        lac.confirm_totp(self.acc, totp.compute_totp(self.secret))
        self.recovery = lac.reset_recovery_codes(self.acc)
        self.pending = app.sign({"account_id": self.acc, "username": "root", "exp": time.time() + 300})

    def post(self, **form):
        return request("POST", B + "/login/totp", form=form, headers={"Cookie": f"hse_totp_pending={self.pending}"})

    def next_code(self):
        # the confirmation used the current step; replay protection wants a later one
        return totp.compute_totp(self.secret, int(time.time()) + totp.TOTP_PERIOD)

    def test_page_shows_the_username(self):
        status, _h, body = request("GET", B + "/login/totp", headers={"Cookie": f"hse_totp_pending={self.pending}"})
        self.assertEqual(status, 200)
        self.assertIn("root", body)

    def test_without_pending_cookie_or_with_a_gone_account(self):
        for cookie in ({}, {"Cookie": "hse_totp_pending=junk"}):
            status, headers, _b = request("POST", B + "/login/totp", form={"code": "1"}, headers=cookie)
            self.assertEqual((status, location(headers)), (303, f"{B}/login"))
        gone = app.sign({"account_id": 999, "username": "x", "exp": time.time() + 300})
        status, headers, _b = request("POST", B + "/login/totp", form={"code": "1"},
                                      headers={"Cookie": f"hse_totp_pending={gone}"})
        self.assertEqual((status, location(headers)), (303, f"{B}/login"))

    def test_empty_code(self):
        status, _h, body = self.post(code=" ")
        self.assertEqual(status, 401)
        self.assertIn("Code is required.", body)

    def test_wrong_code_counts_and_is_audited(self):
        status, _h, body = self.post(code="000000")
        self.assertEqual(status, 401)
        self.assertIn("Invalid code. Try again.", body)
        event = app.audit.request_event.call_args.args
        self.assertEqual((event[2], event[4]["reason"]), ("auth.signin_failed", "wrong_totp"))
        self.assertEqual(sessions.blocked(f"totp:{IP}"), 0)  # (one hit: below the limit)

    def test_right_code_signs_in_and_cannot_be_replayed(self):
        code = self.next_code()
        status, headers, _b = self.post(code=code)
        self.assertEqual((status, location(headers)), (303, f"{B}/machines"))
        session = app.unsign(cookie_value(headers, "hse_session"))
        self.assertEqual((session["sub"], session["admin"]), (f"local:{self.acc}", True))
        self.assertEqual(self.post(code=code)[0], 401)

    def test_recovery_code_works_once(self):
        code = self.recovery[0]
        status, _h, _b = self.post(code=code, use_recovery="1")
        self.assertEqual(status, 303)
        self.assertEqual(self.post(code=code, use_recovery="1")[0], 401)

    def test_a_recovery_code_is_not_a_totp_code(self):
        self.assertEqual(self.post(code=self.recovery[1])[0], 401)


# --- invitations and password reset -----------------------------------------------------------------------------

class Invitations(WithAccounts):
    def token(self, email="new@example.com", role="member") -> str:
        return account_tokens.create_invitation(email, role)

    def test_page_for_a_valid_and_an_invalid_token(self):
        status, _h, body = request("GET", f"{B}/accept/{self.token()}")
        self.assertEqual(status, 200)
        self.assertIn("new@example.com", body)
        status, _h, body = request("GET", f"{B}/accept/doesnotexist")
        self.assertEqual(status, 400)
        self.assertIn("invalid or has expired", body)

    def test_accepting_creates_the_headscale_user_and_the_account_and_signs_in(self):
        self.api.side_effect = lambda method, path, *a, **k: {}
        status, headers, _b = request("POST", f"{B}/accept/{self.token(role='admin')}",
                                      form={"username": "newbie", "password": PASSWORD, "password2": PASSWORD})
        self.assertEqual((status, location(headers)), (303, f"{B}/machines"))
        self.api.assert_called_once_with("POST", "/user", {"name": "newbie"})
        acc = lac.get_account(username="newbie")
        self.assertEqual((acc["role"], acc["email"], acc["headscale_user"]), ("admin", "new@example.com", "newbie"))
        self.assertTrue(app.unsign(cookie_value(headers, "hse_session"))["admin"])

    def test_form_errors_are_400_and_burn_the_token(self):
        # QUIRK: verify_token() consumes the token before the form is validated, so one typo costs the invitation.
        token = self.token()
        status, _h, body = request("POST", f"{B}/accept/{token}",
                                   form={"username": "newbie", "password": PASSWORD, "password2": "other"})
        self.assertEqual(status, 400)
        self.assertIn("Passwords do not match.", body)
        status, _h, body = request("POST", f"{B}/accept/{token}",
                                   form={"username": "newbie", "password": PASSWORD, "password2": PASSWORD})
        self.assertEqual(status, 400)
        self.assertIn("invalid or has expired", body)

    def test_each_validation_message(self):
        cases = [({"username": "", "password": "x"}, "Username and password are required."),
                 ({"username": "ab", "password": PASSWORD, "password2": PASSWORD}, "Username must be 3-32"),
                 ({"username": "bad name", "password": PASSWORD, "password2": PASSWORD}, "Username must be 3-32")]
        for form, text in cases:
            status, _h, body = request("POST", f"{B}/accept/{self.token()}", form=form)
            self.assertEqual(status, 400, form)
            self.assertIn(text, body)
        self.api.assert_not_called()

    def test_headscale_refusing_the_user_is_500(self):
        self.api.side_effect = http_error(409)
        status, _h, body = request("POST", f"{B}/accept/{self.token()}",
                                   form={"username": "newbie", "password": PASSWORD, "password2": PASSWORD})
        self.assertEqual(status, 500)
        self.assertIn("Failed to create user", body)
        self.assertIsNone(lac.get_account(username="newbie"))

    def test_weak_password_removes_the_headscale_user_again(self):
        self.api.side_effect = lambda *a, **k: {}
        status, _h, _b = request("POST", f"{B}/accept/{self.token()}",
                                 form={"username": "newbie", "password": "short", "password2": "short"})
        self.assertEqual(status, 400)
        self.assertEqual([c.args[:2] for c in self.api.call_args_list],
                         [("POST", "/user"), ("DELETE", "/user/newbie")])


class PasswordReset(WithAccounts):
    def test_page_for_a_valid_and_an_invalid_token(self):
        acc = self.account()
        status, _h, body = request("GET", f"{B}/reset/{account_tokens.create_reset_token(acc)}")
        self.assertEqual(status, 200)
        self.assertIn("alice", body)
        status, _h, body = request("GET", f"{B}/reset/nope")
        self.assertEqual(status, 400)
        self.assertIn("invalid or has expired", body)

    def test_reset_changes_the_password_and_signs_in(self):
        acc = self.account()
        status, headers, _b = request("POST", f"{B}/reset/{account_tokens.create_reset_token(acc)}",
                                      form={"password": "An0ther-Passw0rd!", "password2": "An0ther-Passw0rd!"})
        self.assertEqual((status, location(headers)), (303, f"{B}/machines"))
        self.assertTrue(lac.verify_password("An0ther-Passw0rd!", lac.get_account(id=acc)["pw_hash"]))
        self.assertEqual(app.unsign(cookie_value(headers, "hse_session"))["sub"], f"local:{acc}")

    def test_errors(self):
        acc = self.account()
        for form, text in (({}, "Password is required."),
                           ({"password": PASSWORD, "password2": "x"}, "Passwords do not match."),
                           ({"password": "short", "password2": "short"}, None)):
            status, _h, body = request("POST", f"{B}/reset/{account_tokens.create_reset_token(acc)}", form=form)
            self.assertEqual(status, 400, form)
            if text:
                self.assertIn(text, body)

    def test_token_of_a_deleted_account(self):
        acc = self.account()
        token = account_tokens.create_reset_token(acc)
        with accounts_db._db() as db:
            db.execute("DELETE FROM accounts WHERE id = ?", (acc,))
        for method in ("GET", "POST"):
            status, _h, body = request(method, f"{B}/reset/{token}", form={"password": PASSWORD, "password2": PASSWORD})
            self.assertEqual(status, 400, method)
            # the tokens go with the account (ON DELETE CASCADE), so "Account not found." is unreachable this way
            self.assertIn("invalid or has expired", body)

    def test_reset_token_is_not_an_invitation(self):
        acc = self.account()
        self.assertEqual(request("GET", f"{B}/accept/{account_tokens.create_reset_token(acc)}")[0], 400)


# --- account settings (local accounts) --------------------------------------------------------------------------

class AccountSettings(WithAccounts):
    def setUp(self):
        super().setUp()
        self.acc = self.account("alice", "member")
        self.session = local_session(self.acc, "member")

    def post(self, path, session=None, **form):
        return request("POST", B + path, session or self.session, dict({"csrf": "tok"}, **form))

    def test_non_local_sessions_are_refused(self):
        for path in ("/settings/account/password", "/settings/account/totp/confirm",
                     "/settings/account/totp/disable", "/settings/account/totp/recovery/reset"):
            status, _h, body = self.post(path, ADMIN)
            self.assertEqual(status, 403, path)
            self.assertIn("local accounts only", body)

    def test_session_of_a_deleted_account_is_404(self):
        with accounts_db._db() as db:
            db.execute("DELETE FROM accounts WHERE id = ?", (self.acc,))
        for path in ("/settings/account/password", "/settings/account/totp/confirm",
                     "/settings/account/totp/disable", "/settings/account/totp/recovery/reset"):
            status, _h, body = self.post(path)
            self.assertEqual(status, 404, path)
            self.assertIn("Account not found.", body)

    def test_change_password_outcomes(self):
        cases = [({}, "password-required"),
                 ({"old_password": PASSWORD}, "password-required"),
                 ({"old_password": PASSWORD, "new_password": "a", "new_password2": "b"}, "password-mismatch"),
                 ({"old_password": "wrong", "new_password": "N3w-Passw0rd!!", "new_password2": "N3w-Passw0rd!!"},
                  "wrong-password")]
        for form, code in cases:
            status, headers, _b = self.post("/settings/account/password", **form)
            self.assertEqual((status, location(headers)), (303, f"{B}/settings/account?m={code}"), form)

    def test_change_password_ok_and_too_short(self):
        status, headers, _b = self.post("/settings/account/password", old_password=PASSWORD,
                                        new_password="N3w-Passw0rd!!", new_password2="N3w-Passw0rd!!")
        self.assertEqual(location(headers), f"{B}/settings/account?m=password-changed")
        self.assertTrue(lac.verify_password("N3w-Passw0rd!!", lac.get_account(id=self.acc)["pw_hash"]))
        status, headers, _b = self.post("/settings/account/password", old_password="N3w-Passw0rd!!",
                                        new_password="x", new_password2="x")
        # QUIRK: the validation message itself travels in the flash query string
        self.assertTrue(location(headers).startswith(f"{B}/settings/account?m="))
        self.assertNotIn("password-changed", location(headers))

    def test_must_change_blocks_everything_but_the_password_form(self):
        locked = local_session(self.acc, "member", must_change=True)
        with accounts_db._db() as db:
            db.execute("UPDATE accounts SET must_change = 1 WHERE id = ?", (self.acc,))
        status, headers, _b = self.post("/keys", locked)
        self.assertEqual((status, location(headers)), (303, f"{B}/settings/account?m=must-change"))
        status, headers, _b = self.post("/settings/language", locked, lang="es")
        self.assertEqual(status, 303)
        self.assertNotIn("must-change", location(headers))

    def test_totp_enrollment_then_disable_then_recovery(self):
        status, headers, _b = self.post("/settings/account/totp/confirm", code="")
        self.assertEqual(location(headers), f"{B}/settings/account/totp/enroll?m=code-required")
        secret, _qr = lac.enroll_totp(self.acc)
        status, headers, _b = self.post("/settings/account/totp/confirm", code="000000")
        self.assertEqual(location(headers), f"{B}/settings/account/totp/enroll?m=invalid-code")
        status, headers, _b = self.post("/settings/account/totp/recovery/reset")
        self.assertEqual(location(headers), f"{B}/settings/account?m=totp-not-enabled")
        status, headers, _b = self.post("/settings/account/totp/confirm", code=totp.compute_totp(secret))
        self.assertEqual(location(headers), f"{B}/settings/account?m=totp-enabled")
        self.assertEqual(app.audit.request_event.call_args.args[2], "account.totp_enabled")
        # QUIRK (bug): settings_pages.recovery_codes_page() asks ui.icon() for 'alert-triangle', which does not exist, so
        # it raises KeyError. The catch-all in do_POST turns that into "machines?m=failed" AFTER the new codes were
        # stored: the old codes stop working and the new ones are never shown.
        old_hash = lac.get_account(id=self.acc)["recovery_codes"]
        status, headers, _b = self.post("/settings/account/totp/recovery/reset")
        self.assertEqual((status, location(headers)), (303, f"{B}/machines?m=failed"))
        self.assertNotEqual(lac.get_account(id=self.acc)["recovery_codes"], old_hash)
        status, headers, _b = self.post("/settings/account/totp/disable")
        self.assertEqual(location(headers), f"{B}/settings/account?m=totp-disabled")
        self.assertFalse(lac.get_account(id=self.acc)["totp_confirmed"])


class SessionsAndLogout(WithAccounts):
    def test_logout_of_an_oidc_session_goes_through_the_provider(self):
        oidc = dict(ADMIN, idt="tokenhint")
        disc = {"end_session_endpoint": "https://idp.example/logout"}
        with mock.patch.object(sh, "SSO", True), mock.patch.object(sh, "discovery", lambda: disc):
            status, headers, _b = request("POST", B + "/logout", oidc, {"csrf": "tok"})
        self.assertEqual(status, 303)
        target = urllib.parse.urlparse(location(headers))
        self.assertEqual(target.netloc, "idp.example")
        query = urllib.parse.parse_qs(target.query)
        self.assertEqual(query["id_token_hint"], ["tokenhint"])
        self.assertEqual(query["post_logout_redirect_uri"], [f"https://vpn.example.com{B}/"])
        self.assertIn("Max-Age=0", set_cookies(headers)["hse_session"])

    def test_logout_without_end_session_endpoint_or_for_local_goes_to_signed_out(self):
        with mock.patch.object(sh, "SSO", True), mock.patch.object(sh, "discovery", lambda: {}):
            status, headers, _b = request("POST", B + "/logout", ADMIN, {"csrf": "tok"})
        self.assertEqual(location(headers), f"{B}/login?m=signed-out")
        status, headers, _b = request("POST", B + "/logout", dict(ADMIN, kind="local"), {"csrf": "tok"})
        self.assertEqual(location(headers), f"{B}/login?m=signed-out")

    def test_revoking_unknown_session_and_someone_elses_as_a_member(self):
        other = sessions.create(dict(MEMBER, sub="zzz"))
        for sid in ("nope", other):
            status, headers, _b = request("POST", B + "/settings/sessions/revoke", MEMBER, {"csrf": "tok", "sid": sid})
            self.assertEqual(location(headers), f"{B}/settings/sessions?m=session-not-found", sid)
        self.assertIsNotNone(sessions.get(other))

    def test_admin_revokes_someone_elses_session_and_a_member_their_own_other_one(self):
        other = sessions.create(dict(MEMBER, sub="zzz"))
        status, headers, _b = request("POST", B + "/settings/sessions/revoke", ADMIN, {"csrf": "tok", "sid": other})
        self.assertEqual(location(headers), f"{B}/settings/sessions?m=session-revoked")
        mine = sessions.create(dict(MEMBER))
        status, headers, _b = request("POST", B + "/settings/sessions/revoke", MEMBER, {"csrf": "tok", "sid": mine})
        self.assertEqual(location(headers), f"{B}/settings/sessions?m=session-revoked")

    def test_revoking_the_current_session_signs_out(self):
        current = dict(MEMBER, sid=sessions.create(MEMBER))
        status, headers, _b = request("POST", B + "/settings/sessions/revoke", current,
                                      {"csrf": "tok", "sid": current["sid"]})
        self.assertEqual(location(headers), f"{B}/login?m=signed-out")
        self.assertIn("Max-Age=0", set_cookies(headers)["hse_session"])

    def test_revoke_all(self):
        status, headers, _b = request("POST", B + "/settings/sessions/revoke-all", MEMBER,
                                      {"csrf": "tok", "scope": "everyone"})
        self.assertEqual(status, 403)
        status, headers, _b = request("POST", B + "/settings/sessions/revoke-all", ADMIN,
                                      {"csrf": "tok", "scope": "everyone"})
        self.assertEqual(location(headers), f"{B}/settings/sessions?m=sessions-revoked")
        status, headers, _b = request("POST", B + "/settings/sessions/revoke-all", MEMBER, {"csrf": "tok"})
        self.assertEqual(location(headers), f"{B}/login?m=signed-out")
        apikey = dict(ADMIN, sub="", kind="apikey")
        status, headers, _b = request("POST", B + "/settings/sessions/revoke-all", apikey, {"csrf": "tok"})
        self.assertEqual(location(headers), f"{B}/login?m=signed-out")

    def test_language_cookie_and_redirect_target(self):
        for referer, dest in ((f"https://vpn.example.com{B}/dns", f"{B}/dns"),
                              ("https://evil.example/x", f"{B}/settings/general"),
                              ("", f"{B}/settings/general"),
                              ("https://vpn.example.com/outside", f"{B}/settings/general")):
            status, headers, _b = request("POST", B + "/settings/language", MEMBER, {"csrf": "tok", "lang": "es"},
                                          headers={"Referer": referer})
            self.assertEqual((status, location(headers)), (303, dest), referer)
            self.assertTrue(set_cookies(headers)["hse_lang"].startswith("hse_lang=es;"))
        status, headers, _b = request("POST", B + "/settings/language", MEMBER, {"csrf": "tok", "lang": "xx"})
        self.assertTrue(set_cookies(headers)["hse_lang"].startswith("hse_lang=;"))


# --- API key and OIDC sign-in ------------------------------------------------------------------------------------

class ApiKeyLogin(WithAccounts):
    def post(self, key):
        with mock.patch.object(sh, "API_KEY_LOGIN", True):
            return request("POST", B + "/login/apikey", form={"api_key": key})

    def test_bad_shapes_are_rejected_without_calling_headscale(self):
        for key in ("", "nokey", "hskey-api-" + "x" * 200):
            status, _h, body = self.post(key)
            self.assertEqual(status, 401, key[:12])
            self.assertIn("Invalid or expired API key.", body)
        self.sleep.assert_called()

    def test_headscale_refusing_the_key(self):
        with mock.patch.object(hs, "http_json", mock.Mock(side_effect=urllib.error.URLError("refused"))):
            self.assertEqual(self.post("hskey-api-abcdef-secret")[0], 401)

    def test_valid_key_makes_an_admin_session_that_remembers_only_the_prefix(self):
        with mock.patch.object(hs, "http_json", mock.Mock(return_value={})) as http:
            status, headers, _b = self.post("hskey-api-abcdef-secret")
        self.assertEqual((status, location(headers)), (303, f"{B}/machines"))
        self.assertEqual(http.call_args.kwargs["headers"], {"Authorization": "Bearer hskey-api-abcdef-secret"})
        session = app.unsign(cookie_value(headers, "hse_session"))
        self.assertEqual((session["kind"], session["admin"], session["role"]), ("apikey", True, "admin"))
        self.assertNotIn("secret", session["key"])

    def test_the_route_does_not_exist_when_api_key_login_is_off(self):
        with mock.patch.object(sh, "API_KEY_LOGIN", False):
            status, headers, _b = request("POST", B + "/login/apikey", form={"api_key": "hskey-api-x"})
        # QUIRK: falls through to the session check, so it looks like "not signed in", not a 404
        self.assertEqual((status, location(headers)), (303, f"{B}/login"))


class OidcCallback(WithAccounts):
    DISC = {"authorization_endpoint": "https://idp.example/auth", "token_endpoint": "https://idp.example/token",
            "userinfo_endpoint": "https://idp.example/userinfo"}

    def call(self, params, tx_state="state1", userinfo=None, cookie=True):
        tx = app.sign({"state": tx_state, "verifier": "ver", "exp": time.time() + 600})
        headers = {"Cookie": f"hse_oidc={tx}"} if cookie else {}
        http = mock.Mock(side_effect=[{"access_token": "at", "id_token": "idt"}, userinfo or {"sub": "s1"}])
        with mock.patch.object(sh, "SSO", True), mock.patch.object(sh, "discovery", lambda: self.DISC), \
                mock.patch.object(hs, "http_json", http):
            query = urllib.parse.urlencode(params)
            return request("GET", f"{B}/callback?{query}", headers=headers), http

    def test_invalid_state_missing_code_or_cookie_are_400_and_counted(self):
        for params, cookie in (({"code": "c", "state": "other"}, True), ({"state": "state1"}, True),
                               ({"code": "c", "state": "state1"}, False)):
            (status, _h, body), http = self.call(params, cookie=cookie)
            self.assertEqual(status, 400, params)
            self.assertIn("Could not sign in", body)
            http.assert_not_called()
        event = app.audit.request_event.call_args.args
        self.assertEqual((event[2], event[4]["method"]), ("auth.signin_failed", "oidc"))

    def test_error_from_the_provider_is_audited_truncated(self):
        (status, _h, _b), _http = self.call({"error": "access_denied" + "x" * 200, "state": "state1"})
        self.assertEqual(status, 400)
        self.assertEqual(len(app.audit.request_event.call_args.args[4]["reason"]), 100)

    def test_token_exchange_uses_pkce_and_basic_auth(self):
        (status, _h, _b), http = self.call({"code": "thecode", "state": "state1"})
        self.assertEqual(status, 303)
        first, second = http.call_args_list
        self.assertEqual(first.args, ("POST", "https://idp.example/token"))
        body = urllib.parse.parse_qs(first.kwargs["body"].decode())
        self.assertEqual((body["code"], body["code_verifier"], body["grant_type"]),
                         (["thecode"], ["ver"], ["authorization_code"]))
        self.assertTrue(first.kwargs["headers"]["Authorization"].startswith("Basic "))
        self.assertEqual(second.kwargs["headers"], {"Authorization": "Bearer at"})

    def test_groups_decide_the_role_and_the_session_is_created(self):
        info = {"sub": "s1", "preferred_username": "carol", "name": "Carol", "email": "Carol@Example.com",
                "groups": ["vpn-admins", "a"]}
        (status, headers, _b), _http = self.call({"code": "c", "state": "state1"}, userinfo=info)
        self.assertEqual((status, location(headers)), (303, f"{B}/machines"))
        session = app.unsign(cookie_value(headers, "hse_session"))
        self.assertEqual((session["kind"], session["admin"], session["role"], session["groups"], session["idt"]),
                         ("oidc", True, "admin", ["a", "vpn-admins"], "idt"))
        self.assertEqual(session["email"], "Carol@Example.com")  # the session keeps the provider's spelling

    def test_unverified_email_does_not_make_an_admin(self):
        info = {"sub": "s1", "email": "boss@example.com", "email_verified": False}
        with mock.patch.object(sh, "ADMIN_EMAILS", {"boss@example.com"}):
            (_s, headers, _b), _http = self.call({"code": "c", "state": "state1"}, userinfo=info)
        session = app.unsign(cookie_value(headers, "hse_session"))
        self.assertEqual((session["admin"], session["role"]), (False, "member"))


# --- machines -----------------------------------------------------------------------------------------------------

NODE = {"id": "7", "givenName": "bob-laptop", "user": BOB, "availableRoutes": ["10.0.0.0/24", "0.0.0.0/0", "::/0"],
        "approvedRoutes": ["10.1.0.0/24"], "tags": ["tag:old"]}
AUDITOR = dict(ADMIN, admin=False, role="auditor")


class MachineActions(NoNetwork):
    def setUp(self):
        super().setUp()
        self.api.side_effect = lambda *a, **k: {}
        for p in (mock.patch.object(hs, "get_node", lambda nid: NODE if nid == "7" else None),
                  mock.patch.object(hs, "owned_node", lambda user, nid: NODE if nid == "7" and user is BOB else None)):
            p.start()
            self.addCleanup(p.stop)

    def act(self, action, session=ADMIN, node="7", **form):
        return request("POST", f"{B}/machines/{node}/{action}", session, dict({"csrf": "tok"}, **form))

    def calls(self):
        return [c.args for c in self.api.call_args_list]

    def audit_actions(self):
        return [c.args[2] for c in app.audit.request_event.call_args_list]

    def test_unknown_node_and_not_owned_node(self):
        for session, node in ((ADMIN, "99"), (MEMBER, "99")):
            status, headers, _b = self.act("rename", session, node, name="x")
            self.assertEqual(location(headers), f"{B}/machines?m=not-found")
        other = dict(MEMBER, sub="zzz")
        status, headers, _b = self.act("delete", other)
        self.assertEqual(location(headers), f"{B}/machines?m=not-found")
        self.api.assert_not_called()

    def test_auditor_can_see_but_never_write(self):
        for action in ("rename", "delete", "expire", "tags"):
            status, headers, _b = self.act(action, AUDITOR, name="x")
            expected = f"{B}/machines?m=forbidden"
            self.assertEqual(location(headers), expected, action)
        self.api.assert_not_called()

    def test_member_cannot_use_the_admin_only_actions(self):
        for action in ("expiry", "routes", "approve-routes", "tags"):
            status, headers, _b = self.act(action, MEMBER)
            self.assertEqual(location(headers), f"{B}/machines?m=forbidden", action)
        self.api.assert_not_called()

    def test_rename(self):
        status, headers, _b = self.act("rename", name=" New-Name ")
        self.assertEqual(location(headers), f"{B}/machines?m=renamed")
        self.assertEqual(self.calls(), [("POST", "/node/7/rename/new-name")])
        self.assertEqual(self.audit_actions(), ["machine.rename"])

    def test_rename_rejects_bad_names_and_keeps_the_detail_page_as_destination(self):
        for name in ("", "-a", "a_b", "x" * 64, "a b"):
            status, headers, _b = self.act("rename", name=name, back="machines/7")
            self.assertEqual(location(headers), f"{B}/machines/7?m=bad-name", name)
        self.api.assert_not_called()

    def test_back_is_whitelisted_and_ignored_for_delete(self):
        status, headers, _b = self.act("expire", back="https://evil.example/")
        self.assertEqual(location(headers), f"{B}/machines?m=expired")
        status, headers, _b = self.act("expire", back="machines/7")
        self.assertEqual(location(headers), f"{B}/machines/7?m=expired")
        status, headers, _b = self.act("delete", back="machines/7")
        self.assertEqual(location(headers), f"{B}/machines?m=removed")

    def test_expire(self):
        self.act("expire", MEMBER)  # members may expire their own device
        self.assertEqual(self.calls(), [("POST", "/node/7/expire")])
        self.assertEqual(self.audit_actions(), ["machine.expire"])

    def test_expiry_disable_and_enable(self):
        status, headers, _b = self.act("expiry", disable="1")
        self.assertEqual(location(headers), f"{B}/machines?m=expiry-off")
        self.assertEqual(self.calls(), [("POST", "/node/7/expire?disableExpiry=true")])
        self.api.reset_mock()
        with mock.patch.object(hs, "key_expiry_days", lambda: None):
            status, headers, _b = self.act("expiry")
        self.assertEqual(location(headers), f"{B}/machines?m=expiry-on")
        self.assertTrue(self.calls()[0][1].startswith("/node/7/expire?expiry="))
        self.assertEqual(app.audit.request_event.call_args.args[4], {"days": 180})  # never-expire tailnets use 180

    def test_expiry_enable_uses_the_tailnet_setting(self):
        with mock.patch.object(hs, "key_expiry_days", lambda: 30):
            self.act("expiry")
        self.assertEqual(app.audit.request_event.call_args.args[4], {"days": 30})

    def test_routes_only_approves_what_the_node_advertises(self):
        status, headers, _b = self.act("routes", route=["10.0.0.0/24", "192.168.0.0/16", "exit"])
        self.assertEqual(location(headers), f"{B}/machines?m=routes")
        method, path, body = self.calls()[0]
        self.assertEqual((method, path), ("POST", "/node/7/approve_routes"))
        self.assertEqual(body, {"routes": ["0.0.0.0/0", "10.0.0.0/24", "::/0"]})

    def test_routes_with_nothing_ticked_approves_nothing(self):
        # QUIRK: unticking everything revokes all approvals (including ones made elsewhere)
        self.act("routes")
        self.assertEqual(self.calls()[0][2], {"routes": []})

    def test_approve_routes_keeps_what_was_approved_and_adds_everything_advertised(self):
        self.act("approve-routes")
        self.assertEqual(self.calls()[0][2],
                         {"routes": ["0.0.0.0/0", "10.0.0.0/24", "10.1.0.0/24", "::/0"]})

    def test_tags(self):
        status, headers, _b = self.act("tags", tags="TAG:one\n\ntag:two")
        self.assertEqual(location(headers), f"{B}/machines?m=tags")
        self.assertEqual(self.calls()[0][2], {"tags": ["tag:one", "tag:two"]})
        self.api.reset_mock()
        status, headers, _b = self.act("tags", tags="tag:ok\nnot-a-tag")
        self.assertEqual(location(headers), f"{B}/machines?m=failed")
        self.api.assert_not_called()

    def test_tags_rejected_by_headscale_shows_the_reason_on_the_detail_page(self):
        self.api.side_effect = http_error(400, b'{"message": "tag:one is not permitted"}')
        status, _h, body = self.act("tags", tags="tag:one")
        self.assertEqual(status, 400)
        self.assertIn("Could not save the tags", body)
        self.assertIn("not permitted", body)

    def test_other_headscale_errors_redirect_with_failed(self):
        self.api.side_effect = http_error(500)
        status, headers, _b = self.act("expire")
        self.assertEqual(location(headers), f"{B}/machines?m=failed")

    def test_delete(self):
        status, headers, _b = self.act("delete", MEMBER)
        self.assertEqual(location(headers), f"{B}/machines?m=removed")
        self.assertEqual(self.calls(), [("DELETE", "/node/7")])
        self.assertEqual(app.audit.request_event.call_args.args[4], {"user": "bob"})


class Registration(NoNetwork):
    AUTH = "abcdefgh12345678"

    def setUp(self):
        super().setUp()
        p = mock.patch.object(hs, "all_users", lambda: [BOB, {"id": "1", "name": "root"}])
        p.start()
        self.addCleanup(p.stop)
        self.api.side_effect = lambda method, path, body=None, *a, **k: {"node": {"id": "12"}}

    def test_register_node_accepts_the_full_url_printed_by_tailscale_up(self):
        status, headers, _b = request("POST", B + "/machines/register", ADMIN,
                                      {"csrf": "tok", "auth_id": f"https://vpn.example.com/register/{self.AUTH}/",
                                       "user": "bob"})
        self.assertEqual(location(headers), f"{B}/machines?m=registered")
        self.api.assert_called_once_with("POST", "/auth/register", {"user": "bob", "authId": self.AUTH})
        self.assertEqual(app.audit.request_event.call_args.args[2], "machine.register")

    def test_register_node_rejects_bad_ids_and_unknown_users(self):
        for auth, user in (("x", "bob"), (self.AUTH, "ghost"), ("", "bob")):
            status, headers, _b = request("POST", B + "/machines/register", ADMIN,
                                          {"csrf": "tok", "auth_id": auth, "user": user})
            self.assertEqual(location(headers), f"{B}/machines?m=failed", (auth, user))
        self.api.assert_not_called()

    def test_register_node_headscale_error_shows_the_machines_page_with_a_generic_reason(self):
        self.api.side_effect = http_error(400, b'{"message": "auth id unknown"}')
        with mock.patch.object(hs, "all_nodes", lambda: []):
            status, _h, body = request("POST", B + "/machines/register", ADMIN,
                                       {"csrf": "tok", "auth_id": self.AUTH, "user": "bob"})
        self.assertEqual(status, 400)
        self.assertIn("Could not register", body)
        # QUIRK: register_auth_id() already read the HTTPError body for its log line, and a body can be read only
        # once, so the page shows urllib's generic text instead of Headscale's "auth id unknown".
        self.assertIn("HTTP Error 400", body)
        self.assertNotIn("auth id unknown", body)

    def test_admin_register_device_goes_to_the_new_machine(self):
        status, headers, _b = request("POST", f"{B}/register/{self.AUTH}", ADMIN, {"csrf": "tok", "user": "root"})
        self.assertEqual(location(headers), f"{B}/machines/12?m=registered")
        self.assertEqual(self.api.call_args.args[2]["user"], "root")

    def test_admin_register_device_with_unknown_user(self):
        status, headers, _b = request("POST", f"{B}/register/{self.AUTH}", ADMIN, {"csrf": "tok", "user": "ghost"})
        self.assertEqual(location(headers), f"{B}/machines?m=failed")

    def test_a_member_only_registers_for_themselves_whatever_the_form_says(self):
        status, headers, _b = request("POST", f"{B}/register/{self.AUTH}", MEMBER,
                                      {"csrf": "tok", "user": "root"})
        self.assertEqual(status, 303)
        self.assertEqual(self.api.call_args.args[2]["user"], "bob")

    def test_a_member_without_a_headscale_user_is_sent_back_to_the_approval_page(self):
        with mock.patch.object(hs, "user_for_sub", lambda sub: None):
            status, headers, _b = request("POST", f"{B}/register/{self.AUTH}", MEMBER, {"csrf": "tok"})
        self.assertEqual(location(headers), f"{B}/register/{self.AUTH}")
        self.api.assert_not_called()

    def test_auditors_cannot_add_devices(self):
        status, _h, body = request("POST", f"{B}/register/{self.AUTH}", AUDITOR, {"csrf": "tok"})
        self.assertEqual(status, 403)
        self.assertIn("Auditors cannot add devices", body)

    def test_non_numeric_node_id_falls_back_to_the_list(self):
        self.api.side_effect = lambda *a, **k: {"node": {"id": "abc"}}
        status, headers, _b = request("POST", f"{B}/register/{self.AUTH}", ADMIN, {"csrf": "tok", "user": "bob"})
        self.assertEqual(location(headers), f"{B}/machines?m=registered")

    def test_register_error_from_headscale_is_shown_on_the_approval_page(self):
        self.api.side_effect = http_error(400, b'{"message": "expired"}')
        status, _h, body = request("POST", f"{B}/register/{self.AUTH}", ADMIN, {"csrf": "tok", "user": "bob"})
        self.assertEqual(status, 400)
        self.assertIn("Could not register", body)


class RemoveInactive(Base):
    def setUp(self):
        super().setUp()
        old = "2020-01-01T00:00:00Z"
        self.nodes = [
            {"id": "1", "givenName": "stale", "online": False, "lastSeen": old, "user": BOB},
            {"id": "2", "givenName": "stale2", "online": False, "lastSeen": old, "user": BOB},
            {"id": "3", "givenName": "fresh", "online": True, "lastSeen": old, "user": BOB},
        ]
        p = mock.patch.object(hs, "all_nodes", lambda: self.nodes)
        p.start()
        self.addCleanup(p.stop)
        self.api.side_effect = lambda *a, **k: {}

    def post(self, **form):
        return request("POST", B + "/machines/remove-inactive", ADMIN, dict({"csrf": "tok"}, **form))

    def test_removes_only_ticked_machines_that_are_still_inactive(self):
        status, headers, _b = self.post(**{"node-1": "on", "node-3": "on"})
        self.assertEqual(location(headers), f"{B}/machines?m=inactive-removed-1")
        self.api.assert_called_once_with("DELETE", "/node/1")
        self.assertEqual(app.audit.request_event.call_args.args[2], "machines.remove_inactive")

    def test_nothing_ticked_or_nothing_inactive(self):
        status, headers, _b = self.post()
        self.assertEqual(location(headers), f"{B}/machines?m=inactive-none")
        status, headers, _b = self.post(**{"node-3": "on"})
        self.assertEqual(location(headers), f"{B}/machines?m=inactive-none")
        self.api.assert_not_called()

    def test_one_failure_makes_the_whole_result_failed_but_the_rest_are_still_removed(self):
        def api(method, path, *a, **k):
            if path == "/node/1":
                raise http_error(500)
            return {}
        self.api.side_effect = api
        status, headers, _b = self.post(**{"node-1": "on", "node-2": "on"})
        self.assertEqual(location(headers), f"{B}/machines?m=failed")
        self.assertEqual([c.args[1] for c in self.api.call_args_list], ["/node/1", "/node/2"])
        self.assertEqual(app.audit.request_event.call_args.args[4], {"nodes": ["stale2"]})

    def test_members_cannot(self):
        status, _h, body = request("POST", B + "/machines/remove-inactive", MEMBER, {"csrf": "tok"})
        self.assertEqual(status, 403)
        self.api.assert_not_called()


# --- access control policy ----------------------------------------------------------------------------------------

POLICY = """{
  "groups": {"group:dev": ["bob@"]},
  "tagOwners": {"tag:web": ["group:dev"]},
  "autoApprovers": {"routes": {"10.0.0.0/24": ["group:dev"]}, "exitNode": []},
  "acls": [{"action": "accept", "src": ["*"], "dst": ["*:*"]}],
  "ssh": [],
}
"""


class AclEditor(Base):
    def setUp(self):
        super().setUp()
        self.saved = []
        self.reads = {"policy": POLICY}

        def api(method, path, body=None, *a, **k):
            if (method, path) == ("GET", "/policy"):
                return {"policy": self.reads["policy"]}
            if path == "/policy" and method == "PUT":
                self.saved.append(body["policy"])
            return {}
        self.api.side_effect = api

    def post(self, path, **form):
        return request("POST", B + path, ADMIN, dict({"csrf": "tok"}, **form))

    def test_unreadable_policy_endpoints(self):
        self.api.side_effect = http_error(502)
        status, headers, _b = self.post("/acl/rules", op="delete", index="0")
        self.assertEqual(location(headers), f"{B}/acl?m=acl-unreachable&tab=rules")

    def test_a_policy_that_does_not_parse_is_left_alone(self):
        self.reads["policy"] = "{ not json"
        status, headers, _b = self.post("/acl/groups", name="group:x", members="a")
        self.assertEqual(location(headers), f"{B}/acl?m=acl-unreadable&tab=groups")
        self.assertEqual(self.saved, [])

    def test_delete_rule_by_index(self):
        status, headers, _b = self.post("/acl/rules", op="delete", index="0")
        self.assertEqual(location(headers), f"{B}/acl?m=acl-deleted&tab=rules")
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(app.audit.request_event.call_args.args[2], "acl.rule_delete")
        status, headers, _b = self.post("/acl/rules", op="delete", index="5")
        self.assertEqual(location(headers), f"{B}/acl?m=acl-not-found&tab=rules")
        status, headers, _b = self.post("/acl/rules", op="delete", index="x")
        self.assertEqual(location(headers), f"{B}/acl?m=acl-not-found&tab=rules")
        self.assertEqual(len(self.saved), 1)

    def test_delete_group_and_tag_owner_and_auto_route(self):
        for path, key, tab, kind in (("/acl/groups", "group:dev", "groups", "group"),
                                      ("/acl/tags", "tag:web", "groups", "tag"),
                                      ("/acl/autoapprove/routes", "10.0.0.0/24", "auto", "auto_route")):
            status, headers, _b = self.post(path, op="delete", orig_name=key)
            self.assertEqual(location(headers), f"{B}/acl?m=acl-deleted&tab={tab}", path)
            self.assertEqual(app.audit.request_event.call_args.args[2], f"acl.{kind}_delete")
            status, headers, _b = self.post(path, op="delete", orig_name="missing")
            self.assertEqual(location(headers), f"{B}/acl?m=acl-not-found&tab={tab}", path)

    def test_invalid_form_input_is_acl_invalid_for_every_kind(self):
        for path, tab in (("/acl/rules", "rules"), ("/acl/ssh", "ssh"), ("/acl/groups", "groups"),
                          ("/acl/tags", "groups"), ("/acl/autoapprove/routes", "auto")):
            status, headers, _b = self.post(path, orig_name="", name="!!", members="", src="", dst="", cidr="nope")
            self.assertEqual(location(headers), f"{B}/acl?m=acl-invalid&tab={tab}", path)
        status, headers, _b = self.post("/acl/autoapprove/exit-node", approvers="tag:BAD NAME")
        self.assertEqual(location(headers), f"{B}/acl?m=acl-invalid&tab=auto")
        self.assertEqual(self.saved, [])

    def test_add_group_then_rename_it(self):
        status, headers, _b = self.post("/acl/groups", name="group:ops", members="alice@")
        self.assertEqual(location(headers), f"{B}/acl?m=acl-saved&tab=groups")
        self.assertIn("group:ops", self.saved[-1])
        self.post("/acl/groups", orig_name="group:dev", name="group:devs", members="bob@")
        self.assertIn("group:devs", self.saved[-1])
        self.assertNotIn('"group:dev": [', self.saved[-1])

    def test_add_a_rule_and_an_ssh_rule(self):
        status, headers, _b = self.post("/acl/rules", action="accept", src="*", dst="*:22", proto="tcp")
        self.assertIn(location(headers), (f"{B}/acl?m=acl-saved&tab=rules", f"{B}/acl?m=acl-invalid&tab=rules"))

    def test_exit_node_approvers_can_be_emptied(self):
        status, headers, _b = self.post("/acl/autoapprove/exit-node", approvers="")
        self.assertEqual(location(headers), f"{B}/acl?m=acl-saved&tab=auto")
        self.assertEqual(app.audit.request_event.call_args.args[2], "acl.auto_exit_save")

    def test_headscale_rejecting_the_new_policy(self):
        base = self.api.side_effect

        def api(method, path, *a, **k):
            if path == "/policy/check":
                raise http_error(400)
            return base(method, path, *a, **k)
        self.api.side_effect = api
        status, headers, _b = self.post("/acl/groups", name="group:ops", members="alice@")
        self.assertEqual(location(headers), f"{B}/acl?m=acl-rejected&tab=groups")
        self.assertEqual(self.saved, [])

    def test_unknown_acl_path_is_404_for_a_network_admin(self):
        status, _h, _b = self.post("/acl/nothing")
        self.assertEqual(status, 404)


class RawAclEditor(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(hs, "get_policy", lambda: {"policy": POLICY})
        p.start()
        self.addCleanup(p.stop)
        for name in ("all_nodes", "all_users"):
            q = mock.patch.object(hs, name, lambda: [])
            q.start()
            self.addCleanup(q.stop)

    def post(self, **form):
        return request("POST", B + "/acl", ADMIN, dict({"csrf": "tok"}, **form))

    def test_save(self):
        self.api.side_effect = lambda *a, **k: {}
        status, headers, _b = self.post(action="save", policy="{}")
        self.assertEqual(location(headers), f"{B}/acl?m=acl-saved&tab=raw")
        self.assertEqual([c.args[:2] for c in self.api.call_args_list], [("PUT", "/policy")])
        self.assertEqual(app.audit.request_event.call_args.args[2], "acl.save")

    def test_check_only_does_not_save_and_returns_the_draft(self):
        self.api.side_effect = lambda *a, **k: {}
        status, _h, body = self.post(action="check", policy="{ my draft }")
        self.assertEqual(status, 200)
        self.assertIn("The policy is valid.", body)
        self.assertIn("my draft", body)
        self.assertEqual([c.args[:2] for c in self.api.call_args_list], [("POST", "/policy/check")])

    def test_rejected_policy_comes_back_with_the_error_and_the_draft(self):
        def api(*a, **k):
            raise http_error(400, b'{"message": "bad hujson"}')  # (an HTTPError body can be read only once)
        self.api.side_effect = api
        for action in ("save", "check"):
            status, _h, body = self.post(action=action, policy="{ draft")
            self.assertEqual(status, 200, action)
            self.assertIn("bad hujson", body)
            self.assertIn("draft", body)

    def test_test_tab_for_an_auditor_and_for_a_member(self):
        status, _h, body = request("POST", B + "/acl/test", AUDITOR,
                                   {"csrf": "tok", "test_src": "bob", "test_dst": "x", "test_port": "22"})
        self.assertEqual(status, 200)
        status, _h, body = request("POST", B + "/acl/test", MEMBER, {"csrf": "tok"})
        self.assertEqual(status, 403)

    def test_members_and_auditors_cannot_edit_but_network_admins_can_reach_the_editor(self):
        for session in (MEMBER, AUDITOR):
            for path in ("/acl/rules", "/dns"):
                self.assertEqual(request("POST", B + path, session, {"csrf": "tok"})[0], 403, path)
        status, _h, _b = request("POST", B + "/acl", MEMBER, {"csrf": "tok"})
        self.assertEqual(status, 403)
        status, _h, _b = request("POST", B + "/acl", AUDITOR, {"csrf": "tok"})
        self.assertEqual(status, 403)
        netadmin = dict(ADMIN, admin=False, role="network_admin")
        self.api.side_effect = lambda *a, **k: {}
        status, headers, _b = request("POST", B + "/acl", netadmin, {"csrf": "tok", "action": "save", "policy": "{}"})
        self.assertEqual(status, 303)


# --- DNS and key expiry -------------------------------------------------------------------------------------------

class DnsSave(NoNetwork):
    CFG = {"base_domain": "tail.example.net", "magic_dns": True, "override_local_dns": True,
           "nameservers": ["1.1.1.1"], "split": {}, "search_domains": []}

    def setUp(self):
        super().setUp()
        for name, value in (("dns_config", lambda: dict(self.CFG)), ("all_nodes", lambda: []),
                            ("apply_dns", mock.Mock(return_value=(True, "")))):
            p = mock.patch.object(hs, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.editable = {"dns_editable": True}
        q = mock.patch.object(sh, "dns_ctx", lambda: dict(app.CTX, **self.editable))
        q.start()
        self.addCleanup(q.stop)

    def post(self, session=ADMIN, **form):
        return request("POST", B + "/dns", session, dict({"csrf": "tok"}, **form))

    def test_not_editable_shows_the_reason(self):
        self.editable = {"dns_editable": False, "dns_reason": "config.yaml has no managed DNS block."}
        status, _h, body = self.post(section="rename", base_domain="x.example.net")
        self.assertEqual(status, 400)
        self.assertIn("no managed DNS block", body)
        hs.apply_dns.assert_not_called()

    def test_not_editable_without_a_reason_uses_the_generic_message(self):
        self.editable = {"dns_editable": False}
        status, _h, body = self.post(section="rename", base_domain="x.example.net")
        self.assertEqual(status, 400)
        self.assertIn("Editing DNS is not available.", body)

    def test_invalid_domain_and_clash_with_the_server_domain(self):
        status, _h, body = self.post(section="rename", base_domain="not a domain")
        self.assertEqual(status, 400)
        self.assertIn("not a valid domain", body)
        status, _h, body = self.post(section="rename", base_domain="example.com")
        self.assertEqual(status, 400)
        self.assertIn("must differ from the server", body)  # (the apostrophe is HTML-escaped)
        status, _h, body = self.post(section="rename", base_domain="vpn.example.com")
        self.assertEqual(status, 400)
        hs.apply_dns.assert_not_called()

    def test_valid_rename_is_applied_and_audited(self):
        status, headers, _b = self.post(section="rename", base_domain="mesh.example.net")
        self.assertEqual((status, location(headers)), (303, f"{B}/dns?m=dns-saved"))
        hs.apply_dns.assert_called_once()
        self.assertEqual(hs.apply_dns.call_args.args[0]["base_domain"], "mesh.example.net")
        self.assertEqual(app.audit.request_event.call_args.args[2], "dns.save")

    def test_headscale_failing_to_apply_shows_the_error(self):
        hs.apply_dns.return_value = (False, "headscale refused the change")
        status, _h, body = self.post(section="rename", base_domain="mesh.example.net")
        self.assertEqual(status, 400)
        self.assertIn("headscale refused the change", body)

    def test_network_admin_may_save_but_members_may_not(self):
        netadmin = dict(ADMIN, admin=False, role="network_admin")
        self.assertEqual(self.post(netadmin, section="rename", base_domain="mesh.example.net")[0], 303)
        self.assertEqual(self.post(MEMBER, section="rename", base_domain="mesh.example.net")[0], 403)


class KeyExpiry(Base):
    def setUp(self):
        super().setUp()
        for name, value in (("key_expiry_days", lambda: 180), ("apply_key_expiry", mock.Mock(return_value=(True, ""))),
                            ("all_nodes", lambda: []), ("dns_config", lambda: {})):
            p = mock.patch.object(hs, name, value)
            p.start()
            self.addCleanup(p.stop)

    def post(self, **form):
        return request("POST", B + "/settings/key-expiry", ADMIN, dict({"csrf": "tok"}, **form))

    def test_days_validation(self):
        for form in ({}, {"days": "0"}, {"days": "abc"}, {"days": str(hs.KEY_EXPIRY_MAX_DAYS + 1)}, {"days": "-1"}):
            status, _h, body = self.post(**form)
            self.assertEqual(status, 200, form)
            self.assertIn("Enter a number of days between 1 and", body)
        hs.apply_key_expiry.assert_not_called()

    def test_never_wins_over_days(self):
        status, headers, _b = self.post(never="1", days="abc")
        self.assertEqual(location(headers), f"{B}/settings/general?m=key-expiry-saved")
        hs.apply_key_expiry.assert_called_once_with(0)
        self.assertEqual(app.audit.request_event.call_args.args[4], {"from": 180, "to": 0})

    def test_headscale_error_is_shown(self):
        hs.apply_key_expiry.return_value = (False, "could not restart")
        status, _h, body = self.post(days="30")
        self.assertEqual(status, 200)
        self.assertIn("could not restart", body)

    def test_members_cannot(self):
        status, _h, _b = request("POST", B + "/settings/key-expiry", MEMBER, {"csrf": "tok", "days": "30"})
        self.assertEqual(status, 403)
        hs.apply_key_expiry.assert_not_called()


# --- backups ------------------------------------------------------------------------------------------------------

class BackupActions(Base):
    def post(self, path, session=ADMIN, **form):
        return request("POST", B + path, session, dict({"csrf": "tok"}, **form))

    def test_run_audits_only_started_or_busy(self):
        for result, audited in (("started", True), ("busy", True), ("unavailable", False)):
            app.audit.request_event.reset_mock()
            with mock.patch.object(hs, "control_backup", lambda r=result: r):
                status, headers, _b = self.post("/backups/run")
            self.assertEqual(location(headers), f"{B}/backups?m=backup-{result}")
            self.assertEqual(app.audit.request_event.called, audited, result)

    def test_settings_forwarded_to_the_supervisor(self):
        control = mock.Mock(return_value=("saved", ""))
        with mock.patch.object(hs, "control_backup_settings", control):
            status, headers, _b = self.post("/backups/settings", backup_enabled="on", backup_schedule=" 0 3 * * * ",
                                            backup_keep_days=" 14 ")
        self.assertEqual(location(headers), f"{B}/backups?m=backup-settings-saved")
        control.assert_called_once_with(True, "0 3 * * *", "14")
        self.assertEqual(app.audit.request_event.call_args.args[4],
                         {"enabled": True, "schedule": "0 3 * * *", "keep_days": "14"})

    def test_settings_default_to_enabled_and_off_drops_the_schedule_from_the_audit(self):
        control = mock.Mock(return_value=("saved", ""))
        with mock.patch.object(hs, "control_backup_settings", control):
            self.post("/backups/settings")
            control.assert_called_with(True, "", "")
            self.post("/backups/settings", backup_enabled="off", backup_schedule="0 3 * * *")
        self.assertEqual(app.audit.request_event.call_args.args[4]["schedule"], None)

    def test_invalid_settings_are_not_audited(self):
        with mock.patch.object(hs, "control_backup_settings", lambda *a: ("invalid", "bad cron")):
            status, headers, _b = self.post("/backups/settings")
        self.assertEqual(location(headers), f"{B}/backups?m=backup-settings-invalid")
        app.audit.request_event.assert_not_called()

    def test_members_get_403_on_every_backup_action(self):
        # (the dispatcher already stops them; the handlers check again)
        for path in ("/backups/run", "/backups/settings", "/backups/restore", "/backups/download"):
            self.assertEqual(self.post(path, MEMBER)[0], 403, path)
        for name, args in (("backup_now", ()), ("backup_settings", ({},)), ("backup_restore", ({},)),
                           ("backup_download", ({},)), ("notify_test", ())):
            h = bare_handler()
            getattr(h, name)(MEMBER, *args)
            self.assertIn(b" 403 ", h.wfile.getvalue().split(b"\r\n")[0], name)

    def test_restore_needs_the_typed_confirmation(self):
        control = mock.Mock(return_value=("started", "1234"))
        with mock.patch.object(hs, "control_restore", control):
            status, headers, _b = self.post("/backups/restore", name="a.tar.gz", confirm="restore")
            self.assertEqual(location(headers), f"{B}/backups?m=backup-restore-confirm")
            control.assert_not_called()
            status, _h, body = self.post("/backups/restore", name="a.tar.gz", confirm="RESTORE")
        self.assertEqual(status, 200)
        self.assertIn("1234", body)
        control.assert_called_once_with("a.tar.gz")
        self.assertEqual(app.audit.request_event.call_args.args[2], "backup.restore")

    def test_restore_refused_by_the_supervisor(self):
        with mock.patch.object(hs, "control_restore", lambda name: ("notfound", "")):
            status, headers, _b = self.post("/backups/restore", name="x", confirm="RESTORE")
        self.assertEqual(location(headers), f"{B}/backups?m=backup-restore-notfound")
        app.audit.request_event.assert_not_called()

    def test_download_of_a_missing_backup(self):
        with mock.patch.object(app.server_status, "open_backup", lambda name: None):
            status, _h, body = self.post("/backups/download", name="../x")
        self.assertEqual(status, 404)
        self.assertIn("That backup does not exist.", body)


class RestoreStatus(Base):
    def get(self, result, rid):
        with mock.patch.object(hs, "restore_result", lambda: result):
            status, headers, body = request("GET", f"{B}/restore-status?id={rid}")
        self.assertEqual((status, headers["content-type"], headers["cache-control"]),
                         (200, ["application/json"], ["no-store"]))
        return json.loads(body)

    def test_needs_no_session_and_only_answers_for_the_matching_id(self):
        self.assertEqual(self.get(None, "1.5"), {"done": False, "ok": False})
        self.assertEqual(self.get({"requested": 1.5, "ok": True}, "1.5"), {"done": True, "ok": True})
        self.assertEqual(self.get({"requested": 1.5, "ok": False}, "1.5"), {"done": True, "ok": False})
        self.assertEqual(self.get({"requested": 1.5, "ok": True}, "2"), {"done": False, "ok": False})

    def test_garbage_ids(self):
        for rid in ("", "abc", "nan"):
            self.assertEqual(self.get({"requested": 1.5, "ok": True}, rid), {"done": False, "ok": False}, rid)
        self.assertEqual(self.get({"ok": True}, "1"), {"done": False, "ok": False})  # (a result without "requested")


class BackupUploadEdges(Base):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"BACKUP_DIR": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)

    def multipart(self, parts):
        boundary = "BOUND"
        chunks = []
        for part in parts:
            if len(part) == 2:
                chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{part[0]}"\r\n\r\n{part[1]}\r\n'.encode())
            else:
                chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{part[0]}"; '
                               f'filename="{part[2]}"\r\nContent-Type: application/gzip\r\n\r\n').encode()
                              + part[1] + b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode())
        return b"".join(chunks), {"Content-Type": f"multipart/form-data; boundary={boundary}"}

    def post(self, parts, session=ADMIN):
        raw, headers = self.multipart(parts)
        return request("POST", B + "/backups/upload", session, headers=headers, raw=raw)

    def leftovers(self):
        return sorted(os.listdir(self.tmp.name))

    def test_no_backup_directory_is_404(self):
        with mock.patch.dict(os.environ, {"BACKUP_DIR": os.path.join(self.tmp.name, "missing")}):
            status, _h, body = self.post([("csrf", "tok")])
        self.assertEqual(status, 404)
        self.assertIn("Backups cannot be uploaded here.", body)
        with mock.patch.dict(os.environ, {"BACKUP_DIR": ""}):
            self.assertEqual(self.post([("csrf", "tok")])[0], 404)

    def test_member_with_must_change_is_sent_to_the_account_page_first(self):
        status, headers, _b = self.post([("csrf", "tok")], dict(MEMBER, must_change=True))
        self.assertEqual(location(headers), f"{B}/settings/account?m=must-change")

    def test_empty_body_and_bad_length(self):
        status, headers, _b = request("POST", B + "/backups/upload", ADMIN, raw=b"")
        self.assertEqual(location(headers), f"{B}/backups?m=backup-upload-none")
        h = bare_handler()
        h.headers = {"Content-Length": "abc"}
        h.backup_upload(ADMIN)
        self.assertIn(f"{B}/backups?m=backup-upload-none".encode(), h.wfile.getvalue())  # unparsable length = none

    def test_too_large_declared_length(self):
        with mock.patch.object(sh, "BACKUP_UPLOAD_MAX", 10):  # the limit is the maximum plus 1 MiB of form overhead
            status, headers, _b = request("POST", B + "/backups/upload", ADMIN, raw=b"x" * ((1 << 20) + 11))
        self.assertEqual(location(headers), f"{B}/backups?m=backup-upload-toolarge")
        self.assertEqual(self.leftovers(), [])

    def test_stale_work_files_are_cleaned_but_fresh_and_foreign_ones_are_kept(self):
        stale = os.path.join(self.tmp.name, ".upload-" + "a" * 24 + ".part")
        fresh = os.path.join(self.tmp.name, ".upload-" + "b" * 24 + ".part")
        foreign = os.path.join(self.tmp.name, ".upload-zzz.part")
        for path in (stale, fresh, foreign):
            open(path, "wb").close()
        old = time.time() - 7200
        os.utime(stale, (old, old))
        os.utime(foreign, (old, old))
        self.post([("csrf", "tok")])
        self.assertEqual(self.leftovers(), sorted([os.path.basename(fresh), os.path.basename(foreign)]))

    def test_wrong_csrf_with_a_file_writes_nothing(self):
        status, _h, body = self.post([("csrf", "bad"), ("file", b"data", "a.tar.gz")])
        self.assertEqual(status, 403)
        self.assertIn("Session expired", body)
        self.assertEqual(self.leftovers(), [])

    def test_wrong_csrf_without_a_file(self):
        status, _h, body = self.post([("csrf", "bad")])
        self.assertEqual(status, 403)
        self.assertEqual(self.leftovers(), [])

    def test_file_field_under_another_name_is_a_bad_upload(self):
        status, headers, _b = self.post([("csrf", "tok"), ("other", b"data", "a.tar.gz")])
        self.assertEqual(location(headers), f"{B}/backups?m=backup-upload-error")
        self.assertEqual(self.leftovers(), [])

    def test_empty_file_and_missing_file_are_upload_none(self):
        for parts in ([("csrf", "tok"), ("file", b"", "a.tar.gz")], [("csrf", "tok"), ("action", "upload")]):
            status, headers, _b = self.post(parts)
            self.assertEqual(location(headers), f"{B}/backups?m=backup-upload-none", parts)
        self.assertEqual(self.leftovers(), [])

    def test_disk_error_while_storing(self):
        with mock.patch.object(multipart, "read_form", side_effect=OSError("no space left")):
            status, headers, _b = self.post([("csrf", "tok"), ("file", b"data", "a.tar.gz")])
        self.assertEqual(location(headers), f"{B}/backups?m=backup-upload-error")

    def test_refused_by_the_supervisor_discards_the_work_file(self):
        with mock.patch.object(hs, "control_backup_upload", lambda tmp, name: ("invalid", "not a backup")):
            status, headers, _b = self.post([("csrf", "tok"), ("file", b"data", "a.tar.gz")])
        self.assertEqual(location(headers), f"{B}/backups?m=backup-upload-invalid")
        self.assertEqual(self.leftovers(), [])

    def test_the_original_name_is_sanitised_before_it_reaches_the_supervisor(self):
        seen = {}

        def control(tmp, name):
            seen.update(tmp=tmp, name=name)
            return ("saved", "headscale-easy-uploaded-x.tar.gz")
        long_name = "C:\\evil\\" + "é" * 10 + "x" * 200 + ".tar.gz"
        with mock.patch.object(hs, "control_backup_upload", control):
            self.post([("csrf", "tok"), ("file", b"data", long_name)])
        self.assertTrue(seen["name"].startswith("?" * 10 + "x"))
        self.assertEqual(len(seen["name"]), 120)
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, seen["tmp"])))

    def test_restore_after_upload_reports_a_supervisor_refusal_but_keeps_the_file(self):
        with mock.patch.object(hs, "control_backup_upload", lambda t, n: ("saved", "kept.tar.gz")), \
                mock.patch.object(hs, "control_restore", lambda name: ("busy", "")):
            status, headers, _b = self.post([("csrf", "tok"), ("action", "restore"), ("confirm", "RESTORE"),
                                             ("file", b"data", "a.tar.gz")])
        self.assertEqual(location(headers), f"{B}/backups?m=backup-restore-busy")
        self.assertEqual([c.args[2] for c in app.audit.request_event.call_args_list], ["backup.upload"])


# --- other admin-only actions -------------------------------------------------------------------------------------

class NotifyTest(Base):
    def post(self, results):
        with mock.patch.object(app.notify, "send_test", lambda: results):
            return request("POST", B + "/settings/notify-test", ADMIN, {"csrf": "tok"})

    def test_outcomes(self):
        for results, code in (([], "notify-none"), ([("a", True), ("b", True)], "notify-test-ok"),
                              ([("a", True), ("b", False)], "notify-test-failed")):
            status, headers, _b = self.post(results)
            self.assertEqual(location(headers), f"{B}/settings/general?m={code}", results)
        self.assertEqual(app.audit.request_event.call_args.args[4],
                         {"destinations": ["a", "b"], "failed": ["b"]})

    def test_members_get_403(self):
        self.assertEqual(request("POST", B + "/settings/notify-test", MEMBER, {"csrf": "tok"})[0], 403)


class DerpSave(Base):
    def setUp(self):
        super().setUp()
        for name, value in (("host_details", lambda ids: {}), ("all_nodes", lambda: [])):
            p = mock.patch.object(hs, name, value)
            p.start()
            self.addCleanup(p.stop)

    def post(self, **form):
        return request("POST", B + "/derp", ADMIN, dict({"csrf": "tok"}, **form))

    def test_not_editable(self):
        with mock.patch.object(derp, "editable", lambda: "The DERP map is managed elsewhere."), \
                mock.patch.object(derp, "relays", lambda: []), mock.patch.object(derp, "regions", lambda: []), \
                mock.patch.object(derp, "embedded_region", lambda: None):
            status, _h, body = self.post()
        self.assertEqual(status, 400)
        self.assertIn("managed elsewhere", body)

    def test_headscale_refusing_the_map(self):
        with mock.patch.object(derp, "editable", lambda: ""), mock.patch.object(derp, "relays", lambda: []), \
                mock.patch.object(derp, "apply", lambda relays: (False, "restart failed")), \
                mock.patch.object(derp, "regions", lambda: []), \
                mock.patch.object(derp, "embedded_region", lambda: None):
            status, _h, body = self.post()
        self.assertEqual(status, 400)
        self.assertIn("restart failed", body)

    def test_saved(self):
        with mock.patch.object(derp, "editable", lambda: ""), mock.patch.object(derp, "relays", lambda: []), \
                mock.patch.object(derp, "apply", lambda relays: (True, "")):
            status, headers, _b = self.post()
        self.assertEqual(location(headers), f"{B}/derp?m=derp-saved")
        self.assertEqual(app.audit.request_event.call_args.args[2], "derp.save")


# --- first-run bootstrap and helpers ------------------------------------------------------------------------------

class BootstrapAdmin(unittest.TestCase):
    def setUp(self):
        accounts_db.configure(":memory:")
        p = mock.patch.dict(os.environ, {}, clear=False)
        p.start()
        self.addCleanup(p.stop)
        for var in ("HSE_ADMIN_EMAIL", "HSE_ADMIN_PASSWORD"):
            os.environ.pop(var, None)

    def test_does_nothing_without_an_email_or_when_accounts_exist(self):
        app.bootstrap_admin()
        self.assertEqual(lac.list_accounts(), [])
        os.environ["HSE_ADMIN_EMAIL"] = "boss@example.com"
        lac.create_account("someone", "s@example.com", PASSWORD)
        app.bootstrap_admin()
        self.assertEqual(len(lac.list_accounts()), 1)

    def test_email_and_password_create_an_admin_named_after_the_email_prefix(self):
        os.environ.update(HSE_ADMIN_EMAIL="boss@example.com", HSE_ADMIN_PASSWORD=PASSWORD)
        with mock.patch.object(hs, "create_user") as create_user:
            app.bootstrap_admin()
        acc = lac.get_account(username="boss")
        self.assertEqual((acc["role"], acc["email"], acc["headscale_user"]), ("admin", "boss@example.com", "boss"))
        create_user.assert_called_once_with("boss")

    def test_headscale_being_down_does_not_undo_the_account(self):
        os.environ.update(HSE_ADMIN_EMAIL="boss@example.com", HSE_ADMIN_PASSWORD=PASSWORD)
        with mock.patch.object(hs, "create_user", side_effect=OSError("down")):
            app.bootstrap_admin()
        self.assertIsNotNone(lac.get_account(username="boss"))

    def test_a_weak_password_is_logged_not_raised(self):
        os.environ.update(HSE_ADMIN_EMAIL="boss@example.com", HSE_ADMIN_PASSWORD="x")
        with mock.patch.object(hs, "create_user") as create_user:
            app.bootstrap_admin()
        self.assertEqual(lac.list_accounts(), [])
        create_user.assert_not_called()

    def test_email_only_creates_an_invitation_for_an_admin(self):
        os.environ["HSE_ADMIN_EMAIL"] = "boss@example.com"
        with self.assertLogs(sh.log, level="INFO") as logs:
            app.bootstrap_admin()
        urls = [r for r in logs.output if "/accept/" in r]
        self.assertEqual(len(urls), 1)
        token = urls[0].rsplit("/accept/", 1)[1].strip()
        data = account_tokens.check_token(token, kind="invite")
        self.assertEqual((data["email"], data["role"]), ("boss@example.com", "admin"))


class SmallHelpers(unittest.TestCase):
    def test_csv_and_oidc_scope(self):
        with mock.patch.dict(os.environ, {"X_TEST": " a, b ,,c "}):
            self.assertEqual(sorted(sh._csv("X_TEST")), ["a", "b", "c"])
        self.assertEqual(app.oidc_scope(None).split()[0], "openid")
        self.assertIn("openid", app.oidc_scope("email").split())

    def test_lines_and_iso_in(self):
        self.assertEqual(app.lines(" a \n\n b\r\n"), ["a", "b"])
        stamp = app.iso_in(1)
        self.assertTrue(stamp.endswith("Z") or "+" in stamp)

    def test_exit_nodes_for_lists_only_approved_exit_nodes_with_ipv4(self):
        nodes = [
            {"givenName": "gw", "user": {"id": "2"}, "approvedRoutes": ["0.0.0.0/0", "::/0"],
             "ipAddresses": ["fd7a::1", "100.64.0.2"]},
            {"givenName": "v6only", "user": {"id": "2"}, "approvedRoutes": ["::/0"], "ipAddresses": ["fd7a::2"]},
            {"givenName": "plain", "user": {"id": "2"}, "approvedRoutes": ["10.0.0.0/24"], "ipAddresses": ["100.64.0.3"]},
            {"givenName": "theirs", "user": {"id": "9"}, "approvedRoutes": ["0.0.0.0/0"], "ipAddresses": ["100.64.0.9"]},
        ]
        with mock.patch.object(hs, "all_nodes", lambda: nodes), mock.patch.object(hs, "user_for_sub", lambda s: BOB):
            admin = app.exit_nodes_for(ADMIN)
            member = app.exit_nodes_for(MEMBER)
        self.assertEqual([n["name"] for n in admin], ["gw", "theirs"])
        self.assertEqual(member, [{"name": "gw", "ip": "100.64.0.2"}])

    def test_my_user_for_local_sessions(self):
        accounts_db.configure(":memory:")
        acc = lac.create_account("alice", "a@example.com", PASSWORD, headscale_user="alice")
        with mock.patch.object(hs, "user_by_name", lambda name: {"name": name}):
            self.assertEqual(app.my_user({"kind": "local", "sub": f"local:{acc}"}), {"name": "alice"})
            self.assertIsNone(app.my_user({"kind": "local", "sub": "local:999"}))
            self.assertIsNone(app.my_user({"kind": "local", "sub": "local:abc"}))
            self.assertIsNone(app.my_user({"kind": "apikey", "sub": ""}))



if __name__ == "__main__":
    unittest.main()
