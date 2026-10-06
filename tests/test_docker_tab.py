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
        v, err = parsed(dns="1")
        self.assertEqual(err, "")
        run = docker_tab.docker_run(URL, v, "")
        self.assertIn("tailscale/tailscale:v1.102.5", run)
        self.assertNotIn(":latest", run)
        self.assertIn("image: tailscale/tailscale:v1.102.5", docker_tab.compose(URL, v, ""))
        self.assertIn("--login-server=https://vpn.example.com", run)
        self.assertIn("TS_AUTHKEY=<auth-key>", run)
        self.assertIn("--device /dev/net/tun", run)
        self.assertIn("--cap-add NET_ADMIN", run)
        self.assertIn("--restart unless-stopped", run)
        self.assertIn("TS_STATE_DIR=/var/lib/tailscale", run)
        self.assertIn("TS_AUTH_ONCE=true", run)
        self.assertIn("TS_ACCEPT_DNS=true", run)
        self.assertNotIn("sysctl", run)
        yml = docker_tab.compose(URL, v, "")
        self.assertIn("restart: unless-stopped", yml)
        self.assertIn("/dev/net/tun", yml)
        self.assertIn("tailscale-state:/var/lib/tailscale", yml)

    def test_version_can_be_chosen_and_unknown_ones_fall_back_to_the_tested_tag(self):
        v, _e = parsed(version="latest")
        self.assertTrue(docker_tab.docker_run(URL, v, "").endswith("tailscale/tailscale:latest"))
        self.assertIn("image: tailscale/tailscale:latest", docker_tab.compose(URL, v, ""))
        v, _e = parsed(version="evil; rm -rf /")
        self.assertEqual(v["version"], docker_tab.TS_VERSIONS[0])
        self.assertNotIn("evil", docker_tab.docker_run(URL, v, ""))

    def test_options_change_the_snippet(self):
        v, err = parsed(hostname="Edge-1", exit="1", routes="192.168.1.0/24, 10.0.0.0/8", dns="")
        self.assertEqual(err, "")
        run = docker_tab.docker_run(URL, v, "fake-authkey-one")
        for part in ("TS_ROUTES=0.0.0.0/0,::/0,192.168.1.0/24,10.0.0.0/8", "TS_ACCEPT_DNS=false",
                     "TS_AUTHKEY=fake-authkey-one", "TS_HOSTNAME=edge-1", "net.ipv4.ip_forward=1"):
            self.assertIn(part, run)
        self.assertIn("sysctls:", docker_tab.compose(URL, v, ""))

    def test_exit_node_goes_in_ts_routes_never_in_extra_args(self):
        v, _e = parsed(exit="1")
        for snippet in (docker_tab.docker_run(URL, v, ""), docker_tab.compose(URL, v, "")):
            self.assertIn("TS_ROUTES=0.0.0.0/0,::/0", snippet)
            self.assertNotIn("--advertise-exit-node", snippet)

    def test_kernel_exit_node_gets_the_troubleshooting_tips_from_the_docs(self):
        v, _e = parsed(exit="1")
        html = docker_tab.panel({"csrf": "tok"}, URL, v)
        self.assertIn("net.ipv4.ip_forward = 1", html)
        self.assertIn("net.ipv6.conf.all.forwarding = 1", html)
        self.assertIn("sysctl -p /etc/sysctl.d/99-tailscale.conf", html)
        for plain in (parsed(), parsed(userspace="1", exit="1")):  # nothing to forward: no tips
            self.assertNotIn("ip_forward", docker_tab.panel({"csrf": "tok"}, URL, plain[0]))

    def test_kernel_mode_sets_firewall_mode_auto_for_nftables_hosts(self):
        v, _e = parsed(exit="1")
        self.assertIn("TS_DEBUG_FIREWALL_MODE=auto", docker_tab.docker_run(URL, v, ""))
        self.assertIn("TS_DEBUG_FIREWALL_MODE=auto", docker_tab.compose(URL, v, ""))
        u, _e = parsed(userspace="1", exit="1")  # userspace mode has no firewall
        self.assertNotIn("FIREWALL_MODE", docker_tab.docker_run(URL, u, ""))
        self.assertNotIn("FIREWALL_MODE", docker_tab.compose(URL, u, ""))

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
        self.assertIn("TS_ROUTES=10.0.0.0/8", words)
        self.assertIn("TS_EXTRA_ARGS=--login-server=https://vpn.example.com", words)

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


class LoopbackWarning(unittest.TestCase):
    def test_detects_addresses_a_container_cannot_use(self):
        for url in ("http://localhost:8088", "https://localhost", "http://127.0.0.1:8080", "http://[::1]:8080",
                    "http://0.0.0.0:80", "http://hse.localhost"):
            self.assertTrue(docker_tab.loopback(url), url)
        for url in ("https://vpn.example.com", "http://192.168.1.10:8088", "http://172.17.0.1:8088",
                    "http://host.docker.internal:8088"):
            self.assertFalse(docker_tab.loopback(url), url)

    def test_panel_warns_only_for_loopback(self):
        v, _e = parsed(hostname="edge-1")
        self.assertIn("--network host", docker_tab.panel(MEMBER, "http://localhost:8088", v))
        self.assertNotIn("--network host", docker_tab.panel(MEMBER, "https://vpn.example.com", v))


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
        self.assertIn("TS_ROUTES=0.0.0.0/0,::/0", body)
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

    def test_admin_sees_the_owner_picker_before_sending_the_form(self):
        # the first time the tab is opened (GET), not only after an error: generating a key needs an owner
        with mock.patch.object(hs, "all_users", return_value=[{"id": "9", "name": "bob"}]):
            _s, _h, body = request("GET", f"{B}/add", ADMIN)
        self.assertIn('name="user_id"', body)
        self.assertIn('<option value="9"', body)

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
