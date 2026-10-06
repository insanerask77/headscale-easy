"""DERP relays page: which relays the devices use and how fast they are, plus
the editor for an own DERP map (admins). The data comes from derp.py."""

from __future__ import annotations

import os

from i18n import _
from ui import BASE, csrf_input, esc, flash_html, layout, notice, page_head

_ROW_FIELDS = ("id", "code", "name", "hostname", "ipv4", "ipv6", "stun_port", "derp_port", "stun_only", "remove")


def relays_from_form(form: dict) -> tuple[list[dict], str]:
    """The relays typed in the editor. Rows marked "remove" and rows left blank
    are dropped. (relays, error)."""
    def col(name: str) -> list[str]:
        value = form.get(f"{name}[]", [])
        return value if isinstance(value, list) else [value]

    cols = {f: col(f) for f in _ROW_FIELDS}
    count = max((len(v) for v in cols.values()), default=0)
    relays = []
    for i in range(count):
        def cell(name: str) -> str:
            values = cols[name]
            return values[i].strip() if i < len(values) else ""

        if cell("remove") == "1":
            continue
        typed = [cell(f) for f in ("id", "code", "name", "hostname", "ipv4", "ipv6")]
        if not any(typed):
            continue
        for label, raw in ((_("Region ID"), cell("id")), (_("STUN port"), cell("stun_port") or "3478"),
                           (_("DERP port"), cell("derp_port") or "443")):
            if not raw.isdigit():
                return [], _("{field} must be a number.", field=label)
        relays.append({"id": int(cell("id")), "code": cell("code").lower(), "name": cell("name"),
                       "hostname": cell("hostname").lower(), "ipv4": cell("ipv4"), "ipv6": cell("ipv6").lower(),
                       "stun_port": int(cell("stun_port") or 3478), "derp_port": int(cell("derp_port") or 443),
                       "stun_only": cell("stun_only") == "1"})
    return relays, ""


def _status_table(rows: list[dict], embedded: int | None) -> str:
    if not rows:
        return f'<p class="muted">{esc(_("No device has reported its DERP relays yet."))}</p>'
    body = ""
    for r in rows:
        tag = f' <span class="badge blue">{esc(_("Embedded"))}</span>' if r["id"] == embedded else ""
        ms = esc(_("{ms} ms", ms=round(r["latency"]))) if r["latency"] is not None else "—"
        body += f"<tr><td>{esc(r['name'])}{tag}</td><td><code>{r['id']}</code></td><td>{r['devices']}</td><td>{ms}</td></tr>"
    return f"""<div class="table-wrap"><table class="simple">
      <thead><tr><th>{esc(_("Relay"))}</th><th>{esc(_("Region ID"))}</th><th>{esc(_("Devices using it"))}</th>
        <th>{esc(_("Median latency"))}</th></tr></thead><tbody>{body}</tbody></table></div>"""


def _row(r: dict, editable: bool) -> str:
    dis = "" if editable else " disabled"

    def inp(name: str, value, cls: str = "", ph: str = "") -> str:
        return (f'<input name="{name}[]" value="{esc(value)}" class="{cls}" placeholder="{esc(ph)}" '
                f'autocomplete="off" spellcheck="false"{dis}>')

    stun_only = (f'<select name="stun_only[]"{dis}><option value="0">{esc(_("Relay and STUN"))}</option>'
                 f'<option value="1"{" selected" if r["stun_only"] else ""}>{esc(_("STUN only"))}</option></select>')
    remove = (f'<select name="remove[]"{dis}><option value="0">{esc(_("Keep"))}</option>'
              f'<option value="1">{esc(_("Remove"))}</option></select>')
    return f"""<tr>
      <td>{inp("id", r["id"], "short", "900")}</td><td>{inp("code", r["code"], "", "myrelay")}</td>
      <td>{inp("name", r["name"], "", _("My relay"))}</td><td>{inp("hostname", r["hostname"], "", "derp.example.com")}</td>
      <td>{inp("ipv4", r["ipv4"])}</td><td>{inp("ipv6", r["ipv6"])}</td>
      <td>{inp("derp_port", r["derp_port"], "short")}</td><td>{inp("stun_port", r["stun_port"], "short")}</td>
      <td>{stun_only}</td><td>{remove}</td></tr>"""


def editor(session: dict, relays: list[dict], reason: str) -> str:
    editable = not reason
    blank = {"id": "", "code": "", "name": "", "hostname": "", "ipv4": "", "ipv6": "", "derp_port": 443,
             "stun_port": 3478, "stun_only": False}
    rows = "".join(_row(r, editable) for r in relays) + (_row(blank, editable) if editable else "")
    head = "".join(f"<th>{esc(h)}</th>" for h in (
        _("Region ID"), _("Code"), _("Name"), _("Hostname"), "IPv4", "IPv6", _("DERP port"), _("STUN port"),
        _("Mode"), ""))
    save = (f"""<p class="muted small">{esc(_("On save, Headscale validates the configuration and restarts (a few seconds without control plane; existing connections keep working). If anything fails, the previous map is restored."))}</p>
        <div><button class="btn primary" type="submit">{esc(_("Save DERP map"))}</button></div>""" if editable else "")
    return f"""
    <section class="card">
      <h2>{esc(_("Own DERP relays"))}</h2>
      <p class="muted">{esc(_("Add relays you run yourself (derper). Headscale hands them to every device together with the embedded relay and, if enabled, Tailscale's public ones. Region IDs 900 to 998 are for own relays."))}</p>
      {notice("warn", reason) if reason else ""}
      <form method="post" action="{BASE}/derp" class="stack" data-busy>{csrf_input(session)}
        <div class="table-wrap"><table class="simple"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>
        {save}
      </form>
    </section>"""


def derp_page(session: dict, ctx: dict, rows: list[dict], embedded: int | None, relays: list[dict],
              reason: str, flash: str = "", error: str = "") -> str:
    """reason: why the editor is read only ('' = editable); only admins edit."""
    if not session.get("admin"):
        reason = _("Only an admin can change the DERP map.")
    modes = {"embedded": _("This server (recommended)"), "public": _("Tailscale's public relays"),
             "custom": _("My own DERP map")}
    mode = modes.get(os.environ.get("HSE_DERP_MODE", ""))
    mode_note = f'<p class="muted">{esc(_("Mode in use: {mode}", mode=mode))}</p>' if mode else ""
    body = page_head(_("DERP relays"),
                     esc(_("Relays carry traffic between devices that cannot connect directly. Each device uses the closest one."))) \
        + flash_html(flash) + (notice("error", error) if error else "") + f"""
    <section class="card"><h2>{esc(_("Relay status"))}</h2>{mode_note}{_status_table(rows, embedded)}
      <p class="muted small">{esc(_("From what the devices report. Latency is the median of the devices that measured it."))}</p></section>
    {editor(session, relays, reason)}"""
    return layout(_("DERP relays"), "derp", body, session, ctx)

