"""Tests for the remote copy of backups (backup/remote.sh).

The remote is a local directory and rclone/ssh/rsync are small fakes put first
in PATH, so nothing needs the network or Docker. Run in CI with the rest:
python3 -m unittest discover -s tests
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REMOTE_SH = Path(__file__).resolve().parent.parent / "backup" / "remote.sh"

FAKE_RCLONE = r"""#!/bin/sh
# copyto SRC DST | delete DIR --include PAT --min-age Nd -v   (local paths only)
echo "rclone $*" >> "$FAKE_LOG"
case "$1" in
    copyto) mkdir -p "$(dirname "$3")" && cp "$2" "$3" ;;
    delete)
        dir="$2"; pat="$4"; days="${6%d}"
        find "$dir" -maxdepth 1 -name "$pat" -mtime +"$days" | while read -r f; do
            rm -f "$f"; echo "2026/01/01 00:00:00 INFO  : $(basename "$f"): Deleted"
        done ;;
esac
"""

FAKE_RSYNC = r"""#!/bin/sh
echo "rsync $*" >> "$FAKE_LOG"
"""


class RemoteCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.dest = self.tmp / "dest"
        self.dest.mkdir()
        self.log = self.tmp / "calls.log"
        for name, body in (("rclone", FAKE_RCLONE), ("rsync", FAKE_RSYNC)):
            f = self.bin / name
            f.write_text(body)
            f.chmod(0o755)
        self.archive = self.tmp / "headscale-easy-20260101-030000.tar.gz"
        self.archive.write_bytes(b"data")

    def run_remote(self, *args, **env):
        e = {"PATH": f"{self.bin}:{os.environ['PATH']}", "FAKE_LOG": str(self.log),
             "BACKUP_REMOTE_CONFIG_DIR": str(self.tmp / "conf")}
        e.update(env)
        return subprocess.run(["sh", str(REMOTE_SH), *args], env=e, capture_output=True, text=True)

    def calls(self):
        return self.log.read_text() if self.log.exists() else ""

    def test_push_uploads_the_archive(self):
        r = self.run_remote("push", str(self.archive), BACKUP_REMOTE=str(self.dest))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.dest / self.archive.name).read_bytes(), b"data")

    def test_prune_removes_only_old_backups(self):
        old = self.dest / "headscale-easy-20250101-030000.tar.gz"
        new = self.dest / "headscale-easy-20260101-030000.tar.gz"
        other = self.dest / "notes.txt"
        for f in (old, new, other):
            f.write_text("x")
        os.utime(old, (1_000_000_000, 1_000_000_000))
        os.utime(other, (1_000_000_000, 1_000_000_000))
        r = self.run_remote("prune", BACKUP_REMOTE=str(self.dest), BACKUP_REMOTE_KEEP_DAYS="7")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertTrue(other.exists())
        self.assertIn("removed old remote backup: " + old.name, r.stdout)

    def test_fetch_downloads_a_remote_backup(self):
        (self.dest / self.archive.name).write_bytes(b"remote")
        out = self.tmp / "out"
        out.mkdir()
        r = self.run_remote("fetch", str(self.dest / self.archive.name), str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((out / self.archive.name).read_bytes(), b"remote")

    def test_rsync_destination_uses_rsync(self):
        r = self.run_remote("push", str(self.archive), BACKUP_REMOTE="rsync:me@host:/srv/backups")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("me@host:/srv/backups/", self.calls())
        self.assertNotIn("rclone", self.calls())

    def test_rsync_directory_is_validated(self):
        for bad in ("rsync:me@host:relative", "rsync:me@host:/a;rm -rf /", "rsync:me@host:/a b"):
            r = self.run_remote("push", str(self.archive), BACKUP_REMOTE=bad)
            self.assertNotEqual(r.returncode, 0, bad)
        self.assertNotIn("rsync ", self.calls())

    def test_refuses_without_remote_or_with_bad_retention(self):
        self.assertNotEqual(self.run_remote("push", str(self.archive)).returncode, 0)
        r = self.run_remote("prune", BACKUP_REMOTE=str(self.dest), BACKUP_REMOTE_KEEP_DAYS="7; rm -rf /")
        self.assertNotEqual(r.returncode, 0)

    def test_unknown_action(self):
        self.assertNotEqual(self.run_remote("explode", BACKUP_REMOTE=str(self.dest)).returncode, 0)


if __name__ == "__main__":
    unittest.main()
