"""Invitations and password reset links (web/accounts.py), without Authentik.
Run: python3 -m unittest discover -s tests"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))
os.environ.setdefault("HEADSCALE_API_KEY", "test")
os.environ.setdefault("PUBLIC_URL", "https://vpn.example.com")

import accounts  # noqa: E402

ISSUER = "https://vpn.example.com/authentik/application/o/headscale"


def acct(pk, uid, groups=("headscale-users",), superuser=False, active=True):
    return accounts._account({"pk": pk, "uid": uid, "username": f"u{pk}", "name": f"User {pk}",
                              "email": f"u{pk}@example.com", "is_active": active, "is_superuser": superuser,
                              "groups_obj": [{"name": g} for g in groups]})


class Match(unittest.TestCase):
    def test_matches_by_oidc_subject(self):
        users = [{"id": "1", "providerId": f"{ISSUER}/aaa"}, {"id": "2", "providerId": ""},
                 {"id": "3", "providerId": f"{ISSUER}/zzz"}]
        linked, waiting = accounts.match(users, [acct(10, "aaa"), acct(11, "bbb"), acct(12, "ccc", groups=())])
        self.assertEqual({k: v["pk"] for k, v in linked.items()}, {"1": 10})
        # bbb has no Headscale user yet; ccc has no tailnet group: not listed
        self.assertEqual([a["pk"] for a in waiting], [11])

    def test_superusers_are_protected(self):
        self.assertTrue(acct(1, "x", superuser=True)["protected"])
        self.assertTrue(acct(2, "y", groups=("authentik Admins",))["protected"])
        self.assertFalse(acct(3, "z", groups=("vpn-admins",))["protected"])
        self.assertEqual(accounts.reset_item(acct(1, "x", superuser=True)), "")
        self.assertEqual(accounts.reset_item(acct(4, "w", active=False)), "")
        self.assertIn("reset-3", accounts.reset_item(acct(3, "z")))


class Links(unittest.TestCase):
    def test_public_link(self):
        with mock.patch.object(accounts, "PUBLIC_URL", "https://vpn.example.com"):
            self.assertEqual(
                accounts.public_link("http://authentik-server:9000/authentik/if/flow/headscale-easy-recovery/?flow_token=abc"),
                "https://vpn.example.com/authentik/if/flow/headscale-easy-recovery/?flow_token=abc")
            self.assertEqual(accounts.invite_link("u-1"),
                             "https://vpn.example.com/authentik/if/flow/headscale-easy-invitation/?itoken=u-1")


class CreateInvitation(unittest.TestCase):
    def setUp(self):
        accounts._forget()
        self.calls = []

        def api(method, path, body=None):
            self.calls.append((method, path, body))
            if path.startswith("/core/users/"):
                return {"results": [{"pk": 1, "uid": "a", "email": "taken@example.com", "groups_obj": []}]}
            if path.startswith("/flows/instances/"):
                return {"results": [{"slug": accounts.INVITE_FLOW, "pk": "flow-pk"}]}
            return {"pk": "inv-pk", "expires": body["expires"], "fixed_data": body["fixed_data"]}

        patcher = mock.patch.object(accounts, "_api", side_effect=api)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fixes_group_and_email(self):
        inv = accounts.create_invitation("admin", "Ana@Example.com", 7, "boss")
        method, path, body = self.calls[-1]
        self.assertEqual((method, path), ("POST", "/stages/invitation/invitations/"))
        self.assertEqual(body["flow"], "flow-pk")
        self.assertEqual(body["fixed_data"], {"hse_group": "vpn-admins", "hse_invited_by": "boss",
                                              "email": "Ana@Example.com"})
        self.assertRegex(body["name"], r"^hse-ana-[0-9a-f]{6}$")
        self.assertEqual(inv["role"], "admin")

    def test_rejects_bad_input(self):
        for role, email in (("root", ""), ("member", "not-an-email"), ("member", "TAKEN@example.com")):
            with self.assertRaises(accounts.AccountsError):
                accounts.create_invitation(role, email, 7, "boss")


if __name__ == "__main__":
    unittest.main()
