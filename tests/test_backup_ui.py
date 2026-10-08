"""Backups in the console: the list, Download (POST, attachment) and Restore (typed confirmation).

The supervisor and its control socket are fakes here (tests/test_supervisor.py covers the real be_restore); what is
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
from handlers import shared as sh  # noqa: E402
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
    URL = f"{B}/backups/download"

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
    URL = f"{B}/backups/restore"

    def post(self, session=ADMIN, result=("started", "1791209576.5"), **form):
        form = dict({"csrf": "tok", "name": NAME, "confirm": "RESTORE"}, **form)
        with mock.patch.object(hs, "control_restore", return_value=result) as call:
            status, headers, body = request("POST", self.URL, session, form)
        return status, headers, body, call

    def test_admin_with_the_confirmation_starts_it_and_gets_the_waiting_page(self):
        status, _h, body, call = self.post()
        self.assertEqual(status, 200)
        call.assert_called_once_with(NAME)
        self.assertIn('data-await-restore="/console/backups?m=backup-restore-done"', body)
        self.assertIn('data-probe="/console/restore-status?id=1791209576.5"', body)
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
        from backup_pages import restoring_page
        html = restoring_page('1"><script>alert(1)</script>')
        self.assertNotIn("<script>alert", html)
        self.assertIn('data-probe="/console/restore-status?id="', html)

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
            with mock.patch.object(hs, "CONTROL_SOCKET", os.path.join(tmp, "control.sock")):
                self.assertIsNone(hs.restore_result())
                with open(os.path.join(tmp, "restore-result.json"), "w") as fh:
                    fh.write('{"ok": true, "requested": 1.5}')
                self.assertEqual(hs.restore_result(), {"ok": True, "requested": 1.5})
                with open(os.path.join(tmp, "restore-result.json"), "w") as fh:
                    fh.write("not json")
                self.assertIsNone(hs.restore_result())


def _mp(*parts, boundary="----hseTestBoundary"):
    """A multipart body: parts are (name, value) or (name, value, filename)."""
    out = b""
    for part in parts:
        name, value = part[0], part[1]
        filename = part[2] if len(part) > 2 else None
        head = f'Content-Disposition: form-data; name="{name}"' + (f'; filename="{filename}"' if filename is not None else "")
        out += f"--{boundary}\r\n{head}\r\n\r\n".encode() + (value if isinstance(value, bytes) else value.encode()) + b"\r\n"
    return out + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


class Upload(WithBackupDir):
    URL = f"{B}/backups/upload"

    def setUp(self):
        super().setUp()
        self.seen = []

        def saved(tmp, name):  # what the supervisor would be asked: the file is on disk by now
            with open(os.path.join(self.dir, tmp), "rb") as fh:
                self.seen.append((tmp, name, fh.read()))
            return ("saved", "headscale-easy-uploaded-20261005-120000.tar.gz")
        self.saved = saved

    def post(self, parts, session=ADMIN, upload=None, restore=None, **kw):
        raw, ctype = _mp(*parts)
        upload = upload or mock.Mock(side_effect=self.saved)
        restore = restore or mock.Mock(return_value=("started", "1791209576.5"))
        with mock.patch.object(hs, "control_backup_upload", upload), mock.patch.object(hs, "control_restore", restore):
            status, headers, body = request("POST", self.URL, session, headers={"Content-Type": ctype}, raw=raw)
        return status, headers, body, upload, restore

    def left(self):
        return sorted(n for n in os.listdir(self.dir) if n.startswith(".upload-"))

    def test_upload_keeps_the_file_in_the_list_and_audits(self):
        status, headers, _b, upload, restore = self.post([("csrf", "tok"), ("action", "upload"),
                                                          ("file", b"\x1f\x8b-archive", "my backup.tar.gz")])
        self.assertEqual((status, location(headers)), (303, f"{B}/backups?m=backup-uploaded"))
        tmp, name, content = self.seen[0]
        self.assertRegex(tmp, r"^\.upload-[0-9a-f]{24}\.part$")
        self.assertEqual((name, content), ("my backup.tar.gz", b"\x1f\x8b-archive"))
        restore.assert_not_called()
        event = app.audit.request_event.call_args.args
        self.assertEqual((event[2], event[3]), ("backup.upload", "headscale-easy-uploaded-20261005-120000.tar.gz"))

    def test_upload_and_restore_goes_on_to_the_waiting_page(self):
        status, _h, body, _u, restore = self.post([("csrf", "tok"), ("action", "restore"), ("confirm", "RESTORE"),
                                                   ("file", b"data", "a.tar.gz")])
        self.assertEqual(status, 200)
        restore.assert_called_once_with("headscale-easy-uploaded-20261005-120000.tar.gz")
        self.assertIn('data-await-restore="/console/backups?m=backup-restore-done"', body)
        self.assertEqual([c.args[2] for c in app.audit.request_event.call_args_list], ["backup.upload", "backup.restore"])

    def test_restore_needs_the_typed_confirmation_and_the_file_is_not_kept(self):
        for confirm in ("", "restore", "yes"):
            status, headers, _b, upload, restore = self.post([("csrf", "tok"), ("action", "restore"),
                                                              ("confirm", confirm), ("file", b"data", "a.tar.gz")])
            self.assertEqual((status, location(headers)), (303, f"{B}/backups?m=backup-restore-confirm"))
            upload.assert_not_called()
            restore.assert_not_called()
        self.assertEqual(self.left(), [])

    def test_a_restore_that_cannot_start_leaves_the_file_in_the_list(self):
        restore = mock.Mock(return_value=("busy", "x"))
        status, headers, _b, _u, _r = self.post([("csrf", "tok"), ("action", "restore"), ("confirm", "RESTORE"),
                                                 ("file", b"data", "a.tar.gz")], restore=restore)
        self.assertEqual((status, location(headers)), (303, f"{B}/backups?m=backup-restore-busy"))

    def test_no_token_wrong_token_or_token_after_the_file_writes_nothing(self):
        for parts in ([("file", b"data", "a.tar.gz")],
                      [("csrf", "bad"), ("file", b"data", "a.tar.gz")],
                      [("csrf", ""), ("file", b"data", "a.tar.gz")],
                      [("file", b"data", "a.tar.gz"), ("csrf", "tok")]):
            status, _h, _b, upload, restore = self.post(parts)
            self.assertEqual(status, 403, parts[0][0])
            upload.assert_not_called()
            restore.assert_not_called()
            self.assertEqual(self.left(), [])
        app.audit.request_event.assert_not_called()

    def test_only_admins(self):
        status, _h, _b, upload, _r = self.post([("csrf", "tok"), ("file", b"data", "a.tar.gz")], session=MEMBER)
        self.assertEqual(status, 403)
        upload.assert_not_called()
        self.assertEqual(self.left(), [])
        raw, ctype = _mp(("csrf", "tok"), ("file", b"x", "a"))
        status, headers, _b = request("POST", self.URL, None, headers={"Content-Type": ctype}, raw=raw)
        self.assertEqual((status, location(headers)), (303, f"{B}/login"))

    def test_a_refused_file_is_deleted(self):
        upload = mock.Mock(return_value=("invalid", "not a backup"))
        status, headers, _b, _u, _r = self.post([("csrf", "tok"), ("file", b"junk", "a.tar.gz")], upload=upload)
        self.assertEqual((status, location(headers)), (303, f"{B}/backups?m=backup-upload-invalid"))
        self.assertEqual(self.left(), [])
        app.audit.request_event.assert_not_called()

    def test_helper_unavailable_or_failing_leaves_nothing_behind(self):
        for result in ("unavailable", "error"):
            upload = mock.Mock(return_value=(result, ""))
            status, headers, _b, _u, _r = self.post([("csrf", "tok"), ("file", b"junk", "a.tar.gz")], upload=upload)
            self.assertEqual((status, location(headers)), (303, f"{B}/backups?m=backup-upload-{result}"))
            self.assertEqual(self.left(), [])

    def test_empty_and_missing_files(self):
        for parts in ([("csrf", "tok"), ("file", b"", "a.tar.gz")], [("csrf", "tok"), ("action", "upload")]):
            status, headers, _b, upload, _r = self.post(parts)
            self.assertEqual((status, location(headers)), (303, f"{B}/backups?m=backup-upload-none"))
            upload.assert_not_called()
        self.assertEqual(self.left(), [])

    def test_too_large_is_refused_and_leaves_nothing(self):
        with mock.patch.object(sh, "BACKUP_UPLOAD_MAX", 1000):
            status, headers, _b, upload, _r = self.post([("csrf", "tok"), ("file", b"x" * 5000, "a.tar.gz")])
            self.assertEqual((status, location(headers)), (303, f"{B}/backups?m=backup-upload-toolarge"))
            self.assertEqual(self.left(), [])
            upload.assert_not_called()
        with mock.patch.object(sh, "BACKUP_UPLOAD_MAX", 10):  # refused from Content-Length, before reading
            raw, ctype = _mp(("csrf", "tok"), ("file", b"x" * (2 << 20), "a.tar.gz"))
            status, headers, _b = request("POST", self.URL, ADMIN, headers={"Content-Type": ctype}, raw=raw)
            self.assertEqual(location(headers), f"{B}/backups?m=backup-upload-toolarge")

    def test_a_broken_form_is_an_error_and_leaves_nothing(self):
        raw, _ctype = _mp(("csrf", "tok"), ("file", b"x" * 100, "a.tar.gz"))
        for ctype, body in (("multipart/form-data; boundary=zzz", raw), ("application/x-www-form-urlencoded", raw),
                            ("multipart/form-data; boundary=----hseTestBoundary", raw[:-30])):
            with mock.patch.object(hs, "control_backup_upload") as upload:
                status, headers, _b = request("POST", self.URL, ADMIN, headers={"Content-Type": ctype}, raw=body)
            self.assertEqual((status, location(headers)), (303, f"{B}/backups?m=backup-upload-error"), ctype)
            upload.assert_not_called()
            self.assertEqual(self.left(), [])

    def test_old_work_files_are_cleaned_and_recent_ones_kept(self):
        old = os.path.join(self.dir, ".upload-" + "a" * 24 + ".part")
        new = os.path.join(self.dir, ".upload-" + "b" * 24 + ".part")
        other = os.path.join(self.dir, ".upload-not-ours.part")
        for path in (old, new, other):
            with open(path, "wb") as fh:
                fh.write(b"x")
        os.utime(old, (1, 1))
        self.post([("csrf", "tok"), ("file", b"data", "a.tar.gz")])
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new) and os.path.exists(other))

    def test_the_original_name_is_sanitized_before_it_travels(self):
        for sent, expect in (("../../etc/passwd", "passwd"), ("C:\\evil\\x.tar.gz", "x.tar.gz"),
                             ("na\u00efve\x01.tar.gz", "na?ve?.tar.gz"), ("a" * 300 + ".tar.gz", ("a" * 120))):
            self.seen.clear()
            self.post([("csrf", "tok"), ("file", b"data", sent)])
            self.assertEqual(self.seen[0][1], expect, sent)

    def test_not_available_without_a_backup_dir(self):
        with mock.patch.dict(os.environ, {"BACKUP_DIR": ""}):
            status, _h, _b, upload, _r = self.post([("csrf", "tok"), ("file", b"data", "a.tar.gz")])
        self.assertEqual(status, 404)
        upload.assert_not_called()


class Page(WithBackupDir):
    def get(self, session=ADMIN, backup=True, path="/backups"):
        control = {"backup": {"enabled": True, "schedule": "0 3 * * *", "keep_days": 14, "running": False, "files": [],
                             "last": None, "last_ok": None, "count": 0, "bytes": 0}} if backup else {}
        with mock.patch.object(hs, "control_status", return_value=control):
            return request("GET", f"{B}{path}", session)

    def test_menu_entry_for_admins_when_the_image_has_backups(self):
        _s, _h, body = self.get()
        self.assertIn(f'<a class="nav-top active" href="{B}/backups">', body)
        with mock.patch.dict(os.environ, {"BACKUP_DIR": ""}):  # not configured: no entry
            self.assertNotIn(f'href="{B}/backups"', self.get()[2])
        self.assertNotIn(f'href="{B}/backups"', self.get(MEMBER)[2])

    def test_page_is_for_admins(self):
        status, _h, body = self.get()
        self.assertEqual(status, 200)
        self.assertIn("Upload a backup", body)
        self.assertIn('enctype="multipart/form-data"', body)
        self.assertEqual(self.get(MEMBER)[0], 403)

    def test_status_page_no_longer_carries_the_backup_cards(self):
        with mock.patch.object(hs, "control_status", return_value={"backup": {"enabled": True, "files": []}}):
            _s, _h, body = request("GET", f"{B}/settings/status", ADMIN)
        self.assertNotIn("Back up now", body)
        self.assertNotIn("Available backups", body)


if __name__ == "__main__":
    unittest.main()
