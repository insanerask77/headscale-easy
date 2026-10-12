"""Live device status: the diff between two node lists, per-user filtering,
the shared poller (with backoff while Headscale is down) and the /events
endpoint (auth, origin, limits). A fake node list stands in for Headscale.
Standard library only:

    python3 tests/test_live.py
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_security import ADMIN, B, Base, MEMBER, app, hs, request, sessions, sh  # noqa: E402

import live  # noqa: E402


def node(i, name="pc", online=False, user="1"):
    return {"id": str(i), "givenName": f"{name}{i}", "online": online, "user": {"id": user}}


def snap(*nodes):
    return {s["id"]: s for s in map(live.summarize, nodes)}


class Diff(unittest.TestCase):
    def kinds(self, old, new):
        return sorted((e["type"], e["id"]) for e in live.diff(snap(*old), snap(*new)))

    def test_nothing_changed(self):
        self.assertEqual(self.kinds([node(1)], [node(1)]), [])

    def test_online_offline_added_removed_renamed(self):
        self.assertEqual(self.kinds([node(1)], [node(1, online=True)]), [("online", "1")])
        self.assertEqual(self.kinds([node(1, online=True)], [node(1)]), [("offline", "1")])
        self.assertEqual(self.kinds([], [node(2)]), [("added", "2")])
        self.assertEqual(self.kinds([node(2)], []), [("removed", "2")])
        renamed = live.diff(snap(node(1)), snap(dict(node(1), givenName="laptop")))
        self.assertEqual([(e["type"], e["name"], e["was"]) for e in renamed], [("renamed", "laptop", "pc1")])

    def test_several_at_once(self):
        old = [node(1), node(2, online=True), node(3)]
        new = [node(1, online=True), node(3), node(4)]
        self.assertEqual(self.kinds(old, new), [("added", "4"), ("online", "1"), ("removed", "2")])

    def test_message_has_no_more_than_it_needs(self):
        text = live.format_event(dict(node(1), **{"type": "online", "user": "7", "ips": ["100.64.0.1"]})).decode()
        self.assertTrue(text.startswith("event: node\ndata: "))
        self.assertNotIn("100.64", text)
        self.assertNotIn('"user"', text)


class Filtering(unittest.TestCase):
    def test_member_gets_only_their_devices(self):
        mine, everyone = live.Subscriber("s", "2"), live.Subscriber("s2", None)
        mine_ev, other_ev = {"type": "online", "user": "2"}, {"type": "online", "user": "9"}
        self.assertEqual([mine.wants(mine_ev), mine.wants(other_ev)], [True, False])
        self.assertEqual([everyone.wants(mine_ev), everyone.wants(other_ev)], [True, True])
        self.assertFalse(live.Subscriber("s3", "").wants(mine_ev))  # a member with no user yet

    def test_slow_client_is_dropped(self):
        sub = live.Subscriber("s", None)
        for _ in range(live.QUEUE_SIZE + 1):
            sub.push({"type": "online"})
        self.assertTrue(sub.closed.is_set())


class HubTest(unittest.TestCase):
    def setUp(self):
        self.nodes = [node(1), node(2, user="2")]
        self.fail_next = 0

        def fetch():
            if self.fail_next:
                self.fail_next -= 1
                raise OSError("headscale restarting")
            return [dict(n) for n in self.nodes]

        self.hub = live.Hub(fetch, interval=0.02)
        self.addCleanup(self.hub.close_all)

    def wait_event(self, sub, timeout=2):
        return sub.get(timeout)

    def test_poll_once_gives_changes_after_the_baseline(self):
        self.assertEqual(self.hub.poll_once(), [])  # first look: no event for what already exists
        self.nodes[0]["online"] = True
        self.assertEqual([e["type"] for e in self.hub.poll_once()], ["online"])

    def test_fan_out_with_filtering(self):
        admin, member = self.hub.subscribe("a", None), self.hub.subscribe("m", "2")
        time.sleep(0.1)  # baseline taken
        self.nodes[0]["online"] = True   # user 1
        self.nodes[1]["online"] = True   # user 2
        self.assertEqual(sorted([self.wait_event(admin)["id"], self.wait_event(admin)["id"]]), ["1", "2"])
        self.assertEqual(self.wait_event(member)["id"], "2")
        self.assertIsNone(member.get(0.15))  # nothing about user 1

    def test_survives_headscale_being_down(self):
        sub = self.hub.subscribe("a", None)
        time.sleep(0.1)
        self.fail_next = 3
        time.sleep(0.4)
        self.nodes[0]["online"] = True
        self.assertEqual(self.wait_event(sub, 3)["type"], "online")

    def test_poller_stops_when_nobody_listens_and_restarts(self):
        sub = self.hub.subscribe("a", None)
        thread = self.hub.thread
        self.hub.unsubscribe(sub)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        sub2 = self.hub.subscribe("a", None)
        time.sleep(0.1)
        self.nodes[0]["online"] = True
        self.assertEqual(self.wait_event(sub2)["type"], "online")

    def test_limits(self):
        for _ in range(live.MAX_PER_SESSION):
            self.hub.subscribe("same", None)
        with self.assertRaises(live.TooMany):
            self.hub.subscribe("same", None)
        self.hub.subscribe("other", None)  # another session is fine


class Endpoint(Base):
    def setUp(self):
        super().setUp()
        sessions.configure(":memory:")
        self.hub = live.Hub(lambda: [], interval=0.02)
        p = mock.patch.object(sh, "HUB", self.hub)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.hub.close_all)

    def stream(self, session, headers=None):
        """Open /events in a thread; returns (thread, result list)."""
        out = []
        t = threading.Thread(target=lambda: out.append(request("GET", f"{B}/events", session, headers=headers)))
        t.start()
        return t, out

    def wait_subscribed(self, n=1):
        for _ in range(100):
            if self.hub.count() >= n:
                return
            time.sleep(0.02)
        self.fail("the stream did not open")

    def test_needs_a_session(self):
        self.assertEqual(request("GET", f"{B}/events")[0], 401)
        self.assertEqual(self.hub.count(), 0)

    def test_other_site_is_refused(self):
        status, _, _ = request("GET", f"{B}/events", ADMIN, headers={"Origin": "https://evil.example", "Host": "vpn.example.com"})
        self.assertEqual(status, 403)
        self.assertEqual(self.hub.count(), 0)

    def test_admin_stream_delivers_events_and_closes(self):
        t, out = self.stream(ADMIN)
        self.wait_subscribed()
        self.hub.publish([{"type": "online", "id": "5", "name": "pc5", "online": True, "user": "3"}])
        time.sleep(0.2)
        self.hub.close_all()
        t.join(3)
        status, headers, body = out[0]
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], ["text/event-stream"])
        self.assertIn("retry: 3000", body)
        self.assertIn('event: node\ndata: {"type":"online","id":"5","name":"pc5","online":true}', body)
        self.assertEqual(self.hub.count(), 0)

    def test_member_only_sees_their_own(self):
        t, out = self.stream(MEMBER)  # Bob is Headscale user "2"
        self.wait_subscribed()
        self.hub.publish([{"type": "online", "id": "7", "name": "bob-laptop", "online": True, "user": "2"},
                          {"type": "online", "id": "8", "name": "alice-pc", "online": True, "user": "9"}])
        time.sleep(0.2)
        self.hub.close_all()
        t.join(3)
        body = out[0][2]
        self.assertIn("bob-laptop", body)
        self.assertNotIn("alice-pc", body)

    def test_too_many_streams_of_one_session(self):
        session = dict(ADMIN, sid=sessions.create(ADMIN))
        threads = [self.stream(session) for _ in range(live.MAX_PER_SESSION)]
        self.wait_subscribed(live.MAX_PER_SESSION)
        self.assertEqual(request("GET", f"{B}/events", session)[0], 429)
        self.hub.close_all()
        for t, _ in threads:
            t.join(3)

    def test_revoked_session_ends_the_stream(self):
        session = dict(ADMIN, sid=sessions.create(ADMIN))
        with mock.patch.object(live, "HEARTBEAT", 0.05):
            t, out = self.stream(session)
            self.wait_subscribed()
            sessions.revoke(session["sid"])
            t.join(3)
        self.assertFalse(t.is_alive())
        self.assertEqual(self.hub.count(), 0)


if __name__ == "__main__":
    unittest.main()
