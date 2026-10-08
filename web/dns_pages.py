"""The DNS page."""

from __future__ import annotations


from i18n import _
from machines_pages import Machine
from ui import (BASE, badge, copy_btn, csrf_input, dialog, docs_url, esc, flash_html, icon, layout, notice, page_head)


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
