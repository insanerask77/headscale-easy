"""Server status: versions (with an update notice from GitHub's releases API,
cached 12 h), container health (hs-helper GET /status), disk use and basic
Headscale metrics (Prometheus text format). Standard library only. Every
source can fail on its own: the page shows what it has."""

from __future__ import annotations

import logging
import os
import re
import stat
import threading
import time
import urllib.request

import headscale as hs
from version import VERSION

log = logging.getLogger("headscale-easy")

METRICS_URL = os.environ.get("HEADSCALE_METRICS_URL", "http://headscale:9090/metrics")
UPDATE_CHECK = os.environ.get("STATUS_UPDATE_CHECK", "true").strip().lower() not in ("false", "0", "no", "off")
RELEASES = {
    "headscale": "https://api.github.com/repos/juanfont/headscale/releases/latest",
    "easy": "https://api.github.com/repos/insanerask77/headscale-easy/releases/latest",
}
CACHE_TTL = 12 * 3600
FAIL_TTL = 900  # retry a failed lookup sooner
def _disks_from_env(raw: str | None) -> tuple[tuple[str, str], ...]:
    """STATUS_DISKS='data:/data,headscale:/headscale' -> (("data", "/data"), ...)."""
    pairs = []
    for item in (raw or "").split(","):
        key, sep, path = item.strip().partition(":")
        if sep and key.strip() and path.strip():
            pairs.append((key.strip(), path.strip()))
    return tuple(pairs) or (("data", "/data"), ("headscale", "/headscale"))


DISKS = _disks_from_env(os.environ.get("STATUS_DISKS"))

_cache: dict[str, tuple[float, str | None]] = {}
_lock = threading.Lock()


# What aio/backup.py writes: no path separators, no leading dot (those are its work files)
BACKUP_NAME_RE = re.compile(r"^headscale-easy-[0-9A-Za-z][0-9A-Za-z._-]*\.tar\.gz$")


def open_backup(name: str):
    """An archive of BACKUP_DIR opened for reading: (file object, size), or None when the name is not one
    of ours, the directory is not configured (1.x), or the file is missing, a link or not a regular file.
    Opened with O_NOFOLLOW and checked on the descriptor, so nothing can swap it in between."""
    directory = os.environ.get("BACKUP_DIR", "")
    if not directory or not isinstance(name, str) or not BACKUP_NAME_RE.match(name):
        return None
    try:
        fd = os.open(os.path.join(directory, name), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError("not a regular file")
        return os.fdopen(fd, "rb"), st.st_size
    except OSError:
        os.close(fd)
        return None


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """'v0.26.1', 'headscale 0.27.0-beta.1' -> (0, 26, 1); None if no version.
    A pre-release suffix is ignored (0.27.0-beta.1 compares as 0.27.0)."""
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return tuple(int(p or 0) for p in match.groups()) if match else None


def is_newer(latest: str | None, current: str | None) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return bool(a and b and a > b)


def latest_release(key: str, now: float | None = None) -> str | None:
    """Tag of the latest release of 'headscale' or 'easy', or None (offline,
    rate limited, update check disabled). Cached 12 h (15 min on failure)."""
    if not UPDATE_CHECK:
        return None
    now = time.time() if now is None else now
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    try:
        tag = hs.http_json("GET", RELEASES[key], headers={"Accept": "application/vnd.github+json"},
                           timeout=5).get("tag_name")
        tag = tag if isinstance(tag, str) else None
    except (OSError, ValueError, AttributeError):
        log.info("could not look up the latest %s release", key)
        tag = None
    with _lock:
        _cache[key] = (now + (CACHE_TTL if tag else FAIL_TTL), tag)
    return tag


_SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([-+0-9.eE]+|NaN|[+-]Inf)(?:\s+\d+)?\s*$")


def parse_metrics(text: str) -> dict[str, float]:
    """Prometheus text format -> {metric name: sum over its label sets}.
    Comments and malformed or non-finite samples are skipped."""
    out: dict[str, float] = {}
    for line in (text or "").splitlines():
        match = _SAMPLE.match(line.strip())
        if not match:
            continue
        try:
            value = float(match.group(3))
        except ValueError:
            continue
        if value != value or value in (float("inf"), float("-inf")):
            continue
        out[match.group(1)] = out.get(match.group(1), 0.0) + value
    return out


def summarize_metrics(m: dict[str, float]) -> dict[str, float | None]:
    """The few numbers worth showing; None when Headscale does not expose them."""
    def first(*names: str) -> float | None:
        return next((m[n] for n in names if n in m), None)

    requests = [v for k, v in m.items()
                if k.endswith("_http_requests_total") or k == "headscale_http_duration_seconds_count"]
    return {
        "nodes": first("headscale_nodestore_nodes", "headscale_node_count"),
        "requests": sum(requests) if requests else None,
        "goroutines": first("go_goroutines"),
        "memory": first("process_resident_memory_bytes"),
    }


def fetch_metrics() -> dict[str, float | None] | None:
    try:
        req = urllib.request.Request(METRICS_URL, headers={"User-Agent": f"headscale-easy/{VERSION}"})
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - fixed internal URL
            text = resp.read(4_000_000).decode("utf-8", "replace")
    except (OSError, ValueError):
        return None
    return summarize_metrics(parse_metrics(text))


def disk_usage(path: str) -> dict | None:
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    if not total:
        return None
    used = total - free
    return {"path": path, "total": total, "used": used, "free": free, "percent": round(used * 100 / total)}


def online_nodes() -> tuple[int, int] | None:
    """(online, total) from the Headscale API."""
    try:
        nodes = hs.all_nodes()
    except Exception:  # noqa: BLE001 - the page must still render
        return None
    return sum(1 for n in nodes if n.get("online")), len(nodes)


def collect() -> dict:
    """Everything the page shows. Each part is None/empty when its source is down."""
    helper = hs.helper_status()
    hs_version = ((helper or {}).get("headscale") or {}).get("version")
    disks = []
    for key, path in DISKS:
        usage = disk_usage(path)
        if usage:
            disks.append((key, usage))
    return {
        "helper": helper,  # None: hs-helper missing or not answering
        "headscale": {"version": hs_version, "latest": latest_release("headscale")},
        "easy": {"version": VERSION, "latest": latest_release("easy")},
        "containers": (helper or {}).get("containers") or [],
        "backup": (helper or {}).get("backup"),  # all-in-one supervisor only
        "disks": disks,
        "metrics": fetch_metrics(),
        "online": online_nodes(),
        "update_check": UPDATE_CHECK,
    }
