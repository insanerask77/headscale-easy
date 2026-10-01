# Production and hardening

The [quick start](getting-started.md) gets a working server in minutes. This
page is what to do **before you rely on it**: a production checklist, then each
hardening step in detail.

!!! warning "Security notice"
    Headscale Easy is young, security-sensitive networking software, written
    with extensive AI assistance and **not independently audited** (see
    [Security](security.md) and [AI usage](ai-usage.md)). Review it and harden
    your deployment before you expose it to the Internet.

## Production checklist

- [ ] A dedicated host or VM, kept up to date (unattended security upgrades).
- [ ] A real domain and **`SSL_MODE=letsencrypt`**, or `front` behind a proxy
      you already trust. No `http` mode outside a LAN.
- [ ] Host firewall: only 22 (from your IP), 80, 443 and UDP 3478 open.
- [ ] Sign-in with OIDC (built-in Authentik or your own provider), and
      **`PORTAL_API_KEY_LOGIN=false`**.
- [ ] Two-factor authentication **required for admins** (`MFA_REQUIRED=admins`
      or `everyone`), and the `akadmin` password changed and stored safely.
- [ ] `NETWORK_ISOLATION=true` unless you have written your own ACL policy.
- [ ] The web UI reachable only from where you administer it (LAN, VPN or
      tailnet), if you can — see [below](#restrict-the-web-ui).
- [ ] The Docker socket only in `hs-helper` (`make validate` checks it), and
      `hs-helper` stopped if you do not edit DNS or key expiry from the
      console.
- [ ] Daily backups on, **copied off the server**, and a restore tested once.
- [ ] Someone watching [releases](https://github.com/insanerask77/headscale-easy/releases)
      and running `make update`.

## Firewall

Only Caddy publishes TCP ports. On the host, allow just what is needed, for
example with `ufw`:

```bash
sudo ufw default deny incoming
sudo ufw allow from <your-admin-ip> to any port 22 proto tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 3478/udp     # STUN for the embedded DERP relay
sudo ufw enable
```

!!! note
    Docker publishes ports by editing iptables itself and bypasses `ufw` for
    container ports. That is fine here — the only published ports are the ones
    above — but do not publish extra ports (Headscale's `9090` metrics, `50443`
    gRPC) in `docker-compose.override.yml` on a public host.

## HTTPS

- **`letsencrypt`** (recommended): Caddy gets and renews the certificate and
  redirects HTTP to HTTPS. Needs ports 80 and 443 reachable and a DNS record
  pointing at the host.
- **`front`**: your own proxy terminates TLS and forwards to Caddy on port 80.
  Then the connection between your proxy and this host is plain HTTP: keep it
  on a private network or on the same machine, and make sure the proxy passes
  WebSockets and does not buffer (the generated snippets do both).
- **`selfsigned`**: only for tests or closed networks; every client must trust
  `caddy-root-ca.crt`.
- **`http`**: LAN only. Cookies are then not `Secure` and passwords travel in
  clear text.

## Sign-in

- Keep **`PORTAL_API_KEY_LOGIN=false`** when you use OIDC. Any Headscale API
  key then stops being a way into the console. Turn it on only as emergency
  access and turn it off again.
- With the built-in Authentik: require two-factor for admins
  (Settings → General in the web UI, or `MFA_REQUIRED` in `.env`), use a real
  email for `akadmin`, and give admin rights through the `vpn-admins` group
  only to the people who need them.
- With your own OIDC provider: make sure it verifies email addresses if you
  use `PORTAL_ADMIN_EMAILS` (an email it marks as not verified never grants
  admin), and prefer groups (`PORTAL_ADMIN_GROUPS`) if it can send a `groups`
  claim. Enforce two-factor there.
- Sessions last 8 hours and are kept server-side (`data/web/sessions.db`,
  `600`), so they can be revoked at once: **Settings → Sessions** lists them
  (yours; admins and auditors see everybody's) with **Log out** per session,
  **Sign out everywhere** and, for admins, **Sign out everyone else**. Deleting
  a user, or a change of someone's role at their next sign-in, revokes their
  older sessions. Cookies from before this feature have no session id and ask
  for a new sign-in. Changing `PORTAL_SESSION_SECRET` still invalidates every
  cookie, if you ever need to.
- Sign-in attempts are rate limited per client IP: after `SIGNIN_RATE_LIMIT`
  (10) failed API key sign-ins, or sign-in starts and failed OIDC callbacks,
  within `SIGNIN_RATE_WINDOW` (600) seconds, the web UI answers `429 Too Many
  Requests` with a `Retry-After` header and logs an `auth.rate_limited` event.
  Set both in `.env` to tune them.

## Restrict the web UI

Tailscale clients only need the control plane (the domain root) and, with
OIDC, the sign-in pages. The admin console does not need to be public.

With `SSL_MODE=front`, restrict `/admin` in your proxy. For nginx:

```nginx
location /admin {
    allow 192.168.0.0/16;   # your LAN
    allow 100.64.0.0/10;    # your tailnet
    deny all;
    proxy_pass http://<headscale-easy-host>:80;
    # ...the rest of the generated location block
}
```

In Nginx Proxy Manager use an *Access List* on a custom location `/admin`; in
Traefik an `ipAllowList` middleware on a router for `PathPrefix(/admin)`.

!!! warning "Do not restrict `/authentik` or `/oidc`"
    New devices sign in through them from a browser **before** they are on
    the tailnet. Restricting them breaks enrolment.

Headscale's own REST API (`/api/v1/…`) is served on the same domain. The web UI
talks to Headscale inside the Docker network, so if nothing outside the server
calls that API, you can block `/api/` in your front proxy as well.

When Caddy holds the certificate (`letsencrypt`, `selfsigned`, `http`), the
`Caddyfile` is regenerated by the installer. Put the restriction on a
firewall or proxy in front of it instead, or reach the console through the
tailnet only.

## The Docker socket

Access to the Docker socket is **equivalent to root on the host**, so the web
UI does not have it. Only the small `hs-helper` container does
(`helper/helper.py`, Python standard library, about 250 lines). It answers the
web UI over a Unix socket in the `hse-helper` volume and serves exactly:

| Request | What it does |
|---|---|
| `POST /configtest` | `headscale configtest` in the `headscale` container |
| `POST /restart` | restarts the `headscale` container and waits until it is healthy |
| `GET /status` | health of the stack's containers and `headscale version` |

There are no parameters: a query string or a request body is refused (400),
any other path is 404 and any other method 405, before Docker is contacted.
The container name is fixed in `docker-compose.yml`, never taken from a
request. `hs-helper` has **no network** (`network_mode: none`), a read-only
filesystem, no capabilities, `no-new-privileges`, and runs as the owner of the
project files plus the Docker socket's group. Its socket is `660`, so only
that uid/gid (the web UI) can use it.

A compromised web UI can therefore validate the config, restart Headscale and
read container health, nothing else. A compromised `hs-helper` is still root
on the host, which is why it is kept this small: read it.

If you do not need to change DNS or key expiry from the console, stop the
helper:

```bash
docker compose stop hs-helper
```

Everything else keeps working. The DNS page then explains why it is
read-only, saving the device key expiry shows an error, and you change both
with `./install.sh`. (A later `docker compose up -d` starts it again.)

Installations whose `docker-compose.yml` predates the helper still mount the
socket in the `web` service; the web UI keeps using it, with a warning in its
logs, until you update the project files and run `docker compose up -d`.

## Secrets

- `.env` holds every secret (API key, session secret, OIDC and Authentik
  secrets, SMTP password). It is created `chmod 600`; keep it that way and
  never commit it.
- The web UI's renewed Headscale API key is in `data/web/api-key` (`600`).
- Rotate after a suspected leak: create a new Headscale API key
  (`./install.sh` does it), a new `PORTAL_SESSION_SECRET`, and a new OIDC
  client secret in your provider.
- Do not paste `.env` or backups into issues; `make config` prints `.env` with
  the secrets hidden.

## Backups

Backups contain `.env` and Headscale's private keys: whoever has one can
impersonate your server.

- Turn on daily backups (`./install.sh`, or `BACKUP_ENABLED=true`) and copy
  them **off the server** (NAS mount in `BACKUP_DIR`, `rsync`, `restic`…).
- Encrypt copies that leave your network, for example:
  `gpg --symmetric --cipher-algo AES256 backups/headscale-easy-*.tar.gz`.
- Test a restore once, ideally on another machine
  (see [Operations → Backups](operations.md#backups)).

## Updates

```bash
git pull && make update
```

`make update` pulls new images (Headscale, Caddy, Authentik, the web UI) and
recreates the containers; data is kept. Watch the project's
[releases](https://github.com/insanerask77/headscale-easy/releases) and those
of [Headscale](https://github.com/juanfont/headscale/releases) and
[Authentik](https://github.com/goauthentik/authentik/releases) for security
fixes. To control when upgrades happen, pin versions in `.env`
(`HSE_VERSION`, `HEADSCALE_IMAGE_TAG`, `AUTHENTIK_IMAGE_TAG`, `CADDY_IMAGE_TAG`).

## Logs

- **Caddy access log** (`data/caddy-logs/`): every request with client IPs.
- **Activity log** (Logs page, `data/web/audit.db`, `600`): who changed what,
  sign-ins, failed sign-ins, rate-limit blocks and revoked sessions with IPs, kept `AUDIT_RETENTION_DAYS` (90) days.
  Secrets are never written to it: keys are reduced to a prefix and invitation
  or reset links are not stored.
- **Container logs** (`make logs`): no secrets by design, but they do contain
  user names, emails and IPs.

These are personal data in many jurisdictions: keep them only as long as you
need, and include them in your privacy notice if others use your server.

## Network exposure summary

| Exposed | Why | Can you close it? |
|---|---|---|
| TCP 443 `/` | Control plane for Tailscale clients | No |
| TCP 443 `/authentik`, `/oidc` | Device and user sign-in | No, if you use OIDC |
| TCP 443 `/admin` | Web console | Yes — restrict to LAN / tailnet |
| TCP 443 `/api/v1` | Headscale REST API | Yes, if nothing remote uses it |
| TCP 80 | Let's Encrypt and redirect to HTTPS | With `front`, your proxy handles it |
| UDP 3478 | STUN for the embedded DERP relay | Only if you use Tailscale's DERP servers instead |

See [Security](security.md) for the full security model and known limitations.
