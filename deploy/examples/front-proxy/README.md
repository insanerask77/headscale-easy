# Headscale Easy behind a proxy you already run

The image can sit behind nginx, Traefik, Caddy or Nginx Proxy Manager (NPM): the
proxy takes the HTTPS, the image speaks plain HTTP. One proxy host, one upstream
(the whole domain): the image routes `/` to Headscale and `/admin` to the console
by itself.

| File | For |
|---|---|
| [`nginx.conf`](nginx.conf) | nginx: a complete `conf.d` file |
| [`traefik-dynamic.yml`](traefik-dynamic.yml) | Traefik v3: dynamic configuration for the file provider |
| [`Caddyfile`](Caddyfile) | an edge Caddy: one site block |
| [`nginx-proxy-manager.md`](nginx-proxy-manager.md) | NPM: the Proxy Host fields and its Advanced block |

Replace `vpn.example.com` with your domain and `AIO_HOST:8080` with where the image
listens (the host and the `HSE_HTTP_PORT` you published).

## The image's side

```bash
# .env of deploy/compose
HSE_PUBLIC_URL=https://vpn.example.com   # what the browser sees: https, your domain
HSE_TLS=off                              # this image speaks plain HTTP; the proxy does the HTTPS
HSE_HTTP_PORT=8080                       # the port the proxy forwards to
HSE_TRUSTED_PROXIES=192.168.1.50/32      # the proxy's address: this one machine
```

`https://` with `HSE_TLS=off` is what tells the image a proxy is in front. It then
forces `X-Forwarded-Proto: https` towards Headscale and leaves HSTS to the proxy.

### `HSE_TRUSTED_PROXIES`: only the proxy, never its network

Without it the console's activity log and its sign-in rate limit see the **proxy's
address** for every request: one person's failed logins count against everybody.
With it the image reads the client's address from `X-Forwarded-For`, from the
right, skipping only the addresses you listed.

List the proxy itself, as `/32` (or `/128`). **Do not list a subnet that also
contains clients**: anything in that list is believed when it says who the client
is. Checked (nginx in front, the Docker network's gateway as the client, a forged
`X-Forwarded-For: 6.6.6.6` on the request):

| `HSE_TRUSTED_PROXIES` | address in the activity log |
|---|---|
| the proxy's `/32` | `172.29.0.1`, the real client; the forged header is ignored |
| the whole `/24` it lives in | `6.6.6.6`, the forged one |
| empty | the proxy's own address |

`0.0.0.0/0` and `::/0` are refused unless `HSE_TRUSTED_PROXIES_ANY=1` says it is meant.

### UDP 3478 does not go through the proxy

The image's DERP/STUN relay listens on **UDP 3478**. An HTTP proxy cannot carry it:
publish it straight from the container to the Internet (firewall, router), or pick
Tailscale's public relays in the setup wizard (`HSE_DERP_MODE=public`). The compose
file already publishes it.

## What each proxy has to do

The control protocol (`/ts2021`) and the relay (`/derp`) run over an **HTTP/1.1
Upgrade**, and `/machine/map` is a long-poll that stays open for hours. Every file
here therefore:

- speaks **HTTP/1.1** to the upstream (HTTP/2 forbids `Upgrade`) and passes
  `Upgrade` / `Connection`;
- turns **buffering off** and the read timeout up (`3600s`);
- allows large bodies (`client_max_body_size 0`);
- sends `Host`, `X-Forwarded-For` (appending), `X-Forwarded-Proto`.

Traefik and Caddy do the upgrade and the streaming by themselves; nginx needs the
`map` at the top of its file and NPM needs *Websockets Support* ticked.

## What was run

Each proxy was started in Docker in front of the image (`hse-aio:p3`, built from this
branch) on a private network, with the example file where only the domain
(`vpn.test`), the upstream (`p4b4-aio:80`) and the certificate (a throwaway
self-signed one instead of Let's Encrypt) were swapped. The image ran with
`HSE_PUBLIC_URL=https://vpn.test:20443`, `HSE_TLS=off` and the proxy's `/32`.

| Check, through the proxy | nginx 1.31 | Traefik v3 | Caddy 2 |
|---|---|---|---|
| `GET /key?v=142` (Headscale) | 200 | 200 | 200 |
| `GET /admin` | redirect to `/admin/login` | same | same |
| `GET /register/<id>` (device sign-in, no OIDC) | redirect to `/admin/register/<id>` | same | same |
| session cookie | `HttpOnly; Secure` | same | same |
| `POST /ts2021` with `Upgrade`, HTTP/1.1 | 400, same as straight to the image | same | same |
| activity log, forged `X-Forwarded-For` / `X-Real-IP` | the real client | same | same |

Not run: NPM (a web UI: its page is the 1.x one, tested then, adapted to this image's
variables, and not clicked through again here), a real Tailscale client registering through each proxy, and Let's
Encrypt (it needs a public domain). `nginx.conf` was fixed during this run: the
`map` that defines `$connection_upgrade` was commented out and, uncommented, has to
come before the server blocks (nginx refuses an unknown variable).

## Checking yours

```bash
curl -s  https://vpn.example.com/key?v=142          # Headscale's public key (JSON)
curl -sI https://vpn.example.com/admin | head -1    # a redirect to /admin/login
```

The session cookie must carry `Secure` (look at it in your browser's developer tools).

Then sign in, and in **Logs** look at the address on your own sign-in: it must be
yours, not the proxy's.
