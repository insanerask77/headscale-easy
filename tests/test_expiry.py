"""Unit tests for expiry warnings and inactive machines (web/expiry.py).

Standard library only:  python3 -m unittest discover -s tests
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

import expiry  # noqa: E402
import pages  # noqa: E402

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
NEVER = "0001-01-01T00:00:00Z"  # how Headscale sends "no expiry" / "never seen"


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def node(node_id=1, expiry_in=None, online=False, seen_ago=None, created_ago=400, **extra):
    n = {"id": str(node_id), "name": f"n{node_id}", "givenName": f"n{node_id}", "online": online,
         "user": {"id": "1", "name": "ana"}, "createdAt": iso(NOW - timedelta(days=created_ago)),
         "expiry": iso(NOW + timedelta(days=expiry_in)) if expiry_in is not None else NEVER,
         "lastSeen": iso(NOW - timedelta(days=seen_ago)) if seen_ago is not None else None}
    n.update(extra)
    return n


class ExpiryState(unittest.TestCase):
    def test_states(self):
        self.assertEqual(expiry.expiry_state(node(expiry_in=None), NOW), "disabled")
        self.assertEqual(expiry.expiry_state(node(expiry_in=-1), NOW), "expired")
        self.assertEqual(expiry.expiry_state(node(expiry_in=3), NOW), "soon")
        self.assertEqual(expiry.expiry_state(node(expiry_in=14), NOW), "soon")  # boundary included
        self.assertEqual(expiry.expiry_state(node(expiry_in=15), NOW), "ok")
        self.assertEqual(expiry.expiry_state(node(expiry_in=180), NOW), "ok")

    def test_missing_or_null_expiry_is_disabled(self):
        self.assertEqual(expiry.expiry_state({"id": "1"}, NOW), "disabled")
        self.assertEqual(expiry.expiry_state({"id": "1", "expiry": None}, NOW), "disabled")

    def test_custom_window(self):
        self.assertTrue(expiry.expires_soon(node(expiry_in=20), NOW, days=30))
        self.assertFalse(expiry.expires_soon(node(expiry_in=20), NOW, days=7))
        self.assertFalse(expiry.expires_soon(node(expiry_in=-2), NOW, days=30))  # expired is not "soon"


class Inactive(unittest.TestCase):
    def test_online_is_never_inactive(self):
        self.assertFalse(expiry.is_inactive(node(online=True, seen_ago=90), NOW))

    def test_offline_threshold(self):
        self.assertTrue(expiry.is_inactive(node(seen_ago=31), NOW, days=30))
        self.assertFalse(expiry.is_inactive(node(seen_ago=29), NOW, days=30))

    def test_never_seen_counts_from_creation(self):
        self.assertTrue(expiry.is_inactive(node(seen_ago=None, created_ago=45), NOW, days=30))
        self.assertFalse(expiry.is_inactive(node(seen_ago=None, created_ago=2), NOW, days=30))
        self.assertTrue(expiry.is_inactive(node(lastSeen=NEVER, created_ago=45), NOW, days=30))

    def test_no_dates_at_all(self):
        self.assertFalse(expiry.is_inactive({"id": "1", "online": False}, NOW))

    def test_zero_days_means_any_offline_machine(self):
        self.assertTrue(expiry.is_inactive(node(seen_ago=0.01), NOW, days=0))


class Summary(unittest.TestCase):
    def test_counts(self):
        nodes = [node(1, expiry_in=5, online=True), node(2, expiry_in=10, seen_ago=1),
                 node(3, expiry_in=-3, seen_ago=60), node(4, expiry_in=100, seen_ago=45),
                 node(5, expiry_in=None, online=True)]
        self.assertEqual(expiry.summary(nodes, NOW), {"soon": 2, "expired": 1, "inactive": 2})
        self.assertEqual([n["id"] for n in expiry.expiring_nodes(nodes, NOW)], ["1", "2"])
        self.assertEqual([n["id"] for n in expiry.inactive_nodes(nodes, NOW)], ["3", "4"])

    def test_notice(self):
        self.assertEqual(expiry.notice_html([node(1, expiry_in=100, online=True)], NOW), "")
        html = expiry.notice_html([node(1, expiry_in=3, online=True), node(2, expiry_in=4, online=True),
                                   node(3, expiry_in=-1)], NOW)
        self.assertIn("2 machines expire in the next", html)
        self.assertIn('data-f-apply="expiring"', html)
        self.assertIn("1 machine has an expired key", html)
        self.assertIn('data-f-apply="expired"', html)

    def test_notice_inactive_line_is_admin_only(self):
        nodes = [node(1, expiry_in=100, seen_ago=45)]
        self.assertEqual(expiry.notice_html(nodes, admin=False, now=NOW), "")
        html = expiry.notice_html(nodes, admin=True, now=NOW)
        self.assertIn("1 machine has been offline for more than", html)
        self.assertIn('data-open="remove-inactive"', html)
        self.assertIn('data-f-apply="inactive"', html)


class Form(unittest.TestCase):
    def test_selected_ids(self):
        form = {"csrf": "x", "node-12": "1", "node-3": "1", "node-x": "1", "nodes-4": "1", "node-5; rm": "1"}
        self.assertEqual(expiry.selected_ids(form), {"12", "3"})

    def test_flash(self):
        self.assertEqual(expiry.remove_result(3, 0), "inactive-removed-3")
        self.assertEqual(expiry.remove_result(0, 0), "inactive-none")
        self.assertEqual(expiry.remove_result(2, 1), "failed")
        self.assertIn("3 inactive machines removed.", expiry.flash_html("inactive-removed-3"))
        self.assertIn("1 inactive machine removed.", expiry.flash_html("inactive-removed-1"))
        self.assertIn("notice error", expiry.flash_html("inactive-none"))
        self.assertEqual(expiry.flash_html("inactive-removed-<script>"), "")
        self.assertEqual(expiry.flash_html("renamed"), "")


class MachineView(unittest.TestCase):
    """The Machine view model uses the real clock: dates relative to now."""

    def machine(self, **kw):
        real_now = datetime.now(timezone.utc)
        n = node(**kw)
        for key, days in (("expiry", kw.get("expiry_in")), ("lastSeen", kw.get("seen_ago"))):
            if days is not None:
                sign = 1 if key == "expiry" else -1
                n[key] = iso(real_now + sign * timedelta(days=days))
        n["createdAt"] = iso(real_now - timedelta(days=kw.get("created_ago", 400)))
        return pages.Machine(n, None, {}, "", {})

    def test_badge_and_flags(self):
        m = self.machine(expiry_in=5, online=True)
        self.assertTrue(m.expiring_soon)
        self.assertFalse(m.inactive)
        self.assertIn("Expires soon", m.badges())
        self.assertIn("badge orange", m.badges())

    def test_expired_wins_over_soon(self):
        m = self.machine(expiry_in=-1, seen_ago=40)
        self.assertFalse(m.expiring_soon)
        self.assertTrue(m.inactive)
        self.assertIn("Expired", m.badges())
        self.assertNotIn("Expires soon", m.badges())

    def test_far_expiry_no_badge(self):
        m = self.machine(expiry_in=100, seen_ago=2)
        self.assertFalse(m.expiring_soon or m.inactive)
        self.assertNotIn("Expires soon", m.badges())

    def test_dialog_lists_only_inactive(self):
        ms = [self.machine(node_id=1, seen_ago=40), self.machine(node_id=2, online=True),
              self.machine(node_id=3, seen_ago=2)]
        html = expiry.remove_inactive_dialog(ms, {"csrf": "tok"})
        self.assertIn('name="node-1"', html)
        self.assertNotIn('name="node-2"', html)
        self.assertNotIn('name="node-3"', html)
        self.assertIn("checked", html)
        self.assertIn('value="tok"', html)
        self.assertEqual(expiry.remove_inactive_dialog(ms[1:], {"csrf": "tok"}), "")


if __name__ == "__main__":
    unittest.main()
