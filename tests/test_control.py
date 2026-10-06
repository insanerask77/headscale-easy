"""The control socket (aio/control.py) and the console's client for it (web/headscale.py).

The supervisor serves a fixed contract on a Unix socket; the console never touches Docker or the
Headscale process. These tests run the real server on a temporary socket with fake backends and
check what is accepted and, above all, what is refused:

- exact paths only: no query strings, no request bodies, nothing outside the route table;
- the socket is not world accessible;
- a backend failure never leaks a traceback;
- the console client (web/headscale.py) maps each answer, writes request files with mode 600 and
  fails closed when the socket is missing or dead.

    python3 -m unittest tests.test_control
    python3 tests/test_control.py
"""
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.update(HEADSCALE_API_KEY="x", PUBLIC_URL="https://vpn.example.com", SESSION_SECRET="s",
                  HEADSCALE_URL="http://headscale:8080")
# Unix socket paths are limited to ~108 bytes: keep them short
SHORT_TMP = "/tmp" if os.path.isdir("/tmp") else None
sys.path.insert(0, os.path.join(ROOT, "aio"))
sys.path.insert(0, os.path.join(ROOT, "web"))

import control  # noqa: E402
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
            pass  # the server may answer and close before the body is sent
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, dict(resp.getheaders()), (json.loads(raw) if raw else {})
    finally:
        conn.close()


class ServedTest(unittest.TestCase):
    """A control server on a temporary socket; ``self.serve(backends)`` (re)starts it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=SHORT_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.sock = os.path.join(self.tmp.name, "control.sock")

    def serve(self, backends):
        server = control.serve(self.sock, backends)
        threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def req(self, method, target, **kw):
        return call(self.sock, method, target, **kw)


class ProtocolTest(ServedTest):
    def setUp(self):
        super().setUp()
        self.calls = []

        def backend(name, payload):
            return lambda: (self.calls.append(name) or (200, payload))

        self.serve({"configtest": backend("configtest", {"ok": True, "output": "Config OK"}),
                    "restart": backend("restart", {"ok": True}),
                    "status": backend("status", {"ok": True, "api": control.API_VERSION})})

    def test_socket_is_not_world_accessible(self):
        self.assertEqual(os.stat(self.sock).st_mode & 0o777, 0o660)

    def test_the_contract_is_served(self):
        self.assertEqual(self.req("POST", "/configtest")[2], {"ok": True, "output": "Config OK"})
        self.assertEqual(self.req("POST", "/restart")[2], {"ok": True})
        self.assertEqual(self.req("GET", "/status")[2]["api"], control.API_VERSION)
        self.assertEqual(self.calls, ["configtest", "restart", "status"])

    def test_unknown_paths_are_404_and_reach_no_backend(self):
        for target in ("/", "/containers/json", "/containers/headscale/exec", "/_ping", "/status/",
                       "/status/x", "/restart/../status", "/configtest/x", "/STATUS", "/exec/e1/start"):
            for method in ("GET", "POST"):
                with self.subTest(method=method, target=target):
                    code, _, body = self.req(method, target)
                    self.assertEqual(code, 404)
                    self.assertFalse(body["ok"])
        self.assertEqual(self.calls, [])

    def test_wrong_methods_are_405(self):
        cases = [("GET", "/configtest"), ("GET", "/restart"), ("POST", "/status"), ("DELETE", "/restart"),
                 ("PUT", "/configtest"), ("PATCH", "/status"), ("HEAD", "/restart")]
        for method, target in cases:
            with self.subTest(method=method, target=target):
                code, headers, _ = self.req(method, target)
                self.assertEqual(code, 405)
                self.assertIn("Allow", headers)
        self.assertEqual(self.calls, [])

    def test_query_strings_are_refused(self):
        for method, target in (("POST", "/restart?t=0"), ("GET", "/status?all=1"),
                               ("POST", "/configtest?container=caddy"), ("POST", "/restart?")):
            with self.subTest(target=target):
                self.assertEqual(self.req(method, target)[0], 400)
        self.assertEqual(self.calls, [])

    def test_request_bodies_are_refused(self):
        body = json.dumps({"container": "caddy", "Cmd": ["sh", "-c", "id"]}).encode()
        code, _, _ = self.req("POST", "/configtest", body=body, headers={"Content-Type": "application/json"})
        self.assertEqual(code, 400)
        code, _, _ = self.req("POST", "/restart", body=b"x")
        self.assertEqual(code, 400)
        self.assertEqual(self.calls, [])

    def test_stale_socket_is_replaced(self):
        path = os.path.join(self.tmp.name, "stale.sock")
        with open(path, "w"):
            pass
        server = control.serve(path, {})
        server.server_close()
        self.assertTrue(os.path.exists(path))


class BackupRouteTest(ServedTest):
    """The backup routes exist only when the supervisor supplies a backend."""

    def test_settings_route_takes_no_body(self):
        self.serve({})
        self.assertEqual(self.req("POST", "/backup-settings")[0], 404)
        self.serve({"backup_settings": lambda: (200, {"ok": True})})
        self.assertEqual(self.req("POST", "/backup-settings")[0], 200)
        self.assertEqual(self.req("POST", "/backup-settings", body=b'{"a": 1}')[0], 400)
        self.assertEqual(self.req("GET", "/backup-settings")[0], 405)

    def test_upload_route_takes_no_body(self):
        self.serve({})
        self.assertEqual(self.req("POST", "/backup-upload")[0], 404)
        self.serve({"backup_upload": lambda: (200, {"ok": True})})
        self.assertEqual(self.req("POST", "/backup-upload")[0], 200)
        self.assertEqual(self.req("POST", "/backup-upload", body=b"x")[0], 400)

    def test_restore_route_takes_no_body(self):
        self.serve({})
        self.assertEqual(self.req("POST", "/restore")[0], 404)
        self.serve({"restore": lambda: (200, {"ok": True})})
        self.assertEqual(self.req("POST", "/restore")[0], 200)
        self.assertEqual(self.req("POST", "/restore", body=b'{"name": "x"}')[0], 400)
        self.assertEqual(self.req("GET", "/restore")[0], 405)

    def test_route_without_a_backend_is_404(self):
        self.serve({})
        code, _, body = self.req("POST", "/backup")
        self.assertEqual(code, 404)
        self.assertFalse(body["ok"])

    def test_served_by_a_backend(self):
        calls = []
        self.serve({"backup": lambda: (calls.append(1) or (200, {"ok": True, "started": True}))})
        code, _, body = self.req("POST", "/backup")
        self.assertEqual((code, body), (200, {"ok": True, "started": True}))
        self.assertEqual(calls, [1])

    def test_busy_is_reported(self):
        self.serve({"backup": lambda: (200, {"ok": False, "error": "already running"})})
        code, _, body = self.req("POST", "/backup")
        self.assertEqual((code, body["error"]), (200, "already running"))

    def test_contract_wrong_method_query_and_body(self):
        calls = []
        self.serve({"backup": lambda: (calls.append(1) or (200, {"ok": True, "started": True}))})
        for method in ("GET", "PUT", "DELETE"):
            code, headers, _ = self.req(method, "/backup")
            self.assertEqual(code, 405, method)
            self.assertEqual(headers.get("Allow"), "POST")
        self.assertEqual(self.req("POST", "/backup?out=/tmp")[0], 400)
        self.assertEqual(self.req("POST", "/backup", body=b'{"out": "/tmp"}')[0], 400)
        self.assertEqual(self.req("POST", "/backup/")[0], 404)
        self.assertEqual(calls, [])

    def test_backend_failure_does_not_leak(self):
        def boom():
            raise RuntimeError("secret path /data/x")
        self.serve({"backup": boom})
        code, _, body = self.req("POST", "/backup")
        self.assertEqual((code, body), (500, {"ok": False, "error": "internal error"}))


class WebClientTest(ServedTest):
    """web/headscale.py against the real control server."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(hs, "CONTROL_SOCKET", self.sock)
        p.start()
        self.addCleanup(p.stop)

    def test_backup_started_busy_and_error(self):
        answers = iter([(200, {"ok": True, "started": True}), (200, {"ok": False, "error": "already running"}),
                        (200, {"ok": False, "error": "disk full"})])
        self.serve({"backup": lambda: next(answers)})
        self.assertEqual([hs.control_backup() for _ in range(3)], ["started", "busy", "error"])

    def test_backup_settings_results_and_request_file(self):
        answers = iter([(200, {"ok": True}), (200, {"ok": False, "error": "bad", "field": "schedule"}),
                        (200, {"ok": False, "error": "env", "field": "env"}), (200, {"ok": False, "error": "x"})])
        self.serve({"backup_settings": lambda: next(answers)})
        self.assertEqual(hs.control_backup_settings(True, "0 3 * * *", "14"), ("saved", ""))
        path = os.path.join(os.path.dirname(self.sock), "backup-settings.json")
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"enabled": True, "schedule": "0 3 * * *", "keep_days": "14"})
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(hs.control_backup_settings(True, "foo", "14"), ("invalid", "bad"))
        self.assertEqual(hs.control_backup_settings(True, "1 1 * * *", "1"), ("locked", ""))
        self.assertEqual(hs.control_backup_settings(True, "1 1 * * *", "1")[0], "error")

    def test_restore_results_and_request_file(self):
        answers = iter([(200, {"ok": True, "id": "1791209576.5"}), (200, {"ok": False, "error": "bad", "field": "invalid"}),
                        (200, {"ok": False, "error": "busy", "field": "busy"}), (200, {"ok": False, "error": "x"})])
        self.serve({"restore": lambda: next(answers)})
        self.assertEqual(hs.control_restore("headscale-easy-x.tar.gz"), ("started", "1791209576.5"))
        path = os.path.join(os.path.dirname(self.sock), "restore-ui.json")
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"name": "headscale-easy-x.tar.gz"})
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(hs.control_restore("a"), ("invalid", "bad"))
        self.assertEqual(hs.control_restore("a"), ("busy", "busy"))
        self.assertEqual(hs.control_restore("a")[0], "error")

    def test_backup_upload_results_and_request_file(self):
        answers = iter([(200, {"ok": True, "name": "headscale-easy-x.tar.gz"}),
                        (200, {"ok": False, "error": "bad", "field": "invalid"}), (200, {"ok": False, "error": "x"})])
        self.serve({"backup_upload": lambda: next(answers)})
        self.assertEqual(hs.control_backup_upload(".upload-0123456789abcdef.part", "mine.tar.gz"),
                         ("saved", "headscale-easy-x.tar.gz"))
        path = os.path.join(os.path.dirname(self.sock), "backup-upload.json")
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"tmp": ".upload-0123456789abcdef.part", "name": "mine.tar.gz"})
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(hs.control_backup_upload("t", "n"), ("invalid", "bad"))
        self.assertEqual(hs.control_backup_upload("t", "n")[0], "error")

    def test_routes_without_a_backend_are_unavailable(self):
        self.serve({})
        self.assertEqual(hs.control_backup_upload("t", "n")[0], "unavailable")
        self.assertEqual(hs.control_restore("a")[0], "unavailable")
        self.assertEqual(hs.control_backup_settings(True, "0 3 * * *", "14")[0], "unavailable")
        self.assertEqual(hs.control_backup(), "unavailable")

    def test_without_the_socket_everything_is_unavailable(self):
        self.assertEqual(hs.control_backup_upload("t", "n")[0], "unavailable")
        self.assertEqual(hs.control_restore("a")[0], "unavailable")
        self.assertEqual(hs.control_backup_settings(True, "0 3 * * *", "14")[0], "unavailable")
        self.assertEqual(hs.control_backup(), "unavailable")

    def test_backup_dead_socket_is_an_error(self):
        with open(self.sock, "w"):
            pass
        self.assertEqual(hs.control_backup(), "error")

    def test_configtest_and_restart(self):
        self.serve({"configtest": lambda: (200, {"ok": True, "output": "Config OK"}),
                    "restart": lambda: (200, {"ok": True}),
                    "status": lambda: (200, {"ok": True, "api": 1, "headscale": {"version": "v0.26.1"}})})
        self.assertTrue(hs.control_available())
        self.assertEqual(hs.headscale_configtest(), (True, "Config OK"))
        self.assertTrue(hs.restart_headscale())
        self.assertEqual(hs.control_status()["headscale"]["version"], "v0.26.1")

    def test_configtest_failure(self):
        self.serve({"configtest": lambda: (200, {"ok": False, "output": "FTL bad"})})
        self.assertEqual(hs.headscale_configtest(), (False, "FTL bad"))

    def test_a_failing_supervisor(self):
        self.serve({"configtest": lambda: (503, {"ok": False, "error": "Headscale is not running"}),
                    "restart": lambda: (503, {"ok": False, "error": "Headscale is not running"}),
                    "status": lambda: (503, {"ok": False})})
        self.assertFalse(hs.control_available())
        ok, out = hs.headscale_configtest()
        self.assertFalse(ok)
        self.assertIn("503", out)
        self.assertFalse(hs.restart_headscale())

    def test_socket_present_but_dead(self):
        with open(self.sock, "w"):
            pass
        self.assertFalse(hs.control_available())
        self.assertIsNone(hs.control_status())
        self.assertFalse(hs.headscale_configtest()[0])
        self.assertFalse(hs.restart_headscale())

    def test_nothing_available_fails_closed(self):
        self.assertFalse(hs.control_available())
        self.assertIsNone(hs.control_status())
        self.assertEqual(hs.headscale_configtest(), (False, "the supervisor is not running"))
        self.assertFalse(hs.restart_headscale())


if __name__ == "__main__":
    unittest.main()
