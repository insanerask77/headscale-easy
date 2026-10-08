# Nginx Proxy Manager — vpn.example.com (Headscale Easy)

Behind NPM the Headscale Easy image already routes by
path (`/` → Headscale, `/console` → web UI), so NPM needs **a single Proxy Host**
that forwards the whole domain.

Final URL: **https://vpn.example.com** (web UI at `https://vpn.example.com/console`).

---

## 1. Before you start

- The DNS record for `vpn.example.com` must point to **the NPM machine**, not this one.
- NPM must reach `AIO_HOST:8080`. Check from its console:
  `curl -s http://AIO_HOST:8080/key?v=142` must return a public key.

---

## 2. Create the Proxy Host

**Hosts → Proxy Hosts → Add Proxy Host**

### *Details* tab

| Field | Value |
|-------|-------|
| Domain Names | `vpn.example.com` |
| Scheme | `http` |
| Forward Hostname / IP | `AIO_HOST` |
| Forward Port | `8080` |
| Cache Assets | ❌ off |
| Block Common Exploits | ❌ off |
| **Websockets Support** | ✅ **on** |
| Access List | Publicly Accessible |

> **Websockets Support is required.** `/ts2021`, Tailscale's control protocol,
> runs over an *upgraded* connection; without it no device can register.

> **Block Common Exploits off**: its rules reject requests with binary bodies,
> which is exactly what control-plane traffic looks like.

### *SSL* tab

| Field | Value |
|-------|-------|
| SSL Certificate | Request a new SSL Certificate |
| Force SSL | ✅ |
| HTTP/2 Support | ✅ |
| HSTS Enabled | ✅ |

### *Advanced* tab

Paste this as is:

```nginx
# /machine/map is a long-poll that stays open to push network changes.
# With the default 60 s read timeout nodes would reconnect every minute.
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;

# No buffering: network changes must arrive as they happen.
proxy_buffering off;
proxy_request_buffering off;

# Network maps can be large; without this, 413.
client_max_body_size 0;

proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

Save. NPM requests the certificate and reloads by itself.

---

## 3. Tell the image about the proxy

In the `.env` of the Headscale Easy image (see `../../compose/`):

```bash
HSE_PUBLIC_URL=https://vpn.example.com
HSE_TLS=off                          # NPM does the HTTPS, the image speaks plain HTTP
HSE_HTTP_PORT=8080                   # the port NPM forwards to (AIO_HOST:8080)
HSE_TRUSTED_PROXIES=192.168.1.50/32  # NPM's address: this one machine, not its network
```

Then `docker compose up -d`. With `HSE_TRUSTED_PROXIES` the console's activity log and
its sign-in rate limit see each person's own address; without it every request
comes from NPM's. List **only** NPM: anything in that list is believed when it
says who the client is.

---

## 4. The DERP port does not go through NPM

The image's STUN relay listens on **UDP 3478**, plain UDP that no HTTP proxy can
carry. Either:

1. open UDP `3478` straight to `AIO_HOST` on your firewall / router, or
2. choose Tailscale's public relays in the setup wizard (or `HSE_DERP_MODE=public`).

---

## 5. Check

```bash
curl -s https://vpn.example.com/key?v=142          # Headscale's public key
curl -sI https://vpn.example.com/console | head -1   # redirect to /console/login
```

If the first one fails but `curl http://AIO_HOST:8080/key?v=142` works from the
NPM machine, the problem is in NPM (almost always: Websockets Support unchecked).
