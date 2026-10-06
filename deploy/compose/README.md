# Headscale Easy: the compose file

The reference way to run the all-in-one image with Docker Compose. One container
runs Headscale, Caddy (HTTPS) and the web console; everything you add around it
(an identity provider, PostgreSQL, a proxy in front) lives in
[`../examples/`](../examples/).

```bash
cp .env.example .env          # set HSE_PUBLIC_URL (and HSE_TLS, ACME_EMAIL)
chmod 600 .env
docker compose up -d
docker compose logs -f        # the setup wizard prints a one-time token here
```

Open `<HSE_PUBLIC_URL>/admin/setup`, enter the token and follow the steps. Set
`HSE_ADMIN_EMAIL` and `HSE_ADMIN_PASSWORD` in `.env` to skip the wizard and start
with that account. The installer (`install.sh` at the root) does exactly
this and asks the few questions for you.

## What it runs

| Service | When | What |
|---|---|---|
| `headscale-easy` | always | the all-in-one image |
| `backup-remote` | `--profile backup-remote` | uploads every new backup to S3, B2, SFTP (rclone) or a server (rsync over SSH) |

| Port | Why |
|---|---|
| 80/tcp | the console and Headscale; Let's Encrypt's challenge |
| 443/tcp, 443/udp | HTTPS |
| 3478/udp | DERP/STUN, the clients' fallback relay. It is not HTTP: a proxy in front cannot carry it, publish it straight to this container |

Host ports 80 and 443 can be changed with `HSE_HTTP_PORT` and `HSE_HTTPS_PORT`
(keep the port in `HSE_PUBLIC_URL` in step). 3478 is fixed: it is the port the
clients are told to use.

## Data

Two named volumes: `hse-data` (Headscale's database and keys, the console's
accounts, the configuration, Caddy's certificates) and `hse-backups`. Losing the
first without a backup loses the tailnet: every device has to be added again.

To keep the backups in a folder instead (a NAS mount, another disk), replace
`hse-backups:/data/backups` with a bind mount and make the folder writable by the
image's user, which is uid 1000:

```bash
mkdir -p backups && chown 1000:1000 backups
```

```yaml
      - ./backups:/data/backups
```

A folder that Docker creates for you belongs to root: the server still starts, but
backups fail (the log says so and how to fix it).

## Backups

Nightly at 03:00, 14 days kept, with no extra container: see **Backups** in the
console and [`docs/all-in-one.md`](../../docs/all-in-one.md). A backup on the same
disk does not survive losing the server. To copy them elsewhere:

```bash
# .env
BACKUP_REMOTE=s3:my-bucket/headscale-easy       # or rsync:user@host:/srv/backups
# ./remote-config/rclone.conf   (rclone config), or ./remote-config/id_ed25519 (SSH key)

docker compose --profile backup-remote up -d
```

The sidecar sees only the backups, read-only, and uploads each one once (it asks
the remote what it already has, so a restart uploads nothing twice). Its retention
is `BACKUP_REMOTE_KEEP_DAYS`.

## Hardening

The image runs as an unprivileged user (uid 1000) and the compose file drops every
capability and sets `no-new-privileges`. Ports 80 and 443 are bound through the
`net.ipv4.ip_unprivileged_port_start` sysctl, so no capability is needed. There is
no Docker socket anywhere.

## Updating and removing

```bash
docker compose pull && docker compose up -d     # update (pin HSE_VERSION in production)
docker compose down                              # stop, keep the data
docker compose down -v                           # stop and DELETE the data and the backups
```
