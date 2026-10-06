"""One version, one place: the VERSION file. The code, the footer and the image must agree with it:

    python3 tests/test_version.py
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "web"))

import version  # noqa: E402


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class VersionTest(unittest.TestCase):
    def test_the_file_is_a_release_version(self):
        value = read("VERSION").strip()
        self.assertRegex(value, r"\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?")
        self.assertEqual(read("VERSION"), value + "\n")  # one line, one newline

    def test_the_console_reads_the_file(self):
        self.assertEqual(version.VERSION, read("VERSION").strip())

    def test_the_image_default_and_label_follow_it(self):
        dockerfile = read("aio", "Dockerfile")
        self.assertEqual(re.findall(r"^ARG HSE_VERSION=(\S+)$", dockerfile, re.M), [read("VERSION").strip()])
        self.assertIn('org.opencontainers.image.version="${HSE_VERSION}"', dockerfile)
        self.assertRegex(dockerfile, r"(?m)^COPY VERSION /app/VERSION$")
        self.assertNotIn("VERSION", read(".dockerignore"))

    def test_compose_and_changelog_follow_it(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import release_info
        self.assertEqual(release_info.compose_default(), read("VERSION").strip())
        self.assertEqual(release_info.changelog_entries()[0][0], read("VERSION").strip())
        self.assertEqual(release_info.check(), [])

    def test_a_missing_file_does_not_stop_the_console(self):
        from unittest import mock
        with mock.patch("builtins.open", side_effect=OSError):
            self.assertEqual(version._read_version(), "unknown")

    def test_the_footer_and_the_status_page_use_it(self):
        self.assertIn("Headscale Easy v{esc(VERSION)", read("web", "ui.py"))
        self.assertIn('"easy": {"version": VERSION', read("web", "status.py"))


if __name__ == "__main__":
    unittest.main()
