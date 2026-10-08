"""Revocable sessions and sign-in rate limiting: a revoked sid is rejected,
cookies without sid are not accepted, sign out everywhere, role changes revoke
older sessions, the sessions page and the 429 on repeated failed sign-ins
(with a mocked clock). Standard library only:

    python3 tests/test_sessions.py
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_security import ADMIN, MEMBER, B, Base, app, audit, hs, location, request, sessions, sh  # noqa: E402


def live(data: dict) -> dict:
    """data with a freshly registered sid."""
    return dict(data, sid=sessions.create(data))


def cookie(s: dict) -> dict:
    return {"Cookie": f"hse_session={app.sign(s)}"}


class Store(unittest.TestCase):
    def setUp(self):
        sessions.configure(":memory:")
        sessions._hits.clear()

    def test_validate_and_revoke(self):
        s = live(ADMIN)
        self.assertTrue(sessions.validate(s))
        self.assertTrue(sessions.revoke(s["sid"]))
        self.assertFalse(sessions.validate(s))

    def test_cookie_without_sid_or_unknown_sid(self):
        self.assertFalse(sessions.validate(dict(ADMIN)))
        self.assertFalse(sessions.validate(dict(ADMIN, sid="nope")))

    def test_expired_session(self):
        s = live(dict(ADMIN, exp=time.time() - 5))
        self.assertFalse(sessions.validate(s))

    def test_role_change_revokes_older_sessions(self):
        old = live(dict(MEMBER, role="member"))
        new = live(dict(MEMBER, admin=True, role="admin"))
        self.assertFalse(sessions.validate(old))
        self.assertTrue(sessions.validate(new))

    def test_role_mismatch_in_cookie(self):
        s = live(dict(MEMBER, role="member"))
        self.assertFalse(sessions.validate(dict(s, role="admin", admin=True)))

    def test_revoke_user_keeps_other_users(self):
        a, b = live(ADMIN), live(MEMBER)
        self.assertEqual(sessions.revoke_user("a"), 1)
        self.assertFalse(sessions.validate(a))
        self.assertTrue(sessions.validate(b))

    def test_lists(self):
        a, a2, b = live(ADMIN), live(ADMIN), live(MEMBER)
        self.assertEqual({r["sid"] for r in sessions.list_for(a)}, {a["sid"], a2["sid"]})
        self.assertEqual(len(sessions.list_all()), 3)
        sessions.revoke(b["sid"])
        self.assertEqual(len(sessions.list_all()), 2)

    def test_old_rows_are_purged(self):
        s = live(ADMIN)
        sessions.revoke(s["sid"])
        with mock.patch.object(sessions.time, "time", return_value=time.time() + sessions.PURGE_AFTER + 10):
            sessions.create(MEMBER)
        with sessions._lock:
            n = sessions._db().execute("SELECT COUNT(*) FROM sessions WHERE sid = ?", (s["sid"],)).fetchone()[0]
        self.assertEqual(n, 0)


class RateLimiter(unittest.TestCase):
    def setUp(self):
        sessions._hits.clear()

    def test_blocks_after_limit_and_resets_after_window(self):
        t = [1000.0]
        with mock.patch.object(sessions.time, "time", lambda: t[0]):
            for _ in range(sessions.LIMIT):
                self.assertEqual(sessions.blocked("k"), 0)
                sessions.hit("k")
            self.assertGreater(sessions.blocked("k"), 0)
            self.assertEqual(sessions.blocked("other"), 0)
            t[0] += sessions.WINDOW + 1
            self.assertEqual(sessions.blocked("k"), 0)

    def test_window_slides(self):
        t = [0.0]
        with mock.patch.object(sessions.time, "time", lambda: t[0]):
            for i in range(sessions.LIMIT):
                t[0] = i * 10
                sessions.hit("k")
            self.assertGreater(sessions.blocked("k"), 0)
            t[0] = sessions.WINDOW + 1  # only the first hit has left the window
            self.assertEqual(sessions.blocked("k"), 0)

    def test_reset(self):
        for _ in range(sessions.LIMIT):
            sessions.hit("k")
        sessions.reset("k")
        self.assertEqual(sessions.blocked("k"), 0)


class Requests(Base):
    def setUp(self):
        super().setUp()
        sessions.configure(":memory:")
        sessions._hits.clear()
        for name in ("all_users", "all_nodes"):
            p = mock.patch.object(hs, name, lambda: [])
            p.start()
            self.addCleanup(p.stop)

    def test_revoked_session_is_rejected(self):
        s = live(ADMIN)
        self.assertEqual(request("GET", f"{B}/settings/sessions", headers=cookie(s))[0], 200)
        sessions.revoke(s["sid"])
        status, headers, _ = request("GET", f"{B}/settings/sessions", headers=cookie(s))
        self.assertEqual((status, location(headers)), (303, f"{B}/login"))
        status, headers, _ = request("POST", f"{B}/settings/language", headers=cookie(s), form={"csrf": "tok"})
        self.assertEqual((status, location(headers)), (303, f"{B}/login"))

    def test_cookie_without_sid_needs_login(self):
        status, headers, _ = request("GET", f"{B}/machines", headers={"Cookie": f"hse_session={app.sign(ADMIN)}"})
        self.assertEqual((status, location(headers)), (303, f"{B}/login"))

    def test_logout_revokes_the_session(self):
        s = live(ADMIN)
        status, _, _ = request("POST", f"{B}/logout", headers=cookie(s), form={"csrf": "tok"})
        self.assertEqual(status, 303)
        self.assertFalse(sessions.validate(s))

    def test_sign_out_everywhere(self):
        s1, s2, other = live(MEMBER), live(MEMBER), live(ADMIN)
        status, headers, _ = request("POST", f"{B}/settings/sessions/revoke-all", headers=cookie(s1),
                                     form={"csrf": "tok"})
        self.assertEqual((status, location(headers)), (303, f"{B}/login?m=signed-out"))
        self.assertFalse(sessions.validate(s1))
        self.assertFalse(sessions.validate(s2))
        self.assertTrue(sessions.validate(other))

    def test_member_cannot_revoke_someone_elses_session(self):
        admin, member = live(ADMIN), live(MEMBER)
        _, headers, _ = request("POST", f"{B}/settings/sessions/revoke", headers=cookie(member),
                                form={"csrf": "tok", "sid": admin["sid"]})
        self.assertIn("session-not-found", location(headers))
        self.assertTrue(sessions.validate(admin))

    def test_admin_revokes_a_member_session(self):
        admin, member = live(ADMIN), live(MEMBER)
        _, headers, _ = request("POST", f"{B}/settings/sessions/revoke", headers=cookie(admin),
                                form={"csrf": "tok", "sid": member["sid"]})
        self.assertIn("session-revoked", location(headers))
        self.assertFalse(sessions.validate(member))
        self.assertTrue(sessions.validate(admin))

    def test_everyone_scope_is_admin_only(self):
        admin, member = live(ADMIN), live(MEMBER)
        status, _, _ = request("POST", f"{B}/settings/sessions/revoke-all", headers=cookie(member),
                               form={"csrf": "tok", "scope": "everyone"})
        self.assertEqual(status, 403)
        self.assertTrue(sessions.validate(admin))
        request("POST", f"{B}/settings/sessions/revoke-all", headers=cookie(admin),
                form={"csrf": "tok", "scope": "everyone"})
        self.assertFalse(sessions.validate(member))
        self.assertTrue(sessions.validate(admin))

    def test_demo_blocks_session_actions(self):
        s = live(ADMIN)
        with mock.patch.object(sh, "DEMO", True):
            status, _, _ = request("POST", f"{B}/settings/sessions/revoke-all", headers=cookie(s),
                                   form={"csrf": "tok"})
        self.assertEqual(status, 403)
        self.assertTrue(sessions.validate(s))

    def test_deleting_a_user_revokes_their_sessions(self):
        member = live(dict(MEMBER, username="bob"))
        admin = live(ADMIN)
        self.api.side_effect = None
        with mock.patch.object(hs, "all_users", lambda: [{"id": "2", "name": "bob"}]):
            request("POST", f"{B}/users/2/delete", headers=cookie(admin), form={"csrf": "tok"})
        self.assertFalse(sessions.validate(member))

    def test_sign_in_registers_a_session(self):
        with mock.patch.object(sh, "API_KEY_LOGIN", True), mock.patch.object(hs, "http_json", lambda *a, **k: {}):
            status, headers, _ = request("POST", f"{B}/login/apikey", form={"api_key": "hskey-api-abcdefghijkl-xyz"})
        self.assertEqual(status, 303)
        value = next(c for c in headers["set-cookie"] if c.startswith("hse_session=")).split(";")[0].split("=", 1)[1]
        self.assertTrue(sessions.validate(app.unsign(value)))


class SignInRateLimit(Base):
    def setUp(self):
        super().setUp()
        sessions.configure(":memory:")
        sessions._hits.clear()
        for p in (mock.patch.object(sh, "API_KEY_LOGIN", True), mock.patch.object(sh, "SSO", True),
                  mock.patch.object(app.time, "sleep", lambda s: None),
                  mock.patch.object(hs, "http_json", mock.Mock(side_effect=OSError("denied")))):
            p.start()
            self.addCleanup(p.stop)

    def bad_login(self):
        return request("POST", f"{B}/login/apikey", form={"api_key": "hskey-api-wrong"})

    def test_apikey_login_is_limited_then_recovers(self):
        t = [5000.0]
        with mock.patch.object(sessions.time, "time", lambda: t[0]):
            for _ in range(sessions.LIMIT):
                self.assertEqual(self.bad_login()[0], 401)
            status, headers, body = self.bad_login()
            self.assertEqual(status, 429)
            self.assertIn("retry-after", headers)
            self.assertIn("Too many sign-in attempts", body)
            actions = [c.args[2] for c in audit.request_event.call_args_list]
            self.assertIn("auth.rate_limited", actions)
            t[0] += sessions.WINDOW + 1
            self.assertEqual(self.bad_login()[0], 401)

    def test_other_ip_is_not_blocked(self):
        for _ in range(sessions.LIMIT):
            self.bad_login()
        self.assertEqual(self.bad_login()[0], 429)
        with mock.patch.object(audit, "client_ip", lambda h: "203.0.113.9"):
            self.assertEqual(self.bad_login()[0], 401)

    def test_start_sso_is_limited(self):
        with mock.patch.object(sh, "discovery", lambda: {"authorization_endpoint": "https://idp/auth"}):
            for _ in range(sessions.LIMIT):
                self.assertEqual(request("GET", f"{B}/login/sso")[0], 303)
            status, headers, _ = request("GET", f"{B}/login/sso")
        self.assertEqual(status, 429)
        self.assertIn("retry-after", headers)

    def test_callback_failures_are_limited(self):
        for _ in range(sessions.LIMIT):
            self.assertEqual(request("GET", f"{B}/callback?code=c&state=bad")[0], 400)
        self.assertEqual(request("GET", f"{B}/callback?code=c&state=bad")[0], 429)


if __name__ == "__main__":
    unittest.main()
