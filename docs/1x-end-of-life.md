# Headscale Easy 1.x is discontinued

!!! danger "End of life: 6 October 2026"
    Headscale Easy **1.x is discontinued as of 2.0.0**. It receives no more fixes, security
    fixes included. Do not install it on a new server, and plan to leave it on the ones that run it.

## What stays available

- The last 1.x release is **1.5.0**. Its git tag (`v1.5.0`) and its images
  (`ghcr.io/insanerask77/headscale-easy:1.5.0`) stay where they are, as they were published.
- Nothing is deleted, and nothing will be patched. A vulnerability found in 1.x after today is not fixed.
- The 1.x installer, its compose file and its documentation are no longer in the repository's main branch.
  Read them at the `v1.5.0` tag if you must.

## What changed in 2.0

2.0 is a different product to run: one container (Headscale, Caddy and the console) installed with a
single Docker Compose file and a first-run wizard. See [Getting started](getting-started.md) and the
[changelog](changelog.md).

## Moving to 2.0

There is **no in-place upgrade and no conversion tool**. Install 2.0 as a new deployment, then add your
devices to it. Keep your 1.x server and its backups until the new one is working.

## If you stay on 1.x

You are on software that nobody maintains, in a role (a VPN control plane) where that matters. Keep it
behind a firewall, do not expose it to the Internet, and move to 2.0 when you can.
