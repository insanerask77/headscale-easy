# A proxy in front

Your nginx, Traefik, Caddy or Nginx Proxy Manager takes the HTTPS; the image speaks plain HTTP behind it on
`127.0.0.1:8080`.

```bash
docker compose -f compose.yaml -f advanced/proxy.yaml up -d
```

```bash
# .env
HSE_PUBLIC_URL=https://vpn.example.com
HSE_TRUSTED_PROXIES=192.168.1.50/32      # the proxy itself, as /32: never the network it lives in
#HSE_HTTP_PORT=8080   #HSE_PROXY_BIND=127.0.0.1
```

| File | For |
|---|---|
| [`proxy/nginx.conf`](proxy/nginx.conf) | nginx, a complete `conf.d` file |
| [`proxy/traefik-dynamic.yml`](proxy/traefik-dynamic.yml) | Traefik v3, file provider |
| [`proxy/Caddyfile`](proxy/Caddyfile) | an edge Caddy |
| [`proxy/nginx-proxy-manager.md`](proxy/nginx-proxy-manager.md) | Nginx Proxy Manager |

Every proxy must speak HTTP/1.1 upstream, pass `Upgrade`, turn buffering off and wait `3600s`. **UDP 3478 does not
go through a proxy**: the overlay keeps it published. Checked by `scripts/advanced-smoke.sh proxy` (real nginx).
Full guide: documentation → *Advanced configurations → A proxy in front*.
