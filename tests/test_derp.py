"""DERP tests: NetInfo parsing, the own DERP map (validation, render/parse round
trip, config.yaml block, rollback on a bad config or a failed restart) and the
page through the real request handler. Standard library only; no Headscale:

    python3 tests/test_derp.py
"""
import email.message
import io
import os
import sys
import tempfile
import time
import unittest
import urllib.parse
from unittest import mock

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
TMP = tempfile.mkdtemp(prefix="t-derp-")
CONFIG = os.path.join(TMP, "config.yaml")
DERP_FILE = os.path.join(TMP, "derp.yaml")
os.environ.update(HEADSCALE_API_KEY="x", PUBLIC_URL="https://vpn.example.com", SESSION_SECRET="s",
                  HEADSCALE_URL="http://headscale:8080", HEADSCALE_CONFIG=CONFIG, HEADSCALE_DERP_FILE=DERP_FILE)
sys.path.insert(0, WEB)

import app  # noqa: E402
import derp  # noqa: E402
import derp_pages  # noqa: E402
import headscale as hs  # noqa: E402
import sessions  # noqa: E402

sessions.configure(":memory:")
import pages  # noqa: E402

B = app.BASE
ADMIN = {"kind": "oidc", "sub": "a", "username": "root", "name": "Root", "email": "", "groups": [],
         "admin": True, "csrf": "tok", "exp": time.time() + 3600}
MEMBER = dict(ADMIN, sub="b", username="bob", name="Bob", admin=False)
AUDITOR = dict(ADMIN, sub="c", username="eve", admin=False, role="auditor")

BASE_CONFIG = """---
derp:
  urls: []
  server:
    enabled: true
    region_id: 999
    region_name: "Custom Embedded DERP"
  auto_update_enabled: false
  update_frequency: 24h
  # >>> derp map: managed by Headscale Easy (do not edit between these markers)
  paths: []
  # <<< derp map
log:
  level: info
"""

RELAY = {"id": 900, "code": "home", "name": "Home relay", "hostname": "derp.example.com", "ipv4": "203.0.113.5",
         "ipv6": "", "stun_port": 3478, "derp_port": 443, "stun_only": False}


def request(method, path, session=None, form=None):
    body = urllib.parse.urlencode(form or {}, doseq=True).encode()
    msg = email.message.Message()
    if session is not None and "sid" not in session:
        session = dict(session, sid=sessions.create(session))  # a live server-side session
    if session is not None:
        msg["Cookie"] = f"hse_session={app.sign(session)}"
    msg["Content-Length"] = str(len(body))
    h = app.Handler.__new__(app.Handler)
    h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
    h.headers, h.command, h.path = msg, method, path
    h.request_version, h.requestline, h.client_address = "HTTP/1.1", f"{method} {path} HTTP/1.1", ("127.0.0.1", 1)
    h.close_connection = True
    getattr(h, f"do_{method}")()
    head, _sep, rest = h.wfile.getvalue().partition(b"\r\n\r\n")
    status = int(head.decode().split("\r\n")[0].split()[1])
    return status, head.decode(), rest.decode(errors="replace")


def read(path):
    with open(path) as fh:
        return fh.read()


class NetInfo(unittest.TestCase):
    def test_preferred_and_latency(self):
        hi = {"NetInfo": {"PreferredDERP": 1, "DERPLatency": {"1-v4": 0.0123, "1-v6": 0.02, "2-v4": 0.05, "999-v4": 0.001}}}
        preferred, latency = derp.net_info(hi)
        self.assertEqual(preferred, 1)
        self.assertEqual(latency, {1: 12.3, 2: 50.0, 999: 1.0})

    def test_malformed_input_is_ignored(self):
        for hi in ({}, None, {"NetInfo": None}, {"NetInfo": "x"}, {"NetInfo": {"PreferredDERP": "1", "DERPLatency": []}},
                   {"NetInfo": {"PreferredDERP": True}}, {"NetInfo": {"PreferredDERP": 0}}):
            self.assertEqual(derp.net_info(hi), (None, {}), hi)
        _p, latency = derp.net_info({"NetInfo": {"DERPLatency": {"x-v4": 1, "3-v4": "fast", "4-v4": -1, "5-v4": True, "6-v4": 0.5}}})
        self.assertEqual(latency, {6: 500.0})

    def test_status_aggregates(self):
        his = [{"NetInfo": {"PreferredDERP": 999, "DERPLatency": {"999-v4": 0.010}}},
               {"NetInfo": {"PreferredDERP": 999, "DERPLatency": {"999-v4": 0.030, "1-v4": 0.2}}},
               {"NetInfo": {"PreferredDERP": 1}}, {}]
        rows = derp.status(his, {999: "Embedded", 1: "NYC", 5: "Idle"})
        self.assertEqual([r["id"] for r in rows], [999, 1, 5])
        self.assertEqual(rows[0]["devices"], 2)
        self.assertEqual(rows[0]["latency"], 20.0)
        self.assertEqual(rows[1]["latency"], 200.0)
        self.assertIsNone(rows[2]["latency"])

    def test_machine_view(self):
        node = {"id": 1, "givenName": "pc", "online": True}
        details = {"hostinfo": {"NetInfo": {"PreferredDERP": 999, "DERPLatency": {"999-v4": 0.0124, "1-v4": 0.1}}}}
        m = pages.Machine(node, details, {}, "", {999: "Embedded", 1: "NYC"})
        self.assertEqual(m.derp, "Embedded")
        self.assertEqual(m.derp_ms, 12.4)
        self.assertEqual([(n, used) for n, _ms, used in m.derp_latency], [("Embedded", True), ("NYC", False)])


class Validation(unittest.TestCase):
    def check(self, **change):
        return derp.validate([dict(RELAY, **change)])

    def test_valid(self):
        self.assertEqual(derp.validate([RELAY]), "")
        self.assertEqual(derp.validate([]), "")
        self.assertEqual(self.check(hostname="203.0.113.9", ipv4="", ipv6="2001:db8::1"), "")

    def test_rejects(self):
        for change in ({"id": 5}, {"id": 999}, {"id": 1000}, {"code": "Bad Code"}, {"code": ""}, {"name": ""},
                       {"name": "x" * 65}, {"hostname": "not a host"}, {"hostname": ""}, {"ipv4": "999.1.1.1"},
                       {"ipv4": "2001:db8::1"}, {"ipv6": "203.0.113.5"}, {"derp_port": 0}, {"stun_port": 70000}):
            self.assertNotEqual(self.check(**change), "", change)

    def test_duplicate_ids_and_limit(self):
        self.assertNotEqual(derp.validate([RELAY, dict(RELAY, code="other")]), "")
        many = [dict(RELAY, id=900 + i % 99, code=f"r{i}") for i in range(derp.MAX_RELAYS + 1)]
        self.assertNotEqual(derp.validate(many), "")


class Render(unittest.TestCase):
    def test_round_trip(self):
        relays = [dict(RELAY, ipv6="2001:db8::1", stun_only=True),
                  dict(RELAY, id=901, code="two", name='Quote " and: colon', hostname="d2.example.com", ipv4="",
                       derp_port=8443, stun_port=3479)]
        text = derp.render(relays)
        self.assertIn("regionid: 900", text)
        self.assertIn("hostname: \"derp.example.com\"", text)
        self.assertEqual(derp.parse(text), relays)

    def test_empty_map(self):
        self.assertEqual(derp.render([]), "regions: {}\n")
        self.assertEqual(derp.parse("regions: {}\n"), [])

    def test_block(self):
        on = derp.replace_block(BASE_CONFIG, True)
        self.assertIn("    - /etc/headscale/derp.yaml", on)
        self.assertIn("log:\n  level: info", on)
        self.assertEqual(derp.replace_block(on, False), BASE_CONFIG)
        self.assertIsNone(derp.replace_block("derp:\n  urls: []\n", True))


class Apply(unittest.TestCase):
    def setUp(self):
        # Other test modules may have imported the app first, with other paths
        for target, name, value in ((hs, "HEADSCALE_CONFIG", CONFIG), (derp, "DERP_FILE", DERP_FILE)):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        for path, text in ((CONFIG, BASE_CONFIG), (DERP_FILE, "regions: {}\n")):
            with open(path, "w") as fh:
                fh.write(text)

    def run_apply(self, configtest=(True, ""), restart=True):
        with mock.patch.object(hs, "headscale_configtest", return_value=configtest), \
                mock.patch.object(hs, "restart_headscale", return_value=restart) as restart_mock:
            return derp.apply([RELAY]), restart_mock

    def test_success(self):
        (ok, error), restart = self.run_apply()
        self.assertTrue(ok, error)
        self.assertIn("/etc/headscale/derp.yaml", read(CONFIG))
        self.assertEqual(derp.relays(), [RELAY])
        restart.assert_called_once()

    def test_clearing_resets_paths(self):
        self.run_apply()
        with mock.patch.object(hs, "headscale_configtest", return_value=(True, "")), \
                mock.patch.object(hs, "restart_headscale", return_value=True):
            self.assertEqual(derp.apply([]), (True, ""))
        self.assertEqual(read(CONFIG), BASE_CONFIG)
        self.assertEqual(read(DERP_FILE), "regions: {}\n")

    def test_rollback_when_configtest_fails(self):
        (ok, error), restart = self.run_apply(configtest=(False, "line 1\nbad derp map"))
        self.assertFalse(ok)
        self.assertIn("bad derp map", error)
        self.assertEqual(read(CONFIG), BASE_CONFIG)
        self.assertEqual(read(DERP_FILE), "regions: {}\n")
        restart.assert_not_called()

    def test_rollback_when_restart_fails(self):
        (ok, _error), restart = self.run_apply(restart=False)
        self.assertFalse(ok)
        self.assertEqual(read(CONFIG), BASE_CONFIG)
        self.assertEqual(read(DERP_FILE), "regions: {}\n")
        self.assertEqual(restart.call_count, 2)  # the second one brings the old config back up

    def test_missing_block(self):
        with open(CONFIG, "w") as fh:
            fh.write("derp:\n  urls: []\n")
        (ok, error), _restart = self.run_apply()
        self.assertFalse(ok)
        self.assertIn("DERP block", error)
        self.assertEqual(read(DERP_FILE), "regions: {}\n")


class Form(unittest.TestCase):
    def form(self, **cols):
        return {f"{k}[]": v for k, v in cols.items()}

    def test_rows(self):
        relays, error = derp_pages.relays_from_form(self.form(
            id=["900", "901", ""], code=["a", "b", ""], name=["A", "B", ""], hostname=["a.example.com", "b.example.com", ""],
            ipv4=["", "", ""], ipv6=["", "", ""], derp_port=["443", "", ""], stun_port=["3478", "", ""],
            stun_only=["0", "1", "0"], remove=["0", "1", "0"]))
        self.assertEqual(error, "")
        self.assertEqual([r["id"] for r in relays], [900])  # 901 removed, the blank row skipped

    def test_not_a_number(self):
        _relays, error = derp_pages.relays_from_form(self.form(
            id=["abc"], code=["a"], name=["A"], hostname=["a.example.com"], ipv4=[""], ipv6=[""],
            derp_port=["443"], stun_port=["3478"], stun_only=["0"], remove=["0"]))
        self.assertNotEqual(error, "")


class Pages(unittest.TestCase):
    def setUp(self):
        Apply.setUp(self)
        patches = [mock.patch.object(hs, "all_nodes", return_value=[{"id": "1"}]),
                   mock.patch.object(hs, "host_details", return_value={"1": {"hostinfo": {"NetInfo": {
                       "PreferredDERP": 999, "DERPLatency": {"999-v4": 0.02}}}}}),
                   mock.patch.object(hs, "derp_regions", return_value={999: "Custom Embedded DERP"}),
                   mock.patch.object(hs, "control_available", return_value=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def post(self, session=ADMIN, **extra):
        form = {"csrf": "tok", "id[]": ["900", ""], "code[]": ["home", ""], "name[]": ["Home relay", ""],
                "hostname[]": ["derp.example.com", ""], "ipv4[]": ["", ""], "ipv6[]": ["", ""],
                "derp_port[]": ["443", ""], "stun_port[]": ["3478", ""], "stun_only[]": ["0", "0"],
                "remove[]": ["0", "0"]}
        form.update(extra)
        return request("POST", f"{B}/derp", session, form)

    def test_page_for_admin_and_auditor(self):
        status, _h, body = request("GET", f"{B}/derp", ADMIN)
        self.assertEqual(status, 200)
        self.assertIn("Custom Embedded DERP", body)
        self.assertIn("20 ms", body)
        self.assertIn('name="hostname[]"', body)
        status, _h, body = request("GET", f"{B}/derp", AUDITOR)
        self.assertEqual(status, 200)
        self.assertIn("Only an admin can change the DERP map.", body)
        self.assertNotIn("Save DERP map", body)

    def test_member_is_refused(self):
        self.assertEqual(request("GET", f"{B}/derp", MEMBER)[0], 403)
        self.assertEqual(self.post(MEMBER)[0], 403)
        self.assertEqual(self.post(AUDITOR)[0], 403)
        self.assertEqual(read(DERP_FILE), "regions: {}\n")

    def test_anonymous_goes_to_login(self):
        self.assertEqual(request("GET", f"{B}/derp")[0], 303)

    def test_save(self):
        with mock.patch.object(hs, "headscale_configtest", return_value=(True, "")), \
                mock.patch.object(hs, "restart_headscale", return_value=True), \
                mock.patch.object(app.audit, "request_event") as event:
            status, head, _body = self.post()
        self.assertEqual(status, 303, head)
        self.assertEqual(derp.relays()[0]["hostname"], "derp.example.com")
        self.assertEqual(event.call_args[0][2], "derp.save")

    def test_invalid_input_changes_nothing(self):
        status, _h, body = self.post(**{"id[]": ["5", ""]})
        self.assertEqual(status, 400)
        self.assertIn("between 900 and 998", body)
        self.assertEqual(read(DERP_FILE), "regions: {}\n")

    def test_rejected_by_headscale_shows_error_and_rolls_back(self):
        with mock.patch.object(hs, "headscale_configtest", return_value=(False, "nope")), \
                mock.patch.object(hs, "restart_headscale", return_value=True):
            status, _h, body = self.post()
        self.assertEqual(status, 400)
        self.assertIn("nope", body)
        self.assertEqual(read(CONFIG), BASE_CONFIG)
        self.assertEqual(read(DERP_FILE), "regions: {}\n")

    def test_demo_blocks_it(self):
        self.assertRegex(f"{B}/derp", app.DEMO_BLOCKED)


if __name__ == "__main__":
    unittest.main()
