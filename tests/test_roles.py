"""Network admin and Auditor roles (roadmap #12). Standard library only; no
Headscale needed:

    python3 tests/test_roles.py
"""
import os
import sys
import unittest

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
os.environ.update(HEADSCALE_API_KEY="x", PUBLIC_URL="https://vpn.example.com", SESSION_SECRET="s",
                  HEADSCALE_URL="http://headscale:8080")
sys.path.insert(0, WEB)

import app  # noqa: E402
import ui  # noqa: E402
from i18n import set_lang  # noqa: E402


class RoleOfTests(unittest.TestCase):
    """app's module-level ADMIN_GROUPS etc. are read from the environment at
    import time, and another test module may have imported app first (with
    its own, unrelated env) -- so these are set directly here, the same way
    test_dns.py repoints hs.HEADSCALE_CONFIG in its own setUp."""

    def setUp(self):
        app.ADMIN_GROUPS = {"vpn-admins"}
        app.ADMIN_EMAILS = {"root@example.com"}
        app.NETWORK_ADMIN_GROUPS = {"vpn-network-admins"}
        app.AUDITOR_GROUPS = {"vpn-auditors"}

    def test_admin_by_group(self):
        self.assertEqual(app.role_of(["vpn-admins"], "alice@example.com"), "admin")

    def test_admin_by_email_wins_over_no_group(self):
        self.assertEqual(app.role_of([], "root@example.com"), "admin")

    def test_admin_takes_priority_over_other_roles(self):
        # in both vpn-admins and vpn-auditors: admin wins
        self.assertEqual(app.role_of(["vpn-admins", "vpn-auditors"], ""), "admin")

    def test_network_admin_by_group(self):
        self.assertEqual(app.role_of(["vpn-network-admins"], "bob@example.com"), "network_admin")

    def test_auditor_by_group(self):
        self.assertEqual(app.role_of(["vpn-auditors"], "carol@example.com"), "auditor")

    def test_network_admin_takes_priority_over_auditor(self):
        self.assertEqual(app.role_of(["vpn-network-admins", "vpn-auditors"], ""), "network_admin")

    def test_no_matching_group_or_email_is_member(self):
        self.assertEqual(app.role_of(["some-other-group"], "nobody@example.com"), "member")

    def test_empty_groups_and_email_is_member(self):
        self.assertEqual(app.role_of([], ""), "member")


class PermissionHelperTests(unittest.TestCase):
    def test_can_edit_network(self):
        self.assertTrue(app.can_edit_network({"admin": True}))
        self.assertTrue(app.can_edit_network({"admin": False, "role": "network_admin"}))
        self.assertFalse(app.can_edit_network({"admin": False, "role": "auditor"}))
        self.assertFalse(app.can_edit_network({"admin": False, "role": "member"}))
        self.assertFalse(app.can_edit_network({}))

    def test_is_auditor(self):
        self.assertTrue(app.is_auditor({"role": "auditor"}))
        self.assertFalse(app.is_auditor({"role": "admin", "admin": True}))
        self.assertFalse(app.is_auditor({}))


class VisibilityTests(unittest.TestCase):
    """visible_nodes()/node_for() broaden to auditors for reads only; writes
    are blocked separately in machine_action() regardless of what these
    return (see test_bulk.py-style tests would live in a future
    machine_action test; here we only check the read-path helpers)."""

    def setUp(self):
        self._all_nodes = app.hs.all_nodes
        self._get_node = app.hs.get_node
        self._owned_node = app.hs.owned_node
        self._user_nodes = app.hs.user_nodes
        app.hs.all_nodes = lambda: [{"id": 1}, {"id": 2}]
        app.hs.get_node = lambda node_id: {"id": int(node_id)}
        app.hs.owned_node = lambda user, node_id: None
        app.hs.user_nodes = lambda user: []

    def tearDown(self):
        app.hs.all_nodes = self._all_nodes
        app.hs.get_node = self._get_node
        app.hs.owned_node = self._owned_node
        app.hs.user_nodes = self._user_nodes

    def test_auditor_sees_all_nodes(self):
        self.assertEqual(app.visible_nodes({"role": "auditor"}), [{"id": 1}, {"id": 2}])
        self.assertEqual(app.node_for({"role": "auditor"}, "5"), {"id": 5})

    def test_member_only_sees_owned_nodes(self):
        self.assertEqual(app.visible_nodes({"role": "member"}), [])
        self.assertIsNone(app.node_for({"role": "member"}, "5"))

    def test_network_admin_is_not_broadened(self):
        # explicitly scoped to ACL + DNS only, per the roadmap
        self.assertEqual(app.visible_nodes({"role": "network_admin"}), [])
        self.assertIsNone(app.node_for({"role": "network_admin"}, "5"))


class SidebarRenderTests(unittest.TestCase):
    def setUp(self):
        set_lang("en")

    def test_network_admin_sees_acl_but_not_users_or_logs(self):
        html = ui.sidebar("acl", {"admin": False, "role": "network_admin", "csrf": "t"}, {})
        self.assertIn(f'{ui.BASE}/acl', html)
        self.assertNotIn(f'href="{ui.BASE}/users"', html)
        self.assertNotIn(f'href="{ui.BASE}/logs"', html)
        self.assertIn("Network admin", html)

    def test_auditor_sees_users_logs_and_acl(self):
        html = ui.sidebar("machines", {"admin": False, "role": "auditor", "csrf": "t"}, {})
        self.assertIn(f'href="{ui.BASE}/users"', html)
        self.assertIn(f'href="{ui.BASE}/logs"', html)
        self.assertIn(f'{ui.BASE}/acl', html)
        self.assertIn("Auditor", html)

    def test_member_sees_none_of_the_elevated_nav(self):
        html = ui.sidebar("machines", {"admin": False, "role": "member", "csrf": "t"}, {})
        self.assertNotIn(f'href="{ui.BASE}/users"', html)
        self.assertNotIn(f'href="{ui.BASE}/logs"', html)
        self.assertNotIn(f'{ui.BASE}/acl', html)

    def test_session_without_role_key_falls_back_to_admin_bool(self):
        html = ui.sidebar("machines", {"admin": True, "csrf": "t"}, {})
        self.assertIn(f'href="{ui.BASE}/users"', html)
        self.assertIn("Admin", html)


if __name__ == "__main__":
    unittest.main(verbosity=1)
