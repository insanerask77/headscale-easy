"""aio/supervisor.py: process management and the helper protocol on a local socket.

Real child processes, but fake ``headscale`` / ``caddy`` / console executables
(small Python scripts) so nothing needs to be installed:

- restart with exponential backoff (reset after a healthy run), log prefixes;
- SIGTERM stops everything in order (console -> caddy -> headscale);
- the console waits for ``headscale health`` and gets the API key;
- setup mode (no settings): wizard + caddy only, then run mode when the wizard exits 0;
- the helper contract (tests/test_control.py) served by the supervisor.

    python3 -m unittest tests.test_supervisor
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from aio import restore as aio_restore  # noqa: E402
from aio import supervisor as sup  # noqa: E402
from test_control import call  # noqa: E402
import backup_fixtures as fx  # noqa: E402
from test_restore import FakeBackup  # noqa: E402

SHORT_TMP = "/tmp" if os.path.isdir("/tmp") else None

FAKE_HEADSCALE = """#!%(py)s
import json, os, signal, sys, time
args = sys.argv[1:]
cmd = args[0]
if cmd == "version":
    print("headscale version v0.26.1")
    sys.exit(0)
cfg = args[args.index("-c") + 1]
state = os.path.join(os.path.dirname(os.path.dirname(cfg)), "fake")
def flag(name):
    return os.path.exists(os.path.join(state, name))
def log(name, text):
    with open(os.path.join(state, name), "a") as fh:
        fh.write(text + "\\n")
if cmd == "serve":
    log("serves", str(os.getpid()))
    open(os.path.join(state, "up"), "w").close()
    def term(*_):
        log("stops", "headscale")
        try:
            os.unlink(os.path.join(state, "up"))
        except OSError:
            pass
        sys.exit(0)
    signal.signal(signal.SIGTERM, term)
    print("headscale serving", flush=True)
    while True:
        time.sleep(0.05)
elif cmd == "health":
    sys.exit(0 if flag("up") and not flag("health_block") else 1)
elif cmd == "configtest":
    if flag("configtest_fail"):
        print("bad config")
        sys.exit(1)
    print("Config OK")
elif cmd == "apikeys":
    log("apikeys", " ".join(args))
    print("hskey-fake.abc")
else:
    sys.exit(2)
"""

FAKE_CADDY = """#!%(py)s
import json, os, signal, sys, time
cfg = sys.argv[sys.argv.index("--config") + 1]
state = os.path.join(os.path.dirname(os.path.dirname(cfg)), "fake")
with open(os.path.join(state, "caddy_env.json"), "w") as fh:
    json.dump(dict(os.environ), fh)
with open(os.path.join(state, "caddy_starts"), "a") as fh:
    fh.write(str(os.getpid()) + "\\n")
def term(*_):
    with open(os.path.join(state, "stops"), "a") as fh:
        fh.write("caddy\\n")
    sys.exit(0)
signal.signal(signal.SIGTERM, term)
print("caddy serving", flush=True)
while True:
    time.sleep(0.05)
"""

FAKE_CONSOLE = """import json, os, signal, sys, time
state = os.path.join(os.path.dirname(os.path.dirname(os.environ["SESSIONS_DB"])), "fake")
with open(os.path.join(state, "console_env.json"), "w") as fh:
    json.dump(dict(os.environ), fh)
with open(os.path.join(state, "console_starts"), "a") as fh:
    fh.write("up" if os.path.exists(os.path.join(state, "up")) else "headscale-down")
    fh.write("\\n")
def term(*_):
    with open(os.path.join(state, "stops"), "a") as fh:
        fh.write("console\\n")
    sys.exit(0)
signal.signal(signal.SIGTERM, term)
print("console serving", flush=True)
while True:
    time.sleep(0.05)
"""

# The wizard of setup mode: writes the settings the real one would, then exits 0
FAKE_WIZARD = """import json, os, sys, time
data = os.environ["HSE_DATA_DIR"]
print("wizard up", flush=True)
delay = os.path.join(data, "fake", "wizard_delay")
time.sleep(float(open(delay).read()) if os.path.exists(delay) else 3600)
with open(os.path.join(data, "config", "settings.json"), "w") as fh:
    json.dump({"public_url": "http://localhost", "tls": "off"}, fh)
sys.exit(0)
"""


def wait_for(cond, timeout=10.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = cond()
        if value:
            return value
        time.sleep(step)
    return cond()


class Fakes:
    """Fake executables in a temp dir, plus the data dir they share."""

    def __init__(self, tmp):
        self.bin = os.path.join(tmp, "bin")
        self.data = os.path.join(tmp, "data")
        self.run = tempfile.mkdtemp(prefix="hse", dir=SHORT_TMP)
        os.makedirs(self.bin)
        os.makedirs(os.path.join(self.data, "fake"))
        for name, body in (("headscale", FAKE_HEADSCALE), ("caddy", FAKE_CADDY)):
            self.write(os.path.join(self.bin, name), body % {"py": sys.executable}, 0o755)
        self.console = self.write(os.path.join(self.bin, "console.py"), FAKE_CONSOLE)
        self.wizard = self.write(os.path.join(self.bin, "wizard.py"), FAKE_WIZARD)

    @staticmethod
    def write(path, body, mode=0o644):
        with open(path, "w") as fh:
            fh.write(body)
        os.chmod(path, mode)
        return path

    @property
    def state(self):
        return os.path.join(self.data, "fake")

    def flag(self, name, on=True):
        path = os.path.join(self.state, name)
        if on:
            open(path, "w").close()
        elif os.path.exists(path):
            os.unlink(path)

    def lines(self, name):
        try:
            with open(os.path.join(self.state, name)) as fh:
                return fh.read().splitlines()
        except OSError:
            return []

    def json(self, name):
        with open(os.path.join(self.state, name)) as fh:
            return json.load(fh)

    def env(self, **extra):
        env = {"PATH": os.environ["PATH"], "HSE_HEADSCALE_BIN": os.path.join(self.bin, "headscale"),
               "HSE_CADDY_BIN": os.path.join(self.bin, "caddy"), "HSE_CONSOLE_APP": self.console,
               "HSE_WIZARD_APP": self.wizard, "HSE_DATA_DIR": self.data, "HSE_RUN_DIR": self.run,
               "HSE_TEMPLATES_DIR": os.path.join(ROOT, "templates"), "HSE_START_TIMEOUT": "10"}
        env.update(extra)
        return env


class FakeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=SHORT_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.fakes = Fakes(self.tmp.name)
        self.addCleanup(lambda: __import__("shutil").rmtree(self.fakes.run, ignore_errors=True))


# --- Child: restart, backoff, signals ---------------------------------------------------

def py(code):
    return [sys.executable, "-c", code]


class ChildTest(unittest.TestCase):
    def child(self, argv, **kw):
        self.lines = []
        opts = dict(backoff_min=0.05, backoff_max=0.2, healthy_after=5, sink=self.lines.append, stop_timeout=1)
        opts.update(kw)
        child = sup.Child("fake", argv, None, **opts)
        self.addCleanup(child.stop)
        return child

    def delays(self):
        return [float(ln.split(" in ")[1].rstrip("s")) for ln in self.lines if "restarting fake in" in ln]

    def test_output_gets_a_prefix(self):
        child = self.child(py("print('hello', flush=True); import time; time.sleep(30)"))
        child.start()
        self.assertTrue(wait_for(lambda: "[fake] hello" in self.lines))

    def test_crashing_child_is_restarted_with_exponential_backoff(self):
        child = self.child(py("import sys; sys.exit(3)"))
        child.start()
        self.assertTrue(wait_for(lambda: len(self.delays()) >= 5))
        self.assertEqual(self.delays()[:5], [0.05, 0.1, 0.2, 0.2, 0.2])  # capped at the maximum
        self.assertEqual(child.last_exit, 3)
        self.assertGreaterEqual(child.starts, 5)
        self.assertTrue(any("exited with code 3" in ln for ln in self.lines))

    def test_backoff_resets_after_a_healthy_run(self):
        child = self.child(py("import time; time.sleep(0.3)"), healthy_after=0.2)
        child.start()
        self.assertTrue(wait_for(lambda: len(self.delays()) >= 3))
        self.assertEqual(self.delays()[:3], [0.05, 0.05, 0.05])

    def test_unstartable_command_is_retried(self):
        child = self.child(["/nonexistent/binary"])
        child.start()
        self.assertTrue(wait_for(lambda: len(self.delays()) >= 2))
        self.assertTrue(any("could not start" in ln for ln in self.lines))

    def test_failing_prepare_counts_as_a_failure(self):
        calls = []

        def prepare():
            calls.append(1)
            raise RuntimeError("not ready")

        self.lines = []
        child = sup.Child("fake", prepare=prepare, backoff_min=0.05, backoff_max=0.1, sink=self.lines.append)
        self.addCleanup(child.stop)
        child.start()
        self.assertTrue(wait_for(lambda: len(calls) >= 3))
        self.assertEqual(child.starts, 0)

    def test_stop_sends_sigterm_and_does_not_restart(self):
        child = self.child(py(
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *_: (print('got term', flush=True), sys.exit(0)))\n"
            "print('ready', flush=True)\ntime.sleep(30)"))
        child.start()
        self.assertTrue(wait_for(lambda: "[fake] ready" in self.lines))
        child.stop()
        self.assertIn("[fake] got term", self.lines)
        self.assertFalse(child.running)
        self.assertEqual(child.state, "stopped")
        time.sleep(0.3)
        self.assertEqual(child.starts, 1)

    def test_stop_kills_a_child_that_ignores_sigterm(self):
        child = self.child(py(
            "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\ntime.sleep(30)"), stop_timeout=0.3)
        child.start()
        self.assertTrue(wait_for(lambda: "[fake] ready" in self.lines))
        started = time.monotonic()
        child.stop()
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(child.running)
        self.assertTrue(any("killing it" in ln for ln in self.lines))

    def test_stop_reaches_grandchildren(self):
        # the child leads its own process group, so a shell's children die with it
        child = self.child(["/bin/sh", "-c", "sleep 30 & echo $! ; wait"])
        child.start()
        pid = int(wait_for(lambda: next((ln.split()[1] for ln in self.lines if ln.startswith("[fake] ")
                                         and ln.split()[1].isdigit()), None)))
        child.stop()
        self.assertTrue(wait_for(lambda: not os.path.exists("/proc/%d" % pid) or
                                 open("/proc/%d/stat" % pid).read().split()[2] == "Z"))

    def test_restart_starts_a_new_process_without_backoff(self):
        child = self.child(py("import time; time.sleep(30)"), backoff_min=5, backoff_max=5)
        child.start()
        first = wait_for(lambda: child.pid)
        child.restart()
        second = wait_for(lambda: child.pid not in (None, first) and child.pid, timeout=3)
        self.assertTrue(second)
        self.assertEqual(child.starts, 2)

    def test_on_exit_can_end_supervision(self):
        child = self.child(py("import sys; sys.exit(0)"), on_exit=lambda code: code == 0)
        child.start()
        self.assertTrue(wait_for(lambda: child.state == "done"))
        self.assertEqual(child.starts, 1)


# --- Supervisor in-process ---------------------------------------------------------------

class SupervisorMixin:
    def make(self, **env):
        env = {"HSE_PUBLIC_URL": "http://localhost", "HSE_TLS": "off", **env}
        patches = [mock.patch.object(sup, "HEADSCALE_BIN", os.path.join(self.fakes.bin, "headscale")),
                   mock.patch.object(sup, "CADDY_BIN", os.path.join(self.fakes.bin, "caddy")),
                   mock.patch.object(sup, "CONSOLE_CMD", [sys.executable, self.fakes.console]),
                   mock.patch.object(sup, "WIZARD_CMD", [sys.executable, self.fakes.wizard]),
                   mock.patch.object(sup, "START_TIMEOUT", 10.0),
                   mock.patch.object(sup.render, "TEMPLATES_DIR", os.path.join(ROOT, "templates"))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        lines = []
        s = sup.Supervisor(data_dir=self.fakes.data, env={"PATH": os.environ["PATH"], **env}, sink=lines.append,
                           run_dir=self.fakes.run,
                           backoff={"backoff_min": 0.05, "backoff_max": 0.2, "healthy_after": 5},
                           stop_timeouts={"console": 2, "wizard": 2, "caddy": 2, "headscale": 2})
        s.lines = lines
        s.serve_helper()
        self.addCleanup(s.shutdown)
        return s


class SupervisorTest(SupervisorMixin, FakeCase):
    def test_mode_detection(self):
        self.assertEqual(self.make().detect_mode(), "run")
        self.assertEqual(self.make(HSE_PUBLIC_URL="").detect_mode(), "setup")

    def test_mode_from_settings_json(self):
        s = self.make(HSE_PUBLIC_URL="")
        os.makedirs(os.path.join(self.fakes.data, "config"))
        with open(s.paths["settings"], "w") as fh:
            json.dump({"public_url": "http://localhost", "tls": "off"}, fh)
        self.assertEqual(s.detect_mode(), "run")

    def test_run_mode_starts_the_three_processes_and_the_data_layout(self):
        s = self.make()
        s.start_children()
        self.assertEqual(s.mode, "run")
        self.assertTrue(wait_for(lambda: all(c.running for c in s.children.values()) and len(s.children) == 3))
        self.assertEqual(sorted(s.children), ["caddy", "console", "headscale"])
        for sub in ("headscale", "caddy/logs", "console", "config", "backups"):
            path = os.path.join(self.fakes.data, sub)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o700, sub)
        for name in ("config.yaml", "Caddyfile", "derp.yaml"):
            self.assertEqual(os.stat(os.path.join(self.fakes.data, "config", name)).st_mode & 0o777, 0o600)

    def test_logs_have_prefixes(self):
        s = self.make()
        s.start_children()
        for line in ("[headscale] headscale serving", "[caddy] caddy serving", "[console] console serving"):
            self.assertTrue(wait_for(lambda: line in s.lines), line)

    def test_console_waits_for_headscale_health_and_gets_the_api_key(self):
        self.fakes.flag("health_block")
        s = self.make()
        s.start_children()
        self.assertTrue(wait_for(lambda: s.children["headscale"].running))
        time.sleep(0.6)
        self.assertEqual(self.fakes.lines("console_starts"), [])  # still waiting
        self.fakes.flag("health_block", False)
        self.assertTrue(wait_for(lambda: self.fakes.lines("console_starts")))
        self.assertEqual(self.fakes.lines("console_starts"), ["up"])
        env = self.fakes.json("console_env.json")
        self.assertEqual(env["HEADSCALE_API_KEY"], "hskey-fake.abc")
        key = os.path.join(self.fakes.data, "console", "api-key")
        self.assertEqual(open(key).read().strip(), "hskey-fake.abc")
        self.assertEqual(os.stat(key).st_mode & 0o777, 0o600)
        self.assertEqual(self.fakes.lines("apikeys"), ["apikeys create --expiration 90d -c %s/config/config.yaml"
                                                       % self.fakes.data])

    def test_existing_api_key_is_kept(self):
        os.makedirs(os.path.join(self.fakes.data, "console"))
        with open(os.path.join(self.fakes.data, "console", "api-key"), "w") as fh:
            fh.write("hskey-renewed.xyz\n")
        s = self.make()
        s.start_children()
        self.assertTrue(wait_for(lambda: self.fakes.lines("console_starts")))
        self.assertEqual(self.fakes.json("console_env.json")["HEADSCALE_API_KEY"], "hskey-renewed.xyz")
        self.assertEqual(self.fakes.lines("apikeys"), [])

    def test_console_environment(self):
        s = self.make(HSE_ADMIN_EMAIL="admin@example.com", HSE_ADMIN_PASSWORD="fake-password",
                      OIDC_ISSUER="https://idp.example.com", OIDC_CLIENT_ID="hs", OIDC_CLIENT_SECRET="fake-secret")
        s.start_children()
        self.assertTrue(wait_for(lambda: self.fakes.lines("console_starts")))
        env = self.fakes.json("console_env.json")
        self.assertEqual(env["PUBLIC_URL"], "http://localhost")
        self.assertEqual(env["HSE_ADMIN_PASSWORD"], "fake-password")
        self.assertEqual(env["HELPER_SOCKET"], "/run/hse/helper.sock")
        self.assertEqual(env["ACCOUNTS_DB"], self.fakes.data + "/console/accounts.db")
        self.assertGreaterEqual(len(env["SESSION_SECRET"]), 32)
        # the generated secret is persisted, so sessions survive a restart
        path = os.path.join(self.fakes.data, "config", "session-secret")
        self.assertEqual(open(path).read().strip(), env["SESSION_SECRET"])
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_caddy_and_headscale_do_not_get_the_console_secrets(self):
        s = self.make(HSE_ADMIN_PASSWORD="fake-password", OIDC_CLIENT_SECRET="fake-secret",
                      OIDC_ISSUER="https://idp.example.com", OIDC_CLIENT_ID="hs")
        s.start_children()
        self.assertTrue(wait_for(lambda: self.fakes.lines("caddy_starts")))
        env = self.fakes.json("caddy_env.json")
        self.assertNotIn("HSE_ADMIN_PASSWORD", env)
        self.assertNotIn("OIDC_CLIENT_SECRET", env)
        self.assertEqual(env["XDG_DATA_HOME"], self.fakes.data + "/caddy")

    def test_killed_child_is_restarted(self):
        s = self.make()
        s.start_children()
        self.assertTrue(wait_for(lambda: s.children["headscale"].pid))
        first = s.children["headscale"].pid
        os.kill(first, signal.SIGKILL)
        self.assertTrue(wait_for(lambda: s.children["headscale"].pid not in (None, first)))
        self.assertTrue(any("headscale exited with code -9" in ln for ln in s.lines))

    def test_ordered_stop(self):
        s = self.make()
        s.start_children()
        self.assertTrue(wait_for(lambda: self.fakes.lines("console_starts") and s.children["caddy"].running))
        s.stop_children()
        self.assertEqual(self.fakes.lines("stops"), ["console", "caddy", "headscale"])
        self.assertFalse(any(c.running for c in s.children.values()))

    def test_setup_mode_runs_wizard_and_caddy_only(self):
        s = self.make(HSE_PUBLIC_URL="")
        s.start_children()
        self.assertEqual(s.mode, "setup")
        self.assertTrue(wait_for(lambda: all(c.running for c in s.children.values())))
        self.assertEqual(sorted(s.children), ["caddy", "wizard"])
        self.assertIn(":80 {", open(os.path.join(self.fakes.data, "config", "Caddyfile")).read())
        self.assertIn("127.0.0.1:8000", open(os.path.join(self.fakes.data, "config", "Caddyfile")).read())
        self.assertEqual(self.fakes.lines("serves"), [])  # no headscale yet

    def test_wizard_finishing_switches_to_run_mode(self):
        s = self.make(HSE_PUBLIC_URL="")
        with open(os.path.join(self.fakes.state, "wizard_delay"), "w") as fh:
            fh.write("0.3")
        s.start_children()
        self.assertTrue(wait_for(lambda: s.mode == "run" and "console" in s.children and
                                 self.fakes.lines("console_starts"), timeout=15))
        self.assertEqual(sorted(s.children), ["caddy", "console", "headscale"])
        self.assertIn("reverse_proxy 127.0.0.1:8080", open(os.path.join(self.fakes.data, "config", "Caddyfile")).read())

    def test_invalid_settings_stop_the_start(self):
        s = self.make(HSE_PUBLIC_URL="https://localhost", HSE_TLS="auto")
        with self.assertRaises(ValueError):
            s.start_children()


# --- The helper contract, served by the supervisor ----------------------------------------

class HelperSocketTest(SupervisorMixin, FakeCase):
    def setUp(self):
        super().setUp()
        self.s = self.make()
        self.s.start_children()
        self.assertTrue(wait_for(lambda: self.s.children["headscale"].running and
                                 self.fakes.lines("console_starts")))
        self.sock = self.s.helper_socket

    def req(self, method, target, **kw):
        return call(self.sock, method, target, **kw)

    def test_socket_is_not_world_accessible(self):
        self.assertEqual(os.stat(self.sock).st_mode & 0o777, 0o660)

    def test_unknown_paths_are_404(self):
        for path in ("/", "/exec", "/containers/json", "/configtest/", "/status/x", "/reload", "/../etc/passwd"):
            with self.subTest(path=path):
                self.assertEqual(self.req("GET", path)[0], 404)

    def test_wrong_methods_are_405(self):
        for method, path, allow in (("GET", "/configtest", "POST"), ("GET", "/restart", "POST"),
                                    ("POST", "/status", "GET"), ("DELETE", "/status", "GET")):
            with self.subTest(method=method, path=path):
                code, headers, _ = self.req(method, path)
                self.assertEqual(code, 405)
                self.assertEqual(headers.get("Allow"), allow)

    def test_query_strings_and_bodies_are_refused(self):
        self.assertEqual(self.req("GET", "/status?x=1")[0], 400)
        self.assertEqual(self.req("POST", "/restart?t=0")[0], 400)
        self.assertEqual(self.req("POST", "/configtest", body=b'{"cmd": "sh"}',
                                  headers={"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.fakes.lines("stops"), [])

    def test_configtest_ok_and_failure(self):
        code, _, data = self.req("POST", "/configtest")
        self.assertEqual((code, data), (200, {"ok": True, "output": "Config OK"}))
        self.fakes.flag("configtest_fail")
        code, _, data = self.req("POST", "/configtest")
        self.assertEqual((code, data["ok"], data["output"]), (200, False, "bad config"))

    def test_restart_restarts_headscale_and_waits_for_health(self):
        first = self.s.children["headscale"].pid
        code, _, data = self.req("POST", "/restart")
        self.assertEqual((code, data), (200, {"ok": True}))
        self.assertNotEqual(self.s.children["headscale"].pid, first)
        self.assertTrue(self.s.headscale_healthy(fresh=True))

    def test_restart_reports_unhealthy(self):
        with mock.patch.object(sup, "RESTART_WAIT", 1.0):
            self.fakes.flag("health_block")
            code, _, data = self.req("POST", "/restart")
        self.assertEqual(code, 200)
        self.assertFalse(data["ok"])
        self.assertIn("healthy", data["error"])

    def test_status_contract(self):
        code, _, data = self.req("GET", "/status")
        self.assertEqual(code, 200)
        self.assertEqual(data["api"], 1)
        self.assertTrue(data["docker"])  # web/headscale.py treats this backend as available
        self.assertEqual(data["mode"], "run")
        self.assertEqual(data["headscale"]["version"], "v0.26.1")
        rows = {r["name"]: r for r in data["containers"]}
        self.assertEqual(sorted(rows), ["caddy", "console", "headscale"])
        for row in rows.values():
            self.assertEqual(sorted(row), ["health", "image", "name", "service", "state", "status"])
            self.assertEqual(row["state"], "running")
        self.assertEqual(rows["headscale"]["health"], "healthy")

    def test_status_has_a_backup_key_when_the_scheduler_exists(self):
        summary = {"enabled": True, "schedule": "0 3 * * *", "running": False, "last": None}
        with mock.patch.object(self.s, "backup_summary", create=True, return_value=summary):
            data = self.req("GET", "/status")[2]
        self.assertEqual(data["backup"], summary)

    def test_status_without_a_scheduler_has_no_backup_key(self):
        with mock.patch.object(self.s, "backup_summary", None):
            self.assertNotIn("backup", self.req("GET", "/status")[2])

    def test_backup_route_starts_a_manual_run(self):
        with mock.patch.object(self.s, "start_backup", create=True, return_value=True) as start:
            code, _, data = self.req("POST", "/backup")
        self.assertEqual((code, data), (200, {"ok": True, "started": True}))
        start.assert_called_once_with("manual")

    def test_backup_route_reports_a_run_in_progress(self):
        with mock.patch.object(self.s, "start_backup", create=True, return_value=False):
            code, _, data = self.req("POST", "/backup")
        self.assertEqual((code, data), (200, {"ok": False, "error": "already running"}))

    def test_backup_route_in_setup_mode_or_without_a_scheduler(self):
        with mock.patch.object(self.s, "start_backup", None):
            self.assertFalse(self.req("POST", "/backup")[2]["ok"])
        with mock.patch.object(self.s, "start_backup", create=True, return_value=True) as start, \
                mock.patch.object(self.s, "mode", "setup"):
            data = self.req("POST", "/backup")[2]
        self.assertFalse(data["ok"])
        start.assert_not_called()

    def test_status_after_a_crash(self):
        os.kill(self.s.children["console"].pid, signal.SIGKILL)
        self.s.children["console"].backoff_min = 5  # keep it down for a moment
        self.assertTrue(wait_for(lambda: not self.s.children["console"].running))
        rows = {r["name"]: r for r in self.req("GET", "/status")[2]["containers"]}
        self.assertNotEqual(rows["console"]["state"], "running")

    def test_the_console_client_uses_it(self):
        sys.path.insert(0, os.path.join(ROOT, "web"))
        os.environ.update(HEADSCALE_API_KEY="x", PUBLIC_URL="https://vpn.example.com", SESSION_SECRET="s")
        import headscale as hs
        with mock.patch.object(hs, "HELPER_SOCKET", self.sock):
            self.assertTrue(hs.docker_available())
            self.assertEqual(hs.headscale_configtest(), (True, "Config OK"))
            self.assertTrue(hs.restart_headscale(wait=5))
            status = hs.helper_status()
        self.assertEqual({c["name"] for c in status["containers"]}, {"caddy", "console", "headscale"})


# --- The real thing: a supervisor process and signals ---------------------------------------

class ProcessTest(FakeCase):
    def launch(self, **extra):
        env = self.fakes.env(**{"HSE_PUBLIC_URL": "http://localhost", "HSE_TLS": "off", **extra})
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "aio", "supervisor.py")], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        self.out = []
        threading.Thread(target=lambda: [self.out.append(ln.rstrip()) for ln in proc.stdout], daemon=True).start()
        return proc

    def up(self):
        return wait_for(lambda: self.fakes.lines("console_starts") and self.fakes.lines("caddy_starts"), timeout=15)

    def test_sigterm_stops_everything_in_order_and_quickly(self):
        proc = self.launch()
        self.assertTrue(self.up())
        started = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(10), 0)
        self.assertLess(time.monotonic() - started, 8)  # docker stop gives 10 s
        self.assertEqual(self.fakes.lines("stops"), ["console", "caddy", "headscale"])
        self.assertFalse(os.path.exists(os.path.join(self.fakes.run, "helper.sock")))

    def test_sigint_also_stops(self):
        proc = self.launch()
        self.assertTrue(self.up())
        proc.send_signal(signal.SIGINT)
        self.assertEqual(proc.wait(10), 0)
        self.assertEqual(self.fakes.lines("stops"), ["console", "caddy", "headscale"])

    def test_sighup_reloads_caddy_and_headscale(self):
        proc = self.launch()
        self.assertTrue(self.up())
        first_hs = self.fakes.lines("serves")
        first_caddy = self.fakes.lines("caddy_starts")
        proc.send_signal(signal.SIGHUP)
        self.assertTrue(wait_for(lambda: len(self.fakes.lines("serves")) > len(first_hs) and
                                 len(self.fakes.lines("caddy_starts")) > len(first_caddy)))
        self.assertTrue(wait_for(lambda: "[supervisor] reload done" in self.out))
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(10), 0)

    def test_reload_with_a_bad_config_keeps_headscale_running(self):
        proc = self.launch()
        self.assertTrue(self.up())
        first_hs = self.fakes.lines("serves")
        self.fakes.flag("configtest_fail")
        proc.send_signal(signal.SIGHUP)
        self.assertTrue(wait_for(lambda: "[supervisor] reload done" in self.out))
        self.assertEqual(self.fakes.lines("serves"), first_hs)
        self.assertTrue(any("configtest failed" in ln for ln in self.out))
        proc.send_signal(signal.SIGTERM)
        proc.wait(10)

    def test_invalid_configuration_exits_with_a_message(self):
        proc = self.launch(HSE_TLS="auto", HSE_PUBLIC_URL="https://localhost")
        self.assertEqual(proc.wait(10), 2)
        self.assertTrue(wait_for(lambda: any("invalid configuration" in ln for ln in self.out)))

    def test_hse_cli(self):
        proc = self.launch()
        self.assertTrue(self.up())
        hse = os.path.join(ROOT, "aio", "hse")
        env = {"PATH": os.environ["PATH"], "HSE_RUN_DIR": self.fakes.run}
        res = subprocess.run([sys.executable, hse, "health"], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("healthy (run mode)", res.stdout)
        first = self.fakes.lines("serves")
        res = subprocess.run([sys.executable, hse, "reload"], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(wait_for(lambda: len(self.fakes.lines("serves")) > len(first)))
        self.assertEqual(subprocess.run([sys.executable, hse], env=env, capture_output=True).returncode, 2)
        proc.send_signal(signal.SIGTERM)
        proc.wait(10)
        res = subprocess.run([sys.executable, hse, "health"], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)  # supervisor gone

    def test_hse_health_in_setup_mode(self):
        proc = self.launch(HSE_PUBLIC_URL="")
        self.assertTrue(wait_for(lambda: "[wizard] wizard up" in self.out and self.fakes.lines("caddy_starts"),
                                 timeout=15))
        hse = os.path.join(ROOT, "aio", "hse")
        env = {"PATH": os.environ["PATH"], "HSE_RUN_DIR": self.fakes.run}
        res = wait_for(lambda: (lambda r: r if r.returncode == 0 else None)(
            subprocess.run([sys.executable, hse, "health"], env=env, capture_output=True, text=True)))
        self.assertIn("healthy (setup mode): caddy, wizard", res.stdout)
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(10), 0)


# --- Scheduled backups ---------------------------------------------------------------------

# Stands in for aio/backup.py: logs argv and the env it got, optionally waits for a flag file, exits with a code.
FAKE_BACKUP = """#!%(py)s
import json, os, sys, time
state = os.path.join(os.environ["HSE_DATA_DIR"], "fake")
with open(os.path.join(state, "backup.log"), "a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "env": {k: os.environ.get(k) for k in
        ("BACKUP_SCHEDULE", "BACKUP_KEEP_DAYS", "HSE_DATA_DIR", "TZ")}}) + "\\n")
print("fake backup running", flush=True)
hold = os.path.join(state, "backup-hold")
while os.path.exists(hold):
    time.sleep(0.02)
sys.exit(int(open(os.path.join(state, "backup-exit")).read()) if os.path.exists(os.path.join(state, "backup-exit")) else 0)
"""


class SchedulerTest(SupervisorMixin, FakeCase):
    def setUp(self):
        super().setUp()
        self.backup_py = os.path.join(self.fakes.bin, "backup.py")
        self.fakes.write(self.backup_py, FAKE_BACKUP % {"py": sys.executable}, 0o755)
        patch = mock.patch.object(sup, "BACKUP_CMD", [sys.executable, self.backup_py])
        patch.start()
        self.addCleanup(patch.stop)
        os.makedirs(os.path.join(self.fakes.data, "backups"), exist_ok=True)
        self.now = [datetime(2026, 10, 5, 2, 59)]

    def runs(self):
        return [json.loads(line) for line in self.fakes.lines("backup.log")]

    def sched(self, schedule="0 3 * * *", start=True, **env):
        s = self.make(BACKUP_SCHEDULE=schedule, **env)
        s.mode = "run"
        s.settings = s.load()
        s.clock = lambda: self.now[0]
        s.sched_tick = 0.02
        s.catchup_delay = 0.05
        if start:
            s.start_scheduler()
            self.armed(s)
        return s

    def armed(self, s):
        """Wait until the scheduler has computed its next slot (moving the fake clock earlier would race)."""
        if s.backup_cfg["schedule"] != "off":
            self.assertTrue(wait_for(lambda: s._backup_next is not None))

    def status(self, at, ok=True):
        entry = {"at": at, "ok": ok, "file": "headscale-easy-x.tar.gz", "size": 1234, "trigger": "scheduled"}
        with open(os.path.join(self.fakes.data, "backups", "status.json"), "w") as fh:
            json.dump({"last": entry, "last_ok": entry, "count": 3, "bytes": 4096}, fh)

    def test_fires_on_time_once_per_slot(self):
        s = self.sched()
        time.sleep(0.2)
        self.assertEqual(self.runs(), [])  # 02:59: not yet
        self.now[0] = datetime(2026, 10, 5, 3, 0)
        self.assertTrue(wait_for(lambda: len(self.runs()) == 1))
        self.assertEqual(self.runs()[0]["argv"], ["create", "--trigger", "scheduled"])
        self.assertTrue(any(l.startswith("[backup] fake backup running") for l in s.lines))
        self.now[0] = datetime(2026, 10, 5, 3, 0, 40)  # same slot, later ticks: no second run
        time.sleep(0.3)
        self.assertEqual(len(self.runs()), 1)
        self.now[0] = datetime(2026, 10, 6, 3, 0)
        self.assertTrue(wait_for(lambda: len(self.runs()) == 2))

    def test_the_subprocess_gets_the_schedule_the_data_dir_and_the_time_zone(self):
        self.sched("*/5 * * * *", BACKUP_KEEP_DAYS="30", TZ="Europe/Madrid")
        self.now[0] = datetime(2026, 10, 5, 3, 5)
        self.assertTrue(wait_for(lambda: self.runs()))
        env = self.runs()[0]["env"]
        self.assertEqual((env["BACKUP_SCHEDULE"], env["BACKUP_KEEP_DAYS"]), ("*/5 * * * *", "30"))
        self.assertEqual((env["HSE_DATA_DIR"], env["TZ"]), (self.fakes.data, "Europe/Madrid"))

    def test_off_never_fires(self):
        s = self.sched("off")
        self.now[0] = datetime(2026, 10, 5, 3, 0)
        time.sleep(0.3)
        self.assertEqual(self.runs(), [])
        summary = s.backup_summary()
        self.assertFalse(summary["enabled"])
        self.assertIsNone(summary["next_run"])

    def test_missed_run_is_made_up_once(self):
        self.status("2026-10-01T03:00:12")  # last backup four days ago; slots 02..05 Oct passed
        self.now[0] = datetime(2026, 10, 5, 10, 0)
        s = self.sched()
        self.assertTrue(wait_for(lambda: len(self.runs()) == 1))
        self.assertTrue(any("missed" in l for l in s.lines))
        time.sleep(0.4)
        self.assertEqual(len(self.runs()), 1)  # once, not once per missed slot

    def test_no_catch_up_when_the_last_backup_is_recent(self):
        self.status("2026-10-05T03:00:12")
        self.now[0] = datetime(2026, 10, 5, 10, 0)
        self.sched()
        time.sleep(0.4)
        self.assertEqual(self.runs(), [])

    def test_no_catch_up_without_history(self):
        self.now[0] = datetime(2026, 10, 9, 10, 0)
        self.sched()
        time.sleep(0.4)
        self.assertEqual(self.runs(), [])

    def test_catch_up_is_cancelled_when_the_next_slot_arrives_first(self):
        self.status("2026-10-01T03:00:00")
        self.now[0] = datetime(2026, 10, 5, 2, 59)
        s = self.sched(start=False)
        s.catchup_delay = 0.6
        s.start_scheduler()
        time.sleep(0.1)
        self.now[0] = datetime(2026, 10, 5, 3, 0)
        self.assertTrue(wait_for(lambda: len(self.runs()) == 1))
        time.sleep(0.9)
        self.assertEqual(len(self.runs()), 1)

    def test_overlapping_slot_is_skipped(self):
        self.fakes.flag("backup-hold")
        s = self.sched()
        self.now[0] = datetime(2026, 10, 5, 3, 0)
        self.assertTrue(wait_for(lambda: len(self.runs()) == 1))
        self.assertTrue(s.backup_summary()["running"])
        self.assertFalse(s.start_backup("manual"))  # busy
        self.now[0] = datetime(2026, 10, 6, 3, 0)  # the next slot arrives while it still runs
        self.assertTrue(wait_for(lambda: any("skipped" in l for l in s.lines)))
        self.assertEqual(len(self.runs()), 1)
        self.fakes.flag("backup-hold", False)
        self.assertTrue(wait_for(lambda: not s.backup_summary()["running"]))
        self.assertTrue(s.start_backup("manual"))  # free again
        self.assertTrue(wait_for(lambda: len(self.runs()) == 2))
        self.assertEqual(self.runs()[1]["argv"], ["create", "--trigger", "manual"])

    def test_failed_run_is_logged_and_not_retried_in_a_loop(self):
        with open(os.path.join(self.fakes.data, "fake", "backup-exit"), "w") as fh:
            fh.write("1")
        s = self.sched()
        self.now[0] = datetime(2026, 10, 5, 3, 0)
        self.assertTrue(wait_for(lambda: any("FAILED (exit 1)" in l for l in s.lines)))
        self.assertTrue(wait_for(lambda: not s.backup_summary()["running"]))
        time.sleep(0.3)
        self.assertEqual(len(self.runs()), 1)
        with open(os.path.join(self.fakes.data, "fake", "backup-exit"), "w") as fh:
            fh.write("3")  # another backup holds the lock (hse backup)
        self.assertTrue(s.start_backup("manual"))
        self.assertTrue(wait_for(lambda: any("another backup is running" in l for l in s.lines)))

    def test_configure_picks_up_a_new_schedule_and_rejects_an_invalid_one(self):
        s = self.sched("0 3 * * *")
        nxt = datetime.fromisoformat(s.backup_summary()["next_run"])
        self.assertIsNotNone(nxt.tzinfo)  # the console shows it as an instant: it needs the zone
        self.assertEqual(nxt.replace(tzinfo=None), datetime(2026, 10, 5, 3, 0))
        s.settings["backup_schedule"] = "*/5 * * * *"
        s.settings["backup_keep_days"] = "7"
        s.configure_backup()
        summary = s.backup_summary()
        self.assertEqual((summary["schedule"], summary["keep_days"]), ("*/5 * * * *", 7))
        self.armed(s)
        self.now[0] = datetime(2026, 10, 5, 3, 5)
        self.assertTrue(wait_for(lambda: len(self.runs()) == 1))
        s.settings["backup_schedule"] = "nonsense"
        s.configure_backup()
        self.assertEqual(s.backup_summary()["schedule"], "*/5 * * * *")  # kept
        self.assertTrue(any("Invalid schedule" in l for l in s.lines))
        s.settings["backup_schedule"] = "off"
        s.configure_backup()
        self.assertFalse(s.backup_summary()["enabled"])

    def test_hse_reload_rereads_the_schedule(self):
        s = self.make(BACKUP_SCHEDULE="0 3 * * *")
        s.clock = lambda: self.now[0]
        s.sched_tick = 0.02
        s.start_children()
        self.addCleanup(s.shutdown)
        self.assertTrue(wait_for(lambda: s.mode == "run" and len(s.children) == 3 and s.headscale_healthy(fresh=True)))
        self.assertEqual(s.backup_summary()["schedule"], "0 3 * * *")
        s.env["BACKUP_SCHEDULE"] = "30 4 * * *"
        s.reload()
        self.assertEqual(s.backup_summary()["schedule"], "30 4 * * *")

    def test_setup_mode_schedules_nothing(self):
        s = self.make(HSE_PUBLIC_URL="", BACKUP_SCHEDULE="* * * * *")
        s.clock = lambda: self.now[0]
        s.sched_tick = 0.02
        s.start_children()
        self.addCleanup(s.shutdown)
        self.assertEqual(s.mode, "setup")
        self.assertIsNone(s._sched_thread)
        self.assertFalse(s.start_backup("manual"))
        self.now[0] = datetime(2026, 10, 5, 3, 0)
        time.sleep(0.3)
        self.assertEqual(self.runs(), [])

    def test_shutdown_stops_the_scheduler_and_terminates_a_running_backup(self):
        self.fakes.flag("backup-hold")
        s = self.sched()
        self.assertTrue(s.start_backup("manual"))
        self.assertTrue(wait_for(lambda: len(self.runs()) == 1))
        proc = s._backup_proc
        self.assertIsNotNone(proc)
        started = time.monotonic()
        s.shutdown()
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(s._sched_thread.is_alive())
        self.assertIsNotNone(proc.wait(5))
        self.assertTrue(wait_for(lambda: not s._backup_running))
        self.assertFalse(s.start_backup("manual"))

    def test_summary_reads_status_json(self):
        s = self.sched()
        self.status("2026-10-05T01:00:00")
        summary = s.backup_summary()
        self.assertEqual((summary["count"], summary["bytes"]), (3, 4096))
        self.assertEqual(summary["last"]["file"], "headscale-easy-x.tar.gz")
        self.assertEqual(summary["last_ok"]["size"], 1234)
        self.assertTrue(summary["enabled"] and not summary["running"])

    def test_clock_uses_the_configured_time_zone(self):
        try:
            ZoneInfo("Etc/GMT+12")
        except Exception:  # noqa: BLE001
            self.skipTest("no tz database on this host")
        s = self.sched(start=False)
        s.clock = None
        s.settings["tz"] = "Etc/GMT+12"  # UTC-12
        behind = s.now_local()
        s.settings["tz"] = "Etc/GMT-14"  # UTC+14
        ahead = s.now_local()
        self.assertAlmostEqual((ahead - behind).total_seconds(), 26 * 3600, delta=60)

    def test_unknown_time_zone_falls_back_to_system_time_and_warns_once(self):
        s = self.sched(start=False)
        s.clock = None
        s.settings["tz"] = "Mars/Olympus_Mons"
        self.assertLess(abs((s.now_local() - datetime.now()).total_seconds()), 5)
        s.now_local()
        self.assertEqual(len([l for l in s.lines if "tzdata" in l]), 1)


# --- Online restore (SIGUSR1) ------------------------------------------------------------

class OnlineRestoreTest(SupervisorMixin, FakeCase):
    """``hse restore`` on a running container: the supervisor stops the stack, restores, starts it again."""

    def setUp(self):
        super().setUp()
        self.backup = FakeBackup()
        p = mock.patch.object(sup.restore_mod, "_backup", lambda: self.backup)
        p.start()
        self.addCleanup(p.stop)
        fx.write_data(self.fakes.data, fx.data_tree("b"))
        self.s = self.make()
        self.s.start_children()
        self.assertTrue(wait_for(lambda: self.s.children["headscale"].running and
                                 self.fakes.lines("console_starts") and self.fakes.lines("caddy_starts")))
        self.archive = os.path.join(self.tmp.name, "a.tar.gz")
        fx.build_aio_archive(self.archive, fx.data_tree("a"))

    def request(self, archive=None, **extra):
        doc = {"archive": archive or self.archive, "requested": time.time(), **extra}
        with open(os.path.join(self.fakes.run, "restore.json"), "w") as fh:
            json.dump(doc, fh)
        self.s.online_restore()
        with open(os.path.join(self.fakes.run, "restore-result.json")) as fh:
            return json.load(fh), doc

    def marker(self):
        with open(os.path.join(self.fakes.data, "config", "settings.json")) as fh:
            return json.load(fh)["marker"]

    def running(self):
        return all(c.running for c in self.s.children.values()) and len(self.s.children) == 3

    def test_restores_and_starts_the_stack_again(self):
        serves = len(self.fakes.lines("serves"))
        consoles = len(self.fakes.lines("console_starts"))
        result, doc = self.request()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["requested"], doc["requested"])
        self.assertEqual(self.marker(), "a")
        self.assertTrue(wait_for(lambda: len(self.fakes.lines("serves")) == serves + 1 and
                                 len(self.fakes.lines("console_starts")) == consoles + 1 and self.running()))
        self.assertFalse(os.path.exists(os.path.join(self.fakes.run, "restore.json")))
        self.assertEqual(os.stat(os.path.join(self.fakes.run, "restore-result.json")).st_mode & 0o777, 0o600)

    def test_stops_in_order_restores_then_starts(self):
        self.fakes.flag("up", True)
        self.request()
        self.assertEqual(self.fakes.lines("stops")[:3], ["console", "caddy", "headscale"])
        stopped = [i for i, ln in enumerate(self.s.lines) if ln.endswith(" stopped")]
        done = [i for i, ln in enumerate(self.s.lines) if "restore: done" in ln]
        self.assertEqual(len(stopped), 3)
        self.assertTrue(done and max(stopped) < done[0], self.s.lines)

    def test_holds_the_backup_lock_and_takes_a_safety_copy(self):
        result, _ = self.request()
        self.assertEqual(self.backup.locks, 1)
        self.assertIn("-pre-restore-", os.path.basename(result["safety_copy"]))

    def test_failed_restore_reports_the_error_and_starts_the_stack_again(self):
        bad = os.path.join(self.tmp.name, "bad.tar.gz")
        fx.build_aio_archive(bad, fx.data_tree("a"), hashes={"config/Caddyfile": "0" * 64})
        serves = len(self.fakes.lines("serves"))
        result, _ = self.request(bad)
        self.assertFalse(result["ok"])
        self.assertIn("checksum", result["error"])
        self.assertEqual(self.marker(), "b")  # nothing changed
        self.assertTrue(wait_for(lambda: len(self.fakes.lines("serves")) == serves + 1 and self.running()))

    def test_unexpected_exception_still_starts_the_stack(self):
        with mock.patch.object(sup.restore_mod, "restore", side_effect=RuntimeError("boom")):
            result, _ = self.request()
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["error"])
        self.assertTrue(wait_for(self.running))

    def test_refused_in_setup_mode(self):
        for name in list(self.s.children):
            self.s.children[name].stop()
        os.unlink(os.path.join(self.fakes.data, "config", "settings.json"))
        s = self.make(HSE_PUBLIC_URL="")
        s.start_children()
        self.assertEqual(s.mode, "setup")
        self.assertTrue(wait_for(lambda: s.children["wizard"].running))
        with open(os.path.join(self.fakes.run, "restore.json"), "w") as fh:
            json.dump({"archive": self.archive, "requested": 1.5}, fh)
        s.online_restore()
        with open(os.path.join(self.fakes.run, "restore-result.json")) as fh:
            result = json.load(fh)
        self.assertFalse(result["ok"])
        self.assertIn("setup mode", result["error"])
        self.assertTrue(s.children["wizard"].running)  # nothing was stopped
        self.assertFalse(os.path.exists(os.path.join(self.fakes.data, "config", "settings.json")))

    def test_unusable_request_is_ignored(self):
        result_path = os.path.join(self.fakes.run, "restore-result.json")
        for body in ("not json", json.dumps({"requested": 1}), json.dumps({"archive": 5})):
            with open(os.path.join(self.fakes.run, "restore.json"), "w") as fh:
                fh.write(body)
            self.s.online_restore()
            self.assertFalse(os.path.exists(result_path), body)
            self.assertEqual(self.marker(), "b")
        self.assertTrue(self.running())

    def test_postgres_flag_is_passed_through(self):
        with mock.patch.object(sup.restore_mod, "restore", return_value={"ok": True, "files": 1}) as fn:
            self.request(with_postgres=True)
        self.assertTrue(fn.call_args.kwargs["with_postgres"])
        self.assertTrue(fn.call_args.kwargs["offline"])


class OnlineRestoreProcessTest(FakeCase):
    """The real supervisor process, a real SIGUSR1 and aio.restore.request_online (what ``hse restore`` runs)."""

    def setUp(self):
        super().setUp()
        # a stand-in for aio/backup.py when it is not merged yet (the real one wins once it is: aio/ is first on the path)
        self.libs = os.path.join(self.tmp.name, "libs")
        os.makedirs(self.libs)
        with open(os.path.join(self.libs, "backup.py"), "w") as fh:
            fh.write("import contextlib\n@contextlib.contextmanager\ndef lock(out_dir):\n    yield\n")
        self.archive = os.path.join(self.tmp.name, "a.tar.gz")
        fx.build_aio_archive(self.archive, fx.data_tree("a"))

    def test_hse_restore_flow(self):
        env = self.fakes.env(HSE_PUBLIC_URL="http://localhost", HSE_TLS="off", PYTHONPATH=self.libs)
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "aio", "supervisor.py")], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        out = []
        threading.Thread(target=lambda: [out.append(ln.rstrip()) for ln in proc.stdout], daemon=True).start()
        self.assertTrue(wait_for(lambda: self.fakes.lines("console_starts") and self.fakes.lines("caddy_starts"),
                                 timeout=15))
        serves = len(self.fakes.lines("serves"))
        result = aio_restore.request_online(self.archive, self.fakes.run, timeout=30)
        self.assertTrue(result["ok"], (result, out))
        with open(os.path.join(self.fakes.data, "config", "settings.json")) as fh:
            self.assertEqual(json.load(fh)["marker"], "a")
        self.assertTrue(wait_for(lambda: len(self.fakes.lines("serves")) == serves + 1))
        self.assertEqual(self.fakes.lines("stops")[:3], ["console", "caddy", "headscale"])
        self.assertTrue(wait_for(lambda: len(self.fakes.lines("console_starts")) == 2, timeout=15))
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(10), 0)


class BackupSettingsTest(SupervisorMixin, FakeCase):
    """POST /backup-settings: the console turns scheduled backups on/off or changes them (values in a file)."""

    def setUp(self):
        super().setUp()
        self.now = [datetime(2026, 10, 5, 2, 59)]

    def request(self, s, **req):
        with open(os.path.join(self.fakes.run, "backup-settings.json"), "w", encoding="utf-8") as fh:
            json.dump(req, fh)
        return s.be_backup_settings()[1]

    def stored(self):
        with open(os.path.join(self.fakes.data, "config", "settings.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def make_sched(self, stored=None, **env):
        os.makedirs(os.path.join(self.fakes.data, "config"), exist_ok=True)
        if stored is not None:
            with open(os.path.join(self.fakes.data, "config", "settings.json"), "w", encoding="utf-8") as fh:
                json.dump(stored, fh)
        s = self.make(**env)
        s.mode = "run"
        s.settings = s.load()
        s.clock = lambda: self.now[0]
        s.configure_backup()
        os.makedirs(self.fakes.run, exist_ok=True)
        return s

    def test_change_schedule_and_days_is_stored_and_applied(self):
        s = self.make_sched({"public_url": "http://localhost", "tz": "UTC"})
        res = self.request(s, enabled=True, schedule="30 4 * * *", keep_days="7")
        self.assertTrue(res["ok"], res)
        self.assertEqual((self.stored()["backup_schedule"], self.stored()["backup_keep_days"], self.stored()["public_url"]),
                         ("30 4 * * *", "7", "http://localhost"))
        self.assertEqual(s.backup_cfg, {"schedule": "30 4 * * *", "keep_days": 7})
        self.assertEqual(res["backup"]["schedule"], "30 4 * * *")
        path = os.path.join(self.fakes.data, "config", "settings.json")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertFalse(os.path.exists(os.path.join(self.fakes.run, "backup-settings.json")))  # consumed

    def test_turn_off_then_on_again(self):
        s = self.make_sched({"public_url": "http://localhost"})
        self.assertTrue(self.request(s, enabled=False, schedule="30 4 * * *", keep_days="14")["ok"])
        self.assertEqual((s.backup_cfg["schedule"], self.stored()["backup_schedule"]), ("off", "off"))
        self.assertFalse(s.backup_summary()["enabled"])
        self.assertTrue(self.request(s, enabled=True, schedule="", keep_days="14")["ok"])
        self.assertEqual(s.backup_cfg["schedule"], "0 3 * * *")  # nothing typed: the default
        self.assertTrue(s.backup_summary()["enabled"])

    def test_invalid_values_change_nothing(self):
        s = self.make_sched({"public_url": "http://localhost", "backup_schedule": "0 3 * * *"})
        for req, field in (({"schedule": "foo"}, "schedule"), ({"schedule": "*/0 * * * *"}, "schedule"),
                           ({"schedule": "$(x) * * * *"}, "schedule"), ({"keep_days": "0"}, "keep_days"),
                           ({"keep_days": "9999"}, "keep_days"), ({"keep_days": "abc"}, "keep_days")):
            res = self.request(s, **dict({"enabled": True, "schedule": "1 1 * * *", "keep_days": "5"}, **req))
            self.assertEqual((res["ok"], res.get("field")), (False, field), req)
        self.assertEqual(self.stored()["backup_schedule"], "0 3 * * *")
        self.assertEqual(s.backup_cfg["schedule"], "0 3 * * *")

    def test_environment_variables_win(self):
        s = self.make_sched({"public_url": "http://localhost"}, BACKUP_SCHEDULE="0 5 * * *", BACKUP_KEEP_DAYS="3")
        res = self.request(s, enabled=False, schedule="1 1 * * *", keep_days="9")
        self.assertEqual((res["ok"], res["field"]), (False, "env"))
        self.assertNotIn("backup_schedule", self.stored())
        self.assertEqual(s.backup_summary()["env_locked"], {"schedule": True, "keep_days": True})

    def test_only_the_free_half_changes_when_one_is_fixed(self):
        s = self.make_sched({"public_url": "http://localhost"}, BACKUP_SCHEDULE="0 5 * * *")
        self.assertTrue(self.request(s, enabled=False, schedule="1 1 * * *", keep_days="9")["ok"])
        self.assertEqual(self.stored()["backup_keep_days"], "9")
        self.assertNotIn("backup_schedule", self.stored())
        self.assertEqual(s.backup_cfg["schedule"], "0 5 * * *")

    def test_headless_start_keeps_the_effective_settings(self):
        s = self.make_sched(None)  # settings come from the environment: no settings.json yet
        self.assertTrue(self.request(s, enabled=True, schedule="0 2 * * *", keep_days="10")["ok"])
        self.assertEqual((self.stored()["public_url"], self.stored()["backup_schedule"]), ("http://localhost", "0 2 * * *"))

    def test_no_request_and_setup_mode(self):
        s = self.make_sched({"public_url": "http://localhost"})
        self.assertEqual(s.be_backup_settings()[1], {"ok": False, "error": "no request"})
        with open(os.path.join(self.fakes.run, "backup-settings.json"), "w") as fh:
            fh.write("not json")
        self.assertEqual(s.be_backup_settings()[1], {"ok": False, "error": "no request"})
        s.mode = "setup"
        self.assertFalse(self.request(s, enabled=True, schedule="1 1 * * *", keep_days="5")["ok"])


class RestoreFromConsoleTest(SupervisorMixin, FakeCase):
    """POST /restore: the console asks to restore one of /data/backups; the supervisor validates and starts it."""
    NAME = "headscale-easy-20260101-030000.tar.gz"

    def setUp(self):
        super().setUp()
        self.s = self.make()
        self.s.mode = "run"
        self.s.restore_delay = 0.05
        os.makedirs(self.fakes.run, exist_ok=True)
        self.backups = os.path.join(self.fakes.data, "backups")
        os.makedirs(self.backups, exist_ok=True)
        fx.build_aio_archive(os.path.join(self.backups, self.NAME), fx.data_tree("a"))

    def ask(self, name):
        with open(os.path.join(self.fakes.run, "restore-ui.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": name}, fh)
        return self.s.be_restore()[1]

    def request_file(self):
        return os.path.join(self.fakes.run, "restore.json")

    def test_valid_backup_starts_the_online_restore(self):
        res = self.ask(self.NAME)
        self.assertTrue(res["ok"], res)
        with open(self.request_file(), encoding="utf-8") as fh:
            req = json.load(fh)
        self.assertEqual(req["archive"], os.path.join(self.backups, self.NAME))
        self.assertEqual(float(res["id"]), req["requested"])  # what the console polls for
        self.assertEqual(oct(os.stat(self.request_file()).st_mode & 0o777), "0o600")
        self.assertFalse(os.path.exists(os.path.join(self.fakes.run, "restore-ui.json")))  # consumed
        self.assertFalse(self.s.restore_requested.is_set())  # not before the console could answer
        self.assertTrue(self.s.restore_requested.wait(2))

    def test_names_that_are_not_a_backup_of_this_directory(self):
        outside = os.path.join(self.tmp.name, "headscale-easy-outside.tar.gz")
        fx.build_aio_archive(outside, fx.data_tree("x"))
        os.symlink(outside, os.path.join(self.backups, "headscale-easy-link.tar.gz"))
        with open(os.path.join(self.backups, ".headscale-easy-x.tar.gz.part"), "wb") as fh:
            fh.write(b"partial")
        for name in ("../headscale-easy-outside.tar.gz", outside, "/etc/passwd", "headscale-easy-link.tar.gz",
                     ".headscale-easy-x.tar.gz.part", "headscale-easy-missing.tar.gz", "notes.txt", "", None, 7,
                     self.NAME + "/", "x/" + self.NAME):
            res = self.ask(name)
            self.assertEqual((res["ok"], res.get("field")), (False, "invalid"), repr(name))
        self.assertFalse(os.path.exists(self.request_file()))
        self.assertFalse(self.s.restore_requested.wait(0.2))

    def test_a_broken_or_foreign_archive_is_refused(self):
        with open(os.path.join(self.backups, "headscale-easy-garbage.tar.gz"), "wb") as fh:
            fh.write(b"not a tarball")
        fx.build_1x_archive(os.path.join(self.backups, "headscale-easy-old.tar.gz"))
        for name in ("headscale-easy-garbage.tar.gz", "headscale-easy-old.tar.gz"):
            res = self.ask(name)
            self.assertEqual((res["ok"], res.get("field")), (False, "invalid"), name)
        self.assertFalse(os.path.exists(self.request_file()))

    def test_refused_while_a_backup_or_another_restore_runs(self):
        self.s._backup_running = True
        self.assertEqual(self.ask(self.NAME)["field"], "busy")
        self.s._backup_running = False
        with open(self.request_file(), "w") as fh:
            fh.write("{}")
        self.assertEqual(self.ask(self.NAME)["field"], "busy")

    def test_setup_mode_and_no_request(self):
        self.assertEqual(self.s.be_restore()[1], {"ok": False, "error": "no request"})
        self.s.mode = "setup"
        self.assertFalse(self.ask(self.NAME)["ok"])
        self.assertFalse(os.path.exists(self.request_file()))

    def test_backup_files_newest_first_and_only_archives(self):
        for name, mtime in (("headscale-easy-20260102-030000.tar.gz", 2000), ("headscale-easy-20260103-030000.tar.gz", 3000)):
            path = os.path.join(self.backups, name)
            fx.build_aio_archive(path, fx.data_tree("b"))
            os.utime(path, (mtime, mtime))
        os.utime(os.path.join(self.backups, self.NAME), (1000, 1000))
        for junk in ("status.json", ".lock", "headscale-easy-20260104.tar.gz.part", "other.tar.gz"):
            with open(os.path.join(self.backups, junk), "wb") as fh:
                fh.write(b"x")
        files = self.s.backup_files()
        self.assertEqual([f["name"] for f in files], ["headscale-easy-20260103-030000.tar.gz",
                                                       "headscale-easy-20260102-030000.tar.gz", self.NAME])
        self.assertEqual(set(files[0]), {"name", "size", "mtime"})
        self.s.settings = self.s.load()
        self.s.configure_backup()
        self.assertEqual(self.s.backup_summary()["files"], files)

    def test_backup_files_are_capped(self):
        with mock.patch.object(sup, "BACKUP_LIST_MAX", 2):
            for i in range(4):
                with open(os.path.join(self.backups, "headscale-easy-2026020%d-030000.tar.gz" % i), "wb") as fh:
                    fh.write(b"x")
            self.assertEqual(len(self.s.backup_files()), 2)


class BackupUploadTest(SupervisorMixin, FakeCase):
    """POST /backup-upload: a file the console received becomes one of the backups, if it is a valid one."""
    TMP = ".upload-0123456789abcdef0123.part"

    def setUp(self):
        super().setUp()
        self.s = self.make()
        self.s.mode = "run"
        os.makedirs(self.fakes.run, exist_ok=True)
        self.backups = os.path.join(self.fakes.data, "backups")
        os.makedirs(self.backups, exist_ok=True)

    def put(self, builder=None, tmp=None):
        path = os.path.join(self.backups, tmp or self.TMP)
        (builder or (lambda p: fx.build_aio_archive(p, fx.data_tree("up"))))(path)
        return path

    def ask(self, tmp=None, name="headscale-easy-20260301-030000.tar.gz"):
        with open(os.path.join(self.fakes.run, "backup-upload.json"), "w", encoding="utf-8") as fh:
            json.dump({"tmp": tmp or self.TMP, "name": name}, fh)
        return self.s.be_backup_upload()[1]

    def test_valid_file_is_kept_under_its_own_name(self):
        tmp = self.put()
        res = self.ask()
        self.assertEqual(res, {"ok": True, "name": "headscale-easy-20260301-030000.tar.gz"})
        self.assertFalse(os.path.exists(tmp))
        final = os.path.join(self.backups, res["name"])
        self.assertEqual(oct(os.stat(final).st_mode & 0o777), "0o600")
        self.assertEqual([f["name"] for f in self.s.backup_files()], [res["name"]])
        self.assertFalse(os.path.exists(os.path.join(self.fakes.run, "backup-upload.json")))  # consumed

    def test_other_names_get_a_fresh_valid_one_and_nothing_is_overwritten(self):
        for orig in ("my backup.tar.gz", "../../x.tar.gz", "/etc/cron.d/x", "", "backup.zip", None):
            self.put()
            res = self.ask(name=orig)
            self.assertTrue(res["ok"], orig)
            self.assertRegex(res["name"], r"^headscale-easy-uploaded-\d{8}-\d{6}(-\d+)?\.tar\.gz$")
        self.assertEqual(len(self.s.backup_files()), 6)  # same second: suffixes keep them apart
        self.put()
        self.assertTrue(self.ask()["ok"])
        self.put()
        res = self.ask()  # that name is taken now
        self.assertNotEqual(res["name"], "headscale-easy-20260301-030000.tar.gz")
        self.assertEqual(len(self.s.backup_files()), 8)

    def test_a_file_that_is_not_a_valid_backup_is_refused_and_deleted(self):
        def garbage(p):
            with open(p, "wb") as fh:
                fh.write(b"not a tarball")
        for builder in (garbage, fx.build_1x_archive):
            tmp = self.put(builder)
            res = self.ask()
            self.assertEqual((res["ok"], res.get("field")), (False, "invalid"))
            self.assertFalse(os.path.exists(tmp))
        self.assertEqual(self.s.backup_files(), [])

    def test_only_the_consoles_work_files_are_accepted(self):
        victim = self.put(tmp="headscale-easy-20260101-030000.tar.gz")  # an existing backup must not be moved/deleted
        os.symlink(victim, os.path.join(self.backups, self.TMP))
        for tmp in ("headscale-easy-20260101-030000.tar.gz", "../x", "/etc/passwd", ".upload-xyz.part", self.TMP, "", None, 5):
            res = self.ask(tmp=tmp)
            self.assertEqual((res["ok"], res.get("field")), (False, "invalid"), repr(tmp))
        self.assertTrue(os.path.exists(victim))

    def test_setup_mode_and_no_request(self):
        self.assertEqual(self.s.be_backup_upload()[1], {"ok": False, "error": "no request"})
        tmp = self.put()
        self.s.mode = "setup"
        self.assertFalse(self.ask()["ok"])
        self.assertFalse(os.path.exists(tmp))


class EnsureLayoutTest(unittest.TestCase):
    def test_an_unwritable_backups_directory_warns_instead_of_stopping_the_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = os.chmod

            def chmod(path, mode, *a, **k):
                if str(path).endswith("backups"):
                    raise PermissionError(1, "Operation not permitted", path)
                return real(path, mode, *a, **k)
            lines = []
            with mock.patch.object(sup.os, "chmod", chmod), mock.patch.object(sup, "emit", lines.append):
                sup.ensure_layout(tmp)
            self.assertTrue(os.path.isdir(os.path.join(tmp, "console")))  # the rest of the layout was made
            self.assertEqual(len(lines), 1)
            self.assertIn("backups will fail", lines[0])
            self.assertIn("chown", lines[0])

    def test_any_other_directory_failing_is_still_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            def chmod(path, mode, *a, **k):
                raise PermissionError(1, "Operation not permitted", path)
            with mock.patch.object(sup.os, "chmod", chmod):
                with self.assertRaises(PermissionError):
                    sup.ensure_layout(tmp)


class PostgresReadOnlyRoleTest(SupervisorMixin, FakeCase):
    """The console reads PostgreSQL with a read-only role the image creates itself (psql, as the owner)."""
    # Run-time values, not literals: a scanner takes a literal next to "password" for a leaked credential
    OWNER_PW = "o" * 10
    RO_PW = "r" * 10
    PG = {"HEADSCALE_DB_TYPE": "postgres", "HEADSCALE_PG_HOST": "db", "HEADSCALE_PG_USER": "headscale",
          "HEADSCALE_PG_PASS": OWNER_PW, "HEADSCALE_PG_NAME": "hs"}
    RO = {"HEADSCALE_PG_RO_USER": "headscale_ro", "HEADSCALE_PG_RO_PASS": RO_PW}

    def fake_psql(self, exit_code=0, message=""):
        self.fakes.write(os.path.join(self.fakes.bin, "psql"), """#!/bin/sh
echo "$*" > "%(d)s/psql-argv"
env | grep -E '^(PG|HSE_RO)' | sort > "%(d)s/psql-env"
[ -n "%(msg)s" ] && echo "%(msg)s" >&2
exit %(code)d
""" % {"d": self.fakes.data, "msg": message, "code": exit_code}, 0o755)

    def read(self, name):
        with open(os.path.join(self.fakes.data, name)) as fh:
            return fh.read()

    def prepare(self, **env):
        s = self.make(**dict(self.PG, **env))
        s.mode = "run"
        with mock.patch.object(s, "wait_headscale", return_value=True), mock.patch.object(s, "ensure_api_key"):
            os.makedirs(os.path.join(self.fakes.data, "console"), exist_ok=True)
            with open(s.paths["api_key"], "w") as fh:
                fh.write("key")
            with mock.patch.dict(os.environ, {"PATH": self.fakes.bin + os.pathsep + os.environ["PATH"]}):
                _argv, console_env = s.console_prepare()
        return s, console_env

    def test_role_is_created_as_the_owner_and_the_console_gets_it(self):
        self.fake_psql()
        s, env = self.prepare(**self.RO)
        argv = self.read("psql-argv")
        self.assertIn("ON_ERROR_STOP=1", argv)
        self.assertTrue(argv.strip().endswith("templates/headscale-pg-readonly.sql"))
        self.assertNotIn(self.OWNER_PW, argv)  # no password on a command line
        self.assertNotIn(self.RO_PW, argv)
        penv = self.read("psql-env")
        for line in ("PGUSER=headscale", "PGPASSWORD=" + self.OWNER_PW, "PGHOST=db", "PGDATABASE=hs",
                     "HSE_RO_USER=headscale_ro", "HSE_RO_PASS=" + self.RO_PW):
            self.assertIn(line, penv)
        self.assertEqual(env["HEADSCALE_PG_USER"], "headscale_ro")
        self.assertEqual(env["HEADSCALE_PG_PASSWORD"], self.RO_PW)
        self.assertTrue(any("read-only role headscale_ro" in l for l in s.lines))

    def test_if_the_role_cannot_be_created_the_console_keeps_the_owner_and_it_is_logged(self):
        self.fake_psql(1, "ERROR: permission denied to create role")
        s, env = self.prepare(**self.RO)
        self.assertEqual(env["HEADSCALE_PG_USER"], "headscale")
        self.assertEqual(env["HEADSCALE_PG_PASSWORD"], self.OWNER_PW)
        errors = [l for l in s.lines if "ERROR" in l]
        self.assertEqual(len(errors), 1)
        self.assertIn("falls back to the owner", errors[0])
        self.assertIn("permission denied", errors[0])
        self.assertNotIn(self.OWNER_PW, errors[0])
        self.assertNotIn(self.RO_PW, errors[0])

    def test_nothing_runs_without_the_role_settings_or_on_sqlite(self):
        self.fake_psql()
        self.prepare()  # PostgreSQL, no read-only role
        self.assertFalse(os.path.exists(os.path.join(self.fakes.data, "psql-argv")))
        s = self.make(HEADSCALE_PG_RO_USER="ro", HEADSCALE_PG_RO_PASS="x")  # sqlite
        s.mode = "run"
        with mock.patch.object(s, "wait_headscale", return_value=True), mock.patch.object(s, "ensure_api_key"):
            os.makedirs(os.path.join(self.fakes.data, "console"), exist_ok=True)
            with open(s.paths["api_key"], "w") as fh:
                fh.write("key")
            s.console_prepare()
        self.assertFalse(os.path.exists(os.path.join(self.fakes.data, "psql-argv")))

    def test_role_name_and_password_go_together_and_are_safe(self):
        from aio import render
        base = {"public_url": "http://localhost", "db_type": "postgres", "pg_host": "db", "pg_user": "headscale"}
        for bad in ({"pg_ro_user": "headscale_ro"}, {"pg_ro_pass": "x"},
                    {"pg_ro_user": "headscale", "pg_ro_pass": "x"},   # the owner is not a read-only role
                    {"pg_ro_user": "a b", "pg_ro_pass": "x"}, {"pg_ro_user": "ro;drop", "pg_ro_pass": "x"},
                    {"pg_ro_user": "1ro", "pg_ro_pass": "x"}, {"pg_ro_user": "r" * 64, "pg_ro_pass": "x"}):
            with self.assertRaises(ValueError, msg=bad):
                render.to_vars(dict(base, **bad))
        render.to_vars(dict(base, pg_ro_user="headscale_ro", pg_ro_pass="x"))


class RestoreFromConsolePostgresTest(RestoreFromConsoleTest):
    """A PostgreSQL install restoring a PostgreSQL backup from the console must load the dump."""

    def archive(self, name, db):
        tree = fx.data_tree("a", postgres=(db == "postgres"))
        fx.build_aio_archive(os.path.join(self.backups, name), tree)

    def requested_with_postgres(self, name):
        self.assertTrue(self.ask(name)["ok"])
        with open(self.request_file(), encoding="utf-8") as fh:
            return json.load(fh)["with_postgres"]

    def test_the_dump_is_loaded_only_when_both_sides_are_postgres(self):
        self.archive("headscale-easy-20260201-030000.tar.gz", "postgres")
        self.archive("headscale-easy-20260202-030000.tar.gz", "sqlite")
        self.s.load = lambda: {"db_type": "postgres"}
        self.assertTrue(self.requested_with_postgres("headscale-easy-20260201-030000.tar.gz"))
        os.unlink(self.request_file())
        self.assertFalse(self.requested_with_postgres("headscale-easy-20260202-030000.tar.gz"))  # a sqlite archive
        os.unlink(self.request_file())
        self.s.load = lambda: {"db_type": "sqlite"}
        self.assertFalse(self.requested_with_postgres("headscale-easy-20260201-030000.tar.gz"))  # a sqlite install


if __name__ == "__main__":
    unittest.main()
