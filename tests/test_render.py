"""aio/render.py: the config renderer of the all-in-one image.

The golden files in tests/fixtures/render/<case>/ are frozen outputs: any change to the
rendered config.yaml / Caddyfile shows up as a diff. After an intended change, render the case by hand
(render.render_headscale_config / render.render_caddyfile with the case's env.sh), replace the file and
review the diff: the test never writes them itself. The settings precedence and the rest are checked by assertions.

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
    """The template variables of a case (its env.sh: KEY=value; KEY=value)."""
    v = dict(DEFAULTS)
    for key, value in re.findall(r"([A-Z0-9_]+)=([^;\n]*)", read(os.path.join(FIXTURES, case, "env.sh"))):
        v[key] = value.strip().strip('"')
    return v


def case_existing(case):
    path = os.path.join(FIXTURES, case, "existing-config.yaml")
    return read(path) if os.path.exists(path) else None


CASES = sorted(d for d in os.listdir(FIXTURES) if os.path.exists(os.path.join(FIXTURES, d, "env.sh")))


class GoldenTest(unittest.TestCase):
    """The rendered files equal the frozen ones, byte for byte."""

    def check(self, name, case, got):
        self.assertEqual(got, read(os.path.join(FIXTURES, case, name)))

    def test_there_are_cases(self):
        self.assertTrue({"sqlite_off", "letsencrypt_oidc", "postgres_selfsigned", "existing_dns"} <= set(CASES))

    def test_headscale_config(self):
        for case in CASES:
            with self.subTest(case=case):
                got = render.render_headscale_config(case_vars(case), case_existing(case), "/data")
                self.check("headscale-config.yaml", case, got)

    def test_caddyfile(self):
        for case in CASES:
            with self.subTest(case=case):
                got = render.render_caddyfile(case_vars(case), "/data")
                self.check("Caddyfile", case, got)

    def test_existing_dns_without_markers_keeps_base_domain_and_magic_dns(self):
        got = render.render_headscale_config(case_vars("existing_dns"), case_existing("existing_dns"), "/data")
        self.assertIn("  magic_dns: false\n  base_domain: corp.internal\n", got)

    def test_marked_blocks_survive_regeneration(self):
        got = render.render_headscale_config(case_vars("existing_marked"), case_existing("existing_marked"), "/data")
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
        v = render.to_vars(aio_settings(**over))
        return render.render_headscale_config(v, None, "/data"), render.render_caddyfile(v, "/data")

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
                render.to_vars(bad)

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
        for key in ("SESSIONS_DB", "AUDIT_DB", "ACCOUNTS_DB", "API_KEY_FILE"):
            self.assertTrue(env[key].startswith("/data/console/"), key)
        self.assertEqual(env["HEADSCALE_DB"], "/data/headscale/db.sqlite")
        self.assertEqual(env["HEADSCALE_CONFIG"], "/data/config/config.yaml")
        self.assertEqual(env["HEADSCALE_DERP_FILE"], "/data/config/derp.yaml")
        self.assertEqual(env["HEADSCALE_DERP_FILE_IN_CONFIG"], "/data/config/derp.yaml")
        self.assertEqual(env["CONTROL_SOCKET"], "/run/hse/control.sock")
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
        self.write({"tailnet_name": "fromfile", "log_level": "debug"})
        s = render.load_settings({}, self.path)
        self.assertEqual(s["tailnet_name"], "fromfile")
        self.assertEqual(s["log_level"], "debug")

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

    def test_derp_public(self):
        text = self.derp_config(derp_mode="public")
        self.assertIn("controlplane.tailscale.com/derpmap/default", text)
        self.assertIn("  auto_update_enabled: true", text)
        self.assertIn("urls: []", self.derp_config(derp_mode="embedded"))

    def test_derp_custom(self):
        text = self.derp_config(derp_mode="custom", derp_url="https://derp.example.com/map.json")
        self.assertIn("urls:\n    - https://derp.example.com/map.json", text)
        self.assertIn("urls: []", self.derp_config(derp_mode="custom"))  # uploaded derp.yaml only

    def test_signup_mode_default_env_and_validation(self):
        base = {"public_url": "http://localhost", "tls": "off"}
        self.assertEqual(render.to_vars(base)["SIGNUP_MODE"], "off")
        self.assertEqual(render.load_settings({"HSE_SIGNUP": "invite"}, self.path)["signup_mode"], "invite")
        self.assertEqual(render.console_env(dict(base, signup_mode="open"), "/data")["HSE_SIGNUP"], "open")
        with self.assertRaises(ValueError):
            render.to_vars(dict(base, signup_mode="everyone"))

    def test_derp_validation_and_env(self):
        for extra in ({"derp_mode": "nope"}, {"derp_mode": "custom", "derp_url": "ftp://x"},
                      {"derp_mode": "custom", "derp_url": "https://a b"}):
            with self.assertRaises(ValueError, msg=extra):
                self.derp_config(**extra)
        self.assertEqual(render.load_settings({"HSE_DERP_MODE": "public"}, self.path)["derp_mode"], "public")
        self.assertNotIn("derp_mode", render.load_settings({}, self.path))

    def test_backup_schedule_validation(self):
        got = render.load_settings({}, self.path)
        self.assertEqual((got["backup_schedule"], got["backup_keep_days"]), ("0 3 * * *", "14"))  # on by default
        got = render.load_settings({"BACKUP_SCHEDULE": " */15  2-4 * * 1,3 ", "BACKUP_KEEP_DAYS": "30"}, self.path)
        self.assertEqual((got["backup_schedule"], got["backup_keep_days"]), ("*/15 2-4 * * 1,3", "30"))
        self.assertEqual(render.load_settings({"BACKUP_SCHEDULE": "off"}, self.path)["backup_schedule"], "off")
        self.assertEqual(render.load_settings({"BACKUP_SCHEDULE": "OFF"}, self.path)["backup_schedule"], "off")
        # a bad value stops a headless start instead of silently never backing up
        for env in ({"BACKUP_SCHEDULE": "0 3 * *"}, {"BACKUP_SCHEDULE": "0 3 * * * *"}, {"BACKUP_SCHEDULE": "a b c d e"},
                    {"BACKUP_SCHEDULE": "61 3 * * *"}, {"BACKUP_SCHEDULE": "*/0 * * * *"},
                    {"BACKUP_KEEP_DAYS": "0"}, {"BACKUP_KEEP_DAYS": "x"}, {"BACKUP_KEEP_DAYS": "99999"}):
            with self.subTest(env=env):
                with self.assertRaises(ValueError):
                    render.load_settings(env, self.path)
        # settings.json goes through the same check; the environment still wins over it
        with open(self.path, "w") as fh:
            json.dump({"tailnet_name": "myorg", "backup_schedule": "nope"}, fh)
        with self.assertRaises(ValueError):
            render.load_settings({}, self.path)
        self.assertEqual(render.load_settings({"BACKUP_SCHEDULE": "off"}, self.path)["backup_schedule"], "off")

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


class AdvancedEditionTest(unittest.TestCase):
    """Block 1 of PHASE4_EXECUTION_PLAN.md: a proxy in front, an external Authentik, a read-only PostgreSQL role."""

    BASE = {"public_url": "https://vpn.example.com", "tls": "off"}

    def caddy(self, **extra):
        s = dict(self.BASE, **extra)
        return render.render_caddyfile(render.to_vars(s), data_dir="/data")

    def config(self, **extra):
        s = dict(self.BASE, **extra)
        return render.render_headscale_config(render.to_vars(s))

    def test_https_url_with_tls_off_is_front_mode(self):
        self.assertEqual(render.to_vars(self.BASE)["SSL_MODE"], "front")
        self.assertEqual(render.to_vars({"public_url": "http://vpn.example.com", "tls": "off"})["SSL_MODE"], "none")
        self.assertEqual(render.to_vars({"public_url": "https://vpn.example.com", "tls": "internal"})["SSL_MODE"],
                         "selfsigned")
        text = self.caddy()
        self.assertIn(":80 {", text)
        self.assertIn("# HSTS: sent by the front proxy", text)
        self.assertEqual(text.count("header_up X-Forwarded-Proto https"), 2)  # console and Headscale
        self.assertNotIn("redir https://", text)

    def test_plain_http_keeps_todays_output(self):
        plain = render.render_caddyfile(render.to_vars({"public_url": "http://localhost", "tls": "off"}), "/data")
        self.assertNotIn("X-Forwarded-Proto https", plain)
        self.assertNotIn("trusted_proxies", plain)
        self.assertIn("X-Real-IP {remote_host}", plain)

    def test_trusted_proxies_reach_caddy_and_headscale(self):
        text = self.caddy(trusted_proxies="172.18.0.0/16, 10.0.0.5 ,fd00::/8")
        self.assertTrue(text.startswith("{\n    servers {\n        trusted_proxies static 172.18.0.0/16 10.0.0.5/32 fd00::/8\n        trusted_proxies_strict\n"))
        self.assertNotIn("{remote_host}", text)
        self.assertEqual(text.count("header_up X-Real-IP {client_ip}"), 2)
        cfg = self.config(trusted_proxies="172.18.0.0/16")
        self.assertIn("trusted_proxies:\n  - 127.0.0.1/32\n  - 172.18.0.0/16", cfg)
        self.assertIn("trusted_proxies:\n  - 127.0.0.1/32\n", self.config())  # nothing extra by default

    def test_trusted_proxies_validation(self):
        check = render.check_trusted_proxies
        self.assertEqual(check(""), [])
        self.assertEqual(check("10.0.0.1 10.0.0.1,10.0.0.1/32"), ["10.0.0.1/32"])
        for bad in ("nonsense", "10.0.0.0/33", "10.0.0.1;rm -rf", "1.2.3", "*"):
            with self.assertRaises(ValueError, msg=bad):
                check(bad)
        for open_to_all in ("0.0.0.0/0", "::/0", "10.0.0.1, 0.0.0.0/0"):
            with self.assertRaises(ValueError, msg=open_to_all):
                check(open_to_all)
            self.assertIn(next(x for x in check(open_to_all, allow_any=True) if x.endswith("/0")), ("0.0.0.0/0", "::/0"))
        with self.assertRaises(ValueError):
            check(",".join("10.0.0.%d" % i for i in range(17)))

    def test_trusted_proxies_cannot_inject_caddyfile_or_yaml(self):
        for evil in ("10.0.0.1}\nevil", "10.0.0.1\n}", '10.0.0.1"'):
            with self.assertRaises(ValueError):  # refused as a setting, and would be refused as a CIDR anyway
                render.load_settings({"HSE_PUBLIC_URL": "https://x.example.com", "HSE_TRUSTED_PROXIES": evil}, "/nonexistent")

    def test_external_authentik_route_and_provider(self):
        issuer = "https://vpn.example.com/authentik/application/o/headscale/"
        oidc = dict(oidc_issuer=issuer, oidc_client_id="id", oidc_client_secret="secret")
        without = self.caddy(**oidc)  # the issuer hints Authentik, but there is no upstream to route to
        self.assertNotIn("handle /authentik/*", without)
        self.assertEqual(render.to_vars(dict(self.BASE, **oidc))["AUTH_PROVIDER"], "authentik")
        text = self.caddy(authentik_upstream="authentik-server:9000", **oidc)
        self.assertIn("handle /authentik/* {\n        reverse_proxy authentik-server:9000 {", text)
        self.assertNotIn("/add-user", text)
        self.assertIn("redir /authentik /authentik/ 308", text)
        self.assertIn("email_verified_required: false", self.config(authentik_upstream="a:1", **oidc))
        other = self.caddy(authentik_upstream="my-auth:9443", **oidc)
        self.assertIn("reverse_proxy my-auth:9443 {", other)
        keycloak = dict(oidc_issuer="https://sso.example.com/realms/x", oidc_client_id="id", oidc_client_secret="s")
        self.assertEqual(render.to_vars(dict(self.BASE, **keycloak))["AUTH_PROVIDER"], "external")
        self.assertNotIn("handle /authentik/*", self.caddy(**keycloak))

    def test_authentik_upstream_validation(self):
        check = render.check_authentik_upstream
        self.assertEqual(check(""), "")
        self.assertEqual(check("authentik-server:9000"), "authentik-server:9000")
        for bad in ("authentik-server", "http://a:9000", "a:99999", "a b:9000", "a:9000}\nevil", ":9000", "a:0", "a:"):
            with self.assertRaises(ValueError, msg=bad):
                check(bad)

    def test_console_reads_postgresql_with_the_read_only_role(self):
        owner_pw, ro_pw = "o" * 10, "r" * 10  # built, not literals: a scanner takes a literal next to "pass" for a leak
        pg = {"public_url": "https://vpn.example.com", "tls": "off", "db_type": "postgres", "pg_host": "db", "pg_user": "headscale",
              "pg_pass": owner_pw, "pg_name": "headscale"}
        env = render.console_env(pg, "/data")
        self.assertEqual(env["HEADSCALE_PG_USER"], "headscale")
        self.assertEqual(env["HEADSCALE_PG_PASSWORD"], owner_pw)
        ro = render.console_env(dict(pg, pg_ro_user="headscale_ro", pg_ro_pass=ro_pw), "/data")
        self.assertEqual(ro["HEADSCALE_PG_USER"], "headscale_ro")
        self.assertEqual(ro["HEADSCALE_PG_PASSWORD"], ro_pw)
        self.assertNotIn(owner_pw, repr(ro))
        cfg = render.render_headscale_config(render.to_vars(dict(pg, pg_ro_user="headscale_ro", pg_ro_pass=ro_pw)))
        self.assertIn(owner_pw, cfg)  # Headscale itself keeps the owner
        self.assertNotIn(ro_pw, cfg)
        for half in ({"pg_ro_user": "headscale_ro"}, {"pg_ro_pass": "x"}):
            with self.assertRaises(ValueError):
                render.to_vars(dict(pg, **half))

    def test_the_console_never_gets_an_authentik_api_setting(self):
        base = {"public_url": "https://vpn.example.com", "tls": "off", "oidc_client_id": "i", "oidc_client_secret": "s"}
        for issuer in ("https://vpn.example.com/authentik/application/o/h/", "https://sso.example.com/realms/x"):
            env = render.console_env(dict(base, oidc_issuer=issuer, authentik_upstream="a:1"), "/data")
            for name in ("AUTHENTIK_URL", "AUTHENTIK_API_TOKEN", "MFA_MODE_FILE"):
                self.assertNotIn(name, env)
            self.assertEqual(env["OIDC_ISSUER"], issuer)
        for name in ("AUTHENTIK_URL", "AUTHENTIK_API_TOKEN"):  # not even a setting any more
            self.assertNotIn(name, [v for v, _d in render.SETTINGS.values()])

    def test_new_settings_are_read_from_the_environment(self):
        s = render.load_settings({"HSE_PUBLIC_URL": "https://x.example.com", "HSE_TRUSTED_PROXIES": "10.0.0.1",
                                  "HSE_AUTHENTIK_UPSTREAM": "a:9000", "HEADSCALE_PG_RO_USER": "ro"}, "/nonexistent")
        self.assertEqual((s["trusted_proxies"], s["authentik_upstream"], s["pg_ro_user"]), ("10.0.0.1", "a:9000", "ro"))

    def test_a_proxy_in_front_is_told_about_udp_and_the_trusted_proxies(self):
        notes = render.config_warnings(self.BASE)  # https with TLS off
        self.assertTrue(any("UDP 3478" in w for w in notes))
        self.assertTrue(any("HSE_TRUSTED_PROXIES" in w and "/32" in w for w in notes))
        configured = render.config_warnings(dict(self.BASE, trusted_proxies="10.0.0.5/32"))
        self.assertTrue(any("UDP 3478" in w for w in configured))
        self.assertFalse(any("without HSE_TRUSTED_PROXIES" in w for w in configured))
        self.assertEqual(render.config_warnings({"public_url": "http://localhost", "tls": "off"}), [])  # plain http

    def test_warnings(self):
        self.assertEqual(render.config_warnings({"public_url": "http://localhost", "tls": "off"}), [])
        self.assertTrue(any("read-only role" in w for w in render.config_warnings({"db_type": "postgres"})))
        self.assertEqual(render.config_warnings({"db_type": "postgres", "pg_ro_user": "ro"}), [])
        self.assertEqual(render.config_warnings({"authentik_upstream": "a:1", "public_url": "http://localhost", "tls": "off"}), [])
        self.assertTrue(any("terminates TLS" in w for w in render.config_warnings({"trusted_proxies": "1.1.1.1", "tls": "auto"})))


class OidcAccessTest(unittest.TestCase):
    """Who may sign in through the provider, and who is admin in the console (found while running the examples)."""
    BASE = {"public_url": "https://vpn.example.com", "tls": "off", "oidc_issuer": "https://sso.example.com/realms/x",
            "oidc_client_id": "id", "oidc_client_secret": "s"}

    def config(self, **extra):
        return render.render_headscale_config(render.to_vars(dict(self.BASE, **extra)))

    def test_allowed_lists_reach_headscale(self):
        cfg = self.config(oidc_allowed_domains="example.com, corp.example.org", oidc_allowed_users="boss@example.com",
                          oidc_allowed_groups="vpn-users,authentik Admins")
        self.assertIn('  allowed_domains:\n    - "example.com"\n    - "corp.example.org"\n', cfg)
        self.assertIn('  allowed_users:\n    - "boss@example.com"\n', cfg)
        self.assertIn('  allowed_groups:\n    - "vpn-users"\n    - "authentik Admins"\n', cfg)
        self.assertLess(cfg.index("email_verified_required"), cfg.index("allowed_domains"))
        self.assertLess(cfg.index("allowed_groups"), cfg.index("pkce:"))

    def test_nothing_is_added_without_them(self):
        self.assertNotIn("allowed_", self.config())

    def test_validation_and_injection(self):
        for kind, bad in (("domains", "exa mple.com"), ("domains", 'x.com"\n  evil: 1'), ("domains", "-x.com"),
                          ("users", "no-at-sign"), ("users", "a@b.com, c"), ("users", "a b@c.com"),
                          ("groups", "g;rm"), ("groups", "g\nx"), ("groups", "-leading"), ("groups", "a" * 101)):
            with self.assertRaises(ValueError, msg=(kind, bad)):
                render.check_allowed(kind, bad)
        self.assertEqual(render.check_allowed("domains", ""), [])
        self.assertEqual(render.check_allowed("groups", "a, ,a,b"), ["a", "b"])
        with self.assertRaises(ValueError):  # quotes are refused already when the setting is read
            render.load_settings({"HSE_PUBLIC_URL": "https://x.example.com", "HSE_OIDC_ALLOWED_DOMAINS": 'x.com", evil: "'}, "/nonexistent")

    def test_console_gets_groups_and_scope(self):
        env = render.console_env(dict(self.BASE, oidc_scope="openid profile email groups", portal_admin_groups="vpn-admins",
                                      portal_network_admin_groups="net", portal_auditor_groups="aud"), "/data")
        self.assertEqual((env["PORTAL_ADMIN_GROUPS"], env["PORTAL_NETWORK_ADMIN_GROUPS"], env["PORTAL_AUDITOR_GROUPS"]),
                         ("vpn-admins", "net", "aud"))
        self.assertEqual(env["OIDC_SCOPE"], "openid profile email groups")
        plain = render.console_env(self.BASE, "/data")
        self.assertNotIn("PORTAL_ADMIN_GROUPS", plain)  # the console's own default stays
        self.assertEqual(plain["OIDC_SCOPE"], "openid profile email")
        self.assertNotIn("OIDC_SCOPE", render.console_env({"public_url": "https://x.example.com", "tls": "off"}, "/data"))

    def test_the_new_settings_come_from_the_environment(self):
        s = render.load_settings({"HSE_PUBLIC_URL": "https://x.example.com", "HSE_OIDC_ALLOWED_DOMAINS": "example.com",
                                  "PORTAL_ADMIN_GROUPS": "g1,g2"}, "/nonexistent")
        self.assertEqual((s["oidc_allowed_domains"], s["portal_admin_groups"]), ("example.com", "g1,g2"))


if __name__ == "__main__":
    unittest.main()


class ConsoleTuningTests(unittest.TestCase):
    """The console knobs reach the console process, and only when set."""

    KNOBS = {"EXPIRY_WARNING_DAYS": "7", "INACTIVE_DAYS": "60", "AUTO_RENAME_LOCALHOST": "false",
             "RENAME_INTERVAL": "10", "AUDIT_RETENTION_DAYS": "0", "STATUS_UPDATE_CHECK": "false",
             "SIGNIN_RATE_LIMIT": "5", "SIGNIN_RATE_WINDOW": "300", "PORTAL_API_KEY_LOGIN": "true",
             "BACKUP_UPLOAD_MAX_MB": "2048"}

    def _settings(self, env):
        base = {"HSE_PUBLIC_URL": "https://vpn.example.test", "HSE_TLS": "off"}
        with tempfile.TemporaryDirectory() as tmp:
            return render.load_settings(env={**base, **env}, path=os.path.join(tmp, "settings.json"))

    def test_set_variables_reach_the_console(self):
        got = render.console_env(self._settings(self.KNOBS))
        for name, value in self.KNOBS.items():
            self.assertEqual(got.get(name), value, name)

    def test_unset_variables_are_left_to_the_console_default(self):
        got = render.console_env(self._settings({}))
        for name in self.KNOBS:
            self.assertNotIn(name, got, name)

    def test_every_tuning_key_is_a_known_setting(self):
        for key in render.CONSOLE_TUNING:
            self.assertIn(key, render.SETTINGS)
