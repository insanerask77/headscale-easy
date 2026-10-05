"""scripts/restore.sh refuses archives of the all-in-one image and points to `hse restore`.

The guard runs right after the archive is unpacked, before anything touches Docker or
the project directory, so these tests need neither. Run in CI with the rest:
python3 -m unittest discover -s tests
"""
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESTORE = os.path.join(ROOT, "scripts", "restore.sh")


def make_archive(path, files):
    """A .tar.gz with one top-level directory, like backup.sh and aio/backup.py write."""
    with tarfile.open(path, "w:gz") as tar:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo("headscale-easy-20260101-030000/" + name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


@unittest.skipUnless(shutil.which("bash") and shutil.which("tar"), "needs bash and tar")
class RestoreGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(self.project)

    def run_restore(self, files):
        archive = os.path.join(self.tmp, "backup.tar.gz")
        make_archive(archive, files)
        env = dict(os.environ, HSE_PROJECT_DIR=self.project, HSE_USE_COMPOSE="0")
        return subprocess.run(["bash", RESTORE, archive, "--yes"], env=env, capture_output=True, text=True, timeout=60)

    def test_aio_archive_is_refused_and_nothing_is_written(self):
        meta = json.dumps({"format": 2, "edition": "aio"})
        got = self.run_restore({"meta.json": meta, "headscale/db.sqlite": "x", "config/settings.json": "{}"})
        self.assertNotEqual(got.returncode, 0)
        self.assertIn("hse restore", got.stderr)
        self.assertEqual(os.listdir(self.project), [])

    def test_aio_archive_with_compact_json_is_refused(self):
        got = self.run_restore({"meta.json": '{"edition":"aio"}', "headscale/db.sqlite": "x"})
        self.assertNotEqual(got.returncode, 0)
        self.assertIn("hse restore", got.stderr)

    def test_one_x_archive_is_not_stopped_by_the_guard(self):
        # no meta.json (as backup.sh writes): goes on to the usual checks, here "not a backup"
        got = self.run_restore({"config/.env": "A=1\n"})
        self.assertNotEqual(got.returncode, 0)
        self.assertNotIn("hse restore", got.stderr)

    def test_other_edition_in_meta_is_not_stopped_by_the_guard(self):
        got = self.run_restore({"meta.json": json.dumps({"edition": "compose"}), "config/.env": "A=1\n"})
        self.assertNotIn("hse restore", got.stderr)


if __name__ == "__main__":
    unittest.main()
