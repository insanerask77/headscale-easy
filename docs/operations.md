# Operations

## Users and admins

Headscale Easy has two roles:

- **Members** see and manage only their own machines and auth keys.
- **Admins** see every machine and user and manage DNS, the ACL policy and API keys.

With the built-in Authentik, admins are the members of `vpn-admins` (and
Authentik's own `authentik Admins`). Add people at `/add-user` or from **Users →
Add user**; the form lets you make them admins. A Headscale user is created
automatically the first time someone connects a device by signing in.

**Create local user** (Users page) creates a Headscale user without an account,
for servers that join with auth keys.

## Connecting devices

- **Laptops and phones**: install the official Tailscale app, choose *Use an
  alternate server* / *Change server* and enter your URL, then sign in.
- **Linux / servers**: `tailscale up --login-server=https://<your-domain>`, or
  with an auth key (Settings → Keys) for unattended machines:
  `tailscale up --login-server=https://<your-domain> --authkey=<key>`.
- **Auth ID**: if a device shows a URL with a registration ID, an admin can
  approve it with **Add device → Register with Auth ID**.

The **Add device** page in the console shows the exact steps per OS.

## Managing machines

The **Machines** page lists every device you can see (admins: all of them).
Search by name, owner, address, tag or version, and narrow the list with
**Filters** (status, owner, needs update, has routes, key expired). The
download button exports the current list as CSV.

Badges under each name tell you what is special about a machine:

| Badge | Meaning |
|---|---|
| Expiry disabled | Its key never expires |
| Expired | Its key expired: it must sign in again |
| Ephemeral | Removed automatically when it goes offline |
| Subnets / Exit Node | It advertises routes; orange means some are waiting for approval |
| `tag:…` | ACL tags |

The **⋯** menu (and the machine's page) lets you rename it, expire its key
(forces a new sign-in), disable key expiry, edit tags, approve **subnet routes
and exit nodes**, or remove it. Members can rename, expire and remove their own
machines; routes, tags and key expiry are admin-only, as in Tailscale.

The arrow next to the version turns red when a newer Tailscale client is
available (hover it to see which).

## Everyday commands

`make` lists everything. The most useful:

```bash
make ps          # container status
make health      # health of every container
make logs        # follow logs (make logs service=headscale)
make nodes       # machines, from the Headscale CLI
make key user=alice   # reusable 24 h auth key
make apikey      # new Headscale API key
make config      # .env with secrets hidden
```

The Headscale CLI is always available: `docker exec headscale headscale --help`.

## Updating

```bash
git pull
./install.sh        # re-applies templates and the Authentik blueprint
# or, if nothing in the repository changed:
make update         # pull new images and recreate containers
```

Pin versions in `.env` with `HSE_VERSION`, `HEADSCALE_IMAGE_TAG` and
`AUTHENTIK_IMAGE_TAG`. Read the Headscale and Authentik release notes before
major upgrades.

## Backups

```bash
make backup                 # into ./backups
make backup dir=/mnt/nas    # elsewhere
```

It saves `.env` and the generated configuration, the Headscale data volume
(database and keys) and, with Authentik, a dump of its database. The files
contain secrets: store them safely.

To restore on a new host: copy the repository and `.env`, restore the
`headscale-data` volume and Authentik's database, and run `./install.sh`.

## Uninstalling

```bash
./uninstall.sh           # remove containers, keep data and configuration
./uninstall.sh --purge   # also delete volumes, configuration and ./data
```

Versions that used Headplane left a `headplane-data` volume; delete it with
`docker volume rm headplane-data` once you no longer need it.

## Troubleshooting

**Headscale never becomes healthy (with OIDC).** It refuses to start until it
can read the issuer's discovery document from inside its container. Check:

```bash
docker compose logs headscale
docker exec caddy wget -qO- http://authentik-server:9000/authentik/application/o/headscale/.well-known/openid-configuration
```

If Authentik answers but Headscale cannot reach the public URL, your router
probably lacks NAT loopback: with `SSL_MODE=front` set `FRONT_PROXY_IP`.

**`redirect_uri` errors after changing the domain.** Re-run `./install.sh`: it
re-applies the Authentik blueprint with the new URLs (Authentik does not do it by
itself when only environment variables change).

**Clients say `x509: certificate signed by unknown authority`.** You are using
`SSL_MODE=selfsigned`: install `caddy-root-ca.crt` on the client, or switch to
Let's Encrypt.

**Devices connect but cannot reach each other.** Check the ACL policy (with
isolation, users only reach their own devices) and that UDP 3478 is open for
the DERP relay.

**The console says the API key expired.** Re-run `./install.sh` (it creates a
new one) or create one with `make apikey`, put it in `.env` as
`HEADSCALE_API_KEY` and run `docker compose up -d web`.

**Somebody signed in with Google but cannot use the VPN.** New Google accounts
have no group. Add them to `headscale-users` in Authentik.

Still stuck? [Open an issue](https://github.com/insanerask77/headscale-easy/issues/new/choose)
with the output of `make health` and the relevant logs (remove secrets).
