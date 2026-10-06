# A proxy in front

If nginx, Traefik, Caddy or Nginx Proxy Manager (NPM) already takes the HTTPS on your host, let it keep doing
that: the image then speaks plain HTTP behind it. One proxy host, one upstream (the whole domain): the image
routes `/` to Headscale and `/admin` to the console by itself.

```bash
docker compose -f compose.yaml -f advanced/proxy.yaml up -d
```

```bash
# .env
HSE_PUBLIC_URL=https://vpn.example.com     # what the browser sees: https, your domain
HSE_TRUSTED_PROXIES=192.168.1.50/32        # the proxy's own address, as /32 (see below)
#HSE_HTTP_PORT=8080                        # the port the image listens on (default 8080), on 127.0.0.1
#HSE_PROXY_BIND=127.0.0.1                  # change it when the proxy runs on another machine
```

The overlay sets `HSE_TLS=off` (an `https://` URL with TLS off is what tells the image a proxy is in front: it
then forces `X-Forwarded-Proto: https` and leaves HSTS to the proxy) and replaces the published ports: the
image no longer publishes 80 and 443, only `HSE_PROXY_BIND:HSE_HTTP_PORT` for the proxy, and UDP 3478.

## `HSE_TRUSTED_PROXIES`: only the proxy, never its network

Without it, the console's activity log and its sign-in rate limit see the **proxy's address** for every request,
so one person's wrong passwords count against everybody. With it the image reads the client's address from
`X-Forwarded-For`, from the right, skipping only the addresses you listed.

List the proxy itself, as `/32` (or `/128`). **Do not list a subnet that also contains clients**: anything on the
list is believed when it says who the client is. `0.0.0.0/0` and `::/0` are refused unless
`HSE_TRUSTED_PROXIES_ANY=1` says it is meant. If the proxy runs on the same host, the image sees it as the
Docker network's gateway: `docker network inspect <project>_default` shows it.

## UDP 3478 does not go through the proxy

The relay (DERP/STUN) listens on **UDP 3478**. An HTTP proxy cannot carry it: publish it straight from the
container to the Internet (the overlay keeps it published), or choose Tailscale's public relays in the wizard
(`HSE_DERP_MODE=public`).

## What each proxy has to do

The control protocol (`/ts2021`) and the relay (`/derp`) run over an **HTTP/1.1 Upgrade**, and `/machine/map` is a
long-poll that stays open for hours. Every file in
[`advanced/proxy/`](https://github.com/insanerask77/headscale-easy/tree/main/advanced/proxy) therefore speaks
HTTP/1.1 to the upstream, passes `Upgrade` / `Connection`, turns buffering off, raises the read timeout to
`3600s`, allows large bodies and sends `Host`, `X-Forwarded-For` (appending) and `X-Forwarded-Proto`.

| File | For |
|---|---|
| `nginx.conf` | nginx: a complete `conf.d` file (its `map` for `$connection_upgrade` must come before the server blocks) |
| `traefik-dynamic.yml` | Traefik v3: dynamic configuration for the file provider |
| `Caddyfile` | an edge Caddy: one site block |
| `nginx-proxy-manager.md` | NPM: the Proxy Host fields and its Advanced block (*Websockets Support* on) |

Replace `vpn.example.com` and the upstream address (`AIO_HOST:8080`) with yours.

## Checking yours

```bash
curl -s  https://vpn.example.com/key?v=142          # Headscale's public key (JSON)
curl -sI https://vpn.example.com/admin | head -1    # a redirect to /admin/login
```

The session cookie must carry `Secure`. Then sign in and, in **Logs**, look at the address on your own sign-in:
it must be yours, not the proxy's.

## What was run

`scripts/advanced-smoke.sh proxy`: the image with `advanced/proxy.yaml` behind a real **nginx** using
`advanced/proxy/nginx.conf` (only the domain, the upstream and the certificate swapped for a throwaway one).
Through the proxy: `GET /key` answers 200, `GET /admin` redirects to the sign-in page, the session cookie is
`Secure`, and the activity log shows the real client's address while a forged `X-Forwarded-For` and `X-Real-IP`
on the same request are ignored. The container publishes HTTP on `127.0.0.1` only and not 443.

**Not run:** Traefik, Caddy and NPM in this round (their files are the examples as written), a real Tailscale
client registering through a proxy, and Let's Encrypt (it needs a public domain).
