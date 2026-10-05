"""aio/restore.py: validating and restoring backups of the all-in-one image.

The safety copy and the lock normally come from aio/backup.py; here they are
fakes (``FakeBackup``), so these tests do not depend on that module.

    python3 -m unittest tests.test_restore
"""
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from aio import restore  # noqa: E402
import backup_fixtures as fx  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "backup")


class FakeBackup:
    """Stands in for aio/backup.py: ``create`` writes a tar.gz of the current data, ``lock`` records."""

    def __init__(self, ok=True):
        self.ok = ok
        self.created = []
        self.locks = 0

    def create(self, data_dir, out_dir=None, settings=None, trigger="manual", require_settings=True):
        if not self.ok:
            return mock.Mock(ok=False, path=None, error="disk full")
        path = os.path.join(out_dir, "headscale-easy-20260102-030000.tar.gz")
        fx.build_aio_archive(path, data_tree_from_disk(data_dir))
        self.created.append((path, trigger))
        return mock.Mock(ok=True, path=path, error=None)

    @contextlib.contextmanager
    def lock(self, out_dir):
        self.locks += 1
        yield


def data_tree_from_disk(data_dir):
    """The tree of the data currently on disk (just enough for the safety copy)."""
    return fx.data_tree(read_json(os.path.join(data_dir, "config", "settings.json"))["marker"])


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def snapshot(root):
    """{relative path: bytes} of every file under root."""
    out = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, root)] = fh.read()
    return out


class RestoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = os.path.join(self.tmp.name, "data")
        self.run_dir = os.path.join(self.tmp.name, "run")
        os.makedirs(self.run_dir)
        self.archive = os.path.join(self.tmp.name, "a.tar.gz")
        self.fake = FakeBackup()
        p = mock.patch.object(restore, "_backup", lambda: self.fake)
        p.start()
        self.addCleanup(p.stop)

    def restore(self, **kw):
        return restore.restore(self.archive, self.data, run_dir=self.run_dir, **kw)

    def installed(self, marker="b"):
        os.makedirs(self.data, mode=0o700, exist_ok=True)
        fx.write_data(self.data, fx.data_tree(marker))

    def assertRefused(self, archive=None, text="", **kw):
        before = snapshot(self.tmp.name) if os.path.isdir(self.data) else None
        with self.assertRaises(restore.RestoreError) as ctx:
            restore.restore(archive or self.archive, self.data, run_dir=self.run_dir, **kw)
        self.assertIn(text, str(ctx.exception))
        if before is not None:
            after = snapshot(self.tmp.name)
            self.assertEqual({k: v for k, v in after.items() if not k.startswith("data/backups")},
                             {k: v for k, v in before.items() if not k.startswith("data/backups")},
                             "a refused restore must not change anything")
        return ctx.exception


class InspectTest(RestoreCase):
    def test_valid_archive(self):
        fx.build_aio_archive(self.archive)
        meta = restore.inspect(self.archive)
        self.assertEqual((meta["format"], meta["edition"]), (2, "aio"))
        self.assertIn("config/settings.json", meta["files"])

    def test_missing_file(self):
        with self.assertRaises(restore.RestoreError):
            restore.inspect(os.path.join(self.tmp.name, "nope.tar.gz"))

    def test_1x_archive_is_refused_with_a_pointer(self):
        exc = self.assertRefused(os.path.join(FIXTURES, "1x-archive.tar.gz"), "1.x")
        self.assertIn("migration", str(exc))
        with self.assertRaises(restore.RestoreError):
            restore.inspect(os.path.join(FIXTURES, "1x-archive.tar.gz"))

    def test_committed_aio_fixture_is_valid(self):
        self.assertEqual(restore.inspect(os.path.join(FIXTURES, "aio-archive.tar.gz"))["edition"], "aio")

    def test_wrong_edition_and_format(self):
        fx.build_aio_archive(self.archive, meta={"edition": "compose"})
        self.assertRefused(text="edition")
        fx.build_aio_archive(self.archive, meta={"format": 9})
        self.assertRefused(text="format")

    def test_truncated_archive(self):
        fx.build_aio_archive(self.archive)
        with open(self.archive, "rb") as fh:
            blob = fh.read()
        with open(self.archive, "wb") as fh:
            fh.write(blob[: len(blob) // 2])
        self.assertRefused(text="readable")

    def test_not_an_archive(self):
        with open(self.archive, "wb") as fh:
            fh.write(b"definitely not gzip")
        self.assertRefused(text="readable")

    def test_tampered_file_is_caught_by_sha256(self):
        tree = fx.data_tree("a")
        fx.build_aio_archive(self.archive, tree, hashes={"config/Caddyfile": "0" * 64})
        self.assertRefused(text="checksum mismatch")

    def test_file_not_listed_in_meta(self):
        tree = fx.data_tree("a")
        listed = {k: v for k, v in tree.items() if k != "config/derp.yaml"}
        fx.build_aio_archive(self.archive, tree, meta={"files": {
            rel: __import__("hashlib").sha256(c).hexdigest() for rel, c in listed.items()}})
        self.assertRefused(text="does not match meta.json")

    def test_corrupt_database_with_valid_checksum(self):
        tree = fx.data_tree("a")
        tree["console/accounts.db"] = b"SQLite format 3\x00" + b"\xff" * 200
        fx.build_aio_archive(self.archive, tree)
        self.assertRefused(text="integrity")

    def test_required_files(self):
        tree = fx.data_tree("a")
        del tree["config/settings.json"]
        fx.build_aio_archive(self.archive, tree)
        self.assertRefused(text="settings.json")
        tree = fx.data_tree("a")
        del tree["headscale/db.sqlite"]
        fx.build_aio_archive(self.archive, tree)
        self.assertRefused(text="Headscale database")


class HostileArchiveTest(RestoreCase):
    """Nothing is written, inside or outside /data, for any of these."""

    def hostile(self, info, content=None, **kw):
        fx.build_aio_archive(self.archive, extra=[(info, content)], **kw)

    def info(self, name, kind=tarfile.REGTYPE, link=""):
        i = tarfile.TarInfo(name)
        i.type = kind
        i.linkname = link
        if kind == tarfile.REGTYPE:
            i.size = 4
        return i

    def check(self, text=""):
        self.installed("b")
        outside = os.path.join(self.tmp.name, "pwned")
        self.assertRefused(text=text)
        self.assertFalse(os.path.exists(outside))
        self.assertFalse(os.path.exists(os.path.join(self.data, "..", "pwned")))

    def test_parent_directory_traversal(self):
        self.hostile(self.info(fx.TOP + "/../../pwned"), b"evil")
        self.check("unsafe")

    def test_absolute_path(self):
        self.hostile(self.info("/tmp/hse-pwned"), b"evil")
        self.check("unsafe")

    def test_symlink(self):
        self.hostile(self.info(fx.TOP + "/config/link", tarfile.SYMTYPE, "/etc/passwd"))
        self.check("plain file")

    def test_hardlink(self):
        self.hostile(self.info(fx.TOP + "/config/link", tarfile.LNKTYPE, fx.TOP + "/meta.json"))
        self.check("plain file")

    def test_device_and_fifo(self):
        self.hostile(self.info(fx.TOP + "/config/dev", tarfile.CHRTYPE))
        self.check("plain file")
        self.hostile(self.info(fx.TOP + "/config/fifo", tarfile.FIFOTYPE))
        self.check("plain file")

    def test_second_top_level_directory(self):
        self.hostile(self.info("other/config/x"), b"evil")
        self.check("top-level")

    def test_unexpected_name_or_depth(self):
        for name in ("/etc/cron", "/config/sub/dir/x", "/etc/passwd", "/web/other.db", "/config/.hidden"):
            with self.subTest(name=name):
                self.hostile(self.info(fx.TOP + name), b"evil")
                self.check("unexpected")

    def test_duplicate_member(self):
        self.hostile(self.info(fx.TOP + "/config/Caddyfile"), b"evil")
        self.check("duplicate")

    def test_too_many_members(self):
        fx.build_aio_archive(self.archive)
        with mock.patch.object(restore, "MAX_FILES", 3):
            self.installed("b")
            self.assertRefused(text="too many")

    def test_too_large(self):
        fx.build_aio_archive(self.archive)
        with mock.patch.object(restore, "MAX_TOTAL", 10):
            self.installed("b")
            self.assertRefused(text="too large")


class RoundTripTest(RestoreCase):
    def test_round_trip_restores_everything(self):
        tree_a = fx.data_tree("a")
        fx.build_aio_archive(self.archive, tree_a)
        self.installed("b")  # the live data differs
        result = self.restore()
        self.assertTrue(result["ok"])
        self.assertEqual(fx.read_tree(self.data, tree_a), tree_a)
        self.assertEqual(fx.sqlite_value(os.path.join(self.data, "headscale", "db.sqlite")), "hs-a")
        self.assertEqual(fx.sqlite_value(os.path.join(self.data, "console", "accounts.db")), "accounts-a")
        # the AIO keeps its audit log in console/, the CA where Caddy looks for it
        self.assertEqual(fx.sqlite_value(os.path.join(self.data, "console", "audit.db")), "audit-a")
        self.assertTrue(os.path.isfile(os.path.join(self.data, "caddy", "caddy", "pki", "authorities",
                                                    "local", "root.crt")))
        # config.yaml (with the console's DNS edits) comes back, not only settings.json
        self.assertIn("marker: a", read_text(os.path.join(self.data, "config", "config.yaml")))

    def test_sessions_and_journals_are_not_restored(self):
        fx.build_aio_archive(self.archive)
        self.installed("b")
        self.restore()
        self.assertFalse(os.path.exists(os.path.join(self.data, "console", "sessions.db")))
        for stale in ("headscale/db.sqlite-wal", "headscale/db.sqlite-shm", "console/accounts.db-wal"):
            self.assertFalse(os.path.exists(os.path.join(self.data, stale)), stale)

    def test_modes_are_private(self):
        fx.build_aio_archive(self.archive)
        self.installed("b")
        self.restore()
        for base, dirs, files in os.walk(self.data):
            for name in dirs:
                if name.startswith("."):
                    continue
                self.assertEqual(os.stat(os.path.join(base, name)).st_mode & 0o077, 0, name)
            for name in files:
                path = os.path.join(base, name)
                if os.path.dirname(path).endswith("backups"):
                    continue
                self.assertEqual(os.stat(path).st_mode & 0o077, 0, path)

    def test_temporary_directories_are_cleaned_up(self):
        fx.build_aio_archive(self.archive)
        self.installed("b")
        self.restore()
        self.assertFalse(os.path.exists(os.path.join(self.data, restore.STAGING)))
        self.assertFalse(os.path.exists(os.path.join(self.data, restore.ROLLBACK)))

    def test_safety_copy_is_taken_first_and_named(self):
        fx.build_aio_archive(self.archive)
        self.installed("b")
        result = self.restore()
        self.assertIn("-pre-restore-", os.path.basename(result["safety_copy"]))
        self.assertTrue(os.path.isfile(result["safety_copy"]))
        self.assertEqual(self.fake.created[0][1], "pre-restore")
        self.assertEqual(self.fake.locks, 1)  # the lock is held while applying
        # the safety copy holds the data as it was before the restore
        self.assertIn("config/settings.json", restore.inspect(result["safety_copy"])["files"])

    def test_fresh_volume_needs_no_safety_copy(self):
        tree_a = fx.data_tree("a")
        fx.build_aio_archive(self.archive, tree_a)
        result = self.restore()  # /data does not even exist yet
        self.assertIsNone(result["safety_copy"])
        self.assertEqual(self.fake.created, [])
        self.assertEqual(fx.read_tree(self.data, tree_a), tree_a)
        # the container then starts in run mode: settings.json is there
        self.assertTrue(read_json(os.path.join(self.data, "config", "settings.json"))["public_url"])

    def test_failed_safety_copy_changes_nothing(self):
        fx.build_aio_archive(self.archive)
        self.installed("b")
        self.fake.ok = False
        self.assertRefused(text="safety copy")

    def test_offline_false_is_not_supported_here(self):
        fx.build_aio_archive(self.archive)
        with self.assertRaises(restore.RestoreError):
            self.restore(offline=False)

    def test_runs_again_over_its_own_result(self):
        fx.build_aio_archive(self.archive)
        self.installed("b")
        self.restore()
        self.restore()
        self.assertEqual(fx.sqlite_value(os.path.join(self.data, "headscale", "db.sqlite")), "hs-a")


class RollbackTest(RestoreCase):
    def test_failure_midway_puts_the_old_data_back(self):
        tree_a = fx.data_tree("a")
        fx.build_aio_archive(self.archive, tree_a)
        self.installed("b")
        before = {k: v for k, v in snapshot(self.data).items() if not k.startswith("backups")}
        real = os.replace

        def flaky(src, dst, *a, **k):
            if str(dst).endswith(os.path.join("console", "accounts.db")) and restore.STAGING in str(src):
                raise OSError("disk exploded")
            return real(src, dst, *a, **k)

        with mock.patch.object(restore.os, "replace", flaky):
            with self.assertRaises(restore.RestoreError) as ctx:
                self.restore()
        self.assertIn("rolled back", str(ctx.exception))
        after = {k: v for k, v in snapshot(self.data).items() if not k.startswith("backups")}
        self.assertEqual(after, before)  # including sessions.db and the stale journals
        self.assertFalse(os.path.exists(os.path.join(self.data, restore.STAGING)))
        self.assertFalse(os.path.exists(os.path.join(self.data, restore.ROLLBACK)))

    def test_failed_rollback_keeps_the_old_data_and_says_where(self):
        fx.build_aio_archive(self.archive)
        self.installed("b")
        real = os.replace

        def broken(src, dst, *a, **k):
            if restore.STAGING in str(src) and str(dst).endswith("accounts.db"):
                raise OSError("first failure")
            if restore.ROLLBACK in str(src):
                raise OSError("rollback failure")
            return real(src, dst, *a, **k)

        with mock.patch.object(restore.os, "replace", broken):
            with self.assertRaises(restore.RestoreError) as ctx:
                self.restore()
        self.assertIn("rollback failed", str(ctx.exception))
        self.assertIn(restore.ROLLBACK, str(ctx.exception))
        self.assertTrue(os.path.isdir(os.path.join(self.data, restore.ROLLBACK)))


class RunningStackTest(RestoreCase):
    def test_refused_while_a_supervisor_is_alive(self):
        fx.build_aio_archive(self.archive)
        self.installed("b")
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(lambda: (sleeper.kill(), sleeper.wait()))
        with open(os.path.join(self.run_dir, "supervisor.pid"), "w") as fh:
            fh.write(str(sleeper.pid))
        self.assertRefused(text="running")

    def test_stale_pid_file_is_ignored(self):
        fx.build_aio_archive(self.archive)
        with open(os.path.join(self.run_dir, "supervisor.pid"), "w") as fh:
            fh.write("999999")
        self.assertTrue(self.restore()["ok"])

    def test_the_supervisor_itself_may_restore(self):
        fx.build_aio_archive(self.archive)
        with open(os.path.join(self.run_dir, "supervisor.pid"), "w") as fh:
            fh.write(str(os.getpid()))
        self.assertTrue(self.restore()["ok"])


class PostgresTest(RestoreCase):
    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.bin)
        self.log = os.path.join(self.tmp.name, "psql.log")

    def fake_psql(self, code=0):
        path = os.path.join(self.bin, "psql")
        with open(path, "w") as fh:
            fh.write('#!/bin/sh\necho "$@" >> %s\necho "PGPASSWORD=$PGPASSWORD PGHOST=$PGHOST" >> %s.env\n'
                     'echo "boom" >&2\nexit %d\n' % (self.log, self.log, code))
        os.chmod(path, 0o755)
        return mock.patch.dict(os.environ, {"PATH": self.bin + os.pathsep + os.environ["PATH"],
                                            "HEADSCALE_PG_HOST": "db.example", "HEADSCALE_PG_PASS": "fake-pass"})

    def test_dump_is_not_loaded_without_the_flag(self):
        fx.build_aio_archive(self.archive, fx.data_tree("a", postgres=True))
        self.installed("b")
        result = self.restore()
        self.assertTrue(any("NOT loaded" in w for w in result["warnings"]))
        self.assertFalse(result["postgres_loaded"])
        # a stale SQLite file never stays next to a PostgreSQL restore
        self.assertFalse(os.path.exists(os.path.join(self.data, "headscale", "db.sqlite")))
        self.assertFalse(os.path.exists(os.path.join(self.data, "headscale", "headscale.sql")))

    def test_with_postgres_runs_psql_without_the_password_in_argv(self):
        fx.build_aio_archive(self.archive, fx.data_tree("a", postgres=True))
        self.installed("b")
        with self.fake_psql():
            result = self.restore(with_postgres=True)
        self.assertTrue(result["postgres_loaded"])
        argv = read_text(self.log)
        self.assertIn("ON_ERROR_STOP=1", argv)
        self.assertNotIn("fake-pass", argv)
        self.assertIn("PGPASSWORD=fake-pass PGHOST=db.example", read_text(self.log + ".env"))

    def test_missing_psql_is_refused_before_anything_changes(self):
        fx.build_aio_archive(self.archive, fx.data_tree("a", postgres=True))
        self.installed("b")
        with mock.patch.dict(os.environ, {"PATH": self.bin}):
            self.assertRefused(text="psql", with_postgres=True)

    def test_failing_psql_rolls_the_files_back(self):
        fx.build_aio_archive(self.archive, fx.data_tree("a", postgres=True))
        self.installed("b")
        before = {k: v for k, v in snapshot(self.data).items() if not k.startswith("backups")}
        with self.fake_psql(code=3):
            with self.assertRaises(restore.RestoreError):
                self.restore(with_postgres=True)
        after = {k: v for k, v in snapshot(self.data).items() if not k.startswith("backups")}
        self.assertEqual(after, before)


class RequestOnlineTest(RestoreCase):
    def pid_file(self, pid):
        with open(os.path.join(self.run_dir, "supervisor.pid"), "w") as fh:
            fh.write(str(pid))

    def test_no_supervisor(self):
        fx.build_aio_archive(self.archive)
        with self.assertRaises(restore.RestoreError) as ctx:
            restore.request_online(self.archive, self.run_dir)
        self.assertIn("offline", str(ctx.exception))

    def test_invalid_archive_never_signals(self):
        self.pid_file(os.getpid())
        with mock.patch.object(restore.os, "kill") as kill:
            with self.assertRaises(restore.RestoreError):
                restore.request_online(os.path.join(FIXTURES, "1x-archive.tar.gz"), self.run_dir)
        kill.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, restore.RESTORE_REQUEST)))

    def test_request_signal_and_result(self):
        fx.build_aio_archive(self.archive)
        self.pid_file(os.getpid())
        got = threading.Event()
        old = signal.signal(signal.SIGUSR1, lambda *_: got.set())
        self.addCleanup(signal.signal, signal.SIGUSR1, old)

        def fake_supervisor():
            req = os.path.join(self.run_dir, restore.RESTORE_REQUEST)
            got.wait(5)
            doc = read_json(req)
            self.assertEqual(doc["archive"], self.archive)
            self.assertEqual(os.stat(req).st_mode & 0o777, 0o600)
            with open(os.path.join(self.run_dir, restore.RESTORE_RESULT), "w") as fh:
                json.dump({"ok": True, "requested": doc["requested"]}, fh)
            os.unlink(req)

        t = threading.Thread(target=fake_supervisor)
        t.start()
        result = restore.request_online(self.archive, self.run_dir, timeout=10)
        t.join()
        self.assertTrue(result["ok"])

    def test_timeout(self):
        fx.build_aio_archive(self.archive)
        self.pid_file(os.getpid())
        old = signal.signal(signal.SIGUSR1, lambda *_: None)
        self.addCleanup(signal.signal, signal.SIGUSR1, old)
        with self.assertRaises(restore.RestoreError) as ctx:
            restore.request_online(self.archive, self.run_dir, timeout=0.5)
        self.assertIn("did not report", str(ctx.exception))

    def test_second_request_while_one_is_pending(self):
        fx.build_aio_archive(self.archive)
        self.pid_file(os.getpid())
        with open(os.path.join(self.run_dir, restore.RESTORE_REQUEST), "w"):
            pass
        with self.assertRaises(restore.RestoreError) as ctx:
            restore.request_online(self.archive, self.run_dir)
        self.assertIn("already", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
