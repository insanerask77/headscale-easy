"""French, German and Portuguese catalogs: every language mirrors Spanish key
for key, keeps the {placeholders}, and is picked from the cookie or the
browser. Standard library only:

    python3 tests/test_languages.py
"""
import json
import os
import re
import sys
import unittest

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
os.environ.update(HEADSCALE_API_KEY="x", PUBLIC_URL="https://vpn.example.com", SESSION_SECRET="s",
                  HEADSCALE_URL="http://headscale:8080")
sys.path.insert(0, WEB)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import i18n  # noqa: E402

NEW = ("fr", "de", "pt")
LOCALES = os.path.join(WEB, "locales")


def load(lang: str) -> dict:
    out = {}
    names = [f"{lang}.json"] + [f"{lang}.d/{n}" for n in sorted(os.listdir(os.path.join(LOCALES, f"{lang}.d")))
                                if n.endswith(".json")]
    for name in names:
        with open(os.path.join(LOCALES, name), encoding="utf-8") as f:
            out.update(json.load(f))
    return out


def placeholders(text: str) -> list:
    return sorted(re.findall(r"\{\w+\}", text))


class Catalogs(unittest.TestCase):
    def test_languages_registered(self):
        for lang in NEW:
            self.assertIn(lang, i18n.LANGUAGES)

    def test_same_keys_as_spanish(self):
        es = load("es")
        for lang in NEW:
            cat = load(lang)
            self.assertEqual(sorted(set(es) - set(cat)), [], f"{lang}: missing")
            self.assertEqual(sorted(set(cat) - set(es)), [], f"{lang}: unused")

    def test_placeholders_preserved(self):
        for lang in NEW:
            for key, value in load(lang).items():
                self.assertEqual(placeholders(key), placeholders(value), f"{lang}: {key!r}")

    def test_translations_not_empty_and_actually_translated(self):
        for lang in NEW:
            cat = load(lang)
            self.assertFalse([k for k, v in cat.items() if not v.strip()])
            same = [k for k, v in cat.items() if k == v and len(k) > 25]
            self.assertLess(len(same), 5, f"{lang}: long strings left in English: {same[:3]}")


class Selection(unittest.TestCase):
    def tearDown(self):
        i18n.set_lang("en")

    def test_pick_from_cookie_and_browser(self):
        self.assertEqual(i18n.pick_lang("de", None), "de")
        self.assertEqual(i18n.pick_lang(None, "pt-BR,pt;q=0.9,en;q=0.8"), "pt")
        self.assertEqual(i18n.pick_lang(None, "fr-CA,fr;q=0.9"), "fr")
        self.assertEqual(i18n.pick_lang("xx", "ja"), i18n.DEFAULT_LANG)

    def test_translate_and_plurals(self):
        i18n.set_lang("fr")
        self.assertEqual(i18n._("Save"), "Enregistrer")
        i18n.set_lang("de")
        self.assertEqual(i18n.ngettext("{n} machine", "{n} machines", 3), "3 Geräte")
        self.assertEqual(i18n.ngettext("{n} machine", "{n} machines", 1), "1 Gerät")
        i18n.set_lang("pt")
        self.assertEqual(i18n._("Cancel"), "Cancelar")


class Page(unittest.TestCase):
    def test_login_page_in_each_language(self):
        import test_security as ts
        for lang, word in (("fr", "Se connecter"), ("de", "Anmelden"), ("pt", "Fazer login")):
            status, _, body = ts.request("GET", ts.B + "/login", headers={"Accept-Language": lang})
            self.assertEqual(status, 200)
            self.assertIn(word, body, lang)


if __name__ == "__main__":
    unittest.main()
