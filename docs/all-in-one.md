# All-in-one image (preview)

One container with Headscale, Caddy and the web console, set up from your
browser. No Docker socket, no installer, no Authentik. It is a **preview** of
the 2.0 edition: the 1.x installer and the split compose keep working as they
are.

```bash
docker run -d --name headscale-easy \
  -p 80:80 -p 443:443 -p 3478:3478/udp \
  -v hse:/data \
  ghcr.io/insanerask77/headscale-easy-aio
```

Then read the one-time setup token from the logs and open the wizard:

```bash
docker logs headscale-easy
```

Open `http://<your-server>/admin/setup`, enter the token and follow the steps.

!!! note "Image name"
    During 1.x the image is called `headscale-easy-aio` (`headscale-easy` is
    still the console image used by the split compose). In 2.0 it takes over
    the name `headscale-easy`.

## The first-run wizard

Setup mode starts when there is no `/data/config/settings.json` and no
`HSE_PUBLIC_URL`. Only the wizard is served; every other URL redirects to it.

1. **Token**: the one printed in the logs (also in `/data/config/setup-token`).
   Without it nobody can reach any step, so the first visitor cannot take over
   your server. Wrong tokens are rate limited.
2. **Language**.
3. **Public URL and HTTPS**: automatic (Let's Encrypt, needs a domain and an
   email), internal (self-signed) or none (plain HTTP, or HTTPS handled by a
   proxy in front).
4. **Administrator**: email, username and password. Two-factor is optional
   here: the console suggests turning it on (a popup after you sign in, once
   per browser session) and you enable it in **Settings → Account**.
5. **Tailnet name** and whether users are isolated (each user only reaches
   their own devices). The isolation policy also allows
   `autogroup:internet`, so users can route their traffic through exit nodes.
   You can also set the MagicDNS base domain (default `hse.net`).
6. **Sign-up**: who can create an account from the sign-in page: nobody (default),
   people with an invitation key, or anyone. With keys, setup can create the
   first one and shows it once when it ends.
7. **Relays (DERP)**: *this server* (default: DERP and STUN run in the
   container, UDP 3478, nothing third-party), Tailscale's public relays too,
   or your own DERP map.
8. **Backups**: schedule and retention are stored for the built-in backups
   that arrive in a later release.

Finishing creates the Headscale API key, the administrator's Headscale user and
the isolation policy, then starts the console. If a step fails (for example the
config is rejected) you see the error and can retry; nothing is duplicated.

!!! warning "Setup runs over plain HTTP"
    The wizard is served on port 80 before any certificate exists, so the
    administrator password travels unencrypted. Run setup from a trusted
    network, or use the headless start below and put the server behind HTTPS
    first.

## Headless start (no wizard)

With `HSE_PUBLIC_URL` set there is no wizard: the container starts straight in
run mode.

```bash
docker run -d --name headscale-easy \
  -p 80:80 -p 443:443 -p 3478:3478/udp -v hse:/data \
  -e HSE_PUBLIC_URL=https://hs.example.com \
  -e ACME_EMAIL=you@example.com \
  -e HSE_ADMIN_EMAIL=admin@example.com \
  -e HSE_ADMIN_PASSWORD='choose-a-long-one' \
  ghcr.io/insanerask77/headscale-easy-aio
```

Precedence is **environment > `/data/config/settings.json` > defaults**.

| Variable | Default | Meaning |
|---|---|---|
| `HSE_PUBLIC_URL` | *(none: wizard)* | Public `http(s)://host[:port]` of the server |
| `HSE_TLS` | `auto` for https, `off` for http | `auto` (Let's Encrypt), `internal` (self-signed) or `off` |
| `ACME_EMAIL` | | Required with `HSE_TLS=auto` |
| `HSE_ADMIN_EMAIL`, `HSE_ADMIN_PASSWORD` | | First administrator, created on first start if no account exists |
| `HSE_DERP_PORT` | `3478` | STUN port of the embedded DERP relay (publish it as `-p 3478:3478/udp`) |
| `TAILNET_NAME` | `myorg` | Label of the tailnet |
| `HSE_BASE_DOMAIN` | `hse.net` | MagicDNS base domain: devices are `<device>.<base domain>`. Must differ from the server's own domain. Set at first run; later edit it on the DNS page |
| `NETWORK_ISOLATION` | `true` | Each user only reaches their own devices |
| `NODE_KEY_EXPIRY` | `180d` | Device key lifetime |
| `HSE_SIGNUP` | `off` | Self-registration: `off`, `invite` (needs an invitation key) or `open`. Admins change it later in **Settings → General** |
| `HSE_DERP_MODE` | `embedded` | `embedded` (this container's own DERP + STUN, nothing third-party), `public` (also Tailscale's public map) or `custom` |
| `HSE_DERP_URL` | | DERP map URL, with `HSE_DERP_MODE=custom` (you can also upload a map in the console) |
| `DERP_USE_PUBLIC` | | Legacy alias: `true` = `public`, `false` = `embedded`; `HSE_DERP_MODE` wins |
| `UI_LANG`, `TZ` | `en`, `UTC` | Console language and time zone |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | | Sign in with an external OIDC provider |
| `HEADSCALE_DB_TYPE`, `HEADSCALE_PG_*` | `sqlite` | Use an external PostgreSQL |

## What lives in `/data`

| Path | Contents |
|---|---|
| `headscale/` | Headscale database, keys and local socket |
| `caddy/` | Certificates and access logs |
| `console/` | Accounts, sessions and audit databases, Headscale API key |
| `config/` | `settings.json`, rendered `config.yaml`, `Caddyfile`, `derp.yaml` |
| `backups/` | Reserved for the built-in backups |

Everything is created by the container with private permissions (700 / 600).
Back up the volume to back up the server.

## Security and requirements

- Runs as an unprivileged user (uid 1000) with no added capabilities. Headscale
  does not need `NET_ADMIN`.
- Ports 80 and 443 bind without privileges on Docker 20.10 or later. On other
  runtimes add `--sysctl net.ipv4.ip_unprivileged_port_start=0`.
- There is no Docker socket anywhere: the console talks to a small supervisor
  inside the container.
- The UDP port 3478 must be reachable from the internet (STUN).

## Operating it

```bash
docker exec headscale-easy hse health   # healthy when all processes run
docker exec headscale-easy hse reload   # re-render config, restart Caddy and Headscale
docker logs -f headscale-easy           # [supervisor] [headscale] [caddy] [console]
```

A small supervisor runs the three processes, restarts a crashed one with
exponential backoff (1 s up to 30 s) and stops them in order on `docker stop`.
Changing DNS in the console validates the config and restarts Headscale
through it. To update, pull the new image and recreate the container: the data
is in the volume.

Measured on the CI runner: the image is about 55 MB and the idle container
uses about 65 MB of RAM. CI fails above 250 MB and 100 MB.

## Limits of the preview

- No bundled Authentik: use local accounts (with two-factor) or an external
  OIDC provider.
- Scheduled backups are not built in yet; copy the `/data` volume for now.
- Migrating an existing 1.x install is not automated yet.

## Users and sign-up

- **Users → Create local user**: with only a name it is a Headscale user without
  sign-in (for servers). Add an email, a password and a role and it is an
  account the person can sign in with. By default they must choose another
  password at their first sign-in (a temporary one).
- **Users → ⋯ → Set password**: for people with an account. It signs out their
  open sessions.
- **Sign-up** (Settings → General): `off` hides the link and `/admin/signup`
  answers 404. With `invite` the form asks for an invitation key; `open` needs
  none. Self-registered people are always **Members**, never admins.
- **Invitation keys** (Users page): single or multi use, optional expiry, and
  revocable. A key is shown once when created; only its hash is stored. A wrong,
  expired, used-up or revoked key gets the same error. Sign-up attempts are
  rate limited like sign-in.
- No mail is sent and the email address is not verified.

## Add device → Docker

**Add device** has a **Docker** tab: a small form (host name, exit node, subnet
routes, userspace networking, DNS) and two snippets you can copy, a `docker run`
command and a `docker-compose.yml`, both using the official `tailscale/tailscale`
image pointed at this server. **Generate a single-use auth key** creates a key
(1 or 7 days) for you, or for the owner an admin picks, and puts it in the
snippets; it is shown once and never stored. Without a key, leave `TS_AUTHKEY`
out and read the sign-in link with `docker logs`. Exit-node and subnet-route
options add the forwarding settings they need; the routes still have to be
approved in the console. Auditors cannot generate keys.
