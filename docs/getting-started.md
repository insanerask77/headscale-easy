# Getting started

## In five minutes

1. **Download:** `mkdir headscale-easy && cd headscale-easy && curl -fsSLO https://raw.githubusercontent.com/insanerask77/headscale-easy/main/compose.yaml`
2. **Start it:** `docker compose up -d`. One container, version 2.0 pinned in the file.
3. **Set it up:** open `http://<your-server>/console/setup` and enter the one-time token (from
   `docker compose logs`): administrator, public address and HTTPS, tailnet name, relay (DERP),
   sign-up and backups.
4. **Log in:** open `https://<your-domain>/console`.
5. **Connect your first device:** `tailscale up --login-server=https://<your-domain>`
   ([details below](#connect-your-first-device)).

This gets you a working server. Before relying on it — or exposing it to the
Internet for other people — go through
[Production and hardening](hardening.md).

## Requirements

- A Linux host (a small VPS is plenty: 1 vCPU and 1 GB of RAM; the container
  idles under 100 MB).
- Docker with the Compose plugin (v2.24 or newer).
- For real HTTPS: a domain name pointing at the host, and ports 80/443 open.
- **UDP 3478** reachable from the Internet (STUN for the embedded DERP relay).

## Install

```bash
mkdir headscale-easy && cd headscale-easy
curl -fsSLO https://raw.githubusercontent.com/insanerask77/headscale-easy/main/compose.yaml
docker compose up -d
```

`compose.yaml` is the whole app: one service, two named volumes (`hse-data` and
`hse-backups`), no added capability, no Docker socket. Open the setup wizard and answer
there; the one-time token is in `docker compose logs headscale-easy`.

To answer in advance (or to start unattended), put the answers in a `.env` next to it:

```bash
curl -fsSL https://raw.githubusercontent.com/insanerask77/headscale-easy/main/.env.example -o .env
chmod 600 .env        # then uncomment and edit what you need
```

For example `HSE_PUBLIC_URL`, `HSE_TLS` (`auto`: Let's Encrypt, needs `ACME_EMAIL`; `internal`:
a certificate of Caddy's own CA; `off`: plain HTTP, or a proxy you already run terminates
TLS), and `HSE_ADMIN_EMAIL` with `HSE_ADMIN_PASSWORD` to skip the wizard: the server then
starts with that account.

!!! tip "Updating"
    Change `HSE_VERSION` in `.env` (or the tag in `compose.yaml`), then
    `docker compose pull && docker compose up -d`. The data lives in the volumes and is
    never touched; take a backup first with `docker exec headscale-easy hse backup`.

Everything else — an identity provider, a proxy in front, PostgreSQL, remote
backups — is in [Advanced configurations](advanced/index.md). See
[All-in-one image](all-in-one.md) and [Configuration](configuration.md) for every setting.

## Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 80 / 443 | TCP | Web console, control plane, Let's Encrypt |
| 3478 | UDP | STUN for the embedded DERP relay |

## Connect your first device

Open `https://<your-domain>/console`, sign in, and click **Add device**: it shows
the steps for each OS. In short:

=== "Linux"
    ```bash
    curl -fsSL https://tailscale.com/install.sh | sh
    sudo tailscale up --login-server=https://<your-domain>
    ```
    Open the URL it prints and sign in.

=== "Windows / macOS"
    Install the Tailscale app, then in its menu choose **Settings → Change
    server** (macOS: hold ++option++ and click the menu icon) and enter
    `https://<your-domain>`. Sign in when the browser opens.

=== "iOS / Android"
    Install the Tailscale app, tap the profile icon (top right) and then
    *Log in*. Open the menu in the top-right corner (iOS: ⋯ → *Use a custom
    coordination server*; Android: ⋮ → *Use an alternate server*), enter
    `https://<your-domain>` and tap *Log in*. A publicly trusted certificate
    (Let's Encrypt or your own proxy) is required.

    The **Add device** page shows the URL as a QR code: scan it with the
    phone's camera to get it on the phone and paste it in the app (the
    Tailscale app cannot read QR codes itself).

=== "Servers (auth key)"
    Generate a key in **Settings → Keys**, then:
    ```bash
    sudo tailscale up --login-server=https://<your-domain> --authkey=<key>
    ```
    The new key can also be shown as QR codes (the key and this command). On
    Android, paste a key with ⋮ → *Use an auth key* after setting the server.

Once connected, the device shows up in **Machines**. Next: add people
([Operations](operations.md#users-and-admins)) and adjust
[access controls](configuration.md#network-isolation-and-acls).
