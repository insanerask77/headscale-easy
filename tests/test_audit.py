"""Tests for the activity log (web/audit.py). Standard library only:

    python3 -m unittest discover -s tests
"""

import csv
import io
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))
os.environ.setdefault("AUDIT_DB", os.path.join(tempfile.gettempdir(), "hse-audit-import.db"))

import audit  # noqa: E402
from i18n import set_lang  # noqa: E402

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def node(nid, name, online=True, expiry=None, user="ana"):
    return {"id": str(nid), "givenName": name, "online": online, "expiry": expiry,
            "user": {"name": user}, "ipAddresses": [f"100.64.0.{nid}"]}


def info(version):
    return {"hostinfo": {"IPNVersion": version}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        audit.configure(os.path.join(self.tmp.name, "audit.db"))
        set_lang("en")

    def tearDown(self):
        audit.configure(os.path.join(self.tmp.name, "unused.db"))
        self.tmp.cleanup()


class StoreTest(Base):
    def test_record_and_query(self):
        eid = audit.record("ana", "machine.rename", "laptop", {"from": "a", "to": "laptop"}, ip="203.0.113.5",
                           ref="node:1")
        self.assertIsInstance(eid, int)
        events, total = audit.query()
        self.assertEqual(total, 1)
        ev = events[0]
        self.assertEqual((ev["category"], ev["actor"], ev["target"], ev["ip"], ev["ref"]),
                         ("config", "ana", "laptop", "203.0.113.5", "node:1"))
        self.assertEqual(ev["details"], {"from": "a", "to": "laptop"})
        self.assertTrue(ev["ts"].endswith("Z"))

    def test_categories(self):
        self.assertEqual(audit.category_of("auth.signin"), "signin")
        self.assertEqual(audit.category_of("device.connected"), "devices")
        self.assertEqual(audit.category_of("dns.save"), "config")
        self.assertEqual(audit.category_of("invite.create"), "config")

    def test_append_only(self):
        audit.record("ana", "user.create", "bob")
        with self.assertRaises(sqlite3.DatabaseError):
            with audit._lock:
                audit._db().execute("UPDATE events SET actor = 'mallory'")

    def test_secrets_are_never_stored(self):
        full_api = "hskey-api-abcdefghijkl-THISISTHESECRETPART0123456789"
        full_auth = "hskey-auth-ABCDEFGHIJKL-supersecretsupersecret"
        audit.record("ana", "authkey.create", "ana", {"key": full_auth, "note": f"made with {full_api}",
                                                      "nested": {"token": "0123456789abcdef"}})
        with audit._lock:
            raw = audit._db().execute("SELECT details, target FROM events").fetchone()
        text = " ".join(raw)
        self.assertNotIn("THISISTHESECRETPART", text)
        self.assertNotIn("supersecret", text)
        self.assertNotIn("0123456789abcdef", text)
        self.assertIn("hskey-auth-ABCDEFGHIJKL…", text)
        self.assertIn("hskey-api-abcdefghijkl…", text)
        self.assertEqual(audit.prefix("0123456789abcdef"), "012345…")

    def test_record_never_raises(self):
        audit.configure("/nonexistent-dir/for/sure/audit.db")
        self.assertIsNone(audit.record("ana", "user.create", "bob"))
        self.assertFalse(audit.available())

    def test_threads(self):
        def work(n):
            for i in range(50):
                audit.record(f"t{n}", "user.create", f"u{i}")
        threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(audit.query()[1], 400)


class FilterTest(Base):
    def setUp(self):
        super().setUp()
        audit.record("ana", "machine.rename", "laptop", {"to": "laptop"}, ip="198.51.100.1", ref="node:1",
                     ts=audit.now_ts(NOW - timedelta(days=3)))
        audit.record("leo", "dns.save", "DNS", {"changed": {"nameservers": {"from": ["1.1.1.1"], "to": ["9.9.9.9"]}}},
                     ts=audit.now_ts(NOW - timedelta(days=1)))
        audit.record("", "auth.signin_failed", "", {"method": "apikey"}, ip="192.0.2.7",
                     ts=audit.now_ts(NOW - timedelta(hours=1)))
        audit.record(audit.HEADSCALE, "device.connected", "laptop", {"user": "ana"}, ref="node:1",
                     ts=audit.now_ts(NOW))

    def ids(self, **f):
        return [e["action"] for e in audit.query(f)[0]]

    def test_newest_first(self):
        self.assertEqual(self.ids(), ["device.connected", "auth.signin_failed", "dns.save", "machine.rename"])

    def test_category_actor_target(self):
        self.assertEqual(self.ids(category="devices"), ["device.connected"])
        self.assertEqual(self.ids(category="signin"), ["auth.signin_failed"])
        self.assertEqual(self.ids(actor="leo"), ["dns.save"])
        self.assertEqual(self.ids(target="node:1"), ["device.connected", "machine.rename"])

    def test_time_range(self):
        f = audit.filters_from({"from": (NOW - timedelta(days=1)).date().isoformat(),
                                "to": (NOW - timedelta(days=1)).date().isoformat()})
        self.assertEqual(self.ids(**f), ["dns.save"])
        self.assertEqual(audit.filters_from({"from": "2026-13-45"}).get("since"), None)

    def test_text(self):
        self.assertEqual(self.ids(text="9.9.9.9"), ["dns.save"])      # details
        self.assertEqual(self.ids(text="192.0.2"), ["auth.signin_failed"])  # ip
        self.assertEqual(self.ids(text="renamed machine"), ["machine.rename"])  # readable label
        self.assertEqual(self.ids(text="100%"), [])                    # LIKE wildcards are escaped
        set_lang("es")
        self.assertEqual(self.ids(text="conectado"), ["device.connected"])

    def test_pagination(self):
        events, total = audit.query({}, limit=2, offset=2)
        self.assertEqual(total, 4)
        self.assertEqual([e["action"] for e in events], ["dns.save", "machine.rename"])

    def test_actors(self):
        self.assertEqual(audit.actors(), sorted(["ana", "leo", audit.HEADSCALE], key=str.lower))


class RetentionTest(Base):
    def test_purge(self):
        audit.record("ana", "user.create", "old", ts=audit.now_ts(NOW - timedelta(days=91)))
        audit.record("ana", "user.create", "new", ts=audit.now_ts(NOW - timedelta(days=89)))
        self.assertEqual(audit.purge(90, now=NOW), 1)
        self.assertEqual([e["target"] for e in audit.query()[0]], ["new"])
        self.assertEqual(audit.purge(0, now=NOW + timedelta(days=1000)), 0)  # 0 = keep forever
        self.assertEqual(audit.query()[1], 1)


class CsvTest(Base):
    def test_csv(self):
        audit.record("ana", "machine.rename", "=HYPERLINK(\"x\")", {"to": "b"}, ip="198.51.100.1")
        rows = list(csv.reader(io.StringIO(audit.csv_export({}))))
        self.assertEqual(rows[0][:5], ["time_utc", "category", "action", "description", "actor"])
        self.assertEqual(rows[1][2:5], ["machine.rename", "Renamed machine", "ana"])
        self.assertTrue(rows[1][5].startswith("'="))  # no formula injection
        self.assertEqual(rows[1][7], "198.51.100.1")

    def test_csv_filters(self):
        audit.record("ana", "machine.rename", "a")
        audit.record("leo", "dns.save", "DNS")
        rows = list(csv.reader(io.StringIO(audit.csv_export({"actor": "leo"}))))
        self.assertEqual([r[2] for r in rows[1:]], ["dns.save"])


class DeviceDiffTest(Base):
    def test_diff_events(self):
        future = (NOW + timedelta(days=30)).isoformat().replace("+00:00", "Z")
        past = (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        prev = audit.snapshot([node(1, "laptop"), node(2, "phone", online=False), node(3, "old"),
                               node(4, "srv", expiry=future), node(5, "tv")],
                              {"1": info("1.80.0-t123"), "5": info("1.78.0")}, NOW)
        cur = audit.snapshot([node(1, "laptop"), node(2, "phone", online=True), node(4, "srv", expiry=past),
                              node(5, "living-room"), node(6, "new")],
                             {"1": info("1.82.0"), "5": info("1.78.0")}, NOW)
        events = {(a, t) for a, t, _d, _r in audit.diff(prev, cur)}
        self.assertEqual(events, {
            ("device.registered", "new"), ("device.removed", "old"), ("device.connected", "phone"),
            ("device.key_expired", "srv"), ("device.version", "laptop"), ("device.renamed", "living-room"),
        })
        # A rename made from the console is already logged: not repeated
        self.assertNotIn("device.renamed", [a for a, *_ in audit.diff(prev, cur, {("node:5", "living-room")})])
        # ...but a later rename to another name (e.g. headscale nodes rename) is
        self.assertIn("device.renamed", [a for a, *_ in audit.diff(prev, cur, {("node:5", "tv2")})])
        version = next(d for a, _t, d, _r in audit.diff(prev, cur) if a == "device.version")
        self.assertEqual((version["from"], version["to"]), ("1.80.0", "1.82.0"))

    def test_disconnect_and_unknown_version(self):
        prev = audit.snapshot([node(1, "a", online=True)], {}, NOW)
        cur = audit.snapshot([node(1, "a", online=False)], {"1": info("1.82.0")}, NOW)
        self.assertEqual([a for a, *_ in audit.diff(prev, cur)], ["device.disconnected"])

    def test_watch_once_uses_saved_state(self):
        self.assertEqual(audit.watch_once([node(1, "a")], {}, NOW), 0)  # baseline
        self.assertEqual(audit.query()[1], 0)
        # Renamed from the console between two passes
        audit.record("ana", "machine.rename", "b", {"from": "a", "to": "b"}, ref="node:1",
                     ts=audit.now_ts(NOW + timedelta(seconds=10)))
        n = audit.watch_once([node(1, "b", online=False), node(2, "c")], {}, NOW + timedelta(seconds=30))
        self.assertEqual(n, 2)
        actions = [e["action"] for e in audit.query({"category": "devices"})[0]]
        self.assertEqual(sorted(actions), ["device.disconnected", "device.registered"])
        self.assertEqual(audit.query({"category": "devices"})[0][0]["actor"], audit.HEADSCALE)
        # Renamed again outside the console, shortly after the console rename
        self.assertEqual(audit.watch_once([node(1, "d", online=False), node(2, "c")], {}, NOW + timedelta(seconds=45)), 1)
        self.assertEqual(audit.query({"category": "devices"})[0][0]["action"], "device.renamed")
        # Nothing changed: nothing logged
        self.assertEqual(audit.watch_once([node(1, "d", online=False), node(2, "c")], {}, NOW + timedelta(seconds=60)), 0)


class ClientIpTest(unittest.TestCase):
    class H:
        def __init__(self, peer, headers):
            self.client_address = (peer, 1234)
            self.headers = headers

    def test_trusted_proxy(self):
        self.assertEqual(audit.client_ip(self.H("172.18.0.5", {"X-Real-IP": "203.0.113.9"})), "203.0.113.9")
        self.assertEqual(audit.client_ip(self.H("172.18.0.5", {"X-Forwarded-For": "1.2.3.4, 203.0.113.9"})),
                         "203.0.113.9")
        self.assertEqual(audit.client_ip(self.H("172.18.0.5", {"X-Real-IP": "not-an-ip"})), "172.18.0.5")

    def test_untrusted_peer_cannot_spoof(self):
        self.assertEqual(audit.client_ip(self.H("203.0.113.50", {"X-Real-IP": "10.0.0.1"})), "203.0.113.50")

    def test_actor(self):
        self.assertEqual(audit.actor_of({"kind": "apikey", "key": "abcdefghijkl"}), "api-key:abcdefghijkl")
        self.assertEqual(audit.actor_of({"kind": "oidc", "username": "ana", "email": "a@x"}), "ana")
        self.assertEqual(audit.actor_of(None), "")


class PageTest(Base):
    def test_page_renders(self):
        audit.record("ana", "acl.save", "Access control policy", audit.text_diff('{"a": 1}', '{"a": 2}'))
        audit.record("ana", "custom.unknown", "<script>x</script>")
        session = {"csrf": "t", "admin": True, "name": "Ana", "kind": "oidc"}
        html = audit.page(session, {}, {})
        self.assertIn("Saved access control policy", html)
        self.assertIn("custom.unknown", html)
        self.assertNotIn("<script>x</script>", html)
        self.assertIn('data-live="rows"', html)
        self.assertIn('class="add"', html)
        self.assertNotIn('data-live="rows"', audit.page(session, {}, {"page": "2"}))


if __name__ == "__main__":
    unittest.main()
