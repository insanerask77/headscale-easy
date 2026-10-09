"""The managed ``dns:`` block of Headscale's config.yaml: markers, validation and rendering.

Pure text functions, no file or process access. Reading the file and applying a change (validate, restart,
roll back) stay in headscale.py, which re-exports these names.
"""

from __future__ import annotations

import ipaddress
import re

DNS_BEGIN = "# >>> dns: managed by Headscale Easy (do not edit between these markers)"
DNS_END = "# <<< dns"

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def valid_domain(value: str) -> bool:
    return bool(DOMAIN_RE.fullmatch(value.lower()))


def valid_nameserver(value: str) -> bool:
    """An IP address or a DoH resolver (https://...)."""
    if value.startswith("https://"):
        return bool(re.fullmatch(r"https://[\w.-]+(:\d+)?(/[\w./%-]*)?", value))
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def render_dns_block(cfg: dict) -> str:
    def items(values, indent):
        return "".join(f"\n{' ' * indent}- {v}" for v in values) if values else " []"

    split = "".join(f"\n      {domain}:{items(servers, 8)}" for domain, servers in cfg.get("split", {}).items())
    records = "".join(f"\n    - name: {r['name']}\n      type: {r['type']}\n      value: {r['value']}"
                      for r in cfg.get("extra_records", [])) or " []"
    return "\n".join([
        DNS_BEGIN,
        "dns:",
        f"  magic_dns: {'true' if cfg['magic_dns'] else 'false'}",
        f"  base_domain: {cfg['base_domain']}",
        f"  override_local_dns: {'true' if cfg['override_local_dns'] else 'false'}",
        "  nameservers:",
        f"    global:{items(cfg['nameservers'], 6)}",
        f"    split:{split or ' {}'}",
        f"  search_domains:{items(cfg['search_domains'], 4)}",
        f"  extra_records:{records}",
        DNS_END,
    ])


def dns_block_present(text: str) -> bool:
    return DNS_BEGIN in text and DNS_END in text


def replace_dns_block(text: str, block: str) -> str | None:
    """Replace the marked block. None if the file has no markers."""
    start, end = text.find(DNS_BEGIN), text.find(DNS_END)
    if start < 0 or end < start:
        return None
    return text[:start] + block + text[end + len(DNS_END):]
