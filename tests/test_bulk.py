"""Bulk actions on the Machines page (expire keys, remove, add a tag to
several machines at once). Standard library only; no Headscale needed:

    python3 tests/test_bulk.py
"""
import os
import sys
import unittest

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
os.environ.update(HEADSCALE_API_KEY="x", PUBLIC_URL="https://vpn.example.com", SESSION_SECRET="s",
                  HEADSCALE_URL="http://headscale:8080")
sys.path.insert(0, WEB)

import app  # noqa: E402
import pages  # noqa: E402
from i18n import set_lang  # noqa: E402

SESSION_ADMIN = {"admin": True, "username": "root", "csrf": "tok"}
SESSION_MEMBER = {"admin": False, "username": "bob", "csrf": "tok"}
CTX = {"public_url": "https://vpn.example.com", "tailnet": "", "authentik": False, "server_host": "vpn.example.com"}


def machine(node_id, name, owner="alice"):
    node = {"id": node_id, "givenName": name, "name": name, "user": {"id": "1", "name": owner},
           "online": True, "ipAddresses": ["100.64.0.1"], "tags": []}
    return pages.Machine(node, None, {}, "", {})


class BulkTagsFormTests(unittest.TestCase):
    def setUp(self):
        set_lang("en")

    def test_valid_tags(self):
        tags, error = app.bulk_tags_from_form({"tags": "tag:server, tag:prod"})
        self.assertEqual((tags, error), (["tag:server", "tag:prod"], ""))

    def test_requires_at_least_one_tag(self):
        tags, error = app.bulk_tags_from_form({"tags": ""})
        self.assertEqual(tags, [])
        self.assertIn("tag", error)

    def test_rejects_invalid_tag(self):
        _, error = app.bulk_tags_from_form({"tags": "tag:Bad Name"})
        self.assertIn("tag:bad name", error)


class BulkFlashTests(unittest.TestCase):
    def setUp(self):
        set_lang("en")

    def test_singular_and_plural(self):
        self.assertIn("1 machine key expired.", pages.bulk_flash_html("bulk-expired-1"))
        self.assertIn("3 machine keys expired.", pages.bulk_flash_html("bulk-expired-3"))
        self.assertIn("1 machine removed.", pages.bulk_flash_html("bulk-removed-1"))
        self.assertIn("2 machines removed.", pages.bulk_flash_html("bulk-removed-2"))
        self.assertIn("Tag added to 1 machine.", pages.bulk_flash_html("bulk-tagged-1"))
        self.assertIn("Tag added to 5 machines.", pages.bulk_flash_html("bulk-tagged-5"))

    def test_unknown_code_is_blank(self):
        self.assertEqual(pages.bulk_flash_html("acl-saved"), "")
        self.assertEqual(pages.bulk_flash_html(""), "")
        self.assertEqual(pages.bulk_flash_html("bulk-expired-x"), "")


class RenderTests(unittest.TestCase):
    def setUp(self):
        set_lang("en")

    def test_admin_sees_bulk_ui(self):
        machines = [machine("1", "alice-a"), machine("2", "alice-b")]
        page = pages.machines_page(SESSION_ADMIN, CTX, machines, True, "")
        self.assertIn('data-bulk-all', page)
        self.assertIn('name="node-1"', page)
        self.assertIn('name="node-2"', page)
        self.assertIn('<dialog id="bulk-remove">', page)
        self.assertIn('<dialog id="bulk-tag">', page)
        self.assertIn(f'formaction="{pages.BASE}/machines/bulk/expire"', page)

    def test_member_has_no_bulk_ui(self):
        machines = [machine("1", "alice-a")]
        page = pages.machines_page(SESSION_MEMBER, CTX, machines, True, "")
        self.assertNotIn('data-bulk-all', page)
        self.assertNotIn('data-bulk-item', page)
        self.assertNotIn('id="bulk-remove"', page)
        self.assertNotIn('id="bulk-tag"', page)

    def test_bulk_checkboxes_and_dialogs_escape_names(self):
        machines = [machine("1", "<script>evil")]
        page = pages.machines_page(SESSION_ADMIN, CTX, machines, True, "")
        self.assertNotIn("<script>evil", page)


if __name__ == "__main__":
    unittest.main(verbosity=1)
