# Getting started

## In five minutes

1. **Clone:** `git clone https://github.com/insanerask77/headscale-easy.git && cd headscale-easy`
2. **Run the installer:** `./install.sh`. It asks the public address and who handles
   HTTPS (Let's Encrypt is the default), then starts one container.
3. **Set it up:** open the address it prints and enter the one-time token (from
   `docker compose logs`): administrator, tailnet name, relay (DERP), sign-up and backups.
4. **Log in:** open `https://<your-domain>/admin`.
5. **Connect your first device:** `tailscale up --login-server=https://<your-domain>`
   ([details below](#connect-your-first-device)).

This gets you a working server. Before relying on it — or exposing it to the
Internet for other people — go through
[Production and hardening](hardening.md).

## Requirements

- A Linux host (a small VPS is plenty: 1 vCPU and 1 GB of RAM; the container
  idles under 100 MB).
- Docker with the Compose plugin — the installer offers to install it.
- For real HTTPS: a domain name pointing at the host, and ports 80/443 open.
- **UDP 3478** reachable from the Internet (STUN for the embedded DERP relay).

## Install

```bash
git clone https://github.com/insanerask77/headscale-easy.git
cd headscale-easy
./install.sh
```

The installer asks the public address, who provides HTTPS (`auto`: Let's Encrypt,
and it asks for an e-mail; `internal`: a certificate of its own CA; `off`: plain HTTP,
or a proxy you already run terminates TLS) and, optionally, an administrator e-mail
(and password) to skip the wizard. It writes a compose file and a small `.env` (mode
600) in `./headscale-easy` (`--dir` changes it), runs
`docker compose up -d`, waits until the container is healthy and prints the
address of the setup wizard.

Unattended:

```bash
HSE_PUBLIC_URL=https://vpn.example.com HSE_TLS=auto ACME_EMAIL=me@example.com \
HSE_ADMIN_EMAIL=me@example.com HSE_ADMIN_PASSWORD='a long password' \
  ./install.sh --yes
```

With `HSE_ADMIN_EMAIL` set there is no wizard: the server starts with that account.

!!! tip "Updating"
    Run `./install.sh` again on the same directory: it only pulls the new image and
    recreates the container. The `.env` and the data are never touched.

!!! note "Running 1.x?"
    The 1.x stack (built-in Authentik, one container per piece) keeps its own
    installer, `legacy/install-1x.sh`, until 2.0. The new `install.sh` refuses a
    directory that holds a 1.x install, so the two cannot be mixed up.

Everything else — an identity provider, a proxy in front, PostgreSQL, remote
backups — is in the [advanced edition](advanced.md). See
[All-in-one image](all-in-one.md) for every setting.

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
