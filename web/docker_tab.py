"""The Docker tab of Add device: a form and ready-to-copy `docker run` and
`docker-compose.yml` snippets that start the official tailscale/tailscale image
on this tailnet.

Everything typed into the form is validated and quoted before it reaches a
snippet, so copying one cannot run something the person did not write."""

from __future__ import annotations

import ipaddress
import json
import re
import shlex

from i18n import _
from ui import BASE, copy_btn, csrf_input, esc, notice

HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
MAX_ROUTES = 20
KEY_DAYS = ("1", "7")  # short on purpose: the container keeps its own state after the first start
DEFAULTS = {"hostname": "tailscale-docker", "exit": False, "routes": "", "userspace": False, "dns": True,
            "generate": False, "days": "1", "user_id": ""}
PLACEHOLDER = "<auth-key>"


def parse(form: dict) -> tuple[dict, str]:
    """(values, error) from the submitted form. On error the values are still safe to show."""
    values = dict(DEFAULTS)
    values["hostname"] = str(form.get("hostname", "")).strip().lower() or DEFAULTS["hostname"]
    values["exit"] = form.get("exit") == "1"
    values["userspace"] = form.get("userspace") == "1"
    values["dns"] = form.get("dns") == "1"
    values["generate"] = form.get("generate") == "1"
    values["days"] = str(form.get("days", "1")) if str(form.get("days", "1")) in KEY_DAYS else "1"
    values["user_id"] = str(form.get("user_id", ""))
    raw = str(form.get("routes", ""))
    values["routes"] = raw[:400]
    error = ""
    if not HOSTNAME_RE.fullmatch(values["hostname"]):
        error = _("Invalid host name: lowercase letters, digits and dashes only (max. 63).")
    routes, bad = [], ""
    for part in re.split(r"[\s,]+", raw.strip()):
        if not part:
            continue
        try:
            routes.append(str(ipaddress.ip_network(part, strict=True)))
        except ValueError:
            bad = part[:40]
            break
    if not error and bad:
        error = _("“{route}” is not a valid route. Use networks like 192.168.1.0/24.", route=bad)
    if not error and len(routes) > MAX_ROUTES:
        error = _("At most {n} routes.", n=MAX_ROUTES)
    if error:
        routes = []
    values["route_list"] = routes
    return values, error


def _ts_args(url: str, v: dict) -> str:
    args = [f"--login-server={url}"]
    if v["exit"]:
        args.append("--advertise-exit-node")
    if v["route_list"]:
        args.append("--advertise-routes=" + ",".join(v["route_list"]))
    if not v["dns"]:
        args.append("--accept-dns=false")
    return " ".join(args)


def _forwarding(v: dict) -> bool:
    return (v["exit"] or bool(v["route_list"])) and not v["userspace"]


def docker_run(url: str, v: dict, key: str) -> str:
    name = "tailscale-" + v["hostname"]
    q = shlex.quote
    lines = [f"docker run -d --name {q(name)}", f"  --hostname {q(v['hostname'])}", "  --restart unless-stopped"]
    if not v["userspace"]:
        lines += ["  --cap-add NET_ADMIN --cap-add NET_RAW", "  --device /dev/net/tun"]
    if _forwarding(v):
        lines += ["  --sysctl net.ipv4.ip_forward=1", "  --sysctl net.ipv6.conf.all.forwarding=1"]
    lines += [f"  -v {q(name)}:/var/lib/tailscale",
              f"  -e TS_AUTHKEY={q(key) if key else PLACEHOLDER}",
              f"  -e TS_HOSTNAME={q(v['hostname'])}",
              "  -e TS_STATE_DIR=/var/lib/tailscale",
              f"  -e TS_USERSPACE={'true' if v['userspace'] else 'false'}",
              f"  -e {q('TS_EXTRA_ARGS=' + _ts_args(url, v))}",
              "  tailscale/tailscale:latest"]
    return " \\\n".join(lines)


def compose(url: str, v: dict, key: str) -> str:
    name = "tailscale-" + v["hostname"]
    j = json.dumps  # a JSON string is a valid, safely quoted YAML scalar
    out = ["services:", "  tailscale:", "    image: tailscale/tailscale:latest",
           f"    container_name: {j(name)}", f"    hostname: {j(v['hostname'])}", "    restart: unless-stopped",
           "    environment:",
           f"      - {j('TS_AUTHKEY=' + (key or PLACEHOLDER))}",
           f"      - {j('TS_HOSTNAME=' + v['hostname'])}",
           "      - TS_STATE_DIR=/var/lib/tailscale",
           f"      - TS_USERSPACE={'true' if v['userspace'] else 'false'}",
           f"      - {j('TS_EXTRA_ARGS=' + _ts_args(url, v))}",
           "    volumes:", "      - tailscale-state:/var/lib/tailscale"]
    if not v["userspace"]:
        out += ["    devices:", "      - /dev/net/tun:/dev/net/tun", "    cap_add:", "      - NET_ADMIN", "      - NET_RAW"]
    if _forwarding(v):
        out += ["    sysctls:", "      - net.ipv4.ip_forward=1", "      - net.ipv6.conf.all.forwarding=1"]
    out += ["", "volumes:", "  tailscale-state:"]
    return "\n".join(out) + "\n"


def _code(text: str) -> str:
    return f'<div class="code"><code class="pre">{esc(text)}</code>{copy_btn(text)}</div>'


def panel(session: dict, url: str, v: dict | None = None, key: str = "", error: str = "",
          users: list[dict] | None = None) -> str:
    v = v or dict(DEFAULTS, route_list=[])
    chk = lambda on: " checked" if on else ""  # noqa: E731
    who = ""
    if users is not None:  # admins pick who owns the device
        options = "".join(f'<option value="{esc(u["id"])}"{" selected" if str(u["id"]) == v["user_id"] else ""}>'
                          f'{esc(u.get("displayName") or u.get("name"))}</option>' for u in users)
        who = f'<label class="field">{esc(_("Owner of the device"))}<select name="user_id">{options}</select></label>'
    days = "".join(f'<option value="{d}"{" selected" if d == v["days"] else ""}>{esc(_("{n} days", n=d) if d != "1" else _("1 day"))}</option>'
                   for d in KEY_DAYS)
    form = f"""
    <form method="post" action="{BASE}/add/docker" class="stack">{csrf_input(session)}
      <label class="field">{esc(_("Host name"))}<input name="hostname" value="{esc(v['hostname'])}" maxlength="63"
        required pattern="[a-z0-9]([a-z0-9\\-]*[a-z0-9])?" autocomplete="off" spellcheck="false"></label>
      <label class="check"><input type="checkbox" name="exit" value="1"{chk(v['exit'])}>
        <span>{esc(_("Offer to be an exit node"))}</span></label>
      <label class="field">{esc(_("Subnet routes to advertise (optional)"))}<input name="routes" value="{esc(v['routes'])}"
        placeholder="192.168.1.0/24, 10.0.0.0/8" autocomplete="off" spellcheck="false"></label>
      <label class="check"><input type="checkbox" name="userspace" value="1"{chk(v['userspace'])}>
        <span>{esc(_("Userspace networking (no /dev/net/tun, no extra permissions)"))}</span></label>
      <label class="check"><input type="checkbox" name="dns" value="1"{chk(v['dns'])}>
        <span>{esc(_("Use this tailnet's DNS settings"))}</span></label>
      {who}
      <label class="check"><input type="checkbox" name="generate" value="1"{chk(v['generate'])}>
        <span>{esc(_("Generate a single-use auth key"))}</span></label>
      <label class="field">{esc(_("The key is valid for"))}<select name="days">{days}</select></label>
      <div class="form-foot"><button class="btn primary" type="submit">{esc(_("Update the snippets"))}</button></div>
    </form>"""
    err = notice("error", error) if error else ""
    if error:
        return f"{err}{form}"
    keys_link = f'<a class="link" href="{BASE}/settings/keys">' + esc(_("Settings → Keys")) + "</a>"
    if key:
        key_note = notice("ok", _("Auth key generated. It is in the snippets below and is not shown again: copy them now."))
    else:
        key_note = '<p class="muted small">' + _(
            "Replace {placeholder} with an auth key from {link}, or generate one above.",
            placeholder="<code>&lt;auth-key&gt;</code>", link=keys_link) + "</p>"
    name = "tailscale-" + v["hostname"]
    return f"""
    <ol class="steps">
      <li>{esc(_("Set it up:"))}{form}</li>
      <li>{esc(_("Start it with Docker:"))}{_code(docker_run(url, v, key))}</li>
      <li>{esc(_("Or save this as docker-compose.yml and run docker compose up -d:"))}{_code(compose(url, v, key))}</li>
      <li>{_("Without an auth key, leave TS_AUTHKEY out and run {cmd}: it prints a link to sign in.", cmd=f"<code>docker logs {esc(name)}</code>")}</li>
    </ol>
    {key_note}"""
