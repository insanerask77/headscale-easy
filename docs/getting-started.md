# Getting started

## In five minutes

1. **Clone:** `git clone https://github.com/insanerask77/headscale-easy.git && cd headscale-easy`
2. **Run the installer:** `./install.sh`
3. **Configure the domain:** enter the domain that points at this server and
   choose who handles HTTPS (Let's Encrypt is the default).
4. **Configure sign-in:** built-in Authentik (accounts, two-factor, optional
   Google), your own OIDC provider, or none (API key only).
5. **Log in:** open `https://<your-domain>/admin` with the credentials the
   installer prints.
6. **Connect your first device:** `tailscale up --login-server=https://<your-domain>`
   ([details below](#connect-your-first-device)).

This gets you a working server. Before relying on it — or exposing it to the
Internet for other people — go through
[Production and hardening](hardening.md).

## Requirements

- A Linux host (a small VPS is plenty: 1 vCPU, 1 GB RAM without Authentik,
  2 GB with it).
- Docker with the Compose plugin — the installer offers to install it.
- For real HTTPS: a domain name pointing at the host, and ports 80/443 open.
- **UDP 3478** reachable from the Internet (STUN for the embedded DERP relay).

## Install

```bash
git clone https://github.com/insanerask77/headscale-easy.git
cd headscale-easy
./install.sh
```

The installer asks a handful of questions — language, domain, who handles HTTPS,
how users sign in — then writes the configuration, starts everything and prints
your URLs and first credentials:

```text
  Web UI:        https://vpn.example.com/admin/
  Control plane: https://vpn.example.com

  Sign in:
    User: akadmin   Password: ••••••••••••
    Add people at https://vpn.example.com/add-user or from Users in the web UI.
```

!!! tip "Changing settings later"
    Run `./install.sh` again at any time. Your previous answers become the
    defaults and no data is lost.

!!! note "Unattended install"
    `./install.sh --non-interactive` asks nothing: every answer is its default,
    taken from an existing `.env` or else from environment variables with the
    same names as in `.env`, for example
    `DOMAIN=vpn.example.com SSL_MODE=letsencrypt AUTH_PROVIDER=authentik ./install.sh --non-interactive`.
    An invalid value stops it instead of asking again. The end-to-end tests
    install the stack this way.

See [Configuration](configuration.md) for every option.

## Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 80 / 443 | TCP | Web console, control plane, Let's Encrypt |
| 3478 | UDP | STUN for the embedded DERP relay |

## Connect your first device

Open `https://<your-domain>/admin`, sign in, and click **Add device**: it shows
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
