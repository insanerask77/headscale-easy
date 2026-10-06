# Configuración

Headscale Easy es un solo contenedor, configurado de tres maneras que se
apilan en este orden (gana la primera):

1. **Variables de entorno** (`-e`, o el `.env` junto al fichero compose).
2. **`/data/config/settings.json`**, que escriben el asistente de primer arranque y las
   páginas de Ajustes de la consola.
3. **Valores por defecto.**

Sin ninguna (sin `settings.json` y sin `HSE_PUBLIC_URL`) el contenedor arranca en
el [modo de configuración](all-in-one.md#the-first-run-wizard). La imagen renderiza
`config.yaml` de Headscale, el `Caddyfile` y el mapa DERP a partir de esos ajustes en
cada arranque (`/data/config/`); nunca editas a mano los ficheros generados, salvo el
bloque DNS, que gestiona la consola. `docker exec headscale-easy hse reload` vuelve a
renderizar y reinicia tras un cambio.

## Referencia de variables de entorno { #reference }

Todas las variables que lee la imagen. Las marcadas *asistente* también las pregunta el
[asistente de primer arranque](all-in-one.md#the-first-run-wizard).

### Servidor y HTTPS

| Variable | Por defecto | Significado |
|---|---|---|
| `HSE_PUBLIC_URL` | *(ninguna: asistente)* | `http(s)://host[:puerto]` público del servidor. Definirla se salta el asistente |
| `HSE_TLS` | `auto` con https, `off` con http | Quién termina TLS: ver [Modos de HTTPS](#https-modes). *asistente* |
| `ACME_EMAIL` | | Obligatoria con `HSE_TLS=auto` |
| `HSE_DERP_PORT` | `3478` | Puerto STUN del relé DERP integrado (publícalo como UDP) |
| `HSE_DERP_MODE` | `embedded` | `embedded`, `public` (también los relés de Tailscale) o `custom`: ver [Relés](#relays-derp). *asistente* |
| `HSE_DERP_URL` | | URL del mapa DERP, con `HSE_DERP_MODE=custom` |
| `DERP_USE_PUBLIC` | | Alias antiguo: `true` = `public`, `false` = `embedded`; gana `HSE_DERP_MODE` |
| `HEADSCALE_HTTP_PORT`, `HEADSCALE_METRICS_PORT`, `HEADSCALE_GRPC_PORT` | `8080`, `9090`, `50443` | Puertos internos de Headscale (dentro del contenedor; no se publican) |
| `IP_PREFIXES_V4`, `IP_PREFIXES_V6` | `100.64.0.0/10`, `fd7a:115c:a1e0::/48` | Rangos de direcciones que reciben los dispositivos. Cambiarlos renumera todos los dispositivos |
| `LOG_LEVEL` | `info` | Nivel de log de Headscale |
| `HSE_TRUSTED_PROXIES`, `HSE_TRUSTED_PROXIES_ANY` | | El proxy delante: IP reales de los clientes. Ver [Edición avanzada](advanced.md#a-proxy-in-front) |
| `UI_LANG` | `en` | Idioma por defecto de la consola (`en`, `es`, `fr`, `de`, `pt`) |
| `TZ` | `UTC` | Zona horaria (también el reloj de la programación de copias) |

### Tailnet

| Variable | Por defecto | Significado |
|---|---|---|
| `TAILNET_NAME` | `myorg` | Etiqueta de la tailnet. *asistente* |
| `HSE_BASE_DOMAIN` | `hse.net` | Dominio base de MagicDNS: los dispositivos son `<dispositivo>.<dominio base>`. Debe ser distinto del dominio del propio servidor. Se fija en el primer arranque; luego se edita en la página DNS. *asistente* |
| `NETWORK_ISOLATION` | `true` | Cada usuario sólo alcanza sus dispositivos: ver [Aislamiento de red](#network-isolation-and-acls). *asistente* |
| `NODE_KEY_EXPIRY` | `180d` | Vida de la clave de los dispositivos: ver [Caducidad de la clave](#device-key-expiry) |

### Cuentas e inicio de sesión

| Variable | Por defecto | Significado |
|---|---|---|
| `HSE_ADMIN_EMAIL`, `HSE_ADMIN_PASSWORD` | | Primer administrador, creado en el primer arranque si no hay ninguna cuenta. *asistente* |
| `HSE_SIGNUP` | `off` | Auto-registro: `off`, `invite` (pide una clave de invitación) u `open`. *asistente* |
| `MFA_REQUIRED` | `admins` | Quién debe configurar la verificación en dos pasos: `admins`, `everyone` u `optional`. Los admins lo cambian después en **Ajustes → General** |
| `SESSION_SECRET` | *(generado)* | Firma las sesiones de la consola. Si está vacío se genera y se guarda en `/data/config/session-secret` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS`, `SMTP_USE_SSL`, `SMTP_FROM` | | Servidor de correo opcional, para enviar invitaciones y enlaces de restablecimiento |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | | Iniciar sesión con un proveedor OIDC externo: ver [Inicio de sesión](#sign-in) |
| `OIDC_SCOPE` | `openid profile email` | Scopes que piden la consola y Headscale |
| `HSE_OIDC_ALLOWED_DOMAINS`, `HSE_OIDC_ALLOWED_USERS`, `HSE_OIDC_ALLOWED_GROUPS` | | Quién puede entrar con el proveedor (separado por comas; vacío = todos los que el proveedor deje pasar) |
| `PORTAL_ADMIN_EMAILS` | `HSE_ADMIN_EMAIL` | Cuentas del proveedor que son administradoras en la consola |
| `PORTAL_ADMIN_GROUPS`, `PORTAL_NETWORK_ADMIN_GROUPS`, `PORTAL_AUDITOR_GROUPS` | | Grupos del proveedor que corresponden a cada rol de la consola: ver [Roles](#roles) |
| `HSE_AUTHENTIK_UPSTREAM` | | `host:puerto` de un Authentik externo que Caddy sirve bajo `/authentik`, para que no cambie la URL de su emisor |

### Notificaciones

| Variable | Por defecto | Significado |
|---|---|---|
| `NOTIFY_URLS` | | Destinos (Slack, Telegram, ntfy, webhook): ver [Operación → Notificaciones](operations.md#notifications) |
| `NOTIFY_EVENTS` | todos | Qué eventos de dispositivos se envían |

### Base de datos

| Variable | Por defecto | Significado |
|---|---|---|
| `HEADSCALE_DB_TYPE` | `sqlite` | `sqlite` o `postgres` (un servidor externo): ver [Base de datos](#database) |
| `HEADSCALE_PG_HOST`, `HEADSCALE_PG_PORT`, `HEADSCALE_PG_NAME`, `HEADSCALE_PG_USER`, `HEADSCALE_PG_PASS`, `HEADSCALE_PG_SSLMODE` | puerto `5432`, base y usuario `headscale`, TLS `disable` | Conexión al PostgreSQL externo |
| `HEADSCALE_PG_RO_USER`, `HEADSCALE_PG_RO_PASS` | | Rol de solo lectura que la imagen crea para la consola, para que nunca tenga las credenciales del propietario |

### Copias de seguridad

| Variable | Por defecto | Significado |
|---|---|---|
| `BACKUP_SCHEDULE` | `0 3 * * *` | Sintaxis cron, en la zona horaria `TZ`; `off` desactiva las copias programadas. Un valor no válido detiene el contenedor al arrancar. *asistente* |
| `BACKUP_KEEP_DAYS` | `14` | Las copias más antiguas se borran; la última correcta nunca se borra. *asistente* |
| `BACKUP_MODE` | `create` | Sólo para la imagen `backup` usada como sidecar: `sync` sube los archivos que escribió la imagen todo en uno |
| `BACKUP_SYNC_INTERVAL` | `900` | Segundos entre dos subidas en modo `sync` |

## Modos de HTTPS { #https-modes }

| `HSE_TLS` | Quién termina TLS | Úsalo cuando |
|---|---|---|
| `auto` | Caddy, con un certificado de Let's Encrypt | Servidor público con dominio; puertos 80/443 abiertos |
| `internal` | Caddy, con su CA interna | Sin DNS público; puedes instalar una CA en los clientes |
| `off` | Nadie (HTTP plano), o un proxy delante | localhost, una LAN de confianza, o detrás de tu propio reverse proxy |

Con `internal`, el certificado raíz de Caddy está en `/data/caddy/pki/` (y en cada
copia). Instálalo en todos los clientes o se negarán a conectar
(`x509: certificate signed by unknown authority`). Las apps de Android e iOS sólo
funcionan con un certificado de confianza pública.

Detrás de un reverse proxy que ya usas (nginx, Traefik, Caddy, Nginx Proxy Manager)
usa `HSE_TLS=off`, un `HSE_PUBLIC_URL` con `https://` y `HSE_TRUSTED_PROXIES`: mira
[Edición avanzada → Un proxy delante](advanced.md#a-proxy-in-front) para la lista de
comprobación y los ejemplos listos.

El relé DERP integrado necesita el **UDP 3478** accesible desde Internet en todos los
modos: ningún proxy HTTP puede transportarlo.

### Relés (DERP) { #relays-derp }

Por defecto (`HSE_DERP_MODE=embedded`) el contenedor es su propio relé: DERP y STUN
corren dentro y `derp.urls` queda vacío, así que ni Headscale ni la consola contactan
con el mapa DERP de tailscale.com. Los dispositivos que no pueden conectar directamente
dependen entonces de tu relé: mantén accesibles el UDP 3478 y HTTPS. `public` añade los
relés públicos de Tailscale; `custom` usa tu propio mapa (`HSE_DERP_URL`, o uno subido en
**Red → Relés DERP**).

## Inicio de sesión { #sign-in }

Hay tres maneras de entrar en la consola, y se combinan:

| Método | Cuentas | Notas |
|---|---|---|
| **Cuentas locales** (por defecto) | Se crean en la consola: contraseña, verificación en dos pasos opcional | Invitaciones, enlaces de restablecimiento y auto-registro, todo en la consola. Sin otro servicio que mantener |
| **OIDC externo** | Tu proveedor (Authentik, Keycloak, Pocket ID, Google…) | Define `OIDC_*`. El mismo cliente inicia sesión en la consola y registra dispositivos en Headscale |
| **API key** | Ninguna | Acceso de emergencia para administradores con una API key de Headscale |

### Cuentas locales

- El primer administrador sale del asistente, o de `HSE_ADMIN_EMAIL` +
  `HSE_ADMIN_PASSWORD`.
- **Usuarios → Invitar** crea un enlace de un solo uso (1, 7 o 30 días) donde la
  persona elige nombre de usuario y contraseña; si la invitación lleva un email, la
  cuenta debe usarlo. **Usuarios → Invitaciones pendientes** lista los enlaces aún no
  usados (copiar de nuevo o revocar).
- **Usuarios → ⋯ → Enlace de restablecimiento…** crea un enlace de un solo uso (1 hora,
  24 horas o 7 días). **Establecer contraseña** pone una temporal que la persona debe
  cambiar en su próximo inicio de sesión, y cierra sus sesiones abiertas.
- Con un servidor SMTP (`SMTP_*`) la consola puede enviar por correo las invitaciones y
  los enlaces. Sin él, copia el enlace y envíalo en privado.
- Las contraseñas tienen al menos 8 caracteres y se guardan como hashes con sal. Los
  inicios de sesión fallidos tienen límite de intentos por dirección.
- El **auto-registro** desde la página de inicio de sesión es `off`, `invite` u `open`
  (`HSE_SIGNUP`, o **Ajustes → General**). Quien se registra solo es siempre miembro.

### Roles { #roles }

| Rol | Puede |
|---|---|
| Admin | Todo: máquinas, usuarios, DNS, control de acceso, claves, ajustes, logs, copias |
| Admin de red | Editar la política de **Control de acceso** y el **DNS**. Nada más |
| Auditor | Ver todo lo que ve un admin, sin cambiar nada, en ningún sitio, ni siquiera sus propios dispositivos |
| Miembro | Ver y gestionar sólo sus propias máquinas y claves de autenticación |

Con cuentas locales el rol se fija por cuenta (**Usuarios**). Con un proveedor externo
sale de sus grupos y correos:

| Variable | Rol |
|---|---|
| `PORTAL_ADMIN_GROUPS`, `PORTAL_ADMIN_EMAILS` | Admin |
| `PORTAL_NETWORK_ADMIN_GROUPS` | Admin de red |
| `PORTAL_AUDITOR_GROUPS` | Auditor |

Admin tiene prioridad si alguien está en varios grupos. Tu proveedor debe enviar un claim
`groups` (el scope `profile` por defecto suele incluirlo).

### Verificación en dos pasos { #two-factor-authentication }

Las cuentas locales pueden pedir un segundo factor tras la contraseña: una app
autenticadora (TOTP), con códigos de recuperación. Los admins eligen quién debe usarlo en
**Ajustes → General → Verificación en dos pasos** (`MFA_REQUIRED` es el valor inicial):

| `MFA_REQUIRED` | Comportamiento |
|---|---|
| `admins` (por defecto) | Los admins deben configurarlo la primera vez que entran; los miembros pueden |
| `everyone` | Todos los usuarios deben configurarlo |
| `optional` | A nadie se le obliga |

A quien tiene segundo factor siempre se le pide. Cada persona gestiona el suyo en
**Ajustes → General → Cuenta**. Con un proveedor externo, la verificación en dos pasos
se configura allí. El acceso de emergencia con API key no tiene segundo factor.

### Tu propio proveedor OIDC { #your-own-oidc-provider }

Registra un cliente con **dos** redirect URI:

- `https://<tu-dominio>/oidc/callback` (Headscale)
- `https://<tu-dominio>/admin/callback` (consola)

La consola y Headscale comparten el cliente para que la identidad (`sub`) de una persona
coincida en ambos. Hay ejemplos paso a paso para Authentik, Pocket ID, Keycloak y Google
en la [edición avanzada](advanced.md#identity-providers).

## Aislamiento de red y ACL { #network-isolation-and-acls }

Con `NETWORK_ISOLATION=true` (el valor por defecto, o la opción de aislamiento del asistente) la
configuración inicial aplica esta política la primera vez:

```jsonc
{
  "acls": [
    {"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:self:*"]},
    {"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:internet:*"]}
  ]
}
```

Cada usuario sólo alcanza sus dispositivos, admins incluidos, y puede sacar
tráfico a internet por exit nodes (sin la regla `autogroup:internet` un exit
node acepta conexiones pero no reenvía nada). Una política existente nunca se
sobrescribe. Edítala en **Control de acceso**; la sintaxis
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
tiene que volver a iniciar sesión. Headscale Easy la fija en **180 días** (`NODE_KEY_EXPIRY`) (el
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
bloque `dns:` de `/data/config/config.yaml` entre estos marcadores:

```yaml
# >>> dns: managed by Headscale Easy (do not edit between these markers)
dns:
  ...
# <<< dns
```

después ejecuta `headscale configtest` y reinicia Headscale, restaurando el
bloque anterior si la comprobación falla. La imagen conserva ese bloque al
volver a renderizar el fichero (por ejemplo al cambiar un ajuste), así que tu DNS sobrevive.

La validación y el reinicio pasan por el supervisor del propio contenedor: no hay
socket de Docker en ningún sitio. Ver [Seguridad](security.md).

## Base de datos { #database }

Headscale guarda usuarios, máquinas y claves en **SQLite** por defecto: un fichero en
`/data/headscale/`, sin nada más que ejecutar. Es la recomendación del propio Headscale y
lo adecuado para casi cualquier tailnet. **PostgreSQL** se admite como servidor
**externo**, para tailnets grandes o si ya ejecutas (y respaldas) uno:

| Opción | `HEADSCALE_DB_TYPE` | Qué aportas |
|---|---|---|
| SQLite (por defecto) | `sqlite` | Nada |
| PostgreSQL externo | `postgres` | host, puerto, base de datos, usuario propietario y contraseña, modo TLS (`HEADSCALE_PG_*`) |

- **No hay conversión** entre SQLite y PostgreSQL (Headscale no tiene herramienta para
  ello). Elige antes de añadir dispositivos.
- **La consola lee con un rol de solo lectura.** Necesita el Hostinfo que informan los
  dispositivos (SO, versión de Tailscale, relé DERP, endpoints), que la API de Headscale
  no expone. Con `HEADSCALE_PG_RO_USER` la imagen crea ese rol con
  [`templates/headscale-pg-readonly.sql`](https://github.com/insanerask77/headscale-easy/blob/next/templates/headscale-pg-readonly.sql):
  sólo puede hacer `SELECT` de las columnas `id`, `host_info` y `endpoints` de `nodes`
  (sin claves, sin otras tablas) y sus sesiones son de solo lectura. La consola nunca
  recibe las credenciales propias de Headscale. Habla con PostgreSQL con un pequeño
  cliente integrado (solo biblioteca estándar: SCRAM-SHA-256, TLS opcional).
- **Tu servidor:** debe autenticar con `scram-sha-256` (el valor por defecto de
  PostgreSQL desde la 14; el método antiguo `md5` se rechaza). La base de datos debe
  existir y su propietario debe ser el usuario que indiques (Headscale crea sus tablas
  con él). Para crear el rol de solo lectura la imagen ejecuta ese SQL como propietario,
  lo que requiere el privilegio `CREATEROLE`; si no puede, la consola recurre a las
  credenciales del propietario y lo avisa claramente en el log.
  `HEADSCALE_PG_SSLMODE` (`disable`, `prefer`, `require`, `verify-ca`, `verify-full`)
  se aplica a Headscale, a la consola y a las copias.
- Las copias usan `pg_dump` (ver [Operación → Copias de seguridad](operations.md#backups)).
  La [edición avanzada](advanced.md#postgresql) tiene un compose y una lista de comprobación.

## Idioma { #language }

La consola sigue el idioma del navegador (inglés, español, francés, alemán o portugués) y
cada persona puede cambiarlo en **Ajustes → General**. `UI_LANG` (`en`, `es`, `fr`, `de` o
`pt`) fija el valor por defecto cuando el navegador pide un idioma que la consola no tiene.

## Qué hay en `/data` { #what-lives-in-data }

| Ruta | Contenido |
|---|---|
| `headscale/` | Base de datos de Headscale, claves y socket local |
| `caddy/` | Certificados y logs de acceso |
| `console/` | Bases de datos de cuentas, sesiones y auditoría, API key de Headscale |
| `config/` | `settings.json`, `config.yaml` renderizado, `Caddyfile`, `derp.yaml` |
| `backups/` | Las copias integradas y `status.json` |

El contenedor lo crea todo con permisos privados (700 / 600).
