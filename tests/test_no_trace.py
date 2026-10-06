"""The tracked tree describes Headscale Easy 2.0 only.

No file, comment, string or document may refer to a previous version, an installer script, a helper
container, a migration from an older install, a "preview" or the plans the work was organised in. A word
that means something else (Tailscale's own install script, database schema migrations, the PostgreSQL
legacy MD5 method...) is listed in ALLOWED with the reason. What git does not track is not checked: a
developer's own leftovers in their checkout are theirs.

    python3 -m unittest tests.test_no_trace
"""
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

FORBIDDEN = re.compile(
    r"1\.x|legacy|split stack|hs-helper|helper container|migrat|headscale-easy-aio|preview|unreleased"
    r"|install\.sh|installer|instalador|installateur|uninstall|phase [0-9]|fase [0-9]|simplification|authentik api",
    re.I,
)

# (path regex, line regex, why the word means something else)
ALLOWED = [
    (r"^tests/test_no_trace\.py$", r".", "this file lists the forbidden terms"),
    (r"^(CHANGELOG\.md|scripts/release_info\.py|tests/test_release\.py)$", r"(?i)unreleased",
     "an entry that is not dated yet blocks a release tag: that is how the release check works"),
    (r"^(docs/getting-started(\.es)?\.md|web/pages\.py)$", r"tailscale\.com/install\.sh",
     "Tailscale's own client install script"),
    (r"^(web/local_accounts\.py|tests/test_local_accounts\.py)$", r"(?i)migrat", "database schema migrations"),
    (r"^docs/advanced/postgres(\.es)?\.md$", r"(?i)migrates its tables", "Headscale creates its own tables"),
    (r"^(web/pgwire\.py|docs/configuration(\.es)?\.md|tests/test_postgresql\.py)$", r"(?i)legacy",
     "PostgreSQL's legacy MD5 password method, refused"),
    (r"^(CONTRIBUTING\.md|mkdocs\.yml)$", r"(?i)preview locally", "mkdocs serve shows a local preview of the docs"),
    (r"^web/notify\.py$", r"disable_web_page_preview", "a parameter of Telegram's API"),
    (r"^docs/operations(\.es)?\.md$", r"(?i)uninstalling|desinstalar", "how to remove the app and its data"),
]

REMOVED = [
    "helper", "legacy", "deploy", "authentik", "install.sh", "uninstall.sh", "docker-compose.yml",
    "docker-compose.override.yml", "SIMPLIFICATION_PLAN.md", "PHASE1_EXECUTION_PLAN.md",
    "PHASE2_EXECUTION_PLAN.md", "PHASE2_5_EXECUTION_PLAN.md", "PHASE3_EXECUTION_PLAN.md",
    "PHASE4_EXECUTION_PLAN.md", "PHASE6_EXECUTION_PLAN.md", "scripts/embed-compose.sh",
    "scripts/restore.sh", "scripts/dev-local-accounts.sh", "web/Dockerfile", "web/accounts.py", "web/mfa.py",
    "docs/development/local-accounts-testing.md",
]


def tracked():
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in out.stdout.decode("utf-8").split("\0") if p]


def allowed(path, line):
    return any(re.search(p, path) and re.search(l, line) for p, l, _why in ALLOWED)


class NoTraceTest(unittest.TestCase):
    def setUp(self):
        self.files = tracked()
        if self.files is None:
            self.skipTest("git is not available")

    def test_no_forbidden_term_in_tracked_text(self):
        found = []
        for rel in self.files:
            try:
                with open(os.path.join(ROOT, rel), "rb") as fh:
                    raw = fh.read()
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue  # binary or gone: fonts, images
            if "\0" in text:
                continue
            for n, line in enumerate(text.split("\n"), 1):
                if FORBIDDEN.search(line) and not allowed(rel, line):
                    found.append("%s:%d: %s" % (rel, n, line.strip()[:110]))
        self.assertEqual(found, [], "traces of a previous version:\n" + "\n".join(found[:40]))

    def test_every_allowance_has_a_reason_and_is_used(self):
        used = set()
        for rel in self.files:
            try:
                with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            for line in text.split("\n"):
                if FORBIDDEN.search(line):
                    for i, (p, l, _why) in enumerate(ALLOWED):
                        if re.search(p, rel) and re.search(l, line):
                            used.add(i)
        for i, (p, l, why) in enumerate(ALLOWED):
            self.assertTrue(why.strip(), p)
            if i > 1:
                self.assertIn(i, used, "an allowance nothing needs any more: %s %s" % (p, l))

    def test_removed_paths_stay_removed(self):
        for rel in REMOVED:
            with self.subTest(path=rel):
                hits = [f for f in self.files if f == rel or f.startswith(rel.rstrip("/") + "/")]
                self.assertEqual(hits, [], "%s was removed and must not come back" % rel)


if __name__ == "__main__":
    unittest.main()
