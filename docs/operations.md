# Operations

## Users and admins

Headscale Easy has four roles:

- **Members** see and manage only their own machines and auth keys.
- **Admins** see every machine and user and manage DNS, the ACL policy and API keys.
- **Network admins** (optional) edit the ACL policy and DNS only.
- **Auditors** (optional) see everything an admin sees, read only.

See [Roles](configuration.md#roles) to set up the last two, built on their own
Authentik group(s).

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
**Filters** (status, owner, needs update, has routes, key expired, expiring
soon, offline for 30+ days). The download button exports the current list as
CSV.

Badges under each name tell you what is special about a machine:

| Badge | Meaning |
|---|---|
| Expiry disabled | Its key never expires |
| Expired | Its key expired: it must sign in again |
| Expires soon | Its key expires within 14 days: sign in again on it (or disable key expiry) to keep it connected |
| Ephemeral | Removed automatically when it goes offline |
| Subnets / Exit Node | It advertises routes; orange means some are waiting for approval |
| `tag:…` | ACL tags |

The **⋯** menu (and the machine's page) lets you rename it, expire its key
(forces a new sign-in), disable key expiry, edit tags, approve **subnet routes
and exit nodes**, or remove it. Members can rename, expire and remove their own
machines; routes, tags and key expiry are admin-only, as in Tailscale.

**Bulk actions** (admins): tick the checkbox on several rows (or the one in
the header, to select every visible machine) and a bar appears to **expire
keys**, **add a tag** or **remove** all of them at once — useful when
decommissioning a batch of devices or tagging a group of them after the fact.

**Expiring and inactive machines.** When some of the machines you can see
expire in the next 14 days or already expired, a notice at the top of the list
says how many, with a link that filters them (members see it for their own
machines). A machine is *inactive* when it has been offline for more than 30
days (counted from its registration if it never connected); admins get
**Remove inactive machines…**, which lists them all ticked so you can untick
the ones to keep. A machine that came back online in the meantime is never
removed. Change the windows with `EXPIRY_WARNING_DAYS` and `INACTIVE_DAYS` in
`.env` (then `docker compose up -d`).

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

## Activity log

**Logs** (admins only, in the sidebar) is the tailnet's activity log, like the
configuration audit log of Tailscale's admin console. It records:

- **Configuration**: every change made from the web UI — machines renamed,
  removed, expired, routes, tags and key expiry changed, machines registered
  with an Auth ID; users created, renamed and deleted; auth keys and API keys
  created, revoked or expired; the access control policy (with a diff of the
  change), DNS (each setting before and after), the device key expiry and the
  two-factor mode. Automatic renames of machines called `localhost` show up
  as *Headscale Easy (automatic)*.
- **Sign-in**: console sign-ins and sign-outs, and failed API key sign-ins.
- **Devices**: every 30 seconds the web UI compares Headscale's state and logs
  devices that register, are removed, connect or disconnect, whose key
  expires, whose Tailscale version changes, or that are renamed outside the web
  UI (for example with `headscale nodes rename`).

Each event has the time, the actor (the user name, `API key <prefix>` for API
key sessions, or *Headscale* for device events), the client IP, the target and
the details. Secrets are never stored: auth keys, API keys and Auth IDs are
reduced to their prefix.

Search, filter by category, actor and dates (UTC), and download the matching
events with the CSV button. The first page updates on its own.

The log lives in `./data/web/audit.db` (SQLite). Events older than
`AUDIT_RETENTION_DAYS` in `.env` (default `90`; `0` keeps them forever) are
deleted automatically. Changes made outside the web UI (the `headscale` CLI,
the API) are not configuration events, but their effect on devices is logged.

!!! note
    Headscale has no network flow logs (which device talked to which, and
    when): that needs data from the clients that only Tailscale's own
    coordination server collects.

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

**Signing in to the console fails (OIDC).** "The sign-in expired or is not
valid" means the browser came back without the cookie set when sign-in
started: check that you open the console with the exact `PUBLIC_URL` (same
host and scheme — `http` vs `https` matters) and that the browser accepts
cookies. Other errors are in `docker compose logs web`. With your own
provider, the redirect URI `https://<domain>/admin/callback` must be
registered, and admins need a verified email in `PORTAL_ADMIN_EMAILS` or a
group in `PORTAL_ADMIN_GROUPS`.

**Let's Encrypt does not issue the certificate.** Ports 80 and 443 must be
reachable from the Internet and the domain must point at this host (check
with `dig +short <domain>` from outside). `docker compose logs caddy` shows the
ACME error. Too many failed attempts trigger Let's Encrypt's rate limits: fix
the cause and wait an hour.

**Behind my own proxy, devices do not connect or stay "offline".** The proxy
must pass WebSockets and upgrade headers, must not buffer responses, and must
forward to Caddy on port 80 with the original `Host`. Use the snippet the
installer generated for your proxy (`NGINX-PROXY-MANAGER.md`,
`nginx-<domain>.conf` or `traefik-<domain>.yml`), and set
`FRONT_PROXY_IP` if containers cannot reach the public URL.

**A device stays "waiting for approval" or shows a registration URL.** With
OIDC the person must finish sign-in in the browser it opened. Without OIDC,
register it from **Machines → Add device → Register with Auth ID** with the
ID in that URL, or
use an auth key. Check the owner: with isolation, a device registered to the
wrong user is invisible to its owner.

**DNS changes are rejected.** The console runs `headscale configtest` and
rolls back when Headscale refuses the change; the error shown is Headscale's.
The tailnet DNS name must differ from the server's domain. If the DNS page is
read-only, it says why (no Docker socket, or `config.yaml` without the managed
block — run `./install.sh` once).

**The ACL policy blocks traffic you expect.** Use **Check** in the policy
editor before saving, and remember that with `NETWORK_ISOLATION=true` each
user (admins included) only reaches their own devices unless the policy says
otherwise. Tagged devices belong to the tag, not to a user.

**Somebody signed in with Google but cannot use the VPN.** New Google accounts
have no group. Add them to `headscale-users` in Authentik.

Still stuck? [Open an issue](https://github.com/insanerask77/headscale-easy/issues/new/choose)
with the output of `make health` and the relevant logs (remove secrets).
