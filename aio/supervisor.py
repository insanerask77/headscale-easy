"""Supervisor of the all-in-one image (PID 1's child, under tini).

Runs Headscale, Caddy and the console (or, on first run, the setup wizard),
restarts them with exponential backoff, forwards SIGTERM/SIGINT with an
ordered shutdown, and serves the hs-helper protocol (configtest / restart /
status) on a local Unix socket so the console needs no Docker socket.

    SIGHUP   re-render the config, restart Caddy and Headscale (``hse reload``)

Standard library only (plus aio/render.py and helper/helper.py).
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "aio"))
sys.path.insert(0, os.environ.get("HSE_HELPER_DIR") or os.path.join(ROOT, "helper"))

import helper as helper_mod  # noqa: E402
import render  # noqa: E402

log = logging.getLogger("supervisor")

HEADSCALE_BIN = os.environ.get("HSE_HEADSCALE_BIN", "headscale")
CADDY_BIN = os.environ.get("HSE_CADDY_BIN", "caddy")
CONSOLE_CMD = [sys.executable, os.environ.get("HSE_CONSOLE_APP", os.path.join(ROOT, "web", "app.py"))]
WIZARD_CMD = [sys.executable, os.environ.get("HSE_WIZARD_APP", os.path.join(ROOT, "aio", "wizard.py"))]
RUN_DIR = os.environ.get("HSE_RUN_DIR", "/run/hse")
RESTART_WAIT = float(os.environ.get("RESTART_WAIT", "120"))
START_TIMEOUT = float(os.environ.get("HSE_START_TIMEOUT", "90"))
HEALTH_TTL = 10.0

BACKOFF_MIN, BACKOFF_MAX, HEALTHY_AFTER = 1.0, 30.0, 60.0
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_log_lock = threading.Lock()


def emit(line: str):
    with _log_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


# -----------------------------------------------------------------------------
# One supervised process
# -----------------------------------------------------------------------------

class Child:
    """A process kept alive by its own thread: start, restart with exponential
    backoff (min -> max, reset after ``healthy_after`` seconds up), stop.

    ``prepare`` (optional) is called before every (re)start and returns
    ``(argv, env)``; when it raises, the start counts as a failure.
    """

    def __init__(self, name, argv=None, env=None, *, prepare=None, cwd=None, sink=emit,
                 backoff_min=BACKOFF_MIN, backoff_max=BACKOFF_MAX, healthy_after=HEALTHY_AFTER,
                 stop_timeout=3.0, on_exit=None):
        self.name = name
        self._prepare = prepare or (lambda: (argv, env))
        self.cwd = cwd
        self.sink = sink
        self.backoff_min, self.backoff_max, self.healthy_after = backoff_min, backoff_max, healthy_after
        self.stop_timeout = stop_timeout
        self.on_exit = on_exit  # called with the exit code; True = do not restart
        self.proc: subprocess.Popen | None = None
        self.state = "created"
        self.starts = 0
        self.last_exit: int | None = None
        self.started_at = 0.0
        self._stop = threading.Event()
        self._skip_backoff = False
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self._loop, name="child-" + self.name, daemon=True)
        self._thread.start()

    @property
    def pid(self):
        return self.proc.pid if self.proc and self.proc.poll() is None else None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, timeout: float | None = None):
        """Stop for good: SIGTERM, then SIGKILL after the timeout."""
        self._stop.set()
        self._terminate(self.stop_timeout if timeout is None else timeout)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(5)
        self.state = "stopped"

    def restart(self):
        """Kill the current process; the loop starts it again without backoff."""
        self._skip_backoff = True
        self._terminate(self.stop_timeout)

    def _terminate(self, timeout: float):
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        self._signal(proc, signal.SIGTERM)
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.sink("[supervisor] %s did not stop in %.0fs: killing it" % (self.name, timeout))
            self._signal(proc, signal.SIGKILL)
            proc.wait()

    @staticmethod
    def _signal(proc, sig):
        try:
            os.killpg(proc.pid, sig)  # the child leads its own group
        except (ProcessLookupError, PermissionError):
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                pass

    def snapshot(self) -> dict:
        return {"name": self.name, "state": self.state, "pid": self.pid, "starts": self.starts,
                "last_exit": self.last_exit}

    # -- the loop ------------------------------------------------------------
    def _pump(self, proc):
        prefix = "[%s] " % self.name
        for raw in iter(proc.stdout.readline, b""):
            self.sink(prefix + raw.decode(errors="replace").rstrip("\r\n"))
        proc.stdout.close()

    def _loop(self):
        failures = 0
        while not self._stop.is_set():
            self.state = "starting"
            try:
                argv, env = self._prepare()
                if self._stop.is_set():
                    break
                self.proc = subprocess.Popen(argv, env=env, cwd=self.cwd, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                             start_new_session=True)
            except Exception as exc:  # noqa: BLE001 - a failed start is retried with backoff
                self.sink("[supervisor] %s could not start: %s" % (self.name, exc))
                self.proc = None
                code, ran = None, 0.0
            else:
                self.starts += 1
                self.state = "running"
                self.started_at = time.monotonic()
                pump = threading.Thread(target=self._pump, args=(self.proc,), daemon=True)
                pump.start()
                while self.proc.poll() is None:
                    time.sleep(0.05)
                pump.join(2)
                code = self.proc.returncode
                ran = time.monotonic() - self.started_at
                self.last_exit = code
                if not self._stop.is_set() and not self._skip_backoff:
                    self.sink("[supervisor] %s exited with code %s after %.1fs" % (self.name, code, ran))
            if self._stop.is_set():
                break
            if code is not None and self.on_exit and self.on_exit(code):
                self.state = "done"
                return
            if self._skip_backoff:
                self._skip_backoff = False
                failures = 0
                continue
            if ran >= self.healthy_after:
                failures = 0
            delay = min(self.backoff_min * (2 ** failures), self.backoff_max)
            failures += 1
            self.state = "restarting"
            self.sink("[supervisor] restarting %s in %gs" % (self.name, delay))
            self._stop.wait(delay)
        self.state = "stopped"


# -----------------------------------------------------------------------------
# Headscale helpers
# -----------------------------------------------------------------------------

def run_cmd(argv, timeout=15, env=None) -> tuple[int | None, str]:
    """Run a fixed command; (exit code, output). (None, reason) when it cannot run."""
    try:
        res = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    return res.returncode, _ANSI.sub("", res.stdout.decode(errors="replace")).strip()


def base_env(extra: dict | None = None) -> dict:
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TZ", "SSL_CERT_FILE") if k in os.environ}
    env.update(extra or {})
    return env


def ensure_layout(data_dir: str):
    for sub in ("headscale", "caddy", "caddy/logs", "console", "config", "backups"):
        path = os.path.join(data_dir, sub)
        os.makedirs(path, mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)


def ensure_secret(path: str) -> str:
    """The console's SESSION_SECRET: persisted so sessions survive restarts."""
    try:
        with open(path, encoding="utf-8") as fh:
            value = fh.read().strip()
        if value:
            return value
    except OSError:
        pass
    value = secrets.token_urlsafe(48)
    render.write_file(path, value + "\n")
    return value


class Supervisor:
    def __init__(self, data_dir=None, env=None, sink=emit, run_dir=None, backoff=None, stop_timeouts=None):
        self.data_dir = data_dir or render.DATA_DIR
        self.env = os.environ if env is None else env
        self.sink = sink
        self.run_dir = run_dir or RUN_DIR
        self.paths = render.paths(self.data_dir)
        self.backoff = backoff or {}
        self.stop_timeouts = stop_timeouts or {"console": 3.0, "wizard": 3.0, "caddy": 2.0, "headscale": 3.0}
        self.mode = None
        self.settings: dict = {}
        self.children: dict[str, Child] = {}
        self.stopping = threading.Event()
        self.reload_requested = threading.Event()
        self.server = None
        self._op_lock = threading.Lock()
        self._health = {"at": 0.0, "ok": False}
        self._version = {"at": 0.0, "value": None}
        self.helper_socket = os.path.join(self.run_dir, "helper.sock")

    # -- mode and settings ----------------------------------------------------
    def load(self) -> dict:
        return render.load_settings(self.env, self.paths["settings"])

    def detect_mode(self) -> str:
        """'run' with settings (env HSE_PUBLIC_URL or settings.json), else 'setup'."""
        return "run" if self.load().get("public_url") else "setup"

    def log(self, msg):
        self.sink("[supervisor] " + msg)

    # -- processes ------------------------------------------------------------
    def _child(self, name, **kw) -> Child:
        opts = dict(self.backoff, stop_timeout=self.stop_timeouts.get(name, 3.0), sink=self.sink)
        opts.update(kw)
        child = Child(name, **opts)
        self.children[name] = child
        return child

    def headscale_argv(self, *args):
        return [HEADSCALE_BIN, *args, "-c", self.paths["config"]]

    def headscale_healthy(self, fresh=False) -> bool:
        now = time.monotonic()
        if not fresh and now - self._health["at"] < HEALTH_TTL and self._health["ok"]:
            return True
        code, _ = run_cmd(self.headscale_argv("health"), timeout=5, env=base_env())
        self._health.update(at=now, ok=code == 0)
        return code == 0

    def wait_headscale(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not self.stopping.is_set():
            if self.headscale_healthy(fresh=True):
                return True
            if time.monotonic() >= deadline:
                return False
            self.stopping.wait(1.0)
        return False

    def ensure_api_key(self):
        """The Headscale API key the console uses (it renews it by itself afterwards)."""
        path = self.paths["api_key"]
        try:
            with open(path, encoding="utf-8") as fh:
                if fh.read().strip():
                    return
        except OSError:
            pass
        code, out = run_cmd(self.headscale_argv("apikeys", "create", "--expiration", "90d"), timeout=30,
                            env=base_env())
        key = out.splitlines()[-1].strip() if out else ""
        if code != 0 or not key:
            raise RuntimeError("could not create the API key: %s" % (out or code))
        render.write_file(path, key + "\n")
        self.log("created the Headscale API key")

    def console_prepare(self):
        """Wait for Headscale, then the console's argv and environment."""
        if not self.wait_headscale(START_TIMEOUT):
            raise RuntimeError("Headscale is not healthy yet")
        self.ensure_api_key()
        settings = self.load()
        settings["session_secret"] = settings.get("session_secret") or ensure_secret(
            os.path.join(self.data_dir, "config", "session-secret"))
        env = base_env(render.console_env(settings, self.data_dir))
        with open(self.paths["api_key"], encoding="utf-8") as fh:
            env["HEADSCALE_API_KEY"] = fh.read().strip()
        if self.env.get("HSE_ADMIN_PASSWORD"):
            env["HSE_ADMIN_PASSWORD"] = self.env["HSE_ADMIN_PASSWORD"]
        return CONSOLE_CMD, env

    def caddy_prepare(self):
        caddy_home = os.path.join(self.data_dir, "caddy")
        env = base_env({"XDG_DATA_HOME": caddy_home, "XDG_CONFIG_HOME": os.path.join(caddy_home, "config")})
        return [CADDY_BIN, "run", "--config", self.paths["caddyfile"], "--adapter", "caddyfile"], env

    def headscale_prepare(self):
        return self.headscale_argv("serve"), base_env()

    def wizard_prepare(self):
        env = base_env({"HSE_DATA_DIR": self.data_dir, "HSE_RUN_DIR": self.run_dir})
        return WIZARD_CMD, env

    def start_children(self):
        ensure_layout(self.data_dir)
        self.mode = self.detect_mode()
        self.log("starting in %s mode" % self.mode)
        if self.mode == "run":
            self.settings = self.load()
            render.render_all(self.settings, self.data_dir)
            self._child("headscale", prepare=self.headscale_prepare).start()
            self._child("caddy", prepare=self.caddy_prepare).start()
            self._child("console", prepare=self.console_prepare).start()
        else:
            render.render_setup(self.data_dir)
            self._child("caddy", prepare=self.caddy_prepare).start()
            self._child("wizard", prepare=self.wizard_prepare, on_exit=self.wizard_done).start()

    def wizard_done(self, code: int) -> bool:
        """The wizard exits 0 when setup is complete: switch to run mode."""
        if code != 0 or self.stopping.is_set():
            return False
        self.log("setup finished, switching to run mode")
        threading.Thread(target=self.switch_to_run, daemon=True).start()
        return True

    def switch_to_run(self):
        try:
            self.children.pop("wizard", None)
            self.mode = "run"
            self.settings = self.load()
            render.render_all(self.settings, self.data_dir)
            self._child("headscale", prepare=self.headscale_prepare).start()
            caddy = self.children.get("caddy")
            if caddy:
                caddy.restart()
            self._child("console", prepare=self.console_prepare).start()
        except Exception:  # noqa: BLE001
            log.exception("could not switch to run mode")

    def stop_children(self):
        """Ordered: console (or wizard) -> caddy -> headscale."""
        for name in ("console", "wizard", "caddy", "headscale"):
            child = self.children.get(name)
            if child:
                child.stop()
                self.log("%s stopped" % name)

    # -- helper protocol ---------------------------------------------------------
    def be_configtest(self):
        with self._op_lock:
            code, out = run_cmd(self.headscale_argv("configtest"), timeout=60, env=base_env())
        return 200, {"ok": code == 0, "output": out}

    def be_restart(self):
        child = self.children.get("headscale")
        if child is None:
            return 200, {"ok": False, "error": "Headscale is not running in setup mode"}
        with self._op_lock:
            self._health["at"] = 0.0
            self._version["at"] = 0.0
            child.restart()
            time.sleep(min(1.0, RESTART_WAIT))
            if self.wait_headscale(RESTART_WAIT):
                return 200, {"ok": True}
        return 200, {"ok": False, "error": "Headscale did not become healthy in time"}

    def headscale_version(self):
        now = time.time()
        if now - self._version["at"] < helper_mod.VERSION_TTL:
            return self._version["value"]
        code, out = run_cmd([HEADSCALE_BIN, "version"], timeout=15, env=base_env())
        match = helper_mod._VERSION.search(out) if code == 0 else None
        self._version.update(at=now, value=match.group(0) if match else None)
        return self._version["value"]

    def _summary(self, child: Child) -> dict:
        state = "running" if child.running else ("restarting" if child.state in ("starting", "restarting") else "exited")
        health = None
        if child.name == "headscale" and state == "running":
            health = "healthy" if self.headscale_healthy() else "starting"
        elif child.state == "starting" and state != "running":
            health = "starting"
        return {"name": child.name, "service": child.name, "image": "", "state": state, "health": health,
                "status": child.state}

    def be_status(self):
        rows = sorted((self._summary(c) for c in self.children.values()), key=lambda r: r["name"])
        version = self.headscale_version() if "headscale" in self.children else None
        # docker: true so the console treats this backend as available
        return 200, {"api": helper_mod.API_VERSION, "docker": True, "mode": self.mode,
                     "headscale": {"container": "headscale", "version": version}, "containers": rows}

    def serve_helper(self):
        os.makedirs(self.run_dir, mode=0o755, exist_ok=True)
        self.server = helper_mod.serve(self.helper_socket, {
            "configtest": self.be_configtest, "restart": self.be_restart, "status": self.be_status})
        threading.Thread(target=self.server.serve_forever, name="helper-socket", daemon=True).start()
        with open(os.path.join(self.run_dir, "supervisor.pid"), "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))

    # -- reload and shutdown -------------------------------------------------------
    def reload(self):
        """``hse reload``: re-render, test the config, restart Caddy and Headscale."""
        if self.mode != "run":
            self.log("reload ignored in setup mode")
            return
        try:
            self.settings = self.load()
            render.render_all(self.settings, self.data_dir)
        except ValueError as exc:
            self.log("reload: invalid settings: %s" % exc)
            return
        code, out = run_cmd(self.headscale_argv("configtest"), timeout=60, env=base_env())
        if code != 0:
            self.log("reload: configtest failed, Headscale not restarted: %s" % out)
        else:
            self.be_restart()
        if "caddy" in self.children:
            self.children["caddy"].restart()
        self.log("reload done")

    def shutdown(self):
        self.stopping.set()
        self.log("shutting down")
        self.stop_children()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        for name in ("helper.sock", "supervisor.pid"):
            try:
                os.unlink(os.path.join(self.run_dir, name))
            except OSError:
                pass

    def run(self) -> int:
        signal.signal(signal.SIGTERM, lambda *_: self.stopping.set())
        signal.signal(signal.SIGINT, lambda *_: self.stopping.set())
        signal.signal(signal.SIGHUP, lambda *_: self.reload_requested.set())
        try:
            self.serve_helper()
            self.start_children()
            while not self.stopping.wait(0.5):
                if self.reload_requested.is_set():
                    self.reload_requested.clear()
                    self.reload()
        finally:
            self.shutdown()
        return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="[supervisor] %(message)s")
    try:
        return Supervisor().run()
    except ValueError as exc:  # invalid settings
        emit("[supervisor] invalid configuration: %s" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
