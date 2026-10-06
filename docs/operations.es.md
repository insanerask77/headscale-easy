# Operación

## Usuarios y admins { #users-and-admins }

Headscale Easy tiene cuatro roles:

- **Miembros**: ven y gestionan sólo sus máquinas y sus claves.
- **Admins**: ven todas las máquinas y usuarios y gestionan el DNS, la política
  ACL y las API keys.
- **Administradores de red**: solo editan la política ACL y el DNS.
- **Auditores**: ven todo lo que ve un admin, sin poder cambiar nada.

Con cuentas locales (por defecto) el rol se fija por cuenta en la página
**Usuarios**; con un proveedor externo sale de sus grupos. Mira
[Configuración → Roles](configuration.es.md#roles).

- **Invitar a gente:** **Usuarios → Invitar** crea un enlace de un solo uso; cada
  persona elige su nombre de usuario y contraseña. O deja que se registren desde la
  página de inicio de sesión (`HSE_SIGNUP`: desactivado, con clave de invitación o
  abierto).
- **Crear usuario** (página Usuarios) con solo un nombre crea un usuario de Headscale
  sin acceso a la consola, para servidores que se unen con claves. Añade un email, una
  contraseña y un rol y es una cuenta con la que la persona puede entrar.
- **¿Contraseña olvidada?** **⋯ → Enlace de restablecimiento…** crea un enlace de un
  solo uso, o **Establecer contraseña** pone una temporal. Ambas cierran sus sesiones.
- El usuario de Headscale se crea solo la primera vez que alguien conecta un
  dispositivo iniciando sesión.

## Conectar dispositivos { #connecting-devices }

- **Portátiles y móviles**: instala la app oficial de Tailscale, elige *Usar un
  servidor alternativo* / *Cambiar servidor* e introduce tu URL; después inicia sesión.
- **Linux / servidores**: `tailscale up --login-server=https://<tu-dominio>`, o
  con una clave (Ajustes → Claves) para máquinas desatendidas:
  `tailscale up --login-server=https://<tu-dominio> --authkey=<clave>`.
- **Contenedores**: **Añadir dispositivo → Docker** genera un comando `docker run` y un
  `docker-compose.yml` para la imagen oficial `tailscale/tailscale`, con una clave de un
  solo uso opcional.
- **El enlace de inicio de sesión**: el enlace que imprime `tailscale up`
  (`https://<tu-dominio>/register/…`) abre la consola, que pide iniciar sesión y
  después aprobar el dispositivo. Los miembros registran dispositivos a su nombre; los
  admins eligen el dueño.
- **Auth ID**: si un dispositivo muestra una URL con un ID de registro, un admin
  también puede aprobarlo con **Añadir dispositivo → Registrar con Auth ID**.

La página **Añadir dispositivo** de la consola muestra los pasos exactos por SO.

## Gestionar máquinas { #managing-machines }

La página **Máquinas** lista todos los dispositivos que puedes ver (los admins,
todos) y se actualiza sola cada pocos segundos: las máquinas nuevas aparecen y
su estado cambia sin recargar. Busca por nombre, propietario, dirección, etiqueta o versión, y acota la
lista con **Filtros** (estado, propietario, necesita actualización, tiene rutas,
clave caducada, caduca pronto, desconectado más de 30 días). El botón de
descarga exporta la lista actual en CSV.

Las etiquetas bajo cada nombre indican lo que tiene de especial una máquina:

| Etiqueta | Significado |
|---|---|
| Caducidad desactivada | Su clave no caduca nunca |
| Caducada | Su clave caducó: tiene que volver a iniciar sesión |
| Caduca pronto | Su clave caduca en menos de 14 días: vuelve a iniciar sesión en ella (o desactiva la caducidad) para que siga conectada |
| Efímera | Se elimina sola cuando se desconecta |
| Subredes / Exit Node | Anuncia rutas; en naranja, alguna espera aprobación |
| `tag:…` | Etiquetas ACL |

El menú **⋯** (y la página de la máquina) permite renombrarla, expirar su clave
(obliga a iniciar sesión de nuevo), desactivar la caducidad, editar etiquetas,
aprobar **rutas de subred y exit nodes** o eliminarla. Los miembros pueden
renombrar, expirar y eliminar sus máquinas; rutas, etiquetas y caducidad son
sólo para admins, como en Tailscale.

**Acciones en lote** (admins): marca la casilla de varias filas (o la de la
cabecera, para seleccionar todas las visibles) y aparece una barra para
**caducar claves**, **añadir una etiqueta** o **eliminar** todas a la vez;
útil al dar de baja un lote de dispositivos o etiquetar un grupo a posteriori.

**Máquinas que caducan e inactivas.** Cuando alguna de las máquinas que ves
caduca en los próximos 14 días o ya caducó, un aviso arriba de la lista dice
cuántas son, con un enlace que las filtra (los miembros lo ven para sus propias
máquinas). Una máquina está *inactiva* si lleva más de 30 días desconectada
(contando desde su registro si nunca llegó a conectarse); los admins tienen
**Quitar dispositivos inactivos…**, que las lista todas marcadas para que
desmarques las que quieras conservar. Una máquina que ha vuelto a conectarse
entretanto nunca se elimina.

Las apps de Tailscale que no pueden leer el nombre del dispositivo (iPhone,
iPad, Apple TV y la versión de la App Store para Mac) se registran como
`localhost`. Headscale Easy las renombra una vez a `<propietario>-<dispositivo>`,
por ejemplo `ana-iphone` o `leo-mac`; un nombre que pongas después no se vuelve a
cambiar. Comprueba cada 5 segundos.

La flecha junto a la versión se pone roja cuando hay un cliente de Tailscale más
nuevo (pasa el ratón por encima para ver cuál).

## Relays DERP { #derp-relays }

Los relays llevan el tráfico entre dispositivos que no pueden conectarse
directamente. El detalle del dispositivo muestra el relay que prefiere y su
latencia a cada relay que midió; la lista de dispositivos tiene una columna
**Relay**. **Red → Relays DERP** lista los relays en uso, cuántos dispositivos
prefieren cada uno y su latencia mediana.

Los administradores también pueden añadir ahí relays propios (`derper`): ID de
región (900 a 998), código, nombre, nombre de host, IPs y puertos opcionales. Al
guardar se escribe `/data/config/derp.yaml`, se apunta `derp.paths` de
`config.yaml` a él, se valida con `headscale configtest` y se reinicia
Headscale; si algo falla se restaura el mapa anterior. Con el valor por defecto
(`embedded`) el contenedor es su propio relé: mira
[Configuración → Relés](configuration.md#relays-derp).

## Estado del servidor { #server-status }

**Ajustes → Estado** (admins y auditores) muestra la salud del servidor de un
vistazo: las versiones de Headscale y de Headscale Easy con un aviso de "hay
actualización" (las últimas versiones se consultan en GitHub y se guardan 12 horas),
el estado de los tres procesos (Headscale, Caddy y la consola), el uso de disco del
volumen de datos y cifras básicas de las métricas de Headscale (dispositivos en
línea, peticiones servidas, memoria). Cada parte falla por separado: sin acceso a
Internet el resto de la página sigue funcionando.

## Comandos habituales { #everyday-commands }

```bash
docker exec headscale-easy hse health      # sano cuando los tres procesos funcionan
docker exec headscale-easy hse reload      # vuelve a renderizar la config y reinicia Caddy y Headscale
docker exec headscale-easy hse backup      # copia de seguridad ahora
docker exec headscale-easy hse backups     # lista las copias
docker logs -f headscale-easy              # [supervisor] [headscale] [caddy] [console]
```

La CLI de Headscale está siempre disponible dentro del contenedor:

```bash
docker exec headscale-easy headscale nodes list
docker exec headscale-easy headscale preauthkeys create --user 1 --reusable --expiration 24h
docker exec headscale-easy headscale --help
```

## Actualizar { #updating }

```bash
./install.sh                    # en el mismo directorio: descarga la imagen nueva y recrea el contenedor
# o, con el fichero compose:
docker compose pull && docker compose up -d
# o, con docker run:
docker pull ghcr.io/insanerask77/headscale-easy && docker rm -f headscale-easy   # y el mismo docker run
```

Los datos están en el volumen, así que no se pierde nada. Fija la versión en producción
(`HSE_VERSION=2.0.0` en el `.env` del compose, o una etiqueta exacta de la imagen) y lee
las notas de la versión antes de las actualizaciones mayores: la API de Headscale cambia
entre versiones, por eso cada versión de Headscale Easy trae un Headscale fijado. Haz
antes una copia (`hse backup`).

## Copias de seguridad { #backups }

El contenedor hace una copia cada noche a las 03:00 y guarda 14 días, sin ningún
contenedor extra. La programación, qué contiene, cómo restaurar (desde la consola, con
`hse restore` o en un servidor nuevo) y el menú **Copias de seguridad** están en
[Todo en uno → Copias de seguridad](all-in-one.es.md#backups).

### Copias remotas { #remote-backups }

Una copia que está en el mismo servidor no sobrevive a perder el servidor. Dos formas
de guardar copias en otro sitio:

- **Montar otro disco sobre `/data/backups`**, por ejemplo una carpeta de un NAS
  (`-v /mnt/nas/hse-backups:/data/backups`, escribible por el uid 1000).
- **El perfil `backup-remote`** de [`deploy/compose/`](advanced.es.md#the-compose-file):
  un contenedor auxiliar que sube cada archivo nuevo y aplica una retención remota.
  Define `BACKUP_REMOTE` en el `.env` del compose y arráncalo con
  `docker compose --profile backup-remote up -d`. Dos tipos de destino:

    - **Un remoto de rclone** (`BACKUP_REMOTE=s3:mi-bucket/headscale-easy`): S3, B2,
      SFTP, Google Drive y [decenas más](https://rclone.org/overview/). Define el
      remoto en `./remote-config/rclone.conf` (lo escribe `rclone config`), o para S3
      omite el fichero y pon
      `BACKUP_REMOTE=":s3,provider=AWS,env_auth=true,region=eu-west-1:mi-bucket/dir"`
      con `BACKUP_AWS_ACCESS_KEY_ID` y `BACKUP_AWS_SECRET_ACCESS_KEY`.
    - **rsync sobre SSH** (`BACKUP_REMOTE=rsync:usuario@host:/srv/backups`): pon la
      clave privada en `./remote-config/id_ed25519` (y opcionalmente `known_hosts`; sin
      él se acepta la primera clave del host). `BACKUP_REMOTE_SSH_PORT` cambia el
      puerto. El directorio debe existir ya en el servidor.

    `BACKUP_REMOTE_KEEP_DAYS` fija la retención en el remoto (por defecto
    `BACKUP_KEEP_DAYS`). Usa una clave o un bucket que pueda escribir pero no borrar si
    puedes: así un servidor comprometido no puede borrar sus propias copias (fija la
    retención en las reglas de ciclo de vida del bucket). Si una subida falla, la copia
    local se conserva y el auxiliar informa del error en
    `docker logs headscale-easy-backup-remote`.

### Restaurar { #restore }

Mira [Todo en uno → Hacer copias y restaurar](all-in-one.es.md#backing-up-and-restoring):
desde un contenedor parado (también así se restaura en un host nuevo), desde la
consola, o `hse restore <archivo>` en uno en marcha.

## Desinstalar { #uninstalling }

```bash
./uninstall.sh           # elimina el contenedor, conserva los datos
./uninstall.sh --purge   # borra también los volúmenes: usuarios, dispositivos, claves, certificados Y las copias
```

Con `docker run`: `docker rm -f headscale-easy`, y `docker volume rm hse` para borrar
los datos.

## Registro de actividad { #activity-log }

**Registros** (solo admins, en la barra lateral) es el registro de actividad
de la tailnet, como el registro de auditoría de configuración de la consola de
Tailscale. Guarda:

- **Configuración**: cada cambio hecho desde la consola — máquinas
  renombradas, eliminadas o caducadas, cambios de rutas, etiquetas y caducidad
  de la clave, máquinas registradas con un Auth ID; usuarios creados,
  renombrados y eliminados; claves de autenticación y de API creadas,
  revocadas o caducadas; la política de control de acceso (con el diff del
  cambio), el DNS (cada ajuste antes y después), la caducidad de las claves de
  dispositivo y el modo de verificación en dos pasos. Los renombrados
  automáticos de máquinas llamadas `localhost` aparecen como *Headscale Easy
  (automático)*.
- **Inicio de sesión**: inicios y cierres de sesión en la consola, e inicios
  de sesión fallidos con clave de API.
- **Dispositivos**: cada 30 segundos la consola compara el estado de
  Headscale y registra los dispositivos que se registran, se eliminan, se
  conectan o desconectan, cuya clave caduca, cuya versión de Tailscale cambia
  o que se renombran fuera de la consola (por ejemplo con
  `headscale nodes rename`).

Cada evento tiene la hora, el autor (el nombre de usuario, `Clave de API
<prefijo>` en las sesiones con clave de API, o *Headscale* en los eventos de
dispositivos), la IP del cliente, el objetivo y los detalles. Nunca se guardan
secretos: de las claves de autenticación, de API y de los Auth ID solo se
guarda el prefijo.

Busca, filtra por categoría, autor y fechas (UTC), y descarga los eventos que
coinciden con el botón CSV. La primera página se actualiza sola.

El registro está en `/data/console/audit.db` (SQLite). Los eventos de más de
90 días se borran automáticamente. Los cambios hechos fuera de la consola
(el CLI `headscale`, la API) no son eventos de configuración, pero su efecto en
los dispositivos sí se registra.

!!! note "Nota"
    Headscale no tiene registros de flujos de red (qué dispositivo habló con
    cuál y cuándo): hacen falta datos de los clientes que solo recoge el
    servidor de coordinación de Tailscale.

## Notificaciones { #notifications }

Headscale Easy puede avisarte cuando pasa algo con un dispositivo. Define los
destinos con variables de entorno (en el `.env` junto al fichero compose o con
`-e`) y recrea el contenedor:

```bash
# separados por coma, espacio o salto de línea
NOTIFY_URLS="slack:https://hooks.slack.com/services/T000/B000/XXXX ntfy:mi-tema"
NOTIFY_EVENTS="device.registered,device.key_expired,device.expiring,device.removed"
```

| Destino | Formato |
| --- | --- |
| Slack | `slack:<URL del webhook entrante>` (también vale una URL `hooks.slack.com` sin prefijo) |
| Telegram | `telegram:<token del bot>@<id del chat>`, p. ej. `telegram:123456:ABC-def@-100987` |
| ntfy | `ntfy:<tema>` (ntfy.sh) o `ntfy:https://tu-ntfy/tema` |
| Webhook genérico | `webhook:<URL>` (o una URL `https://` sin prefijo): POST con cuerpo JSON `{source, event, target, message, details, timestamp}` |

Eventos (todos por defecto; `NOTIFY_EVENTS` elige algunos): `device.registered`
(se unió un dispositivo nuevo), `device.key_expired`, `device.expiring` (la
clave caduca dentro de 14 días; se envía una vez por dispositivo
y fecha de caducidad, comprobado cada 15 minutos) y `device.removed`. Los
mensajes salen en segundo plano con un tiempo máximo de 10 segundos y 3
intentos, así que un destino lento o roto nunca ralentiza la consola; los
fallos solo aparecen en el registro del contenedor (sin la URL, que
contiene secretos).

Los admins ven los destinos (solo el host) en **Ajustes → General →
Notificaciones**, con un botón **Enviar prueba** (queda
registrado en el registro de actividad).

## Sesiones { #sessions }

**Ajustes → Sesiones** lista dónde has iniciado sesión (IP, navegador, última
actividad). **Cerrar sesión** termina una, **Cerrar sesión en todas partes**
termina todas las tuyas, y los admins ven también las sesiones de todos y
pueden usar **Cerrar la sesión de los demás**. Una sesión revocada deja de
funcionar en su siguiente petición. Las sesiones están en
`/data/console/sessions.db` (SQLite). Tras más de 10 inicios de sesión
fallidos desde una IP en 600 segundos, la consola responde `429` hasta que
pasa la ventana.

## Resolución de problemas { #troubleshooting }

Empieza por los logs y la comprobación de salud:

```bash
docker logs --tail 100 headscale-easy
docker exec headscale-easy hse health
```

**El contenedor no está sano o se reinicia.** `hse health` dice cuál de los tres
procesos está caído; las líneas del log llevan el prefijo `[supervisor]`, `[headscale]`,
`[caddy]` y `[console]`. Un proceso que se cae se reinicia con espera creciente (de 1 s
a 30 s). Un `BACKUP_SCHEDULE` no válido o `HSE_TLS=auto` sin `ACME_EMAIL` detienen el
contenedor al arrancar, con el motivo en el log.

**Headscale nunca llega a estar sano (con un proveedor OIDC externo).** Se niega a
arrancar hasta que puede leer el documento de descubrimiento del emisor desde dentro del
contenedor. Revisa `OIDC_ISSUER` y que el contenedor llegue a él:

```bash
docker exec headscale-easy wget -qO- https://<emisor>/.well-known/openid-configuration
```

Si el proveedor responde desde fuera pero no desde dentro, probablemente tu router no
tiene NAT loopback o el nombre del proveedor no resuelve desde el contenedor: usa una
dirección que resuelva.

**Errores de `redirect_uri` tras cambiar el dominio.** Registra las nuevas redirect URI
en tu proveedor: `https://<dominio>/oidc/callback` y `https://<dominio>/admin/callback`.

**Los clientes dicen `x509: certificate signed by unknown authority`.** Estás usando
`HSE_TLS=internal`: instala el certificado raíz de Caddy (`/data/caddy/pki/`) en el
cliente, o cambia a `auto` (Let's Encrypt).

**Los dispositivos conectan pero no se ven entre sí.** Revisa la política ACL (con
aislamiento, cada usuario solo alcanza sus dispositivos) y que el UDP 3478 esté abierto
para el relé DERP.

**La consola dice que la API key caducó.** La consola renueva sola su API key de
Headscale cuando le quedan 15 días, así que esto solo pasa si el servidor estuvo
apagado toda esa ventana o alguien caducó la clave a mano. Borra
`/data/console/api-key` y reinicia el contenedor: crea una nueva.

**Falla el inicio de sesión en la consola.** "El inicio de sesión caducó o no es
válido" significa que el navegador volvió sin la cookie puesta al empezar: comprueba
que abres la consola con el `HSE_PUBLIC_URL` exacto (mismo host y esquema: importa
`http` frente a `https`) y que el navegador acepta cookies. Tras más de 10 inicios
fallidos en 10 minutos la consola responde `429` durante un rato. Con un proveedor
externo, la redirect URI `https://<dominio>/admin/callback` debe estar registrada, y
los admins necesitan un email verificado en `PORTAL_ADMIN_EMAILS` o un grupo en
`PORTAL_ADMIN_GROUPS`. Si nadie puede entrar, arranca el contenedor con
`HSE_ADMIN_EMAIL` y `HSE_ADMIN_PASSWORD` para crear un administrador, o usa el acceso
con API key de Headscale.

**Let's Encrypt no emite el certificado.** Los puertos 80 y 443 deben ser accesibles
desde Internet y el dominio debe apuntar a este host (compruébalo con
`dig +short <dominio>` desde fuera). `docker logs headscale-easy` muestra el error de
ACME. Demasiados intentos fallidos activan los límites de Let's Encrypt: arregla la
causa y espera una hora.

**Detrás de mi propio proxy los dispositivos no conectan o siguen "offline", o todos los
clientes aparecen con la dirección del proxy.** El proxy debe pasar WebSockets y las
cabeceras de upgrade, no debe almacenar respuestas en búfer y debe reenviar el `Host`
original. Usa `HSE_TLS=off`, un `HSE_PUBLIC_URL` con `https://` y `HSE_TRUSTED_PROXIES`.
Usa los ejemplos de [`deploy/examples/front-proxy/`](advanced.es.md#a-proxy-in-front).

**Un dispositivo se queda "esperando aprobación" o muestra una URL de registro.** Con un
proveedor externo la persona debe terminar el inicio de sesión en el navegador que se
abrió. Si no, abre esa URL: la consola muestra la página de aprobación, o regístralo
desde **Máquinas → Añadir dispositivo → Registrar con Auth ID**, o usa una clave de
autenticación. Revisa el dueño: con aislamiento, un dispositivo registrado al usuario
equivocado es invisible para su dueño.

**Se rechazan los cambios de DNS.** La consola ejecuta `headscale configtest` y vuelve
atrás cuando Headscale rechaza el cambio; el error mostrado es el de Headscale. El nombre
DNS de la tailnet debe ser distinto del dominio del servidor. Si la página DNS está en
solo lectura dice por qué; revisa `hse health` y las líneas `[supervisor]` del log.

**La política ACL bloquea tráfico que esperas.** Usa **Comprobar** en el editor de la
política antes de guardar, y recuerda que con `NETWORK_ISOLATION=true` cada usuario
(admins incluidos) solo alcanza sus propios dispositivos salvo que la política diga otra
cosa. Los dispositivos con etiqueta pertenecen a la etiqueta, no a un usuario.

**Una copia de seguridad falló.** El menú **Copias de seguridad** muestra el último
resultado y el motivo; `docker exec headscale-easy hse backups` las lista. La causa
habitual es que `/data/backups` no sea escribible por el uid 1000 (un bind mount de
root) o un disco lleno. Con un PostgreSQL externo, el volcado necesita el servidor
accesible.

¿Sigues atascado? [Abre una incidencia](https://github.com/insanerask77/headscale-easy/issues/new/choose)
con la salida de `hse health` y los logs relevantes (quita los secretos).
