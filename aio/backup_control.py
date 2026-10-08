"""Backups as the supervisor serves them: the control-socket requests, the schedule and the backup process.

``BackupControl`` is a mixin of ``supervisor.Supervisor``: its methods use the supervisor's state (mode,
paths, settings, locks, the scheduler thread). The backup itself runs as its own process (aio/backup.py).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import cron
import restore as restore_mod
from processes import _ANSI, base_env

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

log = logging.getLogger("supervisor")

# The backup runs as its own process (aio/backup.py): a slow tar or an OOM must not take the supervisor down.
BACKUP_CMD = [sys.executable, os.environ.get("HSE_BACKUP_APP", os.path.join(ROOT, "aio", "backup.py"))]
DEFAULT_BACKUP_SCHEDULE = "0 3 * * *"
BACKUP_NAME_RE = re.compile(r"^headscale-easy-[0-9A-Za-z][0-9A-Za-z._-]*\.tar\.gz$")  # what backup.py writes; no path separators
BACKUP_LIST_MAX = 100
UPLOAD_TMP_RE = re.compile(r"^\.upload-[0-9a-f]{16,64}\.part$")  # what the console writes while a file is arriving
BACKUP_TIMEOUT = 3600.0  # a backup that runs longer than this is killed


class BackupControl:
    """Backup side of the supervisor (mixed into ``supervisor.Supervisor``, which owns the state used here)."""

    if TYPE_CHECKING:
        # Defined by Supervisor; declared here for type checkers only.
        data_dir: str
        run_dir: str
        env: Any
        sink: Any
        paths: dict
        mode: str | None
        stopping: threading.Event
        restore_requested: threading.Event
        restore_delay: float
        clock: Any
        sched_tick: float
        catchup_delay: float
        _op_lock: threading.Lock
        _backup_lock: threading.Lock
        _sched_wake: threading.Event

        def log(self, msg: str) -> None: ...
        def load(self) -> dict: ...

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

        The control protocol takes no body, so the console leaves the values in
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

    def be_restore(self):
        """POST /restore: the console asks to restore one of the backups in /data/backups.

        The control protocol takes no body, so the console leaves ``{"name": ...}`` in
        ``<run dir>/restore-ui.json``. The archive is validated here (the same checks as ``hse restore``);
        the restore itself is the online restore of SIGUSR1, started a moment later so the console can
        still answer its request (it is stopped, and started again, by the restore)."""
        if self.mode != "run":
            return 200, {"ok": False, "error": "restores are not available in setup mode"}
        path = os.path.join(self.run_dir, "restore-ui.json")
        try:
            with open(path, encoding="utf-8") as fh:
                name = json.load(fh).get("name")
            os.unlink(path)
        except (OSError, ValueError, AttributeError):
            return 200, {"ok": False, "error": "no request"}
        backups = os.path.join(self.data_dir, "backups")
        archive = os.path.join(backups, name) if isinstance(name, str) else ""
        if (not isinstance(name, str) or not BACKUP_NAME_RE.match(name) or os.path.islink(archive)
                or not os.path.isfile(archive)):
            return 200, {"ok": False, "error": "unknown backup", "field": "invalid"}
        if self._backup_running:
            return 200, {"ok": False, "error": "a backup is running", "field": "busy"}
        req_path = os.path.join(self.run_dir, restore_mod.RESTORE_REQUEST)
        if os.path.exists(req_path):
            return 200, {"ok": False, "error": "another restore is in progress", "field": "busy"}
        try:
            meta = restore_mod.inspect(archive)
        except restore_mod.RestoreError as exc:
            self.log("restore from the console refused: %s" % exc)
            return 200, {"ok": False, "error": str(exc), "field": "invalid"}
        # A PostgreSQL install restoring a PostgreSQL backup must load the dump, or "restored" would be a lie
        with_postgres = meta.get("db_type") == "postgres" and self.load().get("db_type") == "postgres"
        requested = time.time()
        try:
            os.unlink(os.path.join(self.run_dir, restore_mod.RESTORE_RESULT))
        except OSError:
            pass
        fd = os.open(req_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"archive": archive, "requested": requested, "with_postgres": with_postgres}, fh)
        self.log("restore of %s requested from the console" % name)
        timer = threading.Timer(self.restore_delay, self.restore_requested.set)
        timer.daemon = True
        timer.start()
        return 200, {"ok": True, "id": repr(requested)}

    def be_backup_upload(self):
        """POST /backup-upload: the console received a backup file; check it and put it with the others.

        The console streams the file into ``/data/backups/.upload-<random>.part`` and leaves
        ``{"tmp": ..., "name": <name the user's file had>}`` in ``<run dir>/backup-upload.json``. Here it gets the
        same checks as a restore (format, SHA-256 of every file, database integrity): a file that fails is deleted.
        It is kept under its own name when that is a valid, free backup name, else under a new one."""
        path = os.path.join(self.run_dir, "backup-upload.json")
        try:
            with open(path, encoding="utf-8") as fh:
                req = json.load(fh)
            os.unlink(path)
            tmp_name, orig = req["tmp"], req.get("name") or ""
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return 200, {"ok": False, "error": "no request"}
        if not isinstance(tmp_name, str) or not UPLOAD_TMP_RE.match(tmp_name):
            return 200, {"ok": False, "error": "unknown upload", "field": "invalid"}
        backups = os.path.join(self.data_dir, "backups")
        tmp = os.path.join(backups, tmp_name)
        if os.path.islink(tmp) or not os.path.isfile(tmp):
            return 200, {"ok": False, "error": "unknown upload", "field": "invalid"}

        def discard():
            try:
                os.unlink(tmp)
            except OSError:
                pass
        if self.mode != "run":
            discard()
            return 200, {"ok": False, "error": "backups are not available in setup mode"}
        try:
            restore_mod.inspect(tmp)
        except restore_mod.RestoreError as exc:
            self.log("uploaded backup refused: %s" % exc)
            discard()
            return 200, {"ok": False, "error": str(exc), "field": "invalid"}
        name = orig if isinstance(orig, str) and BACKUP_NAME_RE.match(orig) else ""
        n = 0
        while not name or os.path.exists(os.path.join(backups, name)):
            n += 1
            name = "headscale-easy-uploaded-%s%s.tar.gz" % (time.strftime("%Y%m%d-%H%M%S"), "" if n == 1 else "-%d" % n)
        os.chmod(tmp, 0o600)
        os.replace(tmp, os.path.join(backups, name))
        self.log("backup uploaded from the console: %s" % name)
        return 200, {"ok": True, "name": name}

    def backup_files(self) -> list:
        """The archives in /data/backups, newest first (name, size, mtime): what the console lists."""
        out = []
        try:
            with os.scandir(os.path.join(self.data_dir, "backups")) as it:
                for entry in it:
                    if BACKUP_NAME_RE.match(entry.name) and entry.is_file(follow_symlinks=False):
                        st = entry.stat(follow_symlinks=False)
                        out.append({"name": entry.name, "size": st.st_size, "mtime": int(st.st_mtime)})
        except OSError:
            pass
        out.sort(key=lambda f: (f["mtime"], f["name"]), reverse=True)
        return out[:BACKUP_LIST_MAX]

    def backup_env_locked(self) -> dict:
        """Which backup settings an environment variable fixes (the console cannot change those)."""
        return {"schedule": bool(str(self.env.get("BACKUP_SCHEDULE") or "").strip()),
                "keep_days": bool(str(self.env.get("BACKUP_KEEP_DAYS") or "").strip())}

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

    def _aware(self, naive: datetime) -> datetime:
        """The cron time is wall-clock time in the configured zone: give it that zone, so the console shows the right instant."""
        tz = self._tz()
        return naive.replace(tzinfo=tz) if tz else naive.astimezone()

    def backup_summary(self) -> dict:
        cfg = self.backup_cfg
        enabled = not cron.is_off(cfg["schedule"])
        nxt = None
        if enabled:
            nxt = self._backup_next or cron.next_run(cfg["schedule"], self.now_local())
        status = self._read_backup_status()
        return {"enabled": enabled, "schedule": cfg["schedule"], "next_run": self._aware(nxt).isoformat() if nxt else None,
                "keep_days": cfg["keep_days"], "running": self._backup_running,
                "env_locked": self.backup_env_locked(), "files": self.backup_files(),
                "last": status.get("last"), "last_ok": status.get("last_ok"),
                "count": status.get("count", 0), "bytes": status.get("bytes", 0)}
