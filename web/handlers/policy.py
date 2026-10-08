"""Access-control policy, DNS, DERP and key-expiry handlers (mixin of app.Handler)."""

from __future__ import annotations

import ipaddress
import re
import urllib.error
import urllib.parse

import admin_pages
import audit
import derp
import derp_pages
import headscale as hs
import dns_pages
import settings_pages
import policy
from handlers import shared as sh
from i18n import _
from ui import BASE


def dns_cfg_from_form(form: dict, current: dict) -> tuple[dict, str]:
    """New DNS settings from a DNS page form, over the current ones, and the
    first input error ("" if none). 'section' says which form: 'rename' (tailnet
    name), 'magic' (MagicDNS on/off) or the settings (nameservers, search
    domains, custom records, local DNS). Each list arrives as repeated "...[]"
    fields, one per row; blank rows are ignored. The result keeps what was typed
    so an error can show it again."""
    cfg = {"magic_dns": bool(current.get("magic_dns")), "base_domain": current.get("base_domain") or "",
           "override_local_dns": bool(current.get("override_local_dns")),
           "nameservers": list(current.get("nameservers") or []),
           "search_domains": list(current.get("search_domains") or []),
           "split": {d: list(v) for d, v in (current.get("split") or {}).items()},
           "extra_records": [dict(r) for r in current.get("extra_records") or []]}

    def rows(name: str) -> list[str]:
        value = form.get(name + "[]") or []
        return [str(v).strip() for v in (value if isinstance(value, list) else [value])]

    section = form.get("section")
    if section == "rename":
        cfg["base_domain"] = str(form.get("base_domain", "")).strip().lower().rstrip(".")
        return cfg, ""
    if section == "magic":
        cfg["magic_dns"] = form.get("magic_dns") == "1"
        return cfg, ""

    cfg["override_local_dns"] = form.get("use_local_dns") != "1"
    cfg["nameservers"] = [s for s in rows("ns") if s]
    cfg["search_domains"] = [d.lower().rstrip(".") for d in rows("search") if d]
    cfg["split"], cfg["extra_records"] = {}, []
    error = ""
    domains, servers = rows("split_domain"), rows("split_ns")
    for domain, server in zip(domains + [""] * (len(servers) - len(domains)),
                              servers + [""] * (len(domains) - len(servers))):
        domain = domain.lower().rstrip(".")
        if not domain and not server:
            continue
        if not server and not error:
            error = _("Split DNS: {domain} needs a nameserver.", domain=domain)
        if not domain and not error:
            error = _("Split DNS: nameserver {value} needs a domain.", value=server)
        servers_of = cfg["split"].setdefault(domain, [])
        if server and server not in servers_of:
            servers_of.append(server)

    names, values = rows("rec_name"), rows("rec_value")
    for name, value in zip(names + [""] * (len(values) - len(names)), values + [""] * (len(names) - len(values))):
        name = name.lower().rstrip(".")
        if not name and not value:
            continue
        record = {"name": name, "type": "", "value": value}
        cfg["extra_records"].append(record)
        if error:
            continue
        if not name or not value:
            error = _("Custom records: each record needs a name and an address.")
        elif not hs.valid_domain(name):
            error = _("Invalid domain: {value}", value=name)
        else:
            try:
                record["type"] = "AAAA" if ipaddress.ip_address(value).version == 6 else "A"
            except ValueError:
                error = _("Custom records: {value} is not an IP address.", value=value)
    return cfg, error


ACL_PORT_RE = re.compile(r"^(\*|\d+(-\d+)?(,\s*\d+(-\d+)?)*)$")


ACL_PROTOS = {"", "tcp", "udp", "icmp"}


GROUP_NAME_RE = re.compile(r"^group:[a-z0-9][a-z0-9-]{0,62}$")


def acl_rule_from_form(form: dict) -> tuple[dict, str]:
    """One ACL rule (Headscale's shape) from the rule dialog's fields, and
    the first input error ("" if none). The dialog's single 'port' field is
    appended to every destination entry ('tag:nas' + ':445' -> 'tag:nas:445'),
    which is what Headscale's dst syntax expects."""
    src = policy.split_list(str(form.get("src", "")))
    dst = policy.split_list(str(form.get("dst", "")))
    port = str(form.get("port", "")).strip() or "*"
    proto = str(form.get("proto", "")).strip().lower()
    if not src:
        return {}, _("Add at least one source.")
    if not dst:
        return {}, _("Add at least one destination.")
    if not ACL_PORT_RE.fullmatch(port):
        return {}, _("Invalid port: use *, a number, a list (80,443) or a range (1000-2000).")
    if proto not in ACL_PROTOS:
        return {}, _("Invalid protocol.")
    bad = [t for t in src + dst if t.startswith("tag:") and not sh.TAG_RE.fullmatch(t)]
    if bad:
        return {}, _("Invalid tag: {value}", value=bad[0])
    rule = {"action": "accept", "src": src, "dst": [f"{d}:{port}" for d in dst]}
    if proto:
        rule["proto"] = proto
    return rule, ""


CHECK_PERIOD_RE = re.compile(r"^\d+[a-z]+(\d+[a-z]+)*$")


def ssh_rule_from_form(form: dict) -> tuple[dict, str]:
    """One SSH rule (Headscale's shape) from the SSH rule dialog's fields,
    and the first input error ("" if none)."""
    src = policy.split_list(str(form.get("src", "")))
    dst = policy.split_list(str(form.get("dst", "")))
    users = policy.split_list(str(form.get("users", "")))
    action = str(form.get("action", "")).strip().lower() or "accept"
    check_period = str(form.get("check_period", "")).strip()
    if not src:
        return {}, _("Add at least one source.")
    if not dst:
        return {}, _("Add at least one destination.")
    if not users:
        return {}, _("Add at least one host user.")
    if action not in ("accept", "check"):
        return {}, _("Invalid access type.")
    if check_period and not CHECK_PERIOD_RE.fullmatch(check_period):
        return {}, _("Invalid re-authentication period: use e.g. 12h, 30m or 1d.")
    bad = [t for t in src + dst if t.startswith("tag:") and not sh.TAG_RE.fullmatch(t)]
    if bad:
        return {}, _("Invalid tag: {value}", value=bad[0])
    rule = {"action": action, "src": src, "dst": dst, "users": users}
    if action == "check" and check_period:
        rule["checkPeriod"] = check_period
    return rule, ""


def acl_group_from_form(form: dict) -> tuple[str, list[str], str]:
    """(name, members, error) for the Groups dialog. name always carries the
    group: prefix even if the field left it out."""
    name = str(form.get("name", "")).strip().lower()
    if name and not name.startswith("group:"):
        name = f"group:{name}"
    members = policy.split_list(str(form.get("members", "")))
    if not GROUP_NAME_RE.fullmatch(name):
        return "", [], _("Invalid group name: lowercase letters, digits and dashes, after group:.")
    if not members:
        return "", [], _("Add at least one member.")
    return name, members, ""


def acl_tag_owner_from_form(form: dict) -> tuple[str, list[str], str]:
    """(tag, owners, error) for the Tag owners dialog."""
    name = str(form.get("name", "")).strip().lower()
    owners = policy.split_list(str(form.get("owners", "")))
    if not sh.TAG_RE.fullmatch(name):
        return "", [], _("Invalid tag: use the tag: prefix, lowercase letters, digits and dashes.")
    if not owners:
        return "", [], _("Add at least one owner.")
    return name, owners, ""


def acl_auto_route_from_form(form: dict) -> tuple[str, list[str], str]:
    """(cidr, approvers, error) for the auto-approved route dialog."""
    cidr = str(form.get("cidr", "")).strip()
    approvers = policy.split_list(str(form.get("approvers", "")))
    try:
        ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return "", [], _("Invalid subnet: use CIDR notation (e.g. 10.0.0.0/24).")
    if not approvers:
        return "", [], _("Add at least one approver.")
    bad = [a for a in approvers if a.startswith("tag:") and not sh.TAG_RE.fullmatch(a)]
    if bad:
        return "", [], _("Invalid tag: {value}", value=bad[0])
    return cidr, approvers, ""


def acl_auto_exit_node_from_form(form: dict) -> tuple[list[str], str]:
    """(approvers, error) for the auto-approved exit node form. An empty list
    is valid: it turns auto-approval off."""
    approvers = policy.split_list(str(form.get("approvers", "")))
    bad = [a for a in approvers if a.startswith("tag:") and not sh.TAG_RE.fullmatch(a)]
    if bad:
        return [], _("Invalid tag: {value}", value=bad[0])
    return approvers, ""


def _key_expiry_days(form: dict) -> int | None:
    """Days from the form: 0 = never; None = invalid."""
    if form.get("never") == "1":
        return 0
    raw = str(form.get("days", "")).strip()
    if not raw.isdigit() or not 1 <= int(raw) <= hs.KEY_EXPIRY_MAX_DAYS:
        return None
    return int(raw)


class PolicyHandlers:
    def derp_view(self, session: dict, flash: str = "", error: str = "", relays: list[dict] | None = None) -> str:
        hostinfos = [d.get("hostinfo") or {} for d in hs.host_details([str(n["id"]) for n in hs.all_nodes()]).values()]
        rows = derp.status(hostinfos, derp.regions())
        return derp_pages.derp_page(session, sh.CTX, rows, derp.embedded_region(),
                                    derp.relays() if relays is None else relays, derp.editable(), flash, error)

    def save_derp(self, session: dict, form: dict):
        relays, error = derp_pages.relays_from_form(form)
        error = error or derp.validate(relays)
        if error:
            return self.send(400, self.derp_view(session, error=error, relays=relays))
        reason = derp.editable()
        if reason:
            return self.send(400, self.derp_view(session, error=reason, relays=relays))
        before = derp.relays()
        ok, error = derp.apply(relays)
        if not ok:
            sh.log.warning("%s tried to change the DERP map: %s", session["username"], error)
            return self.send(400, self.derp_view(session, error=error, relays=relays))
        sh.log.info("%s changed the DERP map (%d relays) and restarted Headscale", session["username"], len(relays))
        audit.request_event(self, session, "derp.save", _("DERP map"),
                            {"from": [r["hostname"] for r in before], "to": [r["hostname"] for r in relays]})
        return self.redirect(f"{BASE}/derp?m=derp-saved")

    def save_key_expiry(self, session: dict, form: dict):
        days = _key_expiry_days(form)

        def again(error: str):
            return self.send(200, settings_pages.general_page(session, sh.CTX, key_expiry=hs.key_expiry_days(), error=error))

        if days is None:
            return again(_("Enter a number of days between 1 and {max}.", max=hs.KEY_EXPIRY_MAX_DAYS))
        before = hs.key_expiry_days()
        ok, error = hs.apply_key_expiry(days)
        if not ok:
            sh.log.warning("%s tried to change the key expiry: %s", session["username"], error)
            return again(error)
        sh.log.info("%s set the device key expiry to %s and restarted Headscale", session["username"],
                 f"{days} days" if days else "never")
        audit.request_event(self, session, "settings.key_expiry", _("Device key expiry"), {"from": before, "to": days})
        return self.redirect(f"{BASE}/settings/general?m=key-expiry-saved")

    # --- policy: Advanced (raw HuJSON) tab ---
    def save_acl(self, session: dict, form: dict):
        policy_text = str(form.get("policy", ""))
        try:
            if form.get("action") == "save":
                before = hs.get_policy().get("policy", "")
                hs.api("PUT", "/policy", {"policy": policy_text})
                audit.request_event(self, session, "acl.save", _("Access control policy"), audit.text_diff(before, policy_text))
                sh.log.info("%s saved the ACL policy", session["username"])
                return self.redirect(f"{BASE}/acl?m=acl-saved&tab=raw")
            hs.api("POST", "/policy/check", {"policy": policy_text})
            result = ("ok", _("The policy is valid."))
        except urllib.error.HTTPError as exc:
            result = ("error", hs.api_error(exc))
        # Send the draft back as is so nothing typed is lost
        self.send(200, admin_pages.acl_page(session, sh.CTX, hs.get_policy(), hs.all_nodes(), hs.all_users(), "",
                                            draft=policy_text, result=result, active="raw"))

    # --- policy: visual editor (Rules / Groups & tags / Auto-approval / SSH) ---
    def acl_edit(self, session: dict, form: dict, kind: str):
        """Shared save path for the visual ACL editor's small dialogs (ACL
        rules, groups, tag owners, auto-approved routes and exit node, SSH
        rules). The policy is re-read straight from Headscale
        (never hs.get_policy(), which turns a failed request into an empty
        policy -- building on that would risk overwriting a real one), the
        change is spliced in with policy.replace_block, and it is validated
        and saved exactly like the raw editor's Save button."""
        tab = ("rules" if kind == "rule" else "groups" if kind in ("group", "tag")
              else "ssh" if kind == "ssh_rule" else "auto")
        try:
            current = hs.api("GET", "/policy")
        except urllib.error.HTTPError as exc:
            sh.log.error("could not read the policy before a visual ACL edit: %s", hs.api_error(exc))
            return self.redirect(f"{BASE}/acl?m=acl-unreachable&tab={tab}")
        text = current.get("policy") or ""
        try:
            parsed = policy.parse(text)
        except policy.PolicyError:
            return self.redirect(f"{BASE}/acl?m=acl-unreadable&tab={tab}")

        is_delete = str(form.get("op", "")) == "delete"
        if kind in ("rule", "ssh_rule"):
            key = "acls" if kind == "rule" else "ssh"
            from_form = acl_rule_from_form if kind == "rule" else ssh_rule_from_form
            renderer = policy.render_acls if kind == "rule" else policy.render_ssh_rules
            rules = list(parsed.get(key) or [])
            idx = str(form.get("index", ""))
            if is_delete:
                if not (idx.isdigit() and int(idx) < len(rules)):
                    return self.redirect(f"{BASE}/acl?m=acl-not-found&tab={tab}")
                del rules[int(idx)]
            else:
                rule, error = from_form(form)
                if error:
                    return self.redirect(f"{BASE}/acl?m=acl-invalid&tab={tab}")
                if idx.isdigit() and int(idx) < len(rules):
                    rules[int(idx)] = rule
                else:
                    rules.append(rule)
            new_text = policy.replace_block(text, key, renderer(rules))
        elif kind in ("group", "tag"):
            key = "groups" if kind == "group" else "tagOwners"
            mapping = dict(parsed.get(key) or {})
            orig = str(form.get("orig_name", "")).strip()
            if is_delete:
                if orig not in mapping:
                    return self.redirect(f"{BASE}/acl?m=acl-not-found&tab={tab}")
                del mapping[orig]
            else:
                name, members, error = (acl_group_from_form(form) if kind == "group" else acl_tag_owner_from_form(form))
                if error:
                    return self.redirect(f"{BASE}/acl?m=acl-invalid&tab={tab}")
                if orig and orig != name:
                    mapping.pop(orig, None)
                mapping[name] = members
            renderer = policy.render_groups if kind == "group" else policy.render_tag_owners
            new_text = policy.replace_block(text, key, renderer(mapping))
        elif kind == "auto_route":
            auto = dict(parsed.get("autoApprovers") or {})
            routes = dict(auto.get("routes") or {})
            orig = str(form.get("orig_name", "")).strip()
            if is_delete:
                if orig not in routes:
                    return self.redirect(f"{BASE}/acl?m=acl-not-found&tab={tab}")
                del routes[orig]
            else:
                cidr, approvers, error = acl_auto_route_from_form(form)
                if error:
                    return self.redirect(f"{BASE}/acl?m=acl-invalid&tab={tab}")
                if orig and orig != cidr:
                    routes.pop(orig, None)
                routes[cidr] = approvers
            auto["routes"] = routes
            new_text = policy.replace_block(text, "autoApprovers", policy.render_auto_approvers(auto))
        else:  # kind == "auto_exit"
            approvers, error = acl_auto_exit_node_from_form(form)
            if error:
                return self.redirect(f"{BASE}/acl?m=acl-invalid&tab={tab}")
            auto = dict(parsed.get("autoApprovers") or {})
            auto["exitNode"] = approvers
            new_text = policy.replace_block(text, "autoApprovers", policy.render_auto_approvers(auto))

        try:
            hs.api("POST", "/policy/check", {"policy": new_text})
            hs.api("PUT", "/policy", {"policy": new_text})
        except urllib.error.HTTPError as exc:
            sh.log.warning("Headscale rejected a visual ACL edit: %s", hs.api_error(exc))
            return self.redirect(f"{BASE}/acl?m=acl-rejected&tab={tab}")
        audit.request_event(self, session, f"acl.{kind}_{'delete' if is_delete else 'save'}",
                            _("Access control policy"), audit.text_diff(text, new_text))
        sh.log.info("%s %s an ACL %s", session["username"], "deleted" if is_delete else "saved", kind)
        return self.redirect(f"{BASE}/acl?m={'acl-deleted' if is_delete else 'acl-saved'}&tab={tab}")

    def acl_test(self, session: dict, form: dict):
        current = hs.get_policy()
        nodes, users = hs.all_nodes(), hs.all_users()
        try:
            parsed = policy.parse(current.get("policy") or "")
        except policy.PolicyError:
            parsed = {}
        src = str(form.get("test_src", "")).strip()
        dst = str(form.get("test_dst", "")).strip()
        port = str(form.get("test_port", "")).strip()
        proto = str(form.get("test_proto", "")).strip()
        verdict = policy.evaluate(parsed, nodes, users, src, dst, port or None, proto or None) if src and dst else None
        self.send(200, admin_pages.acl_page(session, sh.CTX, current, nodes, users, "",
                                            test=(src, dst, port, proto, verdict), active="test"))

    # --- DNS ---
    def save_dns(self, session: dict, form: dict):
        ctx = sh.dns_ctx()
        current = hs.dns_config()
        cfg: dict | None = None

        def again(error: str):
            # Show what the admin typed again so it is not lost
            return self.send(400, dns_pages.dns_page(session, ctx, cfg or current, sh.to_machines(sh.visible_nodes(session)),
                                                 error=error))

        if not ctx.get("dns_editable"):
            return again(ctx.get("dns_reason", _("Editing DNS is not available.")))

        cfg, error = dns_cfg_from_form(form, current)
        if error:
            return again(error)
        if not hs.valid_domain(cfg["base_domain"]):
            return again(_("The tailnet DNS name is not a valid domain."))
        if cfg["base_domain"] == sh.SERVER_HOST or sh.SERVER_HOST.endswith("." + cfg["base_domain"]):
            return again(_("The tailnet DNS name must differ from the server's domain."))
        bad = [ns for ns in cfg["nameservers"] + [s for v in cfg["split"].values() for s in v]
               if not hs.valid_nameserver(ns)]
        if bad:
            return again(_("Invalid nameserver: {value}", value=bad[0]))
        bad = [d for d in cfg["search_domains"] + list(cfg["split"]) if not hs.valid_domain(d)]
        if bad:
            return again(_("Invalid domain: {value}", value=bad[0]))
        if cfg["override_local_dns"] and not cfg["nameservers"]:
            return again(_("Turning off \"Use local DNS settings\" needs at least one global nameserver."))

        ok, error = hs.apply_dns(cfg)
        if not ok:
            sh.log.warning("%s tried to change DNS: %s", session["username"], error)
            return again(error)
        sh.log.info("%s changed DNS (%s) and restarted Headscale", session["username"], form.get("section") or "settings")
        audit.request_event(self, session, "dns.save", "DNS", {"changed": audit.changes({k: current.get(k) for k in cfg}, cfg)})
        return self.redirect(f"{BASE}/dns?m=dns-saved")
