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
6. **Inicio de sesión**: Authentik, tu propio proveedor OIDC o sólo API key. Con
   Authentik, opcionalmente un servidor SMTP para el email ("¿Olvidaste la contraseña?").
7. **Aislamiento de red**: si cada usuario sólo alcanza sus dispositivos.
8. **Copias de seguridad**: si se hace una copia diaria (desactivada por
   defecto, recomendada) y, en ese caso, a qué hora, en qué carpeta y cuántos
   días se conserva. Ver [Copias de seguridad](operations.md#backups).

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

### Servidores DERP públicos

El instalador pregunta si usar también los relés DERP públicos de Tailscale
(`DERP_USE_PUBLIC`, **sí** por defecto). Responde **no** para una instalación
totalmente autoalojada: sólo se usa tu relé integrado y `derp.urls` queda vacío,
así que ni Headscale ni el panel web contactan con el mapa DERP de tailscale.com.
Los dispositivos que no puedan conectar directamente dependerán de tu relé:
mantén accesibles el UDP 3478 y HTTPS. Vuelve a ejecutar `./install.sh` para
cambiarlo.

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

### Roles { #roles }

Además de admin y miembro, hay dos roles más específicos disponibles, ambos
opcionales (desactivados salvo que los configures) y basados en grupos de
Authentik, igual que ya funciona `PORTAL_ADMIN_GROUPS`:

| Rol | Variable | Puede |
|---|---|---|
| Administrador de red | `PORTAL_NETWORK_ADMIN_GROUPS` | Editar la política de **Control de acceso** y el **DNS**. Nada más: ni Usuarios, ni Máquinas salvo las suyas, ni Registros, ni Ajustes. |
| Auditor | `PORTAL_AUDITOR_GROUPS` | Ver todo lo que ve un admin (todas las máquinas, Usuarios, DNS, Control de acceso, Registros) pero no cambiar nada, en ningún sitio, ni siquiera en sus propios dispositivos. |

Si alguien está en más de uno de estos grupos, admin tiene prioridad. Con
Authentik integrado, crea tú el grupo o grupos (**Directory → Groups**) y
añade miembros: la consola ya recibe todos los grupos de un usuario que
inicia sesión (el scope OIDC `profile` por defecto los incluye), así que no
hace falta tocar el blueprint. Con tu propio proveedor OIDC, asegúrate de que
envíe el claim `groups`.

### Authentik integrado { #built-in-authentik }

- El primer admin es `akadmin`; el instalador muestra su contraseña del primer
  arranque. Cámbiala en `/authentik/if/user/`.
- Invita a la gente desde la consola (**Usuarios → Invitar usuario**): cada
  uno elige su usuario y su contraseña; ver [Invitaciones y restablecer la
  contraseña](#invitations-and-password-reset).
- O dalos de alta tú en **`/add-user`** (o **Usuarios → Añadir usuario** en la
  consola): un formulario sencillo con nombre, usuario, email, contraseña y si es
  admin. Sin entrar en la administración de Authentik. El email y el usuario
  deben ser únicos.
- Las páginas de login usan el tema de Headscale Easy (oscuro/claro según el navegador).
- Authentik se configura con el blueprint `authentik/blueprints/headscale.yaml`.
  El instalador lo vuelve a aplicar en cada ejecución. El inicio y el cierre de
  sesión usan flujos propios de Headscale Easy (`headscale-easy-sign-in`,
  `headscale-easy-sign-out`): Authentik restablece sus flujos por defecto de vez
  en cuando.

### Invitaciones y restablecer la contraseña { #invitations-and-password-reset }

**Invitaciones.** En **Usuarios → Invitar usuario** elige el acceso (miembro o
admin), opcionalmente un email, y cuánto tiempo vale el enlace (1, 7 o 30 días;
7 por defecto). La consola muestra un enlace para copiar y enviar; quien lo abre
ve *Create your account* (flujo `headscale-easy-invitation`), elige usuario y
contraseña (las mismas reglas que `/add-user`: al menos 10 caracteres, usuario y
email únicos) y entra directamente en la consola, en el grupo que elegiste
(`headscale-users` o `vpn-admins`). Si la invitación lleva email, la cuenta
tiene que usarlo (el campo está bloqueado). El enlace sirve una vez: se gasta
al crear la cuenta, no al abrirlo. Los admins a los que la verificación en dos
pasos obliga la configuran antes de entrar. **Usuarios → Invitaciones
pendientes** lista los enlaces aún sin usar (copiar de nuevo o **Revocar**).

**Restablecer la contraseña sin email.** En el menú **⋯** de un usuario,
**Enlace para restablecer la contraseña…** crea un enlace de un solo uso
(válido 1 hora, 24 horas o 7 días) donde esa persona elige una contraseña nueva
(flujo `headscale-easy-recovery`); la anterior sigue funcionando hasta
entonces. Envíalo por un canal privado. Quien tiene cuenta pero aún no ha
conectado ningún dispositivo (y por tanto aún no es usuario de Headscale)
aparece en **Cuentas sin dispositivos**, con la misma acción. Las cuentas se
emparejan con los usuarios de Headscale por su identidad OIDC, no por el
nombre. Los superusuarios de Authentik (`akadmin`, `authentik Admins`) quedan
fuera a propósito: restablecen su contraseña en Authentik (o con
`docker exec -it authentik-worker ak create_recovery_key 60 akadmin`).

**Con email (opcional).** El instalador pregunta por un servidor SMTP
(desactivado por defecto): servidor, puerto, seguridad (STARTTLS, SSL/TLS o
ninguna), usuario, contraseña y remitente; se guardan como `SMTP_*` en `.env` y
Authentik los recibe como `AUTHENTIK_EMAIL__*`. Entonces la página de inicio de
sesión muestra **Forgot password?** (¿Olvidaste la contraseña?), que envía un
enlace a la dirección de la cuenta (flujo `headscale-easy-forgot-password`), y
la consola también puede enviar por email las invitaciones y los enlaces. Sin
email no aparece ese enlace. Prueba la configuración con
`docker exec authentik-worker ak test_email tu@example.com`.

La consola hace todo esto mediante la API de Authentik con el token de la
cuenta de servicio (`PORTAL_AUTHENTIK_TOKEN`), al que el blueprint permite
listar cuentas, crear enlaces de restablecimiento y gestionar invitaciones. Una
instalación hecha con una versión anterior lo obtiene volviendo a ejecutar
`./install.sh`.

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
política (además de las invitaciones y los enlaces de restablecimiento, arriba). El token nunca llega al navegador. Sin él (un `.env` escrito por un
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
existente nunca se sobrescribe. Edítala en **Control de acceso**; la sintaxis
es la [de Tailscale](https://tailscale.com/kb/1337/policy-syntax).

**Control de acceso** tiene seis pestañas:

- **Reglas**, **Grupos y etiquetas**: formularios para los casos habituales
  —quién puede llegar a qué, grupos reutilizables de usuarios, quién es dueño
  de cada etiqueta— sin escribir HuJSON. Editan la misma política que usa
  Headscale: por debajo, cada guardado reescribe solo el bloque `acls`,
  `groups` o `tagOwners` que tocó y deja el resto del archivo —comentarios,
  orden de las claves, una sección `hosts` u otra cosa escrita a mano—
  exactamente igual. Una regla cuyos destinos mezclan puertos distintos (algo
  que los formularios no pueden representar) se puede eliminar desde aquí,
  pero solo se edita en Avanzado.
- **Auto-aprobación**: declara qué etiqueta, grupo o usuario recibe la
  aprobación automática de una ruta de subred (o del rol de exit node), en
  vez de aprobar cada dispositivo a mano desde su página de máquina (ver
  [Gestionar máquinas](operations.es.md#managing-machines) para ese flujo
  manual de doble confirmación). Es la sección de política `autoApprovers` de
  [Headscale](https://headscale.net/stable/ref/routes/).
- **Reglas SSH**: quién puede conectar por SSH a qué máquinas, como qué
  usuarios del sistema, usando [Tailscale
  SSH](https://tailscale.com/kb/1193/tailscale-ssh) —sin gestionar claves
  SSH—. Una regla también puede exigir volver a autenticarse cada cierto
  tiempo en vez de un permiso fijo. Esto solo controla *a quién se le
  permite entrar*: Tailscale SSH sigue necesitando `tailscale up --ssh` (o
  el equivalente) en cada dispositivo que deba aceptar conexiones.
- **Probar acceso**: elige un origen y un destino (un dispositivo, un
  usuario, una etiqueta…) y dice si la política lo permite y qué regla
  coincidió. Es una **simulación** sobre la política guardada, no una prueba
  real de paquetes; para tener certeza, pruébalo desde los dispositivos
  reales (`tailscale ping`, o intenta llegar al servicio).
- **Avanzado (HuJSON)**: el editor de texto original, sin cambios. Es la vía
  de escape completa: cualquier cosa que el editor visual no pueda
  representar —posturas de dispositivo, comentarios escritos a mano— solo se edita aquí,
  y no se pierde nada por tener ambos.

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

Los admins editan el DNS en la consola (página **DNS**), organizada como la de
Tailscale:

- **Nombre DNS de la tailnet** — *Renombrar tailnet…* pide confirmación antes:
  cambia el nombre completo de cada máquina (`<máquina>.<dominio de la tailnet>`).
- **MagicDNS** — activar/desactivar; desactivarlo pide confirmación, porque los
  nombres de las máquinas dejan de resolverse en todos los dispositivos.
- **Servidores de nombres** — con MagicDNS activado, el dominio de la tailnet se
  resuelve siempre con `100.100.100.100` (se muestra de solo lectura). Debajo,
  los de *DNS dividido* (un servidor restringido a un dominio) y los *globales*,
  uno por fila (una IP o un resolvedor DoH `https://…`). **Usar la configuración
  DNS local** activado: los dispositivos mantienen sus servidores y los globales
  son un respaldo; desactivado (`override_local_dns: true`): todos los
  dispositivos usan los servidores globales.
- **Dominios de búsqueda** — con MagicDNS activado, el dominio de la tailnet es
  siempre el primero.
- **Registros personalizados** — `nombre` + `dirección` (por ejemplo
  `nas.example.com` → `100.64.0.5`): lo resuelven todos los dispositivos de la
  tailnet; A o AAAA según la dirección.

Los miembros ven los mismos ajustes en solo lectura. La consola escribe el
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

## Base de datos { #database }

Headscale guarda usuarios, máquinas y claves en **SQLite** por defecto: un
fichero junto a sus claves privadas, nada más que arrancar. Es lo que recomienda
el propio Headscale y lo adecuado para casi cualquier tailnet. El instalador
ofrece también **PostgreSQL**, para tailnets grandes o si ya tienes (y copias)
un servidor PostgreSQL:

| Opción | Qué se ejecuta | `.env` |
|---|---|---|
| SQLite (por defecto) | Nada más | `HEADSCALE_DB_TYPE=sqlite` |
| PostgreSQL en este stack | Contenedor `headscale-postgresql` (perfil de Compose `postgres`, volumen `headscale-db`), sin publicar | `HEADSCALE_DB_TYPE=postgres`, `HEADSCALE_PG_EXTERNAL=false` |
| Tu propio PostgreSQL | Nada más; indicas host, puerto, base de datos, usuario propietario y contraseña, y el modo TLS | `HEADSCALE_DB_TYPE=postgres`, `HEADSCALE_PG_EXTERNAL=true` |

- **Cambiar no migra los datos.** Pasar una instalación existente entre SQLite
  y PostgreSQL arranca Headscale con una base de datos vacía: hay que volver a
  crear usuarios y registrar las máquinas. El instalador avisa y pregunta antes.
- **El panel lee con un rol de solo lectura.** Necesita el Hostinfo que
  reportan los dispositivos (SO, versión de Tailscale, relay DERP,
  endpoints), que la API de Headscale no expone. El instalador crea
  `HEADSCALE_PG_RO_USER` (`headscale_ro`) con
  [`templates/headscale-pg-readonly.sql`](https://github.com/insanerask77/headscale-easy/blob/main/templates/headscale-pg-readonly.sql):
  solo puede hacer `SELECT` de las columnas `id`, `host_info` y `endpoints`
  de `nodes` (ni claves ni otras tablas) y sus sesiones son de solo lectura.
  El panel nunca recibe las credenciales de Headscale. Habla con PostgreSQL
  con un cliente pequeño incluido (solo biblioteca estándar: SCRAM-SHA-256 o
  MD5, TLS opcional), así que la imagen sigue sin paquetes de terceros.
- **Tu propio servidor:** la base de datos debe existir y su propietario debe
  ser el usuario que indicas (Headscale crea sus tablas con él). Para crear el
  rol de solo lectura el instalador ejecuta ese SQL como propietario, lo que
  requiere el privilegio `CREATEROLE`; si no puede, muestra el comando para
  ejecutarlo como superusuario. `HEADSCALE_PG_SSLMODE` (`disable`, `prefer`,
  `require`, `verify-ca`, `verify-full`) se aplica a Headscale, al panel y a
  las copias.
- Si una actualización de Headscale recrea la tabla `nodes`, las columnas de
  SO y versión quedan vacías y el panel registra `permission denied`: vuelve a
  ejecutar `./install.sh` para aplicar de nuevo el permiso.
- Las copias usan `pg_dump` (ver [Operación → Copias de seguridad](operations.md#backups)).

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
