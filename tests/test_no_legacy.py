"""Pieces of the 1.x split stack must not come back (phase 6).

2.0 is the all-in-one image: no helper container, no root compose file, no 1.x installer or
backup tools. A merge that brings one back fails here, and no compose file may mount the Docker
socket.

    python3 -m unittest tests.test_no_legacy
"""
import glob
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REMOVED = [
    "helper",
    "legacy",
    "docker-compose.yml",
    "docker-compose.override.yml",
    ".env.example",
    "web/Dockerfile",
    "scripts/utils.sh",
    "scripts/restore.sh",
    "scripts/gen_render_goldens.sh",
    "scripts/dev-local-accounts.sh",
    "backup/backup.sh",
    "backup/pg-client.sh",
    "templates/front-caddy.tmpl",
    "templates/front-nginx.conf.tmpl",
    "templates/front-npm.md.tmpl",
    "templates/front-traefik.yml.tmpl",
]


def _tracked(rel):
    """Files git tracks under rel; None when git is not available (then the disk decides)."""
    try:
        out = subprocess.run(["git", "ls-files", "--", rel], cwd=ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.split()


class NoLegacyTest(unittest.TestCase):
    def test_removed_paths_stay_removed(self):
        # What counts is what is committed: a generated file a developer left in their own
        # checkout (git-ignored, e.g. docker-compose.override.yml from a 1.x install) is not a come-back.
        for rel in REMOVED:
            path = os.path.join(ROOT, rel)
            found = _tracked(rel)
            if found is not None:
                pass
            elif os.path.isdir(path):  # a directory of stale bytecode is not a come-back
                found = [f for _, _, names in os.walk(path) for f in names if not f.endswith(".pyc")]
            else:
                found = [rel] if os.path.exists(path) else []
            with self.subTest(path=rel):
                self.assertEqual(found, [], "%s is a 1.x piece: it was removed" % rel)

    def test_no_compose_file_mounts_the_docker_socket(self):
        files = [f for f in glob.glob(os.path.join(ROOT, "**", "docker-compose*.yml"), recursive=True)
                 if "node_modules" not in os.path.relpath(f, ROOT).split(os.sep)]
        self.assertTrue(files, "the reference compose file is missing")
        for path in files:
            with open(path, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    if "docker.sock" in line and not line.lstrip().startswith("#"):
                        self.fail("%s:%d mounts the Docker socket" % (os.path.relpath(path, ROOT), n))

    def test_the_installer_embeds_the_reference_compose_file_only(self):
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("docker.sock", text)
        self.assertIsNone(re.search(r"legacy/|install-1x", text))

    def test_ci_builds_only_what_2_0_ships(self):
        for rel in (".github/workflows/ci.yml", ".github/workflows/docker.yml", ".github/workflows/release.yml"):
            path = os.path.join(ROOT, rel)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            with self.subTest(workflow=rel):
                self.assertNotIn("context: helper", text)
                self.assertNotIn("context: web", text)
                self.assertNotRegex(text, r"docker build[^\n]* (helper|web)\s*$")


if __name__ == "__main__":
    unittest.main()
