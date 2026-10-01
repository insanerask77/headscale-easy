# Configuration

Everything is configured by `./install.sh`. It stores your answers in `.env`
and generates the rest from `templates/`. Run it again to change anything:
your previous answers become the defaults and no data is lost.

## The installer

The questions, in order:

1. **Language** — English or Español (installer and default UI language).
2. **Domain or IP** — the single name the stack is reached at. Caddy routes it:
   `/` → Headscale, `/admin` → web console, `/authentik` → Authentik.
3. **Who provides HTTPS** — see [HTTPS modes](#https-modes).
4. **Ports** — Enter accepts the defaults.
5. **Tailnet** — organization name, initial Headscale user, address ranges.
6. **Sign-in** — Authentik, your own OIDC provider, or API key only. With
   Authentik, optionally an SMTP server for email ("Forgot password?").
7. **Network isolation** — whether every user only reaches their own devices.
8. **Backups** — whether to make a daily backup (off by default, recommended);
   if so, at what time, in which folder and for how many days. See
   [Backups](operations.md#backups).

Then it deploys: Authentik first (Headscale refuses to start until the OIDC
issuer answers), then Headscale, creates the initial user and the API key the
console uses, applies the isolation policy, and starts the rest.

## HTTPS modes

| `SSL_MODE` | Who terminates TLS | Use it when |
|---|---|---|
| `letsencrypt` | Caddy, with a Let's Encrypt certificate | Public server with a domain; ports 80/443 open |
| `selfsigned` | Caddy, with its internal CA | No public DNS; you can install a CA on clients |
| `front` | Another proxy in front (NPM, nginx, Traefik, Caddy) | You already run a reverse proxy |
| `none` | Nobody (plain HTTP) | localhost, a trusted LAN, or behind another VPN |

With `selfsigned`, the installer exports Caddy's root certificate to
`caddy-root-ca.crt`. Install it on every client or they will refuse to connect
(`x509: certificate signed by unknown authority`). Android and iOS apps only
work with a publicly trusted certificate.

The embedded DERP relay needs **UDP 3478** reachable from the Internet in every
mode: no HTTP proxy can carry it.

### Public DERP servers

The installer asks whether to also use Tailscale's public DERP relays
(`DERP_USE_PUBLIC`, **yes** by default). Answer **no** for a fully self-hosted
install: only your own embedded relay is used and `derp.urls` stays empty, so
neither Headscale nor the web UI contacts tailscale.com's DERP map. Devices that
cannot connect directly then depend on your relay: keep UDP 3478 and HTTPS
reachable. Re-run `./install.sh` to change it.

## Behind an existing reverse proxy

Choose `front` and tell the installer which proxy you use and this machine's
address as seen from it. It writes a ready-to-use configuration to
`reverse-proxy/`:

| Proxy | File |
|---|---|
| Nginx Proxy Manager | `reverse-proxy/NGINX-PROXY-MANAGER.md` (step-by-step) |
| nginx | `reverse-proxy/nginx-<domain>.conf` |
| Traefik | `reverse-proxy/traefik-<domain>.yml` (file provider) |
| Caddy | `reverse-proxy/Caddyfile` |

Your proxy forwards the whole domain to Caddy on `BACKEND_HOST:HTTP_PORT`; Caddy
does the path routing. Requirements the snippets already cover: WebSocket /
HTTP upgrade (for `/ts2021`), no response buffering and long read timeouts (for
the `/machine/map` long-poll), no body size limit.

If the containers cannot reach your public URL (no NAT loopback on your router),
set `FRONT_PROXY_IP` to the proxy's LAN address.

## Sign-in

| `AUTH_PROVIDER` | Accounts | Console access |
|---|---|---|
| `authentik` | Built-in Authentik: username + password, optional Google | Everyone signs in; admins are members of `vpn-admins` or `authentik Admins` |
| `external` | Your OIDC provider | Everyone signs in; admins are listed in `PORTAL_ADMIN_EMAILS` (or the groups in `PORTAL_ADMIN_GROUPS`, if your provider sends a `groups` claim) |
| `none` | None | Admins only, with a Headscale API key |

### Roles

Besides admin and member, two narrower roles are available, both opt-in
(off unless configured) and built on Authentik groups the same way
`PORTAL_ADMIN_GROUPS` already is:

| Role | Env var | Can do |
|---|---|---|
| Network admin | `PORTAL_NETWORK_ADMIN_GROUPS` | Edit the **Access controls** policy and **DNS**. Nothing else — no Users, Machines beyond their own, Logs or Settings. |
| Auditor | `PORTAL_AUDITOR_GROUPS` | See everything an admin sees (all machines, Users, DNS, Access controls, Logs) but change nothing, anywhere — not even their own devices. |

Admin takes priority if someone is in more than one of these groups. With
built-in Authentik, create the group(s) yourself (**Directory → Groups**) and
add members: the console already receives every group a signed-in user
belongs to (the default `profile` OIDC scope includes it), so no blueprint
change is needed. With your own OIDC provider, make sure it sends a `groups`
claim.

### Built-in Authentik

- The first admin is `akadmin`; the installer prints its first-start password.
  Change it at `/authentik/if/user/`.
- Invite people from the console (**Users → Invite user**): they choose their
  own user name and password; see [Invitations and password
  reset](#invitations-and-password-reset).
- Or add them yourself at **`/add-user`** (or **Users → Add user** in the
  console): a simple form — name, username, email, password and whether they
  are an admin. No need to enter Authentik's admin interface. Emails and user
  names must be unique.
- The login pages use the Headscale Easy theme (dark/light following the browser).
- Authentik is configured by the blueprint `authentik/blueprints/headscale.yaml`.
  The installer re-applies it on every run. Sign-in and sign-out use Headscale
  Easy's own flows (`headscale-easy-sign-in`, `headscale-easy-sign-out`):
  Authentik resets its default flows from time to time.

### Invitations and password reset

**Invitations.** In **Users → Invite user**, choose the access (member or
admin), optionally an email, and how long the link is valid (1, 7 or 30 days;
7 by default). The console shows a link to copy and send; whoever opens it sees
*Create your account* (flow `headscale-easy-invitation`), chooses a user name
and a password (the same rules as `/add-user`: at least 10 characters, unique
user name and email) and is signed in straight into the console, in the group
you chose (`headscale-users` or `vpn-admins`). With an email in the invitation
the account must use it (the field is locked). The link works once: it is used
up when the account is created, not when it is opened. Admins that the
two-factor setting requires set up their second factor before getting in.
**Users → Pending invitations** lists the links not used yet (copy again or
**Revoke**).

**Password reset without email.** In the **⋯** menu of a user, **Password
reset link…** makes a single-use link (valid 1 hour, 24 hours or 7 days) where
that person chooses a new password (flow `headscale-easy-recovery`); the old
one keeps working until then. Send it privately. People who have an account
but have not connected a device yet (so they are not Headscale users yet) are
listed under **Accounts without devices**, with the same action. Accounts are
matched with Headscale users by their OIDC identity, not by name. Authentik
superusers (`akadmin`, `authentik Admins`) are left out on purpose: they reset
their password in Authentik (or with
`docker exec -it authentik-worker ak create_recovery_key 60 akadmin`).

**With email (optional).** The installer asks for an SMTP server (off by
default): host, port, security (STARTTLS, SSL/TLS or none), user, password and
sender, stored as `SMTP_*` in `.env` and given to Authentik as
`AUTHENTIK_EMAIL__*`. Then the sign-in page shows **Forgot password?**, which
emails a reset link to the account's address (flow
`headscale-easy-forgot-password`), and the console can email invitations and
reset links too. Without email there is no **Forgot password?** link. Test the
settings with `docker exec authentik-worker ak test_email you@example.com`.

The console does all this through Authentik's API with the service account's
token (`PORTAL_AUTHENTIK_TOKEN`), which the blueprint allows to list accounts,
make reset links and manage invitations. An install made with an older version
gets it by running `./install.sh` again.

### Two-factor authentication

Headscale Easy's sign-in asks for a second factor after the password: an
authenticator app (TOTP) or a passkey. Admins choose who must use it in the
web UI, **Settings → General → Two-factor authentication**; the change applies
in Authentik right away (from the next sign-in), without reinstalling.
`MFA_REQUIRED` in `.env` (asked by the installer) is the initial value, and
re-running `./install.sh` applies the value chosen there:

| `MFA_REQUIRED` | Behaviour |
|---|---|
| `admins` (default) | Members of `vpn-admins` and `authentik Admins` must set it up the first time they sign in; members may |
| `everyone` | Every user must set it up |
| `optional` | Nobody is forced |

Users with a second factor are always asked for it. Each person manages theirs
in **Settings → General → Account, password and two-factor authentication**.
Signing in with Google relies on Google's own two-step verification, and the
emergency API key sign-in has no second factor.

How the setting is stored: the mode is the first line (`mode = "admins"`) of
the Authentik policy *Headscale Easy: two-factor required for this user*. The
blueprint creates that policy once, from `MFA_REQUIRED`, and never updates it
afterwards (`state: created`), so restarting Authentik does not undo what an
admin chose. The web UI changes that line through Authentik's API with the
token in `PORTAL_AUTHENTIK_TOKEN` (generated by the installer), which belongs
to the service account `headscale-easy-web` and may only read and change that
policy (besides invitations and reset links, above). The token never reaches the browser. Without it (a `.env` written by
an older installer, until `./install.sh` runs again) the web UI shows the mode read-only;
with your own OIDC provider, two-factor authentication is configured there and
the section is hidden. From the server:
`docker exec headscale-easy python /app/mfa.py get` (or `set everyone`).

### Sign in with Google

Available with Authentik and HTTPS. In the
[Google Cloud console](https://console.cloud.google.com/apis/credentials) create
an **OAuth client ID** of type *Web application* with:

- Authorized JavaScript origin: `https://<your-domain>`
- Authorized redirect URI: `https://<your-domain>/authentik/source/oauth/callback/google/`

Give the client ID and secret to the installer. People signing in with Google
for the first time get an account **without a group**: an admin must add them
to `headscale-users` or `vpn-admins` before they can use the VPN.

### Your own OIDC provider

Register one client with **two** redirect URIs:

- `https://<your-domain>/oidc/callback` (Headscale)
- `https://<your-domain>/admin/callback` (console)

The console and Headscale share the client so a person's identity (`sub`)
matches in both.

### Emergency access

With OIDC you can also allow signing in to the console with a Headscale API key
(`PORTAL_API_KEY_LOGIN=true`), useful if the identity provider is down. Create
a key with `make apikey`.

## Network isolation and ACLs

With `NETWORK_ISOLATION=true` (the default) the installer applies this policy
the first time:

```jsonc
{
  "acls": [
    {"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:self:*"]}
  ]
}
```

Each user reaches only their own devices — admins included. An existing policy
is never overwritten. Edit it in **Access controls**; the syntax is
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
to sign in again. Headscale Easy sets it to **180 days** (Tailscale's default);
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
of `headscale-config.yaml` between these markers:

```yaml
# >>> dns: managed by Headscale Easy (do not edit between these markers)
dns:
  ...
# <<< dns
```

then runs `headscale configtest` and restarts Headscale — restoring the previous
block if the check fails. The installer keeps that block when it regenerates the
file, so your DNS settings survive reconfiguration.

Validating and restarting go through the `hs-helper` container, the only one
with the Docker socket; see [Security](security.md).

## Database

Headscale keeps users, machines and keys in **SQLite** by default: one file next
to its private keys, nothing else to run. That is Headscale's own
recommendation and the right choice for almost every tailnet. The installer
also offers **PostgreSQL**, for large tailnets or if you already run (and back
up) a PostgreSQL server:

| Choice | What runs | `.env` |
|---|---|---|
| SQLite (default) | Nothing extra | `HEADSCALE_DB_TYPE=sqlite` |
| PostgreSQL in this stack | Container `headscale-postgresql` (`postgres` Compose profile, volume `headscale-db`), not published | `HEADSCALE_DB_TYPE=postgres`, `HEADSCALE_PG_EXTERNAL=false` |
| Your own PostgreSQL | Nothing extra; you give host, port, database, owner user and password, and the TLS mode | `HEADSCALE_DB_TYPE=postgres`, `HEADSCALE_PG_EXTERNAL=true` |

- **Switching does not migrate data.** Moving an existing install between
  SQLite and PostgreSQL starts Headscale with an empty database: users and
  machines have to be created and registered again. The installer warns and
  asks before switching.
- **The web UI reads with a read-only role.** It needs the Hostinfo devices
  report (OS, Tailscale version, DERP relay, endpoints), which Headscale's API
  does not expose. The installer creates `HEADSCALE_PG_RO_USER`
  (`headscale_ro`) with
  [`templates/headscale-pg-readonly.sql`](https://github.com/insanerask77/headscale-easy/blob/main/templates/headscale-pg-readonly.sql):
  it may only `SELECT` the `id`, `host_info` and `endpoints` columns of
  `nodes` (no keys, no other tables) and its sessions are read-only. The UI
  never gets Headscale's own credentials. It talks to PostgreSQL with a small
  built-in client (standard library only: SCRAM-SHA-256 or MD5, optional
  TLS), so the image still has no third-party packages.
- **Your own server:** the database must exist and its owner must be the user
  you give (Headscale creates its tables with it). To create the read-only
  role the installer runs that SQL as the owner, which needs the `CREATEROLE`
  privilege; if it cannot, it prints the command to run as a superuser.
  `HEADSCALE_PG_SSLMODE` (`disable`, `prefer`, `require`, `verify-ca`,
  `verify-full`) applies to Headscale, the web UI and backups.
- If a Headscale upgrade recreates the `nodes` table, the OS and version
  columns turn empty and the web UI logs `permission denied`: run
  `./install.sh` again to re-apply the grant.
- Backups use `pg_dump` (see [Operations → Backups](operations.md#backups)).

## Language

The console follows the browser's language (English or Spanish) and each person
can switch it in **Settings → General**. `UI_LANG` sets the default when the
browser asks for a language the console does not have.

## Generated files

| File | Written by | Notes |
|---|---|---|
| `.env` | installer | All settings and secrets (`chmod 600`) |
| `headscale-config.yaml` | installer | Except the DNS block, managed by the console |
| `Caddyfile` | installer | |
| `docker-compose.override.yml` | installer | Caddy's ports, how containers reach the public URL |
| `reverse-proxy/*` | installer | Only with `SSL_MODE=front` |
| `caddy-root-ca.crt` | installer | Only with `SSL_MODE=selfsigned` |

All of them are in `.gitignore`. Do not edit them by hand: re-run the installer.

## `.env` reference

See [`.env.example`](https://github.com/insanerask77/headscale-easy/blob/main/.env.example): every variable, documented.
