"""Release plumbing: the version agrees everywhere, a tag publishes the right image tags, and the workflows
guard a release (scripts/release_info.py, .github/workflows/docker.yml, release.yml, docker-dev.yml).

    python3 -m unittest tests.test_release
"""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import release_info  # noqa: E402


def workflow(name):
    with open(os.path.join(ROOT, ".github", "workflows", name), encoding="utf-8") as fh:
        return fh.read()


class VersionAgreesTest(unittest.TestCase):
    def test_everything_agrees_with_the_version_file(self):
        self.assertEqual(release_info.check(), [])

    def test_the_command_line_check_passes(self):
        out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "release_info.py"), "check"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_a_release_tag_must_match_the_version(self):
        wrong = "v" + release_info.version_file() + "1"
        self.assertTrue(any("does not match VERSION" in p for p in release_info.check(wrong)))

    def test_a_prerelease_of_the_version_is_accepted(self):
        version = release_info.version_file()
        for tag in ("v%s-rc.1" % version, "v%s-beta.2" % version):
            self.assertFalse(any("does not match" in p or "valid" in p for p in release_info.check(tag)), tag)
        for bad in ("v%s-" % version, "v%s-rc 1" % version, "v9.9.9-rc.1"):
            self.assertTrue(any("match" in p or "valid" in p for p in release_info.check(bad)), bad)

    def test_a_prerelease_uses_the_notes_of_its_release(self):
        out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "release_info.py"), "notes",
                              "v%s-rc.1" % release_info.version_file()], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.strip())

    def test_an_unreleased_changelog_blocks_a_tag_but_not_a_branch(self):
        version = release_info.version_file()
        entries = release_info.changelog_entries()
        problems = release_info.check("v" + version)
        self.assertEqual(any("Unreleased" in p for p in problems), "unreleased" in entries[0][1].lower())
        self.assertFalse(any("Unreleased" in p for p in release_info.check()))

    def test_changelog_notes_stop_at_the_next_version(self):
        text = "## [2.0.1] - 2026-01-02\n\nfix\n\n## [2.0.0] - 2026-01-01\n\nfirst\n\n[2.0.1]: https://x\n"
        self.assertEqual(release_info.changelog_notes("2.0.1", text), "fix")
        self.assertEqual(release_info.changelog_notes("2.0.0", text), "first")
        self.assertEqual(release_info.changelog_notes("9.9.9", text), "")


class TagsTest(unittest.TestCase):
    def test_a_release_publishes_the_moving_tags(self):
        self.assertEqual(release_info.tags_for("v2.0.0"), ["2.0.0", "2.0", "2", "latest"])
        self.assertEqual(release_info.tags_for("v2.3.4"), ["2.3.4", "2.3", "2", "latest"])

    def test_a_prerelease_publishes_only_its_own_tag(self):
        self.assertEqual(release_info.tags_for("v2.1.0-rc.1"), ["2.1.0-rc.1"])

    def test_not_a_version_is_refused(self):
        for bad in ("main", "v2", "v2.0", "release-2.0.0"):
            with self.subTest(ref=bad):
                with self.assertRaises(ValueError):
                    release_info.tags_for(bad)

    def test_the_workflow_asks_for_the_same_tags(self):
        text = workflow("docker.yml")
        for pattern in ("{{version}}", "{{major}}.{{minor}}", "{{major}}"):
            self.assertIn("type=semver,pattern=%s" % pattern, text)
        self.assertIn("type=raw,value=latest", text)
        self.assertIn("latest=false", text)  # latest is explicit, and never on a pre-release
        self.assertIn("!contains(github.ref_name, '-')", text)


class WorkflowsTest(unittest.TestCase):
    def test_no_alias_image_is_published(self):
        for name in ("docker.yml", "docker-dev.yml", "release.yml", "ci.yml"):
            with self.subTest(workflow=name):
                self.assertNotIn("alias", workflow(name).lower())

    def test_a_release_checks_the_version_before_publishing(self):
        for name in ("docker.yml", "release.yml"):
            with self.subTest(workflow=name):
                self.assertIn("scripts/release_info.py check --tag", workflow(name))

    def test_the_images_are_published_with_the_same_tags(self):
        text = workflow("docker.yml")
        self.assertIn("image: headscale-easy\n", text)
        self.assertIn("image: headscale-easy-backup\n", text)

    def test_a_published_release_is_smoke_tested_only_on_tags(self):
        text = workflow("docker.yml")
        job = text[text.index("  smoke-published:"):]
        self.assertIn("startsWith(github.ref, 'refs/tags/v')", job)
        self.assertIn("needs: publish", job)
        self.assertIn("scripts/compose-smoke.sh", job)
        self.assertIn("docker pull", job)

    def test_release_notes_come_from_the_changelog(self):
        text = workflow("release.yml")
        self.assertIn("scripts/release_info.py notes", text)
        self.assertIn("body_path: notes.md", text)

    def test_branch_builds_are_not_for_the_compose_file(self):
        text = workflow("docker-dev.yml")
        self.assertIn("type=raw,value=next", text)
        self.assertIn("not for compose.yaml", text)
        self.assertNotIn("type=semver", text)  # no tag ever reaches this workflow

    def test_every_workflow_is_yaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        folder = os.path.join(ROOT, ".github", "workflows")
        for name in sorted(os.listdir(folder)):
            with self.subTest(workflow=name):
                with open(os.path.join(folder, name), encoding="utf-8") as fh:
                    self.assertIsInstance(yaml.safe_load(fh), dict)


if __name__ == "__main__":
    unittest.main()
