# Getting started

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
    Install the Tailscale app. On the sign-in screen open the menu (Android:
    ⋮ → *Change server*; iOS: *Log in* → ⚙ → *Use an alternate server*) and
    enter `https://<your-domain>`. A publicly trusted certificate
    (Let's Encrypt or your own proxy) is required.

=== "Servers (auth key)"
    Generate a key in **Settings → Keys**, then:
    ```bash
    sudo tailscale up --login-server=https://<your-domain> --authkey=<key>
    ```

Once connected, the device shows up in **Machines**. Next: add people
([Operations](operations.md#users-and-admins)) and adjust
[access controls](configuration.md#network-isolation-and-acls).
