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
- [ ] A real domain and **`HSE_TLS=auto`** (Let's Encrypt), or `off` behind a proxy
      you already trust. No plain `http://` URL outside a LAN.
- [ ] Host firewall: only 22 (from your IP), 80, 443 and UDP 3478 open.
- [ ] **Two-factor authentication required for admins** (`MFA_REQUIRED=admins`
      or `everyone`), and a long password for every administrator.
- [ ] **Self-registration off** (`HSE_SIGNUP=off`, the default) or restricted to
      invitation keys, unless you really want anyone to be able to sign up.
- [ ] `NETWORK_ISOLATION=true` unless you have written your own ACL policy.
- [ ] The console reachable only from where you administer it (LAN, VPN or
      tailnet), if you can — see [below](#restrict-the-console).
- [ ] The image pinned to a version (`HSE_VERSION`), and nothing mounting the Docker
      socket (the container does not need it).
- [ ] Nightly backups **copied off the server**, and a restore tested once.
- [ ] Someone watching [releases](https://github.com/insanerask77/headscale-easy/releases)
      and updating.

## Firewall

The container publishes only TCP 80 and 443 and UDP 3478. On the host, allow just
what is needed, for example with `ufw`:

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
    gRPC, the internal `8080`) on a public host: they are not meant to leave the
    container.

## HTTPS

- **`HSE_TLS=auto`** (recommended): Caddy gets and renews the certificate and
  redirects HTTP to HTTPS. Needs ports 80 and 443 reachable and a DNS record
  pointing at the host.
- **`off` behind a proxy**: your own proxy terminates TLS and forwards to the
  container on port 80. Then the connection between your proxy and this host is
  plain HTTP: keep it on a private network or on the same machine, and make sure
  the proxy passes WebSockets and does not buffer (the
  [examples](advanced/proxy.md) do both). Set `HSE_TRUSTED_PROXIES` to
  the proxy's address only, never to a whole network.
- **`internal`**: only for tests or closed networks; every client must trust
  Caddy's root certificate.
- **Plain `http://`**: LAN only. Cookies are then not `Secure` and passwords
  travel in clear text. The first-run wizard is served this way: run it from a
  trusted network, or start headless with `HSE_PUBLIC_URL`.

## Sign-in

- **Local accounts** (default): passwords are stored as salted hashes, failed
  sign-ins are rate limited, and admins can be forced to use two-factor. Set the
  administrator's password with a long passphrase, and give admin rights only to
  the people who need them (a *Network admin* or *Auditor* role is often enough).
- **Sign-up:** `HSE_SIGNUP=off` hides the link and the endpoint answers 404. With
  `invite` a key is needed (shown once, stored hashed, single or multi use, with an
  expiry); `open` lets anyone create a **member** account, never an admin.
- **An external OIDC provider:** make sure it verifies e-mail addresses if you use
  `PORTAL_ADMIN_EMAILS` (an e-mail it marks as not verified never grants admin), and
  prefer groups (`PORTAL_ADMIN_GROUPS`) if it can send a `groups` claim. Restrict who
  may sign in with `HSE_OIDC_ALLOWED_*`: empty means **everyone the provider lets
  in**, which is wrong for Google. Enforce two-factor there.
- **API key sign-in** is an emergency way in for administrators. It is on only while
  no OIDC provider is configured; with OIDC, prefer to keep it off.
- Sessions last 8 hours and are kept server-side (`/data/console/sessions.db`,
  `600`), so they can be revoked at once: **Settings → Sessions** lists them
  (yours; admins and auditors see everybody's) with **Log out** per session,
  **Sign out everywhere** and, for admins, **Sign out everyone else**. Deleting
  a user, or a change of someone's role at their next sign-in, revokes their
  older sessions. Changing `SESSION_SECRET` invalidates every cookie, if you ever
  need to.
- Sign-in attempts are rate limited per client IP: after 10 failed sign-ins within
  600 seconds the console answers `429 Too Many Requests` with a `Retry-After`
  header and logs an `auth.rate_limited` event. Behind a proxy, set
  `HSE_TRUSTED_PROXIES` or every person is counted as the proxy's address.

## Restrict the console

Tailscale clients only need the control plane (the domain root) and, with an
external provider, the sign-in pages. The admin console does not need to be public.

The container's own Caddy is generated from your settings, so put the restriction
in a proxy or firewall **in front** of it (see [A proxy in
front](advanced/proxy.md)), or reach the console through the tailnet
only. For nginx:

```nginx
location /admin {
    allow 192.168.0.0/16;   # your LAN
    allow 100.64.0.0/10;    # your tailnet
    deny all;
    proxy_pass http://<headscale-easy-host>:80;
    # ...the rest of the location block from the example
}
```

In Nginx Proxy Manager use an *Access List* on a custom location `/admin`; in
Traefik an `ipAllowList` middleware on a router for `PathPrefix(/admin)`.

!!! warning "Do not restrict `/oidc`, `/authentik` or `/admin/register`"
    New devices sign in through them from a browser **before** they are on
    the tailnet. Restricting them breaks enrolment. (`/admin/register/<id>` is the
    page a new device opens to be approved.)

Headscale's own REST API (`/api/v1/…`) is served on the same domain. The console
talks to Headscale inside the container, so if nothing outside the server calls
that API, you can block `/api/` in your front proxy as well.

## No Docker socket

Access to the Docker socket is **equivalent to root on the host**, so Headscale
Easy has none: not the console, not the compose file.
Validating the config and restarting Headscale are done by a small supervisor
**inside the container**, which answers a fixed set of requests from the console on
a Unix socket: validate the config (`headscale configtest`), restart Headscale, and
report the health of the three processes. The console cannot ask it to run anything
else.

The container runs as an unprivileged user (uid 1000) with **no added Linux
capability** and `no-new-privileges`, and the compose file drops all of them. Keep it
that way, and do not mount the Docker socket into it. The three processes
(Headscale, Caddy and the console) share the container, so a flaw in one reaches the
others: that is why the data is private to uid 1000 (`700` / `600`) and the container
should sit behind your firewall, not next to workloads you do not trust.

## Secrets

- `/data/config/settings.json` holds your settings, including the OIDC client secret
  and the SMTP password if you set them; `/data/config/session-secret` signs the
  sessions; `/data/console/api-key` is the console's renewed Headscale API key;
  `/data/console/accounts.db` has the password hashes and two-factor secrets. All are
  created `600` inside a `700` directory; keep them that way. The `.env` next to the
  compose file (`600`) holds what you passed as variables: never commit it.
- Rotate after a suspected leak: a new `SESSION_SECRET` (or delete
  `/data/config/session-secret` and restart), a new Headscale API key (delete
  `/data/console/api-key` and restart), and a new OIDC client secret in your provider.
- Do not paste `.env`, `settings.json` or backups into issues.

## Backups

A backup holds password hashes, two-factor secrets, the OIDC client secret and
Headscale's private keys: whoever has one can impersonate your server.

- Copy the nightly backups **off the server**: mount a NAS folder over
  `/data/backups`, or use the `backup-remote` sidecar (S3, B2, SFTP, rsync). See
  [Operations → Remote backups](operations.md#remote-backups).
- Encrypt copies that leave your network, for example:
  `gpg --symmetric --cipher-algo AES256 headscale-easy-*.tar.gz`.
- Test a restore once, ideally on another machine
  (see [All-in-one → Backing up and restoring](all-in-one.md#backing-up-and-restoring)).

## Updates

```bash
docker compose pull && docker compose up -d   # after changing HSE_VERSION
```

Pull the new image and recreate the container; the data is in the volume. Watch the
project's [releases](https://github.com/insanerask77/headscale-easy/releases) and
those of [Headscale](https://github.com/juanfont/headscale/releases) for security
fixes. To control when upgrades happen, pin the version (`HSE_VERSION=2.0.0`, or an
exact image tag). Take a backup first (`docker exec headscale-easy hse backup`).

## Logs

- **Caddy access log** (`/data/caddy/logs/`): every request with client IPs.
- **Activity log** (Logs page, `/data/console/audit.db`, `600`): who changed what,
  sign-ins, failed sign-ins, rate-limit blocks and revoked sessions with IPs, kept 90
  days. Secrets are never written to it: keys are reduced to a prefix and invitation
  or reset links are not stored.
- **Container logs** (`docker logs headscale-easy`): no secrets by design, but they
  do contain user names, e-mails and IPs. The one-time setup token is printed there
  until setup finishes.

These are personal data in many jurisdictions: keep them only as long as you
need, and include them in your privacy notice if others use your server.

## Network exposure summary

| Exposed | Why | Can you close it? |
|---|---|---|
| TCP 443 `/` | Control plane for Tailscale clients | No |
| TCP 443 `/oidc`, `/authentik` | Device and user sign-in with an external provider | No, if you use one |
| TCP 443 `/admin/register` | A new device opens it to be approved | No |
| TCP 443 `/admin` | Web console | Yes — restrict to LAN / tailnet in a proxy in front |
| TCP 443 `/api/v1` | Headscale REST API | Yes, if nothing remote uses it |
| TCP 80 | Let's Encrypt and redirect to HTTPS | With a proxy in front, your proxy handles it |
| UDP 3478 | STUN for the embedded DERP relay | Only if you use Tailscale's DERP servers instead |

See [Security](security.md) for the full security model and known limitations.
