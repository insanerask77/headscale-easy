"""Supervised processes: one thread per child with restart and backoff, and the small helpers around them.

Standard library only.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time

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
