"""Builders for backup archives used in tests (no dependency on aio/backup.py).

``build_aio_archive`` writes the format of PHASE3_EXECUTION_PLAN.md Block 1.1
(``meta.json`` with format 2, edition "aio" and a sha256 per file);
``build_1x_archive`` mimics what backup/backup.sh writes. Only low-entropy fake
keys and IDs (GitGuardian).
"""
import hashlib
import io
import json
import os
import sqlite3
import tarfile
import tempfile

TOP = "headscale-easy-20260101-030000"


def sqlite_bytes(value: str, wal_mode: bool = False) -> bytes:
    """A real SQLite file with one table ``t(v)`` holding ``value``."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "x.db")
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE t (v TEXT)")
        con.execute("INSERT INTO t VALUES (?)", (value,))
        con.commit()
        con.close()
        with open(path, "rb") as fh:
            return fh.read()


def sqlite_value(path: str):
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        return con.execute("SELECT v FROM t").fetchone()[0]
    finally:
        con.close()


def data_tree(marker: str, postgres: bool = False) -> dict:
    """Archive-relative path -> bytes for a fake installation identified by ``marker``."""
    tree = {
        "config/settings.json": json.dumps({"public_url": "http://localhost", "tls": "off",
                                            "marker": marker}).encode(),
        "config/config.yaml": ("# dns edited in the console\nmarker: %s\n" % marker).encode(),
        "config/Caddyfile": ("# caddy %s\n" % marker).encode(),
        "config/derp.yaml": b"regions: {}\n",
        "config/session-secret": ("fake-session-secret-%s\n" % marker).encode(),
        "headscale/noise_private.key": ("privkey:fake-noise-%s\n" % marker).encode(),
        "headscale/derp_server_private.key": ("privkey:fake-derp-%s\n" % marker).encode(),
        "console/accounts.db": sqlite_bytes("accounts-" + marker),
        "console/api-key": ("fake-api-key-%s\n" % marker).encode(),
        "console/mfa-required": b"optional\n",
        "web/audit.db": sqlite_bytes("audit-" + marker),
        "caddy/pki/authorities/local/root.crt": ("fake-root-ca-%s\n" % marker).encode(),
    }
    if postgres:
        tree["headscale/headscale.sql"] = b"CREATE TABLE public.nodes (id int);\n"
    else:
        tree["headscale/db.sqlite"] = sqlite_bytes("hs-" + marker)
    return tree


# where an archive path lives under /data (explicit on purpose: the module under test has its own mapping)
def disk_path(data_dir: str, rel: str) -> str:
    if rel == "web/audit.db":
        return os.path.join(data_dir, "console", "audit.db")
    if rel.startswith("caddy/pki/"):
        return os.path.join(data_dir, "caddy", "caddy", *rel.split("/")[1:])
    return os.path.join(data_dir, *rel.split("/"))


def write_data(data_dir: str, tree: dict, sessions: bool = True):
    """Lay a fake installation out on disk (what a running AIO would hold)."""
    for rel, content in tree.items():
        if rel.endswith("headscale.sql"):
            continue
        path = disk_path(data_dir, rel)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(content)
        os.chmod(path, 0o600)
    if sessions:
        sdb = os.path.join(data_dir, "console", "sessions.db")
        os.makedirs(os.path.dirname(sdb), exist_ok=True)
        with open(sdb, "wb") as fh:
            fh.write(sqlite_bytes("sessions"))
    for stale in ("headscale/db.sqlite-wal", "headscale/db.sqlite-shm", "console/accounts.db-wal"):
        with open(os.path.join(data_dir, *stale.split("/")), "wb") as fh:
            fh.write(b"stale journal")


def read_tree(data_dir: str, tree: dict) -> dict:
    out = {}
    for rel in tree:
        if rel.endswith("headscale.sql"):
            continue
        with open(disk_path(data_dir, rel), "rb") as fh:
            out[rel] = fh.read()
    return out


def build_aio_archive(path, tree=None, *, meta=None, top=TOP, add_meta=True, hashes=None, extra=()):
    """Write an AIO archive. ``meta`` updates meta.json, ``hashes`` overrides per-file sha256,
    ``extra`` is a list of (TarInfo, bytes|None) appended as is (for malicious archives)."""
    tree = data_tree("a") if tree is None else tree
    listed = {rel: hashlib.sha256(content).hexdigest() for rel, content in tree.items()}
    listed.update(hashes or {})
    doc = {"format": 2, "edition": "aio", "created": "2026-01-01T03:00:00", "headscale_version": "0.29.4",
           "db_type": "postgres" if "headscale/headscale.sql" in tree else "sqlite", "files": listed}
    doc.update(meta or {})
    with tarfile.open(path, "w:gz") as tar:
        def add(name, content):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(content))
        if add_meta:
            add("%s/meta.json" % top, json.dumps(doc).encode())
        for rel, content in tree.items():
            add("%s/%s" % (top, rel), content)
        for info, content in extra:
            tar.addfile(info, io.BytesIO(content) if content is not None else None)
    return path


def build_1x_archive(path):
    """What backup/backup.sh writes (no meta.json; config/ holds flattened project paths)."""
    files = {
        "config/.env": b"PUBLIC_URL=https://hs.example.com\nHEADSCALE_PG_PASS=fake-password\n",
        "config/headscale-config.yaml": b"server_url: https://hs.example.com\n",
        "config/Caddyfile": b"hs.example.com {\n}\n",
        "config/data_web_api-key": b"fake-api-key\n",
        "headscale/db.sqlite": sqlite_bytes("hs-1x"),
        "headscale/noise_private.key": b"privkey:fake-noise-1x\n",
        "web/audit.db": sqlite_bytes("audit-1x"),
    }
    with tarfile.open(path, "w:gz") as tar:
        for rel, content in files.items():
            info = tarfile.TarInfo("headscale-easy-20251231-030000/" + rel)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return path


if __name__ == "__main__":  # regenerate tests/fixtures/backup/*
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "backup")
    os.makedirs(here, exist_ok=True)
    build_1x_archive(os.path.join(here, "1x-archive.tar.gz"))
    build_aio_archive(os.path.join(here, "aio-archive.tar.gz"))
