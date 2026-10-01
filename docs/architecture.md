# Architecture and resources

Headscale Easy is **not a fork or a replacement of Headscale**. It runs the
official, unmodified `headscale/headscale` image and adds a deployment and
management layer around it: an installer, a reverse proxy with HTTPS, an
optional identity provider, a web console and backups.

## Components

```text
Tailscale apps ──▶ Caddy /            ──▶ Headscale (official image)
                    :80/:443                  control plane, DERP, SQLite, keys
                                           ▲
                                           │ REST API, read-only DB
Browser ─────────▶ Caddy /admin       ──▶ Headscale Easy UI
                                           ├─ hs-helper ─▶ Docker socket ─▶ configtest / restart Headscale
                                           └─ Authentik API ─▶ users, invitations, 2FA mode

Browser ─────────▶ Caddy /authentik   ──▶ Authentik ──▶ PostgreSQL      (optional)
                                           OIDC provider for Headscale and the UI

STUN, UDP 3478 ──────────────────────▶ Headscale's embedded DERP relay

backup (optional): daily .tar.gz of databases, keys and configuration ──▶ ./backups
```

| Component | Image | What it does | What it does **not** do |
|---|---|---|---|
| **Headscale** | `headscale/headscale` (official, unmodified) | The coordination server: node registration, keys, IP addresses, ACL enforcement, MagicDNS, embedded DERP relay | — it is the part that does the real work |
| **Headscale Easy UI** | `ghcr.io/insanerask77/headscale-easy` | Web console: machines, users, keys, routes, DNS, ACL editor, activity log. Talks to Headscale's REST API | Does not touch WireGuard traffic or replace any Headscale logic; if it stops, the tailnet keeps working |
| **hs-helper** | `ghcr.io/insanerask77/headscale-easy-helper` | The only container with the Docker socket. Answers three fixed requests from the console on a Unix socket: validate Headscale's config, restart Headscale, report container health and Headscale's version. No network | Takes no parameters: it cannot run other commands or touch other containers |
| **Caddy** | `caddy` | Single entry point, HTTPS (Let's Encrypt or self-signed), routes paths to each service | — |
| **Authentik** (optional) | `ghcr.io/goauthentik/server`, `postgres` | Accounts, passwords, two-factor, Google sign-in, invitations; the OIDC provider for both Headscale and the console | Not needed with your own OIDC provider or with API key sign-in only |
| **backup** (optional) | built locally from `backup/` | Daily consistent copies of Headscale's database and keys, Authentik's database, the configuration and the activity log | — |
| **install.sh** | — | Asks a few questions, writes `.env`, `config.yaml`, the `Caddyfile` and the Authentik blueprint, starts everything | Not needed after installation, except to change settings |

### Who does what

- **Tailscale clients only talk to Headscale.** The console is not in the data
  path: devices keep connecting even if the console is down or removed.
- **The console reads and writes through Headscale's REST API** with its own
  API key, which it renews itself. It also reads each device's OS and client
  version from Headscale's database (read-only; the API does not expose them).
- **DNS and device key expiry** are Headscale configuration file settings, not
  API calls. The console edits a marked block of `config.yaml`, validates it
  with `headscale configtest` and restarts Headscale, rolling back if
  Headscale refuses the change. Both go through `hs-helper`: the console has
  no Docker socket.
- **Sign-in:** Headscale and the console use the same OIDC client, so a person
  is the same user in both. Admins come from a group (`vpn-admins`) or a list
  of emails.
- **Everything on one domain:** Headscale at the root (Tailscale clients
  expect that), the console at `/admin` (the same path as Tailscale's own
  console), Authentik at `/authentik`.

## Resource usage

Measured with `docker stats` on 2026-09-29, Headscale Easy 1.1.0, Headscale
0.29.4, Authentik 2026.8.3, x86_64. **Idle, small test installation** (2
users, no connected devices). Headscale's memory grows with the number of
devices; the other components barely change with tailnet size.

| Container | RAM | CPU (idle) | Processes | Image size |
|---|---:|---:|---:|---:|
| `headscale` | 16–25 MB | < 1 % | 17 | 113 MB |
| `headscale-easy` (console) | 28 MB | < 1 % | 4 | 70 MB |
| `caddy` | 13–15 MB | < 1 % | 16 | 89 MB |
| `authentik-server` (optional) | 570 MB | 1–3 % | 26 | 1.95 GB |
| `authentik-worker` (optional) | 330 MB | < 1 % | 30 | *(same image)* |
| `authentik-postgresql` (optional) | 170 MB | < 5 % | 17 | 420 MB |
| `backup` (optional) | a few MB, runs once a day | — | 1 | 31 MB |

**Overhead over Headscale on its own:**

| Setup | Containers | RAM (idle) | Disk (images) |
|---|---:|---:|---:|
| Headscale alone | 1 | ~20 MB | 113 MB |
| + console + Caddy (no Authentik) | 3 | ~65 MB (**+45 MB**) | ~270 MB |
| + Authentik (accounts, 2FA, Google) | 6 | ~1.1 GB (**+1 GB**) | ~2.6 GB |

- The console and Caddy together add about **45 MB of RAM**; the console uses
  almost no CPU: in the background it only asks Headscale for the device list
  every 30 seconds (activity log) or 5 seconds (renaming "localhost" devices), and more
  often while someone has a live page open. Most self-hosted Headscale
  setups need a reverse proxy with HTTPS anyway.
- **Authentik is the heavy part.** It is optional: with your own OIDC provider
  (`AUTH_PROVIDER=external`) or API key sign-in only (`none`), it is not
  installed. Plan 1 GB of RAM without Authentik and 2 GB with it.
- Data on disk is small: Headscale's SQLite (or PostgreSQL) database, the activity log
  (`data/web/audit.db`, capped by `AUDIT_RETENTION_DAYS`) and Caddy's logs are
  a few MB for a small tailnet. Backups are one compressed file per day.

Measure your own installation with:

```bash
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}'
docker compose images
du -sh data backups
```
