"""hs-helper (helper/helper.py) and the web UI's client for it.

The helper is the only container with the Docker socket. These tests run the
real helper on a temporary Unix socket in front of a fake Docker Engine API
(another Unix socket) and check that:

- only POST /configtest, POST /restart and GET /status are served; any other
  path, method, query string or request body is refused and never reaches
  Docker;
- every Docker call targets the fixed Headscale container;
- the web UI (web/headscale.py) uses the helper, falls back to a mounted
  Docker socket only when the helper is missing, and fails closed with
  neither.

Standard library only:

    python3 tests/test_docker_helper.py
"""
import http.client
import http.server
import json
import os
import socket
import socketserver
import sys
import tempfile
import threading
import unittest
import urllib.parse
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.update(HEADSCALE_API_KEY="x", PUBLIC_URL="https://vpn.example.com", SESSION_SECRET="s",
                  HEADSCALE_URL="http://headscale:8080")
# Unix socket paths are limited to ~108 bytes: keep them short
SHORT_TMP = "/tmp" if os.path.isdir("/tmp") else None
sys.path.insert(0, os.path.join(ROOT, "helper"))
sys.path.insert(0, os.path.join(ROOT, "web"))

import helper  # noqa: E402
import headscale as hs  # noqa: E402


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path: str):
        super().__init__("localhost", timeout=10)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self._path)


def call(path: str, method: str, target: str, body: bytes | None = None,
         headers: dict | None = None) -> tuple[int, dict, dict]:
    conn = UnixHTTP(path)
    try:
        try:
            conn.request(method, target, body=body, headers=headers or {})
        except (BrokenPipeError, ConnectionResetError):
            pass  # the helper may answer and close before the body is sent
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, dict(resp.getheaders()), (json.loads(raw) if raw else {})
    finally:
        conn.close()


class FakeDocker:
    """A Docker Engine API that records every call. Knows one container."""

    def __init__(self, path: str):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.exit_code = 0
        self.output = b"\x1b[32mConfig OK\x1b[0m\r\n"
        self.version_output = b"headscale version v0.26.1\r\ncommit: 8106636c\r\nbuilt with: go1.26.5 linux/amd64\r\n"
        self.health = "healthy"
        self.last_cmd: list[str] = []
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def address_string(self):
                return "unix"

            def _reply(self, code, payload=None):
                body = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _raw(self, data: bytes):
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def handle_one(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                fake.calls.append((self.command, self.path, body))
                url = urllib.parse.urlsplit(self.path)
                p = url.path
                if p == "/_ping":
                    return self._raw(b"OK")
                if p == "/containers/headscale/exec":
                    fake.last_cmd = body["Cmd"]
                    return self._reply(201, {"Id": "e1"})
                if p == "/exec/e1/start":
                    return self._raw(fake.version_output if fake.last_cmd == ["headscale", "version"]
                                     else fake.output)
                if p == "/exec/e1/json":
                    return self._reply(200, {"ExitCode": fake.exit_code})
                if p == "/containers/headscale/restart":
                    return self._reply(204)
                if p == "/containers/headscale/json":
                    return self._reply(200, {"State": {"Health": {"Status": fake.health}},
                                             "Config": {"Labels": {"com.docker.compose.project": "hse"}}})
                if p == "/containers/json":
                    return self._reply(200, [
                        {"Names": ["/headscale"], "Image": "headscale/headscale:latest", "State": "running",
                         "Status": "Up 2 hours (healthy)",
                         "Labels": {"com.docker.compose.service": "headscale"}},
                        {"Names": ["/caddy"], "Image": "caddy:2-alpine", "State": "exited",
                         "Status": "Exited (1) 3 minutes ago",
                         "Labels": {"com.docker.compose.service": "caddy"}},
                    ])
                return self._reply(404, {"message": "no such thing"})

            do_GET = do_POST = do_DELETE = handle_one

        self.server = _UnixServer(path, H)
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        conn, _ = super().get_request()
        return conn, ("unix", 0)


class HelperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=SHORT_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.docker_sock = os.path.join(self.tmp.name, "docker.sock")
        self.helper_sock = os.path.join(self.tmp.name, "helper.sock")
        self.docker = FakeDocker(self.docker_sock)
        self.addCleanup(self.docker.close)
        for name, value in (("DOCKER_SOCKET", self.docker_sock), ("HEADSCALE_CONTAINER", "headscale"),
                            ("RESTART_WAIT", 5.0)):
            p = mock.patch.object(helper, name, value)
            p.start()
            self.addCleanup(p.stop)
        sleep = mock.patch.object(helper.time, "sleep", lambda s: None)
        sleep.start()
        self.addCleanup(sleep.stop)
        helper._version_cache.update(at=0.0, value=None)
        self.server = helper.serve(self.helper_sock)
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def req(self, method, target, **kw):
        return call(self.helper_sock, method, target, **kw)

    # --- what is refused --------------------------------------------------------

    def test_socket_is_not_world_accessible(self):
        self.assertEqual(os.stat(self.helper_sock).st_mode & 0o777, 0o660)

    def test_unknown_paths_are_404_and_never_reach_docker(self):
        for target in ("/", "/containers/json", "/containers/headscale/exec", "/_ping", "/status/",
                       "/status/x", "/restart/../status", "/configtest/x", "/STATUS", "/exec/e1/start"):
            for method in ("GET", "POST"):
                with self.subTest(method=method, target=target):
                    code, _, body = self.req(method, target)
                    self.assertEqual(code, 404)
                    self.assertFalse(body["ok"])
        self.assertEqual(self.docker.calls, [])

    def test_wrong_methods_are_405(self):
        cases = [("GET", "/configtest"), ("GET", "/restart"), ("POST", "/status"), ("DELETE", "/restart"),
                 ("PUT", "/configtest"), ("PATCH", "/status"), ("HEAD", "/restart")]
        for method, target in cases:
            with self.subTest(method=method, target=target):
                code, headers, _ = self.req(method, target)
                self.assertEqual(code, 405)
                self.assertIn("Allow", headers)
        self.assertEqual(self.docker.calls, [])

    def test_query_strings_are_refused(self):
        for method, target in (("POST", "/restart?t=0"), ("GET", "/status?all=1"),
                               ("POST", "/configtest?container=caddy"), ("POST", "/restart?")):
            with self.subTest(target=target):
                self.assertEqual(self.req(method, target)[0], 400)
        self.assertEqual(self.docker.calls, [])

    def test_request_bodies_are_refused(self):
        body = json.dumps({"container": "caddy", "Cmd": ["sh", "-c", "id"]}).encode()
        code, _, _ = self.req("POST", "/configtest", body=body, headers={"Content-Type": "application/json"})
        self.assertEqual(code, 400)
        code, _, _ = self.req("POST", "/restart", body=b"x")
        self.assertEqual(code, 400)
        self.assertEqual(self.docker.calls, [])

    def test_container_name_comes_from_the_environment_only(self):
        with mock.patch.object(helper, "HEADSCALE_CONTAINER", "../../containers/caddy"):
            self.req("POST", "/restart")
        paths = [p for _, p, _ in self.docker.calls]
        self.assertTrue(paths)
        self.assertTrue(all(p.startswith("/containers/..%2F..%2Fcontainers%2Fcaddy/") for p in paths), paths)

    # --- what is served ---------------------------------------------------------

    def test_configtest_runs_only_headscale_configtest(self):
        code, _, body = self.req("POST", "/configtest")
        self.assertEqual(code, 200)
        self.assertEqual(body, {"ok": True, "output": "Config OK"})
        execs = [b for m, p, b in self.docker.calls if p == "/containers/headscale/exec"]
        self.assertEqual([e["Cmd"] for e in execs], [["headscale", "configtest"]])

    def test_configtest_reports_a_failure(self):
        self.docker.exit_code, self.docker.output = 1, b"FTL invalid dns\r\n"
        code, _, body = self.req("POST", "/configtest")
        self.assertEqual(code, 200)
        self.assertEqual(body, {"ok": False, "output": "FTL invalid dns"})

    def test_restart_waits_for_healthy(self):
        code, _, body = self.req("POST", "/restart")
        self.assertEqual((code, body), (200, {"ok": True}))
        self.assertIn(("POST", "/containers/headscale/restart?t=10", None), self.docker.calls)

    def test_restart_times_out_when_unhealthy(self):
        self.docker.health = "unhealthy"
        with mock.patch.object(helper, "RESTART_WAIT", 0.05):
            code, _, body = self.req("POST", "/restart")
        self.assertEqual(code, 200)
        self.assertFalse(body["ok"])

    def test_status_contract(self):
        code, _, body = self.req("GET", "/status")
        self.assertEqual(code, 200)
        self.assertEqual(body, {
            "api": 1,
            "docker": True,
            "headscale": {"container": "headscale", "version": "v0.26.1"},
            "containers": [
                {"name": "caddy", "service": "caddy", "image": "caddy:2-alpine", "state": "exited",
                 "health": None, "status": "Exited (1) 3 minutes ago"},
                {"name": "headscale", "service": "headscale", "image": "headscale/headscale:latest",
                 "state": "running", "health": "healthy", "status": "Up 2 hours (healthy)"},
            ],
        })
        listing = [p for _, p, _ in self.docker.calls if p.startswith("/containers/json")][0]
        filters = urllib.parse.parse_qs(urllib.parse.urlsplit(listing).query)["filters"][0]
        self.assertEqual(json.loads(filters), {"label": ["com.docker.compose.project=hse"]})

    def test_status_caches_the_version(self):
        self.req("GET", "/status")
        self.req("GET", "/status")
        execs = [p for _, p, _ in self.docker.calls if p == "/containers/headscale/exec"]
        self.assertEqual(len(execs), 1)

    def test_status_without_docker(self):
        with mock.patch.object(helper, "DOCKER_SOCKET", os.path.join(self.tmp.name, "missing.sock")):
            code, _, body = self.req("GET", "/status")
        self.assertEqual(code, 200)
        self.assertEqual(body, {"api": 1, "docker": False,
                                "headscale": {"container": "headscale", "version": None}, "containers": []})

    def test_actions_without_docker_are_503(self):
        with mock.patch.object(helper, "DOCKER_SOCKET", os.path.join(self.tmp.name, "missing.sock")):
            for target in ("/configtest", "/restart"):
                code, _, body = self.req("POST", target)
                self.assertEqual(code, 503)
                self.assertFalse(body["ok"])

    def test_stale_socket_is_replaced(self):
        path = os.path.join(self.tmp.name, "stale.sock")
        with open(path, "w"):
            pass
        server = helper.serve(path)
        server.server_close()
        self.assertTrue(os.path.exists(path))


class BackupRouteTest(unittest.TestCase):
    """POST /backup: served only when the backend exists (all-in-one supervisor)."""

    def serve(self, backends):
        self.tmp = tempfile.TemporaryDirectory(dir=SHORT_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.sock = os.path.join(self.tmp.name, "helper.sock")
        server = helper.serve(self.sock, backends)
        threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

    def test_route_is_404_on_the_docker_helper(self):
        self.serve(None)
        code, _, body = call(self.sock, "POST", "/backup")
        self.assertEqual(code, 404)
        self.assertFalse(body["ok"])

    def test_served_by_a_backend(self):
        calls = []
        self.serve({"backup": lambda: (calls.append(1) or (200, {"ok": True, "started": True}))})
        code, _, body = call(self.sock, "POST", "/backup")
        self.assertEqual((code, body), (200, {"ok": True, "started": True}))
        self.assertEqual(calls, [1])

    def test_busy_is_reported(self):
        self.serve({"backup": lambda: (200, {"ok": False, "error": "already running"})})
        code, _, body = call(self.sock, "POST", "/backup")
        self.assertEqual((code, body["error"]), (200, "already running"))

    def test_contract_wrong_method_query_and_body(self):
        calls = []
        self.serve({"backup": lambda: (calls.append(1) or (200, {"ok": True, "started": True}))})
        for method in ("GET", "PUT", "DELETE"):
            code, headers, _ = call(self.sock, method, "/backup")
            self.assertEqual(code, 405, method)
            self.assertEqual(headers.get("Allow"), "POST")
        self.assertEqual(call(self.sock, "POST", "/backup?out=/tmp")[0], 400)
        self.assertEqual(call(self.sock, "POST", "/backup", body=b'{"out": "/tmp"}')[0], 400)
        self.assertEqual(call(self.sock, "POST", "/backup/")[0], 404)
        self.assertEqual(calls, [])

    def test_backend_failure_does_not_leak(self):
        def boom():
            raise RuntimeError("secret path /data/x")
        self.serve({"backup": boom})
        code, _, body = call(self.sock, "POST", "/backup")
        self.assertEqual((code, body), (500, {"ok": False, "error": "internal error"}))


class WebClientTest(unittest.TestCase):
    """web/headscale.py against the real helper (over a fake Docker)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=SHORT_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.docker_sock = os.path.join(self.tmp.name, "docker.sock")
        self.helper_sock = os.path.join(self.tmp.name, "helper.sock")
        self.docker = FakeDocker(self.docker_sock)
        self.addCleanup(self.docker.close)
        for target, name, value in ((helper, "DOCKER_SOCKET", self.docker_sock), (helper, "RESTART_WAIT", 5.0),
                                    (hs, "HELPER_SOCKET", self.helper_sock),
                                    (hs, "DOCKER_SOCKET", os.path.join(self.tmp.name, "no-docker.sock")),
                                    (hs, "_legacy_warned", False)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        for mod in (helper, hs):
            p = mock.patch.object(mod.time, "sleep", lambda s: None)
            p.start()
            self.addCleanup(p.stop)
        helper._version_cache.update(at=0.0, value=None)

    def start_helper(self):
        server = helper.serve(self.helper_sock)
        threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

    def test_helper_backup_started_busy_and_error(self):
        answers = iter([(200, {"ok": True, "started": True}), (200, {"ok": False, "error": "already running"}),
                        (200, {"ok": False, "error": "disk full"})])
        server = helper.serve(self.helper_sock, {"backup": lambda: next(answers)})
        threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.assertEqual([hs.helper_backup() for _ in range(3)], ["started", "busy", "error"])

    def test_helper_backup_unavailable_without_helper_or_on_the_docker_helper(self):
        self.assertEqual(hs.helper_backup(), "unavailable")  # no socket
        self.start_helper()                                   # 1.x helper: the route is a 404
        self.assertEqual(hs.helper_backup(), "unavailable")

    def test_helper_backup_dead_socket_is_an_error(self):
        with open(self.helper_sock, "w"):
            pass
        self.assertEqual(hs.helper_backup(), "error")

    def test_uses_the_helper(self):
        self.start_helper()
        self.assertTrue(hs.docker_available())
        self.assertEqual(hs.headscale_configtest(), (True, "Config OK"))
        self.assertTrue(hs.restart_headscale())
        status = hs.helper_status()
        self.assertEqual(status["headscale"]["version"], "v0.26.1")

    def test_configtest_failure_through_the_helper(self):
        self.start_helper()
        self.docker.exit_code, self.docker.output = 1, b"FTL bad\r\n"
        self.assertEqual(hs.headscale_configtest(), (False, "FTL bad"))

    def test_helper_without_docker(self):
        self.start_helper()
        with mock.patch.object(helper, "DOCKER_SOCKET", os.path.join(self.tmp.name, "missing.sock")):
            self.assertFalse(hs.docker_available())
            ok, out = hs.headscale_configtest()
            self.assertFalse(ok)
            self.assertIn("503", out)
            self.assertFalse(hs.restart_headscale())

    def test_helper_socket_present_but_dead(self):
        with open(self.helper_sock, "w"):
            pass
        self.assertFalse(hs.docker_available())
        self.assertIsNone(hs.helper_status())
        self.assertFalse(hs.headscale_configtest()[0])
        self.assertFalse(hs.restart_headscale())

    def test_nothing_available_fails_closed(self):
        self.assertFalse(hs.docker_available())
        self.assertIsNone(hs.helper_status())
        self.assertEqual(hs.headscale_configtest(), (False, "hs-helper is not running"))
        self.assertFalse(hs.restart_headscale())
        self.assertEqual(self.docker.calls, [])

    def test_legacy_docker_socket_fallback_with_warning(self):
        with mock.patch.object(hs, "DOCKER_SOCKET", self.docker_sock), \
                self.assertLogs("headscale-easy", level="WARNING") as logs:
            self.assertTrue(hs.docker_available())
            self.assertEqual(hs.headscale_configtest(), (True, "Config OK"))
            self.assertTrue(hs.restart_headscale())
        self.assertEqual(len(logs.records), 1)
        self.assertIn("hs-helper not found", logs.output[0])

    def test_helper_wins_over_a_mounted_docker_socket(self):
        self.start_helper()
        with mock.patch.object(hs, "DOCKER_SOCKET", self.docker_sock), \
                mock.patch.object(hs, "docker", mock.Mock(side_effect=AssertionError("direct Docker call"))):
            self.assertEqual(hs.headscale_configtest(), (True, "Config OK"))
            self.assertTrue(hs.restart_headscale())


class ComposeTest(unittest.TestCase):
    def test_only_the_helper_mounts_the_docker_socket(self):
        service = None
        with open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("  ") and not line.startswith("   ") and line.rstrip().endswith(":"):
                    service = line.strip().rstrip(":")
                if "docker.sock" in line and not line.lstrip().startswith("#"):
                    self.assertEqual(service, "hs-helper", line)


if __name__ == "__main__":
    unittest.main()
