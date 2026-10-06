# Operations

## Users and admins

Headscale Easy has four roles:

- **Members** see and manage only their own machines and auth keys.
- **Admins** see every machine and user and manage DNS, the ACL policy and API keys.
- **Network admins** edit the ACL policy and DNS only.
- **Auditors** see everything an admin sees, read only.

With local accounts (the default) the role is set per account on the **Users**
page; with an external provider it comes from its groups. See
[Configuration → Roles](configuration.md#roles).

- **Invite people:** **Users → Invite user** makes a single-use link; they choose
  their own user name and password. Or let them sign up from the sign-in page
  (`HSE_SIGNUP`: off, with an invitation key, or open).
- **Create user** (Users page) with only a name creates a Headscale user without
  sign-in, for servers that join with auth keys. Add an e-mail, a password and a
  role and it is an account the person can sign in with.
- **Forgot a password?** **⋯ → Password reset link…** makes a single-use link, or
  **Set password** sets a temporary one. Both sign the person out everywhere.
- A Headscale user is created automatically the first time someone connects a
  device by signing in.

## Connecting devices

- **Laptops and phones**: install the official Tailscale app, choose *Use an
  alternate server* / *Change server* and enter your URL, then sign in.
- **Linux / servers**: `tailscale up --login-server=https://<your-domain>`, or
  with an auth key (Settings → Keys) for unattended machines:
  `tailscale up --login-server=https://<your-domain> --authkey=<key>`.
- **Containers**: **Add device → Docker** generates a `docker run` command and a
  `docker-compose.yml` for the official `tailscale/tailscale` image, with an
  optional single-use key.
- **The sign-in link**: the link `tailscale up` prints
  (`https://<your-domain>/register/…`) opens the console, which asks you to sign
  in and then to approve the device. Members register devices to themselves;
  admins choose the owner.
- **Auth ID**: if a device shows a URL with a registration ID, an admin can
  also approve it with **Add device → Register with Auth ID**.

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
removed. 
Tailscale apps that cannot read the device name (iPhone, iPad, Apple TV and
the App Store build for Mac) register as `localhost`. Headscale Easy renames
them once to `<owner>-<device>`, e.g. `ana-iphone` or `leo-mac`; a name you
choose later is never changed. It checks every 5 seconds.

The arrow next to the version turns red when a newer Tailscale client is
available (hover it to see which).

## DERP relays

Relays carry traffic between devices that cannot connect directly. The machine
detail shows the relay each device prefers and its latency to every relay it
measured; the Machines list has a **Relay** column. **Network → DERP relays**
lists the relays in use, how many devices prefer each and their median latency.

Admins can also add relays they run themselves (`derper`) there: region ID
(900 to 998), code, name, hostname, optional IPs and ports. Saving writes
`/data/config/derp.yaml`, points `derp.paths` of `config.yaml` at it, validates
the result with `headscale configtest` and restarts Headscale; if anything
fails the previous map is restored. With the default (`embedded`) the container
is its own relay: see [Configuration → Relays](configuration.md#relays-derp).

## Server status

**Settings → Status** (admins and auditors) shows the health of the server at
a glance: the Headscale and Headscale Easy versions with an "update available"
notice (latest releases looked up on GitHub, cached for 12 hours), the state of
the three processes (Headscale, Caddy, the console), disk use of the data volume
and basic figures from Headscale's metrics (devices online, requests served,
memory). Each part degrades on its own: with no Internet access the rest of the
page still works.

## Everyday commands

```bash
docker exec headscale-easy hse health      # healthy when all three processes run
docker exec headscale-easy hse reload      # re-render the config, restart Caddy and Headscale
docker exec headscale-easy hse backup      # back up now
docker exec headscale-easy hse backups     # list the backups
docker logs -f headscale-easy              # [supervisor] [headscale] [caddy] [console]
```

The Headscale CLI is always available inside the container. Point it at the rendered config with
`-c /data/config/config.yaml` (without it the CLI cannot find Headscale's socket):

```bash
docker exec headscale-easy headscale -c /data/config/config.yaml nodes list
docker exec headscale-easy headscale -c /data/config/config.yaml preauthkeys create --user 1 --reusable --expiration 24h
docker exec headscale-easy headscale -c /data/config/config.yaml --help
```

## Upgrading { #updating }

Every release is a version of one image, and `compose.yaml` pins it
(`HSE_VERSION`, `2.0.0` by default). An upgrade is three steps, and the data stays in
the volumes:

```bash
docker exec headscale-easy hse backup      # 1. a backup first (it lands in the backups volume)
# 2. set HSE_VERSION=2.0.1 in .env (or change the tag in compose.yaml)
docker compose pull && docker compose up -d   # 3. fetch the new image and recreate the container
```

Then open the console: the version is in the footer and on the **Status** page, and the
container is healthy once `docker compose ps` says so. Read the
[release notes](https://github.com/insanerask77/headscale-easy/releases) first: Headscale's API
changes between versions, which is why every release of Headscale Easy ships one pinned Headscale.

**Rolling back:** set the previous `HSE_VERSION` and run the same `pull` and `up -d`. If a
release changed the data format, restore the backup from step 1 with
[`hse restore`](all-in-one.md#backups).

**Which tag to use.** A release publishes `X.Y.Z` (exact), `X.Y` (the latest patch of that
minor), `X` (the latest minor of that major) and `latest`. Pin `X.Y.Z` in production; `X.Y`
takes patch fixes by itself. The tags `edge`, `next`, `dev` and `branch-*` are builds of a
branch for trying changes: they are **not for `compose.yaml`**, which must stay on a release.

## Backups

The container makes a backup every night at 03:00 and keeps 14 days, with no
extra container. The schedule, what is inside, restoring (from the console, with
`hse restore`, or on a new server) and the **Backups** menu are all in
[All-in-one → Backups](all-in-one.md#backups).

### Remote backups { #remote-backups }

A backup that sits on the same server does not survive losing the server. Two
ways to keep copies elsewhere:

- **Mount another disk over `/data/backups`**, for example a NAS folder
  (`-v /mnt/nas/hse-backups:/data/backups`, writable by uid 1000).
- **The `backup-remote` add-on** (`advanced/backup-remote.yaml`, see [Advanced configurations](advanced/backup-remote.md)):
  a sidecar container that uploads each new archive and applies a remote
  retention. Set `BACKUP_REMOTE` in the compose `.env` and start it with
  `docker compose -f compose.yaml -f advanced/backup-remote.yaml up -d`. Two kinds of destination:

    - **An rclone remote** (`BACKUP_REMOTE=s3:my-bucket/headscale-easy`): S3, B2,
      SFTP, Google Drive and [dozens more](https://rclone.org/overview/). Define the
      remote in `./remote-config/rclone.conf` (`rclone config` writes it), or for
      S3 skip the file and set
      `BACKUP_REMOTE=":s3,provider=AWS,env_auth=true,region=eu-west-1:my-bucket/dir"`
      with `BACKUP_AWS_ACCESS_KEY_ID` and `BACKUP_AWS_SECRET_ACCESS_KEY`.
    - **rsync over SSH** (`BACKUP_REMOTE=rsync:user@host:/srv/backups`): put the
      private key in `./remote-config/id_ed25519` (and optionally `known_hosts`;
      without it the first host key is accepted). `BACKUP_REMOTE_SSH_PORT` changes
      the port. The directory must already exist on the server.

    `BACKUP_REMOTE_KEEP_DAYS` sets the retention on the remote (default
    `BACKUP_KEEP_DAYS`). Use a key or bucket that can write but not delete if you
    can: then a compromised server cannot erase its own backups (set the retention
    in the bucket's lifecycle rules instead). If an upload fails the local backup
    is kept and the sidecar reports the error in `docker logs headscale-easy-backup-remote`.

### Restore

See [All-in-one → Backing up and restoring](all-in-one.md#backing-up-and-restoring):
from a stopped container (also how you restore on a new host), from the
console, or `hse restore <file>` on a running one.

## Uninstalling

```bash
docker compose down      # remove the container, keep the data
docker compose down -v   # also delete the volumes: users, devices, keys, certificates AND the backups
```

With `docker run`: `docker rm -f headscale-easy`, and `docker volume rm hse` to
delete the data.

## Activity log

**Logs** (admins only, in the sidebar) is the tailnet's activity log, like the
configuration audit log of Tailscale's admin console. It records:

- **Configuration**: every change made from the console — machines renamed,
  removed, expired, routes, tags and key expiry changed, machines registered
  with an Auth ID; users created, renamed and deleted; auth keys and API keys
  created, revoked or expired; the access control policy (with a diff of the
  change), DNS (each setting before and after), the device key expiry and the
  two-factor mode. Automatic renames of machines called `localhost` show up
  as *Headscale Easy (automatic)*.
- **Sign-in**: console sign-ins and sign-outs, and failed API key sign-ins.
- **Devices**: every 30 seconds the console compares Headscale's state and logs
  devices that register, are removed, connect or disconnect, whose key
  expires, whose Tailscale version changes, or that are renamed outside the console (for example with `headscale nodes rename`).

Each event has the time, the actor (the user name, `API key <prefix>` for API
key sessions, or *Headscale* for device events), the client IP, the target and
the details. Secrets are never stored: auth keys, API keys and Auth IDs are
reduced to their prefix.

Search, filter by category, actor and dates (UTC), and download the matching
events with the CSV button. The first page updates on its own.

The log lives in `/data/console/audit.db` (SQLite). Events older than
90 days are deleted automatically. Changes made outside the console (the `headscale` CLI,
the API) are not configuration events, but their effect on devices is logged.

!!! note
    Headscale has no network flow logs (which device talked to which, and
    when): that needs data from the clients that only Tailscale's own
    coordination server collects.

## Notifications { #notifications }

Headscale Easy can message you when something happens to a device. Set the
destinations with environment variables (in the `.env` next to the compose file
or with `-e`) and recreate the container:

```bash
# comma, space or new line separated
NOTIFY_URLS="slack:https://hooks.slack.com/services/T000/B000/XXXX ntfy:my-topic"
NOTIFY_EVENTS="device.registered,device.key_expired,device.expiring,device.removed"
```

| Destination | Format |
| --- | --- |
| Slack | `slack:<incoming webhook URL>` (a bare `hooks.slack.com` URL works too) |
| Telegram | `telegram:<bot token>@<chat id>`, e.g. `telegram:123456:ABC-def@-100987` |
| ntfy | `ntfy:<topic>` (ntfy.sh) or `ntfy:https://your-ntfy/topic` |
| Generic webhook | `webhook:<URL>` (or a bare `https://` URL): POST with a JSON body `{source, event, target, message, details, timestamp}` |

Events (all by default; `NOTIFY_EVENTS` picks some): `device.registered` (a new
device joined), `device.key_expired`, `device.expiring` (the key expires within
14 days; sent once per device and expiry date, checked every 15
minutes) and `device.removed`. Messages go out in the background with a
10-second timeout and 3 attempts, so a slow or broken destination never slows
down the console; failures only appear in the container's log (without the
URL, which holds secrets).

Admins see the destinations (host only) in **Settings → General →
Notifications**, with a **Send a test** button (recorded in the activity log).

## Sessions

**Settings → Sessions** lists where you are signed in (IP, browser, last
activity). **Log out** ends one session, **Sign out everywhere** ends all of
yours, and admins also see every user's sessions and can use **Sign out
everyone else**. A revoked session stops working on its next request. Sessions
live in `/data/console/sessions.db` (SQLite). After more than 10 failed
sign-ins from one IP in 600 seconds the console answers `429` until the window
passes.

## Troubleshooting

Start with the logs and the health check:

```bash
docker logs --tail 100 headscale-easy
docker exec headscale-easy hse health
```

**The container is unhealthy or restarts.** `hse health` says which of the three
processes is down; the log lines are prefixed `[supervisor]`, `[headscale]`,
`[caddy]` and `[console]`. A crashed process is restarted with backoff (1 s up to
30 s). An invalid `BACKUP_SCHEDULE` or `HSE_TLS=auto` without `ACME_EMAIL` stops
the container at start, with the reason in the log.

**Headscale never becomes healthy (with an external OIDC provider).** It refuses
to start until it can read the issuer's discovery document from inside the
container. Check `OIDC_ISSUER`, and that the container can reach it:

```bash
docker exec headscale-easy wget -qO- https://<issuer>/.well-known/openid-configuration
```

If the provider answers from outside but not from inside, your router probably
lacks NAT loopback or the provider's name does not resolve from the container:
use a resolvable address.

**`redirect_uri` errors after changing the domain.** Register the new redirect
URIs in your provider: `https://<domain>/oidc/callback` and
`https://<domain>/admin/callback`.

**Clients say `x509: certificate signed by unknown authority`.** You are using
`HSE_TLS=internal`: install Caddy's root certificate (`/data/caddy/pki/`) on the
client, or switch to `auto` (Let's Encrypt).

**Devices connect but cannot reach each other.** Check the ACL policy (with
isolation, users only reach their own devices) and that UDP 3478 is open for
the DERP relay.

**The console says the API key expired.** The console renews its own
Headscale API key when it has 15 days left, so this only happens if the server
was off for that whole window or someone expired the key by hand. Delete
`/data/console/api-key` and restart the container: it creates a new one.

**Signing in to the console fails.** "The sign-in expired or is not valid"
means the browser came back without the cookie set when sign-in started: check
that you open the console with the exact `HSE_PUBLIC_URL` (same host and scheme —
`http` vs `https` matters) and that the browser accepts cookies. After more than
10 failed sign-ins in 10 minutes the console answers `429` for a while. With an
external provider, the redirect URI `https://<domain>/admin/callback` must be
registered, and admins need a verified e-mail in `PORTAL_ADMIN_EMAILS` or a group
in `PORTAL_ADMIN_GROUPS`. If nobody can sign in, start the container with
`HSE_ADMIN_EMAIL` and `HSE_ADMIN_PASSWORD` to create an administrator, or use the
Headscale API key sign-in.

**Let's Encrypt does not issue the certificate.** Ports 80 and 443 must be
reachable from the Internet and the domain must point at this host (check
with `dig +short <domain>` from outside). `docker logs headscale-easy` shows the
ACME error. Too many failed attempts trigger Let's Encrypt's rate limits: fix
the cause and wait an hour.

**Behind my own proxy, devices do not connect or stay "offline", or every client
shows the proxy's address.** The proxy must pass WebSockets and upgrade headers,
must not buffer responses, and must forward the original `Host`. Set
`HSE_TLS=off`, an `https://` `HSE_PUBLIC_URL` and `HSE_TRUSTED_PROXIES`. Use the
snippets in [`advanced/proxy/`](advanced/proxy.md).

**A device stays "waiting for approval" or shows a registration URL.** With an
external provider the person must finish sign-in in the browser it opened.
Otherwise open that URL: the console shows the approval page, or register it from
**Machines → Add device → Register with Auth ID**, or use an auth key. Check the
owner: with isolation, a device registered to the wrong user is invisible to its
owner.

**DNS changes are rejected.** The console runs `headscale configtest` and
rolls back when Headscale refuses the change; the error shown is Headscale's.
The tailnet DNS name must differ from the server's domain. If the DNS page is
read-only it says why; check `hse health` and the `[supervisor]` log lines.

**The ACL policy blocks traffic you expect.** Use **Check** in the policy
editor before saving, and remember that with `NETWORK_ISOLATION=true` each
user (admins included) only reaches their own devices unless the policy says
otherwise. Tagged devices belong to the tag, not to a user.

**A backup failed.** The **Backups** menu shows the last result and the reason;
`docker exec headscale-easy hse backups` lists them. The usual cause is
`/data/backups` not writable by uid 1000 (a bind mount owned by root) or a full
disk. With an external PostgreSQL, the dump needs the server reachable.

Still stuck? [Open an issue](https://github.com/insanerask77/headscale-easy/issues/new/choose)
with the output of `hse health` and the relevant logs (remove secrets).
