"""Machines: routes waiting for approval are visible and approvable in one click."""
import os
import sys
import unittest
from pathlib import Path

os.environ.update(HEADSCALE_API_KEY="x", PUBLIC_URL="https://vpn.example.com", SESSION_SECRET="s",
                  HEADSCALE_URL="http://headscale:8080")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

import machines_pages  # noqa: E402
from i18n import set_lang  # noqa: E402

ADMIN = {"admin": True, "username": "alice", "csrf": "tok"}
MEMBER = {"admin": False, "username": "bob", "csrf": "tok"}
CTX = {"public_url": "https://vpn.example.com", "tailnet": "", "server_host": "vpn.example.com"}


def machine(node_id, available=(), approved=()):
    node = {"id": node_id, "givenName": f"n{node_id}", "name": f"n{node_id}", "user": {"id": "1", "name": "alice"},
            "online": True, "ipAddresses": ["100.64.0.1"], "tags": [],
            "availableRoutes": list(available), "approvedRoutes": list(approved)}
    return machines_pages.Machine(node, None, {}, "", {})


class PendingRoutesTests(unittest.TestCase):
    def setUp(self):
        set_lang("en")

    def test_exit_node_pending_badge_text(self):
        m = machine("1", ["0.0.0.0/0", "::/0"])
        self.assertTrue(m.routes_pending)
        self.assertIn("Exit Node - pending approval", m.badges())

    def test_subnet_pending_badge_text(self):
        m = machine("2", ["10.0.0.0/24"])
        self.assertIn("Subnets - pending approval", m.badges())

    def test_approved_routes_have_no_pending_text(self):
        m = machine("3", ["0.0.0.0/0", "10.0.0.0/24"], ["0.0.0.0/0", "10.0.0.0/24"])
        self.assertFalse(m.routes_pending)
        self.assertNotIn("pending approval", m.badges())

    def test_list_menu_has_approve_for_admin_only_when_pending(self):
        pending, ok = machine("4", ["10.0.0.0/24"]), machine("5", ["10.0.0.0/24"], ["10.0.0.0/24"])
        self.assertIn("/machines/4/approve-routes", machines_pages.machine_menu(pending, ADMIN))
        self.assertNotIn("approve-routes", machines_pages.machine_menu(ok, ADMIN))
        self.assertNotIn("approve-routes", machines_pages.machine_menu(pending, MEMBER))

    def test_detail_routes_section_has_approve_button(self):
        html = machines_pages.routes_section(machine("6", ["0.0.0.0/0"]), ADMIN)
        self.assertIn("/machines/6/approve-routes", html)
        self.assertIn('value="machines/6"', html)
        ok = machines_pages.routes_section(machine("7", ["0.0.0.0/0"], ["0.0.0.0/0"]), ADMIN)
        self.assertNotIn("approve-routes", ok)

    def test_banner_counts_machines(self):
        ms = [machine("8", ["0.0.0.0/0"]), machine("9", ["10.0.0.0/24"]), machine("10", ["10.0.0.0/24"], ["10.0.0.0/24"])]
        self.assertIn("2 machines have routes waiting for approval.", machines_pages.pending_routes_banner(ms))
        self.assertIn("1 machine has routes waiting", machines_pages.pending_routes_banner(ms[:1]))
        self.assertEqual(machines_pages.pending_routes_banner(ms[2:]), "")

    def test_banner_only_on_admin_machines_page(self):
        ms = [machine("11", ["0.0.0.0/0"])]
        self.assertIn("data-pending-routes", machines_pages.machines_page(ADMIN, CTX, ms, True, ""))
        self.assertNotIn("data-pending-routes", machines_pages.machines_page(MEMBER, CTX, ms, True, ""))


if __name__ == "__main__":
    unittest.main()
