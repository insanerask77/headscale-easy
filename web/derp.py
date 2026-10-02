"""DERP relays: latencies reported by the devices and an own DERP map.

Every client reports NetInfo.PreferredDERP (a region id) and NetInfo.DERPLatency
({"<region>-v4": seconds, "<region>-v6": seconds}) in its Hostinfo. The own
DERP map is a YAML file (derp.paths in config.yaml, in a marked block like the
DNS one) that Headscale merges with the embedded relay and Tailscale's public
ones. Standard library only: the file has a fixed shape that this module both
writes and reads back.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re

import headscale as hs
from i18n import _

log = logging.getLogger("headscale-easy")

DERP_FILE = os.environ.get("HEADSCALE_DERP_FILE", "/etc/headscale/derp.yaml")
DERP_FILE_IN_CONFIG = os.environ.get("HEADSCALE_DERP_FILE_IN_CONFIG", "/etc/headscale/derp.yaml")  # where Headscale sees it
BEGIN = "  # >>> derp map: managed by Headscale Easy (do not edit between these markers)"
END = "  # <<< derp map"
MIN_REGION, MAX_REGION = 900, 998  # 900-999 are for own relays; 999 is the embedded one
MAX_RELAYS = 20
_REGION_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def net_info(hostinfo: dict) -> tuple[int | None, dict[int, float]]:
    """(preferred region id, {region id: latency in ms}) from a Hostinfo. The
    lowest of the v4/v6 latencies is kept; malformed entries are skipped."""
    net = (hostinfo or {}).get("NetInfo")
    net = net if isinstance(net, dict) else {}
    pref = net.get("PreferredDERP")
    preferred = pref if isinstance(pref, int) and not isinstance(pref, bool) and pref > 0 else None
    latency: dict[int, float] = {}
    raw = net.get("DERPLatency")
    for key, value in (raw.items() if isinstance(raw, dict) else ()):
        region = str(key).split("-", 1)[0]
        if not region.isdigit() or isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            continue
        ms = round(value * 1000, 1)
        if int(region) not in latency or ms < latency[int(region)]:
            latency[int(region)] = ms
    return preferred, latency


def status(hostinfos: list[dict], regions: dict[int, str]) -> list[dict]:
    """One row per region seen in the fleet: how many devices prefer it and the
    median latency devices measure to it. Sorted by devices, then id."""
    users: dict[int, int] = {}
    samples: dict[int, list[float]] = {}
    for hi in hostinfos:
        preferred, latency = net_info(hi)
        if preferred:
            users[preferred] = users.get(preferred, 0) + 1
        for region, ms in latency.items():
            samples.setdefault(region, []).append(ms)
    rows = []
    for region in sorted(set(users) | set(samples) | set(regions)):
        values = sorted(samples.get(region, []))
        mid = len(values) // 2
        median = None
        if values:
            median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
        rows.append({"id": region, "name": regions.get(region) or _("Region {n}", n=region),
                     "devices": users.get(region, 0), "latency": round(median, 1) if median is not None else None})
    return sorted(rows, key=lambda r: (-r["devices"], r["id"]))


def _valid_ip(value: str, version: int) -> bool:
    try:
        return ipaddress.ip_address(value).version == version
    except ValueError:
        return False


def valid_host(value: str) -> bool:
    return bool(hs._DOMAIN_RE.match(value.lower())) or _valid_ip(value, 4) or _valid_ip(value, 6)


def validate(relays: list[dict]) -> str:
    """'' when the relays are valid, else a translated message."""
    if len(relays) > MAX_RELAYS:
        return _("At most {n} relays.", n=MAX_RELAYS)
    seen: set[int] = set()
    for r in relays:
        label = r.get("name") or r.get("hostname") or ""
        rid = r.get("id")
        if not isinstance(rid, int) or not MIN_REGION <= rid <= MAX_REGION:
            return _("Region ID of {name} must be between {low} and {high}.", name=label, low=MIN_REGION, high=MAX_REGION)
        if rid in seen:
            return _("Region ID {n} is used twice.", n=rid)
        seen.add(rid)
        if not _REGION_CODE_RE.match(r.get("code") or ""):
            return _("Region code of {name} must be lowercase letters, digits and dashes (max. 32).", name=label)
        name = r.get("name") or ""
        if not 1 <= len(name) <= 64 or any(ord(c) < 32 for c in name):
            return _("Every relay needs a name (max. 64 characters).")
        host = r.get("hostname") or ""
        if not valid_host(host):
            return _("Invalid hostname: {value}", value=host)
        if r.get("ipv4") and not _valid_ip(r["ipv4"], 4):
            return _("Invalid IPv4 address: {value}", value=r["ipv4"])
        if r.get("ipv6") and not _valid_ip(r["ipv6"], 6):
            return _("Invalid IPv6 address: {value}", value=r["ipv6"])
        for port in (r.get("stun_port"), r.get("derp_port")):
            if not isinstance(port, int) or not 1 <= port <= 65535:
                return _("Ports must be between 1 and 65535.")
    return ""


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid YAML scalar


def render(relays: list[dict]) -> str:
    """The derp.yaml Headscale reads: one region per relay, with one node."""
    if not relays:
        return "regions: {}\n"
    out = ["# Managed by Headscale Easy (DERP relays page).", "regions:"]
    for r in sorted(relays, key=lambda r: r["id"]):
        out += [f"  {r['id']}:",
                f"    regionid: {r['id']}",
                f"    regioncode: {_q(r['code'])}",
                f"    regionname: {_q(r['name'])}",
                "    nodes:",
                f"      - name: {_q(str(r['id']) + 'a')}",
                f"        regionid: {r['id']}",
                f"        hostname: {_q(r['hostname'])}"]
        if r.get("ipv4"):
            out.append(f"        ipv4: {_q(r['ipv4'])}")
        if r.get("ipv6"):
            out.append(f"        ipv6: {_q(r['ipv6'])}")
        out += [f"        stunport: {r['stun_port']}",
                f"        stunonly: {'true' if r.get('stun_only') else 'false'}",
                f"        derpport: {r['derp_port']}"]
    return "\n".join(out) + "\n"


_FIELDS = {"regioncode": "code", "regionname": "name", "hostname": "hostname", "ipv4": "ipv4", "ipv6": "ipv6",
           "stunport": "stun_port", "derpport": "derp_port", "stunonly": "stun_only"}


def parse(text: str) -> list[dict]:
    """Read back what render() writes (the only shape this module produces)."""
    relays: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        m = re.fullmatch(r"  (\d+):", line)
        if m:
            cur = {"id": int(m.group(1)), "code": "", "name": "", "hostname": "", "ipv4": "", "ipv6": "",
                   "stun_port": 3478, "derp_port": 443, "stun_only": False}
            relays.append(cur)
            continue
        stripped = line.strip().removeprefix("- ")
        key, sep, raw = stripped.partition(":")
        if cur is None or not sep or key not in _FIELDS:
            continue
        raw = raw.strip()
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw.strip("'\"")
        field = _FIELDS[key]
        if field in ("stun_port", "derp_port"):
            if isinstance(value, int) and not isinstance(value, bool):
                cur[field] = value
        elif field == "stun_only":
            cur[field] = value is True
        else:
            cur[field] = str(value)
    return relays


def relays() -> list[dict]:
    try:
        with open(DERP_FILE, encoding="utf-8") as fh:
            return parse(fh.read())
    except OSError:
        return []


def block_present(text: str) -> bool:
    return BEGIN in text and END in text


def render_block(enabled: bool) -> str:
    paths = f"  paths:\n    - {DERP_FILE_IN_CONFIG}" if enabled else "  paths: []"
    return f"{BEGIN}\n{paths}\n{END}"


def replace_block(text: str, enabled: bool) -> str | None:
    """None if the file has no markers (a config older than this feature)."""
    start, end = text.find(BEGIN), text.find(END)
    if start < 0 or end < start:
        return None
    return text[:start] + render_block(enabled) + text[end + len(END):]


def embedded_region() -> int | None:
    """Region id of the relay embedded in Headscale (config.yaml), if enabled."""
    try:
        with open(hs.HEADSCALE_CONFIG, encoding="utf-8") as fh:
            m = re.search(r"^\s+region_id:\s*(\d+)", fh.read(), re.M)
    except OSError:
        return None
    return int(m.group(1)) if m else None


def regions() -> dict[int, str]:
    """Names of every region: Headscale's and Tailscale's (hs.derp_regions) plus
    the own relays."""
    names = dict(hs.derp_regions())
    for r in relays():
        names[r["id"]] = r["name"]
    return names


def editable() -> str:
    """'' if the map can be edited from here, else why not (translated)."""
    try:
        with open(hs.HEADSCALE_CONFIG, encoding="utf-8") as fh:
            marked = block_present(fh.read())
        writable = os.access(hs.HEADSCALE_CONFIG, os.W_OK)
    except OSError:
        marked = writable = False
    if not marked:
        return _("config.yaml has no managed DERP block: run ./install.sh once to enable it.")
    if not (os.access(DERP_FILE, os.W_OK) if os.path.exists(DERP_FILE) else os.access(os.path.dirname(DERP_FILE), os.W_OK)):
        return _("Headscale Easy cannot write the DERP map file: run ./install.sh once to enable it.")
    if not writable:
        return _("Headscale Easy cannot write config.yaml.")
    if not hs.docker_available():
        return _("Headscale Easy has no access to Docker to restart Headscale.")
    return ""


def apply(new_relays: list[dict]) -> tuple[bool, str]:
    """Write derp.yaml and point derp.paths at it (or clear it when there are
    no relays), validate with 'headscale configtest' and restart Headscale. On
    any failure both files are restored."""
    try:
        with open(DERP_FILE, encoding="utf-8") as fh:
            previous = fh.read()
    except OSError:
        previous = None

    def write(content: str):
        with open(DERP_FILE, "w", encoding="utf-8") as fh:  # in place: it is a bind mount
            fh.write(content)

    try:
        write(render(new_relays))
    except OSError as exc:
        return False, _("Headscale Easy cannot write the DERP map file: {error}", error=exc.strerror or str(exc))
    ok, error = hs._apply_config(
        lambda text: replace_block(text, bool(new_relays)),
        _("config.yaml has no managed DERP block. Run ./install.sh once to enable it."),
        _("Headscale did not start with the new DERP map; the previous one was restored."))
    if not ok and previous is not None:
        try:
            write(previous)
        except OSError:
            log.warning("could not restore %s", DERP_FILE)
    return ok, error
