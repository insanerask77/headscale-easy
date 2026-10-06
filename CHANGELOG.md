# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- Docker tab: the exit node of a container survives the first deploy (#83). It is generated as
  `TS_ROUTES=0.0.0.0/0,::/0` (plus any subnet routes) instead of `--advertise-exit-node` in `TS_EXTRA_ARGS`.
  With `TS_AUTH_ONCE=true` the image skips `tailscale up`, the only place `TS_EXTRA_ARGS` applies, so ticking
  the exit node on a container that had already signed in was ignored.
- Docker tab: a container on a host whose kernel has only nftables no longer registers as an exit node that
  forwards nothing (#84). The snippets set `TS_DEBUG_FIREWALL_MODE=auto`; the image defaults to an iptables
  backend that such a kernel lacks.

### Added
- Docker tab: a yellow "Troubleshooting and tips" dropdown with eleven entries (no connection, spent key,
  no Internet, an exit node that does not route or does not appear, options that do not change, subnets that
  do not show, IP forwarding, missing permissions, DNS, relays), in every language, with commands that use the
  container's name. The same text is in the docs, under "Docker devices".
- Docker tab: the command that applies the form's options to a container that already runs (#87), including
  withdrawing a route or an exit node.
- Docker tab: the auth key goes in a separate `.env` block, not in `docker-compose.yml` (#88).
- Docker tab: the image is pinned to a tested Tailscale version; `latest` is an explicit choice (#89).
- Docker tab: options for a container that uses another device as exit node or accepts subnet routes (#86).
- Machines: an exit node or subnet routes waiting for approval say so in the badge, can be approved in one
  click, and administrators see a banner counting them (#85).

## [2.0.1] - 2026-10-06

### Fixed
- Docker tab of Add device: an administrator can generate the single-use auth key the first time. The owner
  picker used to appear only after an error, so the first attempt failed with "There is no Headscale user to
  own the key".

### Added
- Docker tab: with an exit node or routes in kernel networking, a note lists what to try when they do not
  route, from Tailscale's exit node guide: approve the node, enable IPv4 and IPv6 forwarding on the host, or use
  userspace networking.

## [2.0.0] - 2026-10-06

Headscale Easy 2.0 is one container: Headscale, Caddy and the console, supervised together, installed
with a Docker Compose file. The image is about 232 MB and uses about 72 MB of RAM at rest.

### End of 1.x
- Headscale Easy **1.x is discontinued** as of this release: 1.5.0 is the last 1.x version and it receives no
  more fixes, security fixes included. There is no in-place upgrade and no conversion tool: install 2.0 as a
  new deployment. See [1.x is discontinued](https://insanerask77.github.io/headscale-easy/1x-end-of-life/).

### Install
- A single `compose.yaml` and an optional `.env`: `docker compose up -d`, then open the setup wizard.
  The image is pinned to the release (`HSE_VERSION`); no Docker socket, no added capability, no root.
- The first-run wizard asks the language, the public URL, HTTPS (Let's Encrypt, internal CA or a proxy in
  front), the administrator (with two-factor authentication), the tailnet name and its MagicDNS domain,
  the DERP relay, who may sign up, and the backups. Every answer can also be given as `HSE_*` variables for
  a headless start.
- Headscale runs unmodified, pinned to a tested version.

### Sign-in and accounts
- Local accounts: password (scrypt), TOTP with recovery codes, invitations, password-reset links, roles
  (administrator, network administrator, auditor, member), revocable sessions and sign-in rate limiting.
  Invitation and reset links are shown once and can be sent by e-mail with `SMTP_*`.
- Sign-up from the sign-in page: off, by invitation key, or open.
- Device sign-in without an identity provider: the link `tailscale up` prints opens the console, which asks
  you to sign in and approve the device.
- An external OIDC provider is optional: see Advanced configurations.

### Network
- Embedded DERP/STUN relay by default (no third-party relay needed), a custom DERP map, or Tailscale's
  public relays.
- Network isolation per user, an ACL policy editor, DNS and MagicDNS editor, device key expiry, live
  device status, a Docker tab in Add device, an activity log, webhook notifications, a status page.
- English, Spanish, French, German and Portuguese.

### Backups
- A nightly backup (consistent SQLite copies, keys, configuration, Caddy's CA) kept for 14 days, `hse
  backup` and `hse restore`, a Backups page with upload, download and restore, and an optional sidecar
  that uploads each backup to S3, B2, SFTP or a server.

### Advanced configurations
- Compose overlays under `advanced/`: external PostgreSQL (with a read-only role for the console, and an
  optional bundled server), a proxy in front (nginx, Traefik, Caddy, Nginx Proxy Manager), remote backups,
  and OIDC providers (Authentik, Pocket ID, Keycloak, Google).
- `HSE_TRUSTED_PROXIES` gives the console and Headscale the real client address behind a proxy;
  `HSE_OIDC_ALLOWED_*` and `PORTAL_*_GROUPS` say who may sign in and who is what.

### Releases
- Images are published as `ghcr.io/insanerask77/headscale-easy` and `headscale-easy-backup` with the tags
  `X.Y.Z`, `X.Y`, `X` and `latest`. `VERSION` is the single source of the version; CI checks that the
  compose file, the image and this changelog agree.
