"""Tests for webhook notifications (web/notify.py). Standard library only:

    python3 -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_security as ts  # noqa: E402  (sets the environment and imports app)

import app  # noqa: E402
from handlers import shared as sh  # noqa: E402
import audit  # noqa: E402
import notify  # noqa: E402
from i18n import set_lang  # noqa: E402

B = ts.B
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
SLACK = "https://hooks.slack.com/services/T000/B000/SECRET"
TELEGRAM = "telegram:123456:ABC-def_1@-100987"


def node(nid, name, expiry=None):
    return {"id": str(nid), "givenName": name, "user": {"name": "ana"}, "expiry": expiry}


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Parsing(unittest.TestCase):
    def test_destinations(self):
        dests = notify.parse_destinations(
            f"{SLACK}, {TELEGRAM} ntfy:alerts\nntfy:https://ntfy.example.com/t webhook:https://x.example/h "
            "https://y.example/h")
        self.assertEqual([d["kind"] for d in dests], ["slack", "telegram", "ntfy", "ntfy", "webhook", "webhook"])
        self.assertEqual(dests[2]["url"], "https://ntfy.sh/alerts")
        self.assertEqual(dests[1]["chat"], "-100987")

    def test_invalid_entries_are_skipped(self):
        self.assertEqual(notify.parse_destinations("telegram:nope slack:ftp://x file:///etc/passwd  "), [])
        self.assertEqual(notify.parse_destinations(""), [])

    def test_labels_do_not_leak_secrets(self):
        for dest in notify.parse_destinations(f"{SLACK} {TELEGRAM}"):
            self.assertNotIn("SECRET", dest["label"])
            self.assertNotIn("ABC-def", dest["label"])

    def test_events_default_and_filter(self):
        self.assertEqual(notify.parse_events(None), set(notify.ALL_EVENTS))
        self.assertEqual(notify.parse_events(""), set(notify.ALL_EVENTS))
        self.assertEqual(notify.parse_events("device.removed, bogus"), {"device.removed"})
        self.assertEqual(notify.parse_events("bogus"), set(notify.ALL_EVENTS))


class Payloads(unittest.TestCase):
    def build(self, spec, action="device.registered"):
        dest = notify.parse_destinations(spec)[0]
        return notify.build_request(dest, action, "ana-laptop", {"user": "ana"}, ts="2026-09-29T12:00:00Z")

    def test_slack(self):
        url, headers, body = self.build(SLACK)
        self.assertEqual(url, SLACK)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertIn("ana-laptop", json.loads(body)["text"])

    def test_telegram(self):
        url, _h, body = self.build(TELEGRAM)
        self.assertEqual(url, "https://api.telegram.org/bot123456:ABC-def_1/sendMessage")
        data = json.loads(body)
        self.assertEqual(data["chat_id"], "-100987")
        self.assertIn("New device", data["text"])

    def test_ntfy_is_plain_text_with_priority(self):
        url, headers, body = self.build("ntfy:alerts", "device.key_expired")
        self.assertEqual(url, "https://ntfy.sh/alerts")
        self.assertEqual(headers["Priority"], "high")
        self.assertIn(b"ana-laptop", body)

    def test_generic_webhook(self):
        _u, _h, body = self.build("webhook:https://x.example/h", "device.removed")
        data = json.loads(body)
        self.assertEqual(data["event"], "device.removed")
        self.assertEqual(data["target"], "ana-laptop")
        self.assertEqual(data["details"], {"user": "ana"})
        self.assertEqual(data["timestamp"], "2026-09-29T12:00:00Z")
        self.assertEqual(data["source"], "headscale-easy")


class Delivery(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.object(notify, "RETRY_DELAY", 0),):
            p.start()
            self.addCleanup(p.stop)
        self.dest = notify.parse_destinations(SLACK)[0]

    def test_network_failure_does_not_raise_and_retries(self):
        post = mock.Mock(side_effect=urllib.error.URLError("down"))
        with mock.patch.object(notify, "_post", post):
            self.assertFalse(notify.deliver(self.dest, "device.registered", "x"))
        self.assertEqual(post.call_count, notify.ATTEMPTS)

    def test_retry_then_success(self):
        post = mock.Mock(side_effect=[OSError("boom"), None])
        with mock.patch.object(notify, "_post", post):
            self.assertTrue(notify.deliver(self.dest, "device.registered", "x"))
        self.assertEqual(post.call_count, 2)

    def test_failure_log_does_not_contain_the_url(self):
        with mock.patch.object(notify, "_post", mock.Mock(side_effect=OSError("boom"))), \
                self.assertLogs("headscale-easy", "WARNING") as logs:
            notify.deliver(self.dest, "device.registered", "x")
        self.assertNotIn("SECRET", "\n".join(logs.output))

    def test_event_filter_and_threads(self):
        sent = []
        with mock.patch.dict(os.environ, {"NOTIFY_URLS": SLACK, "NOTIFY_EVENTS": "device.removed"}), \
                mock.patch.object(notify, "deliver", lambda *a: sent.append(a)), \
                mock.patch.object(notify.threading, "Thread") as thread:
            thread.side_effect = lambda target, args, **kw: mock.Mock(start=lambda: target(*args))
            self.assertFalse(notify.event("device.registered", "a"))
            self.assertTrue(notify.event("device.removed", "a", {"user": "ana"}))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], "device.removed")

    def test_event_without_destinations_is_a_noop(self):
        with mock.patch.dict(os.environ, {"NOTIFY_URLS": ""}):
            self.assertFalse(notify.event("device.removed", "a"))

    def test_event_never_raises(self):
        with mock.patch.dict(os.environ, {"NOTIFY_URLS": SLACK}), \
                mock.patch.object(notify.threading, "Thread", side_effect=RuntimeError("no threads")):
            self.assertFalse(notify.event("device.registered", "a"))


class AuditHook(unittest.TestCase):
    def test_record_notifies_and_survives_a_broken_notifier(self):
        audit.subscribe(notify.on_audit_event)
        self.addCleanup(audit.unsubscribe, notify.on_audit_event)
        with tempfile.TemporaryDirectory() as tmp:
            audit.configure(os.path.join(tmp, "audit.db"))
            with mock.patch.object(notify, "event") as ev:
                rid = audit.record("headscale", "device.registered", "laptop", {"user": "ana"})
            self.assertIsNotNone(rid)
            ev.assert_called_once_with("device.registered", "laptop", {"user": "ana"})
            with mock.patch.object(notify, "event", side_effect=RuntimeError("x")):
                self.assertIsNotNone(audit.record("headscale", "device.removed", "laptop"))


class Expiring(unittest.TestCase):
    def test_only_new_machines_in_the_window(self):
        soon = iso(NOW + timedelta(days=3))
        far = iso(NOW + timedelta(days=90))
        nodes = [node(1, "a", soon), node(2, "b", far), node(3, "c", None), node(4, "d", iso(NOW - timedelta(days=1)))]
        fresh, state = notify.new_expiring(nodes, {}, NOW)
        self.assertEqual([n["id"] for n in fresh], ["1"])
        self.assertEqual(state, {"1": soon})
        again, state2 = notify.new_expiring(nodes, state, NOW)
        self.assertEqual(again, [])
        # A renewed key (new expiry date) that gets close again is announced again
        renewed = [node(1, "a", iso(NOW + timedelta(days=5)))]
        self.assertEqual(len(notify.new_expiring(renewed, state2, NOW)[0]), 1)
        # Leaving the window prunes it
        self.assertEqual(notify.new_expiring([node(1, "a", far)], state2, NOW)[1], {})

    def test_check_expiring_remembers_between_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit.configure(os.path.join(tmp, "audit.db"))
            nodes = [node(1, "laptop", iso(NOW + timedelta(days=2)))]
            with mock.patch.object(notify, "event") as ev:
                self.assertEqual(notify.check_expiring(nodes, NOW), 1)
                self.assertEqual(notify.check_expiring(nodes, NOW), 0)
            ev.assert_called_once()
            self.assertEqual(ev.call_args[0][:2], ("device.expiring", "laptop"))


def summary(at, ok, trigger="scheduled", error=None):
    return {"last": {"at": at, "ok": ok, "trigger": trigger, "error": error}}


class BackupFailed(unittest.TestCase):
    def test_event_is_known_and_on_by_default(self):
        self.assertIn("backup.failed", notify.ALL_EVENTS)
        self.assertIn("backup.failed", notify.parse_events(None))
        self.assertEqual(notify.parse_events("backup.failed"), {"backup.failed"})

    def test_message(self):
        self.assertEqual(notify.message("backup.failed", "backup", {"error": "disk full"}), "Backup failed: disk full")
        self.assertEqual(notify.message("backup.failed", "backup", {}), "Backup failed.")

    def test_first_look_remembers_and_stays_quiet(self):
        failure, seen = notify.new_backup_failure(summary("t1", False), None)
        self.assertEqual((failure, seen), (None, "t1"))

    def test_new_scheduled_failure_is_reported_once(self):
        failure, seen = notify.new_backup_failure(summary("t2", False, error="boom"), "t1")
        self.assertEqual((failure["error"], seen), ("boom", "t2"))
        self.assertEqual(notify.new_backup_failure(summary("t2", False, error="boom"), seen), (None, "t2"))

    def test_successes_manual_runs_and_missing_data_do_not_report(self):
        self.assertEqual(notify.new_backup_failure(summary("t2", True), "t1"), (None, "t2"))
        self.assertEqual(notify.new_backup_failure(summary("t2", False, trigger="manual"), "t1"), (None, "t2"))
        self.assertEqual(notify.new_backup_failure(None, "t1"), (None, "t1"))
        self.assertEqual(notify.new_backup_failure({"last": None}, None), (None, None))

    def test_check_backup_remembers_between_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit.configure(os.path.join(tmp, "audit.db"))
            with mock.patch.object(notify, "event") as ev:
                self.assertFalse(notify.check_backup(summary("t1", True)))           # first look
                self.assertTrue(notify.check_backup(summary("t2", False, error="x" * 500)))
                self.assertFalse(notify.check_backup(summary("t2", False, error="x")))   # same run
                self.assertFalse(notify.check_backup(None))                          # supervisor not answering
            ev.assert_called_once()
            self.assertEqual(ev.call_args[0][:2], ("backup.failed", "backup"))
            self.assertEqual(len(ev.call_args[0][2]["error"]), 200)


class TestButton(ts.Base):
    def setUp(self):
        super().setUp()
        set_lang("en")

    def test_member_cannot_send(self):
        status, _h, _b = ts.request("POST", f"{B}/settings/notify-test", ts.MEMBER, {"csrf": "tok"})
        self.assertEqual(status, 403)

    def test_requires_csrf(self):
        status, _h, _b = ts.request("POST", f"{B}/settings/notify-test", ts.ADMIN, {"csrf": "bad"})
        self.assertEqual(status, 403)

    def test_admin_sends_test(self):
        with mock.patch.object(notify, "send_test", return_value=[("Slack (x)", True)]):
            status, headers, _b = ts.request("POST", f"{B}/settings/notify-test", ts.ADMIN, {"csrf": "tok"})
        self.assertEqual(status, 303)
        self.assertIn("m=notify-test-ok", ts.location(headers))
        self.assertEqual(app.audit.request_event.call_args[0][2], "settings.notify_test")

    def test_failure_and_no_destination(self):
        with mock.patch.object(notify, "send_test", return_value=[("Slack (x)", False)]):
            _s, headers, _b = ts.request("POST", f"{B}/settings/notify-test", ts.ADMIN, {"csrf": "tok"})
        self.assertIn("notify-test-failed", ts.location(headers))
        with mock.patch.object(notify, "send_test", return_value=[]):
            _s, headers, _b = ts.request("POST", f"{B}/settings/notify-test", ts.ADMIN, {"csrf": "tok"})
        self.assertIn("notify-none", ts.location(headers))

    def test_blocked_in_demo(self):
        with mock.patch.object(sh, "DEMO", True), mock.patch.object(notify, "send_test") as send:
            status, _h, _b = ts.request("POST", f"{B}/settings/notify-test", ts.ADMIN, {"csrf": "tok"})
        self.assertEqual(status, 403)
        send.assert_not_called()

    def test_settings_page_for_admin_only(self):
        with mock.patch.dict(os.environ, {"NOTIFY_URLS": SLACK}), \
                mock.patch.object(app.hs, "key_expiry_days", return_value=180):
            _s, _h, admin_body = ts.request("GET", f"{B}/settings/general", ts.ADMIN)
            _s, _h, member_body = ts.request("GET", f"{B}/settings/general", ts.MEMBER)
        self.assertIn("settings/notify-test", admin_body)
        self.assertIn("hooks.slack.com", admin_body)
        self.assertNotIn("SECRET", admin_body)
        self.assertNotIn("notify-test", member_body)


if __name__ == "__main__":
    unittest.main()
