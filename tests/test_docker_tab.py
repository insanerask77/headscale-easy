"""Docker tab of Add device: the snippets, input validation (no shell or YAML
injection), key generation permissions and the page. Standard library only:

    python3 tests/test_docker_tab.py
"""
import json
import os
import shlex
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_security import ADMIN, B, Base, MEMBER, app, audit, hs, location, request  # noqa: E402

import docker_tab  # noqa: E402

URL = "https://vpn.example.com"
AUDITOR = dict(MEMBER, role="auditor", sub="c", username="carol")


def parsed(**form):
    values, error = docker_tab.parse(form)
    return values, error


class Snippets(unittest.TestCase):
    def test_defaults(self):
        v, err = parsed()
        self.assertEqual(err, "")
        run = docker_tab.docker_run(URL, v, "")
        self.assertIn("tailscale/tailscale:latest", run)
        self.assertIn("--login-server=https://vpn.example.com", run)
        self.assertIn("TS_AUTHKEY=<auth-key>", run)
        self.assertIn("--device /dev/net/tun", run)
        self.assertIn("--cap-add NET_ADMIN", run)
        self.assertIn("--restart unless-stopped", run)
        self.assertIn("TS_STATE_DIR=/var/lib/tailscale", run)
        self.assertNotIn("sysctl", run)
        yml = docker_tab.compose(URL, v, "")
        self.assertIn("restart: unless-stopped", yml)
        self.assertIn("/dev/net/tun", yml)
        self.assertIn("tailscale-state:/var/lib/tailscale", yml)

    def test_options_change_the_snippet(self):
        v, err = parsed(hostname="Edge-1", exit="1", routes="192.168.1.0/24, 10.0.0.0/8", dns="")
        self.assertEqual(err, "")
        run = docker_tab.docker_run(URL, v, "fake-authkey-one")
        for part in ("--advertise-exit-node", "--advertise-routes=192.168.1.0/24,10.0.0.0/8", "--accept-dns=false",
                     "TS_AUTHKEY=fake-authkey-one", "TS_HOSTNAME=edge-1", "net.ipv4.ip_forward=1"):
            self.assertIn(part, run)
        self.assertIn("sysctls:", docker_tab.compose(URL, v, ""))

    def test_userspace_needs_no_device_or_capabilities(self):
        v, _e = parsed(userspace="1", exit="1")
        run = docker_tab.docker_run(URL, v, "")
        self.assertIn("TS_USERSPACE=true", run)
        self.assertNotIn("/dev/net/tun", run)
        self.assertNotIn("cap-add", run)
        self.assertNotIn("sysctl", run)
        self.assertNotIn("cap_add", docker_tab.compose(URL, v, ""))

    def test_shell_command_survives_a_shell_parse(self):
        v, _e = parsed(hostname="edge-1", routes="10.0.0.0/8", dns="1")
        words = shlex.split(docker_tab.docker_run(URL, v, "").replace("\\\n", " "))
        self.assertIn("TS_EXTRA_ARGS=--login-server=https://vpn.example.com --advertise-routes=10.0.0.0/8", words)

    def test_compose_is_valid_json_quoted_yaml(self):
        v, _e = parsed(hostname="edge-1")
        yml = docker_tab.compose(URL, v, 'k"ey: x')
        self.assertIn(json.dumps('TS_AUTHKEY=k"ey: x'), yml)  # quoted, so the colon and quote cannot break the YAML

    def test_validation(self):
        for form in ({"hostname": "bad name"}, {"hostname": "a;rm -rf /"}, {"hostname": "-x"}, {"hostname": "x" * 64},
                     {"routes": "192.168.1.0/24; rm -rf /"}, {"routes": "not-a-route"}, {"routes": "192.168.1.5/24"},
                     {"routes": " ".join(f"10.{i}.0.0/16" for i in range(docker_tab.MAX_ROUTES + 1))}):
            _v, error = parsed(**form)
            self.assertTrue(error, form)

    def test_error_page_shows_no_snippets(self):
        html = docker_tab.panel(MEMBER, URL, *parsed(hostname="bad name")[:1], error="boom")
        self.assertNotIn("docker run", html)
        self.assertIn("boom", html)


class Page(Base):
    def setUp(self):
        super().setUp()
        self.api.side_effect = None
        self.api.return_value = {"preAuthKey": {"key": "fake-authkey-two"}}
        for target, value in (("all_users", lambda: [{"id": "2", "name": "bob"}, {"id": "9", "name": "ann"}]),
                              ("all_nodes", lambda: [])):
            p = mock.patch.object(hs, target, value)
            p.start()
            self.addCleanup(p.stop)

    def test_tab_is_on_the_add_page_with_this_servers_url(self):
        status, _h, body = request("GET", f"{B}/add", MEMBER)
        self.assertEqual(status, 200)
        self.assertIn('data-tab="docker"', body)
        self.assertIn("--login-server=https://vpn.example.com", body)
        self.assertIn("&lt;auth-key&gt;", body)

    def post(self, session, **form):
        return request("POST", f"{B}/add/docker", session, dict({"csrf": "tok", "hostname": "edge-1", "dns": "1"}, **form))

    def test_needs_csrf(self):
        self.assertEqual(request("POST", f"{B}/add/docker", MEMBER, {"hostname": "x"})[0], 403)

    def test_options_without_a_key(self):
        status, _h, body = self.post(MEMBER, exit="1")
        self.assertEqual(status, 200)
        self.assertIn("--advertise-exit-node", body)
        self.assertEqual(self.api.call_count, 0)  # no key unless asked

    def test_invalid_input_is_rejected_and_makes_no_key(self):
        status, _h, body = self.post(MEMBER, hostname="a b", generate="1")
        self.assertEqual(status, 400)
        self.assertNotIn("docker run", body)
        self.assertEqual(self.api.call_count, 0)

    def test_member_key_is_single_use_short_and_theirs(self):
        status, _h, body = self.post(MEMBER, generate="1", days="7", user_id="9")  # a member cannot pick another owner
        self.assertEqual(status, 200)
        self.assertIn("TS_AUTHKEY=fake-authkey-two", body)
        method, path, payload = self.api.call_args.args[:3]
        self.assertEqual((method, path), ("POST", "/preauthkey"))
        self.assertEqual((payload["user"], payload["reusable"], payload["ephemeral"]), ("2", False, False))
        self.assertIn("authkey-two", body)

    def test_admin_chooses_the_owner(self):
        _s, _h, body = self.post(ADMIN, generate="1", user_id="9")
        self.assertEqual(self.api.call_args.args[2]["user"], "9")
        self.assertIn('name="user_id"', body)
        _s, _h, body = request("GET", f"{B}/add", MEMBER)
        self.assertNotIn('name="user_id"', body)

    def test_auditors_cannot_generate(self):
        self.assertEqual(self.post(AUDITOR, generate="1")[0], 403)
        self.assertEqual(self.api.call_count, 0)

    def test_key_never_reaches_the_audit_log(self):
        self.post(MEMBER, generate="1")
        self.assertTrue(audit.request_event.called)
        self.assertNotIn("authkey-two", repr(audit.request_event.call_args_list))

    def test_key_is_not_in_the_next_page(self):
        self.post(MEMBER, generate="1")
        self.assertNotIn("authkey-two", request("GET", f"{B}/add", MEMBER)[2])

    def test_html_is_escaped(self):
        _s, _h, body = self.post(MEMBER, routes="<script>alert(1)</script>")
        self.assertNotIn("<script>alert", body)


if __name__ == "__main__":
    unittest.main()
