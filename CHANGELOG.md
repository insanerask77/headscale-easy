# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-09-28

First release as **Headscale Easy**.

### Added
- Web console modelled on Tailscale's admin panel, at `/admin`: machines
  (status, addresses, OS and client version, rename, expire, remove, key
  expiry, tags, subnet routes, exit nodes, filters, search, CSV export), users,
  DNS editor, ACL policy editor, auth and API keys, register by auth ID.
- Members see and manage only their own devices; admins manage everything.
- English and Spanish, in the installer and the console.
- Docker image `ghcr.io/insanerask77/headscale-easy` (amd64, arm64).
- Built-in Authentik with a themed login, a simple add-user form at `/add-user`
  and optional Google sign-in.
- Per-user network isolation policy (`autogroup:self`) on new installs.
- Ready-made configuration for Nginx Proxy Manager, nginx, Traefik and Caddy
  when another proxy terminates TLS.
- Documentation site on GitHub Pages, in English and Spanish:
  https://insanerask77.github.io/headscale-easy/

### Changed
- Headplane is no longer part of the stack: the console replaces it. Existing
  installations are migrated by `./install.sh` (the `headplane-data` volume is
  kept).
- The console moved from `/mi-vpn` to `/admin` (old links redirect).

[1.0.0]: https://github.com/insanerask77/headscale-easy/releases/tag/v1.0.0
