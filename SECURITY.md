# Security

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately through
[GitHub Security Advisories](https://github.com/insanerask77/headscale-easy/security/advisories/new).
You will get an answer within a few days, and credit in the release notes if
you want it.

Vulnerabilities in Headscale, Authentik or Caddy themselves should go to those
projects; tell us too if Headscale Easy's configuration makes them worse.

## Supported versions

Only the latest release receives fixes.

## Security model

- **One domain, one entry point.** Only Caddy publishes TCP ports (80/443, or
  80 behind your own proxy). Headscale's API, metrics and gRPC ports and
  Authentik are only reachable inside the Docker network. UDP 3478 is published
  for STUN.
- **Console sessions** are HMAC-signed, `HttpOnly`, `SameSite=Lax` cookies
  (`Secure` over HTTPS). Every form carries a CSRF token. A strict
  Content-Security-Policy blocks inline scripts and third-party resources.
- **Members are isolated.** The console only shows a member their own machines
  and keys, and checks ownership on every action. With `NETWORK_ISOLATION=true`
  the ACL policy also isolates them at the network level.
- **The Headscale API key** used by the console lives in `.env` (`chmod 600`)
  and expires (90 days by default). The console renews it 15 days before it
  expires, keeps the new one in `data/web/api-key` (`chmod 600`) and expires
  the old one.
- **The Docker socket.** The console mounts `/var/run/docker.sock` to validate
  (`headscale configtest`) and restart Headscale after a DNS change. Access to
  the socket is equivalent to root on the host. The console only calls those
  two operations on the `headscale` container, runs as an unprivileged user
  with a read-only filesystem, no capabilities and `no-new-privileges`, and
  only admins can reach the DNS form. If you do not need DNS editing from the
  console, remove the socket volume from the `web` service: everything else
  keeps working and DNS changes then show an error.
- **Headscale's database** is mounted read-only in the console (it reads each
  device's OS and client version, which the API does not expose).
- **Secrets** (`.env`, generated configuration, backups) never belong in git;
  they are all in `.gitignore`.
