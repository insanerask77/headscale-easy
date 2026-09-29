"""ACL policy editor tests: HuJSON parsing, the splice that preserves
comments and untouched sections, the access simulator, form -> rule/group
conversion, and rendering. Standard library only; no Headscale needed:

    python3 tests/test_acl.py
"""
import os
import sys
import unittest

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
os.environ.update(HEADSCALE_API_KEY="x", PUBLIC_URL="https://vpn.example.com", SESSION_SECRET="s",
                  HEADSCALE_URL="http://headscale:8080")
sys.path.insert(0, WEB)

import acl_pages  # noqa: E402
import admin_pages  # noqa: E402
import app  # noqa: E402
import policy  # noqa: E402
from i18n import set_lang  # noqa: E402

SESSION_ADMIN = {"admin": True, "username": "root", "csrf": "tok"}
CTX = {"public_url": "https://vpn.example.com", "tailnet": "", "authentik": False, "server_host": "vpn.example.com"}

ISOLATION = """{
  // Each user can only reach their own machines
  "acls": [
    {"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:self:*"]}
  ]
}"""

WITH_SSH = """{
  // do not touch this comment
  "groups": {
    "group:staff": ["alice@", "bob@"]
  },
  "acls": [
    {"action": "accept", "src": ["group:staff"], "dst": ["tag:nas:445"]}
  ],
  "ssh": [
    {"action": "accept", "src": ["group:staff"], "dst": ["tag:nas"], "users": ["root"]}
  ]
}"""

NODES = [
    {"id": 1, "givenName": "alice-laptop", "user": {"name": "alice"}, "tags": [], "ipAddresses": ["100.64.0.1"]},
    {"id": 2, "givenName": "nas", "user": {"name": "bob"}, "tags": ["tag:nas"], "ipAddresses": ["100.64.0.5"]},
    {"id": 3, "givenName": "carol-laptop", "user": {"name": "carol"}, "tags": [], "ipAddresses": ["100.64.0.9"]},
]
USERS = [{"name": "alice"}, {"name": "bob"}, {"name": "carol"}]

POLICY_WITH_GROUPS = {
    "groups": {"group:staff": ["alice@", "bob@"]},
    "tagOwners": {"tag:nas": ["group:staff"]},
    "acls": [
        {"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:self:*"]},
        {"action": "accept", "src": ["group:staff"], "dst": ["tag:nas:445"]},
    ],
}


class ParseTests(unittest.TestCase):
    def test_line_and_block_comments(self):
        self.assertEqual(policy.parse('{ "a": 1 /* x */, "b": 2 // y\n}'), {"a": 1, "b": 2})

    def test_trailing_commas(self):
        self.assertEqual(policy.parse('{"a": [1, 2,], "b": {"c": 3,},}'), {"a": [1, 2], "b": {"c": 3}})

    def test_double_slash_inside_a_string_is_not_a_comment(self):
        self.assertEqual(policy.parse('{"a": "https://example.com"}'), {"a": "https://example.com"})

    def test_blank_policy_is_empty(self):
        self.assertEqual(policy.parse(""), {})
        self.assertEqual(policy.parse("   \n  "), {})

    def test_invalid_hujson_raises(self):
        with self.assertRaises(policy.PolicyError):
            policy.parse("{ not json")

    def test_non_object_raises(self):
        with self.assertRaises(policy.PolicyError):
            policy.parse("[1, 2, 3]")


class SpliceTests(unittest.TestCase):
    def test_editing_acls_preserves_comments_and_other_sections(self):
        new_acls = policy.render_acls([{"action": "accept", "src": ["group:staff"], "dst": ["tag:nas:22"]}])
        result = policy.replace_block(WITH_SSH, "acls", new_acls)
        self.assertIn("// do not touch this comment", result)
        self.assertIn('"ssh": [', result)
        self.assertIn('"users": ["root"]', result)
        self.assertNotIn("tag:nas:445", result)  # the old rule is gone
        self.assertIn("tag:nas:22", result)

    def test_round_trip(self):
        parsed = policy.parse(WITH_SSH)
        new_acls = policy.render_acls(parsed["acls"])
        result = policy.replace_block(WITH_SSH, "acls", new_acls)
        self.assertEqual(policy.parse(result), parsed)

    def test_insert_a_key_that_does_not_exist_yet(self):
        result = policy.replace_block(ISOLATION, "groups", policy.render_groups({"group:staff": ["alice@"]}))
        self.assertEqual(policy.parse(result)["groups"], {"group:staff": ["alice@"]})
        self.assertEqual(policy.parse(result)["acls"], policy.parse(ISOLATION)["acls"])
        self.assertIn("// Each user can only reach their own machines", result)

    def test_from_a_blank_policy(self):
        result = policy.replace_block("", "acls", policy.render_acls([{"action": "accept", "src": ["*"], "dst": ["*:*"]}]))
        self.assertEqual(policy.parse(result), {"acls": [{"action": "accept", "src": ["*"], "dst": ["*:*"]}]})

    def test_invalid_policy_refuses_to_splice(self):
        with self.assertRaises(policy.PolicyError):
            policy.replace_block("{ not json", "acls", "[]")


class RuleViewTests(unittest.TestCase):
    def test_single_port_is_editable(self):
        view = policy.rule_view({"action": "accept", "src": ["group:staff"], "dst": ["tag:nas:445", "tag:db:445"]})
        self.assertTrue(view["editable"])
        self.assertEqual(view["port"], "445")
        self.assertEqual(view["dst"], ["tag:nas", "tag:db"])

    def test_mixed_ports_are_not_editable(self):
        view = policy.rule_view({"action": "accept", "src": ["*"], "dst": ["tag:a:80", "tag:b:443"]})
        self.assertFalse(view["editable"])

    def test_no_port_suffix_defaults_to_star(self):
        view = policy.rule_view({"action": "accept", "src": ["*"], "dst": ["tag:a"]})
        self.assertEqual(view["port"], "*")
        self.assertTrue(view["editable"])


class EvaluateTests(unittest.TestCase):
    def test_empty_policy_allows_everything(self):
        v = policy.evaluate({}, NODES, USERS, "carol@", "nas", "9999", None)
        self.assertTrue(v.allowed)
        self.assertIsNone(v.rule)

    def test_autogroup_member_and_self(self):
        v = policy.evaluate(POLICY_WITH_GROUPS, NODES, USERS, "alice-laptop", "alice-laptop", None, None)
        self.assertTrue(v.allowed)
        self.assertEqual(v.rule_index, 0)

    def test_autogroup_self_denies_a_different_owner(self):
        v = policy.evaluate(POLICY_WITH_GROUPS, NODES, USERS, "carol-laptop", "alice-laptop", None, None)
        self.assertFalse(v.allowed)

    def test_tagged_machine_is_not_autogroup_member(self):
        # "nas" carries tag:nas, so it is excluded from autogroup:member even
        # though its owner (bob) would otherwise match -- isolate this from
        # the group:staff rule, which matches by owner regardless of tags.
        only_isolation = {"acls": [{"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:self:*"]}]}
        v = policy.evaluate(only_isolation, NODES, USERS, "nas", "nas", None, None)
        self.assertFalse(v.allowed)

    def test_group_expansion_and_tag_and_port(self):
        v = policy.evaluate(POLICY_WITH_GROUPS, NODES, USERS, "alice@", "nas", "445", None)
        self.assertTrue(v.allowed)
        self.assertEqual(v.rule_index, 1)

    def test_group_expansion_wrong_port_denies(self):
        v = policy.evaluate(POLICY_WITH_GROUPS, NODES, USERS, "alice@", "nas", "80", None)
        self.assertFalse(v.allowed)

    def test_non_member_denies(self):
        v = policy.evaluate(POLICY_WITH_GROUPS, NODES, USERS, "carol@", "nas", "445", None)
        self.assertFalse(v.allowed)

    def test_port_ranges_and_lists(self):
        pol = {"acls": [{"action": "accept", "src": ["*"], "dst": ["*:1000-2000,3000"]}]}
        for port, ok in [("1500", True), ("3000", True), ("2500", False), ("999", False)]:
            v = policy.evaluate(pol, NODES, USERS, "carol@", "nas", port, None)
            self.assertEqual(v.allowed, ok, port)

    def test_no_matching_rule_denies_by_default(self):
        pol = {"acls": [{"action": "accept", "src": ["tag:admin"], "dst": ["*:*"]}]}
        v = policy.evaluate(pol, NODES, USERS, "carol@", "nas", None, None)
        self.assertFalse(v.allowed)


class FormTests(unittest.TestCase):
    def setUp(self):
        set_lang("en")

    def test_rule_from_form(self):
        rule, error = app.acl_rule_from_form({"src": "alice@, tag:admin", "dst": "tag:nas", "port": "445", "proto": "tcp"})
        self.assertEqual(error, "")
        self.assertEqual(rule, {"action": "accept", "src": ["alice@", "tag:admin"], "dst": ["tag:nas:445"], "proto": "tcp"})

    def test_rule_defaults_port_to_star(self):
        rule, error = app.acl_rule_from_form({"src": "*", "dst": "tag:nas"})
        self.assertEqual(error, "")
        self.assertEqual(rule["dst"], ["tag:nas:*"])
        self.assertNotIn("proto", rule)

    def test_rule_requires_source_and_destination(self):
        _, error = app.acl_rule_from_form({"src": "", "dst": "tag:nas"})
        self.assertIn("source", error)
        _, error = app.acl_rule_from_form({"src": "alice@", "dst": ""})
        self.assertIn("destination", error)

    def test_rule_rejects_bad_port_and_tag(self):
        _, error = app.acl_rule_from_form({"src": "alice@", "dst": "tag:nas", "port": "not-a-port"})
        self.assertIn("port", error)
        _, error = app.acl_rule_from_form({"src": "tag:Bad Name", "dst": "tag:nas"})
        self.assertIn("tag", error)

    def test_group_from_form_adds_prefix(self):
        name, members, error = app.acl_group_from_form({"name": "staff", "members": "alice@, bob@"})
        self.assertEqual((name, members, error), ("group:staff", ["alice@", "bob@"], ""))

    def test_group_from_form_rejects_empty_members(self):
        _, _, error = app.acl_group_from_form({"name": "staff", "members": ""})
        self.assertIn("member", error)

    def test_tag_owner_from_form(self):
        name, owners, error = app.acl_tag_owner_from_form({"name": "tag:nas", "owners": "group:staff"})
        self.assertEqual((name, owners, error), ("tag:nas", ["group:staff"], ""))

    def test_tag_owner_requires_prefix(self):
        _, _, error = app.acl_tag_owner_from_form({"name": "nas", "owners": "alice@"})
        self.assertIn("tag:", error)


class RenderTests(unittest.TestCase):
    def setUp(self):
        set_lang("en")

    def test_rules_panel_escapes_and_marks_uneditable(self):
        pol = {"acls": [{"action": "accept", "src": ["<script>x"], "dst": ["tag:a:80", "tag:b:443"]}]}
        panel = acl_pages.rules_panel(SESSION_ADMIN, pol)
        self.assertNotIn("<script>x", panel)
        self.assertIn("&lt;script&gt;x", panel)
        self.assertIn("Edit in Advanced (HuJSON)", panel)

    def test_groups_panel_renders_tables(self):
        panel = acl_pages.groups_panel(SESSION_ADMIN, POLICY_WITH_GROUPS)
        self.assertIn("group:staff", panel)
        self.assertIn("tag:nas", panel)
        self.assertIn('data-open="acl-group-new"', panel)
        self.assertIn('data-open="acl-tag-new"', panel)

    def test_acl_page_renders_every_tab(self):
        policy_data = {"policy": ISOLATION, "updatedAt": ""}
        for active in ("rules", "groups", "test", "raw"):
            page = admin_pages.acl_page(SESSION_ADMIN, CTX, policy_data, NODES, USERS, "", active=active)
            self.assertNotIn("<script>", page)
            self.assertIn('id="acl-targets"', page)
            self.assertIn(f'data-panel="{active}"', page)

    def test_broken_policy_shows_a_warning_and_no_forms_to_lose_data(self):
        page = admin_pages.acl_page(SESSION_ADMIN, CTX, {"policy": "{ not json", "updatedAt": ""}, NODES, USERS, "")
        self.assertIn("could not be read as HuJSON", page)
        self.assertNotIn('data-open="acl-rule-new"', page)

    def test_allowed_and_denied_render(self):
        allowed = policy.evaluate(POLICY_WITH_GROUPS, NODES, USERS, "alice@", "nas", "445", None)
        denied = policy.evaluate(POLICY_WITH_GROUPS, NODES, USERS, "carol@", "nas", "445", None)
        self.assertIn("Allowed", acl_pages.test_panel(SESSION_ADMIN, ("alice@", "nas", "445", "", allowed)))
        self.assertIn("Denied", acl_pages.test_panel(SESSION_ADMIN, ("carol@", "nas", "445", "", denied)))


if __name__ == "__main__":
    unittest.main(verbosity=1)
