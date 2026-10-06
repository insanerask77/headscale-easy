# Advanced edition

The all-in-one image is the only thing Headscale Easy builds. The advanced edition is the same
image plus whatever you already run: an identity provider, a proxy in front, somewhere to keep
backups. Nothing here needs a different build, and nothing here is needed to start: the
[quick start](getting-started.md) ends in a working tailnet.

Everything lives in [`deploy/`](https://github.com/insanerask77/headscale-easy/tree/next/deploy):

| What | Where |
|---|---|
| The compose file and its profiles | `deploy/compose/` |
| A proxy in front (nginx, Traefik, Caddy, Nginx Proxy Manager) | `deploy/examples/front-proxy/` |
| Authentik as an external identity provider | `deploy/examples/authentik/` |
| Pocket ID, Keycloak, Google | `deploy/examples/pocket-id/`, `keycloak.md`, `google.md` |

Each example says exactly what was run and where, and what was only read.

## The compose file

`deploy/compose/docker-compose.yml` is the installer's output with comments: one service, two
named volumes (the data and the backups), no Linux capability, `no-new-privileges`, log rotation.
Copy `.env.example` to `.env`, set `HSE_PUBLIC_URL`, and `docker compose up -d`. The setup wizard
prints a one-time token in `docker compose logs`; or set `HSE_ADMIN_EMAIL` and
`HSE_ADMIN_PASSWORD` to start with that account.

`docker compose --profile backup-remote up -d` adds a sidecar that uploads each new backup to S3,
B2, SFTP (rclone) or a server (rsync over SSH). It sees only the backups, read-only, and uploads
each one once. See [All-in-one: where the backups go](all-in-one.md#where-the-backups-go).

## A proxy in front

If nginx, Traefik, Caddy or Nginx Proxy Manager already terminates HTTPS on this host:

1. `HSE_PUBLIC_URL=https://vpn.example.com` and **`HSE_TLS=off`**. An `https://` URL with TLS off
   is what tells the image a proxy is in front: it forces `X-Forwarded-Proto: https` upstream and
   leaves HSTS to the proxy.
2. Forward everything, including WebSocket upgrades (`/ts2021` is one), to port 80 of the
   container. The files in `deploy/examples/front-proxy/` have the headers.
3. **`HSE_TRUSTED_PROXIES=<the proxy's address>/32`**, so the console and Headscale see the real
   client address. List the proxy itself, **never the whole network it lives in**: a subnet that
   also contains the clients lets anyone write their own address in `X-Forwarded-For` and be
   believed. Without it everything works but every sign-in is counted against the proxy's address,
   so one person's wrong passwords lock everybody out.
4. Publish **UDP 3478** straight to the container. DERP/STUN is not HTTP: a proxy cannot carry it.

`HSE_TRUSTED_PROXIES` takes up to 16 comma-separated addresses or CIDRs; a `/0` entry is refused
unless `HSE_TRUSTED_PROXIES_ANY=1` says it is meant.

## PostgreSQL

SQLite is the default and is enough for most installs. Use an external PostgreSQL when you already run
one or want the database off the host: `HEADSCALE_DB_TYPE=postgres`, `HEADSCALE_PG_HOST`,
`HEADSCALE_PG_NAME`, `HEADSCALE_PG_USER`, `HEADSCALE_PG_PASS` (and `HEADSCALE_PG_SSLMODE`, which
defaults to `disable`: use `require` or `verify-full` for a server you reach over a network).
`deploy/examples/postgresql/` is a compose file with PostgreSQL and the image.

- **Two roles.** Headscale writes as the owner. The console only needs what each device reports about
  itself, so give it `HEADSCALE_PG_RO_USER` and `HEADSCALE_PG_RO_PASS`: the image creates that role
  itself (it can `SELECT` three columns of one table, in read-only transactions) before the console
  starts, and the console never holds the owner's credentials. Without them the console connects as the
  owner and the log says so. If the owner may not create roles, the image logs an `ERROR` and falls back
  to the owner; create the role by hand with the SQL in the example.
- **Backups** run `pg_dump` (a PostgreSQL 18 client is in the image) and restore with `psql`; a
  restore from the console loads the dump when both the install and the backup use PostgreSQL. A
  `pg_dump` dumps servers up to its own major version: 13 to 18 here. For a newer server the backup
  fails with a sentence that says so: use a PostgreSQL 18 or older server.
- **No SQLite to PostgreSQL conversion**: Headscale has no tool for it. Choose before adding devices.

## Identity providers

Set `OIDC_ISSUER`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` and people sign in to the console and
register devices through your provider. Redirect URIs: `https://<domain>/admin/callback` for the
console and `https://<domain>/oidc/callback` for Headscale.

| Variable | What |
|---|---|
| `HSE_OIDC_ALLOWED_DOMAINS`, `HSE_OIDC_ALLOWED_USERS`, `HSE_OIDC_ALLOWED_GROUPS` | Who may sign in (Headscale's `oidc.allowed_*`), comma-separated. **Empty means everyone the provider lets in**: right for your own Authentik or Keycloak, wrong for Google, where any account qualifies |
| `PORTAL_ADMIN_GROUPS`, `PORTAL_NETWORK_ADMIN_GROUPS`, `PORTAL_AUDITOR_GROUPS` | Who is what in the console, by group (the groups your provider sends) |
| `PORTAL_ADMIN_EMAILS` | Administrators by e-mail, for providers without groups |
| `OIDC_SCOPE` | Scopes asked at sign-in (default `openid profile email`; Pocket ID releases groups only for `groups`) |

### Authentik

Authentik is one more OIDC provider: the console and Headscale trust its identity and groups, and
manage nothing inside it (invitations, resets and two-factor are the console's local accounts).
Headscale identifies an OIDC user by the provider's **issuer URL** plus the user's id, so pick the
issuer address once and keep it. To serve Authentik under your own domain at
`https://<domain>/authentik/`, run it next to the container and set
`HSE_AUTHENTIK_UPSTREAM=authentik-server:9000` (Caddy then routes `/authentik` there).
`deploy/examples/authentik/` has the compose file, the blueprint for the OIDC application and groups,
and the steps.
