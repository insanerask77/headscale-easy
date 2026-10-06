# Why Headscale Easy?

## Headscale works. The work is around it.

[Headscale](https://github.com/juanfont/headscale) is a solid, mature
open-source implementation of Tailscale's coordination server, and many people
run it directly with its command line and configuration file. Headscale Easy
does not try to improve or replace it.

What takes time is everything **around** Headscale when you want a complete,
self-hosted setup for several people:

> auth + DNS + HTTPS + device management + backups = a weekend project

- **HTTPS:** a reverse proxy, certificates, WebSockets and the DERP relay
  working through it.
- **Accounts and sign-in:** an OIDC provider, clients and redirect URIs for
  Headscale, groups for admins, two-factor, invitations and password resets.
- **Per-user isolation:** an ACL policy so each person only reaches their own
  devices.
- **DNS:** MagicDNS, nameservers, split DNS, custom records — edited in YAML and
  applied with a restart.
- **Day-to-day management** for people who will not use a CLI: seeing
  machines, approving routes, creating auth keys, removing old devices.
- **Backups** of the database and the private keys, and a restore you have
  actually tested.

Headscale Easy is that glue, packaged:

- **One container:** Headscale, HTTPS and the console in a single image, set up
  from your browser; nothing else to deploy.
- **A web console** for the everyday tasks, modelled on Tailscale's admin
  panel.
- **Centralised configuration** in one place (a few variables or the wizard), on one domain.
- **Auth, DNS, HTTPS and backups** set up for you, with sensible, secure
  defaults.
- **Everything self-hosted**: no external service is required (Google sign-in
  and email are optional).

If you are happy running Headscale by hand, you do not need this project.

## What it is, and what it is not

| Headscale Easy **is** | Headscale Easy **is not** |
|---|---|
| A deployment and management layer around the official Headscale | A fork or a replacement of Headscale |
| One container: Caddy + local accounts + a web console + backups | A new coordination server or a new VPN protocol |
| Opinionated: one domain, one container, one server | A tool for every topology (Kubernetes, multi-server, existing Headscale installs) |
| Removable: devices keep working if the console is stopped | In the data path of your traffic |

See [Architecture and resources](architecture.md) for how the pieces fit and
what they cost in RAM and disk.

## Headscale Easy and Headplane

[Headplane](https://github.com/tale/headplane) is an established,
feature-complete web UI for Headscale, and a good choice. The two projects
overlap in the web UI and differ in scope: Headplane is a **UI you add to a
Headscale you already run**; Headscale Easy **installs and wires the whole
stack** and includes a UI.

*This comparison is based on each project's public documentation in
September 2026 and is written by the Headscale Easy maintainer. Headplane
changes quickly: check [its documentation](https://github.com/tale/headplane)
for the current state, and open an issue if something here is wrong.*

| | Headplane | Headscale Easy |
|---|---|---|
| **Scope** | Web UI for an existing Headscale | One container: Headscale + reverse proxy + accounts + web UI + backups |
| **Installs Headscale** | No — you bring your own | Yes, the official image, configured |
| **HTTPS** | Up to you | Caddy: Let's Encrypt, self-signed, or snippets for your existing proxy |
| **Identity provider** | Sign in with your OIDC provider | Built-in local accounts (password, 2FA, invitations, password reset, sign-up) or your own OIDC provider |
| **Machines** (rename, expire, routes, owner/tags) | Yes | Yes, plus per-user isolation in the console, expiry warnings and bulk removal of inactive devices |
| **ACL editor** | Yes | HuJSON editor with validation |
| **DNS settings** | Yes (edits Headscale's configuration) | Yes (edits a managed block of `config.yaml`, validates with `configtest`, rolls back on error) |
| **Other Headscale settings** | Yes, broad configuration editing | DNS, DERP relays and device key expiry; the rest through the wizard and a few variables |
| **Users and accounts** | Headscale users | Headscale users + local accounts, invitations, reset links |
| **Backups / restore** | Not in scope | Scheduled backups and one-command restore |
| **Activity log** | — | Configuration changes, sign-ins, device events |
| **Member self-service** | Admin-focused | Members sign in and manage only their own devices and keys |
| **Deployment** | Container next to your Headscale | One container on one server |
| **Maturity** | Established project with many users and contributors | Young (September 2026), one maintainer, AI-assisted, not audited |

**Choose Headplane** if you already run Headscale (or want full control of
each piece) and want a mature UI on top.

**Choose Headscale Easy** if you are starting from scratch and want the
complete setup — HTTPS, accounts, two-factor, DNS, backups — done for you in one
container, and accept a younger project.

## The workflow, end to end

The value is less in any single screen than in the path from an empty server
to a managed tailnet. With Headscale Easy:

| Step | With Headscale Easy | By hand with Headscale |
|---|---|---|
| 1. **Install** | `docker compose up -d`, then the setup wizard (public address, who handles HTTPS) | Write `config.yaml`, a Compose file, reverse proxy config |
| 2. **HTTPS** | Chosen at install; certificates automatic | Configure the proxy, certificates, WebSockets, DERP |
| 3. **Sign-in** | Local accounts out of the box, or your provider's issuer + client | Deploy or configure a provider, clients, redirect URIs, groups |
| 4. **DNS** | DNS page: MagicDNS, nameservers, split DNS, records; validated and applied | Edit YAML, `headscale configtest`, restart |
| 5. **Create a user** | Users → Invite user: the person picks their own password, 2FA as configured | Create accounts in the provider; `headscale users create` for local users |
| 6. **Enroll a device** | Add device page: per-OS steps and a QR code; `tailscale up --login-server=…` and sign in | Same client command; register or create auth keys with the CLI |
| 7. **Manage routes** | Machine → approve subnet routes or exit node | `headscale nodes approve-routes` |
| 8. **Backup** | Nightly, built in; **Backups** menu or `hse restore` | Script SQLite and key copies yourself |

Each step still uses Headscale underneath; the console only
calls its API, its configuration file and its CLI.

The [quick start](getting-started.md) walks through steps 1 and 6; the
[hardening guide](hardening.md) covers what to do before production.
