# Edición avanzada

La imagen todo en uno es lo único que construye Headscale Easy. La edición avanzada es esa misma
imagen más lo que ya tengas: un proveedor de identidad, un proxy delante, un sitio donde guardar las
copias. Nada de esto necesita otra imagen, y nada es necesario para empezar: el
[inicio rápido](getting-started.md) termina en una tailnet que funciona.

Todo está en [`deploy/`](https://github.com/insanerask77/headscale-easy/tree/next/deploy):

| Qué | Dónde |
|---|---|
| El compose y sus perfiles | `deploy/compose/` |
| Un proxy delante (nginx, Traefik, Caddy, Nginx Proxy Manager) | `deploy/examples/front-proxy/` |
| Authentik, el camino sin cambios para una instalación 1.x | `deploy/examples/authentik/` |
| Pocket ID, Keycloak, Google | `deploy/examples/pocket-id/`, `keycloak.md`, `google.md` |

Cada ejemplo dice exactamente qué se ejecutó y dónde, y qué solo se leyó.

## El compose

`deploy/compose/docker-compose.yml` es lo que escribe el instalador, con comentarios: un servicio,
dos volúmenes con nombre (los datos y las copias), ninguna capability de Linux, `no-new-privileges` y
rotación de logs. Copia `.env.example` a `.env`, pon `HSE_PUBLIC_URL` y `docker compose up -d`. El
asistente imprime un token de un solo uso en `docker compose logs`; o define `HSE_ADMIN_EMAIL` y
`HSE_ADMIN_PASSWORD` para empezar con esa cuenta.

`docker compose --profile backup-remote up -d` añade un sidecar que sube cada copia nueva a S3, B2,
SFTP (rclone) o a un servidor (rsync sobre SSH). Solo ve las copias, en solo lectura, y sube cada una
una vez. Mira [Todo en uno: adónde van las copias](all-in-one.md#donde-van-las-copias).

## Un proxy delante

Si nginx, Traefik, Caddy o Nginx Proxy Manager ya termina el HTTPS en este equipo:

1. `HSE_PUBLIC_URL=https://vpn.example.com` y **`HSE_TLS=off`**. Una URL `https://` con TLS
   desactivado es lo que le dice a la imagen que hay un proxy delante: fuerza
   `X-Forwarded-Proto: https` hacia dentro y deja el HSTS al proxy.
2. Reenvía todo, incluidas las actualizaciones a WebSocket (`/ts2021` es una), al puerto 80 del
   contenedor. Los ficheros de `deploy/examples/front-proxy/` traen las cabeceras.
3. **`HSE_TRUSTED_PROXIES=<dirección del proxy>/32`**, para que la consola y Headscale vean la
   dirección real del cliente. Lista el proxy, **nunca la red entera donde vive**: una subred que
   también contiene a los clientes deja que cualquiera escriba su propia dirección en
   `X-Forwarded-For` y se la crean. Sin ella todo funciona, pero cada inicio de sesión se cuenta
   contra la dirección del proxy, así que las contraseñas erróneas de una persona bloquean a todos.
4. Publica el **UDP 3478** directamente al contenedor. DERP/STUN no es HTTP: un proxy no puede
   llevarlo.

`HSE_TRUSTED_PROXIES` admite hasta 16 direcciones o CIDR separados por comas; una entrada `/0` se
rechaza salvo que `HSE_TRUSTED_PROXIES_ANY=1` diga que es a propósito.

## PostgreSQL

SQLite es lo de por defecto y basta para la mayoría de instalaciones. Usa un PostgreSQL externo si ya
tienes uno o quieres la base de datos fuera del equipo: `HEADSCALE_DB_TYPE=postgres`,
`HEADSCALE_PG_HOST`, `HEADSCALE_PG_NAME`, `HEADSCALE_PG_USER`, `HEADSCALE_PG_PASS` (y
`HEADSCALE_PG_SSLMODE`, que por defecto es `disable`: usa `require` o `verify-full` para un servidor al
que llegas por red). `deploy/examples/postgresql/` es un compose con PostgreSQL y la imagen.

- **Dos roles.** Headscale escribe como propietario. La consola solo necesita lo que cada dispositivo
  cuenta de sí mismo, así que dale `HEADSCALE_PG_RO_USER` y `HEADSCALE_PG_RO_PASS`: la imagen crea ese
  rol por sí sola (puede hacer `SELECT` de tres columnas de una tabla, en transacciones de solo lectura)
  antes de arrancar la consola, y la consola nunca tiene las credenciales del propietario. Sin ellas la
  consola se conecta como propietario y el log lo dice. Si el propietario no puede crear roles, la
  imagen registra un `ERROR` y vuelve al propietario; crea el rol a mano con el SQL del ejemplo.
- **Las copias** ejecutan `pg_dump` (la imagen lleva un cliente PostgreSQL 18) y se restauran con `psql`;
  una restauración desde la consola carga el volcado cuando tanto la instalación como la copia usan
  PostgreSQL. `pg_dump` vuelca servidores hasta su propia versión mayor: aquí del 13 al 18. Con un
  servidor más nuevo la copia falla con una frase que lo dice, y el sidecar `backup`
  (`BACKUP_MODE=create`) es la salida.
- **No hay migración de SQLite a PostgreSQL**: Headscale no tiene herramienta para ello.

## Proveedores de identidad

Define `OIDC_ISSUER`, `OIDC_CLIENT_ID` y `OIDC_CLIENT_SECRET` y la gente entra en la consola y
registra dispositivos a través de tu proveedor. URI de redirección: `https://<dominio>/admin/callback`
para la consola y `https://<dominio>/oidc/callback` para Headscale.

| Variable | Qué |
|---|---|
| `HSE_OIDC_ALLOWED_DOMAINS`, `HSE_OIDC_ALLOWED_USERS`, `HSE_OIDC_ALLOWED_GROUPS` | Quién puede entrar (`oidc.allowed_*` de Headscale), separado por comas. **Vacío significa todo el que el proveedor deje entrar**: bien para tu Authentik o Keycloak, mal para Google, donde vale cualquier cuenta |
| `PORTAL_ADMIN_GROUPS`, `PORTAL_NETWORK_ADMIN_GROUPS`, `PORTAL_AUDITOR_GROUPS` | Quién es qué en la consola, por grupo (grupos de administración por defecto: `vpn-admins`, `authentik Admins`) |
| `PORTAL_ADMIN_EMAILS` | Administradores por correo, para proveedores sin grupos |
| `OIDC_SCOPE` | Scopes que se piden al entrar (por defecto `openid profile email`; Pocket ID da los grupos solo con `groups`) |

### Un Authentik 1.x que ya tienes

Headscale identifica a un usuario OIDC por la **URL del emisor** del proveedor más el id del usuario.
Si cambia el emisor, todos los usuarios son desconocidos. El stack 1.x sirve Authentik en
`https://<dominio>/authentik/`; para conservar esa dirección, ejecuta el AIO junto a él y define
`HSE_AUTHENTIK_UPSTREAM=authentik-server:9000` (Caddy enruta entonces `/authentik` allí), los `OIDC_*`
del `.env` antiguo y `AUTHENTIK_API_TOKEN` (y `AUTHENTIK_URL` si no es el de por defecto) para que la
consola siga gestionando invitaciones y restablecimientos a través de Authentik.
`deploy/examples/authentik/` tiene el compose y los pasos.
