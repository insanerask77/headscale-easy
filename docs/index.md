---
hide:
  - navigation
  - toc
---

<div class="hse-hero" markdown>

![Headscale Easy](assets/logo.svg){ width="72" }

# Headscale Easy

**The open source Tailscale alternative.**<br>
Your own Headscale server with a built-in web console that looks and feels like
Tailscale's — user accounts, Google sign-in and HTTPS included. One command to install.

[Get started](getting-started.md){ .md-button .md-button--primary }
[GitHub](https://github.com/insanerask77/headscale-easy){ .md-button }
[:simple-buymeacoffee: Buy me a coffee](https://buymeacoffee.com/insanerask){ .md-button }

</div>

![Headscale Easy — Machines](images/machines-dark.png){ .hse-shot }

Tailscale is wonderful, but its coordination server is closed and hosted by
them. [Headscale](https://github.com/juanfont/headscale) is the open source
implementation of that server — but it only has a command line, and wiring up
HTTPS, users and single sign-on by hand takes an afternoon.

**Headscale Easy** is what [wg-easy](https://github.com/wg-easy/wg-easy) is to
WireGuard: Headscale plus everything around it, installed by one interactive
script and managed from a console modelled on Tailscale's admin panel. Use the
official Tailscale apps on every device; only the server is yours.

## Features

<div class="grid cards" markdown>

-   :material-monitor-dashboard: **Tailscale-style console**

    Machines, users, DNS, access controls and keys at `/admin`. Dark and light
    themes, works on phones.

-   :material-account-lock: **Real user accounts**

    Passwords with the built-in Authentik, optional **Sign in with Google**, or
    your own OIDC provider.

-   :material-shield-account: **A private VPN per user**

    Members only see and reach their own devices; admins manage everything.

-   :material-router-network: **Routes and exit nodes**

    Approve subnet routes and exit nodes, edit tags, expire keys, rename,
    filter and export machines.

-   :material-dns: **DNS and ACLs**

    MagicDNS, nameservers, split DNS and the HuJSON policy — validated before
    they are applied.

-   :material-lock-check: **HTTPS your way**

    Let's Encrypt, self-signed, behind your existing proxy (config generated),
    or plain HTTP on a LAN.

-   :material-translate: **English and Spanish**

    In the installer and the console. More languages welcome.

-   :material-feather: **Lightweight**

    The console is plain Python standard library: no build step, no JS
    framework, no database of its own.

</div>

## Screenshots

=== "Machine details"
    ![Machine details](images/machine-detail.png){ .hse-shot }
=== "Light theme"
    ![Machines, light theme](images/machines-light.png){ .hse-shot }
=== "Users"
    ![Users](images/users.png){ .hse-shot }
=== "DNS"
    ![DNS](images/dns.png){ .hse-shot }
=== "Policy editor"
    ![Policy editor](images/access-controls.png){ .hse-shot }
=== "Keys"
    ![Keys](images/keys.png){ .hse-shot }
=== "Sign in"
    ![Sign in](images/sign-in.png){ .hse-shot }

## Support the project

Headscale Easy is built and maintained in my spare time by
**[Rafa Madolell](https://github.com/insanerask77)**. If it saves you time or money,
⭐ [star the repository](https://github.com/insanerask77/headscale-easy) and
☕ [buy me a coffee](https://buymeacoffee.com/insanerask).

<small>Headscale Easy is an independent project, not affiliated with Tailscale Inc.
or the Headscale project. "Tailscale" is a trademark of Tailscale Inc.</small>
