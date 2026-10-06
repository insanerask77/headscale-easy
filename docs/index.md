---
hide:
  - navigation
  - toc
---

<div class="hse-hero" markdown>

![Headscale Easy](assets/logo.svg){ width="72" }

# Headscale Easy

**Headscale, with everything around it.**<br>
A deployment and management layer for Headscale: a Compose app, HTTPS, accounts
and sign-in, a Tailscale-style web console and backups. One container, one command to install.

[Get started](getting-started.md){ .md-button .md-button--primary }
[GitHub](https://github.com/insanerask77/headscale-easy){ .md-button }
[:simple-kofi: Buy me a coffee on Ko-fi](https://ko-fi.com/rafaelmadolell){ .md-button }

</div>

![Headscale Easy — Machines](images/machines-dark.png){ .hse-shot }

!!! warning "Security notice"
    This is security-sensitive networking software, and a young project with
    one maintainer. AI-assisted development was used extensively
    ([AI usage](ai-usage.md)) and **there has been no independent security
    audit**. Review and harden your deployment before exposing it to the
    Internet: see [Security](security.md) and
    [Production and hardening](hardening.md).

[Headscale](https://github.com/juanfont/headscale) works well on its own;
what takes time is the glue around it — auth, DNS, HTTPS, device management
and backups. **Headscale Easy** runs the official, unmodified Headscale and
packages that glue, much like [wg-easy](https://github.com/wg-easy/wg-easy)
does for WireGuard: one container, set up from
your browser and managed from a console modelled on Tailscale's admin panel. Use the
official Tailscale apps on every device; only the server is yours.

## Features

<div class="grid cards" markdown>

-   :material-monitor-dashboard: **Tailscale-style console**

    Machines, users, DNS, access controls and keys at `/admin`. Dark and light
    themes, works on phones.

-   :material-account-lock: **Real user accounts**

    Passwords with two-factor, invitations and sign-up built in, or your own
    OIDC provider (Authentik, Keycloak, Pocket ID, Google).

-   :material-shield-account: **A private VPN per user**

    Members only see and reach their own devices; admins manage everything.

-   :material-router-network: **Routes and exit nodes**

    Approve subnet routes and exit nodes, edit tags, expire keys, rename,
    filter and export machines.

-   :material-dns: **DNS and ACLs**

    MagicDNS, nameservers, split DNS and the HuJSON policy — validated before
    they are applied.

-   :material-lock-check: **HTTPS your way**

    Let's Encrypt, self-signed, behind your existing proxy (ready-made snippets),
    or plain HTTP on a LAN.

-   :material-translate: **English, Spanish, French, German and Portuguese**

    In the console, in English and Spanish. More languages welcome.

-   :material-feather: **Lightweight**

    The console is plain Python standard library: no build step, no JS
    framework, no database server: one container, about 70 MB of RAM.

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
=== "Access controls"
    ![Access controls](images/access-controls.png){ .hse-shot }
=== "Keys"
    ![Keys](images/keys.png){ .hse-shot }
=== "Sign in"
    ![Sign in](images/sign-in.png){ .hse-shot }

## Support the project

Headscale Easy is built and maintained in my spare time by
**[Rafa Madolell](https://github.com/insanerask77)**. If it saves you time or money,
⭐ [star the repository](https://github.com/insanerask77/headscale-easy) and
☕ [buy me a coffee on Ko-fi](https://ko-fi.com/rafaelmadolell).

<small>Headscale Easy is an independent project, not affiliated with Tailscale Inc.
or the Headscale project. "Tailscale" is a trademark of Tailscale Inc.</small>
