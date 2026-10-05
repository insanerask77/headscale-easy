"""Pages of Headscale Easy: Machines (list and detail), Add device, DNS and
Settings (General, Keys). Admin-only parts are rendered only when the session
is an admin; app.py enforces the same rule on every action."""

from __future__ import annotations

import csv
import io
import ipaddress
import re
from datetime import datetime, timezone

import derp as derp_info
import expiry
import notify
from i18n import LANGUAGES, _, get_lang, ngettext
from qr import qr_figure
import docker_tab
from ui import (BASE, LOGO, badge, bare_page, copy_btn, csrf_input, docs_url, esc, flash_html, icon, initials, layout, live_indicator,
                notice, page_head, parse_time, relative, time_tag, user_label)

OS_NAMES = {"linux": "Linux", "windows": "Windows", "macos": "macOS", "ios": "iOS",
            "android": "Android", "freebsd": "FreeBSD", "openbsd": "OpenBSD", "tvos": "tvOS"}
EXIT_ROUTES = {"0.0.0.0/0", "::/0"}


def version_tuple(v: str) -> tuple:
    return tuple(int(p) for p in v.split(".") if p.isdigit())


class Machine:
    """View model of a node: the API data plus the Hostinfo from the database."""

    def __init__(self, node: dict, details: dict | None, dns: dict, latest: str, regions: dict):
        self.node = node
        hi = (details or {}).get("hostinfo") or {}
        self.hostinfo = hi
        self.endpoints = (details or {}).get("endpoints") or []
        self.id = str(node.get("id"))
        self.name = node.get("givenName") or node.get("name") or ""
        self.hostname = hi.get("Hostname") or node.get("name") or ""
        self.online = bool(node.get("online"))
        self.last_seen_raw = node.get("lastSeen")
        self.last_seen = parse_time(self.last_seen_raw)
        self.created = node.get("createdAt")
        self.owner = node.get("user") or {}
        self.owner_label = self.owner.get("email") or self.owner.get("displayName") or self.owner.get("name") or ""
        self.tags = node.get("tags") or []

        ips = node.get("ipAddresses") or []
        self.ipv4 = next((ip for ip in ips if ":" not in ip), "")
        self.ipv6 = next((ip for ip in ips if ":" in ip), "")
        self.fqdn = f"{self.name}.{dns['base_domain']}" if dns.get("magic_dns") and dns.get("base_domain") else ""

        self.expiry = parse_time(node.get("expiry"))
        self.expiry_disabled = self.expiry is None
        self.expired = self.expiry is not None and self.expiry < datetime.now(timezone.utc)
        self.expiring_soon = expiry.expires_soon(node)
        self.inactive = expiry.is_inactive(node)

        os_raw = (hi.get("OS") or "").lower()
        self.os = OS_NAMES.get(os_raw, hi.get("OS") or "")
        if os_raw == "linux" and hi.get("Distro"):
            self.os_detail = f"{hi['Distro'].capitalize()} {hi.get('DistroVersion', '')}".strip()
        else:
            self.os_detail = hi.get("OSVersion") or ""
        self.version = (hi.get("IPNVersion") or "").split("-")[0]
        self.latest = latest
        self.update_available = bool(self.version and latest and version_tuple(self.version) < version_tuple(latest))

        derp, latency = derp_info.net_info(hi)
        self.derp = regions.get(derp, _("Region {n}", n=derp)) if derp else ""
        # [(region name, ms, is the preferred one)], fastest first
        self.derp_latency = sorted(((regions.get(r, _("Region {n}", n=r)), ms, r == derp)
                                    for r, ms in latency.items()), key=lambda x: x[1])
        self.derp_ms = latency.get(derp) if derp else None

        available = set(node.get("availableRoutes") or [])
        self.approved = set(node.get("approvedRoutes") or [])
        self.exit_node = bool(available & EXIT_ROUTES)
        self.exit_node_approved = bool(self.approved & EXIT_ROUTES)
        self.subnets = sorted(available - EXIT_ROUTES)
        self.subnets_pending = any(r not in self.approved for r in self.subnets)
        self.ephemeral = bool((node.get("preAuthKey") or {}).get("ephemeral"))
        self.register = {"REGISTER_METHOD_OIDC": _("Browser sign-in"),
                         "REGISTER_METHOD_AUTH_KEY": _("Auth key"),
                         "REGISTER_METHOD_CLI": _("Command line")}.get(node.get("registerMethod"), "—")

    def os_line(self) -> str:
        return " ".join(x for x in (self.os, self.os_detail) if x)

    def search_text(self) -> str:
        return " ".join([self.name, self.hostname, self.ipv4, self.ipv6, self.os, self.os_detail,
                         self.version, self.owner_label, self.owner.get("name", ""), *self.tags]).lower()

    def badges(self) -> str:
        out = []
        if self.expired:
            out.append(badge(_("Expired"), "red"))
        elif self.expiring_soon:
            out.append(badge(_("Expires soon"), "orange", expiry.soon_badge_tip(self.node)))
        elif self.expiry_disabled:
            out.append(badge(_("Expiry disabled")))
        if self.subnets:
            out.append(badge(_("Subnets"), "orange", _("This machine has unapproved routes."))
                       if self.subnets_pending else badge(_("Subnets"), "blue"))
        if self.exit_node:
            out.append(badge(_("Exit Node"), "blue") if self.exit_node_approved
                       else badge(_("Exit Node"), "orange", _("This machine is not yet approved as an exit node.")))
        if self.ephemeral:
            out.append(badge(_("Ephemeral")))
        out += [badge(t, "tag") for t in self.tags]
        return "".join(out)


# -----------------------------------------------------------------------------
# Machines
# -----------------------------------------------------------------------------

def machine_menu(m: Machine, session: dict, detail: bool = False) -> str:
    admin = session.get("admin")
    read_only = session.get("role") == "auditor"
    items = [] if detail else [f'<a href="{BASE}/machines/{m.id}">{esc(_("View details"))}</a>']
    if m.ipv4:
        items.append(f'<button type="button" data-copy="{esc(m.ipv4)}">{esc(_("Copy IPv4"))}</button>')
    if not read_only:
        items.append(f'<button type="button" data-open="rename-{m.id}">{esc(_("Edit machine name…"))}</button>')
        if admin:
            items.append(f'<button type="button" data-open="tags-{m.id}">{esc(_("Edit ACL tags…"))}</button>')
            if m.exit_node or m.subnets:
                items.append(f'<a href="{BASE}/machines/{m.id}#routes">{esc(_("Edit route settings…"))}</a>')
        items.append(f'<button type="button" data-open="expire-{m.id}">{esc(_("Expire key…"))}</button>')
        if admin:
            label = _("Enable key expiry") if m.expiry_disabled else _("Disable key expiry")
            items.append(f"""<form method="post" action="{BASE}/machines/{m.id}/expiry">{csrf_input(session)}
                <input type="hidden" name="back" value="{'machines/' + m.id if detail else 'machines'}">
                <input type="hidden" name="disable" value="{'0' if m.expiry_disabled else '1'}">
                <button type="submit">{esc(label)}</button></form>""")
        items.append("<hr>")
        items.append(f'<button type="button" class="danger" data-open="remove-{m.id}">{esc(_("Remove…"))}</button>')
    return f"""
      <details class="dropdown">
        <summary class="icon-btn" aria-label="{esc(_("Actions"))}">{icon("more")}</summary>
        <div class="dropdown-body right">{"".join(items)}</div>
      </details>"""


def dialog(dialog_id: str, title: str, text: str, form_action: str, session: dict, fields: str = "",
           submit: str = "", danger: bool = False, back: str = "") -> str:
    back_input = f'<input type="hidden" name="back" value="{esc(back)}">' if back else ""
    return f"""
    <dialog id="{dialog_id}">
      <form method="post" action="{form_action}">{csrf_input(session)}{back_input}
        <h3>{esc(title)}</h3>
        {f'<p class="muted">{text}</p>' if text else ""}
        {fields}
        <div class="dialog-actions"><button type="button" class="btn" data-close>{esc(_("Cancel"))}</button>
          <button class="btn {"danger-solid" if danger else "primary"}" type="submit">{esc(submit)}</button></div>
      </form>
    </dialog>"""


def machine_dialogs(m: Machine, session: dict, back: str) -> str:
    out = [
        dialog(f"rename-{m.id}", _("Edit machine name"),
               esc(_("The name used in the tailnet and in MagicDNS. Lowercase letters, digits and dashes.")),
               f"{BASE}/machines/{m.id}/rename", session, back=back, submit=_("Save"),
               fields=f'<label class="field">{esc(_("Machine name"))}<input name="name" value="{esc(m.name)}" '
                      f'maxlength="63" required pattern="[a-z0-9]([a-z0-9\\-]*[a-z0-9])?" autocomplete="off" spellcheck="false"></label>'),
        dialog(f"expire-{m.id}", _("Expire the key of {name}?", name=m.name),
               esc(_("The device is disconnected until someone signs in on it again. Useful if it was lost "
                     "or to force a new sign-in.")),
               f"{BASE}/machines/{m.id}/expire", session, back=back, submit=_("Expire key"), danger=True),
        dialog(f"remove-{m.id}", _("Remove {name}?", name=m.name),
               esc(_("It is removed from the tailnet. To use it again it has to be connected again.")),
               f"{BASE}/machines/{m.id}/delete", session, submit=_("Remove machine"), danger=True),
    ]
    if session.get("admin"):
        out.append(dialog(
            f"tags-{m.id}", _("Edit ACL tags for {name}", name=m.name),
            esc(_("Comma separated, with the tag: prefix. Each tag needs an owner in tagOwners of the policy. "
                  "A tagged machine no longer belongs to its user.")),
            f"{BASE}/machines/{m.id}/tags", session, back=back, submit=_("Save"),
            fields=f'<label class="field">{esc(_("Tags"))}<input name="tags" value="{esc(", ".join(m.tags))}" '
                   f'placeholder="tag:server, tag:prod" autocomplete="off" spellcheck="false"></label>'))
    return "".join(out)


def status_html(m: Machine) -> str:
    if m.online:
        return f'<span class="status on"><i></i>{esc(_("Connected"))}</span>'
    return f'<span class="status off"><i></i>{time_tag(m.last_seen_raw, _("Never"), "short")}</span>'


def relay_html(m: Machine) -> str:
    """Preferred DERP region and its latency, for the Machines table."""
    if not m.derp:
        return '<span class="muted">—</span>'
    ms = f' <span class="muted">{esc(_("{ms} ms", ms=round(m.derp_ms)))}</span>' if m.derp_ms is not None else ""
    return f"{esc(m.derp)}{ms}"


def addresses_dropdown(m: Machine) -> str:
    rows = [("IPv4", m.ipv4), ("IPv6", m.ipv6), ("MagicDNS", m.fqdn)]
    items = "".join(f'<div class="addr-row"><span class="muted small">{k}</span><code>{esc(v)}</code>{copy_btn(v)}</div>'
                    for k, v in rows if v)
    return f"""
      <details class="dropdown addr">
        <summary><span>{esc(m.ipv4 or m.ipv6)}</span>{icon("chevron-down", "chev")}</summary>
        <div class="dropdown-body wide">{items}</div>
      </details>"""


def version_html(m: Machine) -> str:
    if not m.version:
        return f'<span class="muted">{esc(m.os) or "—"}</span>'
    tip = (_("Update available: {version}", version=m.latest) if m.update_available
           else _("Up to date"))
    return f"""<div class="version">
      <span class="upd {"needs" if m.update_available else ""}" title="{esc(tip)}">{icon("update")}</span>
      <div><div>{esc(m.version)}</div><div class="muted">{esc(m.os_line())}</div></div>
    </div>"""


def register_dialog(session: dict, users: list[dict]) -> str:
    opts = "".join(f'<option value="{esc(u["name"])}">{esc(user_label(u))}</option>'
                   for u in sorted(users, key=lambda u: user_label(u).lower()))
    return dialog("register", _("Register a machine"),
                  esc(_("For devices that ran 'tailscale up' without a key: paste the Auth ID "
                        "(hskey-authreq-…) or the URL it printed, and choose the owner.")),
                  f"{BASE}/machines/register", session, submit=_("Register"),
                  fields=f'<label class="field">{esc(_("Auth ID or URL"))}<input name="auth_id" placeholder="hskey-authreq-…" '
                         f'required autocomplete="off" spellcheck="false"></label>'
                         f'<label class="field">{esc(_("Owner"))}<select name="user" required>{opts}</select></label>')


def register_page(session: dict, ctx: dict, auth_id: str, users: list[dict] | None, owner: dict | None,
                  error: str = "") -> str:
    """Approve a device that ran 'tailscale up' without a key: Caddy sends the
    /register/<id> link Headscale prints here (AUTH_PROVIDER=none). Admins
    choose the owner (users); everyone else adds it to their own user (owner)."""
    head = page_head(_("Add a device"), esc(_("A device is waiting to join the tailnet. Only approve it if you "
                                               "just ran 'tailscale up' or signed in on that device yourself.")))
    head += notice("error", error) if error else ""
    if users is not None:
        opts = "".join(f'<option value="{esc(u["name"])}"{" selected" if owner and u["name"] == owner["name"] else ""}>'
                       f'{esc(user_label(u))}</option>' for u in sorted(users, key=lambda u: user_label(u).lower()))
        who = f'<label class="field">{esc(_("Owner"))}<select name="user" required>{opts}</select></label>'
    elif owner:
        who = f'<p>{esc(_("It will be added to your account, {user}.", user=user_label(owner)))}</p>'
    else:
        body = head + f"""
    <section class="card"><p>{esc(_("Your account has no Headscale user yet, so it cannot own devices. Ask an admin."))}</p>
      <p><a class="btn" href="{BASE}/machines">{esc(_("Back to Machines"))}</a></p></section>"""
        return layout(_("Add a device"), "machines", body, session, ctx)
    body = head + f"""
    <section class="card">
      <form method="post" action="{BASE}/register/{esc(auth_id)}">{csrf_input(session)}
        <dl class="kvs"><dt>{esc(_("Request"))}</dt><dd><code>{esc(auth_id)}</code></dd></dl>
        {who}
        <div class="dialog-actions"><a class="btn" href="{BASE}/machines">{esc(_("Cancel"))}</a>
          <button class="btn primary" type="submit">{esc(_("Approve device"))}</button></div>
      </form>
    </section>"""
    return layout(_("Add a device"), "machines", body, session, ctx)


def machines_page(session: dict, ctx: dict, machines: list[Machine], has_user: bool, flash: str,
                  users: list[dict] | None = None, error: str = "") -> str:
    admin = session.get("admin")
    add_items = [
        f'<a href="{BASE}/add">{esc(_("Client device"))}<span>{esc(_("Laptop, phone or server"))}</span></a>',
        f'<a href="{BASE}/settings/keys#new">{esc(_("Generate auth key"))}<span>{esc(_("For headless devices"))}</span></a>',
    ]
    if admin:
        add_items.append(f'<button type="button" data-open="register">{esc(_("Register with Auth ID"))}'
                         f'<span>{esc(_("Approve a pending sign-in"))}</span></button>')
    add = f"""
      <details class="dropdown">
        <summary class="btn primary">{esc(_("Add device"))}{icon("chevron-down", "chev")}</summary>
        <div class="dropdown-body right menu-rich">{"".join(add_items)}</div>
      </details>"""
    head = page_head(_("Machines"),
                     esc(_("Manage the devices connected to your tailnet.") if admin
                         else _("Manage your devices. Only you can see them, and they can only reach each other.")),
                     add, (_("See how to manage devices"), docs_url("operations/#managing-machines")))
    head += register_dialog(session, users or []) if admin else ""
    head += notice("error", error) if error else ""
    head += flash_html(flash) or expiry.flash_html(flash) or bulk_flash_html(flash)

    if not machines:
        text = (_("No machines are connected to the tailnet yet.") if admin else
                _("Connect your first one: it shows up here as soon as you sign in from it.") if not has_user else
                _("You have no machines in the tailnet right now."))
        return layout(_("Machines"), "machines", head + f"""
    <section class="empty">
      <div class="big-logo">{LOGO}</div>
      <h3>{esc(_("No machines yet"))}</h3>
      <p class="muted">{esc(text)}</p>
      <a class="btn primary" href="{BASE}/add">{esc(_("Add device"))}</a>
    </section>""", session, ctx)

    owner_filter = ""
    if admin and users:
        opts = "".join(f'<option value="{esc(u["id"])}">{esc(user_label(u))}</option>'
                       for u in sorted(users, key=lambda u: user_label(u).lower()))
        owner_filter = (f'<label class="field">{esc(_("Owner"))}<select data-f="owner">'
                        f'<option value="">{esc(_("All users"))}</option>{opts}</select></label>')
    filters = f"""
      <details class="dropdown filters">
        <summary class="btn">{icon("filter")}{esc(_("Filters"))}<span class="fcount" hidden></span>{icon("chevron-down", "chev")}</summary>
        <div class="dropdown-body filter-body">
          <label class="field">{esc(_("Status"))}<select data-f="status">
            <option value="">{esc(_("All"))}</option>
            <option value="on">{esc(_("Connected"))}</option>
            <option value="off">{esc(_("Disconnected"))}</option></select></label>
          {owner_filter}
          <label class="check"><input type="checkbox" data-f="update"><span>{esc(_("Needs update"))}</span></label>
          <label class="check"><input type="checkbox" data-f="routes"><span>{esc(_("Has routes (subnets or exit node)"))}</span></label>
          <label class="check"><input type="checkbox" data-f="expired"><span>{esc(_("Key expired"))}</span></label>
          {expiry.filter_options()}
          <button type="button" class="btn small" data-f-clear>{esc(_("Clear filters"))}</button>
        </div>
      </details>"""

    bulk_col = f'<th class="bulk-col" hidden><input type="checkbox" data-bulk-all aria-label="{esc(_("Select all"))}"></th>' if admin else ""
    rows, dialogs = [], []
    for m in machines:
        bulk_cell = (f'<td class="bulk-col" hidden><input type="checkbox" name="node-{esc(m.id)}" value="1" '
                    f'data-bulk-item aria-label="{esc(_("Select {name}", name=m.name))}"></td>') if admin else ""
        rows.append(f"""
        <tr data-href="{BASE}/machines/{m.id}" data-search="{esc(m.search_text())}" data-status="{'on' if m.online else 'off'}"
            data-owner="{esc(m.owner.get('id'))}" data-update="{int(m.update_available)}"
            data-routes="{int(m.exit_node or bool(m.subnets))}" data-expired="{int(m.expired)}"
            data-expiring="{int(m.expiring_soon)}" data-inactive="{int(m.inactive)}">
          {bulk_cell}
          <td><a class="name" href="{BASE}/machines/{m.id}">{esc(m.name)}</a>
            <div class="owner">{esc(m.owner_label)}</div>
            <div class="badges">{m.badges()}</div></td>
          <td>{addresses_dropdown(m)}</td>
          <td class="hide-sm">{version_html(m)}</td>
          <td class="hide-sm">{relay_html(m)}</td>
          <td>{status_html(m)}</td>
          <td class="actions">{machine_menu(m, session)}</td>
        </tr>""")
        dialogs.append(machine_dialogs(m, session, "machines"))

    table = f"""
    <div class="table-wrap">
      <table class="machines">
        <thead><tr>{bulk_col}<th>{esc(_("Machine"))}</th>
          <th><span title="{esc(_("The machine's Tailscale IP addresses and MagicDNS name"))}">{esc(_("Addresses"))} {icon("info", "i-xs")}</span></th>
          <th class="hide-sm">{esc(_("Version"))}</th>
          <th class="hide-sm"><span title="{esc(_("The DERP relay the machine prefers and its latency"))}">{esc(_("Relay"))} {icon("info", "i-xs")}</span></th>
          <th>{esc(_("Last seen"))}</th><th></th></tr></thead>
        <tbody data-live="rows" data-stream="{BASE}/events">{"".join(rows)}
        </tbody>
      </table>
    </div>
    <p class="no-results muted" hidden>{esc(_("No machines match the current filters."))}</p>"""
    if admin:
        table = f"""<form method="post" id="bulk-form">{csrf_input(session)}{table}
    <div class="bulk-bar" data-bulk-bar hidden>
      <span data-bulk-count data-one="{esc(_("1 machine selected"))}" data-many="{esc(_("{n} machines selected"))}"></span>
      <button type="submit" formaction="{BASE}/machines/bulk/expire" class="btn small">{esc(_("Expire keys"))}</button>
      <button type="button" class="btn small" data-open="bulk-tag">{esc(_("Add tag…"))}</button>
      <button type="button" class="btn small danger-solid" data-open="bulk-remove">{esc(_("Remove…"))}</button>
      <button type="button" class="btn small" data-bulk-clear>{esc(_("Clear selection"))}</button>
    </div>
    </form>
    {dialog("bulk-remove", _("Remove selected machines?"),
            esc(_("They are removed from the tailnet. To use one again it has to be connected again.")),
            f"{BASE}/machines/bulk/remove", session, submit=_("Remove machines"), danger=True,
            fields='<div data-bulk-mirror="bulk-remove"></div>')}
    {dialog("bulk-tag", _("Add tag to selected machines"),
            esc(_("Comma separated, with the tag: prefix. Added to each machine's existing tags. Each tag needs "
                  "an owner in tagOwners of the policy.")),
            f"{BASE}/machines/bulk/tags", session, submit=_("Add tag"),
            fields='<div data-bulk-mirror="bulk-tag"></div>'
                   f'<label class="field">{esc(_("Tags"))}<input name="tags" placeholder="tag:server, tag:prod" '
                   f'required autocomplete="off" spellcheck="false"></label>')}"""

    body = head + f"""
    <div data-live="expiry-notice">{expiry.notice_html([m.node for m in machines], admin)}</div>
    <div class="toolbar">
      <label class="search">{icon("search")}<input type="search" placeholder="{esc(_("Search by name, owner, tag, version…"))}" data-filter aria-label="{esc(_("Search machines"))}"></label>
      {filters}
      <a class="link hide-sm" href="{docs_url("operations/#managing-machines")}" target="_blank" rel="noopener">{esc(_("Learn more"))}</a>
      <span class="spacer"></span>
      <a class="icon-btn boxed" href="{BASE}/machines.csv" title="{esc(_("Export to CSV"))}" aria-label="{esc(_("Export to CSV"))}">{icon("download")}</a>
    </div>
    <span class="pill" data-count data-one="{esc(_("1 machine"))}" data-many="{esc(_("{n} machines"))}">{esc(ngettext("{n} machine", "{n} machines", len(machines)))}</span> {live_indicator()}
    {table}
    <div data-live="dialogs">{"".join(dialogs)}</div>
    {f'<div data-live="inactive">{expiry.remove_inactive_dialog(machines, session)}</div>' if admin else ""}"""
    return layout(_("Machines"), "machines", body, session, ctx)


def bulk_flash_html(code: str) -> str:
    m = re.fullmatch(r"bulk-(expired|removed|tagged)-(\d+)", code or "")
    if not m:
        return ""
    n = int(m.group(2))
    texts = {
        "expired": ngettext("{n} machine key expired.", "{n} machine keys expired.", n),
        "removed": ngettext("{n} machine removed.", "{n} machines removed.", n),
        "tagged": ngettext("Tag added to {n} machine.", "Tag added to {n} machines.", n),
    }
    return notice("ok", texts[m.group(1)])


def machines_csv(machines: list[Machine]) -> str:
    """Export like Tailscale's "Download": one row per machine."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["name", "owner", "ipv4", "ipv6", "magicdns", "os", "version", "online", "last_seen",
                "expiry", "tags", "exit_node", "subnets"])
    for m in machines:
        w.writerow([m.name, m.owner_label, m.ipv4, m.ipv6, m.fqdn, m.os_line(), m.version,
                    "yes" if m.online else "no", m.last_seen_raw or "", m.node.get("expiry") or "",
                    " ".join(m.tags), "yes" if m.exit_node else "no", " ".join(m.subnets)])
    return out.getvalue()


def kv(label: str, value: str, copy: str | None = None) -> str:
    return f'<div class="kv"><dt>{esc(label)}</dt><dd>{value}{copy_btn(copy) if copy else ""}</dd></div>'


def _endpoint_key(ep: str):
    host = ep.rsplit(":", 1)[0].strip("[]")
    try:
        ip = ipaddress.ip_address(host)
        return (0 if ip.is_global else 1, ip.version, ep)
    except ValueError:
        return (2, 0, ep)


def routes_section(m: Machine, session: dict) -> str:
    rows = []
    if m.exit_node:
        rows.append((_("Exit node"), "0.0.0.0/0, ::/0", m.exit_node_approved, "exit"))
    rows += [(_("Subnet"), r, r in m.approved, r) for r in m.subnets]
    if not rows:
        return f"""<p class="muted">{esc(_("This machine does not advertise subnets or act as an exit node. To do it:"))}</p>
          <div class="code"><code>tailscale set --advertise-routes=10.0.0.0/24</code>{copy_btn("tailscale set --advertise-routes=10.0.0.0/24")}</div>
          <div class="code"><code>tailscale set --advertise-exit-node</code>{copy_btn("tailscale set --advertise-exit-node")}</div>"""
    if session.get("admin"):
        checks = "".join(
            f'<label class="check route"><input type="checkbox" name="route" value="{esc(v)}" {"checked" if ok else ""}>'
            f'<span><b>{esc(kind)}</b> <code>{esc(r)}</code></span></label>'
            for kind, r, ok, v in rows)
        return f"""
        <form method="post" action="{BASE}/machines/{m.id}/routes" class="stack">{csrf_input(session)}
          <input type="hidden" name="back" value="machines/{m.id}">
          <p class="muted small">{esc(_("Tick the routes this machine may offer to the tailnet."))}</p>
          {checks}
          <div><button class="btn primary small" type="submit">{esc(_("Save routes"))}</button></div>
        </form>"""
    table = "".join(
        f'<tr><td>{esc(kind)}</td><td><code>{esc(r)}</code></td>'
        f'<td>{badge(_("Approved"), "blue") if ok else badge(_("Awaiting approval"), "orange")}</td></tr>'
        for kind, r, ok, _v in rows)
    return f"""<table class="simple"><thead><tr><th>{esc(_("Type"))}</th><th>{esc(_("Route"))}</th><th>{esc(_("Status"))}</th></tr></thead>
      <tbody>{table}</tbody></table>
      <p class="muted small">{esc(_("An admin approves advertised routes."))}</p>"""


def machine_page(session: dict, ctx: dict, m: Machine, flash: str, error: str = "") -> str:
    hi = m.hostinfo
    if m.expiry_disabled:
        expiry = esc(_("Disabled"))
    elif m.expired:
        expiry = f'{esc(_("Expired"))} · {time_tag(m.node.get("expiry"))}'
    else:
        expiry = f'{esc(_("Expires {when}", when=relative(m.expiry, future=True)))} · {time_tag(m.node.get("expiry"))}'
    version = esc(m.version) or "—"
    if m.update_available:
        version += " " + badge(_("Update available: {version}", version=m.latest), "orange")
    node_key = m.node.get("nodeKey") or ""
    last_seen = (esc(_("Connected")) if m.online else
                 f"{esc(relative(m.last_seen))} · {time_tag(m.last_seen_raw)}")

    details = "".join([
        kv(_("Owner"), esc(m.owner_label)),
        kv(_("Machine name"), f"<code>{esc(m.name)}</code>", m.name),
        kv(_("OS hostname"), esc(m.hostname) or "—"),
        kv(_("OS"), esc(m.os_line()) or "—"),
        kv(_("Tailscale version"), version),
        kv(_("Architecture"), esc(hi.get("GoArch") or hi.get("Machine") or "—")),
        kv(_("Registered with"), esc(m.register)),
        kv(_("ID"), f"<code>{esc(m.id)}</code>"),
        kv(_("Node key"), f'<code class="trunc">{esc(node_key)}</code>', node_key) if node_key else "",
        kv(_("Created"), time_tag(m.created)),
        kv(_("Last seen"), last_seen),
        kv(_("Key expiry"), expiry),
    ])
    addresses = "".join([
        kv(_("Tailscale IPv4"), f"<code>{esc(m.ipv4)}</code>", m.ipv4) if m.ipv4 else "",
        kv(_("Tailscale IPv6"), f"<code>{esc(m.ipv6)}</code>", m.ipv6) if m.ipv6 else "",
        kv(_("Short domain"), f"<code>{esc(m.name)}</code>", m.name),
        kv(_("Full domain"), f"<code>{esc(m.fqdn)}</code>", m.fqdn) if m.fqdn else "",
    ])
    endpoints = "".join(f"<li><code>{esc(e)}</code></li>" for e in sorted(m.endpoints, key=_endpoint_key))
    latency = "".join(
        f"<li>{esc(name)}: {esc(_('{ms} ms', ms=round(ms)))}{' ' + badge(_('In use'), 'blue') if used else ''}</li>"
        for name, ms, used in m.derp_latency)
    connection = "".join([
        kv(_("Preferred DERP relay"), esc(m.derp) + (f' <span class="muted">{esc(_("{ms} ms", ms=round(m.derp_ms)))}</span>'
                                                      if m.derp_ms is not None else "") if m.derp else "—"),
        kv(_("DERP latency"), f'<ul class="plain">{latency}</ul>') if latency else "",
        kv(_("Endpoints"), f'<ul class="plain">{endpoints}</ul>' if endpoints else "—"),
    ])

    body = f"""
    <nav class="crumbs"><a href="{BASE}/machines">{esc(_("Machines"))}</a><span>/</span>{esc(m.name)}</nav>
    {notice("error", error) if error else ""}{flash_html(flash)}
    <div class="page-head">
      <div>
        <h1 data-live="title">{esc(m.name)}</h1>
        <div class="meta" data-live="meta" data-stream="{BASE}/events">{status_html(m)}<span class="muted">{esc(m.owner_label)}</span>{m.badges()}</div>
      </div>
      <div class="head-actions">
        <button class="btn" type="button" data-open="rename-{m.id}">{esc(_("Edit machine name"))}</button>
        {machine_menu(m, session, detail=True)}
      </div>
    </div>
    <div class="grid-2">
      <section class="card"><h2>{esc(_("Machine details"))}</h2><dl class="kvs" data-live="details">{details}</dl></section>
      <div>
        <section class="card"><h2>{esc(_("Addresses"))}</h2><dl class="kvs" data-live="addresses">{addresses}</dl></section>
        <section class="card" id="routes"><h2>{esc(_("Routes"))}</h2>{routes_section(m, session)}</section>
        <section class="card"><h2>{esc(_("Connection"))}</h2><dl class="kvs" data-live="connection">{connection}</dl>
          <p class="muted small">{esc(_("Endpoints are the addresses the machine announces for direct connections. Without a direct connection, traffic goes through the DERP relay."))}</p></section>
      </div>
    </div>
    {machine_dialogs(m, session, f"machines/{m.id}")}"""
    return layout(m.name, "machines", body, session, ctx)


# -----------------------------------------------------------------------------
# Add device
# -----------------------------------------------------------------------------

def add_page(session: dict, ctx: dict, docker: dict | None = None) -> str:
    """docker: what the Docker tab shows after its form was sent (values, key, error, users)."""
    url = ctx["public_url"]
    login = f"tailscale up --login-server={url}"

    def code(cmd: str) -> str:
        return f'<div class="code"><code>{esc(cmd)}</code>{copy_btn(cmd)}</div>'

    server_qr = qr_figure(url, _("server URL {url}", url=url), _(
        "Scan it with the phone's camera to get the URL on the phone, then copy it into the app. "
        "The Tailscale app cannot read QR codes itself."))
    panels = {
        "linux": ("Linux", f"""
          <ol class="steps">
            <li>{esc(_("Install Tailscale:"))}{code("curl -fsSL https://tailscale.com/install.sh | sh")}</li>
            <li>{esc(_("Connect it to this tailnet. It prints a link to sign in with your account:"))}{code("sudo " + login)}</li>
          </ol>"""),
        "windows": ("Windows", f"""
          <ol class="steps">
            <li>{_("Download and install Tailscale from {link}.", link='<a class="link" href="https://tailscale.com/download/windows" target="_blank" rel="noopener">tailscale.com/download/windows</a>')}</li>
            <li>{esc(_("Open PowerShell and run (the browser opens to sign in):"))}{code(login)}</li>
          </ol>
          <p class="muted small">{esc(_("Headscale's guide for Windows:"))} <a class="link" href="{esc(url)}/windows" target="_blank">{esc(url)}/windows</a></p>"""),
        "macos": ("macOS", f"""
          <ol class="steps">
            <li>{_("Install Tailscale from {link}.", link='<a class="link" href="https://tailscale.com/download/mac" target="_blank" rel="noopener">tailscale.com/download/mac</a>')}</li>
            <li>{esc(_("In Terminal (the browser opens to sign in):"))}{code("/Applications/Tailscale.app/Contents/MacOS/Tailscale up --login-server=" + url)}</li>
          </ol>
          <p class="muted small">{esc(_("Headscale's guide for Apple devices:"))} <a class="link" href="{esc(url)}/apple" target="_blank">{esc(url)}/apple</a></p>"""),
        "ios": ("iOS", f"""
          <div class="qr-row">
            <ol class="steps">
              <li>{esc(_("Install Tailscale from the App Store."))}</li>
              <li>{esc(_("In the Tailscale app, tap the profile icon in the top-right corner, then Log in (or your account, if the app is already signed in to another tailnet)."))}</li>
              <li>{esc(_("Tap the ⋯ menu in the top-right corner, choose Use a custom coordination server and enter this URL:"))}{code(url)}</li>
              <li>{esc(_("Tap Log in and sign in with your account."))}</li>
            </ol>
            {server_qr}
          </div>
          <p class="muted small">{esc(_("Headscale's guide for Apple devices:"))} <a class="link" href="{esc(url)}/apple" target="_blank">{esc(url)}/apple</a></p>"""),
        "android": ("Android", f"""
          <div class="qr-row">
            <ol class="steps">
              <li>{esc(_("Install Tailscale from Google Play."))}</li>
              <li>{esc(_("In the Tailscale app, tap the profile icon in the top-right corner, then Log in (or your account, if the app is already signed in to another tailnet)."))}</li>
              <li>{esc(_("Tap the ⋮ menu in the top-right corner, choose Use an alternate server and enter this URL:"))}{code(url)}</li>
              <li>{esc(_("Tap Log in and sign in with your account."))}</li>
            </ol>
            {server_qr}
          </div>
          <p class="muted small">{_("To connect without signing in, set the server first and then choose Use an auth key in the same ⋮ menu, with a key from {link}.", link=f'<a class="link" href="{BASE}/settings/keys">' + esc(_("Settings → Keys")) + "</a>")}</p>"""),
    }
    docker = docker or {}
    panels["docker"] = ("Docker", docker_tab.panel(session, url, docker.get("values"), docker.get("key", ""),
                                                   docker.get("error", ""), docker.get("users")))
    first = "docker" if docker else "linux"
    tabs = "".join(f'<button type="button" role="tab" data-tab="{k}" class="{"active" if k == first else ""}">{label}</button>'
                   for k, (label, _c) in panels.items())
    bodies = "".join(f'<div class="tab-panel" data-panel="{k}" {"" if k == first else "hidden"}>{content}</div>'
                     for k, (_l, content) in panels.items())
    body = page_head(_("Add device"), esc(_("Install Tailscale and point it at this server instead of Tailscale's."))) + f"""
    <section class="card"><div class="ostabs" role="tablist">{tabs}</div>{bodies}</section>
    <section class="card">
      <h2>{esc(_("Servers and headless devices"))}</h2>
      <p class="muted">{_("Generate an auth key in {link} and use it like this:", link=f'<a class="link" href="{BASE}/settings/keys">' + esc(_("Settings → Keys")) + "</a>")}</p>
      {code(login + " --authkey=<auth-key>")}
    </section>"""
    return layout(_("Add device"), "machines", body, session, ctx)


# -----------------------------------------------------------------------------
# DNS
# -----------------------------------------------------------------------------

# Laid out like the Tailscale console: Tailnet DNS name, MagicDNS, Nameservers
# (the implicit MagicDNS entry, split DNS, global), Search domains and Custom
# records. Admins edit lists as rows. Without JavaScript every list shows one
# blank row to add an entry (repeated "name[]" fields, see Handler.form); with
# it, the blank rows give way to "Add" buttons and each row gets a remove
# button. Renaming the tailnet and turning MagicDNS off are separate actions
# behind a confirmation, since both change how every machine is named.

MAGIC_DNS_IP = "100.100.100.100"
# Suggestions for the nameserver fields (a datalist: any other value is fine)
DNS_PRESETS = [("1.1.1.1", "Cloudflare"), ("1.0.0.1", "Cloudflare"), ("8.8.8.8", "Google"), ("8.8.4.4", "Google"),
               ("9.9.9.9", "Quad9"), ("149.112.112.112", "Quad9"), ("2606:4700:4700::1111", "Cloudflare"),
               ("2001:4860:4860::8888", "Google")]


def _dns_input(name: str, value: str, label: str, placeholder: str, ns: bool = False) -> str:
    presets = ' list="dns-presets"' if ns else ""
    return (f'<input name="{name}" value="{esc(value)}" aria-label="{esc(label)}" placeholder="{esc(placeholder)}" '
            f'spellcheck="false" autocomplete="off"{presets}>')


def _dns_row(inputs: str, blank: bool = False) -> str:
    return (f'<li class="dns-row"{" data-dns-blank" if blank else ""}>{inputs}'
            f'<button type="button" class="icon-btn" data-dns-remove hidden aria-label="{esc(_("Remove"))}" '
            f'title="{esc(_("Remove"))}">{icon("x")}</button></li>')


def _dns_rows(list_id: str, rows: list[str], blank: str, add_label: str, empty: str) -> str:
    """Editable list: the rows, a blank row (no JS), a template and an Add button (JS)."""
    return f"""<ul class="plain dns-rows" data-dns-list="{list_id}">{"".join(rows)}{_dns_row(blank, blank=True)}</ul>
      <p class="muted small dns-none">{esc(empty)}</p>
      <template data-dns-template="{list_id}">{_dns_row(blank)}</template>
      <button type="button" class="btn small" data-dns-add="{list_id}" hidden>{icon("plus")} {esc(add_label)}</button>"""


def _dns_list(values: list[str], empty: str) -> str:
    items = "".join(f"<li><code>{esc(v)}</code></li>" for v in values)
    return f'<ul class="plain">{items}</ul>' if items else f'<p class="muted">{esc(empty)}</p>'


def _dns_fixed(value: str, tip: str) -> str:
    """A read-only entry that Headscale adds by itself (MagicDNS)."""
    return (f'<ul class="plain"><li class="dns-fixed"><code>{esc(value)}</code>'
            f'{badge("MagicDNS", "blue", tip)}</li></ul>')


def dns_nameservers(dns: dict, edit: bool) -> str:
    base, magic = dns.get("base_domain") or "", dns.get("magic_dns")
    split, glob = dns.get("split") or {}, dns.get("nameservers") or []
    use_local = not dns.get("override_local_dns")
    implicit = (f'<h3 class="dns-group"><code>{esc(base)}</code></h3>'
                + _dns_fixed(MAGIC_DNS_IP, _("Added by MagicDNS: names under the tailnet domain resolve to machines."))
                if magic and base else "")
    none_global = _("None: each device uses its own nameservers.")
    if edit:
        def split_inputs(domain, server):
            return (_dns_input("split_domain[]", domain, _("Domain"), "corp.lan")
                    + _dns_input("split_ns[]", server, _("Nameserver"), "10.0.0.53", ns=True))
        split_html = _dns_rows("split", [_dns_row(split_inputs(d, s)) for d, servers in split.items()
                                         for s in servers or [""]],
                               split_inputs("", ""), _("Add split DNS nameserver"), _("None."))
        global_html = _dns_rows("global", [_dns_row(_dns_input("ns[]", s, _("Nameserver"), "1.1.1.1", ns=True))
                                           for s in glob],
                                _dns_input("ns[]", "", _("Nameserver"), "1.1.1.1", ns=True),
                                _("Add nameserver"), none_global)
        presets = "".join(f'<option value="{esc(ip)}">{esc(name)}</option>' for ip, name in DNS_PRESETS)
        local = f"""<datalist id="dns-presets">{presets}</datalist>
      <label class="check dns-local"><input type="checkbox" name="use_local_dns" value="1" {"checked" if use_local else ""}>
        <span><b>{esc(_("Use local DNS settings"))}</b><span class="muted">{esc(_("On: devices keep their own nameservers and use the global ones only as a fallback. Off (override local DNS): every device uses the global nameservers, so at least one is needed."))}</span></span></label>
      <p class="muted small">{esc(_("A nameserver is an IP address or a DoH resolver (https://…)."))}</p>"""
    else:
        split_html = (f'<table class="simple"><thead><tr><th>{esc(_("Domain"))}</th><th>{esc(_("Nameservers"))}</th></tr></thead><tbody>'
                      + "".join(f"<tr><td><code>{esc(d)}</code></td><td>{esc(', '.join(v))}</td></tr>" for d, v in split.items())
                      + "</tbody></table>") if split else f'<p class="muted">{esc(_("None."))}</p>'
        global_html = _dns_list(glob, none_global)
        local = (f'<p class="muted small">{esc(_("Use local DNS settings"))}: '
                 f'<b>{esc(_("On") if use_local else _("Off"))}</b></p>')
    return f"""<section class="card dns-ns">
      <h2>{esc(_("Nameservers"))}</h2>
      <p class="muted">{esc(_("The nameservers devices use while connected to the tailnet."))}</p>
      {implicit}
      <h3 class="dns-group">{esc(_("Split DNS"))}</h3>
      <p class="muted small">{esc(_("Restrict a nameserver to a domain: only queries for that domain (and its subdomains) go to it."))}</p>
      {split_html}
      <h3 class="dns-group">{esc(_("Global nameservers"))}</h3>
      <p class="muted small">{esc(_("Used for every other query."))}</p>
      {global_html}
      {local}
    </section>"""


def dns_search(dns: dict, edit: bool) -> str:
    base, magic = dns.get("base_domain") or "", dns.get("magic_dns")
    domains = dns.get("search_domains") or []
    fixed = _dns_fixed(base, _("Added by MagicDNS")) if magic and base else ""
    if edit:
        body = _dns_rows("search", [_dns_row(_dns_input("search[]", d, _("Search domain"), "corp.lan")) for d in domains],
                         _dns_input("search[]", "", _("Search domain"), "corp.lan"), _("Add search domain"),
                         "" if fixed else _("None."))
    else:
        body = _dns_list(domains, _("None.")) if domains or not fixed else ""
    magic_note = " " + _("With MagicDNS enabled, your tailnet domain is always the first search domain.") if fixed else ""
    return f"""<section class="card">
      <h2>{esc(_("Search domains"))}</h2>
      <p class="muted">{esc(_("Devices try these domains when a name is not fully qualified (e.g. nas → nas.corp.lan).") + magic_note)}</p>
      {fixed}{body}
    </section>"""


def dns_records(dns: dict, edit: bool) -> str:
    records = dns.get("extra_records") or []
    if edit:
        def inputs(r):
            return (_dns_input("rec_name[]", r.get("name", ""), _("Name"), "nas.example.com")
                    + _dns_input("rec_value[]", r.get("value", ""), _("Address"), "100.64.0.5"))
        body = _dns_rows("records", [_dns_row(inputs(r)) for r in records], inputs({}), _("Add record"), _("None."))
    elif records:
        body = (f'<table class="simple"><thead><tr><th>{esc(_("Name"))}</th><th>{esc(_("Type"))}</th><th>{esc(_("Address"))}</th></tr></thead><tbody>'
                + "".join(f"<tr><td><code>{esc(r.get('name'))}</code></td><td>{esc(r.get('type'))}</td><td><code>{esc(r.get('value'))}</code></td></tr>"
                          for r in records) + "</tbody></table>")
    else:
        body = f'<p class="muted">{esc(_("None."))}</p>'
    return f"""<section class="card">
      <h2>{esc(_("Custom records"))}</h2>
      <p class="muted">{esc(_("Names that resolve on every device of the tailnet (A or AAAA, from the address)."))}</p>
      {body}
    </section>"""


def dns_dialogs(session: dict, ctx: dict, dns: dict) -> str:
    base = dns.get("base_domain") or "…"
    out = dialog(
        "dns-rename", _("Rename tailnet"),
        esc(_("Every machine's full name changes from <machine>.{old} to <machine>.<new name>. Anything that uses "
              "the old names (bookmarks, SSH configs, scripts) must be updated. Headscale restarts to apply it.",
              old=base)),
        f"{BASE}/dns", session, submit=_("Rename tailnet"),
        fields=f"""<input type="hidden" name="section" value="rename">
        <label class="field">{esc(_("Tailnet DNS name"))}
          <input name="base_domain" value="{esc(dns.get("base_domain"))}" required spellcheck="false" autocomplete="off">
          <span class="muted small">{esc(_("Must differ from the server's domain ({host}).", host=ctx.get("server_host", "")))}</span></label>""")
    if dns.get("magic_dns"):
        out += dialog(
            "dns-magic-off", _("Disable MagicDNS?"),
            esc(_("Machine names (my-laptop, my-laptop.{domain}) stop resolving on every device: only IP addresses "
                  "keep working. Headscale restarts to apply it.", domain=base)),
            f"{BASE}/dns", session, submit=_("Disable MagicDNS"), danger=True,
            fields='<input type="hidden" name="section" value="magic"><input type="hidden" name="magic_dns" value="0">')
    return out


def dns_page(session: dict, ctx: dict, dns: dict, machines: list[Machine], error: str = "", flash: str = "") -> str:
    admin = session.get("admin")
    edit = bool(admin and ctx.get("dns_editable"))
    base = dns.get("base_domain") or ""
    magic = dns.get("magic_dns")
    names = "".join(f'<tr><td>{esc(m.name)}</td><td><code>{esc(m.fqdn)}</code></td><td class="actions">{copy_btn(m.fqdn)}</td></tr>'
                    for m in machines if m.fqdn)
    sub = _("DNS settings are shared by the whole tailnet.") + ("" if admin else " " + _("Only an admin can change them."))
    reason = notice("error", ctx.get("dns_reason") or _("Editing DNS is not available.")) if admin and not edit else ""
    rename_btn = magic_btn = ""
    if edit:
        rename_btn = f'<button type="button" class="btn small" data-open="dns-rename">{esc(_("Rename tailnet…"))}</button>'
        magic_btn = (f'<button type="button" class="btn small" data-open="dns-magic-off">{esc(_("Disable MagicDNS…"))}</button>'
                     if magic else
                     f"""<form method="post" action="{BASE}/dns" data-busy>{csrf_input(session)}
          <input type="hidden" name="section" value="magic"><input type="hidden" name="magic_dns" value="1">
          <button class="btn small primary" type="submit">{esc(_("Enable MagicDNS"))}</button></form>""")
    head = f"""
    <section class="card">
      <div class="card-title"><h2>{esc(_("Tailnet DNS name"))}</h2>{rename_btn}</div>
      <p class="muted">{esc(_("Every machine gets a name under this domain."))}</p>
      <div class="code"><code>{esc(base) or "—"}</code>{copy_btn(base) if base else ""}</div>
    </section>
    <section class="card">
      <div class="card-title"><h2>MagicDNS {badge(_("Enabled"), "green") if magic else badge(_("Disabled"))}</h2>{magic_btn}</div>
      <p class="muted">{esc(_("MagicDNS lets you reach machines by name (e.g. ssh user@my-laptop) instead of by IP."))}</p>
      {f'<table class="simple"><thead><tr><th>{esc(_("Machine"))}</th><th>{esc(_("Full name"))}</th><th></th></tr></thead><tbody>{names}</tbody></table>' if names and magic else ""}
    </section>"""
    sections = dns_nameservers(dns, edit) + dns_search(dns, edit) + dns_records(dns, edit)
    if edit:
        sections = f"""<form method="post" action="{BASE}/dns" data-busy>
      {csrf_input(session)}<input type="hidden" name="section" value="settings">
      {sections}
      <div class="card form-foot">
        <p class="muted small">{esc(_("On save, Headscale validates the configuration and restarts (a few seconds without control plane; existing connections keep working). If anything fails, the previous settings are restored."))}</p>
        <button class="btn primary" type="submit">{esc(_("Save and apply"))}</button>
      </div>
    </form>{dns_dialogs(session, ctx, dns)}"""
    body = (page_head(_("DNS"), esc(sub), link=(_("Learn more"), docs_url("configuration/#dns"))) + flash_html(flash)
            + (notice("error", error) if error else "") + reason + head + sections)
    return layout(_("DNS"), "dns", body, session, ctx)


# -----------------------------------------------------------------------------
# Settings
# -----------------------------------------------------------------------------

def mfa_options() -> list[tuple[str, str, str]]:
    """(value, label, description) of each two-factor mode."""
    return [
        ("admins", _("Required for admins"),
         _("Admins (vpn-admins, authentik Admins) must set it up the first time they sign in; members may.")),
        ("everyone", _("Required for everyone"), _("Every user must set it up when signing in.")),
        ("optional", _("Optional"), _("Nobody is forced; each user decides in their account settings.")),
    ]


def mfa_section(session: dict, mfa: dict) -> str:
    """Admins: the two-factor mode of the built-in Authentik. mfa has 'mode'
    (the one in use, or the installer's choice when it cannot be read),
    'editable' and, when not editable, 'reason'."""
    labels = {value: label for value, label, _d in mfa_options()}
    head = f"""
    <section class="card">
      <h2>{esc(_("Two-factor authentication"))}</h2>
      <p class="muted">{esc(_("A code from an authenticator app or a passkey after the password, when signing in with Authentik. Users who already set one up are always asked for it."))}</p>"""
    if not mfa.get("editable"):
        return head + f"""
      <dl class="kvs">{kv(_("Mode"), esc(labels.get(mfa.get("mode"), mfa.get("mode"))))}</dl>
      <p class="muted small">{esc(mfa.get("reason") or "")}</p>
    </section>"""
    radios = "".join(
        f"""<label class="check"><input type="radio" name="mode" value="{value}" {"checked" if value == mfa.get("mode") else ""} required>
          <span><b>{esc(label)}</b><span class="muted">{esc(desc)}</span></span></label>"""
        for value, label, desc in mfa_options())
    return head + f"""
      <form method="post" action="{BASE}/settings/mfa" class="stack" data-busy>{csrf_input(session)}
        {radios}
        <p class="muted small">{esc(_("Applied in Authentik right away: it affects the next sign-in, nobody is signed out."))}</p>
        <div><button class="btn primary" type="submit">{esc(_("Save"))}</button></div>
      </form>
    </section>"""


def notify_section(session: dict) -> str:
    """Settings > General (admins): where notifications go, and "Send a test"."""
    dests = notify.destinations()
    if not dests:
        body = (f'<p class="muted">{esc(_("Get a message in Slack, Telegram, ntfy or any webhook when a device joins, is removed or its key expires."))}</p>'
                f'<p class="muted small">{esc(_("Set NOTIFY_URLS in .env (or run ./install.sh) and restart the web container."))}</p>')
    else:
        labels = {"device.registered": _("New device"), "device.key_expired": _("Key expired"),
                  "device.expiring": _("Key expiring soon"), "device.removed": _("Device removed"),
                  "backup.failed": _("Backup failed")}
        chosen = notify.events()
        items = "".join(f"<li>{esc(d['label'])}</li>" for d in dests)
        evs = ", ".join(labels[e] for e in notify.ALL_EVENTS if e in chosen)
        body = (f'<ul>{items}</ul>'
                f'<p class="muted small">{esc(_("Events: {events}", events=evs))}</p>'
                f'<form method="post" action="{BASE}/settings/notify-test" data-busy>{csrf_input(session)}'
                f'<button class="btn" type="submit">{esc(_("Send a test"))}</button></form>')
    return f"""
    <section class="card">
      <h2>{esc(_("Notifications"))}</h2>
      {body}
    </section>"""


def general_page(session: dict, ctx: dict, flash: str = "", key_expiry: int | None = None,
                 error: str = "", mfa: dict | None = None, extra: str = "") -> str:
    role = _("Admin") if session.get("admin") else _("Member")
    if session.get("kind") == "apikey":
        role = _("Admin (Headscale API key session)")
    groups = ", ".join(session.get("groups") or []) or "—"
    name = session.get("name") or session.get("username") or _("Administrator")
    manage = (f'<a class="btn" href="{esc(ctx["public_url"])}/authentik/if/user/#/settings">{esc(_("Account, password and two-factor authentication"))}</a>'
              if ctx.get("authentik") and session.get("kind") != "apikey" else "")
    langs = "".join(f'<button type="submit" name="lang" value="{code}" class="{"active" if get_lang() == code else ""}">{esc(label)}</button>'
                    for code, label in LANGUAGES.items())
    devices = ""
    if session.get("admin") and key_expiry is not None:
        never = key_expiry == 0
        devices = f"""
    <section class="card">
      <h2>{esc(_("Device management"))}</h2>
      <form method="post" action="{BASE}/settings/key-expiry" class="stack" data-busy>{csrf_input(session)}
        <label class="field">{esc(_("Key expiry"))}
          <span class="muted small">{esc(_("New devices must sign in again after this many days (Tailscale uses 180). Auth keys have their own expiry: it only limits until when a key can add devices."))}</span>
          <span class="inline"><input type="number" name="days" min="1" max="365" value="{key_expiry or 180}" class="short"> {esc(_("days"))}</span></label>
        <label class="check"><input type="checkbox" name="never" value="1" {"checked" if never else ""}>
          <span>{esc(_("Never expire (not recommended)"))}</span></label>
        <p class="muted small">{esc(_("Saving restarts Headscale: devices reconnect within a few seconds. Existing devices keep their current expiry; change it per machine."))}</p>
        <div><button class="btn primary" type="submit">{esc(_("Save"))}</button></div>
      </form>
    </section>"""
    notifications = notify_section(session) if session.get("admin") else ""
    body = page_head(_("General"), esc(_("Your account and how Headscale Easy looks for you."))) + flash_html(flash) + (notice("error", error) if error else "") + f"""
    <section class="card">
      <h2>{esc(_("Account"))}</h2>
      <div class="account"><span class="avatar big">{initials(name)}</span>
        <div><b>{esc(name)}</b><div class="muted">{esc(session.get("email"))}</div></div></div>
      <dl class="kvs">
        {kv(_("User name"), f"<code>{esc(session.get('username'))}</code>") if session.get("username") else ""}
        {kv(_("Role"), esc(role))}
        {kv(_("Groups"), esc(groups)) if session.get("kind") != "apikey" else ""}
      </dl>
      {manage}
    </section>
    {devices}
    {notifications}
    {mfa_section(session, mfa) if session.get("admin") and mfa is not None else ""}
    {extra}
    <section class="card">
      <h2>{esc(_("Appearance"))}</h2>
      <p class="muted">{esc(_("Saved in this browser."))}</p>
      <div class="segmented" role="radiogroup" aria-label="{esc(_("Theme"))}">
        <button type="button" data-theme-set="system">{esc(_("System"))}</button>
        <button type="button" data-theme-set="dark">{esc(_("Dark"))}</button>
        <button type="button" data-theme-set="light">{esc(_("Light"))}</button>
      </div>
    </section>
    <section class="card">
      <h2>{esc(_("Language"))}</h2>
      <p class="muted">{esc(_("By default, the language of your browser."))}</p>
      <form method="post" action="{BASE}/settings/language" class="segmented">{csrf_input(session)}{langs}</form>
    </section>
    <section class="card">
      <h2>{esc(_("Session"))}</h2>
      <p class="muted">{esc(_("Signs you out here and from the identity provider."))}</p>
      <form method="post" action="{BASE}/logout">{csrf_input(session)}<button class="btn" type="submit">{esc(_("Log out"))}</button></form>
    </section>"""
    return layout(_("General"), "general", body, session, ctx)


def key_state(k: dict) -> tuple[str, str]:
    exp = parse_time(k.get("expiration"))
    if exp is not None and exp <= datetime.now(timezone.utc):
        return _("Expired"), ""
    if k.get("used") and not k.get("reusable"):
        return _("Used"), ""
    return _("Active"), "green"


def user_select(users: list[dict] | None, selected: str = "") -> str:
    opts = "".join(f'<option value="{esc(u["id"])}" {"selected" if str(u["id"]) == selected else ""}>{esc(user_label(u))}</option>'
                   for u in sorted(users or [], key=lambda u: user_label(u).lower()))
    return f'<label class="field">{esc(_("User"))}<select name="user_id" required>{opts}</select></label>'


def apikeys_section(session: dict, apikeys: list[dict], own_prefix: str, new_apikey: str) -> str:
    new_html = ""
    if new_apikey:
        new_html = f"""<div class="keybox"><p><b>{esc(_("API key created."))}</b> {esc(_("Copy it now: it will not be shown again."))}</p>
          <div class="code"><code>{esc(new_apikey)}</code>{copy_btn(new_apikey)}</div></div>"""
    rows = []
    now = datetime.now(timezone.utc)
    for k in sorted(apikeys, key=lambda k: k.get("createdAt") or "", reverse=True):
        exp = parse_time(k.get("expiration"))
        active = exp is None or exp > now
        own = own_prefix and own_prefix in (k.get("prefix") or "")
        state = badge(_("Active"), "green") if active else badge(_("Expired"))
        if own:
            state += " " + badge(_("Used by Headscale Easy"), "blue")
        action = ""
        if active and not own:
            action = f"""<form method="post" action="{BASE}/apikeys/{esc(k['id'])}/expire">{csrf_input(session)}
              <button class="btn small" type="submit">{esc(_("Expire"))}</button></form>"""
        rows.append(f"""<tr><td><code>{esc(k.get("prefix"))}</code></td><td>{time_tag(k.get("createdAt"))}</td>
          <td>{time_tag(k.get("expiration"), _("Never"))}</td><td>{time_tag(k.get("lastSeen"), _("Never"))}</td>
          <td>{state}</td><td class="actions">{action}</td></tr>""")
    table = (f"""<div class="table-wrap"><table class="simple"><thead><tr><th>{esc(_("Prefix"))}</th><th>{esc(_("Created"))}</th>
        <th>{esc(_("Expires"))}</th><th>{esc(_("Last used"))}</th><th>{esc(_("Status"))}</th><th></th></tr></thead>
        <tbody>{"".join(rows)}</tbody></table></div>""" if rows else f'<p class="muted">{esc(_("No API keys."))}</p>')
    return f"""
    <section class="card">
      <div class="card-title"><h2>{esc(_("API keys"))}</h2>
        <button class="btn" type="button" data-open="new-apikey">{esc(_("Generate API key…"))}</button></div>
      <p class="muted">{esc(_("Full admin access to the Headscale API. Use them for automation and never share them."))}</p>
      {new_html}{table}
    </section>
    {dialog("new-apikey", _("Generate API key"), "", f"{BASE}/apikeys", session, submit=_("Generate key"),
            fields=f'<label class="field">{esc(_("Expiration"))}<select name="days"><option value="30">{esc(_("30 days"))}</option>'
                   f'<option value="90" selected>{esc(_("90 days"))}</option><option value="365">{esc(_("365 days"))}</option></select></label>')}"""


def keys_page(session: dict, ctx: dict, keys: list[dict] | None, flash: str, new_key: dict | None = None,
              users: list[dict] | None = None, apikeys: list[dict] | None = None, own_prefix: str = "",
              new_apikey: str = "", preselect: str = "") -> str:
    url = ctx["public_url"]
    admin = session.get("admin")
    by_id = {str(u["id"]): u for u in users or []}

    new_html = ""
    if new_key:
        cmd = f"tailscale up --login-server={url} --authkey={new_key['key']}"
        new_html = f"""
    <section class="card keybox">
      <h2>{esc(_("Auth key generated"))}</h2>
      <p>{esc(_("Copy it now: it will not be shown again."))}</p>
      <div class="code"><code>{esc(new_key["key"])}</code>{copy_btn(new_key["key"])}</div>
      <p class="muted small">{esc(_("To connect a device with it:"))}</p>
      <div class="code"><code>{esc(cmd)}</code>{copy_btn(cmd)}</div>
      <details class="qr-details">
        <summary>{esc(_("Show as QR codes"))}</summary>
        <div class="qr-pair">
          {qr_figure(new_key["key"], _("auth key"), _("Auth key"))}
          {qr_figure(cmd, _("command to connect a Linux device"), _("Linux command"))}
        </div>
        <p class="muted small">{esc(_("Most phone cameras can copy the text of a QR code. On Android, paste the key in the Tailscale app with Use an auth key (⋮ menu, after setting the server)."))}</p>
      </details>
    </section>"""

    if keys is None and not admin:
        table = f'<p class="muted">{esc(_("Auth keys belong to your tailnet user, which is created the first time you connect a device by signing in from it."))} <a class="link" href="{BASE}/add">{esc(_("Add device"))}</a></p>'
        can_create = False
    elif not keys:
        table = f'<p class="muted">{esc(_("No auth keys yet."))}</p>'
        can_create = bool(users) if admin else True
    else:
        can_create = True
        rows = []
        for k in sorted(keys, key=lambda k: k.get("createdAt") or "", reverse=True):
            state, kind = key_state(k)
            flags = [_("Reusable") if k.get("reusable") else _("One-off")]
            if k.get("ephemeral"):
                flags.append(_("Ephemeral"))
            owner = ""
            if admin:
                u = by_id.get(str((k.get("user") or {}).get("id")), k.get("user") or {})
                owner = f"<td>{esc(user_label(u))}</td>"
            revoke = ""
            if state == _("Active"):
                revoke = f"""<form method="post" action="{BASE}/keys/{esc(k['id'])}/revoke">{csrf_input(session)}
                  <button class="btn small" type="submit">{esc(_("Revoke"))}</button></form>"""
            rows.append(f"""<tr><td><code>{esc(k.get("key"))}</code></td>{owner}<td>{esc(" · ".join(flags))}</td>
              <td>{time_tag(k.get("createdAt"))}</td><td>{time_tag(k.get("expiration"))}</td>
              <td>{badge(state, kind)}</td><td class="actions">{revoke}</td></tr>""")
        table = f"""<div class="table-wrap"><table class="simple"><thead><tr><th>{esc(_("Key"))}</th>
          {f"<th>{esc(_('User'))}</th>" if admin else ""}<th>{esc(_("Type"))}</th><th>{esc(_("Created"))}</th>
          <th>{esc(_("Expires"))}</th><th>{esc(_("Status"))}</th><th></th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>"""

    create = (f'<button class="btn primary" type="button" data-open="new">{esc(_("Generate auth key…"))}</button>'
              if can_create else "")
    fields = (user_select(users, preselect) if admin else "") + f"""
        <label class="check"><input type="checkbox" name="reusable" value="1">
          <span><b>{esc(_("Reusable"))}</b><span class="muted">{esc(_("Use it for several machines. Otherwise it is invalidated after the first one."))}</span></span></label>
        <label class="check"><input type="checkbox" name="ephemeral" value="1">
          <span><b>{esc(_("Ephemeral"))}</b><span class="muted">{esc(_("Machines are removed automatically when they go offline. For containers and CI."))}</span></span></label>
        <label class="field">{esc(_("Expiration"))}<select name="days">
          <option value="1">{esc(_("1 day"))}</option><option value="7">{esc(_("7 days"))}</option>
          <option value="30">{esc(_("30 days"))}</option><option value="90" selected>{esc(_("90 days"))}</option></select>
          <span class="muted small">{esc(_("Until when the key can add devices. The devices themselves follow the key expiry in Settings → General."))}</span></label>"""
    body = page_head(_("Keys"), esc(_("Auth keys connect devices without signing in on them: servers, containers or headless devices. The machines belong to the key's user."))) \
        + flash_html(flash) + new_html + f"""
    <section class="card">
      <div class="card-title"><h2>{esc(_("Auth keys"))}</h2>{create}</div>
      {table}
    </section>
    {dialog("new", _("Generate auth key"), "", f"{BASE}/keys", session, fields=fields, submit=_("Generate key"))}
    {apikeys_section(session, apikeys or [], own_prefix, new_apikey) if admin else ""}"""
    return layout(_("Keys"), "keys", body, session, ctx)


def sessions_page(session: dict, ctx: dict, rows: list[dict], flash: str = "") -> str:
    """Settings -> Sessions: your sessions; admins and auditors see everybody's."""
    admin = bool(session.get("admin"))
    sees_all = admin or session.get("role") == "auditor"
    body_rows = []
    for r in rows:
        current = r["sid"] == session.get("sid")
        who = f"<td>{esc(r['name'] or '—')}</td>" if sees_all else ""
        kind = _("API key") if r["kind"] == "apikey" else "OIDC"
        btn = f"""<form method="post" action="{BASE}/settings/sessions/revoke">{csrf_input(session)}
          <input type="hidden" name="sid" value="{esc(r['sid'])}">
          <button class="btn small" type="submit">{esc(_("Log out"))}</button></form>""" \
            if (admin or current or (session.get("sub") and r["sub"] == session.get("sub"))) else ""
        created = datetime.fromtimestamp(r["created"], timezone.utc).isoformat()
        seen = datetime.fromtimestamp(r["last_seen"], timezone.utc).isoformat()
        body_rows.append(f"""<tr>{who}<td>{esc(kind)}</td><td>{esc(r['role'])}</td><td><code>{esc(r['ip'] or '—')}</code></td>
          <td class="muted small">{esc((r['ua'] or '—')[:80])}</td><td>{time_tag(created)}</td><td>{time_tag(seen)}</td>
          <td>{badge(_("This session"), "blue") if current else ""}</td><td class="actions">{btn}</td></tr>""")
    if body_rows:
        table = f"""<div class="table-wrap"><table class="simple"><thead><tr>{f"<th>{esc(_('User'))}</th>" if sees_all else ""}
          <th>{esc(_("Type"))}</th><th>{esc(_("Role"))}</th><th>{esc(_("IP address"))}</th><th>{esc(_("Browser"))}</th>
          <th>{esc(_("Created"))}</th><th>{esc(_("Last activity"))}</th><th></th><th></th></tr></thead>
          <tbody>{"".join(body_rows)}</tbody></table></div>"""
    else:
        table = f'<p class="muted">{esc(_("No active sessions."))}</p>'
    everyone = f"""
      <form method="post" action="{BASE}/settings/sessions/revoke-all" class="inline">{csrf_input(session)}
        <input type="hidden" name="scope" value="everyone">
        <button class="btn" type="submit">{esc(_("Sign out everyone else"))}</button></form>""" if admin else ""
    body = page_head(_("Sessions"), esc(_("Where you are signed in. Signing a session out takes effect at once."))) + flash_html(flash) + f"""
    <section class="card">
      <div class="card-title"><h2>{esc(_("Active sessions"))}</h2>
        <form method="post" action="{BASE}/settings/sessions/revoke-all" class="inline">{csrf_input(session)}
          <button class="btn" type="submit">{esc(_("Sign out everywhere"))}</button></form>{everyone}</div>
      {table}
    </section>"""
    return layout(_("Sessions"), "sessions", body, session, ctx)


# --- TOTP and account settings (Block 3.3) ---

def totp_verify_page(username: str, error: str | None = None) -> str:
    """TOTP verification page (second step after password)."""
    error_html = f'<p class="error">{esc(error)}</p>' if error else ""
    body = f"""
    <div class="login-container">
      <div class="login-card">
        {LOGO}
        <h1>{esc(_("Two-factor authentication"))}</h1>
        <p>{esc(_("Enter the 6-digit code from your authenticator app."))}</p>
        {error_html}
        <form method="post" action="{BASE}/login/totp">
          <label>{esc(_("Code"))}
            <input type="text" name="code" inputmode="numeric" pattern="[0-9]{{6}}"
                   autocomplete="one-time-code" required autofocus maxlength="6">
          </label>
          <button type="submit" class="btn btn-primary">{esc(_("Verify"))}</button>
          <details style="margin-top: 1rem">
            <summary>{esc(_("Use a recovery code"))}</summary>
            <p style="margin-top: 0.5rem; font-size: 0.9rem">{esc(_("Enter one of your recovery codes if you don't have access to your authenticator app."))}</p>
            <input type="hidden" name="use_recovery" value="1">
          </details>
        </form>
        <p class="login-footer">{esc(_("Signed in as"))} <strong>{esc(username)}</strong></p>
      </div>
    </div>"""
    return bare_page(_("Two-factor authentication"), body)


def account_settings_page(session: dict, ctx: dict, account: dict, flash: str = "") -> str:
    """Account settings page for local accounts."""
    username = account['username']
    email = account['email']
    role = account['role']
    totp_enabled = account.get('totp_confirmed', 0) == 1

    role_labels = {
        'admin': _("Administrator"),
        'network_admin': _("Network Administrator"),
        'auditor': _("Auditor"),
        'member': _("Member")
    }
    role_label = role_labels.get(role, role)

    totp_section = f"""
    <section class="card">
      <div class="card-title"><h2>{esc(_("Two-factor authentication"))}</h2></div>
      <p>{esc(_("Two-factor authentication (2FA) adds an extra layer of security to your account."))}</p>
      {"<p class='success'>" + esc(_("Two-factor authentication is enabled.")) + "</p>" if totp_enabled else ""}
      <div class="btn-group">
        {"" if totp_enabled else f'<a href="{BASE}/settings/account/totp/enroll" class="btn btn-primary">{esc(_("Enable 2FA"))}</a>'}
        {f'''<form method="post" action="{BASE}/settings/account/totp/disable" class="inline" onsubmit="return confirm('{esc(_("Are you sure you want to disable two-factor authentication?"))}')">{csrf_input(session)}
          <button type="submit" class="btn">{esc(_("Disable 2FA"))}</button></form>''' if totp_enabled else ""}
        {f'''<form method="post" action="{BASE}/settings/account/totp/recovery/reset" class="inline">{csrf_input(session)}
          <button type="submit" class="btn">{esc(_("Reset recovery codes"))}</button></form>''' if totp_enabled else ""}
      </div>
    </section>"""

    body = page_head(_("Account settings"), "") + flash_html(flash) + f"""
    <section class="card">
      <div class="card-title"><h2>{esc(_("Account information"))}</h2></div>
      <dl class="info-list">
        <dt>{esc(_("Username"))}</dt><dd>{esc(username)}</dd>
        <dt>{esc(_("Email"))}</dt><dd>{esc(email)}</dd>
        <dt>{esc(_("Role"))}</dt><dd>{esc(role_label)}</dd>
      </dl>
    </section>
    <section class="card">
      <div class="card-title"><h2>{esc(_("Change password"))}</h2></div>
      <form method="post" action="{BASE}/settings/account/password">
        {csrf_input(session)}
        <label>{esc(_("Current password"))}
          <input type="password" name="old_password" required autocomplete="current-password">
        </label>
        <label>{esc(_("New password"))}
          <input type="password" name="new_password" required autocomplete="new-password" minlength="8">
        </label>
        <label>{esc(_("Confirm new password"))}
          <input type="password" name="new_password2" required autocomplete="new-password" minlength="8">
        </label>
        <button type="submit" class="btn btn-primary">{esc(_("Change password"))}</button>
      </form>
    </section>
    {totp_section}"""
    return layout(_("Account settings"), "settings", body, session, ctx)


def totp_enroll_page(session: dict, ctx: dict, secret: str, qr_data: str) -> str:
    """TOTP enrollment page with QR code."""
    qr_svg = qr_figure(qr_data)

    # Format secret in groups of 4 for easier manual entry
    secret_formatted = " ".join([secret[i:i+4] for i in range(0, len(secret), 4)])

    body = page_head(_("Set up two-factor authentication"),
                    esc(_("Scan the QR code with your authenticator app, then enter a code to confirm."))) + f"""
    <section class="card">
      <div class="card-title"><h2>{esc(_("1. Scan QR code"))}</h2></div>
      <div style="text-align: center; padding: 1rem;">
        {qr_svg}
      </div>
      <details style="margin-top: 1rem">
        <summary>{esc(_("Can't scan the QR code?"))}</summary>
        <p style="margin-top: 0.5rem">{esc(_("Enter this code manually in your authenticator app:"))}</p>
        <code style="display: block; padding: 0.5rem; background: var(--bg-2); border-radius: 4px; font-size: 0.9rem; word-break: break-all;">
          {esc(secret_formatted)}
        </code>
      </details>
    </section>
    <section class="card">
      <div class="card-title"><h2>{esc(_("2. Verify"))}</h2></div>
      <p>{esc(_("Enter the 6-digit code from your authenticator app to confirm setup."))}</p>
      <form method="post" action="{BASE}/settings/account/totp/confirm">
        {csrf_input(session)}
        <label>{esc(_("Verification code"))}
          <input type="text" name="code" inputmode="numeric" pattern="[0-9]{{6}}"
                 autocomplete="off" required autofocus maxlength="6"
                 placeholder="000000">
        </label>
        <div class="btn-group">
          <button type="submit" class="btn btn-primary">{esc(_("Confirm and enable"))}</button>
          <a href="{BASE}/settings/account" class="btn">{esc(_("Cancel"))}</a>
        </div>
      </form>
    </section>
    <section class="card notice-info">
      <p><strong>{esc(_("Recommended authenticator apps:"))}</strong></p>
      <ul>
        <li>Google Authenticator (iOS, Android)</li>
        <li>Microsoft Authenticator (iOS, Android)</li>
        <li>Authy (iOS, Android, Desktop)</li>
        <li>1Password (all platforms)</li>
      </ul>
    </section>"""
    return layout(_("Set up 2FA"), "settings", body, session, ctx)


def recovery_codes_page(session: dict, ctx: dict, codes: list[str]) -> str:
    """Show recovery codes after generation/reset."""
    codes_html = "".join(f"<li><code>{esc(code)}</code></li>" for code in codes)

    body = page_head(_("Recovery codes"),
                    esc(_("Save these recovery codes in a safe place. Each code can only be used once."))) + f"""
    <section class="card notice-warning">
      <p><strong>{icon('alert-triangle')} {esc(_("Important:"))}</strong>
         {esc(_("These codes will not be shown again. Save them now."))}</p>
    </section>
    <section class="card">
      <div class="card-title"><h2>{esc(_("Your recovery codes"))}</h2></div>
      <ul class="recovery-codes">
        {codes_html}
      </ul>
      <p style="margin-top: 1rem; font-size: 0.9rem; color: var(--text-secondary);">
        {esc(_("Use a recovery code if you lose access to your authenticator app. Each code can only be used once."))}
      </p>
      <div class="btn-group" style="margin-top: 1rem">
        <button onclick="window.print()" class="btn">{icon('printer')} {esc(_("Print"))}</button>
        <a href="{BASE}/settings/account" class="btn btn-primary">{esc(_("Done"))}</a>
      </div>
    </section>
    <style>
      .recovery-codes {{ list-style: none; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.5rem; }}
      .recovery-codes li {{ background: var(--bg-2); padding: 0.75rem; border-radius: 4px; text-align: center; }}
      .recovery-codes code {{ font-size: 1.1rem; font-weight: 600; letter-spacing: 0.05em; }}
      @media print {{
        .nav, .btn-group {{ display: none; }}
        .recovery-codes {{ grid-template-columns: repeat(2, 1fr); }}
      }}
    </style>"""
    return layout(_("Recovery codes"), "settings", body, session, ctx)


def invitation_page(token: str, email: str, role: str, error: str = "") -> str:
    """Accept invitation page - choose username and password."""
    role_labels = {
        'admin': _("Administrator"),
        'network_admin': _("Network Administrator"),
        'auditor': _("Auditor"),
        'member': _("Member")
    }
    role_label = role_labels.get(role, role)

    error_html = f'<p class="error">{esc(error)}</p>' if error else ""

    body = f"""
    <div class="login-container">
      <div class="login-card">
        {LOGO}
        <h1>{esc(_("Accept invitation"))}</h1>
        <p>{esc(_("You've been invited to join this Tailscale network."))}</p>
        {error_html}
        <form method="post" action="{BASE}/accept/{esc(token)}">
          <div style="background: var(--bg-2); padding: 0.75rem; border-radius: 4px; margin-bottom: 1rem;">
            <div style="font-size: 0.9rem; color: var(--text-secondary);">{esc(_("Email"))}</div>
            <div style="font-weight: 600;">{esc(email)}</div>
            <div style="font-size: 0.9rem; color: var(--text-secondary); margin-top: 0.5rem;">{esc(_("Role"))}</div>
            <div style="font-weight: 600;">{esc(role_label)}</div>
          </div>
          <label>{esc(_("Username"))}
            <input type="text" name="username" required autofocus autocomplete="username"
                   pattern="[a-zA-Z0-9_-]{{3,32}}" title="{esc(_("3-32 characters: letters, numbers, - and _"))}"
                   placeholder="{esc(_("Choose a username"))}">
          </label>
          <label>{esc(_("Password"))}
            <input type="password" name="password" required autocomplete="new-password"
                   minlength="8" placeholder="{esc(_("At least 8 characters"))}">
          </label>
          <label>{esc(_("Confirm password"))}
            <input type="password" name="password2" required autocomplete="new-password"
                   placeholder="{esc(_("Re-enter your password"))}">
          </label>
          <button type="submit" class="btn btn-primary">{esc(_("Create account"))}</button>
        </form>
      </div>
    </div>"""
    return bare_page(_("Accept invitation"), body)


def reset_password_page(token: str, username: str, error: str = "") -> str:
    """Reset password page - choose new password."""
    error_html = f'<p class="error">{esc(error)}</p>' if error else ""

    body = f"""
    <div class="login-container">
      <div class="login-card">
        {LOGO}
        <h1>{esc(_("Reset password"))}</h1>
        <p>{esc(_("Choose a new password for your account."))}</p>
        {error_html}
        <form method="post" action="{BASE}/reset/{esc(token)}">
          <div style="background: var(--bg-2); padding: 0.75rem; border-radius: 4px; margin-bottom: 1rem;">
            <div style="font-size: 0.9rem; color: var(--text-secondary);">{esc(_("Username"))}</div>
            <div style="font-weight: 600;">{esc(username)}</div>
          </div>
          <label>{esc(_("New password"))}
            <input type="password" name="password" required autofocus autocomplete="new-password"
                   minlength="8" placeholder="{esc(_("At least 8 characters"))}">
          </label>
          <label>{esc(_("Confirm password"))}
            <input type="password" name="password2" required autocomplete="new-password"
                   placeholder="{esc(_("Re-enter your password"))}">
          </label>
          <button type="submit" class="btn btn-primary">{esc(_("Reset password"))}</button>
        </form>
        <p class="login-footer">
          <a href="{BASE}/login">{esc(_("Back to sign in"))}</a>
        </p>
      </div>
    </div>"""
    return bare_page(_("Reset password"), body)
