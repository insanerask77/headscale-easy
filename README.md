<p align="center">
  <img src="web/static/favicon.svg" width="72" alt="Headscale Easy logo">
</p>

<h1 align="center">Headscale Easy</h1>

<p align="center">
  <b>The open source Tailscale alternative.</b><br>
  Your own <a href="https://github.com/juanfont/headscale">Headscale</a> control server with a built-in web console that looks and feels like Tailscale's —
  user accounts, Google sign-in and HTTPS included. One command to install.
</p>

<p align="center">
  <a href="https://github.com/insanerask77/headscale-easy/actions/workflows/ci.yml"><img src="https://github.com/insanerask77/headscale-easy/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/insanerask77/headscale-easy/releases"><img src="https://img.shields.io/github/v/release/insanerask77/headscale-easy?sort=semver" alt="Release"></a>
  <a href="https://github.com/insanerask77/headscale-easy/pkgs/container/headscale-easy"><img src="https://img.shields.io/badge/image-ghcr.io-blue?logo=docker" alt="Docker image"></a>
  <a href="https://insanerask77.github.io/headscale-easy/"><img src="https://img.shields.io/badge/docs-github.io-3f6fe0" alt="Documentation"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
  <a href="https://github.com/insanerask77/headscale-easy/stargazers"><img src="https://img.shields.io/github/stars/insanerask77/headscale-easy?style=social" alt="GitHub stars"></a>
  <a href="https://buymeacoffee.com/insanerask"><img src="https://img.shields.io/badge/Buy%20me%20a%20coffee-☕-FFDD00" alt="Buy me a coffee"></a>
</p>

<p align="center">
  <b>English</b> · <a href="README.es.md">Español</a>
</p>

<p align="center">
  <img src="docs/images/machines-dark.png" alt="Headscale Easy — Machines" width="900">
</p>

---

Tailscale is wonderful, but its coordination server is closed and hosted by
them. [Headscale](https://github.com/juanfont/headscale) is the open source
implementation of that server — but it only has a command line, and wiring up
HTTPS, users and single sign-on by hand takes an afternoon.

**Headscale Easy** is what [wg-easy](https://github.com/wg-easy/wg-easy) is to
WireGuard: Headscale plus everything around it, installed and configured by one
interactive script, managed from a web console modelled on Tailscale's admin
panel. Use the official Tailscale apps on every device; only the server is yours.

## ✨ Features

- 🖥️ **Tailscale-style web console** at `/admin`: machines, users, DNS, access
  controls, keys. Dark and light themes, works on phones.
- 👤 **Real user accounts** with passwords (built-in [Authentik](https://goauthentik.io))
  and optional **"Sign in with Google"** — or plug in your own OIDC provider
  (Keycloak, Authelia, Google…).
- 🔒 **Every user gets their own private VPN**: members only see and reach their
  own devices (ACL `autogroup:self`); admins manage everything.
- 📱 **Machines**: status, addresses, OS and client version with update hints,
  rename, expire, remove, key expiry, tags, **subnet routes and exit nodes**,
  filters, search and CSV export.
- 🔑 **Auth keys** (one-off, reusable, ephemeral) and API keys; register
  devices by auth ID.
- 🌐 **DNS**: MagicDNS, tailnet domain, nameservers, split DNS, search domains —
  validated with `headscale configtest` and rolled back if Headscale refuses them.
- 📝 **Policy editor** for the HuJSON ACL policy.
- 🔐 **HTTPS your way**: Let's Encrypt, self-signed, behind your existing proxy
  (Nginx Proxy Manager, nginx, Traefik, Caddy — the snippet is generated for
  you), or plain HTTP on a LAN.
- 🌍 **English and Spanish** in the installer and the console (more welcome!).
- 🪶 **Lightweight**: the console is plain Python standard library, no
  build step, no JavaScript framework, no database of its own.

## 📸 Screenshots

| Machines (light) | Machine details |
|---|---|
| ![Machines, light theme](docs/images/machines-light.png) | ![Machine details](docs/images/machine-detail.png) |
| **Users** | **DNS** |
| ![Users](docs/images/users.png) | ![DNS](docs/images/dns.png) |
| **Policy editor** | **Keys** |
| ![Policy editor](docs/images/access-controls.png) | ![Keys](docs/images/keys.png) |
| **Sign in (Authentik, themed)** | **Add device** |
| ![Sign in](docs/images/sign-in.png) | ![Add device](docs/images/add-device.png) |

## 🚀 Quick start

You need a Linux host with Docker (the installer can install it for you) and,
for real HTTPS, a domain pointing at it.

```bash
git clone https://github.com/insanerask77/headscale-easy.git
cd headscale-easy
./install.sh
```

The installer asks a handful of questions (language, domain, who handles HTTPS,
how users sign in), writes the configuration, starts everything and prints your
URLs and first credentials. Run it again at any time to change settings — your
data is kept.

Then open `https://your-domain/admin`, and connect devices with the official
Tailscale app:

```bash
tailscale up --login-server=https://your-domain
```

On phones and desktop apps, choose **"Use an alternate server"** / **"Change
server"** and enter the same URL. The console's **Add device** page shows the
exact steps for each OS.

### Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 80 / 443 | TCP | Web console, control plane, Let's Encrypt |
| 3478 | UDP | STUN for the embedded DERP relay (must be reachable) |

## 🧩 How it works

```
                        ┌─────────────── your server ───────────────┐
 Tailscale apps ──────▶ │ Caddy ─┬─ /            → Headscale         │
 Browser ─────────────▶ │        ├─ /admin       → Headscale Easy UI │
                        │        └─ /authentik   → Authentik (opt.)  │
                        └───────────────────────────────────────────┘
```

| Container | Image | Role |
|-----------|-------|------|
| `headscale` | `headscale/headscale` | Coordination server (control plane + DERP) |
| `headscale-easy` | `ghcr.io/insanerask77/headscale-easy` | Web console |
| `caddy` | `caddy` | Reverse proxy, HTTPS |
| `authentik-*` | `ghcr.io/goauthentik/server`, `postgres` | Accounts and SSO (optional) |

Everything lives on one domain. The console talks to Headscale's REST API with
an API key the installer creates; it reads each device's OS and client version
from Headscale's database (read-only) and, for DNS changes only, uses the
Docker socket to validate and restart Headscale.

## 📚 Documentation

**📖 [insanerask77.github.io/headscale-easy](https://insanerask77.github.io/headscale-easy/)** — English and Spanish.

- [Getting started](https://insanerask77.github.io/headscale-easy/getting-started/) — requirements, install, first device.
- [Configuration](https://insanerask77.github.io/headscale-easy/configuration/) — installer options, HTTPS modes, front
  proxies, sign-in providers, DNS, `.env` reference.
- [Operations](https://insanerask77.github.io/headscale-easy/operations/) — users and admins, machines, updates, backups,
  troubleshooting.
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md)

## 🙌 Support the project

Headscale Easy is built and maintained in my spare time by
**[Rafa Madolell](https://github.com/insanerask77)**. If it saves you time or
money:

- ⭐ **Star the repo** — it really helps others find it.
- ☕ **[Buy me a coffee](https://buymeacoffee.com/insanerask)**.
- 🐛 Report bugs, suggest features, or send a pull request.
- 🌍 Translate the console into your language (see [CONTRIBUTING](CONTRIBUTING.md#translations)).

<a href="https://buymeacoffee.com/insanerask"><img src="https://img.shields.io/badge/Buy%20me%20a%20coffee-insanerask-FFDD00?style=for-the-badge&logo=buymeacoffee&logoColor=black" alt="Buy me a coffee"></a>

## ⚖️ License and credits

[MIT](LICENSE) © 2026 [Rafa Madolell](https://github.com/insanerask77).

Built on the shoulders of [Headscale](https://github.com/juanfont/headscale),
[Caddy](https://caddyserver.com) and [Authentik](https://goauthentik.io).
Icons from [Lucide](https://lucide.dev), font [Inter](https://rsms.me/inter/) —
see [third-party notices](THIRD_PARTY_NOTICES.md).

Headscale Easy is an independent project, not affiliated with or endorsed by
Tailscale Inc. or the Headscale project. "Tailscale" is a trademark of
Tailscale Inc.
