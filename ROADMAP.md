# Roadmap

What comes next for Headscale Easy, most relevant first. Tick an item when it ships and note the version.
Ideas and votes are welcome in [issues](https://github.com/insanerask77/headscale-easy/issues) and
[discussions](https://github.com/insanerask77/headscale-easy/discussions).

Effort: **S** = hours · **M** = one or two days · **L** = several days.

## Trust

- [ ] **Independent security review** · L
  Ask for a third-party review of sign-in, sessions, two-factor authentication and the backup and restore
  paths; publish the findings and fixes.
- [ ] **End-to-end tests in CI with a real Tailscale client** · L
  Register a device, move traffic through the embedded relay, restore a backup and sign in through an
  external OIDC provider, all against the published image.
- [ ] **Run every advanced configuration against a real provider** · M
  Authentik, Pocket ID, Keycloak and Google sign-in, Traefik and Nginx Proxy Manager in front, and a
  managed PostgreSQL, each one documented as run and not only read.

## Accounts

- [ ] **Passkeys (WebAuthn) for local accounts** · L
  A second factor and a password replacement. Needs a careful, reviewed implementation: it is a large
  surface for our own cryptography code.
- [ ] **Invitations: polish** · S
  Resend, expiry shown in the list, and a QR code for the link.
- [ ] **Account recovery without e-mail** · M
  A documented command (`hse account reset`) for an administrator locked out of a server with no SMTP.

## Operations

- [ ] **`hse proxy-snippet`** · S
  Print a ready-to-paste configuration for nginx, Traefik or Caddy from the current settings.
- [ ] **In-console upgrade check with release notes** · S
  The Status page already shows a newer version; show what changed and the command to upgrade.
- [ ] **Metrics endpoint for Prometheus** · M
  Devices online, backups, sign-in failures.

## Ideas

- A mobile-friendly layout for the device list.
- Per-user device limits.
- Scheduled key-expiry reports by e-mail.
