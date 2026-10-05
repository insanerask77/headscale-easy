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
8. **Backups**: when to back up (cron syntax, default every night at 03:00;
   `off` disables it) and how many days to keep (default 14). The wizard shows
   when the next backup will run. See [Backups](#backups).

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
| `UI_LANG`, `TZ` | `en`, `UTC` | Console language and time zone (`TZ` is also the clock of the backup schedule) |
| `BACKUP_SCHEDULE` | `0 3 * * *` | When to back up, cron syntax; `off` disables scheduled backups. An invalid value stops the container at start |
| `BACKUP_KEEP_DAYS` | `14` | Backups older than this are deleted (the newest successful one is always kept) |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | | Sign in with an external OIDC provider |
| `HEADSCALE_DB_TYPE`, `HEADSCALE_PG_*` | `sqlite` | Use an external PostgreSQL |

## What lives in `/data`

| Path | Contents |
|---|---|
| `headscale/` | Headscale database, keys and local socket |
| `caddy/` | Certificates and access logs |
| `console/` | Accounts, sessions and audit databases, Headscale API key |
| `config/` | `settings.json`, rendered `config.yaml`, `Caddyfile`, `derp.yaml` |
| `backups/` | The built-in backups and `status.json` (last and next run) |

Everything is created by the container with private permissions (700 / 600).
The container backs itself up, see [Backups](#backups); a copy of the volume
works too.

## Backups { #backups }

The container makes a backup **every night at 03:00** (time zone `TZ`) and keeps
the last 14 days in `/data/backups`, with no extra container. Turn it off with
`BACKUP_SCHEDULE=off`, change it with `BACKUP_SCHEDULE` / `BACKUP_KEEP_DAYS`
(in the wizard, or later in **Settings → Status → Backup settings**, which
turns scheduled backups on or off and changes the schedule and the days kept;
a value fixed by an environment variable can only be changed there). A backup also runs once shortly after the container starts
if its scheduled time passed while it was stopped.

Each backup is one `headscale-easy-<date>-<time>.tar.gz` with:

| Inside | What it is |
|---|---|
| `headscale/` | The database (a consistent copy taken while Headscale runs: `db.sqlite`, or `headscale.sql` from `pg_dump` on an external PostgreSQL) and its private keys, so devices stay registered after a restore |
| `config/` | `settings.json`, `config.yaml`, `Caddyfile`, `derp.yaml`, session secret: your DNS edits live in `config.yaml` |
| `console/` | Local accounts (password hashes and two-factor secrets), the Headscale API key, the two-factor mode |
| `web/` | The activity log |
| `caddy/pki/` | The internal CA, if you use `HSE_TLS=internal` |
| `meta.json` | Format, versions and a SHA-256 of every file, checked before a restore |

Active sessions are **not** saved: after a restore everyone signs in again
(a restored session would revive logins that were revoked after the backup).
Every backup is read back and verified before it counts, and old ones are
deleted by age, never the newest successful one.

!!! warning "A backup is a set of secrets"
    It holds password hashes, two-factor secrets, the OIDC client secret and
    Headscale's private keys. The archive and `/data/backups` are private
    (600 / 700). Copy them somewhere safe and off this server, and treat the
    copy like the server. The console can show the state of the backups but
    does not offer a download on purpose.

### Backing up and restoring

```bash
docker exec headscale-easy hse backup          # a backup right now
docker exec headscale-easy hse backups         # list them: size, age, result
docker exec headscale-easy hse restore /data/backups/headscale-easy-20261005-030000.tar.gz
```

**Settings → Status** shows the last backup (time, size, result), the next run
and a **Back up now** button (administrators). A failed scheduled backup also
sends a notification if you configured one.

Restore, in order of preference:

1. **Stopped container (recommended).** Also how you restore on a *new* host:
   use a new, empty volume and start the container normally afterwards. It starts
   in run mode (no wizard) with the same users, machines, accounts and DNS.

    ```bash
    docker stop headscale-easy
    docker run --rm -v hse:/data --entrypoint hse \
      ghcr.io/insanerask77/headscale-easy-aio restore /data/backups/<file>.tar.gz
    docker start headscale-easy
    ```

2. **Running container**: `docker exec headscale-easy hse restore <file>` stops
   the three processes, restores, and starts them again. It is refused while
   setup is not finished (use the stopped-container way).

Both ways check the archive first (format, SHA-256 of every file, database
integrity) and change nothing if it is not valid. Before replacing anything they
make a `…-pre-restore-…` backup of the current data, and put it back if the
restore fails half way. An archive from a 1.x install is refused: see the
migration notes (phase 5). The reverse is also true: `scripts/restore.sh`
(the 1.x tool) refuses an archive from this image and points to `hse restore`.

### Where the backups go

`/data/backups` is on the same volume as the data, so it does not survive losing
the disk. Mount somewhere else (for example a NAS folder) over it:

```bash
docker run … -v hse:/data -v /mnt/nas/hse-backups:/data/backups …
```

The folder must be writable by uid 1000. For copies to S3, B2, SFTP or another
server, use the `backup` image as a **sync sidecar** in the advanced edition:
with `BACKUP_MODE=sync` it uploads each new archive it finds in `/backups`
(mount the same folder, read-only) every `BACKUP_SYNC_INTERVAL` seconds and
applies the remote retention. rclone and rsync are not bundled into the
all-in-one image. See [Operations → Remote backups](operations.md#remote-backups)
for the destination settings.

### Schedule syntax

Five fields, `minute hour day-of-month month day-of-week`, with `*`, lists
(`1,15`), ranges (`1-5`) and steps (`*/6`, `0-20/5`); day of week is 0-7 (0 and 7
are Sunday). When both day-of-month and day-of-week are set, either may match,
as in cron. `off` (or empty) disables the schedule. For example `30 2 * * 1-5`
is 02:30 on weekdays.

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
docker exec headscale-easy hse backup   # back up now (see Backups)
docker logs -f headscale-easy           # [supervisor] [headscale] [caddy] [console]
```

A small supervisor runs the three processes, restarts a crashed one with
exponential backoff (1 s up to 30 s) and stops them in order on `docker stop`.
Changing DNS in the console validates the config and restarts Headscale
through it. To update, pull the new image and recreate the container: the data
is in the volume.

Measured on the CI runner: the image is about 55 MB and the idle container
uses about 65 MB of RAM, also while a backup runs. CI fails above 250 MB and 100 MB.

## Limits of the preview

- No bundled Authentik: use local accounts (with two-factor) or an external
  OIDC provider.
- Backups stay on the volume: for remote copies use the sync sidecar (see [Backups](#backups)).
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

!!! warning "Use an address the container can reach"
    If the server's public URL is `localhost` (or `127.0.0.1`), a container cannot
    reach it: inside a container `localhost` is the container itself. The tab warns
    you. Use a real name or IP, or on Linux add `--network host` (and drop
    `--hostname` and `--sysctl`, which Docker does not allow with it).

## Live device status

The Machines page (and a device's page) updates on its own: a device that
connects, disconnects, is added, removed or renamed shows up within a few
seconds, without reloading. The console keeps a Server-Sent Events stream open
(`/admin/events`); members only receive events about their own devices. A small
**Live** indicator shows the connection; if it drops the page falls back to
refreshing every few seconds. When a reverse proxy sits in front of the
container, make sure it does not buffer `text/event-stream` responses (the
bundled Caddy does not).
