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
from urllib.parse import urlparse

from i18n import _
from ui import BASE, copy_btn, csrf_input, esc, notice

HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
MAX_ROUTES = 20
KEY_DAYS = ("1", "7")  # short on purpose: the container keeps its own state after the first start
DEFAULTS = {"hostname": "tailscale-docker", "exit": False, "routes": "", "userspace": False, "dns": True,
            "generate": False, "days": "1", "user_id": ""}
PLACEHOLDER = "<auth-key>"
# Image tags the snippets can pin; the first is the tested one (the exit node and firewall behaviour was
# verified on it) and the default. "latest" is offered last, on purpose: it changes under the person's feet.
TS_VERSIONS = ("v1.102.5", "latest")
DEFAULTS["version"] = TS_VERSIONS[0]


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
    values["version"] = str(form.get("version", "")) if str(form.get("version", "")) in TS_VERSIONS else TS_VERSIONS[0]
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
    return " ".join(args)


def _env(url: str, v: dict, key: str, ref: bool = False) -> list[str]:
    """KEY=value pairs for the official image's own variables (TS_ROUTES, TS_ACCEPT_DNS, TS_AUTH_ONCE...).
    With ref, the auth key is a ${TS_AUTHKEY} reference to a .env file instead of the key itself."""
    env = ["TS_AUTHKEY=${TS_AUTHKEY}" if ref else f"TS_AUTHKEY={key or PLACEHOLDER}", f"TS_HOSTNAME={v['hostname']}",
           "TS_STATE_DIR=/var/lib/tailscale",
           "TS_AUTH_ONCE=true",  # a restart keeps the saved identity instead of logging in again with a spent key
           f"TS_USERSPACE={'true' if v['userspace'] else 'false'}",
           f"TS_ACCEPT_DNS={'true' if v['dns'] else 'false'}"]
    if v["route_list"]:
        env.append("TS_ROUTES=" + ",".join(v["route_list"]))
    if not v["userspace"]:
        env.append("TS_DEBUG_FIREWALL_MODE=auto")  # nftables-only hosts (no iptables tables): tailscaled picks the backend that works
    env.append("TS_EXTRA_ARGS=" + _ts_args(url, v))
    return env


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
    lines += [f"  -v {q(name)}:/var/lib/tailscale"]
    lines += [f"  -e {q(e) if not e.endswith(PLACEHOLDER) else e}" for e in _env(url, v, key)]
    lines += [f"  tailscale/tailscale:{v['version']}"]
    return " \\\n".join(lines)


def env_file(key: str) -> str:
    """The .env file that sits next to docker-compose.yml and holds the only secret."""
    value = key or PLACEHOLDER
    return f"TS_AUTHKEY={value if re.fullmatch(r'[A-Za-z0-9_.<>-]+', value) else shlex.quote(value)}\n"


def compose(url: str, v: dict, key: str) -> str:
    """The compose file never holds the key (it gets pasted into tickets and repos): see env_file()."""
    name = "tailscale-" + v["hostname"]
    j = json.dumps  # a JSON string is a valid, safely quoted YAML scalar
    out = ["services:", "  tailscale:", f"    image: tailscale/tailscale:{v['version']}",
           f"    container_name: {j(name)}", f"    hostname: {j(v['hostname'])}", "    restart: unless-stopped",
           "    environment:"]
    out += [f"      - {j(e)}" for e in _env(url, v, key, ref=True)]
    out += ["    volumes:", "      - tailscale-state:/var/lib/tailscale"]
    if not v["userspace"]:
        out += ["    devices:", "      - /dev/net/tun:/dev/net/tun", "    cap_add:", "      - NET_ADMIN", "      - NET_RAW"]
    if _forwarding(v):
        out += ["    sysctls:", "      - net.ipv4.ip_forward=1", "      - net.ipv6.conf.all.forwarding=1"]
    out += ["", "volumes:", "  tailscale-state:"]
    return "\n".join(out) + "\n"


# Tailscale's own "Use exit nodes" guide: IP forwarding on a Linux host that has a /etc/sysctl.d directory
FORWARDING_CMDS = ("echo 'net.ipv4.ip_forward = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf\n"
                   "echo 'net.ipv6.conf.all.forwarding = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf\n"
                   "sudo sysctl -p /etc/sysctl.d/99-tailscale.conf")


def reconfigure_cmd(v: dict) -> str:
    """`docker exec ... tailscale set` that applies the form's options to a container that already runs.
    An empty --advertise-routes= and --advertise-exit-node=false withdraw what was advertised before."""
    name = "tailscale-" + v["hostname"]
    return (f"docker exec {shlex.quote(name)} tailscale set"
            f" --advertise-routes={shlex.quote(','.join(v['route_list']))}"
            f" --advertise-exit-node={'true' if v['exit'] else 'false'}"
            f" --accept-dns={'true' if v['dns'] else 'false'}")


def _code(text: str) -> str:
    return f'<div class="code"><code class="pre">{esc(text)}</code>{copy_btn(text)}</div>'


def _tips(v: dict) -> str:
    """What to try when an exit node or a subnet router does not route (kernel networking only)."""
    if not _forwarding(v):
        return ""
    return f"""
    <details class="stack"><summary>{esc(_("If the exit node or the routes do not work"))}</summary>
      <ol class="steps">
        <li>{esc(_("Approve it: open the machine in Machines and enable the exit node or the routes, unless the policy approves them automatically."))}</li>
        <li>{esc(_("Enable IP forwarding, IPv4 and IPv6, on the host too, then restart the container:"))}{_code(FORWARDING_CMDS)}</li>
        <li>{esc(_("If the host cannot give the container /dev/net/tun, tick “Userspace networking” above: it works everywhere, with lower performance."))}</li>
      </ol>
    </details>"""


def loopback(url: str) -> bool:
    """Does the server's address point at the machine it runs on? From inside a
    container that is the container itself, so it can never reach the server."""
    host = (urlparse(url).hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback or host in ("0.0.0.0", "::")
    except ValueError:
        return False


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
    versions = "".join(f'<option value="{t}"{" selected" if t == v["version"] else ""}>'
                       f'{esc(t if t != "latest" else _("latest (not pinned)"))}</option>' for t in TS_VERSIONS)
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
      <label class="field">{esc(_("Tailscale version"))}<select name="version">{versions}</select></label>
      <label class="check"><input type="checkbox" name="generate" value="1"{chk(v['generate'])}>
        <span>{esc(_("Generate a single-use auth key"))}</span></label>
      <label class="field">{esc(_("The key is valid for"))}<select name="days">{days}</select></label>
      <div class="form-foot"><button class="btn primary" type="submit">{esc(_("Update the snippets"))}</button></div>
    </form>"""
    err = notice("error", error) if error else ""
    warn = notice("warn", _(
        "This server's address is {url}. Inside a container “localhost” is the container itself, so it cannot reach the "
        "server. Use the server's real address (a name or IP the container can reach), or on Linux add --network host "
        "to the container (and drop --hostname and --sysctl).", url=url)) if loopback(url) else ""
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
    {warn}
    <ol class="steps">
      <li>{esc(_("Set it up:"))}{form}</li>
      <li>{esc(_("Start it with Docker:"))}{_code(docker_run(url, v, key))}</li>
      <li>{esc(_("Or save this as docker-compose.yml and run docker compose up -d:"))}{_code(compose(url, v, key))}
        {esc(_("The auth key is not in that file. Save it next to it as .env:"))}{_code(env_file(key))}</li>
      <li>{_("Without an auth key, leave TS_AUTHKEY out and run {cmd}: it prints a link to sign in.", cmd=f"<code>docker logs {esc(name)}</code>")}</li>
    </ol>
    <p class="muted small">{esc(_("After the first successful start, delete the TS_AUTHKEY line from .env: the container keeps its own identity. Revoke keys you did not use in Settings → Keys."))}</p>
    <details class="stack"><summary>{esc(_("Already running? Apply a changed option without recreating the container"))}</summary>
      <p class="muted small">{esc(_("Recreating the container does not withdraw a route or an exit node it already advertised: the saved state wins. This command sets exactly what the form says, and an empty route list or an unticked exit node withdraws it:"))}</p>
      {_code(reconfigure_cmd(v))}
    </details>
    {_tips(v)}
    {key_note}"""
