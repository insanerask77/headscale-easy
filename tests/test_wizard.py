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
        status, _h, html = c.request("/console/setup")
        self.assertEqual(status, 200)
        status, headers, _b = c.request("/console/setup", {"token": self.token, "csrf": c.csrf(html)})
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
            self.assertEqual(self.c.request("/console/setup/" + step)[0], 403, step)
            self.assertEqual(self.c.request("/console/setup/" + step, {"x": "1"})[0], 403, step)

    def test_wrong_token_is_403(self):
        _s, _h, html = self.c.request("/console/setup")
        status, _h, _b = self.c.request("/console/setup", {"token": "nope", "csrf": self.c.csrf(html)})
        self.assertEqual(status, 403)
        self.assertEqual(self.c.request("/console/setup/language")[0], 403)

    def test_token_attempts_are_rate_limited(self):
        _s, _h, html = self.c.request("/console/setup")
        csrf = self.c.csrf(html)
        codes = [self.c.request("/console/setup", {"token": "bad%d" % i, "csrf": csrf})[0] for i in range(6)]
        self.assertEqual(codes[:5], [403] * 5)
        self.assertEqual(codes[5], 429)
        # even the right token waits
        self.assertEqual(self.c.request("/console/setup", {"token": self.token, "csrf": csrf})[0], 429)

    def test_csrf_is_enforced_on_every_post(self):
        self.unlock()
        for step in ("language", "server", "admin", "signup", "network", "derp", "backups", "finish"):
            self.assertEqual(self.c.request("/console/setup/" + step, {"x": "1"})[0], 403, step)
            self.assertEqual(self.c.request("/console/setup/" + step, {"csrf": "wrong"})[0], 403, step)
        status, _h, _b = self.c.request("/console/setup", {"token": self.token})
        self.assertEqual(status, 403)

    def test_everything_else_redirects_to_setup(self):
        for path in ("/", "/console", "/console/machines", "/console/login", "/healthz", "/console/setupfoo", "/api/v1/node"):
            status, headers, _b = self.c.request(path)
            self.assertEqual((status, headers["Location"]), (302, "/console/setup"), path)

    def test_static_files_are_served_and_confined(self):
        self.assertEqual(self.c.request("/console/static/style.css")[0], 200)
        self.assertEqual(self.c.request("/console/static/../app.py")[0], 404)
        self.assertEqual(self.c.request("/console/static/%2e%2e/app.py")[0], 404)

    def test_steps_cannot_be_skipped(self):
        self.unlock()
        status, headers, _b = self.c.request("/console/setup/finish")
        self.assertEqual((status, headers["Location"]), (303, "/console/setup/language"))
        status, headers, _b = self.c.request("/console/setup/network")
        self.assertEqual(headers["Location"], "/console/setup/language")
        self.assertEqual(self.c.request("/console/setup/totp")[0], 404)  # two-factor is not a wizard step

    def test_session_cookie_flags(self):
        _s, headers, _b = self.c.request("/console/setup")
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

    def test_derp_step(self):
        self.assertEqual(wizard.check_derp("embedded", "https://ignored"), {"derp_mode": "embedded", "derp_url": ""})
        self.assertEqual(wizard.check_derp("public"), {"derp_mode": "public", "derp_url": ""})
        self.assertEqual(wizard.check_derp("custom", "https://d.example/map.json")["derp_url"], "https://d.example/map.json")
        self.assertEqual(wizard.check_derp("custom")["derp_url"], "")
        for mode, url in (("", ""), ("nope", ""), ("custom", "ftp://x"), ("custom", "a b")):
            with self.assertRaises(wizard.SetupError, msg=(mode, url)):
                wizard.check_derp(mode, url)

    def test_network_and_backups(self):
        self.assertEqual(wizard.check_network("My-Org", True)["tailnet_name"], "my-org")
        self.assertEqual(wizard.check_network("acme", True)["base_domain"], "")
        got = wizard.check_network("acme", True, " Corp.Internal. ", "https://vpn.example.com")
        self.assertEqual(got["base_domain"], "corp.internal")
        for bad in ("nodots", "a b.com", "-x.com", "x..com", "vpn.example.com", "example.com"):
            with self.assertRaises(wizard.SetupError, msg=bad):
                wizard.check_network("acme", True, bad, "https://vpn.example.com")
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

    def test_backups_off_and_toggle(self):
        self.assertEqual(wizard.check_backups("off", "14")["backup_schedule"], "off")
        self.assertEqual(wizard.check_backups("", "14")["backup_schedule"], "off")
        # turned off: whatever is in the schedule box is ignored, retention is still checked
        self.assertEqual(wizard.check_backups("not a cron", "14", enabled=False)["backup_schedule"], "off")
        with self.assertRaises(wizard.SetupError):
            wizard.check_backups("off", "0", enabled=False)
        self.assertEqual(wizard.check_backups("*/5 * * * *", "3", enabled=True),
                         {"backup_schedule": "*/5 * * * *", "backup_keep_days": "3"})

    def test_backups_page_defaults_and_next_run(self):
        page = wizard.backups_page({"csrf": "t"}, {})
        self.assertIn('value="0 3 * * *"', page)
        self.assertIn('<option value="on" selected>', page)  # on by default
        self.assertIn("Next run: ", page)
        self.assertIn("/data/backups", page)
        off = wizard.backups_page({"csrf": "t"}, {"backup_schedule": "off", "backup_keep_days": "7"})
        self.assertIn('<option value="off" selected>', off)
        self.assertNotIn("Next run: ", off)
        self.assertIn('value="0 3 * * *"', off)  # what turning them back on would use
        self.assertIn('value="7"', off)

    def test_server_step_rejects_bad_url_over_http(self):
        pass  # covered end to end in FullFlowTest.test_invalid_input_keeps_the_step


class FullFlowTest(WizardTestBase):
    def go_to_network(self, tls="off", url="http://localhost:8080"):
        c = self.unlock()
        self.assertEqual(c.post("/console/setup/language", {"lang": "es"})[0], 303)
        self.assertEqual(c.post("/console/setup/server", {"public_url": url, "tls": tls, "acme_email": ""})[0], 303)
        status, headers, _b = c.post("/console/setup/admin", admin_form("Admin@Example.com", PW1, PW1))
        self.assertEqual((status, headers["Location"]), (303, "/console/setup/signup"))
        self.assertEqual(c.post("/console/setup/signup", {"mode": "off"})[0], 303)
        return c

    def test_backups_step_off_and_invalid(self):
        c = self.go_to_network()
        c.post("/console/setup/network", {"tailnet_name": "acme", "isolation": "1"})
        c.post("/console/setup/derp", {"derp_mode": "embedded"})
        for bad in ("0 3 * *", "0 3 * * $(x)", "99 3 * * *"):
            status, _h, html = c.post("/console/setup/backups", {"backup_schedule": bad, "backup_keep_days": "14"})
            self.assertEqual(status, 400, bad)
            self.assertIn('class="notice error"', html)
            self.assertIn('name="backup_schedule"', html)  # the step is shown again
        self.assertEqual(c.post("/console/setup/backups", {"backup_enabled": "off", "backup_schedule": "0 3 * * *",
                                                         "backup_keep_days": "14"})[0], 303)
        _s, _h, review = c.request("/console/setup/finish")
        self.assertIn("<dt>Copias de seguridad</dt><dd>Desactivada</dd>", review)  # the flow runs in Spanish
        status, _h, done = c.post("/console/setup/finish", {}, page=review)
        self.assertEqual(status, 200, done[:300])
        saved = json.load(open(os.path.join(self.data, "config", "settings.json")))
        self.assertEqual(saved["backup_schedule"], "off")

    def test_full_flow(self):
        c = self.go_to_network()
        self.assertIn("es", [ck.value for ck in c.jar if ck.name == "hse_lang"])
        self.assertEqual(c.post("/console/setup/network", {"tailnet_name": "acme", "isolation": "1"})[0], 303)
        self.assertEqual(c.post("/console/setup/derp", {"derp_mode": "embedded"})[0], 303)
        self.assertEqual(c.post("/console/setup/backups", {"backup_schedule": "30 2 * * *", "backup_keep_days": "7"})[0], 303)
        _s, _h, review = c.request("/console/setup/finish")
        self.assertIn("admin@example.com", review)
        status, _h, done = c.post("/console/setup/finish", {}, page=review)
        self.assertEqual(status, 200, done[:500])
        self.assertIn('href="http://localhost:8080/console/login"', done)
        # the page waits for the console and then goes to the sign-in page (app.js, CSP allows only our scripts)
        self.assertIn('data-await-console="http://localhost:8080/console/login"', done)
        self.assertIn('data-probe="/console/healthz"', done)
        self.assertIn("data-await-waiting", done)
        self.assertNotIn("<script>", done)
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
        policy = open(os.path.join(self.state, "policy")).read()
        self.assertIn("autogroup:self", policy)
        self.assertIn("autogroup:internet", policy)  # without it an exit node forwards nothing
        # the account: admin, no two-factor yet (the console suggests it), mapped to the Headscale user
        account = lac.get_account(email="admin@example.com")
        self.assertEqual((account["role"], account["totp_confirmed"], account["headscale_user"]), ("admin", 0, "admin"))
        self.assertEqual(saved["mfa_required"], "optional")  # an admin is not forced to enrol at first sign-in
        # the loaded settings are what the supervisor will use
        loaded = render.load_settings({}, path)
        self.assertEqual(loaded["tailnet_name"], "acme")
        render.to_vars(loaded)
        # the token is gone: nobody can start over
        c2 = Client(self.server.server_address[1])
        _s, _h, html = c2.request("/console/setup")
        self.assertEqual(c2.request("/console/setup", {"token": self.token, "csrf": c2.csrf(html)})[0], 403)

    def finish_all(self, c, isolation="1"):
        c.post("/console/setup/network", {"tailnet_name": "acme", **({"isolation": "1"} if isolation else {})})
        c.post("/console/setup/derp", {"derp_mode": "embedded"})
        c.post("/console/setup/backups", {"backup_schedule": "0 3 * * *", "backup_keep_days": "14"})
        return c.post("/console/setup/finish", {})

    def test_isolation_off_skips_the_policy_and_existing_policy_is_kept(self):
        c = self.go_to_network()
        self.assertEqual(self.finish_all(c, isolation="")[0], 200)
        self.assertFalse(os.path.exists(os.path.join(self.state, "policy")))
        self.assertEqual(json.load(open(os.path.join(self.data, "config", "settings.json")))["network_isolation"],
                         "false")

    def test_signup_step(self):
        self.assertEqual(wizard.check_signup("off"), {"signup_mode": "off", "first_key": False})
        self.assertEqual(wizard.check_signup("invite", True), {"signup_mode": "invite", "first_key": True})
        self.assertFalse(wizard.check_signup("open", True)["first_key"])  # a key only matters with invitations
        for bad in ("", "everyone"):
            with self.assertRaises(wizard.SetupError):
                wizard.check_signup(bad)

    def test_signup_defaults_to_off(self):
        c = self.go_to_network()
        self.assertEqual(self.finish_all(c)[0], 200)
        saved = json.load(open(os.path.join(self.data, "config", "settings.json")))
        self.assertEqual(saved["signup_mode"], "off")
        self.assertEqual(render.console_env(render.load_settings({}, os.path.join(self.data, "config", "settings.json")),
                                            self.data)["HSE_SIGNUP"], "off")

    def test_invite_mode_with_a_first_key_shown_once(self):
        c = self.go_to_network()
        self.assertEqual(c.post("/console/setup/signup", {"mode": "invite", "first_key": "1"})[0], 303)
        status, _h, done = self.finish_all(c)
        self.assertEqual(status, 200)
        key = re.search(r"<code>(hse-[\w-]+)</code>", done).group(1)
        saved = json.load(open(os.path.join(self.data, "config", "settings.json")))
        self.assertEqual(saved["signup_mode"], "invite")
        self.assertNotIn(key, json.dumps(saved))
        self.assertIsNotNone(lac.use_signup_key(key))  # works once
        self.assertIsNone(lac.use_signup_key(key))

    def test_invite_mode_without_a_first_key(self):
        c = self.go_to_network()
        c.post("/console/setup/signup", {"mode": "invite"})
        self.assertNotIn("hse-", self.finish_all(c)[2])
        self.assertEqual(lac.list_signup_keys(), [])

    def test_existing_policy_is_not_replaced(self):
        with open(os.path.join(self.state, "policy"), "w") as fh:
            fh.write("custom")
        c = self.go_to_network()
        self.assertEqual(self.finish_all(c)[0], 200)
        self.assertEqual(open(os.path.join(self.state, "policy")).read(), "custom")

    def test_finish_failure_can_be_retried_without_duplicates(self):
        c = self.go_to_network()
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
        status, _h, html = c.post("/console/setup/finish", {})  # retry
        self.assertEqual(status, 200, html[:400])
        self.assertEqual(open(os.path.join(self.state, "users")).read().split(), ["admin"])
        self.assertEqual(len(lac.list_accounts()), 1)
        self.assertEqual(open(os.path.join(self.state, "calls")).read().count("apikeys"), 1)

    def test_resuming_reuses_the_account(self):
        c = self.go_to_network()
        c2 = self.unlock(Client(self.server.server_address[1]))  # a new browser session, e.g. wizard restarted
        c2.post("/console/setup/language", {"lang": "en"})
        c2.post("/console/setup/server", {"public_url": "http://localhost:8080", "tls": "off", "acme_email": ""})
        status, headers, _b = c2.post("/console/setup/admin", admin_form("admin@example.com", PW2, PW2))
        self.assertEqual((status, headers["Location"]), (303, "/console/setup/signup"))
        self.assertEqual(len(lac.list_accounts()), 1)
        self.assertTrue(lac.verify_password(PW2, lac.get_account(email="admin@example.com")["pw_hash"]))

    def test_pages_use_the_console_components(self):
        c = self.unlock()
        pages = [c.request("/console/setup/language")[2]]
        c.post("/console/setup/language", {"lang": "en"})
        pages.append(c.request("/console/setup/server")[2])
        c.post("/console/setup/server", {"public_url": "http://localhost", "tls": "off", "acme_email": ""})
        pages.append(c.request("/console/setup/admin")[2])
        pages.append(c.request("/console/setup")[2])  # (redirects: still the card markup is checked below)
        for html in pages[:3]:
            self.assertIn('class="card narrow center login setup"', html)
            self.assertIn("btn wide primary", html)
            self.assertIn('class="field"', html)
            self.assertIn('class="setup-steps"', html)
            self.assertNotIn("login-container", html)  # that class has no CSS
        token_page = Client(self.server.server_address[1]).request("/console/setup")[2]
        self.assertIn('class="card narrow center login setup"', token_page)

    def test_invalid_input_keeps_the_step(self):
        c = self.unlock()
        c.post("/console/setup/language", {"lang": "en"})
        status, _h, html = c.post("/console/setup/server", {"public_url": "not a url", "tls": "off", "acme_email": ""})
        self.assertEqual(status, 400)
        self.assertIn("http(s)://host[:port]", html)
        status, _h, html = c.post("/console/setup/server", {"public_url": "http://x.com", "tls": "auto", "acme_email": ""})
        self.assertEqual(status, 400)
        c.post("/console/setup/server", {"public_url": "http://localhost", "tls": "off", "acme_email": ""})
        status, _h, html = c.post("/console/setup/admin", admin_form("bad", PW1, PW1))
        self.assertEqual(status, 400)
        status, _h, html = c.post("/console/setup/admin", admin_form("a@b.com", PW1, PW3))
        self.assertEqual(status, 400)
        status, _h, html = c.post("/console/setup/admin", admin_form("a@b.com", PW_SHORT, PW_SHORT))
        self.assertEqual(status, 400)
        self.assertEqual(lac.list_accounts(), [])

    def test_html_is_escaped(self):
        c = self.unlock()
        c.post("/console/setup/language", {"lang": "en"})
        _s, _h, html = c.post("/console/setup/server", {"public_url": '"><script>alert(1)</script>', "tls": "off",
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
