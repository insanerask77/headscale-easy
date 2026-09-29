"""DNS page tests: form -> config.yaml round trips on a temp file, and renders
for admins and members. Standard library only; no Headscale needed:

    python3 tests/test_dns.py
"""
import html.parser
import io
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
TMP = tempfile.mkdtemp(prefix="t-dns-")
CONFIG = os.path.join(TMP, "config.yaml")
os.environ.update(HEADSCALE_API_KEY="x", PUBLIC_URL="https://vpn.example.com", SESSION_SECRET="s",
                  HEADSCALE_CONFIG=CONFIG, HEADSCALE_URL="http://headscale:8080")
sys.path.insert(0, WEB)

import app  # noqa: E402
import headscale as hs  # noqa: E402
import pages  # noqa: E402
from i18n import set_lang  # noqa: E402

SESSION_ADMIN = {"admin": True, "username": "root", "csrf": "tok"}
SESSION_MEMBER = {"admin": False, "username": "bob", "csrf": "tok"}
CTX_EDIT = {"dns_editable": True, "server_host": "vpn.example.com"}
CTX_RO = {"dns_editable": False, "dns_reason": "No Docker socket here.", "server_host": "vpn.example.com"}

FULL = {"magic_dns": True, "base_domain": "corp.ts.net", "override_local_dns": True,
        "nameservers": ["1.1.1.1", "https://dns.nextdns.io/abc123"],
        "search_domains": ["corp.lan", "lab.lan"],
        "split": {"corp.lan": ["10.0.0.53", "10.0.0.54"], "lab.lan": ["192.168.1.1"]},
        "extra_records": [{"name": "nas.example.com", "type": "A", "value": "100.64.0.5"},
                          {"name": "v6.example.com", "type": "AAAA", "value": "fd7a:115c:a1e0::5"}]}


# The shape install.sh writes (templates/headscale-config.yaml.tmpl)
BASE_CONFIG = """---
server_url: https://vpn.example.com
# >>> dns: managed by Headscale Easy (do not edit between these markers)
dns:
  magic_dns: true
  base_domain: myorg.headscale.net
  override_local_dns: true
  nameservers:
    global:
      - 1.1.1.1
      - 1.0.0.1
    split: {}
  search_domains: []
  extra_records: []
# <<< dns
log:
  level: info
"""


def write_config(cfg):
    text = BASE_CONFIG
    with open(CONFIG, "w") as fh:
        fh.write(hs.replace_dns_block(text, hs.render_dns_block(cfg)))


class FormFields(html.parser.HTMLParser):
    """What a browser without JS submits for the form whose action/section match."""

    def __init__(self, section):
        super().__init__()
        self.section, self.depth, self.in_tpl, self.fields, self.forms = section, 0, 0, [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.forms.append([])
        elif tag == "template":
            self.in_tpl += 1
        elif tag == "input" and self.forms and not self.in_tpl:
            if a.get("type") == "checkbox" and "checked" not in a:
                return
            self.forms[-1].append((a["name"], a.get("value", "")))

    def handle_endtag(self, tag):
        if tag == "template":
            self.in_tpl -= 1
        elif tag == "form" and self.forms:
            f = self.forms.pop()
            if ("section", self.section) in f:
                self.fields = f


def submitted(page_html, section="settings"):
    p = FormFields(section)
    p.feed(page_html)
    qs = urllib.parse.urlencode(p.fields)
    data = urllib.parse.parse_qs(qs, keep_blank_values=True)
    return {k: (v if k == "route" or k.endswith("[]") else v[0]) for k, v in data.items()}


class FakeHandler:
    def __init__(self, body):
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body.encode())


class DnsTests(unittest.TestCase):
    def setUp(self):
        # Another test module may have imported headscale first with its own
        # config path: point it at this module's temp file.
        hs.HEADSCALE_CONFIG = CONFIG
        set_lang("en")

    def test_form_repeated_fields(self):
        body = "a=1&ns[]=1.1.1.1&ns[]=&ns[]=8.8.8.8&route=x&route=y&csrf=t"
        form = app.Handler.form(FakeHandler(body))
        self.assertEqual(form["ns[]"], ["1.1.1.1", "", "8.8.8.8"])
        self.assertEqual(form["route"], ["x", "y"])
        self.assertEqual(form["a"], "1")

    def test_block_round_trip(self):
        write_config(FULL)
        self.assertEqual(hs.dns_config(), FULL)
        with open(CONFIG) as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nserver_url:") and text.endswith("log:\n  level: info\n"))

    def test_original_config_parses(self):
        with open(CONFIG, "w") as fh:
            fh.write(BASE_CONFIG)
        self.assertEqual(hs.dns_config(), {
            "magic_dns": True, "base_domain": "myorg.headscale.net", "override_local_dns": True,
            "nameservers": ["1.1.1.1", "1.0.0.1"], "search_domains": [], "split": {}, "extra_records": []})

    def test_no_js_submission_round_trip(self):
        """Rendering the admin page and submitting it untouched keeps every setting."""
        for cfg in (FULL, dict(FULL, override_local_dns=False, split={}, extra_records=[], search_domains=[]),
                    dict(FULL, magic_dns=False)):
            page = pages.dns_page(SESSION_ADMIN, CTX_EDIT, cfg, [])
            new, err = app.dns_cfg_from_form(submitted(page), cfg)
            self.assertEqual(err, "")
            self.assertEqual(new, cfg)
            write_config(new)
            self.assertEqual(hs.dns_config(), cfg)

    def test_settings_rows(self):
        form = {"section": "settings", "use_local_dns": "1",
                "ns[]": [" 9.9.9.9 ", "", "1.1.1.1"],
                "search[]": ["", "Corp.LAN."],
                "split_domain[]": ["corp.lan", "corp.lan", "", "lab.lan"],
                "split_ns[]": ["10.0.0.53", "10.0.0.54", "", "10.1.0.1"],
                "rec_name[]": ["NAS.example.com", ""], "rec_value[]": ["100.64.0.9", ""]}
        cfg, err = app.dns_cfg_from_form(form, FULL)
        self.assertEqual(err, "")
        self.assertFalse(cfg["override_local_dns"])
        self.assertEqual(cfg["nameservers"], ["9.9.9.9", "1.1.1.1"])
        self.assertEqual(cfg["search_domains"], ["corp.lan"])
        self.assertEqual(cfg["split"], {"corp.lan": ["10.0.0.53", "10.0.0.54"], "lab.lan": ["10.1.0.1"]})
        self.assertEqual(cfg["extra_records"], [{"name": "nas.example.com", "type": "A", "value": "100.64.0.9"}])
        self.assertEqual((cfg["magic_dns"], cfg["base_domain"]), (True, "corp.ts.net"))
        write_config(cfg)
        self.assertEqual(hs.dns_config(), cfg)

    def test_empty_lists_and_override(self):
        cfg, err = app.dns_cfg_from_form({"section": "settings"}, FULL)
        self.assertEqual(err, "")
        self.assertTrue(cfg["override_local_dns"])  # checkbox absent = use local off = override
        self.assertEqual((cfg["nameservers"], cfg["split"], cfg["extra_records"], cfg["search_domains"]),
                         ([], {}, [], []))
        write_config(cfg)
        self.assertEqual(hs.dns_config(), cfg)

    def test_single_value_not_list(self):
        cfg, err = app.dns_cfg_from_form({"section": "settings", "ns[]": "8.8.8.8", "use_local_dns": "1"}, FULL)
        self.assertEqual(cfg["nameservers"], ["8.8.8.8"])

    def test_errors_keep_draft(self):
        cases = [
            ({"split_domain[]": ["corp.lan"], "split_ns[]": [""]}, "needs a nameserver"),
            ({"split_domain[]": [""], "split_ns[]": ["10.0.0.1"]}, "needs a domain"),
            ({"split_domain[]": ["corp.lan"]}, "needs a nameserver"),
            ({"rec_name[]": ["nas.example.com"], "rec_value[]": [""]}, "needs a name and an address"),
            ({"rec_name[]": ["nas.example.com"], "rec_value[]": ["nope"]}, "is not an IP address"),
            ({"rec_name[]": ["bad_name"], "rec_value[]": ["100.64.0.1"]}, "Invalid domain"),
        ]
        for extra, msg in cases:
            cfg, err = app.dns_cfg_from_form(dict({"section": "settings"}, **extra), FULL)
            self.assertIn(msg, err, extra)
            # The draft renders with what was typed
            page = pages.dns_page(SESSION_ADMIN, CTX_EDIT, cfg, [], error=err)
            for v in [x for vals in extra.values() for x in vals if x]:
                self.assertIn(f'value="{v}"', page)

    def test_rename_and_magic_keep_the_rest(self):
        cfg, err = app.dns_cfg_from_form({"section": "rename", "base_domain": " New.TS.net. "}, FULL)
        self.assertEqual(err, "")
        self.assertEqual(cfg, dict(FULL, base_domain="new.ts.net"))
        cfg, _ = app.dns_cfg_from_form({"section": "magic", "magic_dns": "0"}, FULL)
        self.assertEqual(cfg, dict(FULL, magic_dns=False))
        cfg, _ = app.dns_cfg_from_form({"section": "magic", "magic_dns": "1"}, dict(FULL, magic_dns=False))
        self.assertEqual(cfg, FULL)
        # The page's own dialog / enable forms submit these fields
        page = pages.dns_page(SESSION_ADMIN, CTX_EDIT, FULL, [])
        self.assertEqual(submitted(page, "rename")["base_domain"], "corp.ts.net")
        self.assertEqual(submitted(page, "magic")["magic_dns"], "0")
        page = pages.dns_page(SESSION_ADMIN, CTX_EDIT, dict(FULL, magic_dns=False), [])
        self.assertEqual(submitted(page, "magic")["magic_dns"], "1")

    def test_render_admin(self):
        page = pages.dns_page(SESSION_ADMIN, CTX_EDIT, FULL, [])
        self.assertIn('data-open="dns-rename"', page)
        self.assertIn('<dialog id="dns-rename">', page)
        self.assertIn('data-open="dns-magic-off"', page)
        self.assertIn('<dialog id="dns-magic-off">', page)
        self.assertIn("100.100.100.100", page)
        self.assertIn("always the first search domain", page)
        self.assertIn('name="use_local_dns" value="1" >', page)  # override on -> unchecked
        self.assertEqual(page.count("<template data-dns-template"), 4)
        self.assertEqual(page.count("data-dns-blank"), 4)
        self.assertNotIn("<textarea", page)
        page = pages.dns_page(SESSION_ADMIN, CTX_EDIT, dict(FULL, magic_dns=False, override_local_dns=False), [])
        self.assertNotIn("100.100.100.100", page)
        self.assertNotIn("dns-magic-off", page)
        self.assertIn("Enable MagicDNS", page)
        self.assertNotIn("always the first search domain", page)
        self.assertIn('name="use_local_dns" value="1" checked>', page)

    def test_render_member_and_readonly_admin(self):
        for session, ctx in ((SESSION_MEMBER, CTX_EDIT), (SESSION_ADMIN, CTX_RO)):
            page = pages.dns_page(session, ctx, FULL, [])
            main = page[page.index("<main"):] if "<main" in page else page
            self.assertNotIn('action="/dns"', main.replace(pages.BASE, ""))
            self.assertNotIn("<dialog", main)
            self.assertNotIn("data-dns-list", main)
            self.assertNotIn("data-open=\"dns-", main)
            for v in ("10.0.0.53", "https://dns.nextdns.io/abc123", "nas.example.com", "lab.lan", "100.100.100.100"):
                self.assertIn(v, main)
        self.assertIn("No Docker socket here.", pages.dns_page(SESSION_ADMIN, CTX_RO, FULL, []))
        self.assertIn("Only an admin can change them.", pages.dns_page(SESSION_MEMBER, CTX_EDIT, FULL, []))

    def test_escaping(self):
        evil = dict(FULL, base_domain='x"><script>', search_domains=['<i>evil</i>'])
        for session in (SESSION_ADMIN, SESSION_MEMBER):
            page = pages.dns_page(session, CTX_EDIT, evil, [])
            self.assertNotIn("<script>", page)
            self.assertNotIn("<i>evil", page)

    def test_spanish(self):
        set_lang("es")
        page = pages.dns_page(SESSION_ADMIN, CTX_EDIT, FULL, [])
        self.assertIn("Renombrar tailnet…", page)
        self.assertIn("Usar la configuración DNS local", page)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=1)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
