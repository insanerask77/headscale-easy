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
6. **Sign-in** — Authentik, your own OIDC provider, or API key only.
7. **Network isolation** — whether every user only reaches their own devices.

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

### Built-in Authentik

- The first admin is `akadmin`; the installer prints its first-start password.
  Change it at `/authentik/if/user/`.
- Add people at **`/add-user`** (or **Users → Add user** in the console): a
  simple form — name, username, email, password and whether they are an admin.
  No need to enter Authentik's admin interface.
- The login pages use the Headscale Easy theme (dark/light following the browser).
- Authentik is configured by the blueprint `authentik/blueprints/headscale.yaml`.
  The installer re-applies it on every run.

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
is never overwritten. Edit it in **Access controls → Policy editor**; the syntax
is [Tailscale's](https://tailscale.com/kb/1337/policy-syntax).

## DNS

Admins edit DNS in the console (**DNS** page): MagicDNS, the tailnet domain,
nameservers, split DNS and search domains. The console writes the `dns:` block
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

This is the only feature that needs the Docker socket; see [Security](security.md).

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
