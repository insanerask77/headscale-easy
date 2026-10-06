# Configuration

Headscale Easy is one container, configured in three ways that stack in this
order (the first wins):

1. **Environment variables** (`-e`, or the `.env` next to the compose file).
2. **`/data/config/settings.json`**, written by the setup wizard and by the
   console's Settings pages.
3. **Defaults.**

With none of them (no `settings.json`, no `HSE_PUBLIC_URL`) the container starts
in [setup mode](all-in-one.md#the-first-run-wizard). The image renders Headscale's
`config.yaml`, the `Caddyfile` and the DERP map from those settings on every
start (`/data/config/`); you never edit the generated files by hand, except the
DNS block, which the console manages. `docker exec headscale-easy hse reload`
re-renders and restarts after a change.

## Environment variable reference { #reference }

Every variable the image reads. The ones marked *wizard* are also asked by the
[first-run wizard](all-in-one.md#the-first-run-wizard).

### Server and HTTPS

| Variable | Default | Meaning |
|---|---|---|
| `HSE_PUBLIC_URL` | *(none: wizard)* | Public `http(s)://host[:port]` of the server. Setting it skips the wizard |
| `HSE_TLS` | `auto` for https, `off` for http | Who terminates TLS: see [HTTPS modes](#https-modes). *wizard* |
| `ACME_EMAIL` | | Required with `HSE_TLS=auto` |
| `HSE_DERP_PORT` | `3478` | STUN port of the embedded DERP relay (publish it as UDP) |
| `HSE_DERP_MODE` | `embedded` | `embedded`, `public` (also Tailscale's relays) or `custom`: see [Relays](#relays-derp). *wizard* |
| `HSE_DERP_URL` | | DERP map URL, with `HSE_DERP_MODE=custom` |
| `DERP_USE_PUBLIC` | | Legacy alias: `true` = `public`, `false` = `embedded`; `HSE_DERP_MODE` wins |
| `HEADSCALE_HTTP_PORT`, `HEADSCALE_METRICS_PORT`, `HEADSCALE_GRPC_PORT` | `8080`, `9090`, `50443` | Headscale's internal ports (inside the container; not published) |
| `IP_PREFIXES_V4`, `IP_PREFIXES_V6` | `100.64.0.0/10`, `fd7a:115c:a1e0::/48` | Address ranges handed to devices. Changing them renumbers every device |
| `LOG_LEVEL` | `info` | Headscale's log level |
| `HSE_TRUSTED_PROXIES`, `HSE_TRUSTED_PROXIES_ANY` | | The proxy in front: real client IPs. See [Advanced edition](advanced.md#a-proxy-in-front) |
| `UI_LANG` | `en` | Default console language (`en`, `es`, `fr`, `de`, `pt`) |
| `TZ` | `UTC` | Time zone (also the clock of the backup schedule) |

### Tailnet

| Variable | Default | Meaning |
|---|---|---|
| `TAILNET_NAME` | `myorg` | Label of the tailnet. *wizard* |
| `HSE_BASE_DOMAIN` | `hse.net` | MagicDNS base domain: devices are `<device>.<base domain>`. Must differ from the server's own domain. Set at first run; later edit it on the DNS page. *wizard* |
| `NETWORK_ISOLATION` | `true` | Each user only reaches their own devices: see [Network isolation](#network-isolation-and-acls). *wizard* |
| `NODE_KEY_EXPIRY` | `180d` | Device key lifetime: see [Device key expiry](#device-key-expiry) |

### Accounts and sign-in

| Variable | Default | Meaning |
|---|---|---|
| `HSE_ADMIN_EMAIL`, `HSE_ADMIN_PASSWORD` | | First administrator, created on first start if no account exists. *wizard* |
| `HSE_SIGNUP` | `off` | Self-registration: `off`, `invite` (needs an invitation key) or `open`. *wizard* |
| `MFA_REQUIRED` | `admins` | Who must set up two-factor: `admins`, `everyone` or `optional`. Admins change it later in **Settings → General** |
| `SESSION_SECRET` | *(generated)* | Signs console sessions. Generated and kept in `/data/config/session-secret` when empty |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS`, `SMTP_USE_SSL`, `SMTP_FROM` | | Optional mail server, to e-mail invitations and password-reset links |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | | Sign in with an external OIDC provider: see [Sign-in](#sign-in) |
| `OIDC_SCOPE` | `openid profile email` | Scopes asked by the console and Headscale |
| `HSE_OIDC_ALLOWED_DOMAINS`, `HSE_OIDC_ALLOWED_USERS`, `HSE_OIDC_ALLOWED_GROUPS` | | Who may sign in through the provider (comma-separated; empty = everyone the provider lets in) |
| `PORTAL_ADMIN_EMAILS` | `HSE_ADMIN_EMAIL` | Provider accounts that are administrators in the console |
| `PORTAL_ADMIN_GROUPS`, `PORTAL_NETWORK_ADMIN_GROUPS`, `PORTAL_AUDITOR_GROUPS` | | Provider groups that map to each console role: see [Roles](#roles) |
| `HSE_AUTHENTIK_UPSTREAM` | | `host:port` of an external Authentik that Caddy serves under `/authentik`, so its issuer URL does not change |

### Notifications

| Variable | Default | Meaning |
|---|---|---|
| `NOTIFY_URLS` | | Destinations (Slack, Telegram, ntfy, webhook): see [Operations → Notifications](operations.md#notifications) |
| `NOTIFY_EVENTS` | all | Which device events are sent |

### Console tuning

Optional. Unset, the console uses the default shown.

| Variable | Default | Meaning |
|---|---|---|
| `EXPIRY_WARNING_DAYS` | `14` | A machine "expires soon" when its key expires within this many days |
| `INACTIVE_DAYS` | `30` | A machine counts as inactive after this many days offline |
| `AUTO_RENAME_LOCALHOST` | `true` | Rename machines that register as `localhost` |
| `RENAME_INTERVAL` | `5` | Seconds between two rename passes (minimum 1) |
| `AUDIT_RETENTION_DAYS` | `90` | Days the activity log keeps events; `0` keeps them forever |
| `STATUS_UPDATE_CHECK` | `true` | Look for a newer release and show it on the Status page |
| `SIGNIN_RATE_LIMIT`, `SIGNIN_RATE_WINDOW` | `10`, `600` | Failed sign-ins allowed from one IP within the window (seconds) before the console answers `429` |
| `PORTAL_API_KEY_LOGIN` | `false` | Emergency sign-in with the Headscale API key (always on when no provider is set) |
| `BACKUP_UPLOAD_MAX_MB` | `1024` | Largest backup the Backups page accepts as an upload |

### Database

| Variable | Default | Meaning |
|---|---|---|
| `HEADSCALE_DB_TYPE` | `sqlite` | `sqlite` or `postgres` (an external server): see [Database](#database) |
| `HEADSCALE_PG_HOST`, `HEADSCALE_PG_PORT`, `HEADSCALE_PG_NAME`, `HEADSCALE_PG_USER`, `HEADSCALE_PG_PASS`, `HEADSCALE_PG_SSLMODE` | port `5432`, database and user `headscale`, TLS `disable` | Connection to the external PostgreSQL |
| `HEADSCALE_PG_RO_USER`, `HEADSCALE_PG_RO_PASS` | | Read-only role the image creates for the console, so it never holds the owner's credentials |

### Backups

| Variable | Default | Meaning |
|---|---|---|
| `BACKUP_SCHEDULE` | `0 3 * * *` | Cron syntax, in the time zone `TZ`; `off` disables scheduled backups. An invalid value stops the container at start. *wizard* |
| `BACKUP_KEEP_DAYS` | `14` | Older backups are deleted; the newest successful one is never deleted. *wizard* |
| `BACKUP_MODE` | `create` | Only for the `backup` image used as a sidecar: `sync` uploads the archives the all-in-one image wrote |
| `BACKUP_SYNC_INTERVAL` | `900` | Seconds between two uploads in `sync` mode |

## HTTPS modes

| `HSE_TLS` | Who terminates TLS | Use it when |
|---|---|---|
| `auto` | Caddy, with a Let's Encrypt certificate | Public server with a domain; ports 80/443 open |
| `internal` | Caddy, with its internal CA | No public DNS; you can install a CA on clients |
| `off` | Nobody (plain HTTP), or a proxy in front | localhost, a trusted LAN, or behind your own reverse proxy |

With `internal`, Caddy's root certificate is in `/data/caddy/pki/` (and in every
backup). Install it on every client or they will refuse to connect
(`x509: certificate signed by unknown authority`). Android and iOS apps only
work with a publicly trusted certificate.

Behind a reverse proxy you already run (nginx, Traefik, Caddy, Nginx Proxy
Manager) use `HSE_TLS=off`, an `https://` `HSE_PUBLIC_URL` and
`HSE_TRUSTED_PROXIES`: see [Advanced edition → A proxy in
front](advanced.md#a-proxy-in-front) for the checklist and ready-made snippets.

The embedded DERP relay needs **UDP 3478** reachable from the Internet in every
mode: no HTTP proxy can carry it.

### Relays (DERP) { #relays-derp }

By default (`HSE_DERP_MODE=embedded`) the container is its own relay: DERP and
STUN run inside it and `derp.urls` stays empty, so neither Headscale nor the
console contacts tailscale.com's DERP map. Devices that cannot connect directly
then depend on your relay: keep UDP 3478 and HTTPS reachable. `public` adds
Tailscale's public relays; `custom` uses your own map (`HSE_DERP_URL`, or one
uploaded in **Network → DERP relays**).

## Sign-in

There are three ways to sign in to the console, and they combine:

| Method | Accounts | Notes |
|---|---|---|
| **Local accounts** (default) | Created in the console: password, optional two-factor | Invitations, reset links and self-registration, all in the console. No other service to run |
| **External OIDC** | Your provider (Authentik, Keycloak, Pocket ID, Google…) | Set `OIDC_*`. The same client signs people in to the console and registers devices in Headscale |
| **API key** | None | Emergency access for administrators with a Headscale API key |

### Local accounts

- The first administrator comes from the wizard, or from `HSE_ADMIN_EMAIL` +
  `HSE_ADMIN_PASSWORD`.
- **Users → Invite user** makes a single-use link (1, 7 or 30 days) where the
  person chooses a user name and password; with an e-mail in the invitation the
  account must use it. **Users → Pending invitations** lists the links not used
  yet (copy again or revoke).
- **Users → ⋯ → Password reset link…** makes a single-use link (1 hour, 24 hours
  or 7 days). **Set password** sets a temporary one that the person must
  change at the next sign-in, and signs their open sessions out.
- With an SMTP server (`SMTP_*`) the console can e-mail invitations and reset
  links. Without one, copy the link and send it privately.
- Passwords have at least 8 characters and are stored as salted hashes. Failed
  sign-ins are rate limited per address.
- **Self-registration** from the sign-in page is `off`, `invite` or `open`
  (`HSE_SIGNUP`, or **Settings → General**). Self-registered people are always
  members.

### Roles

| Role | Can do |
|---|---|
| Admin | Everything: machines, users, DNS, access controls, keys, settings, logs, backups |
| Network admin | Edit the **Access controls** policy and **DNS**. Nothing else |
| Auditor | See everything an admin sees, change nothing, anywhere — not even their own devices |
| Member | See and manage only their own machines and auth keys |

With local accounts the role is set per account (**Users**). With an external
provider it comes from its groups and e-mails:

| Variable | Role |
|---|---|
| `PORTAL_ADMIN_GROUPS`, `PORTAL_ADMIN_EMAILS` | Admin |
| `PORTAL_NETWORK_ADMIN_GROUPS` | Network admin |
| `PORTAL_AUDITOR_GROUPS` | Auditor |

Admin takes priority if someone is in more than one group. Your provider must
send a `groups` claim (the default `profile` scope usually includes it).

### Two-factor authentication

Local accounts can ask for a second factor after the password: an authenticator
app (TOTP), with recovery codes. Admins choose who must use it in
**Settings → General → Two-factor authentication** (`MFA_REQUIRED` is the initial
value):

| `MFA_REQUIRED` | Behaviour |
|---|---|
| `admins` (default) | Admins must set it up the first time they sign in; members may |
| `everyone` | Every user must set it up |
| `optional` | Nobody is forced |

Users with a second factor are always asked for it. Each person manages theirs
in **Settings → General → Account**. With an external provider, two-factor is
configured there. The emergency API key sign-in has no second factor.

### Your own OIDC provider

Register one client with **two** redirect URIs:

- `https://<your-domain>/oidc/callback` (Headscale)
- `https://<your-domain>/admin/callback` (console)

The console and Headscale share the client so a person's identity (`sub`)
matches in both. Step-by-step examples for Authentik, Pocket ID, Keycloak and
Google are in the [advanced edition](advanced.md#identity-providers).

## Network isolation and ACLs

With `NETWORK_ISOLATION=true` (the default, or the wizard's isolation option) setup applies this policy
the first time:

```jsonc
{
  "acls": [
    {"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:self:*"]},
    {"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:internet:*"]}
  ]
}
```

Each user reaches only their own devices — admins included — and can send
internet traffic through exit nodes (without the `autogroup:internet` rule an
exit node accepts connections but forwards nothing). An existing policy is
never overwritten. Edit it in **Access controls**; the syntax is
[Tailscale's](https://tailscale.com/kb/1337/policy-syntax).

**Access controls** has six tabs:

- **Rules**, **Groups & tags**: forms for the common cases — who can reach
  what, reusable groups of users, who owns each tag — without writing HuJSON.
  They edit the same policy Headscale uses: under the hood, each save rewrites
  only the `acls`, `groups` or `tagOwners` block it touched and leaves the rest
  of the file — comments, key order, a `hosts` section or anything else you
  wrote by hand — exactly as it was. A rule whose destinations mix different
  ports (something the forms cannot represent) can still be deleted from here,
  but only edited in Advanced.
- **Auto-approval**: declare which tag, group or user gets a subnet route (or
  the exit node role) approved automatically, instead of approving each
  device by hand from its machine page (see [Managing
  machines](operations.md#managing-machines) for that manual, double opt-in
  flow). This is Headscale's [`autoApprovers` policy
  section](https://headscale.net/stable/ref/routes/).
- **SSH rules**: who can SSH into which machines, as which host users, using
  [Tailscale SSH](https://tailscale.com/kb/1193/tailscale-ssh) — no SSH keys
  to manage. A rule can also require re-authenticating every so often instead
  of a flat allow. This only controls *who is allowed in*: Tailscale SSH still
  needs `tailscale up --ssh` (or the equivalent) on each device that should
  accept connections.
- **Test access**: pick a source and a destination (a device name, a user, a
  tag…) and it says whether the policy allows it and which rule matched. This
  is a **simulation** over the saved policy, not a live packet test — for
  certainty, test from the actual devices (`tailscale ping`, or try to reach
  the service).
- **Advanced (HuJSON)**: the original text editor, unchanged. It is the full
  escape hatch: anything the visual editor cannot represent — device
  postures, hand-written comments — is only ever edited here, and nothing is
  lost by having both.

## Device key expiry

Like Tailscale, every device has a key that expires: after that the device has
to sign in again. Headscale Easy sets it to **180 days** (`NODE_KEY_EXPIRY`) (Tailscale's default);
admins change it in **Settings → General → Device management** (1–365 days, or
never). Saving restarts Headscale, and it applies to devices added from then
on: change existing ones per machine (**⋯ → Enable/Disable key expiry**).

The expiry of an **auth key** is something else: it only limits until when the
key can add devices.

## DNS

Admins edit DNS in the console (**DNS** page), laid out like Tailscale's:

- **Tailnet DNS name** — *Rename tailnet…* asks for confirmation first: every
  machine's full name (`<machine>.<tailnet domain>`) changes.
- **MagicDNS** — on/off; turning it off asks for confirmation, since machine
  names stop resolving on every device.
- **Nameservers** — with MagicDNS on, the tailnet domain always resolves through
  `100.100.100.100` (shown read-only). Below it, *split DNS* nameservers (a
  nameserver restricted to a domain) and *global* nameservers, one per row (an
  IP or a DoH resolver `https://…`). **Use local DNS settings** on: devices keep
  their own nameservers and the global ones are a fallback; off
  (`override_local_dns: true`): every device uses the global nameservers.
- **Search domains** — with MagicDNS on, the tailnet domain is always the first.
- **Custom records** — `name` + `address` (e.g. `nas.example.com` →
  `100.64.0.5`): every device of the tailnet resolves it; A or AAAA from the
  address.

Members see the same settings read-only. The console writes the `dns:` block
of `/data/config/config.yaml` between these markers:

```yaml
# >>> dns: managed by Headscale Easy (do not edit between these markers)
dns:
  ...
# <<< dns
```

then runs `headscale configtest` and restarts Headscale — restoring the previous
block if the check fails. The image keeps that block when it re-renders the
file (for example after you change a setting), so your DNS settings survive.

Validating and restarting go through the container's own supervisor: there is
no Docker socket anywhere. See [Security](security.md).

## Database

Headscale keeps users, machines and keys in **SQLite** by default: one file in
`/data/headscale/`, nothing else to run. That is Headscale's own recommendation
and the right choice for almost every tailnet. **PostgreSQL** is supported as an
**external** server, for large tailnets or if you already run (and back up) one:

| Choice | `HEADSCALE_DB_TYPE` | What you provide |
|---|---|---|
| SQLite (default) | `sqlite` | Nothing |
| External PostgreSQL | `postgres` | host, port, database, owner user and password, TLS mode (`HEADSCALE_PG_*`) |

- **There is no conversion** between SQLite and PostgreSQL (Headscale has no
  tool for it). Choose before you add devices.
- **The console reads with a read-only role.** It needs the Hostinfo devices
  report (OS, Tailscale version, DERP relay, endpoints), which Headscale's API
  does not expose. With `HEADSCALE_PG_RO_USER` the image creates that role with
  [`templates/headscale-pg-readonly.sql`](https://github.com/insanerask77/headscale-easy/blob/next/templates/headscale-pg-readonly.sql):
  it may only `SELECT` the `id`, `host_info` and `endpoints` columns of
  `nodes` (no keys, no other tables) and its sessions are read-only. The console
  never gets Headscale's own credentials. It talks to PostgreSQL with a small
  built-in client (standard library only: SCRAM-SHA-256, optional TLS).
- **Your server:** it must authenticate with `scram-sha-256` (PostgreSQL's
  default since 14; the legacy `md5` method is refused). The database must
  exist and its owner must be the user you give (Headscale creates its tables
  with it). To create the read-only role the image runs that SQL as the owner,
  which needs the `CREATEROLE` privilege; if it cannot, the console falls back
  to the owner's credentials and logs it loudly.
  `HEADSCALE_PG_SSLMODE` (`disable`, `prefer`, `require`, `verify-ca`,
  `verify-full`) applies to Headscale, the console and backups.
- Backups use `pg_dump` (see [Operations → Backups](operations.md#backups)).
  The [advanced edition](advanced.md#postgresql) has a compose file and a checklist.

## Language

The console follows the browser's language (English, Spanish, French, German or Portuguese) and each person
can switch it in **Settings → General**. `UI_LANG` (`en`, `es`, `fr`, `de` or `pt`) sets the default when the
browser asks for a language the console does not have.

## What lives in `/data`

| Path | Contents |
|---|---|
| `headscale/` | Headscale database, keys and local socket |
| `caddy/` | Certificates and access logs |
| `console/` | Accounts, sessions and audit databases, Headscale API key |
| `config/` | `settings.json`, rendered `config.yaml`, `Caddyfile`, `derp.yaml` |
| `backups/` | The built-in backups and `status.json` |

Everything is created by the container with private permissions (700 / 600).
