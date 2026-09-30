"""ACL policy editing for the visual editor (Access controls -> Rules /
Groups & tags / Auto-approval / SSH rules / Test access).

Headscale's policy is HuJSON (JSON with '//' and '/* */' comments and
trailing commas), the same format as Tailscale's ACL file, kept behind the
Headscale API (GET/PUT /policy, POST /policy/check). There is no HuJSON
library in the standard library and no build step in this project, so this
module does two things by hand:

  - parse(): HuJSON -> a plain dict, for reading (populating the visual forms
    and driving the access simulator).
  - replace_block(): finds the exact byte range of one top-level key's VALUE
    in the original text with a small hand-written scanner (spans()) and
    replaces only that range. Comments, key order and every section the
    visual editor does not touch (postures, hosts written by hand...)
    survive untouched. This is why the visual editor never needs to
    reserialize -- and never destroys -- a hand-written policy.

evaluate() is a best-effort reachability simulator for "Test access": there
is no Headscale endpoint for it, only POST /policy/check (syntax only). It
approximates Tailscale's ACL matching (autogroup:member/self/internet,
groups, tags, hosts, users, ports) closely enough to be useful, but it is a
simulation over the saved policy, not a live packet test -- said plainly in
the UI.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections import namedtuple

from i18n import _

Verdict = namedtuple("Verdict", "allowed rule_index rule")


class PolicyError(ValueError):
    """The policy text could not be parsed as HuJSON."""


# -----------------------------------------------------------------------------
# HuJSON scanning (comments, strings, balanced brackets)
# -----------------------------------------------------------------------------

def _skip_ws_comments(text: str, i: int) -> int:
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        break
    return i


def _scan_string(text: str, i: int) -> int:
    """text[i] == '"'. Returns the index right after the closing quote."""
    n, j = len(text), i + 1
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == '"':
            return j + 1
        j += 1
    raise PolicyError(_("Unterminated string in the policy."))


def _scan_balanced(text: str, i: int) -> int:
    """text[i] in '{['. Returns the index right after the matching close,
    skipping nested strings and comments."""
    n, j, depth = len(text), i, 0
    while j < n:
        c = text[j]
        if c == '"':
            j = _scan_string(text, j)
            continue
        if c == "/" and j + 1 < n and text[j + 1] == "/":
            k = text.find("\n", j)
            j = n if k < 0 else k
            continue
        if c == "/" and j + 1 < n and text[j + 1] == "*":
            k = text.find("*/", j + 2)
            j = n if k < 0 else k + 2
            continue
        if c in "{[":
            depth += 1
            j += 1
            continue
        if c in "}]":
            depth -= 1
            j += 1
            if depth == 0:
                return j
            continue
        j += 1
    raise PolicyError(_("Unbalanced brackets in the policy."))


def _scan_value(text: str, i: int) -> int:
    c = text[i]
    if c == '"':
        return _scan_string(text, i)
    if c in "{[":
        return _scan_balanced(text, i)
    # A bare token (number, true, false, null): up to the next delimiter or comment
    n, j = len(text), i
    while j < n and text[j] not in ",}]\n" and not (text[j] == "/" and j + 1 < n and text[j + 1] in "/*"):
        j += 1
    return j


def spans(text: str) -> dict[str, tuple[int, int]]:
    """{key: (start, end)} of each top-level key's VALUE in the original
    text (comments included, byte-for-byte)."""
    i = _skip_ws_comments(text, 0)
    if i >= len(text) or text[i] != "{":
        raise PolicyError(_("The policy must be a JSON object."))
    i += 1
    out: dict[str, tuple[int, int]] = {}
    while True:
        i = _skip_ws_comments(text, i)
        if i >= len(text):
            raise PolicyError(_("Unexpected end of the policy."))
        if text[i] == "}":
            return out
        if text[i] != '"':
            raise PolicyError(_("Expected a key."))
        key_start = i
        i = _scan_string(text, i)
        key = json.loads(text[key_start:i])
        i = _skip_ws_comments(text, i)
        if i >= len(text) or text[i] != ":":
            raise PolicyError(_("Expected ':' after a key."))
        i = _skip_ws_comments(text, i + 1)
        if i >= len(text):
            raise PolicyError(_("Unexpected end of the policy."))
        val_start = i
        i = _scan_value(text, i)
        out[key] = (val_start, i)
        i = _skip_ws_comments(text, i)
        if i < len(text) and text[i] == ",":
            i = _skip_ws_comments(text, i + 1)
            continue
        if i < len(text) and text[i] == "}":
            return out
        raise PolicyError(_("Expected ',' or '}' in the policy."))


# -----------------------------------------------------------------------------
# Reading
# -----------------------------------------------------------------------------

def _strip_comments(text: str) -> str:
    """HuJSON -> JSON: drop comments and trailing commas. String contents
    (including a literal '//' inside one) are left untouched."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def parse(text: str) -> dict:
    """HuJSON -> structure. Blank text (no policy yet) -> {}."""
    text = text or ""
    if not text.strip():
        return {}
    try:
        data = json.loads(_strip_comments(text))
    except ValueError as exc:
        raise PolicyError(str(exc)) from exc
    if not isinstance(data, dict):
        raise PolicyError(_("The policy must be a JSON object."))
    return data


# -----------------------------------------------------------------------------
# Writing: render a block, splice it back into the original text
# -----------------------------------------------------------------------------

def render_acls(rules: list[dict]) -> str:
    if not rules:
        return "[]"
    items = []
    for r in rules:
        fields = [f'"action": {json.dumps(r.get("action") or "accept")}']
        if r.get("proto"):
            fields.append(f'"proto": {json.dumps(r["proto"])}')
        fields.append(f'"src": {json.dumps(list(r.get("src") or []))}')
        fields.append(f'"dst": {json.dumps(list(r.get("dst") or []))}')
        items.append("    {" + ", ".join(fields) + "}")
    return "[\n" + ",\n".join(items) + "\n  ]"


def _render_str_list_map(mapping: dict[str, list[str]]) -> str:
    if not mapping:
        return "{}"
    items = [f"    {json.dumps(name)}: {json.dumps(list(members))}" for name, members in mapping.items()]
    return "{\n" + ",\n".join(items) + "\n  }"


render_groups = _render_str_list_map
render_tag_owners = _render_str_list_map


def render_hosts(hosts: dict[str, str]) -> str:
    if not hosts:
        return "{}"
    items = [f"    {json.dumps(name)}: {json.dumps(value)}" for name, value in hosts.items()]
    return "{\n" + ",\n".join(items) + "\n  }"


def render_auto_approvers(cfg: dict) -> str:
    """cfg: {'routes': {cidr: [approvers]}, 'exitNode': [approvers]}. Both
    parts live under the single top-level 'autoApprovers' key, so this
    renders the whole object -- replace_block() only splices whole top-level
    values, not their nested keys."""
    parts = []
    routes = cfg.get("routes") or {}
    if routes:
        items = [f"      {json.dumps(cidr)}: {json.dumps(list(approvers))}" for cidr, approvers in routes.items()]
        parts.append('    "routes": {\n' + ",\n".join(items) + "\n    }")
    exit_node = cfg.get("exitNode") or []
    if exit_node:
        parts.append(f'    "exitNode": {json.dumps(list(exit_node))}')
    if not parts:
        return "{}"
    return "{\n" + ",\n".join(parts) + "\n  }"


def render_ssh_rules(rules: list[dict]) -> str:
    if not rules:
        return "[]"
    items = []
    for r in rules:
        fields = [f'"action": {json.dumps(r.get("action") or "accept")}',
                  f'"src": {json.dumps(list(r.get("src") or []))}',
                  f'"dst": {json.dumps(list(r.get("dst") or []))}',
                  f'"users": {json.dumps(list(r.get("users") or []))}']
        if r.get("checkPeriod"):
            fields.append(f'"checkPeriod": {json.dumps(r["checkPeriod"])}')
        items.append("    {" + ", ".join(fields) + "}")
    return "[\n" + ",\n".join(items) + "\n  ]"


def replace_block(text: str, key: str, rendered: str) -> str:
    """Replace (or insert) the value of a top-level key. rendered is the
    value expression only ('[...]' or '{...}'), no key and no trailing comma.
    Everything else in the text -- comments, key order, hosts, postures,
    whatever the visual editor did not touch -- is preserved byte-for-byte."""
    text = text if text and text.strip() else "{}"
    open_i = _skip_ws_comments(text, 0)
    if open_i >= len(text) or text[open_i] != "{":
        raise PolicyError(_("The policy must be a JSON object."))
    ranges = spans(text)
    if key in ranges:
        start, end = ranges[key]
        return text[:start] + rendered + text[end:]
    insert_at = open_i + 1
    entry = f'\n  {json.dumps(key)}: {rendered}' + (",\n" if ranges else "\n")
    return text[:insert_at] + entry + text[insert_at:]


def split_list(value: str) -> list[str]:
    return [x.strip() for x in re.split(r"[\n,]", value or "") if x.strip()]


# -----------------------------------------------------------------------------
# Vocabulary for the datalist suggestions
# -----------------------------------------------------------------------------

def vocabulary(policy_data: dict, nodes: list[dict], users: list[dict]) -> dict:
    tags = set((policy_data.get("tagOwners") or {}).keys())
    for n in nodes:
        tags.update(n.get("tags") or [])
    return {
        "auto": ["autogroup:member", "autogroup:self", "autogroup:internet"],
        "groups": sorted((policy_data.get("groups") or {}).keys()),
        "tags": sorted(tags),
        "users": sorted({u.get("name") for u in users if u.get("name")}),
        "hosts": sorted((policy_data.get("hosts") or {}).keys()),
    }


# -----------------------------------------------------------------------------
# Rule <-> form shape
# -----------------------------------------------------------------------------

_PORT_RE = re.compile(r":(\*|\d+(?:-\d+)?(?:,\s*\d+(?:-\d+)?)*)$")
_BRACKET_RE = re.compile(r"^\[(.+)\](?::(\*|\d+(?:-\d+)?(?:,\s*\d+(?:-\d+)?)*))?$")


def _split_dst(entry: str) -> tuple[str, str]:
    """'tag:nas:445' -> ('tag:nas', '445'); '[::1]:80' -> ('::1', '80');
    no recognizable port suffix -> (entry, '*')."""
    entry = (entry or "").strip()
    m = _BRACKET_RE.match(entry)
    if m:
        return m.group(1), m.group(2) or "*"
    m = _PORT_RE.search(entry)
    if not m:
        return entry, "*"
    return entry[:m.start()], m.group(1)


def rule_view(rule: dict) -> dict:
    """Best-effort decomposition of a stored ACL rule (Headscale's shape)
    into src, base dst identities (without the port), a single port spec and
    a protocol, for the Rules form. 'editable' is False when the rule's dst
    entries mix different ports -- such a rule can still be deleted from the
    visual editor, but only edited in Advanced (HuJSON)."""
    dst_entries = rule.get("dst") or []
    bases, ports = [], set()
    for entry in dst_entries:
        host, port = _split_dst(entry)
        bases.append(host)
        ports.add(port)
    return {
        "src": list(rule.get("src") or []),
        "dst": bases,
        "port": next(iter(ports), "*") if len(ports) <= 1 else "*",
        "proto": rule.get("proto") or "",
        "editable": len(ports) <= 1,
    }


_SSH_RULE_KEYS = {"action", "src", "dst", "users", "checkPeriod"}


def ssh_rule_view(rule: dict) -> dict:
    """A stored SSH rule (Headscale's shape) as-is for the SSH rules form.
    'editable' is False when the rule uses a field the form does not cover
    (acceptEnv, or anything else beyond action/src/dst/users/checkPeriod) --
    such a rule can still be deleted from the visual editor, but only edited
    in Advanced (HuJSON)."""
    return {
        "action": rule.get("action") or "accept",
        "src": list(rule.get("src") or []),
        "dst": list(rule.get("dst") or []),
        "users": list(rule.get("users") or []),
        "checkPeriod": rule.get("checkPeriod") or "",
        "editable": set(rule.keys()) <= _SSH_RULE_KEYS,
    }


# -----------------------------------------------------------------------------
# Access simulator
# -----------------------------------------------------------------------------

def _safe_ip(value: str):
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _ip_in_principal(spec: str, principal: dict) -> bool:
    try:
        net = ipaddress.ip_network(spec, strict=False)
    except ValueError:
        return spec in principal["ips"]
    return any(ip and ip in net for ip in (_safe_ip(v) for v in principal["ips"]))


def _resolve_principal(spec: str, nodes: list[dict], users: list[dict], policy_data: dict) -> dict:
    """Best-effort identity of a typed src/dst string: a machine name, a user
    (alice@ or a bare user name), a tag, a declared host alias, or a raw
    IP/CIDR."""
    spec = (spec or "").strip()
    node = next((n for n in nodes if (n.get("givenName") or n.get("name")) == spec), None)
    if node:
        return {"owner": (node.get("user") or {}).get("name"), "tags": set(node.get("tags") or []),
                "ips": set(node.get("ipAddresses") or [])}
    if spec.endswith("@") and not spec.startswith("tag:"):
        return {"owner": spec[:-1], "tags": set(), "ips": set()}
    user = next((u for u in users if u.get("name") == spec), None)
    if user:
        return {"owner": user["name"], "tags": set(), "ips": set()}
    if spec.startswith("tag:"):
        return {"owner": None, "tags": {spec}, "ips": set()}
    hosts = policy_data.get("hosts") or {}
    if spec in hosts:
        return {"owner": None, "tags": set(), "ips": {hosts[spec]}}
    return {"owner": None, "tags": set(), "ips": {spec} if spec else set()}


def _entry_matches(entry: str, principal: dict, policy_data: dict, self_owner: str | None) -> bool:
    entry = (entry or "").strip()
    if entry == "*":
        return True
    if entry == "autogroup:member":
        return principal["owner"] is not None and not principal["tags"]
    if entry in ("autogroup:self", "autogroup:self:*"):
        return self_owner is not None and principal["owner"] == self_owner
    if entry == "autogroup:internet":
        return False  # only meaningful as a dst through an exit node; not modeled
    if entry.startswith("group:"):
        members = (policy_data.get("groups") or {}).get(entry) or []
        owner = principal["owner"]
        return owner is not None and (f"{owner}@" in members or owner in members)
    if entry.startswith("tag:"):
        return entry in principal["tags"]
    if entry.endswith("@"):
        return principal["owner"] == entry[:-1]
    hosts = policy_data.get("hosts") or {}
    if entry in hosts:
        return _ip_in_principal(hosts[entry], principal)
    return _ip_in_principal(entry, principal)


def _proto_matches(rule_proto: str | None, requested: str | None) -> bool:
    if not requested:
        return True
    requested = requested.lower()
    if rule_proto:
        return rule_proto.lower() == requested
    return requested in ("tcp", "udp")  # Headscale's default when proto is omitted


def _port_matches(spec: str, requested: str | None) -> bool:
    if not requested:
        return True
    if spec == "*":
        return True
    try:
        want = int(requested)
    except ValueError:
        return False
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            if lo.isdigit() and hi.isdigit() and int(lo) <= want <= int(hi):
                return True
        elif part.isdigit() and int(part) == want:
            return True
    return False


def evaluate(policy_data: dict, nodes: list[dict], users: list[dict], src_text: str, dst_text: str,
            port: str | None = None, proto: str | None = None) -> Verdict:
    """Whether src_text can reach dst_text (optionally on a given port and
    protocol), and the rule that allowed it. A simulation over policy_data,
    not a live test."""
    acls = policy_data.get("acls") or []
    if not acls:
        return Verdict(True, None, None)
    src = _resolve_principal(src_text, nodes, users, policy_data)
    dst = _resolve_principal(dst_text, nodes, users, policy_data)
    for idx, rule in enumerate(acls):
        if not _proto_matches(rule.get("proto"), proto):
            continue
        if not any(_entry_matches(e, src, policy_data, None) for e in rule.get("src") or []):
            continue
        for entry in rule.get("dst") or []:
            host_spec, port_spec = _split_dst(entry)
            if _entry_matches(host_spec, dst, policy_data, src["owner"]) and _port_matches(port_spec, port):
                return Verdict(True, idx, rule)
    return Verdict(False, None, None)
