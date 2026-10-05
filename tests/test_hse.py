"""aio/hse: the backup, backups and restore subcommands.

The backup and restore engines (aio/backup.py, aio/restore.py) are replaced by
small fakes, so this tests only the CLI: arguments, exit codes, messages, the
confirmation, and the online hand-off to the supervisor (restore.json,
SIGUSR1, restore-result.json) with this test process playing the supervisor.

    python3 -m unittest tests.test_hse
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_hse():
    loader = importlib.machinery.SourceFileLoader("hse_cli", os.path.join(ROOT, "aio", "hse"))
    spec = importlib.util.spec_from_loader("hse_cli", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class BackupBusy(Exception):
    pass


class RestoreError(Exception):
    pass


class Result:
    def __init__(self, ok=True, path="/data/backups/headscale-easy-20261005-030000.tar.gz", size=1048576,
                 duration=1.5, files=9, error=None):
        self.ok, self.path, self.size, self.duration, self.files, self.error = ok, path, size, duration, files, error


META = {"format": 2, "edition": "aio", "created": "2026-10-05T03:00:00Z", "headscale_version": "v0.29.4",
        "db_type": "sqlite", "files": {"config/settings.json": "0" * 64, "headscale/db.sqlite": "1" * 64}}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = os.path.join(self.tmp.name, "data")
        self.run_dir = os.path.join(self.tmp.name, "run")
        os.makedirs(os.path.join(self.data, "backups"))
        os.makedirs(self.run_dir)
        self.hse = load_hse()
        for name, value in (("DATA_DIR", self.data), ("RUN_DIR", self.run_dir), ("RESTORE_WAIT", 1.0)):
            patcher = mock.patch.object(self.hse, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # fake engines
        self.bk = types.SimpleNamespace(BackupBusy=BackupBusy, create=mock.Mock(return_value=Result()))
        self.rs = types.SimpleNamespace(RestoreError=RestoreError, inspect=mock.Mock(return_value=META),
                                        restore=mock.Mock(return_value={"ok": True, "safety_copy": None}))
        self.render = types.SimpleNamespace(
            load_settings=mock.Mock(return_value={"backup_keep_days": "14"}),
            paths=lambda d: {"settings": os.path.join(d, "config", "settings.json")})
        mods = {"backup": self.bk, "restore": self.rs, "render": self.render}
        patcher = mock.patch.object(self.hse, "_load", side_effect=lambda name: mods[name])
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.hse.main(["hse", *args])
        return code, out.getvalue(), err.getvalue()

    def archive(self, name="headscale-easy-20261005-030000.tar.gz", size=10, age=0):
        path = os.path.join(self.data, "backups", name)
        with open(path, "wb") as fh:
            fh.write(b"x" * size)
        if age:
            os.utime(path, (time.time() - age, time.time() - age))
        return path


class Usage(Case):
    def test_unknown_or_missing_command(self):
        for args in ((), ("nope",), ("health", "extra")):
            code, _out, err = self.run_cli(*args)
            self.assertEqual(code, 2, args)
            self.assertIn("hse backup", err)

    def test_health_still_works_without_a_supervisor(self):
        code, _out, err = self.run_cli("health")
        self.assertEqual(code, 1)
        self.assertIn("unhealthy", err)


class BackupCmd(Case):
    def test_runs_a_manual_backup_with_the_settings(self):
        code, out, _err = self.run_cli("backup")
        self.assertEqual(code, 0)
        self.assertIn("headscale-easy-20261005-030000.tar.gz", out)
        self.assertIn("1.0 MB", out)
        self.bk.create.assert_called_once_with(self.data, out_dir=None, settings={"backup_keep_days": "14"},
                                               trigger="manual")
        self.render.load_settings.assert_called_once_with(
            os.environ, os.path.join(self.data, "config", "settings.json"))

    def test_out_dir(self):
        self.assertEqual(self.run_cli("backup", "--out", "/mnt/nas")[0], 0)
        self.assertEqual(self.bk.create.call_args.kwargs["out_dir"], "/mnt/nas")

    def test_bad_arguments(self):
        for args in (("backup", "--out"), ("backup", "x"), ("backup", "--out", "a", "b")):
            self.assertEqual(self.run_cli(*args)[0], 2, args)
        self.bk.create.assert_not_called()

    def test_failure_exits_1_with_the_error(self):
        self.bk.create.return_value = Result(ok=False, path=None, error="disk full")
        code, out, err = self.run_cli("backup")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("disk full", err)

    def test_busy_exits_3(self):
        self.bk.create.side_effect = BackupBusy()
        code, _out, err = self.run_cli("backup")
        self.assertEqual(code, 3)
        self.assertIn("already running", err)

    def test_invalid_settings_and_missing_module(self):
        self.render.load_settings.side_effect = ValueError("bad value")
        self.assertEqual(self.run_cli("backup")[0], 1)
        self.hse._load.side_effect = ImportError("No module named aio.backup")
        code, _out, err = self.run_cli("backup")
        self.assertEqual(code, 1)
        self.assertIn("not available", err)

    def test_unexpected_errors_are_not_swallowed(self):
        self.bk.create.side_effect = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            self.run_cli("backup")


class BackupsCmd(Case):
    def test_empty(self):
        code, out, _err = self.run_cli("backups")
        self.assertEqual(code, 0)
        self.assertIn("No backups yet", out)

    def test_lists_newest_first_and_ignores_other_files(self):
        self.archive("headscale-easy-20261003-030000.tar.gz", 2048, age=2 * 86400 + 60)
        self.archive("headscale-easy-20261005-030000.tar.gz", 1048576, age=3600 * 3)
        self.archive("headscale-easy-20261005-040000-pre-restore-x.tar.gz", 10, age=60)
        self.archive(".headscale-easy-20261005-050000.tar.gz.part", 5)
        self.archive("notes.txt", 5)
        code, out, _err = self.run_cli("backups")
        self.assertEqual(code, 0)
        self.assertIn("3 backups", out)
        self.assertNotIn("notes.txt", out)
        self.assertNotIn(".part", out)
        order = [line.split()[0] for line in out.splitlines() if line.startswith("  headscale-easy-")]
        self.assertEqual(order, ["headscale-easy-20261005-040000-pre-restore-x.tar.gz",
                                 "headscale-easy-20261005-030000.tar.gz",
                                 "headscale-easy-20261003-030000.tar.gz"])
        self.assertIn("taken before a restore", out)
        self.assertIn("1.0 MB", out)
        self.assertIn("2 days ago", out)
        self.assertIn("3 hours ago", out)

    def test_last_run_from_status_json(self):
        self.archive()
        status = os.path.join(self.data, "backups", "status.json")
        with open(status, "w") as fh:
            json.dump({"last": {"at": "2026-10-05T03:00:04Z", "ok": False, "trigger": "scheduled",
                                "error": "disk full"}}, fh)
        _c, out, _e = self.run_cli("backups")
        self.assertIn("FAILED, 2026-10-05 03:00 UTC (scheduled): disk full", out)
        with open(status, "w") as fh:
            json.dump({"last": {"at": "2026-10-05T03:00:04Z", "ok": True, "trigger": "manual"}}, fh)
        self.assertIn("Last run: ok, 2026-10-05 03:00 UTC (manual)", self.run_cli("backups")[1])
        with open(status, "w") as fh:
            fh.write("{not json")
        self.assertEqual(self.run_cli("backups")[0], 0)  # a broken status file never breaks the listing

    def test_arguments_are_refused(self):
        self.assertEqual(self.run_cli("backups", "x")[0], 2)


class RestoreOffline(Case):
    def setUp(self):
        super().setUp()
        self.file = self.archive()

    def test_missing_file(self):
        code, _out, err = self.run_cli("restore", "/nonexistent.tar.gz", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("no such file", err)
        self.rs.inspect.assert_not_called()

    def test_usage(self):
        for args in (("restore",), ("restore", "a", "b"), ("restore", self.file, "--force")):
            self.assertEqual(self.run_cli(*args)[0], 2, args)
        self.rs.restore.assert_not_called()

    def test_restores_offline_with_yes(self):
        self.rs.restore.return_value = {"ok": True, "safety_copy": "/data/backups/x-pre-restore-y.tar.gz"}
        code, out, _err = self.run_cli("restore", self.file, "--yes")
        self.assertEqual(code, 0)
        self.rs.inspect.assert_called_once_with(self.file)
        self.rs.restore.assert_called_once_with(self.file, self.data, offline=True, with_postgres=False)
        self.assertIn("v0.29.4", out)
        self.assertIn("2 files", out)
        self.assertIn("restored", out)
        self.assertIn("x-pre-restore-y.tar.gz", out)

    def test_with_postgres_flag(self):
        self.run_cli("restore", self.file, "--yes", "--with-postgres")
        self.assertTrue(self.rs.restore.call_args.kwargs["with_postgres"])

    def test_bare_name_is_looked_up_in_the_backups_dir(self):
        self.assertEqual(self.run_cli("restore", os.path.basename(self.file), "--yes")[0], 0)
        self.rs.inspect.assert_called_once_with(self.file)

    def test_relative_path_becomes_absolute(self):
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(os.path.dirname(self.file))
        self.run_cli("restore", "./" + os.path.basename(self.file), "--yes")
        self.assertEqual(self.rs.inspect.call_args.args[0], os.path.realpath(self.file))

    def test_refuses_an_archive_that_does_not_validate_and_changes_nothing(self):
        self.rs.inspect.side_effect = RestoreError("this is a 1.x backup: use scripts/restore.sh")
        code, _out, err = self.run_cli("restore", self.file, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("1.x backup", err)
        self.rs.restore.assert_not_called()

    def test_no_terminal_and_no_yes_is_refused(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=False):
            code, _out, err = self.run_cli("restore", self.file)
        self.assertEqual(code, 2)
        self.assertIn("--yes", err)
        self.rs.restore.assert_not_called()

    def test_interactive_confirmation(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=True):
            with mock.patch("builtins.input", return_value="n"):
                code, out, _err = self.run_cli("restore", self.file)
            self.assertEqual(code, 0)
            self.assertIn("cancelled", out)
            self.rs.restore.assert_not_called()
            with mock.patch("builtins.input", return_value="y"):
                self.assertEqual(self.run_cli("restore", self.file)[0], 0)
        self.rs.restore.assert_called_once()

    def test_engine_errors(self):
        self.rs.restore.side_effect = RestoreError("sha256 mismatch")
        code, _out, err = self.run_cli("restore", self.file, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("sha256 mismatch", err)
        self.rs.restore.side_effect = BackupBusy()
        self.assertEqual(self.run_cli("restore", self.file, "--yes")[0], 3)

    def test_a_dead_supervisor_pid_means_offline(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        with open(os.path.join(self.run_dir, "supervisor.pid"), "w") as fh:
            fh.write(str(child.pid))
        self.assertEqual(self.run_cli("restore", self.file, "--yes")[0], 0)
        self.rs.restore.assert_called_once()


class RestoreOnline(Case):
    """This process is the 'supervisor': it has a pid file and answers SIGUSR1."""

    def setUp(self):
        super().setUp()
        self.file = self.archive()
        with open(os.path.join(self.run_dir, "supervisor.pid"), "w") as fh:
            fh.write(str(os.getpid()))
        self.requests = []
        self.answer = lambda req: {"ok": True, "requested": req["requested"], "safety_copy": "/data/backups/safe.tar.gz"}
        old = signal.signal(signal.SIGUSR1, self.on_usr1)
        self.addCleanup(signal.signal, signal.SIGUSR1, old)

    def on_usr1(self, *_):
        with open(os.path.join(self.run_dir, "restore.json"), encoding="utf-8") as fh:
            req = json.load(fh)
        self.requests.append(req)
        mode = os.stat(os.path.join(self.run_dir, "restore.json")).st_mode & 0o777
        self.assertEqual(mode, 0o600)
        data = self.answer(req)
        if data is not None:
            with open(os.path.join(self.run_dir, "restore-result.json"), "w", encoding="utf-8") as fh:
                json.dump(data, fh)

    def test_hands_the_restore_to_the_supervisor(self):
        code, out, _err = self.run_cli("restore", self.file, "--yes", "--with-postgres")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["archive"], self.file)
        self.assertTrue(self.requests[0]["with_postgres"])
        self.assertIn("restored", out)
        self.assertIn("safe.tar.gz", out)
        self.rs.restore.assert_not_called()  # only the supervisor mutates /data online

    def test_a_failed_restore_exits_1_with_the_reason(self):
        self.answer = lambda req: {"ok": False, "requested": req["requested"], "error": "setup mode: nothing to restore over"}
        code, _out, err = self.run_cli("restore", self.file, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("setup mode", err)

    def test_busy_exits_3(self):
        self.answer = lambda req: {"ok": False, "requested": req["requested"], "error": "a backup is already running"}
        self.assertEqual(self.run_cli("restore", self.file, "--yes")[0], 3)

    def test_no_answer_times_out(self):
        self.answer = lambda req: None
        started = time.monotonic()
        code, _out, err = self.run_cli("restore", self.file, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("no answer", err)
        self.assertLess(time.monotonic() - started, 5)

    def test_a_stale_result_is_not_taken_for_the_answer(self):
        stale = os.path.join(self.run_dir, "restore-result.json")
        with open(stale, "w") as fh:
            json.dump({"ok": True, "requested": 1.0}, fh)
        self.answer = lambda req: None
        self.assertEqual(self.run_cli("restore", self.file, "--yes")[0], 1)
        self.assertFalse(os.path.exists(stale))

    def test_an_answer_for_another_request_is_ignored(self):
        self.answer = lambda req: {"ok": True, "requested": req["requested"] + 100}
        self.assertEqual(self.run_cli("restore", self.file, "--yes")[0], 1)

    def test_validation_failure_never_signals(self):
        self.rs.inspect.side_effect = RestoreError("tampered")
        self.assertEqual(self.run_cli("restore", self.file, "--yes")[0], 1)
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
