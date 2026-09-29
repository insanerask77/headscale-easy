# Configuración

Todo lo configura `./install.sh`. Guarda tus respuestas en `.env` y genera el
resto a partir de `templates/`. Vuelve a ejecutarlo para cambiar lo que sea: tus
respuestas anteriores son los valores por defecto y no se pierde ningún dato.

## El instalador { #the-installer }

Las preguntas, por orden:

1. **Idioma**: English o Español (del instalador y de la consola por defecto).
2. **Dominio o IP**: el único nombre con el que se accede al stack. Caddy lo
   enruta: `/` → Headscale, `/admin` → consola web, `/authentik` → Authentik.
3. **Quién pone el HTTPS**: ver [Modos de HTTPS](#https-modes).
4. **Puertos**: Enter acepta los valores por defecto.
5. **Tailnet**: nombre de la organización, usuario inicial de Headscale, rangos de direcciones.
6. **Inicio de sesión**: Authentik, tu propio proveedor OIDC o sólo API key.
7. **Aislamiento de red**: si cada usuario sólo alcanza sus dispositivos.

Después despliega: primero Authentik (Headscale no arranca hasta que el issuer
OIDC responde), luego Headscale, crea el usuario inicial y la API key que usa la
consola, aplica la política de aislamiento y arranca el resto.

## Modos de HTTPS { #https-modes }

| `SSL_MODE` | Quién termina el TLS | Úsalo cuando |
|---|---|---|
| `letsencrypt` | Caddy, con un certificado de Let's Encrypt | Servidor público con dominio y puertos 80/443 abiertos |
| `selfsigned` | Caddy, con su CA interna | Sin DNS público; puedes instalar una CA en los clientes |
| `front` | Otro proxy por delante (NPM, nginx, Traefik, Caddy) | Ya tienes un reverse proxy |
| `none` | Nadie (HTTP sin cifrar) | localhost, una LAN de confianza o detrás de otra VPN |

Con `selfsigned`, el instalador exporta el certificado raíz de Caddy a
`caddy-root-ca.crt`. Instálalo en cada cliente o se negarán a conectar
(`x509: certificate signed by unknown authority`). Las apps de Android e iOS sólo
funcionan con un certificado de confianza pública.

El relay DERP integrado necesita el **UDP 3478** accesible desde internet en
todos los modos: ningún proxy HTTP puede transportarlo.

## Detrás de un reverse proxy existente { #behind-an-existing-reverse-proxy }

Elige `front` e indica al instalador qué proxy usas y la dirección de esta
máquina vista desde él. Deja una configuración lista para usar en `reverse-proxy/`:

| Proxy | Fichero |
|---|---|
| Nginx Proxy Manager | `reverse-proxy/NGINX-PROXY-MANAGER.md` (paso a paso) |
| nginx | `reverse-proxy/nginx-<dominio>.conf` |
| Traefik | `reverse-proxy/traefik-<dominio>.yml` (file provider) |
| Caddy | `reverse-proxy/Caddyfile` |

Tu proxy reenvía el dominio entero a Caddy en `BACKEND_HOST:HTTP_PORT` y Caddy
enruta por ruta. Requisitos que ya cubren los snippets: WebSocket / HTTP upgrade
(para `/ts2021`), sin buffering de respuesta y timeouts de lectura largos (para
el long-poll de `/machine/map`) y sin límite de tamaño de cuerpo.

Si los contenedores no alcanzan tu URL pública (tu router no hace NAT loopback),
pon en `FRONT_PROXY_IP` la IP del proxy en la LAN.

## Inicio de sesión { #sign-in }

| `AUTH_PROVIDER` | Cuentas | Acceso a la consola |
|---|---|---|
| `authentik` | Authentik integrado: usuario + contraseña, Google opcional | Todos inician sesión; admins son los miembros de `vpn-admins` o `authentik Admins` |
| `external` | Tu proveedor OIDC | Todos inician sesión; admins son los de `PORTAL_ADMIN_EMAILS` (o los grupos de `PORTAL_ADMIN_GROUPS`, si tu proveedor envía el claim `groups`) |
| `none` | Ninguna | Sólo admins, con una API key de Headscale |

### Authentik integrado { #built-in-authentik }

- El primer admin es `akadmin`; el instalador muestra su contraseña del primer
  arranque. Cámbiala en `/authentik/if/user/`.
- Da de alta a la gente en **`/add-user`** (o **Usuarios → Añadir usuario** en la
  consola): un formulario sencillo con nombre, usuario, email, contraseña y si es
  admin. Sin entrar en la administración de Authentik. El email y el usuario
  deben ser únicos.
- Las páginas de login usan el tema de Headscale Easy (oscuro/claro según el navegador).
- Authentik se configura con el blueprint `authentik/blueprints/headscale.yaml`.
  El instalador lo vuelve a aplicar en cada ejecución. El inicio y el cierre de
  sesión usan flujos propios de Headscale Easy (`headscale-easy-sign-in`,
  `headscale-easy-sign-out`): Authentik restablece sus flujos por defecto de vez
  en cuando.

### Verificación en dos pasos { #two-factor-authentication }

El inicio de sesión de Headscale Easy pide un segundo factor después de la
contraseña: una app de códigos (TOTP) o una passkey. Los admins eligen quién
está obligado en el panel web, **Ajustes → General → Verificación en dos
pasos**; el cambio se aplica en Authentik al momento (desde el siguiente inicio
de sesión), sin reinstalar. `MFA_REQUIRED` en `.env` (lo pregunta el
instalador) es el valor inicial, y volver a ejecutar `./install.sh` aplica el
que se elija ahí:

| `MFA_REQUIRED` | Comportamiento |
|---|---|
| `admins` (por defecto) | Los miembros de `vpn-admins` y `authentik Admins` lo configuran la primera vez que entran; el resto, si quiere |
| `everyone` | Todos los usuarios lo configuran |
| `optional` | Nadie está obligado |

A quien ya tiene un segundo factor siempre se le pide. Cada persona gestiona el
suyo en **Ajustes → General → Cuenta, contraseña y verificación en dos pasos**.
El login con Google se apoya en la verificación en dos pasos de Google, y el
acceso de emergencia con API key no tiene segundo factor.

Dónde se guarda: el modo es la primera línea (`mode = "admins"`) de la política
de Authentik *Headscale Easy: two-factor required for this user*. El blueprint
crea esa política una sola vez, a partir de `MFA_REQUIRED`, y no la vuelve a
tocar (`state: created`), así que reiniciar Authentik no deshace lo que eligió
un admin. El panel cambia esa línea mediante la API de Authentik con el token
de `PORTAL_AUTHENTIK_TOKEN` (lo genera el instalador), que pertenece a la
cuenta de servicio `headscale-easy-web` y sólo puede leer y cambiar esa
política. El token nunca llega al navegador. Sin él (un `.env` escrito por un
instalador anterior, hasta que se vuelva a ejecutar `./install.sh`) el panel muestra el modo
en sólo lectura; con tu propio proveedor OIDC, la verificación en dos pasos se
configura allí y la sección no aparece. Desde el servidor:
`docker exec headscale-easy python /app/mfa.py get` (o `set everyone`).

### Login con Google { #sign-in-with-google }

Disponible con Authentik y HTTPS. En la
[consola de Google Cloud](https://console.cloud.google.com/apis/credentials) crea
un **OAuth client ID** de tipo *Web application* con:

- Authorized JavaScript origin: `https://<tu-dominio>`
- Authorized redirect URI: `https://<tu-dominio>/authentik/source/oauth/callback/google/`

Pasa el client ID y el secret al instalador. Quien entra con Google por primera
vez obtiene una cuenta **sin grupo**: un admin debe añadirlo a `headscale-users`
o `vpn-admins` para que pueda usar la VPN.

### Tu propio proveedor OIDC { #your-own-oidc-provider }

Registra un cliente con **dos** redirect URIs:

- `https://<tu-dominio>/oidc/callback` (Headscale)
- `https://<tu-dominio>/admin/callback` (consola)

La consola y Headscale comparten el cliente para que la identidad de cada
persona (`sub`) coincida en ambos.

### Acceso de emergencia { #emergency-access }

Con OIDC también puedes permitir entrar a la consola con una API key de
Headscale (`PORTAL_API_KEY_LOGIN=true`), útil si el proveedor de identidad está
caído. Crea una clave con `make apikey`.

## Aislamiento de red y ACL { #network-isolation-and-acls }

Con `NETWORK_ISOLATION=true` (el valor por defecto) el instalador aplica esta
política la primera vez:

```jsonc
{
  "acls": [
    {"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:self:*"]}
  ]
}
```

Cada usuario sólo alcanza sus dispositivos, admins incluidos. Una política
existente nunca se sobrescribe. Edítala en **Control de acceso → Editor de
políticas**; la sintaxis es la [de Tailscale](https://tailscale.com/kb/1337/policy-syntax).

## Caducidad de la clave de los dispositivos { #device-key-expiry }

Como en Tailscale, cada dispositivo tiene una clave que caduca: pasado ese plazo
tiene que volver a iniciar sesión. Headscale Easy la fija en **180 días** (el
valor de Tailscale); los admins la cambian en **Ajustes → General → Gestión de
dispositivos** (de 1 a 365 días, o nunca). Al guardar se reinicia Headscale, y
se aplica a los dispositivos que se añadan desde entonces: los existentes se
cambian en cada máquina (**⋯ → Activar/Desactivar caducidad**).

La caducidad de una **clave de autenticación** es otra cosa: solo limita hasta
cuándo puede la clave añadir dispositivos.

## DNS { #dns }

Los admins editan el DNS en la consola (página **DNS**): MagicDNS, el dominio de
la tailnet, nameservers, split DNS y dominios de búsqueda. La consola escribe el
bloque `dns:` de `headscale-config.yaml` entre estos marcadores:

```yaml
# >>> dns: managed by Headscale Easy (do not edit between these markers)
dns:
  ...
# <<< dns
```

después ejecuta `headscale configtest` y reinicia Headscale, restaurando el
bloque anterior si la comprobación falla. El instalador conserva ese bloque al
regenerar el fichero, así que tu DNS sobrevive a las reconfiguraciones.

Es la única función que necesita el socket de Docker; ver [Seguridad](security.md).

## Idioma { #language }

La consola sigue el idioma del navegador (inglés o español) y cada persona puede
cambiarlo en **Ajustes → General**. `UI_LANG` fija el idioma por defecto cuando
el navegador pide uno que la consola no tiene.

## Ficheros generados { #generated-files }

| Fichero | Lo escribe | Notas |
|---|---|---|
| `.env` | instalador | Toda la configuración y los secretos (`chmod 600`) |
| `headscale-config.yaml` | instalador | Salvo el bloque DNS, que gestiona la consola |
| `Caddyfile` | instalador | |
| `docker-compose.override.yml` | instalador | Puertos de Caddy y cómo alcanzan los contenedores la URL pública |
| `reverse-proxy/*` | instalador | Sólo con `SSL_MODE=front` |
| `caddy-root-ca.crt` | instalador | Sólo con `SSL_MODE=selfsigned` |

Todos están en `.gitignore`. No los edites a mano: vuelve a ejecutar el instalador.

## Referencia de `.env` { #env-reference }

Ver [`.env.example`](https://github.com/insanerask77/headscale-easy/blob/main/.env.example): cada variable, documentada.
