#!/usr/bin/env python3
"""aio/backup.py: one consistent .tar.gz of everything that matters in /data.

    python aio/backup.py create [--trigger scheduled|manual] [--out DIR]

Exit codes: 0 ok, 1 failed, 3 another backup is running.

Archive layout (same top-level names as backup/backup.sh, plus console/ and meta.json):

    headscale-easy-<YYYYmmdd-HHMMSS>/
      meta.json      format, edition, created, headscale_version, db_type, files {path: sha256}
      config/        settings.json config.yaml Caddyfile derp.yaml session-secret
      headscale/     db.sqlite | headscale.sql, *.key
      console/       accounts.db api-key
      web/           audit.db
      caddy/pki/     the internal CA, when there is one

Not backed up: console/sessions.db (a restored session would revive revoked
logins), Caddy certificates other than the CA, logs, backups/ itself.
Archives hold every secret: the directory is 700 and the files 600.
Standard library only.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime

FORMAT = 2
EDITION = "aio"
ARCHIVE_GLOB = "headscale-easy-*.tar.gz"
ARCHIVE_RE = re.compile(r"^headscale-easy-.+\.tar\.gz$")
STATUS_FILE = "status.json"
LOCK_FILE = ".lock"
PART_MAX_AGE = 3600.0

# (source relative to /data, archive path, kind, required)
_FILES = (
    ("config/settings.json", "config/settings.json", "file", False),
    ("config/config.yaml", "config/config.yaml", "file", False),
    ("config/Caddyfile", "config/Caddyfile", "file", False),
    ("config/derp.yaml", "config/derp.yaml", "file", False),
    ("config/session-secret", "config/session-secret", "file", False),
    ("console/accounts.db", "console/accounts.db", "sqlite", False),
    ("console/api-key", "console/api-key", "file", False),
    ("console/audit.db", "web/audit.db", "sqlite", False),
)


class BackupBusy(Exception):
    """Another backup (or restore) holds the lock."""


class BackupError(Exception):
    pass


@dataclass
class Result:
    ok: bool
    path: str | None = None
    size: int = 0
    duration: float = 0.0
    files: int = 0
    error: str | None = None


# -- helpers ------------------------------------------------------------------
def default_data_dir() -> str:
    return os.environ.get("HSE_DATA_DIR", "/data")


def backups_dir(data_dir: str) -> str:
    return os.path.join(data_dir, "backups")


def _ensure_dir(path: str):
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_settings(data_dir: str) -> dict:
    try:
        from aio import render
    except ImportError:
        import render  # type: ignore
    return render.load_settings(os.environ, render.settings_file(data_dir))


@contextlib.contextmanager
def lock(out_dir: str):
    """flock on <out_dir>/.lock; BackupBusy when someone else holds it."""
    _ensure_dir(out_dir)
    fd = os.open(os.path.join(out_dir, LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise BackupBusy("a backup is already running") from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# -- status ---------------------------------------------------------------------
def read_status(out_dir: str) -> dict:
    try:
        with open(os.path.join(out_dir, STATUS_FILE), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _archives(out_dir: str) -> list[str]:
    try:
        names = os.listdir(out_dir)
    except OSError:
        return []
    return sorted(n for n in names if ARCHIVE_RE.match(n) and not n.startswith("."))


def _write_status(out_dir: str, entry: dict):
    prev = read_status(out_dir)
    names = _archives(out_dir)
    total = 0
    for n in names:
        try:
            total += os.path.getsize(os.path.join(out_dir, n))
        except OSError:
            pass
    status = {"last": entry, "last_ok": entry if entry["ok"] else prev.get("last_ok"),
              "count": len(names), "bytes": total}
    tmp = os.path.join(out_dir, ".%s.tmp" % STATUS_FILE)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(status, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, os.path.join(out_dir, STATUS_FILE))


# -- copying -----------------------------------------------------------------------
def _sqlite_copy(src: str, dst: str):
    """Consistent online copy (copying db + -wal by hand is not), then integrity_check on the copy."""
    os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
    uri = "file:%s?mode=ro" % src.replace("?", "%3f").replace("#", "%23")
    source = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        dest = sqlite3.connect(dst)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    check = sqlite3.connect(dst)
    try:
        row = check.execute("PRAGMA integrity_check").fetchone()
    finally:
        check.close()
    if not row or row[0] != "ok":
        raise BackupError("integrity check failed for %s" % os.path.basename(src))
    os.chmod(dst, 0o600)


def _file_copy(src: str, dst: str):
    os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o600)


def _pg_dump(settings: dict, dst: str):
    exe = shutil.which("pg_dump")
    if not exe:
        raise BackupError("pg_dump is not installed in this image: back up PostgreSQL "
                          "with its own tools or the backup sidecar (BACKUP_MODE=create)")
    env = dict(os.environ)
    env.update(PGHOST=settings.get("pg_host", ""), PGPORT=str(settings.get("pg_port") or "5432"),
               PGUSER=settings.get("pg_user") or "headscale", PGDATABASE=settings.get("pg_name") or "headscale",
               PGPASSWORD=settings.get("pg_pass", ""), PGSSLMODE=settings.get("pg_sslmode") or "disable")
    os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
    with open(dst, "wb") as out:
        proc = subprocess.run([exe, "--no-owner", "--clean", "--if-exists"], stdout=out,
                              stderr=subprocess.PIPE, env=env, timeout=600)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace")
        if "version mismatch" in err.lower():
            found = re.search(r"server version: *([0-9]+)[0-9.]*; *pg_dump version: *([0-9]+)", err)
            if found:
                raise BackupError("PostgreSQL is %s and this image can dump up to %s: back it up with the "
                                  "sidecar (BACKUP_MODE=create) or a newer image" % found.groups())
            raise BackupError("pg_dump is older than the PostgreSQL server: use a client of the "
                              "same or a newer major version")
        raise BackupError("pg_dump failed: %s" % (err.strip().splitlines() or ["unknown error"])[-1][:300])
    _make_portable(dst)
    os.chmod(dst, 0o600)


# pg_dump 17 and later write this line whatever the server is; a PostgreSQL 16 does not know the setting and
# refuses the whole restore. It only switches off a time limit that is off by default, so the dump goes without.
_NOT_PORTABLE = (b"SET transaction_timeout = 0;",)


def _make_portable(path: str):
    """Rewrite the dump without the lines an older server rejects, and check it holds the nodes table.
    Streams it: a dump can be large."""
    tmp = path + ".portable"
    found = False
    with open(path, "rb") as src, open(tmp, "wb") as out:
        for line in src:
            if line.rstrip(b"\r\n") in _NOT_PORTABLE:
                continue
            if b"CREATE TABLE public.nodes" in line:
                found = True
            out.write(line)
    if not found:
        os.unlink(tmp)
        raise BackupError("the Headscale dump has no nodes table")
    os.replace(tmp, path)


def _headscale_version() -> str | None:
    exe = os.environ.get("HSE_HEADSCALE_BIN") or shutil.which("headscale")
    if not exe:
        return None
    try:
        proc = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\d+\.\d+\.\d+\S*", proc.stdout) if proc.returncode == 0 else None
    return match.group(0) if match else None


def _settings_snapshot(settings: dict, dst: str):
    """Headless start (env vars, no wizard): there is no settings.json on disk, so the archive
    carries the effective settings instead. A restore then starts in run mode on an empty volume."""
    if not settings.get("public_url"):
        raise BackupError("missing config/settings.json")  # setup mode: nothing to back up yet
    os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2, sort_keys=True)
    os.chmod(dst, 0o600)


def _collect(data_dir: str, root: str, settings: dict, require_settings: bool = True):
    """Fill ``root`` (the archive's top directory); returns the db type."""
    for rel, dest, kind, required in _FILES:
        src = os.path.join(data_dir, rel)
        if not os.path.isfile(src):
            if rel == "config/settings.json":
                if require_settings or settings.get("public_url"):
                    _settings_snapshot(settings, os.path.join(root, dest))
            elif required:
                raise BackupError("missing %s" % rel)
            continue
        (_sqlite_copy if kind == "sqlite" else _file_copy)(src, os.path.join(root, dest))
    hs = os.path.join(data_dir, "headscale")
    db_type = settings.get("db_type") or "sqlite"
    if db_type == "postgres":
        _pg_dump(settings, os.path.join(root, "headscale", "headscale.sql"))
    else:
        db = os.path.join(hs, "db.sqlite")
        if not os.path.isfile(db):
            raise BackupError("missing headscale/db.sqlite")
        _sqlite_copy(db, os.path.join(root, "headscale", "db.sqlite"))
    if os.path.isdir(hs):
        for name in sorted(os.listdir(hs)):
            path = os.path.join(hs, name)
            if name.endswith(".key") and os.path.isfile(path):
                _file_copy(path, os.path.join(root, "headscale", name))
    pki = os.path.join(data_dir, "caddy", "caddy", "pki")
    if os.path.isdir(pki):
        for dirpath, _dirs, names in os.walk(pki):
            for name in names:
                src = os.path.join(dirpath, name)
                if os.path.isfile(src) and not os.path.islink(src):
                    rel = os.path.relpath(src, pki)
                    _file_copy(src, os.path.join(root, "caddy", "pki", rel))
    return db_type


def _tree_files(root: str) -> list[str]:
    out = []
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            out.append(os.path.relpath(os.path.join(dirpath, name), root))
    return sorted(out)


def _tar_filter(info: tarfile.TarInfo):
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = 0o700 if info.isdir() else 0o600
    return info


def _write_archive(workdir: str, name: str, part: str):
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as raw:
        with tarfile.open(fileobj=raw, mode="w:gz") as tar:
            tar.add(os.path.join(workdir, name), arcname=name, filter=_tar_filter)
        raw.flush()
        os.fsync(raw.fileno())


# -- verification --------------------------------------------------------------------
def verify(archive: str):
    """Read the archive back: safe names, regular members only, sha256 of every file. Raises BackupError."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            top = {m.name.split("/")[0] for m in members}
            if len(top) != 1:
                raise BackupError("unexpected layout")
            meta = None
            sums = {}
            for m in members:
                parts = m.name.split("/")
                if m.name.startswith("/") or ".." in parts:
                    raise BackupError("unsafe member name %r" % m.name)
                if not (m.isreg() or m.isdir()):
                    raise BackupError("unsupported member %r" % m.name)
                if m.isreg():
                    fh = tar.extractfile(m)
                    h = hashlib.sha256()
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                    rel = "/".join(parts[1:])
                    if rel == "meta.json":
                        fh.seek(0)
                        meta = json.loads(fh.read().decode("utf-8"))
                    else:
                        sums[rel] = h.hexdigest()
    except (OSError, tarfile.TarError, EOFError, ValueError) as exc:
        raise BackupError("cannot read the archive back: %s" % exc) from None
    if not meta or meta.get("files") != sums:
        raise BackupError("archive does not match its meta.json")


# -- retention ---------------------------------------------------------------------------
def prune(out_dir: str, keep_days: float, now: float | None = None) -> list[str]:
    """Delete archives older than keep_days (mtime). The newest archive is never deleted;
    stale .part files (older than an hour) go too. Returns the removed names."""
    now = time.time() if now is None else now
    removed = []
    names = _archives(out_dir)
    newest = max(names, key=lambda n: os.path.getmtime(os.path.join(out_dir, n)), default=None)
    limit = now - float(keep_days) * 86400
    for n in names:
        path = os.path.join(out_dir, n)
        try:
            if n != newest and os.path.getmtime(path) < limit:
                os.unlink(path)
                removed.append(n)
        except OSError:
            pass
    try:
        listing = os.listdir(out_dir)
    except OSError:
        listing = []
    for n in listing:
        if n.startswith(".headscale-easy-") and n.endswith(".part"):
            path = os.path.join(out_dir, n)
            try:
                if now - os.path.getmtime(path) > PART_MAX_AGE:
                    os.unlink(path)
                    removed.append(n)
            except OSError:
                pass
    return removed


# -- create -----------------------------------------------------------------------------
def create(data_dir=None, out_dir=None, settings=None, trigger="manual", log=None,
           require_settings=True) -> Result:
    """Make one backup. Takes the lock (BackupBusy if taken); any other failure is a Result with ok=False."""
    data_dir = data_dir or default_data_dir()
    out_dir = out_dir or backups_dir(data_dir)
    log = log or (lambda msg: print("[backup] " + msg, flush=True))
    started = time.time()
    _ensure_dir(out_dir)
    with lock(out_dir):
        result = Result(False)
        work = None
        part = None
        try:
            if settings is None:
                settings = _load_settings(data_dir)
            work = tempfile.mkdtemp(prefix=".work-", dir=out_dir)
            stamp = datetime.fromtimestamp(started).strftime("%Y%m%d-%H%M%S")
            name = "headscale-easy-" + stamp
            n = 1
            while os.path.exists(os.path.join(out_dir, name + ".tar.gz")):
                n += 1
                name = "headscale-easy-%s-%d" % (stamp, n)
            root = os.path.join(work, name)
            os.mkdir(root, 0o700)
            db_type = _collect(data_dir, root, settings, require_settings)
            files = _tree_files(root)
            meta = {"format": FORMAT, "edition": EDITION,
                    "created": datetime.fromtimestamp(started).astimezone().isoformat(timespec="seconds"),
                    "headscale_version": _headscale_version(), "db_type": db_type,
                    "files": {rel.replace(os.sep, "/"): _sha256(os.path.join(root, rel)) for rel in files}}
            with open(os.path.join(root, "meta.json"), "w", encoding="utf-8") as fh:
                json.dump(meta, fh, indent=2, sort_keys=True)
            os.chmod(os.path.join(root, "meta.json"), 0o600)
            part = os.path.join(out_dir, ".%s.tar.gz.part" % name)
            final = os.path.join(out_dir, name + ".tar.gz")
            old_umask = os.umask(0o077)
            try:
                _write_archive(work, name, part)
            finally:
                os.umask(old_umask)
            verify(part)
            os.replace(part, final)
            part = None
            result = Result(True, final, os.path.getsize(final), time.time() - started, len(files) + 1)
            log("wrote %s (%d bytes, %d files)" % (os.path.basename(final), result.size, result.files))
        except BackupError as exc:
            result = Result(False, error=str(exc))
        except (OSError, sqlite3.Error, subprocess.SubprocessError, ValueError) as exc:
            result = Result(False, error="%s: %s" % (type(exc).__name__, exc))
        finally:
            if part and os.path.exists(part):
                os.unlink(part)
            if work:
                shutil.rmtree(work, ignore_errors=True)
        result.duration = time.time() - started
        if not result.ok:
            log("FAILED: %s" % result.error)
        entry = {"at": int(started), "ok": result.ok, "trigger": trigger, "duration": round(result.duration, 2),
                 "file": os.path.basename(result.path) if result.path else None,
                 "size": result.size, "error": result.error}
        if result.ok:
            try:
                keep = int((settings or {}).get("backup_keep_days") or 14)
                for gone in prune(out_dir, keep):
                    log("removed old backup: %s" % gone)
            except (OSError, ValueError) as exc:
                log("retention failed: %s" % exc)
        try:
            _write_status(out_dir, entry)
        except OSError as exc:
            log("cannot write status.json: %s" % exc)
        return result


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="backup.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    cp = sub.add_parser("create", help="make a backup now")
    cp.add_argument("--trigger", choices=("scheduled", "manual"), default="manual")
    cp.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    try:
        res = create(trigger=args.trigger, out_dir=args.out)
    except BackupBusy:
        print("[backup] another backup is running", file=sys.stderr)
        return 3
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
