"""Accounts made in the console and self-registration: users created with a
password, password reset, the forced change of a temporary password, and the
three sign-up modes (off / invitation key / open). Requests go through the real
request handler; Headscale and the audit log are fakes. Standard library only:

    python3 tests/test_signup.py
"""
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_security import ADMIN, B, Base, MEMBER, app, audit, hs, location, request, sessions  # noqa: E402

import local_accounts as lac  # noqa: E402
import account_tokens  # noqa: E402
import accounts_db  # noqa: E402
import signup  # noqa: E402

PUBLIC = {"Cookie": "hse_signup=tok123"}
GOOD = {"csrf": "tok123", "username": "newbie", "email": "new@example.com",
        "password": "a-long-password", "password2": "a-long-password"}


class SignupBase(Base):
    def setUp(self):
        super().setUp()
        accounts_db.configure(":memory:")
        sessions.configure(":memory:")
        sessions._hits.clear()
        self.tmp = tempfile.mkdtemp()
        p = mock.patch.object(signup, "MODE_FILE", os.path.join(self.tmp, "signup-mode"))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(signup, "START", "off")
        p.start()
        self.addCleanup(p.stop)
        self.api.side_effect = None
        self.api.return_value = {}

    def mode(self, value):
        signup.set_mode(value)

    def hs_calls(self):
        return [c.args[:2] for c in self.api.call_args_list]


class Modes(SignupBase):
    def test_default_is_off_and_404(self):
        self.assertEqual(signup.mode(), "off")
        self.assertEqual(request("GET", f"{B}/signup")[0], 404)
        self.assertEqual(request("POST", f"{B}/signup", form=GOOD, headers=PUBLIC)[0], 404)
        self.assertEqual(lac.list_accounts(), [])
        _, _, body = request("GET", f"{B}/login")
        self.assertNotIn(f"{B}/signup", body)

    def test_garbage_in_the_mode_file_means_off(self):
        with open(signup.MODE_FILE, "w") as fh:
            fh.write("everyone\n")
        self.assertEqual(signup.mode(), "off")

    def test_login_page_links_to_signup_when_on(self):
        for mode in ("open", "invite"):
            self.mode(mode)
            self.assertIn(f"{B}/signup", request("GET", f"{B}/login")[2])

    def test_form_has_key_field_only_in_invite_mode(self):
        self.mode("open")
        status, headers, body = request("GET", f"{B}/signup")
        self.assertEqual(status, 200)
        self.assertNotIn('name="key"', body)
        self.assertIn("hse_signup=", headers["set-cookie"][0])
        self.mode("invite")
        self.assertIn('name="key"', request("GET", f"{B}/signup")[2])


class OpenSignup(SignupBase):
    def setUp(self):
        super().setUp()
        self.mode("open")

    def test_creates_a_member_never_an_admin(self):
        status, headers, _ = request("POST", f"{B}/signup", form=dict(GOOD, role="admin", admin="1"), headers=PUBLIC)
        self.assertEqual((status, location(headers)), (303, f"{B}/login?m=signed-up"))
        account = lac.get_account(username="newbie")
        self.assertEqual((account["role"], account["headscale_user"], account["must_change"]), ("member", "newbie", 0))
        self.assertIn(("POST", "/user"), self.hs_calls())
        self.assertTrue(lac.verify_password("a-long-password", account["pw_hash"]))

    def test_needs_the_csrf_cookie(self):
        self.assertEqual(request("POST", f"{B}/signup", form=GOOD)[0], 403)
        self.assertEqual(request("POST", f"{B}/signup", form=dict(GOOD, csrf="other"), headers=PUBLIC)[0], 403)
        self.assertEqual(lac.list_accounts(), [])

    def test_validation(self):
        for change in ({"username": "ab"}, {"username": "has space"}, {"email": "nope"},
                       {"password": "short", "password2": "short"}, {"password2": "different-password"}):
            status, _, body = request("POST", f"{B}/signup", form=dict(GOOD, **change), headers=PUBLIC)
            self.assertEqual(status, 400, change)
        self.assertEqual(lac.list_accounts(), [])
        self.assertEqual(self.hs_calls(), [])

    def test_taken_username_or_email(self):
        lac.create_account("newbie", "other@example.com", "a-long-password")
        for change in ({}, {"username": "different", "email": "other@example.com"}):
            status, _, body = request("POST", f"{B}/signup", form=dict(GOOD, **change), headers=PUBLIC)
            self.assertEqual(status, 400)
            self.assertIn("already in use", body)

    def test_headscale_failure_creates_no_account(self):
        self.api.side_effect = RuntimeError("down")
        self.assertEqual(request("POST", f"{B}/signup", form=GOOD, headers=PUBLIC)[0], 400)
        self.assertEqual(lac.list_accounts(), [])

    def test_rate_limit(self):
        codes = [request("POST", f"{B}/signup", form=dict(GOOD, username=f"user{i}", email=f"u{i}@example.com"),
                         headers=PUBLIC)[0] for i in range(sessions.LIMIT + 2)]
        self.assertEqual(codes[:sessions.LIMIT], [303] * sessions.LIMIT)
        self.assertEqual(codes[-1], 429)

    def test_audit_has_no_password(self):
        request("POST", f"{B}/signup", form=GOOD, headers=PUBLIC)
        self.assertTrue(audit.request_event.called)
        self.assertNotIn("a-long-password", repr(audit.request_event.call_args_list))


class InviteSignup(SignupBase):
    def setUp(self):
        super().setUp()
        self.mode("invite")

    def go(self, key, **change):
        return request("POST", f"{B}/signup", form=dict(GOOD, key=key, **change), headers=PUBLIC)

    def test_valid_key_works_once(self):
        key = account_tokens.create_signup_key("friends")
        self.assertEqual(self.go(key)[0], 303)
        status, _, body = self.go(key, username="second", email="s@example.com")
        self.assertEqual(status, 400)
        self.assertIn("invitation key is not valid", body)
        self.assertEqual([a["username"] for a in lac.list_accounts()], ["newbie"])

    def test_missing_wrong_revoked_expired_give_the_same_error(self):
        revoked = account_tokens.create_signup_key()
        account_tokens.revoke_signup_key(account_tokens.list_signup_keys()[0]["id"])
        expired = account_tokens.create_signup_key(expires_hours=1)
        with accounts_db._db() as db:
            db.execute("UPDATE signup_keys SET expires = '2000-01-01T00:00:00+00:00' WHERE key_hash = ?",
                       (account_tokens._hash_token(expired),))
        bodies = set()
        for key in ("", "hse-wrong", revoked, expired):
            status, _, body = self.go(key)
            self.assertEqual(status, 400, key)
            bodies.add(re.sub(r'value="[^"]*"', "", body).replace("  ", " "))
        self.assertEqual(len(bodies), 1)
        self.assertEqual(lac.list_accounts(), [])
        self.assertEqual(self.hs_calls(), [])

    def test_a_key_is_given_back_when_the_name_is_taken(self):
        key = account_tokens.create_signup_key()
        lac.create_account("newbie", "x@example.com", "a-long-password")
        self.assertEqual(self.go(key)[0], 400)
        self.assertEqual(self.go(key, username="free", email="free@example.com")[0], 303)

    def test_multi_use_key(self):
        key = account_tokens.create_signup_key(max_uses=2)
        codes = [self.go(key, username=f"user{i}", email=f"u{i}@example.com")[0] for i in range(3)]
        self.assertEqual(codes, [303, 303, 400])


class AdminSide(SignupBase):
    def test_only_admins_manage_keys_and_the_mode(self):
        for path, form in ((f"{B}/signup/keys", {"uses": "1", "days": "7"}), (f"{B}/settings/signup", {"mode": "open"}),
                           (f"{B}/signup/keys/1/revoke", {}), (f"{B}/users/1/password", {"password": "a-long-password"})):
            self.assertEqual(request("POST", path, MEMBER, dict(form, csrf="tok"))[0], 403, path)
        self.assertEqual(signup.mode(), "off")
        self.assertEqual(account_tokens.list_signup_keys(), [])

    def test_admin_sets_the_mode(self):
        status, headers, _ = request("POST", f"{B}/settings/signup", ADMIN, {"csrf": "tok", "mode": "invite"})
        self.assertEqual((status, location(headers)), (303, f"{B}/settings/general?m=signup-saved"))
        self.assertEqual(signup.mode(), "invite")
        request("POST", f"{B}/settings/signup", ADMIN, {"csrf": "tok", "mode": "bogus"})
        self.assertEqual(signup.mode(), "invite")
        self.assertIn('name="mode" value="invite" checked', request("GET", f"{B}/settings/general", ADMIN)[2])
        self.assertNotIn('name="mode" value="invite"', request("GET", f"{B}/settings/general", MEMBER)[2])

    def test_key_is_shown_once_and_stored_hashed(self):
        self.api.return_value = []
        with mock.patch.object(hs, "all_users", lambda: []), mock.patch.object(hs, "all_nodes", lambda: []):
            status, _, body = request("POST", f"{B}/signup/keys", ADMIN,
                                      {"csrf": "tok", "label": "team", "uses": "5", "days": "7"})
            self.assertEqual(status, 200)
            key = re.search(r"<code>(hse-[\w-]+)</code>", body).group(1)
            self.assertNotIn(key, request("GET", f"{B}/users", ADMIN)[2])
        self.assertEqual(account_tokens.list_signup_keys()[0]["max_uses"], 5)
        self.assertIsNotNone(account_tokens.use_signup_key(key))
        self.assertNotIn(key, repr(audit.request_event.call_args_list))

    def test_revoke_key(self):
        key = account_tokens.create_signup_key()
        key_id = account_tokens.list_signup_keys()[0]["id"]
        request("POST", f"{B}/signup/keys/{key_id}/revoke", ADMIN, {"csrf": "tok"})
        self.assertIsNone(account_tokens.use_signup_key(key))


class CreateUsersWithPasswords(SignupBase):
    def create(self, **extra):
        users = [{"id": "9", "name": "carol"}]
        with mock.patch.object(hs, "all_users", lambda: users), mock.patch.object(hs, "all_nodes", lambda: []):
            return request("POST", f"{B}/users", ADMIN, dict({"csrf": "tok", "name": "carol", "email": "c@example.com",
                           "password": "temporary-pass", "role": "auditor", "must_change": "1"}, **extra))

    def test_creates_account_and_headscale_user(self):
        status, headers, _ = self.create()
        self.assertEqual((status, location(headers)), (303, f"{B}/users?m=user-created"))
        a = lac.get_account(username="carol")
        self.assertEqual((a["role"], a["must_change"], a["headscale_user"]), ("auditor", 1, "carol"))
        self.assertNotIn("temporary-pass", repr(audit.request_event.call_args_list))

    def test_without_a_password_it_is_only_a_headscale_user(self):
        status, headers, _ = self.create(password="")
        self.assertEqual(status, 303)
        self.assertEqual(lac.list_accounts(), [])

    def test_validation_and_duplicates(self):
        for extra in ({"email": "nope"}, {"password": "short"}, {"role": "root"}, {"name": "has.dot"}):
            self.assertEqual(self.create(**extra)[0], 400, extra)
        self.assertEqual(lac.list_accounts(), [])
        lac.create_account("dave", "c@example.com", "a-long-password")
        self.assertEqual(self.create()[0], 400)  # email taken
        self.assertNotIn(("POST", "/user"), self.hs_calls())

    def test_failed_account_removes_the_headscale_user(self):
        with mock.patch.object(lac, "create_account", side_effect=ValueError("boom")):
            self.assertEqual(self.create()[0], 400)
        self.assertIn(("DELETE", "/user/9"), self.hs_calls())

    def test_set_password_signs_the_person_out_and_forces_a_change(self):
        account_id = lac.create_account("carol", "c@example.com", "old-password-1", headscale_user="carol")
        victim = sessions.create({"kind": "local", "sub": f"local:{account_id}", "username": "carol", "name": "c",
                                  "admin": False, "csrf": "t"})
        users = [{"id": "9", "name": "carol"}]
        with mock.patch.object(hs, "all_users", lambda: users):
            status, headers, _ = request("POST", f"{B}/users/9/password", ADMIN,
                                         {"csrf": "tok", "password": "brand-new-pass", "must_change": "1"})
        self.assertEqual((status, location(headers)), (303, f"{B}/users?m=password-set"))
        a = lac.get_account(id=account_id)
        self.assertTrue(lac.verify_password("brand-new-pass", a["pw_hash"]))
        self.assertEqual(a["must_change"], 1)
        self.assertFalse(sessions.validate({"sid": victim, "sub": f"local:{account_id}", "exp": 9e12}))
        self.assertNotIn("brand-new-pass", repr(audit.request_event.call_args_list))

    def test_set_password_for_a_user_without_an_account(self):
        with mock.patch.object(hs, "all_users", lambda: [{"id": "9", "name": "servers"}]):
            _, headers, _ = request("POST", f"{B}/users/9/password", ADMIN, {"csrf": "tok", "password": "brand-new-pass"})
        self.assertEqual(location(headers), f"{B}/users?m=not-found")


class MustChange(SignupBase):
    def setUp(self):
        super().setUp()
        self.id = lac.create_account("tmp", "t@example.com", "temporary-pass", must_change=True)
        self.session = {"kind": "local", "sub": f"local:{self.id}", "username": "tmp", "name": "t@example.com",
                        "email": "t@example.com", "groups": [], "admin": False, "role": "member", "csrf": "tok",
                        "exp": 9e12}

    def test_everything_but_the_password_page_redirects(self):
        for path in (f"{B}/machines", f"{B}/dns", f"{B}/settings/general", f"{B}/add"):
            status, headers, _ = request("GET", path, self.session)
            self.assertEqual((status, location(headers)), (303, f"{B}/settings/account?m=must-change"), path)
        status, _, body = request("GET", f"{B}/settings/account?m=must-change", self.session)
        self.assertEqual(status, 200)
        self.assertIn("Choose a new password", body)
        status, headers, _ = request("POST", f"{B}/keys", self.session, {"csrf": "tok"})
        self.assertEqual(location(headers), f"{B}/settings/account?m=must-change")
        self.assertEqual(request("POST", f"{B}/logout", self.session, {"csrf": "tok"})[0], 303)

    def test_changing_the_password_lifts_it(self):
        status, headers, _ = request("POST", f"{B}/settings/account/password", self.session, {
            "csrf": "tok", "old_password": "temporary-pass", "new_password": "my-own-password",
            "new_password2": "my-own-password"})
        self.assertEqual(location(headers), f"{B}/settings/account?m=password-changed")
        self.assertEqual(lac.get_account(id=self.id)["must_change"], 0)
        with mock.patch.object(hs, "all_nodes", lambda: []), mock.patch.object(hs, "all_users", lambda: []):
            self.assertEqual(request("GET", f"{B}/settings/general", self.session)[0], 200)

    def test_wrong_old_password_keeps_the_block(self):
        request("POST", f"{B}/settings/account/password", self.session, {
            "csrf": "tok", "old_password": "nope", "new_password": "my-own-password", "new_password2": "my-own-password"})
        self.assertEqual(lac.get_account(id=self.id)["must_change"], 1)


if __name__ == "__main__":
    unittest.main()
