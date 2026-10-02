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
   their own devices).
6. **Backups**: schedule and retention are stored for the built-in backups
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
| `HSE_DERP_PORT` | `3478` | STUN port of the embedded DERP relay |
| `TAILNET_NAME` | `myorg` | Base of the MagicDNS name (`<name>.headscale.net`) |
| `NETWORK_ISOLATION` | `true` | Each user only reaches their own devices |
| `NODE_KEY_EXPIRY` | `180d` | Device key lifetime |
| `DERP_USE_PUBLIC` | `true` | Also use Tailscale's public DERP map |
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
