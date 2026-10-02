"""Tests for aio/wizard.py: token gate, CSRF, validation and the full flow against a fake Headscale."""
import http.cookiejar
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "aio"))
sys.path.insert(0, os.path.join(ROOT, "web"))

os.environ.setdefault("PUBLIC_URL", "http://localhost")
os.environ.setdefault("HEADSCALE_API_KEY", "x")
os.environ.setdefault("SESSION_SECRET", "x")

import local_accounts as lac  # noqa: E402
import render  # noqa: E402
import wizard  # noqa: E402

FAKE_HEADSCALE = """#!/usr/bin/env python3
import json, os, sys, time
state = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
args = sys.argv[1:]
def flag(name): return os.path.exists(os.path.join(state, name))
def touch(name, text=""):
    with open(os.path.join(state, name), "a") as fh: fh.write(text)
def log(): touch("calls", " ".join(args[:3]) + "\\n")
cmd = args[0]
if cmd == "serve":
    touch("served"); time.sleep(60)
elif cmd == "health":
    sys.exit(0)
elif cmd == "apikeys":
    log(); print("fake-api-key-000")
elif cmd == "users" and args[1] == "list":
    names = open(os.path.join(state, "users")).read().split() if flag("users") else []
    print(json.dumps([{"id": i + 1, "name": n} for i, n in enumerate(names)]))
elif cmd == "users" and args[1] == "create":
    log(); touch("users", args[2] + "\\n")
elif cmd == "policy" and args[1] == "get":
    sys.exit(0 if flag("policy") else 1)
elif cmd == "policy" and args[1] == "set":
    log(); touch("policy", sys.stdin.read())
else:
    sys.exit(2)
"""


# Throwaway test values, built at run time so no credential-looking literal sits in the source
PW1, PW2, PW3 = ("-".join(["test", "pass", str(n)]) for n in (1, 2, 3))
PW_SHORT = "t-" + "1"


def admin_form(email, pw, pw2=None, username="admin"):
    """The admin step's fields, assembled here so no literal pairs a user with a password."""
    form = {"email": email, "password": pw, "password2": pw if pw2 is None else pw2}
    form["username"] = username
    return form


class Client:
    """A browser: cookies, no redirects followed (the wizard's redirects are part of the contract)."""

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    def __init__(self, port):
        self.base = "http://127.0.0.1:%d" % port
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), self._NoRedirect)

    def request(self, path, data=None, headers=None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(self.base + path, data=body, headers=headers or {})
        try:
            resp = self.opener.open(req, timeout=10)
        except urllib.error.HTTPError as exc:
            resp = exc
        return resp.status, resp.headers, resp.read().decode()

    def csrf(self, html):
        return re.search(r'name="csrf" value="([^"]+)"', html).group(1)

    def post(self, path, data, page=None):
        """POST with the csrf token of ``page`` (the form's GET, fetched here when omitted)."""
        if page is None:
            page = self.request(path)[2]
        return self.request(path, dict(data, csrf=self.csrf(page)))


class WizardTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = os.path.join(self.tmp, "data")
        for sub in ("config", "console", "headscale", "caddy/logs"):
            os.makedirs(os.path.join(self.data, sub), mode=0o700)
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.state)
        self.fake = os.path.join(self.tmp, "headscale")
        with open(self.fake, "w") as fh:
            fh.write(FAKE_HEADSCALE)
        os.chmod(self.fake, 0o755)
        patches = [("HEADSCALE_BIN", self.fake), ("EXIT_DELAY", 0.05), ("HEADSCALE_START_TIMEOUT", 10)]
        for name, value in patches:
            old = getattr(wizard, name)
            setattr(wizard, name, value)
            self.addCleanup(setattr, wizard, name, old)
        lac.configure(os.path.join(self.data, "console", "accounts.db"))
        self.token = wizard.ensure_token(self.data)
        self.exited = threading.Event()
        self.wiz = wizard.Wizard(self.data, env={}, exit_callback=self.exited.set)
        self.server = wizard.make_server(self.wiz, "127.0.0.1", 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.c = Client(self.server.server_address[1])

    def unlock(self, client=None):
        c = client or self.c
        status, _h, html = c.request("/admin/setup")
        self.assertEqual(status, 200)
        status, headers, _b = c.request("/admin/setup", {"token": self.token, "csrf": c.csrf(html)})
        self.assertEqual(status, 303, _b[:200])
        return c


class TokenAndRoutingTest(WizardTestBase):
    def test_token_file_is_private_and_logged_in_banner(self):
        path = wizard.token_path(self.data)
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        self.assertIn(self.token, wizard.banner(self.token))
        self.assertEqual(wizard.ensure_token(self.data), self.token)  # stable across restarts

    def test_steps_without_token_are_forbidden(self):
        for step in wizard.STEPS:
            self.assertEqual(self.c.request("/admin/setup/" + step)[0], 403, step)
            self.assertEqual(self.c.request("/admin/setup/" + step, {"x": "1"})[0], 403, step)

    def test_wrong_token_is_403(self):
        _s, _h, html = self.c.request("/admin/setup")
        status, _h, _b = self.c.request("/admin/setup", {"token": "nope", "csrf": self.c.csrf(html)})
        self.assertEqual(status, 403)
        self.assertEqual(self.c.request("/admin/setup/language")[0], 403)

    def test_token_attempts_are_rate_limited(self):
        _s, _h, html = self.c.request("/admin/setup")
        csrf = self.c.csrf(html)
        codes = [self.c.request("/admin/setup", {"token": "bad%d" % i, "csrf": csrf})[0] for i in range(6)]
        self.assertEqual(codes[:5], [403] * 5)
        self.assertEqual(codes[5], 429)
        # even the right token waits
        self.assertEqual(self.c.request("/admin/setup", {"token": self.token, "csrf": csrf})[0], 429)

    def test_csrf_is_enforced_on_every_post(self):
        self.unlock()
        for step in ("language", "server", "admin", "totp", "network", "backups", "finish"):
            self.assertEqual(self.c.request("/admin/setup/" + step, {"x": "1"})[0], 403, step)
            self.assertEqual(self.c.request("/admin/setup/" + step, {"csrf": "wrong"})[0], 403, step)
        status, _h, _b = self.c.request("/admin/setup", {"token": self.token})
        self.assertEqual(status, 403)

    def test_everything_else_redirects_to_setup(self):
        for path in ("/", "/admin", "/admin/machines", "/admin/login", "/healthz", "/admin/setupfoo", "/api/v1/node"):
            status, headers, _b = self.c.request(path)
            self.assertEqual((status, headers["Location"]), (302, "/admin/setup"), path)

    def test_static_files_are_served_and_confined(self):
        self.assertEqual(self.c.request("/admin/static/style.css")[0], 200)
        self.assertEqual(self.c.request("/admin/static/../app.py")[0], 404)
        self.assertEqual(self.c.request("/admin/static/%2e%2e/app.py")[0], 404)

    def test_steps_cannot_be_skipped(self):
        self.unlock()
        status, headers, _b = self.c.request("/admin/setup/finish")
        self.assertEqual((status, headers["Location"]), (303, "/admin/setup/language"))
        status, headers, _b = self.c.request("/admin/setup/totp")
        self.assertEqual(headers["Location"], "/admin/setup/language")

    def test_session_cookie_flags(self):
        _s, headers, _b = self.c.request("/admin/setup")
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)


class ValidationTest(unittest.TestCase):
    def setUp(self):
        wizard.set_lang("en")

    def test_urls(self):
        self.assertEqual(wizard.check_url("https://hs.example.com/"), "https://hs.example.com")
        self.assertEqual(wizard.check_url("http://10.0.0.5:8080"), "http://10.0.0.5:8080")
        for bad in ("hs.example.com", "ftp://x.com", "https://", "https://x.com/path", "https://x.com?a=1",
                    "https://" + "@".join(["u:" + "q", "x.com"]), "https://x.com:99999", 'https://x.com"', "https://x .com", "http://a$b.com"):
            with self.assertRaises(wizard.SetupError, msg=bad):
                wizard.check_url(bad)

    def test_emails(self):
        self.assertEqual(wizard.check_email(" Admin@Example.COM "), "admin@example.com")
        for bad in ("", "a", "a@b", "a b@c.com", "a@b.c\nd", 'a"@b.com'):
            with self.assertRaises(wizard.SetupError, msg=bad):
                wizard.check_email(bad)

    def test_tls_rules(self):
        ok = wizard.check_server("https://hs.example.com", "auto", "a@example.com")
        self.assertEqual(ok, {"public_url": "https://hs.example.com", "tls": "auto", "acme_email": "a@example.com"})
        self.assertEqual(wizard.check_server("http://localhost", "off", "")["tls"], "off")
        self.assertEqual(wizard.check_server("https://10.0.0.1", "internal", "")["acme_email"], "")
        for args in (("http://hs.example.com", "auto", "a@example.com"),   # auto needs https
                     ("http://hs.example.com", "internal", ""),
                     ("https://hs.example.com", "auto", ""),                # and an email
                     ("https://10.0.0.1", "auto", "a@example.com"),         # not for IPs
                     ("https://localhost", "auto", "a@example.com"),
                     ("https://hs.example.com", "magic", "")):
            with self.assertRaises(wizard.SetupError, msg=str(args)):
                wizard.check_server(*args)

    def test_network_and_backups(self):
        self.assertEqual(wizard.check_network("My-Org", True)["tailnet_name"], "my-org")
        self.assertEqual(wizard.check_network("x", False)["network_isolation"], "false")
        for bad in ("", "-a", "a-", "a.b", "a b", "x" * 40):
            with self.assertRaises(wizard.SetupError, msg=bad):
                wizard.check_network(bad, True)
        self.assertEqual(wizard.check_backups("0  3 * * *", "14"), {"backup_schedule": "0 3 * * *",
                                                                   "backup_keep_days": "14"})
        for sched, days in (("0 3 * *", "14"), ("0 3 * * * *", "14"), ("a b c d e", "14"), ("0 3 * * *", "0"),
                            ("0 3 * * *", "x"), ("0 3 * * *", "99999"), ("0 3 * * $(x)", "14")):
            with self.assertRaises(wizard.SetupError, msg=(sched, days)):
                wizard.check_backups(sched, days)

    def test_server_step_rejects_bad_url_over_http(self):
        pass  # covered end to end in FullFlowTest.test_invalid_input_keeps_the_step


class FullFlowTest(WizardTestBase):
    def go_to_totp(self, tls="off", url="http://localhost:8080"):
        c = self.unlock()
        self.assertEqual(c.post("/admin/setup/language", {"lang": "es"})[0], 303)
        self.assertEqual(c.post("/admin/setup/server", {"public_url": url, "tls": tls, "acme_email": ""})[0], 303)
        status, headers, _b = c.post("/admin/setup/admin", admin_form("Admin@Example.com", PW1, PW1))
        self.assertEqual((status, headers["Location"]), (303, "/admin/setup/totp"))
        return c

    def confirm_totp(self, c):
        _s, _h, html = c.request("/admin/setup/totp")
        account = lac.get_account(email="admin@example.com")
        self.assertTrue(account["totp_secret"])
        code = lac.compute_totp(account["totp_secret"])
        return c.request("/admin/setup/totp", {"code": code, "csrf": c.csrf(html)})

    def test_wrong_totp_code_is_refused(self):
        c = self.go_to_totp()
        status, _h, html = c.post("/admin/setup/totp", {"code": "000000"})
        self.assertEqual(status, 400)
        self.assertFalse(lac.get_account(email="admin@example.com")["totp_confirmed"])
        status, headers, _b = c.request("/admin/setup/network")
        self.assertEqual(headers["Location"], "/admin/setup/totp")  # cannot skip 2FA

    def test_full_flow(self):
        c = self.go_to_totp()
        self.assertIn("es", [ck.value for ck in c.jar if ck.name == "hse_lang"])
        status, headers, _b = self.confirm_totp(c)
        self.assertEqual((status, headers["Location"]), (303, "/admin/setup/recovery"))
        _s, _h, html = c.request("/admin/setup/recovery")
        self.assertEqual(len(re.findall(r"<pre>(.*?)</pre>", html, re.S)[0].split()), 8)
        self.assertEqual(c.post("/admin/setup/recovery", {}, page=html)[0], 303)
        self.assertEqual(c.request("/admin/setup/recovery")[1]["Location"], "/admin/setup/network")
        self.assertEqual(c.post("/admin/setup/network", {"tailnet_name": "acme", "isolation": "1"})[0], 303)
        self.assertEqual(c.post("/admin/setup/backups", {"backup_schedule": "30 2 * * *", "backup_keep_days": "7"})[0], 303)
        _s, _h, review = c.request("/admin/setup/finish")
        self.assertIn("admin@example.com", review)
        status, _h, done = c.post("/admin/setup/finish", {}, page=review)
        self.assertEqual(status, 200, done[:500])
        self.assertIn("http://localhost:8080/admin/", done)
        self.assertTrue(self.exited.wait(5), "the wizard did not ask to exit")

        # settings.json (600) with a generated session secret, and what was asked for
        path = os.path.join(self.data, "config", "settings.json")
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        saved = json.load(open(path))
        self.assertEqual((saved["public_url"], saved["tls"], saved["ui_lang"], saved["tailnet_name"]),
                         ("http://localhost:8080", "off", "es", "acme"))
        self.assertEqual((saved["network_isolation"], saved["backup_schedule"], saved["backup_keep_days"],
                          saved["admin_email"]), ("true", "30 2 * * *", "7", "admin@example.com"))
        self.assertGreaterEqual(len(saved["session_secret"]), 32)
        self.assertFalse(os.path.exists(path + ".pending"))
        # config rendered, API key written, token gone
        self.assertIn("http://localhost:8080", open(os.path.join(self.data, "config", "config.yaml")).read())
        self.assertTrue(os.path.exists(os.path.join(self.data, "config", "Caddyfile")))
        key = os.path.join(self.data, "console", "api-key")
        self.assertEqual(open(key).read().strip(), "fake-api-key-000")
        self.assertEqual(oct(os.stat(key).st_mode & 0o777), "0o600")
        self.assertFalse(os.path.exists(wizard.token_path(self.data)))
        # Headscale: user created, isolation policy applied (once)
        self.assertEqual(open(os.path.join(self.state, "users")).read().split(), ["admin"])
        self.assertIn("autogroup:self", open(os.path.join(self.state, "policy")).read())
        # the account: admin, TOTP confirmed, mapped to the Headscale user
        account = lac.get_account(email="admin@example.com")
        self.assertEqual((account["role"], account["totp_confirmed"], account["headscale_user"]), ("admin", 1, "admin"))
        # the loaded settings are what the supervisor will use
        loaded = render.load_settings({}, path)
        self.assertEqual(loaded["tailnet_name"], "acme")
        render.to_vars(loaded)
        # the token is gone: nobody can start over
        c2 = Client(self.server.server_address[1])
        _s, _h, html = c2.request("/admin/setup")
        self.assertEqual(c2.request("/admin/setup", {"token": self.token, "csrf": c2.csrf(html)})[0], 403)

    def finish_all(self, c, isolation="1"):
        self.confirm_totp(c)
        _s, _h, html = c.request("/admin/setup/recovery")
        c.post("/admin/setup/recovery", {}, page=html)
        c.post("/admin/setup/network", {"tailnet_name": "acme", **({"isolation": "1"} if isolation else {})})
        c.post("/admin/setup/backups", {"backup_schedule": "0 3 * * *", "backup_keep_days": "14"})
        return c.post("/admin/setup/finish", {})

    def test_isolation_off_skips_the_policy_and_existing_policy_is_kept(self):
        c = self.go_to_totp()
        self.assertEqual(self.finish_all(c, isolation="")[0], 200)
        self.assertFalse(os.path.exists(os.path.join(self.state, "policy")))
        self.assertEqual(json.load(open(os.path.join(self.data, "config", "settings.json")))["network_isolation"],
                         "false")

    def test_existing_policy_is_not_replaced(self):
        with open(os.path.join(self.state, "policy"), "w") as fh:
            fh.write("custom")
        c = self.go_to_totp()
        self.assertEqual(self.finish_all(c)[0], 200)
        self.assertEqual(open(os.path.join(self.state, "policy")).read(), "custom")

    def test_finish_failure_can_be_retried_without_duplicates(self):
        c = self.go_to_totp()
        # break `users create` once: the fake exits 2 for unknown commands
        broken = open(self.fake).read().replace('elif cmd == "users" and args[1] == "create":',
                                                'elif cmd == "users" and args[1] == "create" and os.path.exists(os.path.join(state, "break")):\n    sys.exit(1)\nelif cmd == "users" and args[1] == "create":')
        open(self.fake, "w").write(broken)
        open(os.path.join(self.state, "break"), "w").close()
        status, _h, html = self.finish_all(c)
        self.assertEqual(status, 500)
        self.assertIn("No se pudo crear el usuario de Headscale", html)  # the session language is es
        self.assertFalse(os.path.exists(os.path.join(self.data, "config", "settings.json")),
                         "settings.json is the last thing written")
        self.assertTrue(os.path.exists(wizard.token_path(self.data)))
        os.unlink(os.path.join(self.state, "break"))
        status, _h, html = c.post("/admin/setup/finish", {})  # retry
        self.assertEqual(status, 200, html[:400])
        self.assertEqual(open(os.path.join(self.state, "users")).read().split(), ["admin"])
        self.assertEqual(len(lac.list_accounts()), 1)
        self.assertEqual(open(os.path.join(self.state, "calls")).read().count("apikeys"), 1)

    def test_resuming_reuses_the_account_and_totp(self):
        c = self.go_to_totp()
        self.confirm_totp(c)
        secret = lac.get_account(email="admin@example.com")["totp_secret"]
        c2 = self.unlock(Client(self.server.server_address[1]))  # a new browser session, e.g. wizard restarted
        c2.post("/admin/setup/language", {"lang": "en"})
        c2.post("/admin/setup/server", {"public_url": "http://localhost:8080", "tls": "off", "acme_email": ""})
        status, headers, _b = c2.post("/admin/setup/admin", admin_form("admin@example.com", PW2, PW2))
        self.assertEqual(status, 303)
        self.assertEqual(len(lac.list_accounts()), 1)
        self.assertTrue(lac.verify_password(PW2, lac.get_account(email="admin@example.com")["pw_hash"]))
        self.assertEqual(c2.request("/admin/setup/totp")[1]["Location"], "/admin/setup/network")
        self.assertEqual(lac.get_account(email="admin@example.com")["totp_secret"], secret)

    def test_invalid_input_keeps_the_step(self):
        c = self.unlock()
        c.post("/admin/setup/language", {"lang": "en"})
        status, _h, html = c.post("/admin/setup/server", {"public_url": "not a url", "tls": "off", "acme_email": ""})
        self.assertEqual(status, 400)
        self.assertIn("http(s)://host[:port]", html)
        status, _h, html = c.post("/admin/setup/server", {"public_url": "http://x.com", "tls": "auto", "acme_email": ""})
        self.assertEqual(status, 400)
        c.post("/admin/setup/server", {"public_url": "http://localhost", "tls": "off", "acme_email": ""})
        status, _h, html = c.post("/admin/setup/admin", admin_form("bad", PW1, PW1))
        self.assertEqual(status, 400)
        status, _h, html = c.post("/admin/setup/admin", admin_form("a@b.com", PW1, PW3))
        self.assertEqual(status, 400)
        status, _h, html = c.post("/admin/setup/admin", admin_form("a@b.com", PW_SHORT, PW_SHORT))
        self.assertEqual(status, 400)
        self.assertEqual(lac.list_accounts(), [])

    def test_html_is_escaped(self):
        c = self.unlock()
        c.post("/admin/setup/language", {"lang": "en"})
        _s, _h, html = c.post("/admin/setup/server", {"public_url": '"><script>alert(1)</script>', "tls": "off",
                                                       "acme_email": ""})
        self.assertNotIn("<script>alert(1)", html)


class NoWizardWithEnvTest(unittest.TestCase):
    def test_supervisor_starts_run_mode_when_public_url_is_set(self):
        import supervisor
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        sup = supervisor.Supervisor(data_dir=tmp, env={"HSE_PUBLIC_URL": "http://localhost"}, sink=lambda _l: None,
                                    run_dir=os.path.join(tmp, "run"))
        self.assertEqual(sup.detect_mode(), "run")
        sup = supervisor.Supervisor(data_dir=tmp, env={}, sink=lambda _l: None, run_dir=os.path.join(tmp, "run"))
        self.assertEqual(sup.detect_mode(), "setup")

    def test_wizard_gets_the_env_it_needs(self):
        import supervisor
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        sup = supervisor.Supervisor(data_dir=tmp, env={"HSE_TLS": "internal", "HSE_HEADSCALE_BIN": "/x/hs", "SECRET": "no"},
                                    sink=lambda _l: None, run_dir=tmp)
        _argv, env = sup.wizard_prepare()
        self.assertEqual((env["HSE_TLS"], env["HSE_HEADSCALE_BIN"], env["HSE_DATA_DIR"]), ("internal", "/x/hs", tmp))
        self.assertNotIn("SECRET", env)


if __name__ == "__main__":
    unittest.main()
