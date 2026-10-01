"""End-to-end tests against a real, running Headscale Easy stack.

NOT part of 'make test': they need Docker and a stack deployed by
'./install.sh --non-interactive' in this checkout. Run them with 'make e2e'
(tests/e2e/run.sh deploys a throwaway stack, runs them and dumps the logs on
failure). The tests run in order (test_NN_...) and share state: a later step
uses what an earlier one created.

Covered: API key sign-in, OIDC sign-in through the built-in Authentik (when
AUTH_PROVIDER=authentik), Headscale's API, users, auth keys and a real
Tailscale client registering with one, the ACL policy, DNS (validated with
'headscale configtest' and applied by restarting Headscale through
hs-helper), session revocation, and backup + restore.
"""

import os
import re
import time
import unittest
import urllib.parse

from client import ENV, PUBLIC_URL, ROOT, Client, HeadscaleAPI, container_started_at, run, wait_for

AUTH = ENV.get("AUTH_PROVIDER", "none")
API_KEY = ENV.get("HEADSCALE_API_KEY", "")
TAILSCALE_IMAGE = os.environ.get("E2E_TAILSCALE_IMAGE", "tailscale/tailscale:stable")
TS_CONTAINER = "hse-e2e-tailscale"
DOMAIN_HOST = urllib.parse.urlsplit(PUBLIC_URL).hostname or ""

POLICY = """// headscale-easy e2e policy
{
  "groups": {"group:e2e": ["e2e-user@"]},
  "acls": [
    {"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:self:*"]},
    {"action": "accept", "src": ["group:e2e"], "dst": ["group:e2e:22"]}
  ]
}
"""


def stack_up() -> bool:
    return Client().get("/admin/healthz").status == 200 and Client().get("/health").status == 200


@unittest.skipUnless(API_KEY and PUBLIC_URL, "no deployed stack (.env with HEADSCALE_API_KEY): run 'make e2e'")
class StackTest(unittest.TestCase):
    admin: Client
    api: HeadscaleAPI
    state: dict = {}

    @classmethod
    def setUpClass(cls):
        wait_for("the web UI and Headscale", stack_up, timeout=180)
        cls.api = HeadscaleAPI(API_KEY)
        cls.admin = Client()

    def signed_in(self, key: str = API_KEY) -> Client:
        c = Client()
        resp = c.login_apikey(key)
        self.assertEqual(resp.status, 303, resp)
        self.assertTrue(resp.location.endswith("/admin/machines"), resp)
        self.assertIn("hse_session", c.cookies)
        return c

    def sids(self, c: Client) -> set[str]:
        resp = c.get("/admin/settings/sessions")
        self.assertEqual(resp.status, 200, resp)
        return set(re.findall(r'name="sid" value="([^"]+)"', resp.text))

    # --- sign-in -------------------------------------------------------------
    def test_00_health(self):
        self.assertEqual(Client().get("/admin/healthz").text, "ok")
        # Not signed in: every page sends to the sign-in page
        resp = Client().get("/admin/machines")
        self.assertEqual(resp.status, 303)
        self.assertTrue(resp.location.endswith("/admin/login"), resp)

    def test_01_apikey_login_rejects_bad_key(self):
        c = Client()
        resp = c.login_apikey("hskey-api-notarealkey-0000000000000000000000000000")
        self.assertEqual(resp.status, 401, resp)
        self.assertNotIn("hse_session", c.cookies)

    def test_02_apikey_login(self):
        c = self.signed_in()
        resp = c.get("/admin/machines")
        self.assertEqual(resp.status, 200, resp)
        self.assertIn('name="csrf"', resp.text)
        # Admin-only pages
        for page in ("/admin/users", "/admin/acl", "/admin/dns", "/admin/settings/keys", "/admin/logs"):
            self.assertEqual(c.get(page).status, 200, page)
        type(self).admin = c

    def test_03_oidc_login_with_authentik(self):
        if AUTH != "authentik":
            self.skipTest("AUTH_PROVIDER is not authentik")
        password = ENV.get("AUTHENTIK_BOOTSTRAP_PASSWORD", "")
        self.assertTrue(password, "AUTHENTIK_BOOTSTRAP_PASSWORD missing from .env")
        c = Client()
        resp = c.get("/admin/login/sso")
        self.assertEqual(resp.status, 303, resp)
        self.assertIn("/authentik/application/o/authorize/", resp.location)
        self.assertIn("hse_oidc", c.cookies)
        callback = authentik_sign_in(c, resp.location, "akadmin", password)
        self.assertIn("/admin/callback?", callback)
        resp = c.get(callback)
        self.assertEqual(resp.status, 303, resp)
        self.assertTrue(resp.location.endswith("/admin/machines"), resp)
        self.assertIn("hse_session", c.cookies)
        self.assertEqual(c.get("/admin/machines").status, 200)
        # akadmin is in "authentik Admins" (PORTAL_ADMIN_GROUPS): an admin
        self.assertEqual(c.get("/admin/users").status, 200)
        # Signing out ends the session (and goes through Authentik's sign-out flow)
        resp = c.form("/admin/logout")
        self.assertEqual(resp.status, 303, resp)
        self.assertEqual(c.get("/admin/machines").status, 303)

    # --- Headscale API, users, keys, a real client ---------------------------
    def test_10_headscale_api(self):
        self.assertEqual(self.api.call("GET", "/user").status, 200)
        self.assertEqual(HeadscaleAPI("hskey-api-bogus-000000000000").call("GET", "/user").status, 401)
        self.assertIsNotNone(self.api.user(ENV.get("ADMIN_USER", "admin")), self.api.users())

    def test_11_create_user_in_ui(self):
        resp = self.admin.form("/admin/users", {"name": "e2e-user", "display_name": "E2E User"})
        self.assertEqual(resp.status, 303, resp)
        self.assertIn("m=user-created", resp.location)
        user = self.api.user("e2e-user")
        self.assertIsNotNone(user)
        self.state["user_id"] = str(user["id"])

    def test_12_api_key_from_ui(self):
        resp = self.admin.form("/admin/apikeys", {"days": "30"})
        self.assertEqual(resp.status, 200, resp)
        keys = set(re.findall(r"hskey-api-[A-Za-z0-9_-]{20,}", resp.text)) - {API_KEY}
        self.assertEqual(len(keys), 1, keys)
        # The new key signs in to the web UI and works on the API
        self.signed_in(keys.pop())

    def test_13_auth_key_and_tailscale_node(self):
        resp = self.admin.form("/admin/keys", {"user_id": self.state["user_id"], "reusable": "1", "days": "1"})
        self.assertEqual(resp.status, 200, resp)
        m = re.search(r"--authkey=([^\s<\"&]+)", resp.text)
        self.assertTrue(m, resp.text[:2000])
        authkey = m.group(1)
        if os.environ.get("E2E_TAILSCALE", "true") != "true":
            self.skipTest("E2E_TAILSCALE=false: no Tailscale client")
        run("docker", "rm", "-f", TS_CONTAINER, check=False)
        run("docker", "run", "-d", "--name", TS_CONTAINER,
            "--add-host", f"{DOMAIN_HOST}:host-gateway",
            "-e", f"TS_AUTHKEY={authkey}", "-e", "TS_HOSTNAME=e2e-node", "-e", "TS_USERSPACE=true",
            "-e", "TS_STATE_DIR=/var/lib/tailscale",
            "-e", f"TS_EXTRA_ARGS=--login-server={PUBLIC_URL}",
            TAILSCALE_IMAGE)
        try:
            node = wait_for("the Tailscale node to register",
                            lambda: next((n for n in self.api.nodes() if n.get("name") == "e2e-node"
                                          or n.get("givenName") == "e2e-node"), None), timeout=120)
        except AssertionError:
            print(run("docker", "logs", "--tail", "60", TS_CONTAINER, check=False).stderr)
            raise
        self.assertEqual(node["user"]["name"], "e2e-user")
        self.state["node_id"] = str(node["id"])
        self.assertIn("e2e-node", self.admin.get("/admin/machines").text)
        self.assertEqual(self.admin.get(f"/admin/machines/{node['id']}").status, 200)

    def test_14_rename_node_in_ui(self):
        if "node_id" not in self.state:
            self.skipTest("no node registered")
        node_id = self.state["node_id"]
        resp = self.admin.form(f"/admin/machines/{node_id}/rename", {"name": "e2e-renamed"})
        self.assertEqual(resp.status, 303, resp)
        self.assertIn("m=renamed", resp.location)
        node = next(n for n in self.api.nodes() if str(n["id"]) == node_id)
        self.assertEqual(node["givenName"], "e2e-renamed")

    # --- ACL -------------------------------------------------------------------
    def test_20_acl_check_and_save(self):
        resp = self.admin.form("/admin/acl", {"action": "check", "policy": POLICY}, page="/admin/acl")
        self.assertEqual(resp.status, 200, resp)
        self.assertIn("The policy is valid.", resp.text)
        resp = self.admin.form("/admin/acl", {"action": "save", "policy": POLICY}, page="/admin/acl")
        self.assertEqual(resp.status, 303, resp)
        self.assertIn("m=acl-saved", resp.location)
        saved = self.api.ok("GET", "/policy").get("policy", "")
        self.assertIn("headscale-easy e2e policy", saved)
        self.assertIn("group:e2e", self.admin.get("/admin/acl").text)

    def test_21_acl_invalid_policy_is_refused(self):
        bad = '{"acls": [{"action": "accept", "src": ["group:nope"], "dst": ["*:*"]}]}'
        resp = self.admin.form("/admin/acl", {"action": "save", "policy": bad}, page="/admin/acl")
        self.assertEqual(resp.status, 200, resp)
        self.assertIn("headscale-easy e2e policy", self.api.ok("GET", "/policy").get("policy", ""))

    def test_22_acl_simulator(self):
        resp = self.admin.form("/admin/acl/test", {"test_src": "e2e-user@", "test_dst": "e2e-user@", "test_port": "22"},
                               page="/admin/acl")
        self.assertEqual(resp.status, 200, resp)

    # --- DNS: configtest + restart through hs-helper ---------------------------
    def test_30_dns_invalid_is_refused(self):
        resp = self.admin.form("/admin/dns", {"section": "settings", "ns[]": ["not a server"]}, page="/admin/dns")
        self.assertEqual(resp.status, 400, resp)

    def test_31_dns_save_restarts_headscale(self):
        before = container_started_at("headscale")
        resp = self.admin.form("/admin/dns", {"section": "settings", "ns[]": ["1.1.1.1", "9.9.9.9"],
                                              "search[]": ["e2e.internal"]}, page="/admin/dns")
        self.assertEqual(resp.status, 303, resp)
        self.assertIn("m=dns-saved", resp.location)
        with open(os.path.join(ROOT, "headscale-config.yaml"), encoding="utf-8") as fh:
            config = fh.read()
        self.assertIn("9.9.9.9", config)
        self.assertIn("e2e.internal", config)
        # hs-helper restarted the container, and it came back healthy
        self.assertNotEqual(container_started_at("headscale"), before)
        wait_for("Headscale after the DNS change", lambda: self.api.call("GET", "/user").status == 200)
        self.assertIn("9.9.9.9", self.admin.get("/admin/dns").text)

    def test_32_status_page_through_helper(self):
        resp = self.admin.get("/admin/settings/status")
        self.assertEqual(resp.status, 200, resp)
        self.assertIn("headscale", resp.text.lower())

    # --- sessions ----------------------------------------------------------------
    def test_40_revoke_one_session(self):
        before = self.sids(self.admin)
        other = self.signed_in()
        new = self.sids(self.admin) - before
        self.assertEqual(len(new), 1, new)
        resp = self.admin.form("/admin/settings/sessions/revoke", {"sid": new.pop()})
        self.assertEqual(resp.status, 303, resp)
        self.assertIn("m=session-revoked", resp.location)
        # The revoked cookie no longer works; the admin's still does
        self.assertEqual(other.get("/admin/machines").status, 303)
        self.assertEqual(self.admin.get("/admin/machines").status, 200)

    def test_41_sign_out_everyone_else(self):
        others = [self.signed_in(), self.signed_in()]
        resp = self.admin.form("/admin/settings/sessions/revoke-all", {"scope": "everyone"})
        self.assertEqual(resp.status, 303, resp)
        for c in others:
            self.assertEqual(c.get("/admin/machines").status, 303)
        self.assertEqual(self.admin.get("/admin/machines").status, 200)

    def test_42_sign_out_everywhere(self):
        c = self.signed_in()
        resp = c.form("/admin/settings/sessions/revoke-all")
        self.assertEqual(resp.status, 303, resp)
        self.assertIn("/admin/login", resp.location)
        self.assertEqual(c.get("/admin/machines").status, 303)

    def test_43_logout(self):
        c = self.signed_in()
        resp = c.form("/admin/logout")
        self.assertEqual(resp.status, 303, resp)
        self.assertNotIn("hse_session", c.cookies)
        self.assertEqual(c.get("/admin/machines").status, 303)

    # --- backup + restore ----------------------------------------------------------
    def test_50_backup_and_restore(self):
        self.api.ok("POST", "/user", {"name": "e2e-backup"})
        backups = os.path.join(ROOT, "backups")
        existing = set(os.listdir(backups)) if os.path.isdir(backups) else set()
        run("./scripts/utils.sh", "backup")
        made = sorted(f for f in set(os.listdir(backups)) - existing if f.endswith(".tar.gz"))
        self.assertEqual(len(made), 1, made)
        archive = os.path.join(backups, made[0])

        # Lose data after the backup...
        user = self.api.user("e2e-backup")
        self.api.ok("DELETE", f"/user/{user['id']}")
        self.assertIsNone(self.api.user("e2e-backup"))
        nodes_before = {str(n["id"]) for n in self.api.nodes()}

        # ...and get it back
        run("./scripts/restore.sh", archive, "--yes")
        wait_for("the stack after the restore", stack_up, timeout=300)
        wait_for("Headscale's API after the restore", lambda: self.api.call("GET", "/user").status == 200,
                 timeout=120)
        self.assertIsNotNone(self.api.user("e2e-backup"))
        self.assertIsNotNone(self.api.user("e2e-user"))
        self.assertEqual({str(n["id"]) for n in self.api.nodes()}, nodes_before)
        self.assertIn("headscale-easy e2e policy", self.api.ok("GET", "/policy").get("policy", ""))
        # The web UI still accepts the API key from the restored .env
        self.assertEqual(self.signed_in().get("/admin/machines").status, 200)

    @classmethod
    def tearDownClass(cls):
        run("docker", "rm", "-f", TS_CONTAINER, check=False)


def authentik_sign_in(c: Client, url: str, username: str, password: str) -> str:
    """Drive Authentik's flows the way its web UI does (the flow executor API)
    until it redirects to the web UI's /admin/callback; return that URL."""
    for _hop in range(30):
        path = urllib.parse.urlsplit(url).path
        if path.startswith("/admin/callback"):
            return url
        m = re.match(r"/authentik/if/flow/([^/]+)/", path)
        if m:
            url = _run_flow(c, m.group(1), urllib.parse.urlsplit(url).query, username, password)
            continue
        resp = c.get(url)
        if resp.status in (301, 302, 303, 307, 308):
            url = resp.location
            continue
        raise AssertionError(f"unexpected Authentik response: {resp!r}")
    raise AssertionError("too many redirects signing in to Authentik")


def _run_flow(c: Client, slug: str, query: str, username: str, password: str) -> str:
    executor = f"/authentik/api/v3/flows/executor/{slug}/?" + urllib.parse.urlencode({"query": query})

    def call(body=None):
        headers = {"Accept": "application/json", "Origin": c.url("")}
        if "authentik_csrf" in c.cookies:
            headers["X-authentik-CSRF"] = c.cookies["authentik_csrf"]
        if body is None:
            resp = c.get(executor, headers=headers)
        else:
            headers["Content-Type"] = "application/json"
            resp = c.post(executor, body, headers=headers)
            if resp.status in (301, 302, 303, 307, 308):
                resp = c.get(resp.location, headers=headers)
        if resp.status != 200:
            raise AssertionError(f"Authentik flow {slug}: {resp!r}")
        return resp.json()

    challenge = call()
    for _step in range(10):
        component = challenge.get("component")
        if component == "xak-flow-redirect":
            return c.url(challenge["to"]) if challenge["to"].startswith("/") else challenge["to"]
        if component == "ak-stage-identification":
            challenge = call({"component": component, "uid_field": username})
        elif component == "ak-stage-password":
            challenge = call({"component": component, "password": password})
        elif component == "ak-stage-consent":
            challenge = call({"component": component, "token": challenge.get("token", "")})
        else:
            raise AssertionError(f"Authentik flow {slug}: unexpected stage {challenge}")
    raise AssertionError(f"Authentik flow {slug} did not finish")


if __name__ == "__main__":
    unittest.main()
