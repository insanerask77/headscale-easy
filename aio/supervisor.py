"""Supervisor of the all-in-one image (PID 1's child, under tini).

Runs Headscale, Caddy and the console (or, on first run, the setup wizard),
restarts them with exponential backoff, forwards SIGTERM/SIGINT with an
ordered shutdown, and serves the hs-helper protocol (configtest / restart /
status) on a local Unix socket so the console needs no Docker socket.

    SIGHUP   re-render the config, restart Caddy and Headscale (``hse reload``)
    SIGUSR1  online restore: read <run dir>/restore.json, stop everything, restore the
             archive into /data, start again, write <run dir>/restore-result.json

Standard library only (plus aio/render.py and helper/helper.py).
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "aio"))
sys.path.insert(0, os.environ.get("HSE_HELPER_DIR") or os.path.join(ROOT, "helper"))

import cron  # noqa: E402
import helper as helper_mod  # noqa: E402
import render  # noqa: E402
import restore as restore_mod  # noqa: E402

log = logging.getLogger("supervisor")

HEADSCALE_BIN = os.environ.get("HSE_HEADSCALE_BIN", "headscale")
CADDY_BIN = os.environ.get("HSE_CADDY_BIN", "caddy")
CONSOLE_CMD = [sys.executable, os.environ.get("HSE_CONSOLE_APP", os.path.join(ROOT, "web", "app.py"))]
WIZARD_CMD = [sys.executable, os.environ.get("HSE_WIZARD_APP", os.path.join(ROOT, "aio", "wizard.py"))]
RUN_DIR = os.environ.get("HSE_RUN_DIR", "/run/hse")
RESTART_WAIT = float(os.environ.get("RESTART_WAIT", "120"))
START_TIMEOUT = float(os.environ.get("HSE_START_TIMEOUT", "90"))
HEALTH_TTL = 10.0
# The backup runs as its own process (aio/backup.py): a slow tar or an OOM must not take the supervisor down.
BACKUP_CMD = [sys.executable, os.environ.get("HSE_BACKUP_APP", os.path.join(ROOT, "aio", "backup.py"))]
DEFAULT_BACKUP_SCHEDULE = "0 3 * * *"
BACKUP_TICK = 30.0       # how often the scheduler looks at the clock (it is woken early by reload / shutdown)
BACKUP_CATCHUP = 60.0    # delay before the one catch-up run after a missed slot
BACKUP_TIMEOUT = 3600.0  # a backup that runs longer than this is killed

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
        self.restore_requested = threading.Event()
        self.server = None
        self._op_lock = threading.Lock()
        self._health = {"at": 0.0, "ok": False}
        self._version = {"at": 0.0, "value": None}
        self.helper_socket = os.path.join(self.run_dir, "helper.sock")
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
        result = {"api": helper_mod.API_VERSION, "docker": True, "mode": self.mode,
                  "headscale": {"container": "headscale", "version": version}, "containers": rows}
        summary = getattr(self, "backup_summary", None)  # the scheduler's view (Block 2)
        if self.mode == "run" and callable(summary):
            result["backup"] = summary()
        return 200, result

    def be_backup(self):
        """POST /backup: start a manual backup and answer at once; the console polls /status."""
        start = getattr(self, "start_backup", None)
        if self.mode != "run" or not callable(start):
            return 200, {"ok": False, "error": "backups are not available in setup mode"}
        if start("manual"):
            return 200, {"ok": True, "started": True}
        return 200, {"ok": False, "error": "already running"}

    def be_backup_settings(self):
        """POST /backup-settings: the console asks to turn scheduled backups on/off or change them.

        The helper protocol takes no body, so the console leaves the values in
        ``<run dir>/backup-settings.json`` (``enabled``, ``schedule``, ``keep_days``). They are checked here
        (this is where ``cron`` lives), written to settings.json and picked up by the scheduler at once.
        A value set by an environment variable wins over settings.json, so it cannot be changed from here."""
        if self.mode != "run":
            return 200, {"ok": False, "error": "backups are not available in setup mode"}
        path = os.path.join(self.run_dir, "backup-settings.json")
        try:
            with open(path, encoding="utf-8") as fh:
                req = json.load(fh)
            os.unlink(path)
        except (OSError, ValueError):
            return 200, {"ok": False, "error": "no request"}
        if not isinstance(req, dict):
            return 200, {"ok": False, "error": "no request"}
        locked = self.backup_env_locked()
        changes = {}
        if not locked["schedule"]:
            schedule = str(req.get("schedule") or "").strip()
            if not req.get("enabled", True):
                schedule = "off"
            elif not schedule or cron.is_off(schedule):
                schedule = DEFAULT_BACKUP_SCHEDULE
            try:
                if not cron.is_off(schedule):
                    cron.parse(schedule)
            except ValueError as exc:
                return 200, {"ok": False, "error": str(exc), "field": "schedule"}
            changes["backup_schedule"] = "off" if cron.is_off(schedule) else schedule
        if not locked["keep_days"]:
            try:
                keep = int(req.get("keep_days"))
            except (TypeError, ValueError):
                return 200, {"ok": False, "error": "days to keep must be a number", "field": "keep_days"}
            if not 1 <= keep <= 3650:
                return 200, {"ok": False, "error": "days to keep must be between 1 and 3650", "field": "keep_days"}
            changes["backup_keep_days"] = str(keep)
        if not changes:
            return 200, {"ok": False, "error": "locked by environment variables", "field": "env"}
        with self._op_lock:
            stored = {}
            try:
                with open(self.paths["settings"], encoding="utf-8") as fh:
                    stored = json.load(fh)
            except (OSError, ValueError):
                pass
            if not isinstance(stored, dict) or not stored:
                # headless start: nothing on disk yet, so keep the effective settings with the change
                # (a settings.json with only these keys would make a restore on a fresh volume start the wizard)
                stored = dict(self.load())
            stored.update(changes)
            tmp = self.paths["settings"] + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(stored, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.paths["settings"])
            self.settings = self.load()
            self.configure_backup()
        self.log("backups: settings changed from the console (%s)" % ", ".join(sorted(changes)))
        return 200, {"ok": True, "backup": self.backup_summary()}

    def backup_env_locked(self) -> dict:
        """Which backup settings an environment variable fixes (the console cannot change those)."""
        return {"schedule": bool(str(self.env.get("BACKUP_SCHEDULE") or "").strip()),
                "keep_days": bool(str(self.env.get("BACKUP_KEEP_DAYS") or "").strip())}

    def serve_helper(self):
        os.makedirs(self.run_dir, mode=0o755, exist_ok=True)
        self.server = helper_mod.serve(self.helper_socket, {
            "configtest": self.be_configtest, "restart": self.be_restart, "status": self.be_status,
            "backup": self.be_backup, "backup_settings": self.be_backup_settings})
        threading.Thread(target=self.server.serve_forever, name="helper-socket", daemon=True).start()
        with open(os.path.join(self.run_dir, "supervisor.pid"), "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))

    # -- Backups ---------------------------------------------------------------------
    # The supervisor only decides *when*: aio/backup.py does the work in a child
    # process. Run mode only; nothing is scheduled while the wizard is running.
    def _tz(self):
        name = str(self.settings.get("tz") or self.env.get("TZ") or "").strip()
        if not name:
            return None
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            if not self._tz_warned:
                self._tz_warned = True
                self.log("time zone %r is unknown here (is tzdata installed?): the schedule uses the system time" % name)
            return None

    def now_local(self) -> datetime:
        """Naive wall-clock time in the configured time zone (what the cron expression is read in)."""
        if self.clock:
            return self.clock()
        return datetime.now(self._tz()).replace(tzinfo=None)

    def configure_backup(self):
        """Read BACKUP_SCHEDULE / BACKUP_KEEP_DAYS from the loaded settings (start and ``hse reload``).

        An invalid value is logged and the previous configuration stays.
        """
        schedule = str(self.settings.get("backup_schedule") or "").strip()
        try:
            keep = int(self.settings.get("backup_keep_days") or 14)
        except (TypeError, ValueError):
            self.log("backups: invalid BACKUP_KEEP_DAYS %r, keeping %s" % (
                self.settings.get("backup_keep_days"), self.backup_cfg["keep_days"]))
            return
        if not cron.is_off(schedule):
            try:
                cron.parse(schedule)
            except ValueError as exc:
                self.log("backups: %s (keeping %r)" % (exc, self.backup_cfg["schedule"]))
                return
        schedule = "off" if cron.is_off(schedule) else schedule
        if schedule != self.backup_cfg["schedule"] or keep != self.backup_cfg["keep_days"]:
            if schedule == "off":
                self.log("scheduled backups: off")
            else:
                self.log("scheduled backups: %r (keeping %d days)" % (schedule, keep))
        self.backup_cfg = {"schedule": schedule, "keep_days": keep}
        self._backup_next = None
        self._sched_wake.set()

    def start_scheduler(self):
        self.configure_backup()
        if self._sched_thread is not None and self._sched_thread.is_alive():
            return
        self._sched_thread = threading.Thread(target=self._scheduler_loop, name="backup-scheduler", daemon=True)
        self._sched_thread.start()

    def _last_backup_at(self):
        """When the last backup was attempted (naive local time), from status.json; None without history."""
        status = self._read_backup_status()
        entry = status.get("last") or status.get("last_ok") or {}
        raw = entry.get("at")
        try:
            if isinstance(raw, (int, float)):
                moment = datetime.fromtimestamp(raw, self._tz())
            else:
                moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if moment.tzinfo is not None:
                moment = moment.astimezone(self._tz())
            return moment.replace(tzinfo=None)
        except (TypeError, ValueError, OSError, OverflowError):
            return None

    def _scheduler_loop(self):
        # A slot missed while the container was down is made up once, shortly after start
        # (not once per missed slot, and not on a fresh install with no history).
        catchup_at = None
        schedule = self.backup_cfg["schedule"]
        if not cron.is_off(schedule):
            last = self._last_backup_at()
            if last is not None and cron.next_run(schedule, last) <= self.now_local():
                catchup_at = time.monotonic() + self.catchup_delay
        while not self.stopping.is_set():
            self._sched_wake.clear()  # before reading the config: a reload in between wakes the wait below
            schedule = self.backup_cfg["schedule"]
            if cron.is_off(schedule):
                catchup_at = None
                self._backup_next = None
            else:
                now = self.now_local()
                if self._backup_next is None:
                    self._backup_next = cron.next_run(schedule, now)
                if now >= self._backup_next:
                    # next slot from *now*: a repeated hour (DST) or a clock jump never fires twice
                    self._backup_next = cron.next_run(schedule, now)
                    catchup_at = None
                    if not self.start_backup("scheduled"):
                        self.log("backup slot skipped: another backup is still running")
                elif catchup_at is not None and time.monotonic() >= catchup_at:
                    catchup_at = None
                    self.log("a scheduled backup was missed while stopped: running it now")
                    self.start_backup("scheduled")
            self._sched_wake.wait(self.sched_tick)

    def _backup_env(self) -> dict:
        extra = {k: v for k, v in self.env.items() if k.startswith(("HSE_", "BACKUP_", "HEADSCALE_", "PG"))}
        extra.update(HSE_DATA_DIR=self.data_dir, BACKUP_SCHEDULE=self.backup_cfg["schedule"],
                     BACKUP_KEEP_DAYS=str(self.backup_cfg["keep_days"]))
        if self.settings.get("tz"):
            extra["TZ"] = str(self.settings["tz"])
        return base_env(extra)

    def start_backup(self, trigger: str = "manual") -> bool:
        """Start ``aio/backup.py create`` in the background. False when one is already running
        (or in setup mode / while stopping). A run started outside the supervisor
        (``hse backup``) holds the same lock in backup.py and makes this run exit 3: it is logged, not retried."""
        if self.mode != "run" or self.stopping.is_set():
            return False
        with self._backup_lock:
            if self._backup_running:
                return False
            self._backup_running = True
        threading.Thread(target=self._backup_worker, args=(trigger,), name="backup", daemon=True).start()
        return True

    def _backup_worker(self, trigger: str):
        timer = None
        try:
            argv = [*BACKUP_CMD, "create", "--trigger", trigger]
            self.log("backup started (%s)" % trigger)
            try:
                proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, env=self._backup_env())
            except OSError as exc:
                self.log("backup could not start: %s" % exc)
                return
            self._backup_proc = proc
            timer = threading.Timer(BACKUP_TIMEOUT, proc.kill)
            timer.daemon = True
            timer.start()
            for raw in proc.stdout:
                line = _ANSI.sub("", raw.decode(errors="replace")).rstrip()
                if line:
                    self.sink(line if line.startswith("[backup]") else "[backup] " + line)
            code = proc.wait()
            if code == 0:
                self.log("backup finished")
            elif code == 3:
                self.log("backup skipped: another backup is running")
            else:
                self.log("backup FAILED (exit %s)" % code)
        except Exception:  # noqa: BLE001
            log.exception("backup worker crashed")
        finally:
            if timer:
                timer.cancel()
            self._backup_proc = None
            with self._backup_lock:
                self._backup_running = False

    def _read_backup_status(self) -> dict:
        try:
            with open(os.path.join(self.data_dir, "backups", "status.json"), encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def backup_summary(self) -> dict:
        cfg = self.backup_cfg
        enabled = not cron.is_off(cfg["schedule"])
        nxt = None
        if enabled:
            nxt = self._backup_next or cron.next_run(cfg["schedule"], self.now_local())
        status = self._read_backup_status()
        return {"enabled": enabled, "schedule": cfg["schedule"], "next_run": nxt.isoformat() if nxt else None,
                "keep_days": cfg["keep_days"], "running": self._backup_running,
                "env_locked": self.backup_env_locked(),
                "last": status.get("last"), "last_ok": status.get("last_ok"),
                "count": status.get("count", 0), "bytes": status.get("bytes", 0)}

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
        for name in ("helper.sock", "supervisor.pid"):
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
            self.serve_helper()
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
