"""Restore a backup made by the all-in-one image (Phase 3, Block 3).

    inspect(archive)                 validate an archive, return its meta.json
    restore(archive, data_dir)       put it back into /data (the stack must be stopped)
    request_online(archive)          ask a running supervisor to do it (SIGUSR1)

Nothing in ``data_dir`` changes until the archive has been fully validated
(safe member names, sha256 of every file against ``meta.json``, SQLite
``integrity_check`` on every database). The apply step moves the current files
aside, replaces them one by one and puts them back if anything fails; a safety
copy of the current data is taken first.

The archive format is the one written by aio/backup.py (``meta.json`` with
``format: 2``, ``edition: "aio"`` and a sha256 per file); this module reads it
on its own. Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import zlib

log = logging.getLogger("hse.restore")

FORMAT = 2
EDITION = "aio"
MAX_FILES = 20000
MAX_TOTAL = 8 * 1024 ** 3
STAGING = ".restore-staging"
ROLLBACK = ".restore-rollback"
RESTORE_REQUEST = "restore.json"
RESTORE_RESULT = "restore-result.json"

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_FLAT_DIRS = ("config", "headscale", "console")
_SQLITE = (".db", ".sqlite")


class RestoreError(Exception):
    """The archive cannot be restored; the message is meant for the operator."""


def _run_dir(run_dir=None):
    return run_dir or os.environ.get("HSE_RUN_DIR", "/run/hse")


def _backup():
    """aio/backup.py, imported lazily (it is only needed for the safety copy and the lock)."""
    try:
        from aio import backup  # noqa: PLC0415
    except ImportError:  # run as a script from aio/
        import backup  # type: ignore  # noqa: PLC0415
    return backup


# -- reading an archive -------------------------------------------------------------------

def _member_path(name: str) -> tuple[str, str]:
    """(top directory, relative path) of a member name, or RestoreError."""
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        raise RestoreError("unsafe member name in the archive: %r" % name)
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise RestoreError("unsafe member name in the archive: %r" % name)
    return parts[0], "/".join(parts[1:])


def _allowed(rel: str, is_dir: bool) -> bool:
    """Only the layout of Block 1.1 (flat config/headscale/console, web/audit.db, caddy/pki/**)."""
    parts = rel.split("/")
    if not all(_NAME.match(p) for p in parts):
        return False
    if rel == "meta.json":
        return not is_dir
    top = parts[0]
    if top in _FLAT_DIRS:
        return len(parts) == 1 if is_dir else len(parts) == 2
    if top == "web":
        return len(parts) == 1 if is_dir else rel == "web/audit.db"
    if top == "caddy":
        return len(parts) == 1 or parts[1] == "pki"
    return False


def _unpack(archive: str, dest: str) -> dict[str, str]:
    """Extract by hand into ``dest`` (700/600); returns {relative path: sha256}.

    No ``extractall``: every member is checked first, and only plain files and
    directories under the expected names are written.
    """
    hashes: dict[str, str] = {}
    top = None
    total = count = 0
    unexpected = None  # first member with a name outside the layout; reported after the 1.x check
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for m in tar:
                count += 1
                if count > MAX_FILES:
                    raise RestoreError("the archive has too many members")
                if not (m.isfile() or m.isdir()):
                    raise RestoreError("the archive has a member that is not a plain file: %r" % m.name)
                t, rel = _member_path(m.name)
                if top is None:
                    top = t
                elif t != top:
                    raise RestoreError("the archive has more than one top-level directory")
                if not rel:
                    if not m.isdir():
                        raise RestoreError("unexpected file at the top of the archive: %r" % m.name)
                    continue
                if not _allowed(rel, m.isdir()):
                    unexpected = unexpected or m.name
                    continue  # never written
                target = os.path.join(dest, *rel.split("/"))
                if m.isdir():
                    os.makedirs(target, mode=0o700, exist_ok=True)
                    continue
                total += m.size
                if total > MAX_TOTAL:
                    raise RestoreError("the archive is too large")
                os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
                if os.path.lexists(target):
                    raise RestoreError("duplicate member in the archive: %r" % m.name)
                digest = hashlib.sha256()
                src = tar.extractfile(m)
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as out:
                    for chunk in iter(lambda: src.read(1 << 20), b""):
                        digest.update(chunk)
                        out.write(chunk)
                hashes[rel] = digest.hexdigest()
    except RestoreError:
        raise
    except (tarfile.TarError, EOFError, zlib.error, OSError) as exc:
        raise RestoreError("not a readable backup archive: %s" % exc) from exc
    if top is None:
        raise RestoreError("the archive is empty")
    if "meta.json" not in hashes:
        raise RestoreError(
            "this is not an all-in-one backup (no meta.json). A Headscale Easy 1.x backup is restored with "
            "scripts/restore.sh on the 1.x stack, or moved to 2.0 with the migration (Phase 5).")
    if unexpected:
        raise RestoreError("unexpected member in the archive: %r" % unexpected)
    return hashes


def _sqlite_ok(path: str) -> bool:
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        try:
            return con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            con.close()
    except sqlite3.Error:
        return False


def _validate(root: str, hashes: dict[str, str]) -> dict:
    """meta.json, sha256 of every file, required files, SQLite integrity."""
    try:
        with open(os.path.join(root, "meta.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError) as exc:
        raise RestoreError("meta.json is not valid JSON: %s" % exc) from exc
    if not isinstance(meta, dict) or not isinstance(meta.get("files"), dict):
        raise RestoreError("meta.json is malformed")
    if meta.get("edition") != EDITION:
        raise RestoreError("this backup is from the %r edition, not the all-in-one image" % meta.get("edition"))
    if meta.get("format") != FORMAT:
        raise RestoreError("unsupported backup format %r (this version reads format %d)" % (meta.get("format"), FORMAT))
    listed = meta["files"]
    actual = {rel: h for rel, h in hashes.items() if rel != "meta.json"}
    if set(listed) != set(actual):
        extra = sorted(set(actual) - set(listed))
        missing = sorted(set(listed) - set(actual))
        raise RestoreError("the archive does not match meta.json (unlisted: %s; missing: %s)"
                           % (extra or "none", missing or "none"))
    for rel, digest in actual.items():
        if listed[rel] != digest:
            raise RestoreError("checksum mismatch for %s: the archive is damaged or was modified" % rel)
    if "config/settings.json" not in actual:
        raise RestoreError("the archive has no config/settings.json")
    if "headscale/db.sqlite" not in actual and "headscale/headscale.sql" not in actual:
        raise RestoreError("the archive has no Headscale database")
    for rel in actual:
        if rel.endswith(_SQLITE) and not _sqlite_ok(os.path.join(root, *rel.split("/"))):
            raise RestoreError("the database %s in the archive fails its integrity check" % rel)
    return meta


def inspect(archive: str) -> dict:
    """Validate ``archive`` without touching any data; returns the contents of meta.json."""
    if not os.path.isfile(archive):
        raise RestoreError("backup file not found: %s" % archive)
    with tempfile.TemporaryDirectory(prefix="hse-inspect-") as tmp:
        hashes = _unpack(archive, tmp)
        return _validate(tmp, hashes)


# -- applying -------------------------------------------------------------------------------

def _target(data_dir: str, rel: str) -> str:
    """Where a file of the archive lives in /data."""
    if rel == "web/audit.db":  # the AIO keeps the console's audit log in console/
        return os.path.join(data_dir, "console", "audit.db")
    if rel.startswith("caddy/pki/"):  # XDG_DATA_HOME=/data/caddy -> /data/caddy/caddy/pki
        return os.path.join(data_dir, "caddy", "caddy", *rel.split("/")[1:])
    return os.path.join(data_dir, *rel.split("/"))


def _has_data(data_dir: str) -> bool:
    return (os.path.exists(os.path.join(data_dir, "config", "settings.json")) or
            os.path.exists(os.path.join(data_dir, "headscale", "db.sqlite")))


def _supervisor_alive(run_dir: str) -> bool:
    try:
        with open(os.path.join(run_dir, "supervisor.pid"), encoding="utf-8") as fh:
            pid = int(fh.read().strip())
        if pid == os.getpid():
            return False  # the supervisor itself, which has stopped its children
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _safety_copy(data_dir: str, out_dir: str) -> str | None:
    """A normal backup of the current data, renamed ``...-pre-restore-<time>``."""
    if not _has_data(data_dir):
        log.warning("nothing to back up yet (fresh volume): no safety copy taken")
        return None
    backup = _backup()
    result = backup.create(data_dir, out_dir=out_dir, trigger="pre-restore")
    if not getattr(result, "ok", False):
        raise RestoreError("could not take the safety copy of the current data, nothing was changed: %s"
                           % getattr(result, "error", "unknown error"))
    path = result.path
    renamed = re.sub(r"\.tar\.gz$", "-pre-restore-%s.tar.gz" % time.strftime("%Y%m%d%H%M%S"), path)
    os.replace(path, renamed)
    return renamed


def _psql_env(data_dir: str) -> dict:
    from aio import render  # noqa: PLC0415
    s = render.load_settings(os.environ, render.settings_file(data_dir))
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
    env.update(PGHOST=s.get("pg_host") or "", PGPORT=str(s.get("pg_port") or "5432"),
               PGUSER=s.get("pg_user") or "headscale", PGDATABASE=s.get("pg_name") or "headscale",
               PGSSLMODE=s.get("pg_sslmode") or "disable", PGPASSWORD=s.get("pg_pass") or "")
    return env


def _move_aside(path: str, rollback: str, data_dir: str, undo: list):
    """Move an existing file into the rollback directory, remembering how to put it back."""
    if not os.path.lexists(path):
        return
    saved = os.path.join(rollback, os.path.relpath(path, data_dir))
    os.makedirs(os.path.dirname(saved), mode=0o700, exist_ok=True)
    os.replace(path, saved)
    undo.append((path, saved))


def _rollback(undo: list, created: list):
    for path in created:
        try:
            os.unlink(path)
        except OSError:
            pass
    for path, saved in reversed(undo):
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        os.replace(saved, path)


def restore(archive: str, data_dir: str, *, offline: bool = True, with_postgres: bool = False,
            run_dir: str | None = None) -> dict:
    """Put ``archive`` back into ``data_dir``. The stack must not be running.

    ``offline=True`` refuses to run while another process is the live
    supervisor (``<run_dir>/supervisor.pid``); the supervisor itself calls this
    after stopping its children, so its own pid is accepted. ``offline=False``
    is not supported here: a running container goes through request_online().
    """
    if not offline:
        raise RestoreError("a running stack is restored through request_online() (hse restore does that)")
    if _supervisor_alive(_run_dir(run_dir)):
        raise RestoreError("the stack is running: stop the container first, or restore through the running "
                           "container with 'hse restore'")
    if not os.path.isfile(archive):
        raise RestoreError("backup file not found: %s" % archive)
    archive = os.path.abspath(archive)
    data_dir = os.path.abspath(data_dir)
    os.makedirs(data_dir, mode=0o700, exist_ok=True)
    staging = os.path.join(data_dir, STAGING)
    rollback = os.path.join(data_dir, ROLLBACK)
    for leftover in (staging, rollback):
        shutil.rmtree(leftover, ignore_errors=True)
    out_dir = os.path.join(data_dir, "backups")
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    os.makedirs(staging, mode=0o700)
    warnings: list[str] = []
    keep_rollback = False  # kept only when a rollback itself failed: it then holds the old data
    try:
        hashes = _unpack(archive, staging)
        meta = _validate(staging, hashes)
        has_sql = "headscale/headscale.sql" in hashes
        if has_sql and with_postgres and not shutil.which("psql"):
            raise RestoreError("this backup holds a PostgreSQL dump but psql is not installed in this image")
        if has_sql and not with_postgres:
            warnings.append("the PostgreSQL dump (headscale.sql) was NOT loaded; run again with --with-postgres")

        safety = _safety_copy(data_dir, out_dir)
        backup = _backup()
        undo: list[tuple[str, str]] = []
        created: list[str] = []
        try:
            with backup.lock(out_dir):
                order = sorted((r for r in hashes if r != "meta.json"),
                               key=lambda r: (0 if r.startswith("config/") else 1, r))
                # old state that must not survive: a stale db.sqlite next to a PostgreSQL dump and every
                # session (accounts were replaced, so signed-in users must sign in again)
                gone = [os.path.join(data_dir, "console", "sessions.db")]
                if has_sql:
                    gone.append(os.path.join(data_dir, "headscale", "db.sqlite"))
                replaced = [_target(data_dir, r) for r in order if r.endswith(_SQLITE)]
                for path in gone:
                    for suffix in ("", "-wal", "-shm"):
                        _move_aside(path + suffix, rollback, data_dir, undo)
                for path in replaced:  # the file is swapped below; its journal must not outlive it
                    for suffix in ("-wal", "-shm"):
                        _move_aside(path + suffix, rollback, data_dir, undo)
                for rel in order:
                    if rel.startswith("headscale/headscale.sql"):
                        continue  # loaded into PostgreSQL below, not a file of /data
                    target = _target(data_dir, rel)
                    os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
                    _move_aside(target, rollback, data_dir, undo)
                    os.replace(os.path.join(staging, *rel.split("/")), target)
                    created.append(target)
                    os.chmod(target, 0o600)
                for rel in order:  # keep every directory private
                    d = os.path.dirname(_target(data_dir, rel))
                    while len(d) > len(data_dir) and d.startswith(data_dir):
                        os.chmod(d, 0o700)
                        d = os.path.dirname(d)
                if has_sql and with_postgres:
                    proc = subprocess.run(
                        ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-f", os.path.join(staging, "headscale", "headscale.sql")],
                        env=_psql_env(data_dir), capture_output=True, text=True, timeout=600)
                    if proc.returncode != 0:
                        raise RestoreError("loading the PostgreSQL dump failed: %s" % proc.stderr.strip()[-500:])
        except BaseException as exc:
            try:
                _rollback(undo, created)
            except OSError as rexc:
                keep_rollback = True
                hint = ("; restore it by hand with 'hse restore %s'" % safety) if safety else ""
                raise RestoreError("restore failed (%s) AND the automatic rollback failed (%s). "
                                   "Your previous data is in %s%s" % (exc, rexc, rollback, hint)) from exc
            if isinstance(exc, RestoreError):
                raise
            raise RestoreError("restore failed and was rolled back: %s" % exc) from exc
        return {"ok": True, "safety_copy": safety, "files": len(hashes) - 1, "warnings": warnings,
                "created": meta.get("created"), "postgres_loaded": bool(has_sql and with_postgres)}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if not keep_rollback:
            shutil.rmtree(rollback, ignore_errors=True)


# -- online restore (through the supervisor) --------------------------------------------------

def request_online(archive: str, run_dir: str | None = None, *, with_postgres: bool = False,
                   timeout: float = 120.0) -> dict:
    """Validate here, then ask the supervisor to stop the stack, restore and start it again.

    ``hse`` never writes into /data in this mode: only the supervisor does.
    """
    run = _run_dir(run_dir)
    inspect(archive)
    try:
        with open(os.path.join(run, "supervisor.pid"), encoding="utf-8") as fh:
            pid = int(fh.read().strip())
        os.kill(pid, 0)
    except (OSError, ValueError) as exc:
        raise RestoreError("no running supervisor (%s): restore offline instead" % exc) from exc
    result_path = os.path.join(run, RESTORE_RESULT)
    request_path = os.path.join(run, RESTORE_REQUEST)
    if os.path.exists(request_path):
        raise RestoreError("another restore is already in progress")
    try:
        os.unlink(result_path)
    except OSError:
        pass
    requested = time.time()
    body = json.dumps({"archive": os.path.abspath(archive), "requested": requested,
                       "with_postgres": bool(with_postgres)})
    fd = os.open(request_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    try:
        os.kill(pid, signal.SIGUSR1)
    except OSError as exc:
        os.unlink(request_path)
        raise RestoreError("cannot signal the supervisor: %s" % exc) from exc
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(result_path, encoding="utf-8") as fh:
                result = json.load(fh)
            if result.get("requested") == requested:
                return result
        except (OSError, ValueError):
            pass
        time.sleep(0.25)
    raise RestoreError("the supervisor did not report a result in %ds; check 'docker logs'" % timeout)
