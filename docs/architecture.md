# Architecture and resources

Headscale Easy is **not a fork or a replacement of Headscale**. It runs the
official, unmodified `headscale` binary and adds a deployment and management
layer around it: HTTPS, local accounts, a web console and backups, all in **one
container**.

## Components

```text
               ┌──────────────── headscale-easy (one container) ────────────────┐
 :80/:443 ───▶ │ caddy ──┬─ /       ──▶ headscale  (official binary, child proc)  │
 :3478/udp ──▶ │         └─ /admin  ──▶ console    (Python, standard library)    │
               │                                                                 │
               │ supervisor: starts, restarts and stops the three, runs          │
               │ configtest and backups                                          │
               │                                                                 │
               │ /data: headscale/ caddy/ console/ config/ backups/              │
               └─────────────────────────────────────────────────────────────────┘
       optional, outside: OIDC provider · PostgreSQL · front proxy · remote backups
```

| Component | What it does | What it does **not** do |
|---|---|---|
| **Headscale** | The coordination server: node registration, keys, IP addresses, ACL enforcement, MagicDNS, the embedded DERP relay and STUN | — it is the part that does the real work |
| **Console** | Web console at `/admin`: machines, users, keys, routes, DNS, access controls, backups, activity log. Local accounts with password and two-factor, invitations and sign-up. Talks to Headscale's REST API | Does not touch WireGuard traffic or replace any Headscale logic; if it stops, the tailnet keeps working |
| **Caddy** | Single entry point: HTTPS (Let's Encrypt, internal CA or none), routes `/` to Headscale and `/admin` to the console | — |
| **Supervisor** | PID 1's child (under `tini`). Starts the three processes, restarts a crashed one with backoff, forwards signals, validates the config (`headscale configtest`) and restarts Headscale when the console asks, and runs the scheduled backups. Serves a small Unix-socket protocol to the console | Answers a fixed set of requests (validate the config, restart Headscale, report status): the console cannot ask it to run anything else |
| **Setup wizard** | First-run web wizard served instead of the console until setup is done | Not running once the server is configured |
| **`hse`** | Command-line control: `health`, `reload`, `backup`, `backups`, `restore` | — |

Optional pieces live **outside** the image: an OIDC provider (Authentik, Keycloak,
Pocket ID, Google), an external PostgreSQL, a reverse proxy in front, and the
`backup` sidecar that uploads archives to S3, SFTP or rsync. See the
[advanced edition](advanced.md).

### Who does what

- **Tailscale clients only talk to Headscale.** The console is not in the data
  path: devices keep connecting even if the console is down or removed.
- **The console reads and writes through Headscale's REST API** with its own
  API key, which it renews itself. It also reads each device's OS and client
  version from Headscale's database (read-only; the API does not expose them).
- **DNS and device key expiry** are Headscale configuration file settings, not
  API calls. The console edits a marked block of `config.yaml`, validates it
  with `headscale configtest` and restarts Headscale, rolling back if
  Headscale refuses the change. Both go through the supervisor, inside the same
  container: **there is no Docker socket anywhere**.
- **Sign-in:** local accounts live in the console's own database. With an
  external OIDC provider, Headscale and the console use the same client, so a
  person is the same user in both. Admins come from the account's role, a group
  or a list of e-mails.
- **Everything on one domain:** Headscale at the root (Tailscale clients
  expect that), the console at `/admin` (the same path as Tailscale's own
  console).
- **The container runs as uid 1000, with no added capability.** The three
  processes share it, so a flaw in one reaches the others: the
  [hardening guide](hardening.md) covers what to put around it.

## Resource usage

Measured by `scripts/aio-smoke.sh` on 2026-10-06 (Headscale 0.29.4, Caddy 2.11.4,
x86_64, Docker): a freshly built image, started headless
(`HSE_PUBLIC_URL=http://localhost`, `HSE_TLS=off`), a small test installation (one
administrator, no connected devices). Headscale's memory grows with the number of
devices; the other components barely change with tailnet size.

| | Measured | CI limit |
|---|---:|---:|
| Image size | **232 MB** | 250 MB |
| RAM, idle for 60 s | **72 MB** | 100 MB |
| RAM while a backup runs | **72 MB** | 100 MB |
| Processes in the container | 46 | — |
| Containers | **1** | — |

The CI job fails the build above the limits, so these numbers cannot drift
unnoticed. For comparison, Headscale alone idles at about 20 MB and its image is
about 113 MB: the console, Caddy, the supervisor and the backups add roughly
50 MB of RAM and 120 MB of disk, and replace the reverse proxy, the identity
provider and the helper container you would otherwise run next to it.

- The console uses almost no CPU: in the background it only asks Headscale for the
  device list every 30 seconds (activity log) or 5 seconds (renaming "localhost"
  devices), and more often while someone has a live page open.
- Data on disk is small: Headscale's SQLite database, the activity log
  (`/data/console/audit.db`) and Caddy's logs are a few MB for a small tailnet.
  Backups are one compressed file per night, 14 kept by default.
- A running backup does not raise memory: the archive is streamed and the
  largest backup upload tested (400 MB) peaked at 76 MB.

Measure your own installation with:

```bash
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}'
docker image ls ghcr.io/insanerask77/headscale-easy
docker exec headscale-easy du -sh /data/*
```
