"""aio/render.py: the Python port of install.sh's config generators.

The compose target must be byte-identical to install.sh: the golden files in
tests/fixtures/render/<case>/ come from install.sh itself
(scripts/gen_render_goldens.sh). The aio target and the settings precedence are
checked by assertions.

    python3 -m unittest tests.test_render
"""
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from aio import render  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "render")
DEFAULTS = {
    "HEADSCALE_HTTP_PORT": "8080", "HEADSCALE_METRICS_PORT": "9090", "HEADSCALE_GRPC_PORT": "50443",
    "HEADSCALE_DERP_PORT": "3478", "IP_PREFIXES_V4": "100.64.0.0/10", "IP_PREFIXES_V6": "fd7a:115c:a1e0::/48",
    "LOG_LEVEL": "info", "TAILNET_NAME": "myorg", "DERP_USE_PUBLIC": "true", "ENABLE_OIDC": "false",
    "AUTH_PROVIDER": "none", "OIDC_SCOPE": "openid profile email", "ACME_EMAIL": "",
    "HEADSCALE_DB_TYPE": "sqlite", "HEADSCALE_PG_HOST": "", "HEADSCALE_PG_PORT": "5432",
    "HEADSCALE_PG_NAME": "headscale", "HEADSCALE_PG_USER": "headscale", "HEADSCALE_PG_PASS": "",
    "HEADSCALE_PG_SSLMODE": "disable", "NODE_KEY_EXPIRY": "180d",
    "OIDC_ISSUER_URL": "", "OIDC_CLIENT_ID": "", "OIDC_CLIENT_SECRET": "",
}


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def case_vars(case):
    """The install.sh variables of a case (its env.sh: KEY=value; KEY=value)."""
    v = dict(DEFAULTS)
    for key, value in re.findall(r"([A-Z0-9_]+)=([^;\n]*)", read(os.path.join(FIXTURES, case, "env.sh"))):
        v[key] = value.strip().strip('"')
    return v


def case_existing(case):
    path = os.path.join(FIXTURES, case, "existing-config.yaml")
    return read(path) if os.path.exists(path) else None


CASES = sorted(d for d in os.listdir(FIXTURES) if os.path.exists(os.path.join(FIXTURES, d, "env.sh")))


class GoldenTest(unittest.TestCase):
    """compose target == install.sh, byte for byte."""

    def test_there_are_cases(self):
        self.assertTrue({"sqlite_off", "letsencrypt_oidc", "postgres_selfsigned", "existing_dns"} <= set(CASES))

    def test_headscale_config(self):
        for case in CASES:
            with self.subTest(case=case):
                got = render.render_headscale_config(case_vars(case), "compose", case_existing(case))
                self.assertEqual(got, read(os.path.join(FIXTURES, case, "headscale-config.yaml")))

    def test_caddyfile(self):
        for case in CASES:
            with self.subTest(case=case):
                got = render.render_caddyfile(case_vars(case), "compose")
                self.assertEqual(got, read(os.path.join(FIXTURES, case, "Caddyfile")))

    def test_existing_dns_without_markers_keeps_base_domain_and_magic_dns(self):
        got = render.render_headscale_config(case_vars("existing_dns"), "compose", case_existing("existing_dns"))
        self.assertIn("  magic_dns: false\n  base_domain: corp.internal\n", got)

    def test_marked_blocks_survive_regeneration(self):
        got = render.render_headscale_config(case_vars("existing_marked"), "compose", case_existing("existing_marked"))
        self.assertIn("base_domain: edited.example", got)
        self.assertIn("  expiry: 30d\n", got)
        self.assertIn("    - /etc/headscale/derp.yaml\n", got)


class EnvsubstTest(unittest.TestCase):
    def test_only_braced_variables(self):
        self.assertEqual(render.envsubst("a ${X} b ${Y} c {X} $", {"X": "1"}), "a 1 b  c {X} $")

    def test_values_are_not_expanded_again(self):
        self.assertEqual(render.envsubst("${A}", {"A": "${B}", "B": "no"}), "${B}")


def aio_settings(**over):
    s = {"public_url": "http://localhost", "tls": "off", "tailnet_name": "acme"}
    s.update(over)
    return s


class AioTargetTest(unittest.TestCase):
    def conf(self, **over):
        v = render.to_vars(aio_settings(**over), "aio")
        return render.render_headscale_config(v, "aio", None, "/data"), render.render_caddyfile(v, "aio", "/data")

    def test_headscale_config_paths_and_listeners(self):
        cfg, _ = self.conf()
        self.assertIn("listen_addr: 127.0.0.1:8080", cfg)
        self.assertIn("metrics_listen_addr: 127.0.0.1:9090", cfg)
        self.assertIn('stun_listen_addr: "0.0.0.0:3478"', cfg)  # STUN must stay public
        self.assertIn("path: /data/headscale/db.sqlite", cfg)
        self.assertIn("private_key_path: /data/headscale/private.key", cfg)
        self.assertIn("unix_socket: /data/headscale/headscale.sock", cfg)
        self.assertNotIn("/var/lib/headscale", cfg)
        self.assertNotIn("/var/run/headscale", cfg)

    def test_trusted_proxies_is_loopback(self):
        cfg, _ = self.conf()
        self.assertIn("trusted_proxies:\n  - 127.0.0.1/32\n", cfg)
        self.assertNotIn("172.16.0.0/12", cfg)

    def test_caddyfile_upstreams_and_logs(self):
        _, caddy = self.conf()
        self.assertIn("reverse_proxy 127.0.0.1:8000", caddy)
        self.assertIn("reverse_proxy 127.0.0.1:8080", caddy)
        self.assertIn("output file /data/caddy/logs/access.log", caddy)
        self.assertNotIn("headscale-easy:", caddy)
        self.assertNotIn("authentik-server", caddy)
        self.assertNotIn("reverse_proxy headscale:", caddy)

    def test_register_route_without_oidc(self):
        _, caddy = self.conf()
        self.assertIn("redir @register /admin/register/{re.register.1} 302", caddy)

    def test_no_register_route_with_oidc(self):
        cfg, caddy = self.conf(oidc_issuer="https://idp.example.com", oidc_client_id="hs", oidc_client_secret="x")
        self.assertNotIn("@register", caddy)
        self.assertIn('issuer: "https://idp.example.com"', cfg)
        self.assertIn("email_verified_required: true", cfg)

    def test_tls_mapping(self):
        _, auto = self.conf(public_url="https://vpn.example.com", tls="auto", acme_email="a@example.com")
        self.assertIn("tls a@example.com", auto)
        self.assertIn("vpn.example.com {", auto)
        self.assertIn("http://vpn.example.com {\n    redir https://{host}{uri} permanent", auto)
        _, internal = self.conf(public_url="https://vpn.example.com", tls="internal")
        self.assertIn("tls internal", internal)
        _, off = self.conf()
        self.assertIn(":80 {", off)
        self.assertIn("# No TLS here", off)
        self.assertNotIn("redir https", off)

    def test_tls_defaults_follow_the_url_scheme(self):
        self.assertEqual(render.to_vars({"public_url": "http://localhost"})["SSL_MODE"], "none")
        v = render.to_vars({"public_url": "https://vpn.example.com", "acme_email": "a@example.com"})
        self.assertEqual(v["SSL_MODE"], "letsencrypt")

    def test_invalid_combinations(self):
        for bad in (
            {"public_url": "vpn.example.com"},
            {"public_url": "http://vpn.example.com", "tls": "auto", "acme_email": "a@example.com"},
            {"public_url": "https://localhost", "tls": "auto", "acme_email": "a@example.com"},
            {"public_url": "https://10.0.0.5", "tls": "auto", "acme_email": "a@example.com"},
            {"public_url": "https://vpn.example.com", "tls": "auto"},  # no ACME email
            {"public_url": "http://localhost", "tls": "maybe"},
            {"public_url": "http://localhost", "db_type": "postgres"},  # no host
            {"public_url": "http://localhost", "oidc_issuer": "https://idp.example.com"},  # no client id
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                render.to_vars(bad, "aio")

    def test_postgres(self):
        cfg, _ = self.conf(db_type="postgres", pg_host="db", pg_pass="fake", pg_sslmode="require")
        self.assertIn('    host: "db"\n', cfg)
        self.assertIn('    ssl: "require"\n', cfg)

    def test_setup_caddyfile_only_serves_the_wizard(self):
        out = render.render_setup_caddyfile("/data")
        self.assertIn(":80 {", out)
        self.assertIn("reverse_proxy 127.0.0.1:8000", out)
        self.assertNotIn("8080", out)
        self.assertIn("/data/caddy/logs/access.log", out)


class ConsoleEnvTest(unittest.TestCase):
    def test_paths_under_data(self):
        env = render.console_env(aio_settings(session_secret="fake-session", admin_email="a@example.com"), "/data")
        self.assertEqual(env["PUBLIC_URL"], "http://localhost")
        self.assertEqual(env["SESSION_SECRET"], "fake-session")
        for key in ("SESSIONS_DB", "AUDIT_DB", "ACCOUNTS_DB", "API_KEY_FILE", "MFA_MODE_FILE"):
            self.assertTrue(env[key].startswith("/data/console/"), key)
        self.assertEqual(env["HEADSCALE_DB"], "/data/headscale/db.sqlite")
        self.assertEqual(env["HEADSCALE_CONFIG"], "/data/config/config.yaml")
        self.assertEqual(env["HEADSCALE_DERP_FILE"], "/data/config/derp.yaml")
        self.assertEqual(env["HEADSCALE_DERP_FILE_IN_CONFIG"], "/data/config/derp.yaml")
        self.assertEqual(env["HELPER_SOCKET"], "/run/hse/helper.sock")
        self.assertEqual(env["HEADSCALE_URL"], "http://127.0.0.1:8080")
        self.assertEqual(env["HEADSCALE_METRICS_URL"], "http://127.0.0.1:9090/metrics")
        self.assertNotIn("HEADSCALE_API_KEY", env)  # the supervisor adds it

    def test_postgres_env(self):
        env = render.console_env(aio_settings(db_type="postgres", pg_host="db", pg_pass="fake"), "/data")
        self.assertEqual(env["HEADSCALE_DB_TYPE"], "postgres")
        self.assertEqual(env["HEADSCALE_PG_HOST"], "db")
        self.assertEqual(env["HEADSCALE_PG_PASSWORD"], "fake")


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "settings.json")

    def write(self, data):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def test_defaults(self):
        s = render.load_settings({}, self.path)
        self.assertEqual(s["tailnet_name"], "myorg")
        self.assertEqual(s["derp_port"], "3478")
        self.assertEqual(s["network_isolation"], "true")
        self.assertNotIn("public_url", s)

    def test_settings_file_over_defaults(self):
        self.write({"tailnet_name": "fromfile", "derp_use_public": False})
        s = render.load_settings({}, self.path)
        self.assertEqual(s["tailnet_name"], "fromfile")
        self.assertEqual(s["derp_use_public"], "false")

    def test_env_over_settings_file(self):
        self.write({"tailnet_name": "fromfile", "public_url": "https://file.example.com"})
        s = render.load_settings({"TAILNET_NAME": "fromenv"}, self.path)
        self.assertEqual(s["tailnet_name"], "fromenv")
        self.assertEqual(s["public_url"], "https://file.example.com")

    def test_empty_env_does_not_override(self):
        self.write({"tailnet_name": "fromfile"})
        self.assertEqual(render.load_settings({"TAILNET_NAME": ""}, self.path)["tailnet_name"], "fromfile")

    def test_env_names(self):
        env = {"HSE_PUBLIC_URL": "https://vpn.example.com", "HSE_TLS": "internal", "ACME_EMAIL": "a@example.com",
               "HSE_DERP_PORT": "3479", "HSE_ADMIN_EMAIL": "root@example.com", "OIDC_ISSUER": "https://idp",
               "OIDC_CLIENT_ID": "id", "OIDC_CLIENT_SECRET": "s", "HEADSCALE_DB_TYPE": "postgres",
               "HEADSCALE_PG_HOST": "db", "UI_LANG": "es", "TZ": "Europe/Madrid", "TAILNET_NAME": "t",
               "NODE_KEY_EXPIRY": "90d", "DERP_USE_PUBLIC": "false", "NETWORK_ISOLATION": "false"}
        s = render.load_settings(env, self.path)
        self.assertEqual(s["public_url"], "https://vpn.example.com")
        self.assertEqual(s["tls"], "internal")
        self.assertEqual(s["derp_port"], "3479")
        self.assertEqual(s["admin_email"], "root@example.com")
        self.assertEqual(s["oidc_issuer"], "https://idp")
        self.assertEqual(s["pg_host"], "db")
        self.assertEqual(s["ui_lang"], "es")
        self.assertEqual(s["node_key_expiry"], "90d")
        self.assertEqual(s["network_isolation"], "false")

    def test_base_domain_default_setting_and_env(self):
        base = {"public_url": "https://vpn.example.com", "tailnet_name": "acme", "acme_email": "a@example.com"}
        self.assertEqual(render.to_vars(base)["BASE_DOMAIN"], "hse.net")
        self.assertEqual(render.to_vars(base, "compose")["BASE_DOMAIN"], "acme.headscale.net")
        self.assertEqual(render.to_vars(dict(base, base_domain="Corp.Internal"))["BASE_DOMAIN"], "corp.internal")
        self.assertEqual(render.load_settings({"HSE_BASE_DOMAIN": "x.example"}, self.path)["base_domain"], "x.example")
        self.assertIn("base_domain: corp.internal", render.dns_block(render.to_vars(dict(base, base_domain="corp.internal"))))

    def test_base_domain_validation(self):
        for bad in ("nodots", "a b.com", "-x.com", "x..com", "a" * 64 + ".com", "vpn.example.com", "example.com"):
            with self.assertRaises(ValueError, msg=bad):
                render.to_vars({"public_url": "https://vpn.example.com", "acme_email": "a@example.com", "base_domain": bad})

    def derp_config(self, **extra):
        settings = {"public_url": "http://localhost", "tls": "off", **extra}
        with tempfile.TemporaryDirectory() as d:
            return read(render.render_all(settings, d)["config"])

    def test_derp_defaults_to_embedded(self):
        text = self.derp_config()
        self.assertIn("derp:\n  urls: []\n  server:\n    enabled: true", text)
        self.assertIn("  auto_update_enabled: false", text)
        self.assertNotIn("controlplane.tailscale.com", text)

    def test_derp_public_and_legacy_alias(self):
        for extra in ({"derp_mode": "public"}, {"derp_use_public": "true"}):
            text = self.derp_config(**extra)
            self.assertIn("controlplane.tailscale.com/derpmap/default", text, extra)
            self.assertIn("  auto_update_enabled: true", text)
        self.assertIn("urls: []", self.derp_config(derp_use_public="false"))
        # an explicit mode wins over the alias
        self.assertIn("urls: []", self.derp_config(derp_mode="embedded", derp_use_public="true"))

    def test_derp_custom(self):
        text = self.derp_config(derp_mode="custom", derp_url="https://derp.example.com/map.json")
        self.assertIn("urls:\n    - https://derp.example.com/map.json", text)
        self.assertIn("urls: []", self.derp_config(derp_mode="custom"))  # uploaded derp.yaml only

    def test_derp_validation_and_env(self):
        for extra in ({"derp_mode": "nope"}, {"derp_mode": "custom", "derp_url": "ftp://x"},
                      {"derp_mode": "custom", "derp_url": "https://a b"}):
            with self.assertRaises(ValueError, msg=extra):
                self.derp_config(**extra)
        self.assertEqual(render.load_settings({"HSE_DERP_MODE": "public"}, self.path)["derp_mode"], "public")
        self.assertNotIn("derp_mode", render.load_settings({}, self.path))

    def test_missing_or_corrupt_file_is_ignored(self):
        self.assertEqual(render.load_settings({}, self.path)["tailnet_name"], "myorg")
        with open(self.path, "w") as fh:
            fh.write("{not json")
        self.assertEqual(render.load_settings({}, self.path)["tailnet_name"], "myorg")

    def test_rejects_quotes_newlines_and_shell_characters(self):
        for bad in ('a"b', "a'b", "a$b", "a`b", "a\\b", "a\nb", "a\rb"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    render.load_settings({"TAILNET_NAME": bad}, self.path)
                self.write({"tailnet_name": bad})
                with self.assertRaises(ValueError):
                    render.load_settings({}, self.path)
        self.assertTrue(render.validate_env_text("plain value-1_2"))


class RenderAllTest(unittest.TestCase):
    def test_writes_files_with_private_modes_and_keeps_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            settings = aio_settings()
            p = render.render_all(settings, d)
            for key in ("config", "caddyfile", "derp"):
                self.assertEqual(os.stat(p[key]).st_mode & 0o777, 0o600, key)
            self.assertEqual(os.stat(os.path.dirname(p["config"])).st_mode & 0o777, 0o700)
            self.assertEqual(read(p["derp"]), "regions: {}\n")
            # The console edits the dns block; a re-render keeps it
            text = read(p["config"]).replace("base_domain: hse.net", "base_domain: edited.example")
            with open(p["config"], "w", encoding="utf-8") as fh:
                fh.write(text)
            render.render_all(settings, d)
            self.assertIn("base_domain: edited.example", read(p["config"]))
            self.assertIn("path: %s/headscale/db.sqlite" % d, read(p["config"]))

    def test_render_setup(self):
        with tempfile.TemporaryDirectory() as d:
            p = render.render_setup(d)
            self.assertIn("127.0.0.1:8000", read(p["caddyfile"]))


if __name__ == "__main__":
    unittest.main()
