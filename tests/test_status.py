"""Server status page tests: Prometheus parsing, version comparison, the
release cache, and the page through the real request handler (with the helper
down, up, and for each role). Standard library only; no Headscale:

    python3 tests/test_status.py
"""
import contextlib
import email.message
import io
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
os.environ.update(HEADSCALE_API_KEY="x", PUBLIC_URL="https://vpn.example.com", SESSION_SECRET="s",
                  HEADSCALE_URL="http://headscale:8080", HEADSCALE_CONFIG=os.path.join(tempfile.mkdtemp(), "c.yaml"))
sys.path.insert(0, WEB)

import app  # noqa: E402
import headscale as hs  # noqa: E402
import sessions  # noqa: E402
import status  # noqa: E402

sessions.configure(":memory:")

B = app.BASE
ADMIN = {"kind": "oidc", "sub": "a", "username": "root", "name": "Root", "email": "", "groups": [],
         "admin": True, "csrf": "tok", "exp": time.time() + 3600}
MEMBER = dict(ADMIN, sub="b", username="bob", name="Bob", admin=False)
AUDITOR = dict(ADMIN, sub="c", username="eve", admin=False, role="auditor")

METRICS = """# HELP go_goroutines Number of goroutines.
# TYPE go_goroutines gauge
go_goroutines 42
process_resident_memory_bytes 1.048576e+08
headscale_nodestore_nodes 7
headscale_http_requests_total{code="200",path="/a"} 10
headscale_http_requests_total{code="404",path="/b"} 5
broken line here
bad_value NaN
"""

HELPER = {"api": 1, "docker": True, "headscale": {"container": "headscale", "version": "v0.26.1"},
          "containers": [
              {"name": "headscale", "service": "headscale", "image": "headscale/headscale:latest",
               "state": "running", "health": "healthy", "status": "Up 2 hours (healthy)"},
              {"name": "caddy", "service": "caddy", "image": "caddy:2", "state": "exited", "health": None,
               "status": "Exited (1)"}]}


def request(method, path, session=None):
    msg = email.message.Message()
    if session is not None and "sid" not in session:
        session = dict(session, sid=sessions.create(session))  # a live server-side session
    if session is not None:
        msg["Cookie"] = f"hse_session={app.sign(session)}"
    msg["Content-Length"] = "0"
    h = app.Handler.__new__(app.Handler)
    h.rfile, h.wfile = io.BytesIO(b""), io.BytesIO()
    h.headers, h.command, h.path = msg, method, path
    h.request_version, h.requestline, h.client_address = "HTTP/1.1", f"{method} {path} HTTP/1.1", ("127.0.0.1", 1)
    h.close_connection = True
    getattr(h, f"do_{method}")()
    head, _sep, rest = h.wfile.getvalue().partition(b"\r\n\r\n")
    return int(head.decode().split("\r\n")[0].split()[1]), rest.decode(errors="replace")


def post(path, session, form=None):
    """A POST through the real handler: (status, response head, body)."""
    body = "&".join(f"{k}={v}" for k, v in (form or {}).items()).encode()
    msg = email.message.Message()
    if session is not None:
        msg["Cookie"] = f"hse_session={app.sign(dict(session, sid=sessions.create(session)))}"
    msg["Content-Length"] = str(len(body))
    h = app.Handler.__new__(app.Handler)
    h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
    h.headers, h.command, h.path = msg, "POST", path
    h.request_version, h.requestline, h.client_address = "HTTP/1.1", f"POST {path} HTTP/1.1", ("127.0.0.1", 1)
    h.close_connection = True
    h.do_POST()
    head, _sep, rest = h.wfile.getvalue().partition(b"\r\n\r\n")
    return int(head.decode().split("\r\n")[0].split()[1]), head.decode(), rest.decode(errors="replace")


class Parsing(unittest.TestCase):
    def test_metrics_sum_labels_and_skip_garbage(self):
        m = status.parse_metrics(METRICS)
        self.assertEqual(m["go_goroutines"], 42)
        self.assertEqual(m["headscale_http_requests_total"], 15)
        self.assertNotIn("bad_value", m)
        s = status.summarize_metrics(m)
        self.assertEqual((s["nodes"], s["requests"], s["goroutines"]), (7, 15, 42))
        self.assertEqual(s["memory"], 104857600)

    def test_summary_without_known_metrics(self):
        s = status.summarize_metrics(status.parse_metrics("foo 1\n"))
        self.assertTrue(all(v is None for v in s.values()))

    def test_versions(self):
        self.assertEqual(status.parse_version("v0.26.1"), (0, 26, 1))
        self.assertEqual(status.parse_version("headscale 0.27.0-beta.1"), (0, 27, 0))
        self.assertIsNone(status.parse_version("dev"))
        self.assertTrue(status.is_newer("v0.27.0", "0.26.1"))
        self.assertTrue(status.is_newer("v1.10.0", "1.9.0"))
        self.assertFalse(status.is_newer("v1.4.0", "1.4.0"))
        self.assertFalse(status.is_newer("v1.3.0", "1.4.0"))
        self.assertFalse(status.is_newer(None, "1.4.0"))
        self.assertFalse(status.is_newer("v1.5.0", "dev"))


class Releases(unittest.TestCase):
    def setUp(self):
        status._cache.clear()

    def test_cached_for_12_hours_and_failures_retried_sooner(self):
        with mock.patch.object(hs, "http_json", return_value={"tag_name": "v9.9.9"}) as get:
            self.assertEqual(status.latest_release("easy", now=1000), "v9.9.9")
            self.assertEqual(status.latest_release("easy", now=1000 + 3600), "v9.9.9")
            self.assertEqual(get.call_count, 1)
            status.latest_release("easy", now=1000 + 12 * 3600 + 1)
            self.assertEqual(get.call_count, 2)
        status._cache.clear()
        with mock.patch.object(hs, "http_json", side_effect=OSError("offline")) as get:
            self.assertIsNone(status.latest_release("easy", now=1000))
            self.assertIsNone(status.latest_release("easy", now=1100))
            self.assertEqual(get.call_count, 1)
            status.latest_release("easy", now=1000 + status.FAIL_TTL + 1)
            self.assertEqual(get.call_count, 2)

    def test_disabled(self):
        with mock.patch.object(status, "UPDATE_CHECK", False), mock.patch.object(hs, "http_json") as get:
            self.assertIsNone(status.latest_release("easy"))
            get.assert_not_called()


@contextlib.contextmanager
def sources(helper, metrics=None, latest="v1.4.0"):
    with mock.patch.object(hs, "helper_status", return_value=helper), \
            mock.patch.object(status, "fetch_metrics", return_value=metrics), \
            mock.patch.object(status, "latest_release", return_value=latest), \
            mock.patch.object(status, "online_nodes", return_value=(2, 3)):
        yield


class Page(unittest.TestCase):
    def get(self, session, **kw):
        with sources(**kw):
            return request("GET", f"{B}/settings/status", session)

    def test_helper_down_still_renders(self):
        code, html = self.get(ADMIN, helper=None)
        self.assertEqual(code, 200)
        self.assertIn("not answering", html)
        self.assertIn("not reachable", html)
        self.assertIn("2 of 3", html)

    def test_full_page(self):
        metrics = status.summarize_metrics(status.parse_metrics(METRICS))
        code, html = self.get(ADMIN, helper=HELPER, metrics=metrics, latest="v99.0.0")
        self.assertEqual(code, 200)
        self.assertIn("v0.26.1", html)
        self.assertIn("Update available: v99.0.0", html)
        self.assertIn("healthy", html)
        self.assertIn("exited", html)
        self.assertIn("Requests served", html)

    def test_up_to_date(self):
        _c, html = self.get(ADMIN, helper=HELPER, latest="v0.26.1")
        self.assertIn("Up to date", html)

    def test_auditor_sees_it_member_does_not(self):
        self.assertEqual(self.get(AUDITOR, helper=None)[0], 200)
        self.assertEqual(self.get(MEMBER, helper=None)[0], 403)

    def test_requires_sign_in(self):
        self.assertNotEqual(self.get(None, helper=None)[0], 200)

    def test_nav_link_only_for_admins(self):
        self.assertIn("settings/status", self.get(ADMIN, helper=None)[1])
        with sources(None):
            _c, html = request("GET", f"{B}/settings/general", MEMBER)
        self.assertNotIn("settings/status", html)


BACKUP_OK = {"enabled": True, "schedule": "0 3 * * *", "next_run": "2026-10-06T03:00:00Z", "keep_days": 14,
             "running": False, "count": 3, "bytes": 5242880,
             "last": {"at": "2026-10-05T03:00:04Z", "ok": True, "file": "headscale-easy-20261005-030000.tar.gz",
                      "size": 1048576, "duration": 4.2, "trigger": "scheduled", "error": None}}
BACKUP_OK["last_ok"] = BACKUP_OK["last"]
BACKUP_FAILED = dict(BACKUP_OK, last={"at": "2026-10-05T03:00:04Z", "ok": False, "file": None, "size": 0,
                                      "duration": 1.0, "trigger": "scheduled", "error": "disk <full>"},
                     last_ok={"at": "2026-10-04T03:00:04Z", "ok": True, "file": "x.tar.gz", "size": 1})


class BackupCard(unittest.TestCase):
    def page(self, backup, session=ADMIN):
        return Page.get(self, session, helper=dict(HELPER, **({"backup": backup} if backup is not None else {})))

    def test_hidden_without_the_backup_key(self):
        _c, html = self.page(None)
        self.assertNotIn("Back up now", html)
        self.assertNotIn('data-live="backup"', html)

    def test_ok_state(self):
        code, html = self.page(BACKUP_OK)
        self.assertEqual(code, 200)
        self.assertIn('data-live="backup"', html)
        self.assertIn("headscale-easy-20261005-030000.tar.gz", html)
        self.assertIn("1.0 MB", html)
        self.assertIn("0 3 * * *", html)
        self.assertIn("2026-10-06 03:00 UTC", html)  # next run
        self.assertIn("14 days", html)
        self.assertIn("3 backups", html)
        self.assertIn("/data/backups", html)
        self.assertIn(f'action="{B}/settings/status/backup"', html)

    def test_failed_state_shows_the_error_escaped_and_the_last_good_one(self):
        _c, html = self.page(BACKUP_FAILED)
        self.assertIn("Failed", html)
        self.assertIn("disk &lt;full&gt;", html)
        self.assertNotIn("disk <full>", html)
        self.assertIn("Last good backup", html)

    def test_never_and_running_and_off(self):
        _c, html = self.page(dict(BACKUP_OK, last=None, last_ok=None))
        self.assertIn("Never", html)
        _c, html = self.page(dict(BACKUP_OK, running=True))
        self.assertIn("Running", html)
        self.assertIn("disabled", html)  # no second run from the button
        _c, html = self.page(dict(BACKUP_OK, enabled=False, schedule="off", next_run=None))
        self.assertIn("BACKUP_SCHEDULE=off", html)
        self.assertIn("Back up now", html)  # manual backups still work

    def test_auditor_sees_the_card_without_the_button(self):
        _c, html = self.page(BACKUP_OK, AUDITOR)
        self.assertIn("headscale-easy-20261005-030000.tar.gz", html)
        self.assertNotIn("Back up now", html)

    def test_flash_messages(self):
        for code, text in (("backup-started", "Backup started"), ("backup-busy", "already running"),
                           ("backup-unavailable", "not available"), ("backup-error", "Could not start")):
            with sources(dict(HELPER, backup=BACKUP_OK)):
                _c, html = request("GET", f"{B}/settings/status?m={code}", ADMIN)
            self.assertIn(text, html, code)


class BackupNow(unittest.TestCase):
    URL = f"{B}/settings/status/backup"

    def run_post(self, session, result="started", csrf="tok"):
        with mock.patch.object(hs, "helper_backup", return_value=result) as call, \
                mock.patch.object(app.audit, "request_event") as event:
            code, head, _body = post(self.URL, session, {"csrf": csrf})
        return code, head, call, event

    def test_admin_starts_a_backup_and_it_is_audited(self):
        code, head, call, event = self.run_post(ADMIN)
        self.assertEqual(code, 303)
        self.assertIn("settings/status?m=backup-started", head)
        call.assert_called_once_with()
        self.assertEqual(event.call_args.args[2], "backup.run")

    def test_busy_unavailable_and_error_redirect_with_their_message(self):
        for result in ("busy", "unavailable", "error"):
            code, head, _call, _event = self.run_post(ADMIN, result)
            self.assertEqual(code, 303)
            self.assertIn(f"m=backup-{result}", head)

    def test_only_started_and_busy_are_audited(self):
        for result, audited in (("started", 1), ("busy", 1), ("unavailable", 0), ("error", 0)):
            _c, _h, _call, event = self.run_post(ADMIN, result)
            self.assertEqual(event.call_count, audited, result)

    def test_other_roles_are_refused_and_nothing_runs(self):
        network_admin = dict(MEMBER, role="network_admin")
        for who in (MEMBER, AUDITOR, network_admin):
            code, _head, call, event = self.run_post(who)
            self.assertEqual(code, 403)
            call.assert_not_called()
            event.assert_not_called()

    def test_csrf_is_required(self):
        for token in ("", "wrong"):
            code, _head, call, _event = self.run_post(ADMIN, csrf=token)
            self.assertEqual(code, 403)
            call.assert_not_called()

    def test_requires_sign_in_and_get_is_not_the_action(self):
        with mock.patch.object(hs, "helper_backup") as call:
            code, head, _ = post(self.URL, None, {"csrf": "tok"})
            self.assertEqual(code, 303)
            self.assertIn("login", head)
            self.assertNotEqual(request("GET", self.URL, ADMIN)[0], 200)
        call.assert_not_called()


class Disk(unittest.TestCase):
    def test_disks_from_env(self):
        self.assertEqual(status._disks_from_env("data:/data, extra:/mnt/x"), (("data", "/data"), ("extra", "/mnt/x")))
        self.assertEqual(status._disks_from_env(None), (("data", "/data"), ("headscale", "/headscale")))
        self.assertEqual(status._disks_from_env("junk,:/x,y:"), (("data", "/data"), ("headscale", "/headscale")))

    def test_disk_usage(self):
        d = status.disk_usage(tempfile.gettempdir())
        self.assertTrue(0 <= d["percent"] <= 100)
        self.assertIsNone(status.disk_usage("/nonexistent-path-xyz"))


if __name__ == "__main__":
    unittest.main()
