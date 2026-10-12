"""Supervisor of the all-in-one image (PID 1's child, under tini).

Runs Headscale, Caddy and the console (or, on first run, the setup wizard),
restarts them with exponential backoff, forwards SIGTERM/SIGINT with an
ordered shutdown, and serves the control socket (aio/control.py: configtest / restart /
status) on a local Unix socket so the console needs no Docker socket.

    SIGHUP   re-render the config, restart Caddy and Headscale (``hse reload``)
    SIGUSR1  online restore: read <run dir>/restore.json, stop everything, restore the
             archive into /data, start again, write <run dir>/restore-result.json

Standard library only (plus aio/render.py and aio/control.py).
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import signal
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "aio"))

import control  # noqa: E402
import render  # noqa: E402
import restore as restore_mod  # noqa: E402
from backup_control import BackupControl  # noqa: E402
from processes import Child, base_env, emit, run_cmd  # noqa: E402,F401
import backup_control  # noqa: E402,F401

log = logging.getLogger("supervisor")

HEADSCALE_BIN = os.environ.get("HSE_HEADSCALE_BIN", "headscale")
CADDY_BIN = os.environ.get("HSE_CADDY_BIN", "caddy")
CONSOLE_CMD = [sys.executable, os.environ.get("HSE_CONSOLE_APP", os.path.join(ROOT, "web", "app.py"))]
WIZARD_CMD = [sys.executable, os.environ.get("HSE_WIZARD_APP", os.path.join(ROOT, "aio", "wizard.py"))]
RUN_DIR = os.environ.get("HSE_RUN_DIR", "/run/hse")
RESTART_WAIT = float(os.environ.get("RESTART_WAIT", "120"))
START_TIMEOUT = float(os.environ.get("HSE_START_TIMEOUT", "90"))
HEALTH_TTL = 10.0
RESTORE_DELAY = 1.5      # lets the console answer the request that started a restore from its page
BACKUP_TICK = 30.0       # how often the scheduler looks at the clock (it is woken early by reload / shutdown)
BACKUP_CATCHUP = 60.0    # delay before the one catch-up run after a missed slot


def ensure_layout(data_dir: str):
    for sub in ("headscale", "caddy", "caddy/logs", "console", "config", "backups"):
        path = os.path.join(data_dir, sub)
        try:
            os.makedirs(path, mode=0o700, exist_ok=True)
            os.chmod(path, 0o700)
        except PermissionError:
            if sub != "backups":
                raise
            # A bind mount (a NAS folder) that Docker created for root: everything else works, backups
            # cannot be written. Say how to fix it instead of crash-looping the whole server.
            emit("[supervisor] warning: %s is not writable by uid %d, so backups will fail. "
                 "Fix it on the host: chown %d:%d <the folder>" % (path, os.getuid(), os.getuid(), os.getgid()))


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


class Supervisor(BackupControl):
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
        self.restore_requested = threading.Event()
        self.restore_delay = RESTORE_DELAY
        self.server = None
        self._op_lock = threading.Lock()
        self._health = {"at": 0.0, "ok": False}
        self._version = {"at": 0.0, "value": None}
        self.control_socket = os.path.join(self.run_dir, "control.sock")
        # scheduled backups (see "Backups" below)
        self.clock = None  # test hook: callable returning the naive local time
        self.sched_tick = BACKUP_TICK
        self.catchup_delay = BACKUP_CATCHUP
        self.backup_cfg = {"schedule": "off", "keep_days": 14}
        self._backup_lock = threading.Lock()
        self._backup_running = False
        self._backup_proc = None
        self._backup_next = None
        self._sched_wake = threading.Event()
        self._sched_thread = None
        self._tz_warned = False

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
        if settings.get("db_type") == "postgres" and settings.get("pg_ro_user"):
            if not self.apply_pg_readonly(settings):
                # Keep the console working with what it used before the role existed (the owner), loudly
                settings = {k: v for k, v in settings.items() if k not in ("pg_ro_user", "pg_ro_pass")}
        settings["session_secret"] = settings.get("session_secret") or ensure_secret(
            os.path.join(self.data_dir, "config", "session-secret"))
        env = base_env(render.console_env(settings, self.data_dir))
        with open(self.paths["api_key"], encoding="utf-8") as fh:
            env["HEADSCALE_API_KEY"] = fh.read().strip()
        if self.env.get("HSE_ADMIN_PASSWORD"):
            env["HSE_ADMIN_PASSWORD"] = self.env["HSE_ADMIN_PASSWORD"]
        return CONSOLE_CMD, env

    def apply_pg_readonly(self, settings) -> bool:
        """Create or refresh the console's read-only PostgreSQL role (templates/headscale-pg-readonly.sql).

        Run as Headscale's owner, once Headscale is healthy (its tables exist then), before every console start:
        the SQL is idempotent. The credentials travel in the environment of that one process, never in argv."""
        sql = os.path.join(ROOT, "templates", "headscale-pg-readonly.sql")
        env = base_env({"PGHOST": settings.get("pg_host", ""), "PGPORT": str(settings.get("pg_port") or "5432"),
                        "PGUSER": settings.get("pg_user") or "headscale", "PGDATABASE": settings.get("pg_name") or "headscale",
                        "PGPASSWORD": settings.get("pg_pass", ""), "PGSSLMODE": settings.get("pg_sslmode") or "disable",
                        "HSE_RO_USER": settings["pg_ro_user"], "HSE_RO_PASS": settings["pg_ro_pass"]})
        code, out = run_cmd(["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", sql], timeout=30, env=env)
        if code == 0:
            self.log("PostgreSQL: the console reads with the read-only role %s" % settings["pg_ro_user"])
            return True
        self.log("ERROR: could not create the read-only PostgreSQL role (%s): the console falls back to the "
                 "owner's credentials. %s" % (settings["pg_ro_user"], (out or "").splitlines()[-1][:200] if out else ""))
        return False

    def caddy_prepare(self):
        caddy_home = os.path.join(self.data_dir, "caddy")
        env = base_env({"XDG_DATA_HOME": caddy_home, "XDG_CONFIG_HOME": os.path.join(caddy_home, "config")})
        return [CADDY_BIN, "run", "--config", self.paths["caddyfile"], "--adapter", "caddyfile"], env

    def headscale_prepare(self):
        return self.headscale_argv("serve"), base_env()

    def wizard_prepare(self):
        env = base_env({"HSE_DATA_DIR": self.data_dir, "HSE_RUN_DIR": self.run_dir})
        # the wizard runs Headscale briefly at the end and honours env-provided settings
        for name in ["HSE_HEADSCALE_BIN", "HSE_WIZARD_PORT", "HSE_START_TIMEOUT",
                     *(var for var, _default in render.SETTINGS.values())]:
            if self.env.get(name):
                env[name] = self.env[name]
        return WIZARD_CMD, env

    def start_children(self):
        ensure_layout(self.data_dir)
        self.mode = self.detect_mode()
        self.log("starting in %s mode" % self.mode)
        if self.mode == "run":
            self.settings = self.load()
            render.render_all(self.settings, self.data_dir)
            for warning in render.config_warnings(self.settings):
                self.log("warning: " + warning)
            self._child("headscale", prepare=self.headscale_prepare).start()
            self._child("caddy", prepare=self.caddy_prepare).start()
            self._child("console", prepare=self.console_prepare).start()
            self.start_scheduler()
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
            self.start_scheduler()
        except Exception:  # noqa: BLE001
            log.exception("could not switch to run mode")

    def stop_children(self):
        """Ordered: console (or wizard) -> caddy -> headscale."""
        for name in ("console", "wizard", "caddy", "headscale"):
            child = self.children.get(name)
            if child:
                child.stop()
                self.log("%s stopped" % name)

    # -- control protocol --------------------------------------------------------
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
        if now - self._version["at"] < control.VERSION_TTL:
            return self._version["value"]
        code, out = run_cmd([HEADSCALE_BIN, "version"], timeout=15, env=base_env())
        match = control._VERSION.search(out) if code == 0 else None
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
        result = {"api": control.API_VERSION, "mode": self.mode,
                  "headscale": {"process": "headscale", "version": version}, "processes": rows}
        summary = getattr(self, "backup_summary", None)  # the scheduler's view (Block 2)
        if self.mode == "run" and callable(summary):
            result["backup"] = summary()
        return 200, result


    def serve_control(self):
        os.makedirs(self.run_dir, mode=0o755, exist_ok=True)
        self.server = control.serve(self.control_socket, {
            "configtest": self.be_configtest, "restart": self.be_restart, "status": self.be_status,
            "backup": self.be_backup, "backup_settings": self.be_backup_settings,
            "restore": self.be_restore, "backup_upload": self.be_backup_upload})
        threading.Thread(target=self.server.serve_forever, name="control-socket", daemon=True).start()
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
        self.configure_backup()
        code, out = run_cmd(self.headscale_argv("configtest"), timeout=60, env=base_env())
        if code != 0:
            self.log("reload: configtest failed, Headscale not restarted: %s" % out)
        else:
            self.be_restart()
        if "caddy" in self.children:
            self.children["caddy"].restart()
        self.log("reload done")

    def online_restore(self):
        """``hse restore`` on a running container (SIGUSR1).

        ``hse`` validated the archive and wrote restore.json; only this method changes /data:
        stop console -> caddy -> headscale, restore, start everything again, report the result.
        The stack is started again whatever happens (a failed restore rolls itself back).
        """
        req_path = os.path.join(self.run_dir, restore_mod.RESTORE_REQUEST)
        res_path = os.path.join(self.run_dir, restore_mod.RESTORE_RESULT)
        try:
            with open(req_path, encoding="utf-8") as fh:
                req = json.load(fh)
            archive = req["archive"]
            if not isinstance(archive, str):
                raise TypeError("archive")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.log("restore requested but %s is unusable: %s" % (restore_mod.RESTORE_REQUEST, exc))
            return
        result = {"ok": False, "requested": req.get("requested")}
        if self.mode != "run":
            result["error"] = "the stack is in setup mode: restore offline (stop the container first)"
        else:
            with self._op_lock:
                self.log("restore: stopping the stack")
                self.stop_children()
                try:
                    done = restore_mod.restore(archive, self.data_dir, offline=True,
                                               with_postgres=bool(req.get("with_postgres")), run_dir=self.run_dir)
                    result.update(ok=True, safety_copy=done.get("safety_copy"), files=done.get("files"),
                                  warnings=done.get("warnings", []))
                    self.log("restore: done (%s files)" % done.get("files"))
                except restore_mod.RestoreError as exc:
                    result["error"] = str(exc)
                    self.log("restore failed: %s" % exc)
                except Exception as exc:  # noqa: BLE001
                    log.exception("restore crashed")
                    result["error"] = "unexpected error: %s" % exc
                finally:
                    self.children = {}
                    self._health["at"] = 0.0
                    self._version["at"] = 0.0
                    if not self.stopping.is_set():
                        try:
                            self.start_children()
                        except Exception:  # noqa: BLE001
                            log.exception("could not start the stack after the restore")
                            result.setdefault("error", "restored, but the stack did not start: see the logs")
                            result["ok"] = False
        try:
            os.unlink(req_path)
        except OSError:
            pass
        render.write_file(res_path, json.dumps(result))

    def shutdown(self):
        self.stopping.set()
        self._sched_wake.set()
        self.log("shutting down")
        proc = self._backup_proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        if self._sched_thread is not None:
            self._sched_thread.join(timeout=2)
        self.stop_children()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        for name in ("control.sock", "supervisor.pid"):
            try:
                os.unlink(os.path.join(self.run_dir, name))
            except OSError:
                pass

    def run(self) -> int:
        signal.signal(signal.SIGTERM, lambda *_: self.stopping.set())
        signal.signal(signal.SIGINT, lambda *_: self.stopping.set())
        signal.signal(signal.SIGHUP, lambda *_: self.reload_requested.set())
        signal.signal(signal.SIGUSR1, lambda *_: self.restore_requested.set())
        try:
            self.serve_control()
            self.start_children()
            while not self.stopping.wait(0.5):
                if self.reload_requested.is_set():
                    self.reload_requested.clear()
                    self.reload()
                if self.restore_requested.is_set():
                    self.restore_requested.clear()
                    self.online_restore()
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
