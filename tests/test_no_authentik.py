"""The console does not know Authentik (phase 6, block 2).

Authentik is one external OIDC provider like Keycloak or Pocket ID. The console signs people in
through it and nothing else: no API client, no service-account token, no Authentik-only pages.
What stays, on purpose:

- aio/render.py: the optional Caddy route that serves an external Authentik under /authentik/
  (HSE_AUTHENTIK_UPSTREAM) and the "emails are not verified" detail of Headscale's OIDC config;
- two lines of prose in web/app.py and web/local_accounts.py;
- deploy/examples/authentik/ and its documentation.

    python3 -m unittest tests.test_no_authentik
"""
import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

# (file, text the line must contain): every other mention in web/ is a failure
PROSE = (
    ("web/app.py", "OIDC (any provider: Authentik, Keycloak, Pocket ID, Google...)"),
    ("web/local_accounts.py", "Replaces the bundled Authentik"),
)
GONE = ("web/accounts.py", "web/mfa.py", "authentik")
GONE_NAMES = ("AUTHENTIK_URL", "AUTHENTIK_API_TOKEN", "PORTAL_AUTHENTIK_TOKEN", "MFA_MODE_FILE")


def lines_with_authentik(pattern):
    for path in sorted(glob.glob(os.path.join(ROOT, pattern))):
        with open(path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if re.search("authentik", line, re.I):
                    yield os.path.relpath(path, ROOT), n, line.strip()


class NoAuthentik(unittest.TestCase):
    def test_the_authentik_modules_and_directory_are_gone(self):
        for rel in GONE:
            self.assertFalse(os.path.exists(os.path.join(ROOT, rel)), "%s was removed" % rel)

    def test_web_only_mentions_authentik_in_two_lines_of_prose(self):
        for rel, n, line in lines_with_authentik("web/*.py"):
            self.assertTrue(any(rel == f and text in line for f, text in PROSE), "%s:%d: %s" % (rel, n, line))

    def test_web_locales_have_no_authentik_strings(self):
        for pattern in ("web/locales/*.json", "web/locales/*.d/*.json"):
            for rel, n, line in lines_with_authentik(pattern):
                self.fail("%s:%d: %s" % (rel, n, line))

    def test_aio_only_renders_the_route_and_the_provider_details(self):
        for rel, n, line in lines_with_authentik("aio/*.py"):
            self.assertEqual(rel, "aio/render.py", "%s:%d: %s" % (rel, n, line))

    def test_the_removed_settings_are_nowhere_in_the_code_or_the_examples(self):
        paths = glob.glob(os.path.join(ROOT, "web", "*.py")) + glob.glob(os.path.join(ROOT, "aio", "*.py")) \
            + glob.glob(os.path.join(ROOT, "deploy", "**", "*"), recursive=True)
        for path in paths:
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for name in GONE_NAMES:
                self.assertNotIn(name, text, "%s mentions %s" % (os.path.relpath(path, ROOT), name))

    def test_the_users_page_has_no_authentik_context(self):
        with open(os.path.join(ROOT, "web", "app.py"), encoding="utf-8") as fh:
            self.assertNotIn('"authentik"', fh.read())


if __name__ == "__main__":
    unittest.main()
