"""Readable names for machines that report "localhost".

The Tailscale apps for iOS, iPadOS, tvOS and the App Store build for macOS
run sandboxed and often cannot read the device name, so they register as
"localhost" (and Headscale turns the next ones into "localhost-<random>").
Tailscale's own control server hides this; Headscale does not.

A background thread renames those machines once to "<owner>-<device>",
e.g. "ana-iphone" or "leo-mac". Only names that still look like localhost
are touched: a name somebody chose is never changed.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse

import audit
import headscale as hs

log = logging.getLogger("headscale-easy")

ENABLED = os.environ.get("AUTO_RENAME_LOCALHOST", "true").lower() not in ("0", "false", "no")
INTERVAL = 30  # seconds

LOCALHOST_RE = re.compile(r"^localhost(-[a-z0-9]+)?$")


def is_placeholder(name: str) -> bool:
    return bool(LOCALHOST_RE.fullmatch((name or "").lower()))


def device_kind(hostinfo: dict) -> str:
    """'iphone', 'ipad', 'mac', 'apple-tv'... from the reported OS and model."""
    os_name = (hostinfo.get("OS") or "").lower()
    model = (hostinfo.get("DeviceModel") or "").lower().replace(" ", "")
    for prefix, kind in (("iphone", "iphone"), ("ipad", "ipad"), ("appletv", "apple-tv"),
                         ("macbook", "macbook"), ("imac", "imac"), ("macmini", "mac-mini"),
                         ("macstudio", "mac-studio"), ("macpro", "mac-pro"), ("mac", "mac")):
        if model.startswith(prefix):
            return kind
    return {"ios": "iphone", "ipados": "ipad", "tvos": "apple-tv", "macos": "mac",
            "darwin": "mac", "android": "android", "windows": "windows"}.get(os_name, "device")


def _label(text: str) -> str:
    """DNS-safe label: lowercase letters, digits and hyphens."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    return re.sub(r"-{2,}", "-", text)


def suggested_name(owner: str, kind: str, taken: set[str]) -> str:
    base = _label(f"{owner.split('@')[0]}-{kind}")[:55].strip("-") or kind
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def rename_placeholders() -> int:
    """One pass: rename every machine still called localhost. Returns how many."""
    nodes = hs.all_nodes()
    pending = [n for n in nodes if is_placeholder(n.get("givenName") or n.get("name") or "")]
    if not pending:
        return 0
    log.info("found %d machine(s) named localhost: %s", len(pending),
             ", ".join(f"{n['id']}={n.get('givenName') or n.get('name')}" for n in pending))
    details = hs.host_details([str(n["id"]) for n in pending])
    taken = {(n.get("givenName") or "").lower() for n in nodes}
    renamed = 0
    for node in pending:
        hostinfo = details.get(str(node["id"]), {}).get("hostinfo", {})
        owner = (node.get("user") or {}).get("name") or "user"
        name = suggested_name(owner, device_kind(hostinfo), taken)
        try:
            hs.api("POST", f"/node/{node['id']}/rename/{urllib.parse.quote(name)}")
        except urllib.error.HTTPError as exc:
            log.warning("could not rename machine %s (%s): %s", node["id"], node.get("givenName"), hs.api_error(exc))
            continue
        taken.add(name)
        renamed += 1
        log.info("renamed machine %s from %s to %s", node["id"], node.get("givenName"), name)
        audit.record(audit.SYSTEM, "machine.rename", name, {"from": node.get("givenName"), "to": name}, ref=f"node:{node['id']}")
    return renamed


def _loop() -> None:
    while True:
        try:
            rename_placeholders()
        except Exception as exc:  # noqa: BLE001 - keep the thread alive
            log.warning("automatic machine naming failed: %s", exc)
        time.sleep(INTERVAL)


def start() -> None:
    log.info("automatic naming of machines called localhost: %s", "on" if ENABLED else "off")
    if ENABLED:
        threading.Thread(target=_loop, name="naming", daemon=True).start()
