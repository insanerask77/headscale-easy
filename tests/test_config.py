"""Settings.from_env: the environment parsing rules, tested without touching os.environ.

    python3 -m unittest tests.test_config
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))

from config import Settings, csv_set, oidc_scope  # noqa: E402

MINIMAL = {"PUBLIC_URL": "https://vpn.example.com/", "SESSION_SECRET": "s"}


class SettingsTest(unittest.TestCase):
    def test_defaults(self):
        s = Settings.from_env(MINIMAL)
        self.assertEqual(s.public_url, "https://vpn.example.com")  # trailing slash stripped
        self.assertEqual(s.session_secret, b"s")
        self.assertFalse(s.sso)
        self.assertTrue(s.api_key_login)  # without SSO the API key is the only way in
        self.assertEqual(s.admin_groups, {"vpn-admins"})
        self.assertEqual(s.admin_emails, set())
        self.assertEqual(s.network_admin_groups, set())
        self.assertEqual(s.auditor_groups, set())
        self.assertEqual(s.mfa_required, "admins")
        self.assertEqual(s.oidc_scope, "openid profile email")
        self.assertTrue(s.secure_cookies)
        self.assertEqual(s.server_host, "vpn.example.com")

    def test_required_variables(self):
        with self.assertRaises(KeyError):
            Settings.from_env({"SESSION_SECRET": "s"})
        with self.assertRaises(KeyError):
            Settings.from_env({"PUBLIC_URL": "https://x"})

    def test_sso_needs_issuer_and_client_id(self):
        sso = dict(MINIMAL, OIDC_ISSUER="https://idp", OIDC_CLIENT_ID="c")
        s = Settings.from_env(sso)
        self.assertTrue(s.sso)
        self.assertFalse(s.api_key_login)
        self.assertTrue(Settings.from_env(dict(sso, PORTAL_API_KEY_LOGIN="TRUE")).api_key_login)
        self.assertFalse(Settings.from_env(dict(MINIMAL, OIDC_ISSUER="https://idp")).sso)

    def test_lists_are_trimmed_and_emails_lowercased(self):
        s = Settings.from_env(dict(MINIMAL, PORTAL_ADMIN_GROUPS=" a, b ,,", PORTAL_ADMIN_EMAILS="Root@Example.com"))
        self.assertEqual(s.admin_groups, {"a", "b"})
        self.assertEqual(s.admin_emails, {"root@example.com"})

    def test_unknown_mfa_mode_falls_back_to_admins(self):
        self.assertEqual(Settings.from_env(dict(MINIMAL, MFA_REQUIRED="everyone")).mfa_required, "everyone")
        self.assertEqual(Settings.from_env(dict(MINIMAL, MFA_REQUIRED="nope")).mfa_required, "admins")

    def test_http_url_has_no_secure_cookies(self):
        self.assertFalse(Settings.from_env(dict(MINIMAL, PUBLIC_URL="http://localhost")).secure_cookies)

    def test_helpers(self):
        self.assertEqual(csv_set({"X": "a,b"}, "X"), {"a", "b"})
        self.assertEqual(csv_set({}, "X", "d"), {"d"})
        self.assertEqual(oidc_scope("email email  groups\n"), "openid email groups")


if __name__ == "__main__":
    unittest.main()
