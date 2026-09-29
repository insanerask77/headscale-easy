"""Pages of Headscale Easy: Machines (list and detail), Add device, DNS and
Settings (General, Keys). Admin-only parts are rendered only when the session
is an admin; app.py enforces the same rule on every action."""

from __future__ import annotations

import csv
import io
import ipaddress
from datetime import datetime, timezone

from i18n import LANGUAGES, _, get_lang, ngettext
from ui import (BASE, LOGO, badge, copy_btn, csrf_input, docs_url, esc, flash_html, icon, initials, layout, notice,
                page_head, parse_time, relative, time_tag, user_label)

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

        os_raw = (hi.get("OS") or "").lower()
        self.os = OS_NAMES.get(os_raw, hi.get("OS") or "")
        if os_raw == "linux" and hi.get("Distro"):
            self.os_detail = f"{hi['Distro'].capitalize()} {hi.get('DistroVersion', '')}".strip()
        else:
            self.os_detail = hi.get("OSVersion") or ""
        self.version = (hi.get("IPNVersion") or "").split("-")[0]
        self.latest = latest
        self.update_available = bool(self.version and latest and version_tuple(self.version) < version_tuple(latest))

        derp = (hi.get("NetInfo") or {}).get("PreferredDERP")
        self.derp = regions.get(derp, _("Region {n}", n=derp)) if derp else ""

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
    items = [] if detail else [f'<a href="{BASE}/machines/{m.id}">{esc(_("View details"))}</a>']
    items.append(f'<button type="button" data-open="rename-{m.id}">{esc(_("Edit machine name…"))}</button>')
    if admin:
        items.append(f'<button type="button" data-open="tags-{m.id}">{esc(_("Edit ACL tags…"))}</button>')
        if m.exit_node or m.subnets:
            items.append(f'<a href="{BASE}/machines/{m.id}#routes">{esc(_("Edit route settings…"))}</a>')
    if m.ipv4:
        items.append(f'<button type="button" data-copy="{esc(m.ipv4)}">{esc(_("Copy IPv4"))}</button>')
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
    head += flash_html(flash)

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
          <button type="button" class="btn small" data-f-clear>{esc(_("Clear filters"))}</button>
        </div>
      </details>"""

    rows, dialogs = [], []
    for m in machines:
        rows.append(f"""
        <tr data-href="{BASE}/machines/{m.id}" data-search="{esc(m.search_text())}" data-status="{'on' if m.online else 'off'}"
            data-owner="{esc(m.owner.get('id'))}" data-update="{int(m.update_available)}"
            data-routes="{int(m.exit_node or bool(m.subnets))}" data-expired="{int(m.expired)}">
          <td><a class="name" href="{BASE}/machines/{m.id}">{esc(m.name)}</a>
            <div class="owner">{esc(m.owner_label)}</div>
            <div class="badges">{m.badges()}</div></td>
          <td>{addresses_dropdown(m)}</td>
          <td class="hide-sm">{version_html(m)}</td>
          <td>{status_html(m)}</td>
          <td class="actions">{machine_menu(m, session)}</td>
        </tr>""")
        dialogs.append(machine_dialogs(m, session, "machines"))

    body = head + f"""
    <div class="toolbar">
      <label class="search">{icon("search")}<input type="search" placeholder="{esc(_("Search by name, owner, tag, version…"))}" data-filter aria-label="{esc(_("Search machines"))}"></label>
      {filters}
      <a class="link hide-sm" href="{docs_url("operations/#managing-machines")}" target="_blank" rel="noopener">{esc(_("Learn more"))}</a>
      <span class="spacer"></span>
      <a class="icon-btn boxed" href="{BASE}/machines.csv" title="{esc(_("Export to CSV"))}" aria-label="{esc(_("Export to CSV"))}">{icon("download")}</a>
    </div>
    <span class="pill" data-count data-one="{esc(_("1 machine"))}" data-many="{esc(_("{n} machines"))}">{esc(ngettext("{n} machine", "{n} machines", len(machines)))}</span>
    <div class="table-wrap">
      <table class="machines">
        <thead><tr><th>{esc(_("Machine"))}</th>
          <th><span title="{esc(_("The machine's Tailscale IP addresses and MagicDNS name"))}">{esc(_("Addresses"))} {icon("info", "i-xs")}</span></th>
          <th class="hide-sm">{esc(_("Version"))}</th><th>{esc(_("Last seen"))}</th><th></th></tr></thead>
        <tbody data-live="rows">{"".join(rows)}
        </tbody>
      </table>
    </div>
    <p class="no-results muted" hidden>{esc(_("No machines match the current filters."))}</p>
    <div data-live="dialogs">{"".join(dialogs)}</div>"""
    return layout(_("Machines"), "machines", body, session, ctx)


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
    connection = "".join([
        kv(_("Preferred DERP relay"), esc(m.derp) or "—"),
        kv(_("Endpoints"), f'<ul class="plain">{endpoints}</ul>' if endpoints else "—"),
    ])

    body = f"""
    <nav class="crumbs"><a href="{BASE}/machines">{esc(_("Machines"))}</a><span>/</span>{esc(m.name)}</nav>
    {notice("error", error) if error else ""}{flash_html(flash)}
    <div class="page-head">
      <div>
        <h1 data-live="title">{esc(m.name)}</h1>
        <div class="meta" data-live="meta">{status_html(m)}<span class="muted">{esc(m.owner_label)}</span>{m.badges()}</div>
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

def add_page(session: dict, ctx: dict) -> str:
    url = ctx["public_url"]
    login = f"tailscale up --login-server={url}"

    def code(cmd: str) -> str:
        return f'<div class="code"><code>{esc(cmd)}</code>{copy_btn(cmd)}</div>'

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
          <ol class="steps">
            <li>{esc(_("Install Tailscale from the App Store."))}</li>
            <li>{esc(_("Open Settings → Tailscale and turn on Use Alternate Coordination Server with this URL:"))}{code(url)}</li>
            <li>{esc(_("Open the Tailscale app and sign in with your account."))}</li>
          </ol>
          <p class="muted small">{esc(_("Headscale's guide for Apple devices:"))} <a class="link" href="{esc(url)}/apple" target="_blank">{esc(url)}/apple</a></p>"""),
        "android": ("Android", f"""
          <ol class="steps">
            <li>{esc(_("Install Tailscale from Google Play."))}</li>
            <li>{esc(_("On the sign-in screen open the ⋮ menu, choose Use an alternate server and enter:"))}{code(url)}</li>
            <li>{esc(_("Sign in with your account."))}</li>
          </ol>"""),
    }
    tabs = "".join(f'<button type="button" role="tab" data-tab="{k}" class="{"active" if k == "linux" else ""}">{label}</button>'
                   for k, (label, _c) in panels.items())
    bodies = "".join(f'<div class="tab-panel" data-panel="{k}" {"" if k == "linux" else "hidden"}>{content}</div>'
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

def dns_form(session: dict, ctx: dict, dns: dict, error: str) -> str:
    if not ctx.get("dns_editable"):
        return notice("error", ctx.get("dns_reason") or _("Editing DNS is not available."))
    split = "\n".join(f"{d}: {', '.join(v)}" for d, v in (dns.get("split") or {}).items())
    return f"""{notice("error", error) if error else ""}
    <form method="post" action="{BASE}/dns" class="card stack" data-busy>
      {csrf_input(session)}
      <h2>{esc(_("Edit DNS"))}</h2>
      <label class="check"><input type="checkbox" name="magic_dns" value="1" {"checked" if dns.get("magic_dns") else ""}>
        <span><b>MagicDNS</b><span class="muted">{esc(_("Reach machines by name."))}</span></span></label>
      <label class="check"><input type="checkbox" name="override_local_dns" value="1" {"checked" if dns.get("override_local_dns") else ""}>
        <span><b>{esc(_("Override local DNS"))}</b><span class="muted">{esc(_("Devices use the nameservers below instead of their own."))}</span></span></label>
      <label class="field">{esc(_("Tailnet DNS name (base_domain)"))}
        <input name="base_domain" value="{esc(dns.get("base_domain"))}" required spellcheck="false">
        <span class="muted small">{esc(_("Must differ from the server's domain ({host}).", host=ctx.get("server_host", "")))}</span></label>
      <label class="field">{esc(_("Global nameservers"))}
        <textarea name="nameservers" rows="3" spellcheck="false" placeholder="1.1.1.1&#10;https://dns.nextdns.io/abc123">{esc(chr(10).join(dns.get("nameservers") or []))}</textarea>
        <span class="muted small">{esc(_("One per line: an IP or a DoH resolver (https://…)."))}</span></label>
      <label class="field">{esc(_("Split DNS"))}
        <textarea name="split" rows="3" spellcheck="false" placeholder="corp.lan: 10.0.0.53, 10.0.0.54">{esc(split)}</textarea>
        <span class="muted small">{esc(_("One line per domain: domain: server, server"))}</span></label>
      <label class="field">{esc(_("Search domains"))}
        <textarea name="search_domains" rows="2" spellcheck="false" placeholder="corp.lan">{esc(chr(10).join(dns.get("search_domains") or []))}</textarea>
        <span class="muted small">{esc(_("One per line."))}</span></label>
      <div class="form-foot">
        <p class="muted small">{esc(_("On save, Headscale validates the configuration and restarts (a few seconds without control plane; existing connections keep working). If anything fails, the previous settings are restored."))}</p>
        <button class="btn primary" type="submit">{esc(_("Save and apply"))}</button>
      </div>
    </form>"""


def dns_page(session: dict, ctx: dict, dns: dict, machines: list[Machine], error: str = "", flash: str = "") -> str:
    admin = session.get("admin")
    base = dns.get("base_domain") or ""
    magic = dns.get("magic_dns")
    ns = "".join(f"<li><code>{esc(n)}</code></li>" for n in dns.get("nameservers") or [])
    search = "".join(f"<li><code>{esc(d)}</code></li>" for d in dns.get("search_domains") or [])
    split_rows = "".join(f"<tr><td><code>{esc(d)}</code></td><td>{esc(', '.join(v))}</td></tr>"
                         for d, v in (dns.get("split") or {}).items())
    names = "".join(f'<tr><td>{esc(m.name)}</td><td><code>{esc(m.fqdn)}</code></td><td class="actions">{copy_btn(m.fqdn)}</td></tr>'
                    for m in machines if m.fqdn)
    sub = _("DNS settings are shared by the whole tailnet.") + ("" if admin else " " + _("Only an admin can change them."))
    body = page_head(_("DNS"), esc(sub), link=(_("Learn more"), docs_url("configuration/#dns"))) + flash_html(flash) + f"""
    <section class="card">
      <h2>{esc(_("Tailnet DNS name"))}</h2>
      <p class="muted">{esc(_("Every machine gets a name under this domain."))}</p>
      <div class="code"><code>{esc(base) or "—"}</code>{copy_btn(base) if base else ""}</div>
    </section>
    <section class="card">
      <div class="card-title"><h2>MagicDNS</h2>{badge(_("Enabled"), "green") if magic else badge(_("Disabled"))}</div>
      <p class="muted">{esc(_("MagicDNS lets you reach machines by name (e.g. ssh user@my-laptop) instead of by IP."))}</p>
      {f'<table class="simple"><thead><tr><th>{esc(_("Machine"))}</th><th>{esc(_("Full name"))}</th><th></th></tr></thead><tbody>{names}</tbody></table>' if names and magic else ""}
    </section>
    <div class="grid-2">
      <section class="card"><h2>{esc(_("Nameservers"))}</h2>
        {f'<ul class="plain">{ns}</ul>' if ns else f'<p class="muted">{esc(_("Each device uses its own."))}</p>'}</section>
      <section class="card"><h2>{esc(_("Search domains"))}</h2>
        {f'<ul class="plain">{search}</ul>' if search else f'<p class="muted">{esc(_("None."))}</p>'}</section>
    </div>
    {f'<section class="card"><h2>{esc(_("Split DNS"))}</h2><table class="simple"><thead><tr><th>{esc(_("Domain"))}</th><th>{esc(_("Nameservers"))}</th></tr></thead><tbody>{split_rows}</tbody></table></section>' if split_rows else ""}
    {dns_form(session, ctx, dns, error) if admin else ""}"""
    return layout(_("DNS"), "dns", body, session, ctx)


# -----------------------------------------------------------------------------
# Settings
# -----------------------------------------------------------------------------

def general_page(session: dict, ctx: dict, flash: str = "", key_expiry: int | None = None,
                 error: str = "") -> str:
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
