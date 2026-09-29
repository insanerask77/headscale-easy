# Operations

## Users and admins

Headscale Easy has two roles:

- **Members** see and manage only their own machines and auth keys.
- **Admins** see every machine and user and manage DNS, the ACL policy and API keys.

With the built-in Authentik, admins are the members of `vpn-admins` (and
Authentik's own `authentik Admins`). Add people at `/add-user` or from **Users →
Add user**; the form lets you make them admins. A Headscale user is created
automatically the first time someone connects a device by signing in.

**Create local user** (Users page) creates a Headscale user without an account,
for servers that join with auth keys.

## Connecting devices

- **Laptops and phones**: install the official Tailscale app, choose *Use an
  alternate server* / *Change server* and enter your URL, then sign in.
- **Linux / servers**: `tailscale up --login-server=https://<your-domain>`, or
  with an auth key (Settings → Keys) for unattended machines:
  `tailscale up --login-server=https://<your-domain> --authkey=<key>`.
- **Auth ID**: if a device shows a URL with a registration ID, an admin can
  approve it with **Add device → Register with Auth ID**.

The **Add device** page in the console shows the exact steps per OS.

## Managing machines

The **Machines** page lists every device you can see (admins: all of them)
and updates itself every few seconds: new machines appear and their status
changes without reloading.
Search by name, owner, address, tag or version, and narrow the list with
**Filters** (status, owner, needs update, has routes, key expired). The
download button exports the current list as CSV.

Badges under each name tell you what is special about a machine:

| Badge | Meaning |
|---|---|
| Expiry disabled | Its key never expires |
| Expired | Its key expired: it must sign in again |
| Ephemeral | Removed automatically when it goes offline |
| Subnets / Exit Node | It advertises routes; orange means some are waiting for approval |
| `tag:…` | ACL tags |

The **⋯** menu (and the machine's page) lets you rename it, expire its key
(forces a new sign-in), disable key expiry, edit tags, approve **subnet routes
and exit nodes**, or remove it. Members can rename, expire and remove their own
machines; routes, tags and key expiry are admin-only, as in Tailscale.

Tailscale apps that cannot read the device name (iPhone, iPad, Apple TV and
the App Store build for Mac) register as `localhost`. Headscale Easy renames
them once to `<owner>-<device>`, e.g. `ana-iphone` or `leo-mac`; a name you
choose later is never changed. Set `AUTO_RENAME_LOCALHOST=false` in `.env` to
turn it off.

The arrow next to the version turns red when a newer Tailscale client is
available (hover it to see which).

## Everyday commands

`make` lists everything. The most useful:

```bash
make ps          # container status
make health      # health of every container
make logs        # follow logs (make logs service=headscale)
make nodes       # machines, from the Headscale CLI
make key user=alice   # reusable 24 h auth key
make apikey      # new Headscale API key
make config      # .env with secrets hidden
```

The Headscale CLI is always available: `docker exec headscale headscale --help`.

## Updating

```bash
git pull
./install.sh        # re-applies templates and the Authentik blueprint
# or, if nothing in the repository changed:
make update         # pull new images and recreate containers
```

Pin versions in `.env` with `HSE_VERSION`, `HEADSCALE_IMAGE_TAG` and
`AUTHENTIK_IMAGE_TAG`. Read the Headscale and Authentik release notes before
major upgrades.

## Backups

Daily backups are **optional**: the installer asks (off by default, since they
add a small container and disk use, but recommended). When enabled, the
`backup` container makes a backup every day at the time you chose (03:00 by
default) and keeps the last 14 days in `./backups`; the installer makes a first
one right away and shows the exact restore command. Each backup is one
`.tar.gz` with:

- Headscale's database (a consistent copy taken while it runs) and its private
  keys, so devices stay registered after a restore;
- Authentik's database, if you use it;
- the configuration (`.env`, `headscale-config.yaml`, `Caddyfile`...);
- Caddy's internal CA with `SSL_MODE=selfsigned`.

Turn them on or off, or change the time, folder and retention, by running
`./install.sh` again. Or edit `.env` and run `docker compose up -d`:

| Variable | Default | |
|---|---|---|
| `BACKUP_ENABLED` | `false` | Also add `backup` to `COMPOSE_PROFILES` |
| `BACKUP_SCHEDULE` | `0 3 * * *` | Cron syntax (time zone `TZ`); `off` disables it |
| `BACKUP_DIR` | `./backups` | Any path on the host, e.g. a NAS mount |
| `BACKUP_KEEP_DAYS` | `14` | Older backups are deleted |

Back up right now with `make backup` (works with scheduled backups off too). Backups contain secrets (`.env`): they
are readable only by you, keep copies somewhere safe and off this server.

### Restore

```bash
make restore file=backups/headscale-easy-20260929-030000.tar.gz
```

It stops the stack, puts back the configuration (the current files are kept as
`*.before-restore-*`), Headscale's database and keys, Authentik's database and
Caddy's CA, and starts the stack again.

**On a new server:** install Docker, clone the repository, copy the backup and
run the same command — no need to run the installer first.

## Uninstalling

```bash
./uninstall.sh           # remove containers, keep data and configuration
./uninstall.sh --purge   # also delete volumes, configuration and ./data
```

## Troubleshooting

**Headscale never becomes healthy (with OIDC).** It refuses to start until it
can read the issuer's discovery document from inside its container. Check:

```bash
docker compose logs headscale
docker exec caddy wget -qO- http://authentik-server:9000/authentik/application/o/headscale/.well-known/openid-configuration
```

If Authentik answers but Headscale cannot reach the public URL, your router
probably lacks NAT loopback: with `SSL_MODE=front` set `FRONT_PROXY_IP`.

**`redirect_uri` errors after changing the domain.** Re-run `./install.sh`: it
re-applies the Authentik blueprint with the new URLs (Authentik does not do it by
itself when only environment variables change).

**Clients say `x509: certificate signed by unknown authority`.** You are using
`SSL_MODE=selfsigned`: install `caddy-root-ca.crt` on the client, or switch to
Let's Encrypt.

**Devices connect but cannot reach each other.** Check the ACL policy (with
isolation, users only reach their own devices) and that UDP 3478 is open for
the DERP relay.

**The console says the API key expired.** The console renews its own
Headscale API key when it has 15 days left (it keeps the new one in
`data/web/api-key`), so this only happens if the server was off for that
whole window or someone expired the key by hand. Re-run `./install.sh`: it
creates a new one.

**Somebody signed in with Google but cannot use the VPN.** New Google accounts
have no group. Add them to `headscale-users` in Authentik.

Still stuck? [Open an issue](https://github.com/insanerask77/headscale-easy/issues/new/choose)
with the output of `make health` and the relevant logs (remove secrets).
