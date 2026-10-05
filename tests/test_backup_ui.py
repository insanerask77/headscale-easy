"""Backups in the console: the list, Download (POST, attachment) and Restore (typed confirmation).

The supervisor and the helper are fakes here (tests/test_supervisor.py covers the real be_restore); what is
checked is the web side: who may do it, CSRF, the typed confirmation, names that try to leave the backups
directory, links, the headers of the download and the page that waits for the restart.

    python3 -m unittest tests.test_backup_ui
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "web"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_security import ADMIN, MEMBER, B, Base, location, request  # noqa: E402  (sets the environment app needs)
import app  # noqa: E402
import headscale as hs  # noqa: E402
import status as server_status  # noqa: E402

NAME = "headscale-easy-20261005-030000.tar.gz"


class WithBackupDir(Base):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = os.path.join(self.tmp.name, "backups")
        os.makedirs(self.dir)
        with open(os.path.join(self.dir, NAME), "wb") as fh:
            fh.write(b"fake-archive-bytes")
        p = mock.patch.dict(os.environ, {"BACKUP_DIR": self.dir})
        p.start()
        self.addCleanup(p.stop)


class Download(WithBackupDir):
    URL = f"{B}/settings/status/backup/download"

    def get(self, session=ADMIN, **form):
        return request("POST", self.URL, session, dict({"csrf": "tok", "name": NAME}, **form))

    def test_admin_gets_the_archive_as_an_attachment(self):
        status, headers, body = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(body, "fake-archive-bytes")
        low = {k.lower(): (v[0] if isinstance(v, list) else v) for k, v in headers.items()}
        self.assertEqual(low["content-type"], "application/gzip")
        self.assertEqual(low["content-length"], str(len(b"fake-archive-bytes")))
        self.assertEqual(low["content-disposition"], f'attachment; filename="{NAME}"')
        self.assertEqual(low["cache-control"], "no-store")
        self.assertEqual(low["x-content-type-options"], "nosniff")
        event = app.audit.request_event.call_args.args
        self.assertEqual((event[2], event[3]), ("backup.download", NAME))

    def test_only_admins_and_only_with_the_token(self):
        for session, csrf in ((MEMBER, "tok"), (ADMIN, "bad"), (ADMIN, "")):
            status, _h, body = self.get(session, csrf=csrf)
            self.assertEqual(status, 403)
            self.assertNotIn("fake-archive", body)
        app.audit.request_event.assert_not_called()

    def test_a_get_is_not_a_download(self):
        status, _h, body = request("GET", f"{self.URL}?name={NAME}", ADMIN)
        self.assertNotEqual(status, 200)
        self.assertNotIn("fake-archive", body)

    def test_names_that_leave_the_directory_or_are_not_ours(self):
        secret = os.path.join(self.tmp.name, "headscale-easy-secret.tar.gz")
        with open(secret, "wb") as fh:
            fh.write(b"outside")
        os.symlink(secret, os.path.join(self.dir, "headscale-easy-link.tar.gz"))
        with open(os.path.join(self.dir, "notes.txt"), "wb") as fh:
            fh.write(b"not an archive")
        os.mkdir(os.path.join(self.dir, "headscale-easy-dir.tar.gz"))
        bad = ("../headscale-easy-secret.tar.gz", "/etc/passwd", "..%2f..%2fetc%2fpasswd", "headscale-easy-link.tar.gz",
               "headscale-easy-dir.tar.gz", "notes.txt", ".headscale-easy-x.tar.gz", "", "headscale-easy-.tar.gz",
               "headscale-easy-missing.tar.gz", NAME + "/", "headscale-easy-a/../" + NAME, NAME + "\n", NAME + "\x00")
        for name in bad:
            status, _h, body = self.get(name=name)
            self.assertEqual(status, 404, repr(name))
            self.assertNotIn("outside", body)
        app.audit.request_event.assert_not_called()

    def test_not_available_without_a_backup_dir(self):
        with mock.patch.dict(os.environ, {"BACKUP_DIR": ""}):
            self.assertEqual(self.get()[0], 404)

    def test_open_backup_helper(self):
        fh, size = server_status.open_backup(NAME)
        with fh:
            self.assertEqual((fh.read(), size), (b"fake-archive-bytes", 18))
        self.assertIsNone(server_status.open_backup(None))
        self.assertIsNone(server_status.open_backup(5))


class Restore(WithBackupDir):
    URL = f"{B}/settings/status/backup/restore"

    def post(self, session=ADMIN, result=("started", "1791209576.5"), **form):
        form = dict({"csrf": "tok", "name": NAME, "confirm": "RESTORE"}, **form)
        with mock.patch.object(hs, "helper_restore", return_value=result) as call:
            status, headers, body = request("POST", self.URL, session, form)
        return status, headers, body, call

    def test_admin_with_the_confirmation_starts_it_and_gets_the_waiting_page(self):
        status, _h, body, call = self.post()
        self.assertEqual(status, 200)
        call.assert_called_once_with(NAME)
        self.assertIn('data-await-restore="/admin/settings/status?m=backup-restore-done"', body)
        self.assertIn('data-probe="/admin/restore-status?id=1791209576.5"', body)
        self.assertNotIn("<script>", body)
        event = app.audit.request_event.call_args.args
        self.assertEqual((event[2], event[3]), ("backup.restore", NAME))

    def test_the_confirmation_must_be_typed_exactly(self):
        for confirm in ("", "restore", "RESTORE ", "yes", "Restore"):
            status, headers, _b, call = self.post(confirm=confirm)
            self.assertEqual(status, 303, confirm)
            self.assertIn("m=backup-restore-confirm", location(headers))
            call.assert_not_called()

    def test_only_admins_and_only_with_the_token(self):
        for session, csrf in ((MEMBER, "tok"), (ADMIN, "bad"), (ADMIN, "")):
            status, _h, _b, call = self.post(session, csrf=csrf)
            self.assertEqual(status, 403)
            call.assert_not_called()

    def test_refusals_redirect_with_their_message_and_are_not_audited(self):
        for result in ("invalid", "busy", "unavailable", "error"):
            status, headers, _b, _c = self.post(result=(result, "x"))
            self.assertEqual(status, 303)
            self.assertIn(f"m=backup-restore-{result}", location(headers))
        app.audit.request_event.assert_not_called()

    def test_waiting_page_ignores_a_strange_id(self):
        from status_pages import restoring_page
        html = restoring_page('1"><script>alert(1)</script>')
        self.assertNotIn("<script>alert", html)
        self.assertIn('data-probe="/admin/restore-status?id="', html)

    def test_restore_status_says_done_only_for_its_own_id(self):
        def ask(result, rid):
            with mock.patch.object(hs, "restore_result", return_value=result):
                status, _h, body = request("GET", f"{B}/restore-status?id={rid}")
            return status, body
        done = {"ok": True, "requested": 1791209576.5}
        self.assertEqual(ask(done, "1791209576.5"), (200, '{"done": true, "ok": true}'))
        self.assertEqual(ask(dict(done, ok=False, error="secret detail"), "1791209576.5"),
                         (200, '{"done": true, "ok": false}'))  # no error text without a session
        for rid in ("1791209576.4", "", "abc", "nan"):
            self.assertEqual(ask(done, rid), (200, '{"done": false, "ok": false}'), rid)
        self.assertEqual(ask(None, "1791209576.5"), (200, '{"done": false, "ok": false}'))


class ClientAndFlash(unittest.TestCase):
    def test_restore_result_reads_the_file_next_to_the_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(hs, "HELPER_SOCKET", os.path.join(tmp, "helper.sock")):
                self.assertIsNone(hs.restore_result())
                with open(os.path.join(tmp, "restore-result.json"), "w") as fh:
                    fh.write('{"ok": true, "requested": 1.5}')
                self.assertEqual(hs.restore_result(), {"ok": True, "requested": 1.5})
                with open(os.path.join(tmp, "restore-result.json"), "w") as fh:
                    fh.write("not json")
                self.assertIsNone(hs.restore_result())


if __name__ == "__main__":
    unittest.main()
