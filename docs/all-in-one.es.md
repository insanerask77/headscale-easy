# Imagen todo en uno (preview)

Un solo contenedor con Headscale, Caddy y la consola web, que se configura
desde el navegador. Sin socket de Docker, sin instalador, sin Authentik. Es una
**preview** de la edición 2.0: el instalador 1.x y el compose dividido siguen
funcionando igual.

```bash
docker run -d --name headscale-easy \
  -p 80:80 -p 443:443 -p 3478:3478/udp \
  -v hse:/data \
  ghcr.io/insanerask77/headscale-easy-aio
```

Lee en los logs el token de configuración de un solo uso y abre el asistente:

```bash
docker logs headscale-easy
```

Abre `http://<tu-servidor>/admin/setup`, introduce el token y sigue los pasos.

!!! note "Nombre de la imagen"
    Durante 1.x la imagen se llama `headscale-easy-aio` (`headscale-easy` sigue
    siendo la imagen de la consola del compose dividido). En 2.0 pasará a
    llamarse `headscale-easy`.

## El asistente de primer arranque

El modo configuración arranca cuando no existe `/data/config/settings.json` ni
`HSE_PUBLIC_URL`. Solo se sirve el asistente; cualquier otra URL redirige a él.

1. **Token**: el que sale en los logs (también en `/data/config/setup-token`).
   Sin él nadie accede a ningún paso, así que el primer visitante no puede
   apoderarse del servidor. Los tokens erróneos tienen límite de intentos.
2. **Idioma**.
3. **URL pública y HTTPS**: automático (Let's Encrypt, requiere dominio y
   correo), interno (autofirmado) o ninguno (HTTP simple, o HTTPS en un proxy
   delante).
4. **Administrador**: correo, usuario y contraseña. El doble factor es
   opcional aquí: la consola te sugiere activarlo (un aviso al iniciar sesión,
   una vez por sesión del navegador) y se activa en **Ajustes → Cuenta**.
5. **Nombre del tailnet** y si se aísla a los usuarios (cada uno solo alcanza
   sus propios dispositivos).
6. **Copias**: la programación y la retención se guardan para las copias
   integradas que llegarán en una versión posterior.

Al terminar se crean la clave de API de Headscale, el usuario de Headscale del
administrador y la política de aislamiento, y arranca la consola. Si un paso
falla verás el error y podrás reintentar sin duplicar nada.

!!! warning "La configuración va por HTTP simple"
    El asistente se sirve por el puerto 80 antes de que exista ningún
    certificado, así que la contraseña del administrador viaja sin cifrar.
    Hazlo desde una red de confianza, o usa el arranque sin asistente y pon
    antes el servidor detrás de HTTPS.

## Arranque sin asistente

Con `HSE_PUBLIC_URL` definida no hay asistente: el contenedor arranca
directamente en modo normal.

```bash
docker run -d --name headscale-easy \
  -p 80:80 -p 443:443 -p 3478:3478/udp -v hse:/data \
  -e HSE_PUBLIC_URL=https://hs.example.com \
  -e ACME_EMAIL=tu@example.com \
  -e HSE_ADMIN_EMAIL=admin@example.com \
  -e HSE_ADMIN_PASSWORD='elige-una-larga' \
  ghcr.io/insanerask77/headscale-easy-aio
```

La precedencia es **entorno > `/data/config/settings.json` > valores por
defecto**. Las variables (`HSE_TLS`, `TAILNET_NAME`, `NETWORK_ISOLATION`,
`NODE_KEY_EXPIRY`, `UI_LANG`, `TZ`, `OIDC_*`, `HEADSCALE_DB_TYPE` y
`HEADSCALE_PG_*`, entre otras) están en la
[versión en inglés](all-in-one.md#headless-start-no-wizard).

## Qué hay en `/data`

| Ruta | Contenido |
|---|---|
| `headscale/` | Base de datos, claves y socket local de Headscale |
| `caddy/` | Certificados y logs de acceso |
| `console/` | Bases de cuentas, sesiones y auditoría; clave de API de Headscale |
| `config/` | `settings.json`, `config.yaml`, `Caddyfile` y `derp.yaml` generados |
| `backups/` | Reservado para las copias integradas |

Todo lo crea el contenedor con permisos privados (700 / 600). Copia el volumen
para copiar el servidor.

## Seguridad y requisitos

- Corre como usuario sin privilegios (uid 1000) y sin capabilities añadidas.
  Headscale no necesita `NET_ADMIN`.
- Los puertos 80 y 443 se abren sin privilegios desde Docker 20.10. En otros
  runtimes añade `--sysctl net.ipv4.ip_unprivileged_port_start=0`.
- No hay socket de Docker en ningún sitio: la consola habla con un pequeño
  supervisor dentro del contenedor.
- El UDP 3478 debe ser accesible desde internet (STUN).

## Operación

```bash
docker exec headscale-easy hse health   # sano cuando todos los procesos corren
docker exec headscale-easy hse reload   # regenera la config y reinicia Caddy y Headscale
docker logs -f headscale-easy           # [supervisor] [headscale] [caddy] [console]
```

Un supervisor ejecuta los tres procesos, reinicia el que caiga con espera
exponencial (de 1 s a 30 s) y los detiene en orden con `docker stop`. Cambiar
el DNS en la consola valida la configuración y reinicia Headscale a través de
él. Para actualizar, descarga la imagen nueva y recrea el contenedor: los datos
están en el volumen.

Medido en el runner de CI: la imagen pesa unos 55 MB y el contenedor en reposo
usa unos 65 MB de RAM. CI falla por encima de 250 MB y 100 MB.

## Límites de la preview

- Sin Authentik integrado: cuentas locales (con doble factor) u OIDC externo.
- Las copias programadas aún no están integradas; copia el volumen `/data`.
- La migración de una instalación 1.x todavía no está automatizada.
