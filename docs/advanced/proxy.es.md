# Un proxy delante

Si nginx, Traefik, Caddy o Nginx Proxy Manager (NPM) ya se ocupa del HTTPS en tu host, deja que siga haciéndolo:
la imagen habla HTTP plano detrás. Un host de proxy, un upstream (todo el dominio): la imagen enruta `/` a
Headscale y `/console` a la consola por sí sola.

```bash
docker compose -f compose.yaml -f advanced/proxy.yaml up -d
```

```bash
# .env
HSE_PUBLIC_URL=https://vpn.example.com     # lo que ve el navegador: https y tu dominio
HSE_TRUSTED_PROXIES=192.168.1.50/32        # la dirección del propio proxy, como /32 (ver abajo)
#HSE_HTTP_PORT=8080                        # el puerto en el que escucha la imagen (por defecto 8080), en 127.0.0.1
#HSE_PROXY_BIND=127.0.0.1                  # cámbialo si el proxy corre en otra máquina
```

El overlay pone `HSE_TLS=off` (una URL `https://` con TLS apagado es lo que le dice a la imagen que hay un proxy
delante: entonces fuerza `X-Forwarded-Proto: https` y deja el HSTS al proxy) y sustituye los puertos
publicados: la imagen deja de publicar 80 y 443 y publica solo `HSE_PROXY_BIND:HSE_HTTP_PORT` para el proxy y
UDP 3478.

## `HSE_TRUSTED_PROXIES`: solo el proxy, nunca su red

Sin esta variable, el registro de actividad de la consola y su límite de intentos de acceso ven la **dirección
del proxy** en cada petición, así que las contraseñas erróneas de una persona cuentan contra todas. Con ella la
imagen lee la dirección del cliente de `X-Forwarded-For`, de derecha a izquierda, saltando solo las direcciones
que listaste.

Lista al propio proxy, como `/32` (o `/128`). **No listes una subred que también contenga clientes**: se cree
cualquier cosa de la lista cuando dice quién es el cliente. `0.0.0.0/0` y `::/0` se rechazan salvo que
`HSE_TRUSTED_PROXIES_ANY=1` diga que es a propósito. Si el proxy corre en el mismo host, la imagen lo ve como la
puerta de enlace de la red de Docker: `docker network inspect <proyecto>_default` la muestra.

## El UDP 3478 no pasa por el proxy

El relay (DERP/STUN) escucha en **UDP 3478**. Un proxy HTTP no puede llevarlo: publícalo directamente desde el
contenedor a Internet (el overlay lo mantiene publicado) o elige los relays públicos de Tailscale en el asistente
(`HSE_DERP_MODE=public`).

## Qué tiene que hacer cada proxy

El protocolo de control (`/ts2021`) y el relay (`/derp`) usan un **Upgrade de HTTP/1.1**, y `/machine/map` es un
long-poll que queda abierto horas. Por eso cada archivo de
[`advanced/proxy/`](https://github.com/insanerask77/headscale-easy/tree/main/advanced/proxy) habla HTTP/1.1 con el
upstream, pasa `Upgrade` / `Connection`, apaga el buffering, sube el tiempo de lectura a `3600s`, admite cuerpos
grandes y envía `Host`, `X-Forwarded-For` (añadiendo) y `X-Forwarded-Proto`.

| Archivo | Para |
|---|---|
| `nginx.conf` | nginx: un archivo `conf.d` completo (su `map` de `$connection_upgrade` debe ir antes de los bloques server) |
| `traefik-dynamic.yml` | Traefik v3: configuración dinámica del proveedor de archivos |
| `Caddyfile` | un Caddy de borde: un bloque de sitio |
| `nginx-proxy-manager.md` | NPM: los campos del Proxy Host y su bloque Advanced (*Websockets Support* activado) |

Cambia `vpn.example.com` y la dirección del upstream (`AIO_HOST:8080`) por las tuyas.

## Comprobar el tuyo

```bash
curl -s  https://vpn.example.com/key?v=142          # la clave pública de Headscale (JSON)
curl -sI https://vpn.example.com/console | head -1    # una redirección a /console/login
```

La cookie de sesión debe llevar `Secure`. Después inicia sesión y, en **Registros**, mira la dirección de tu propio
acceso: debe ser la tuya, no la del proxy.

## Qué se ejecutó

`scripts/advanced-smoke.sh proxy`: la imagen con `advanced/proxy.yaml` detrás de un **nginx** real con
`advanced/proxy/nginx.conf` (solo cambiados el dominio, el upstream y un certificado de usar y tirar). A través del
proxy: `GET /key` responde 200, `GET /console` redirige a la página de acceso, la cookie de sesión es `Secure` y el
registro de actividad muestra la dirección real del cliente mientras un `X-Forwarded-For` y un `X-Real-IP`
falsificados en la misma petición se ignoran. El contenedor publica HTTP solo en `127.0.0.1` y no el 443.

**No se ejecutó:** Traefik, Caddy y NPM en esta ronda (sus archivos son los ejemplos tal cual), un cliente Tailscale
real registrándose a través de un proxy, ni Let's Encrypt (necesita un dominio público).
