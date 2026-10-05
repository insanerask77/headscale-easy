"""aio/backup.py: create, verify, prune, status, lock and the pg_dump path.

Everything runs on a fake /data in a temp dir; pg_dump is a small fake script
put first in PATH. No Docker, no network.

    python3 -m unittest tests.test_backup
"""
import json
import os
import sqlite3
import stat
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
from aio import backup  # noqa: E402

SETTINGS = {"db_type": "sqlite", "backup_keep_days": "14"}


def make_db(path, rows=3, wal=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO t (v) VALUES (?)", [("row%d" % i,) for i in range(rows)])
    con.commit()
    con.close()


def write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = os.path.join(self._tmp.name, "data")
        self.out = os.path.join(self.data, "backups")
        d = self.data
        write(os.path.join(d, "config", "settings.json"), '{"public_url": "http://x"}')
        write(os.path.join(d, "config", "config.yaml"), "dns: {}\n")
        write(os.path.join(d, "config", "Caddyfile"), ":80\n")
        write(os.path.join(d, "config", "session-secret"), "s3cret")
        write(os.path.join(d, "console", "api-key"), "key")
        write(os.path.join(d, "console", "sessions.db"), "must not be backed up")
        make_db(os.path.join(d, "headscale", "db.sqlite"))
        write(os.path.join(d, "headscale", "noise_private.key"), "privkey:aaaa")
        make_db(os.path.join(d, "console", "accounts.db"))
        make_db(os.path.join(d, "console", "audit.db"))
        write(os.path.join(d, "caddy", "caddy", "pki", "authorities", "local", "root.crt"), "CERT")
        self.logs = []

    def create(self, **kw):
        kw.setdefault("settings", dict(SETTINGS))
        return backup.create(self.data, log=self.logs.append, **kw)

    def members(self, path):
        with tarfile.open(path, "r:gz") as tar:
            return {m.name.split("/", 1)[1]: m for m in tar.getmembers() if "/" in m.name}


class CreateTest(Base):
    def test_layout_and_meta(self):
        res = self.create()
        self.assertTrue(res.ok, res.error)
        names = set(self.members(res.path))
        for want in ("meta.json", "config/settings.json", "config/config.yaml", "config/Caddyfile",
                     "config/session-secret", "headscale/db.sqlite", "headscale/noise_private.key",
                     "console/accounts.db", "console/api-key", "web/audit.db",
                     "caddy/pki/authorities/local/root.crt"):
            self.assertIn(want, names)
        # same top-level names as backup/backup.sh, plus console/
        tops = {n.split("/")[0] for n in names if "/" in n}
        self.assertTrue({"config", "headscale", "web", "caddy"} <= tops)
        self.assertLessEqual(tops, {"config", "headscale", "web", "caddy", "console"})
        with tarfile.open(res.path, "r:gz") as tar:
            top = tar.getnames()[0]
            meta = json.load(tar.extractfile(top + "/meta.json"))
        self.assertEqual((meta["format"], meta["edition"], meta["db_type"]), (2, "aio", "sqlite"))
        files = {n for n, m in self.members(res.path).items() if m.isreg()} - {"meta.json"}
        self.assertEqual(set(meta["files"]), files)

    def test_optional_files_skipped_required_missing_fails(self):
        self.assertTrue(self.create().ok)  # no derp.yaml here: optional
        os.unlink(os.path.join(self.data, "config", "settings.json"))
        res = self.create()
        self.assertFalse(res.ok)
        self.assertIn("settings.json", res.error)
        write(os.path.join(self.data, "config", "settings.json"), "{}")
        os.unlink(os.path.join(self.data, "headscale", "db.sqlite"))
        res = self.create()
        self.assertFalse(res.ok)
        self.assertIn("db.sqlite", res.error)

    def test_no_sessions_db_and_permissions(self):
        res = self.create()
        names = set(self.members(res.path))
        self.assertFalse([n for n in names if "sessions" in n])
        self.assertEqual(stat.S_IMODE(os.stat(res.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.out).st_mode), 0o700)
        for m in self.members(res.path).values():
            self.assertEqual(m.mode, 0o700 if m.isdir() else 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.out, "status.json")).st_mode), 0o600)

    def test_consistent_copy_under_writes(self):
        db = os.path.join(self.data, "headscale", "db.sqlite")
        os.unlink(db)
        make_db(db, rows=2000, wal=True)
        stop = threading.Event()

        def writer():
            con = sqlite3.connect(db, timeout=30)
            while not stop.is_set():
                con.execute("INSERT INTO t (v) VALUES ('w')")
                con.commit()
            con.close()

        th = threading.Thread(target=writer)
        th.start()
        try:
            res = self.create()
        finally:
            stop.set()
            th.join()
        self.assertTrue(res.ok, res.error)
        with tempfile.TemporaryDirectory() as tmp, tarfile.open(res.path, "r:gz") as tar:
            top = tar.getnames()[0]
            tar.extract(top + "/headscale/db.sqlite", tmp, filter="data")
            con = sqlite3.connect(os.path.join(tmp, top, "headscale", "db.sqlite"))
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertGreaterEqual(con.execute("SELECT count(*) FROM t").fetchone()[0], 2000)
            con.close()

    def test_corrupted_source_fails_and_leaves_nothing(self):
        first = self.create()
        self.assertTrue(first.ok)
        with open(os.path.join(self.data, "console", "accounts.db"), "wb") as fh:
            fh.write(b"this is not a database" * 50)
        res = self.create()
        self.assertFalse(res.ok)
        left = sorted(os.listdir(self.out))
        self.assertEqual([n for n in left if n.endswith(".tar.gz")], [os.path.basename(first.path)])
        self.assertFalse([n for n in left if n.startswith(".work") or n.endswith(".part")])
        st = backup.read_status(self.out)
        self.assertFalse(st["last"]["ok"])
        self.assertTrue(st["last_ok"]["ok"])
        self.assertEqual(st["count"], 1)

    def test_status_after_success(self):
        res = self.create(trigger="scheduled")
        st = backup.read_status(self.out)
        self.assertTrue(st["last"]["ok"])
        self.assertEqual(st["last"]["trigger"], "scheduled")
        self.assertEqual(st["last"]["file"], os.path.basename(res.path))
        self.assertEqual((st["count"], st["bytes"]), (1, res.size))
        self.assertEqual(backup.read_status(os.path.join(self._tmp.name, "nope")), {})

    def test_two_backups_in_one_second_do_not_collide(self):
        with mock.patch("aio.backup.time.time", return_value=1_800_000_000.0):
            a, b = self.create(), self.create()
        self.assertTrue(a.ok and b.ok)
        self.assertNotEqual(a.path, b.path)


class LockTest(Base):
    def test_busy_when_locked(self):
        with backup.lock(self.out):
            with self.assertRaises(backup.BackupBusy):
                self.create()
        self.assertTrue(self.create().ok)

    def test_cli_exit_codes(self):
        env = dict(os.environ, HSE_DATA_DIR=self.data)
        cmd = [sys.executable, os.path.join(ROOT, "aio", "backup.py"), "create", "--trigger", "manual"]
        ok = subprocess.run(cmd, env=env, capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        with backup.lock(self.out):
            self.assertEqual(subprocess.run(cmd, env=env, capture_output=True).returncode, 3)
        os.unlink(os.path.join(self.data, "config", "settings.json"))
        self.assertEqual(subprocess.run(cmd, env=env, capture_output=True).returncode, 1)


class PruneTest(Base):
    def archive(self, name, age_days, now):
        p = os.path.join(self.out, name)
        os.makedirs(self.out, exist_ok=True)
        write(p, "x")
        os.utime(p, (now - age_days * 86400,) * 2)
        return p

    def test_old_pruned_newest_kept_unrelated_ignored(self):
        now = time.time()
        self.archive("headscale-easy-a.tar.gz", 30, now)
        self.archive("headscale-easy-b.tar.gz", 20, now)
        self.archive("headscale-easy-c.tar.gz", 1, now)
        other = self.archive("notes.txt", 400, now)
        self.assertEqual(sorted(backup.prune(self.out, 14, now)),
                         ["headscale-easy-a.tar.gz", "headscale-easy-b.tar.gz"])
        self.assertTrue(os.path.exists(other))

    def test_newest_kept_even_when_older_than_limit(self):
        now = time.time()
        self.archive("headscale-easy-a.tar.gz", 90, now)
        self.archive("headscale-easy-b.tar.gz", 60, now)
        self.assertEqual(backup.prune(self.out, 14, now), ["headscale-easy-a.tar.gz"])
        self.assertTrue(os.path.exists(os.path.join(self.out, "headscale-easy-b.tar.gz")))

    def test_part_files(self):
        now = time.time()
        young = self.archive(".headscale-easy-x.tar.gz.part", 0, now)
        old = self.archive(".headscale-easy-y.tar.gz.part", 1, now)
        backup.prune(self.out, 14, now)
        self.assertTrue(os.path.exists(young))
        self.assertFalse(os.path.exists(old))

    def test_create_prunes_by_keep_days(self):
        old = self.create()
        os.utime(old.path, (1, 1))
        with mock.patch("aio.backup.time.time", return_value=time.time() + 5):
            new = self.create(settings={"db_type": "sqlite", "backup_keep_days": "1"})
        self.assertTrue(new.ok)
        self.assertFalse(os.path.exists(old.path))
        self.assertTrue(os.path.exists(new.path))


class VerifyTest(Base):
    def test_truncated_archive(self):
        res = self.create()
        with open(res.path, "rb") as fh:
            blob = fh.read()
        bad = os.path.join(self.out, "bad.tar.gz")
        with open(bad, "wb") as fh:
            fh.write(blob[: len(blob) // 2])
        with self.assertRaises(backup.BackupError):
            backup.verify(bad)

    def test_tampered_member(self):
        res = self.create()
        bad = os.path.join(self.out, "tampered.tar.gz")
        with tarfile.open(res.path, "r:gz") as src, tarfile.open(bad, "w:gz") as dst:
            for m in src.getmembers():
                fh = src.extractfile(m) if m.isreg() else None
                if m.name.endswith("config/Caddyfile"):
                    import io
                    data = b"evil\n"
                    m.size = len(data)
                    fh = io.BytesIO(data)
                dst.addfile(m, fh)
        with self.assertRaises(backup.BackupError):
            backup.verify(bad)
        backup.verify(res.path)

    def test_unsafe_names_and_links_rejected(self):
        import io
        for kind in ("abs", "dotdot", "link"):
            path = os.path.join(self.out, "u-%s.tar.gz" % kind)
            os.makedirs(self.out, exist_ok=True)
            with tarfile.open(path, "w:gz") as tar:
                info = tarfile.TarInfo({"abs": "/etc/x", "dotdot": "top/../x", "link": "top/l"}[kind])
                if kind == "link":
                    info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                    tar.addfile(info)
                else:
                    info.size = 1
                    tar.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(backup.BackupError, msg=kind):
                backup.verify(path)


FAKE_PG_DUMP = """#!/bin/sh
echo "$*" > "$FAKE_DIR/argv"
echo "PGPASSWORD=$PGPASSWORD PGHOST=$PGHOST PGUSER=$PGUSER" > "$FAKE_DIR/env"
case "$FAKE_MODE" in
  ok) echo 'CREATE TABLE public.nodes (id int);' ;;
  nonodes) echo 'CREATE TABLE public.users (id int);' ;;
  mismatch) echo 'pg_dump: error: aborting because of server version mismatch' >&2; exit 1 ;;
  fail) echo 'pg_dump: error: connection refused' >&2; exit 1 ;;
esac
"""


class PostgresTest(Base):
    PG = {"db_type": "postgres", "pg_host": "db.example", "pg_port": "5432", "pg_name": "headscale",
          "pg_user": "headscale", "pg_pass": "hunter2", "pg_sslmode": "disable", "backup_keep_days": "14"}

    def setUp(self):
        super().setUp()
        os.unlink(os.path.join(self.data, "headscale", "db.sqlite"))
        self.bin = os.path.join(self._tmp.name, "bin")
        os.makedirs(self.bin)
        exe = os.path.join(self.bin, "pg_dump")
        write(exe, FAKE_PG_DUMP)
        os.chmod(exe, 0o755)
        env = mock.patch.dict(os.environ, {"PATH": self.bin + os.pathsep + os.environ["PATH"],
                                           "FAKE_DIR": self._tmp.name, "FAKE_MODE": "ok"})
        env.start()
        self.addCleanup(env.stop)

    def run_pg(self, mode="ok"):
        os.environ["FAKE_MODE"] = mode
        return self.create(settings=dict(self.PG))

    def test_ok_and_password_not_in_argv(self):
        res = self.run_pg()
        self.assertTrue(res.ok, res.error)
        self.assertIn("headscale/headscale.sql", self.members(res.path))
        self.assertNotIn("headscale/db.sqlite", self.members(res.path))
        with open(os.path.join(self._tmp.name, "argv")) as fh:
            argv = fh.read()
        self.assertNotIn("hunter2", argv)
        self.assertIn("--clean", argv)
        with open(os.path.join(self._tmp.name, "env")) as fh:
            self.assertIn("PGPASSWORD=hunter2", fh.read())

    def test_failures(self):
        for mode, text in (("nonodes", "nodes table"), ("mismatch", "same or a newer major"),
                           ("fail", "connection refused")):
            res = self.run_pg(mode)
            self.assertFalse(res.ok, mode)
            self.assertIn(text, res.error)
        self.assertEqual([n for n in os.listdir(self.out) if n.endswith(".tar.gz")], [])

    def test_no_pg_dump_installed(self):
        os.unlink(os.path.join(self.bin, "pg_dump"))
        with mock.patch("aio.backup.shutil.which", return_value=None):
            res = self.create(settings=dict(self.PG))
        self.assertFalse(res.ok)
        self.assertIn("pg_dump is not installed", res.error)


if __name__ == "__main__":
    unittest.main()
