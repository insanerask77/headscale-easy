# Operación

## Usuarios y admins { #users-and-admins }

Headscale Easy tiene cuatro roles:

- **Miembros**: ven y gestionan sólo sus máquinas y sus claves.
- **Admins**: ven todas las máquinas y usuarios y gestionan el DNS, la política
  ACL y las API keys.
- **Administradores de red** (opcional): solo editan la política ACL y el DNS.
- **Auditores** (opcional): ven todo lo que ve un admin, sin poder cambiar nada.

Consulta [Roles](configuration.es.md#roles) para configurar estos dos
últimos, basados en su propio grupo o grupos de Authentik.

Con Authentik integrado, los admins son los miembros de `vpn-admins` (y del
grupo `authentik Admins` del propio Authentik). Da de alta a la gente en
`/add-user` o desde **Usuarios → Añadir usuario**; el formulario permite hacerlos
admins. El usuario de Headscale se crea solo la primera vez que alguien conecta
un dispositivo iniciando sesión.

**Crear usuario local** (página Usuarios) crea un usuario de Headscale sin
cuenta, para servidores que se unen con claves.

## Conectar dispositivos { #connecting-devices }

- **Portátiles y móviles**: instala la app oficial de Tailscale, elige *Use an
  alternate server* / *Change server*, introduce tu URL e inicia sesión.
- **Linux / servidores**: `tailscale up --login-server=https://<tu-dominio>`, o
  con una clave (Ajustes → Claves) para máquinas desatendidas:
  `tailscale up --login-server=https://<tu-dominio> --authkey=<clave>`.
- **Auth ID**: si un dispositivo muestra una URL con un ID de registro, un admin
  puede aprobarlo con **Añadir dispositivo → Registrar con Auth ID**.

La página **Añadir dispositivo** de la consola muestra los pasos para cada sistema.

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
entretanto nunca se elimina. Cambia los plazos con `EXPIRY_WARNING_DAYS` e
`INACTIVE_DAYS` en `.env` (y luego `docker compose up -d`).

Las apps de Tailscale que no pueden leer el nombre del dispositivo (iPhone,
iPad, Apple TV y la versión de la App Store para Mac) se registran como
`localhost`. Headscale Easy las renombra una vez a `<propietario>-<dispositivo>`,
por ejemplo `ana-iphone` o `leo-mac`; un nombre que pongas después no se vuelve a
cambiar. Pon `AUTO_RENAME_LOCALHOST=false` en `.env` para desactivarlo.

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
guardar se escribe `headscale-derp.yaml`, se apunta `derp.paths` de
`config.yaml` a él, se valida con `headscale configtest` y se reinicia
Headscale; si algo falla se restaura el mapa anterior. Las instalaciones
existentes necesitan ejecutar `./install.sh` una vez para añadir el bloque
marcado y el fichero. No disponible en la demo.

## Comandos habituales { #everyday-commands }

`make` lo lista todo. Los más útiles:

```bash
make ps          # estado de los contenedores
make health      # salud de cada contenedor
make logs        # seguir los logs (make logs service=headscale)
make nodes       # máquinas, desde la CLI de Headscale
make key user=alice   # clave reutilizable de 24 h
make apikey      # nueva API key de Headscale
make config      # .env con los secretos ocultos
```

La CLI de Headscale siempre está disponible: `docker exec headscale headscale --help`.

## Actualizar { #updating }

```bash
git pull
./install.sh        # vuelve a aplicar las plantillas y el blueprint de Authentik
# o, si nada cambió en el repositorio:
make update         # descarga imágenes nuevas y recrea los contenedores
```

Fija versiones en `.env` con `HSE_VERSION`, `HEADSCALE_IMAGE_TAG` y
`AUTHENTIK_IMAGE_TAG`. Lee las notas de versión de Headscale y Authentik antes de
saltos de versión mayor.

## Copias de seguridad { #backups }

Las copias diarias son **opcionales**: el instalador lo pregunta (desactivadas
por defecto, porque añaden un contenedor pequeño y ocupan disco, pero
recomendadas). Si las activas, el contenedor `backup` hace una copia cada día a
la hora que elijas (03:00 por defecto) y conserva las de los últimos 14 días en
`./backups`; el instalador hace la primera al momento y te muestra el comando
exacto para restaurarla. Cada copia es un `.tar.gz` con:

- la base de datos de Headscale (una copia consistente aunque esté en marcha) y
  sus claves privadas, para que los dispositivos sigan registrados al restaurar;
- la base de datos de Authentik, si lo usas;
- la configuración (`.env`, `headscale-config.yaml`, `Caddyfile`...);
- la CA interna de Caddy con `SSL_MODE=selfsigned`.

Actívalas o desactívalas, o cambia la hora, la carpeta y la retención,
volviendo a ejecutar `./install.sh`. O edita `.env` y ejecuta `docker compose up -d`:

| Variable | Por defecto | |
|---|---|---|
| `BACKUP_ENABLED` | `false` | Añade también `backup` a `COMPOSE_PROFILES` |
| `BACKUP_SCHEDULE` | `0 3 * * *` | Sintaxis cron (zona horaria `TZ`); `off` la desactiva |
| `BACKUP_DIR` | `./backups` | Cualquier ruta del servidor, por ejemplo un NAS montado |
| `BACKUP_KEEP_DAYS` | `14` | Las copias más antiguas se borran |

Para hacer una copia en el momento: `make backup` (funciona aunque las copias
programadas estén desactivadas). Las copias contienen
secretos (`.env`): solo las puedes leer tú; guarda copias en un lugar seguro y
fuera de este servidor.

### Restaurar { #restore }

```bash
make restore file=backups/headscale-easy-20260929-030000.tar.gz
```

Detiene el stack, restaura la configuración (los ficheros actuales se guardan
como `*.before-restore-*`), la base de datos y las claves de Headscale, la base
de datos de Authentik y la CA de Caddy, y vuelve a arrancar el stack.

**En un servidor nuevo:** instala Docker, clona el repositorio, copia la copia de
seguridad y ejecuta el mismo comando; no hace falta pasar antes el instalador.

## Desinstalar { #uninstalling }

```bash
./uninstall.sh           # elimina los contenedores, conserva datos y configuración
./uninstall.sh --purge   # borra también volúmenes, configuración y ./data
```

## Registro de actividad { #activity-log }

**Registros** (solo admins, en la barra lateral) es el registro de actividad
de la tailnet, como el registro de auditoría de configuración de la consola de
Tailscale. Guarda:

- **Configuración**: cada cambio hecho desde la interfaz web — máquinas
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
- **Dispositivos**: cada 30 segundos la interfaz web compara el estado de
  Headscale y registra los dispositivos que se registran, se eliminan, se
  conectan o desconectan, cuya clave caduca, cuya versión de Tailscale cambia
  o que se renombran fuera de la interfaz web (por ejemplo con
  `headscale nodes rename`).

Cada evento tiene la hora, el autor (el nombre de usuario, `Clave de API
<prefijo>` en las sesiones con clave de API, o *Headscale* en los eventos de
dispositivos), la IP del cliente, el objetivo y los detalles. Nunca se guardan
secretos: de las claves de autenticación, de API y de los Auth ID solo se
guarda el prefijo.

Busca, filtra por categoría, autor y fechas (UTC), y descarga los eventos que
coinciden con el botón CSV. La primera página se actualiza sola.

El registro está en `./data/web/audit.db` (SQLite). Los eventos más antiguos
que `AUDIT_RETENTION_DAYS` de `.env` (por defecto `90`; `0` los guarda para
siempre) se borran automáticamente. Los cambios hechos fuera de la interfaz web
(el CLI `headscale`, la API) no son eventos de configuración, pero su efecto en
los dispositivos sí se registra.

!!! note "Nota"
    Headscale no tiene registros de flujos de red (qué dispositivo habló con
    cuál y cuándo): hacen falta datos de los clientes que solo recoge el
    servidor de coordinación de Tailscale.

## Resolución de problemas { #troubleshooting }

**Headscale nunca llega a estar sano (con OIDC).** No arranca hasta que puede
leer el documento de descubrimiento del issuer desde su contenedor. Comprueba:

```bash
docker compose logs headscale
docker exec caddy wget -qO- http://authentik-server:9000/authentik/application/o/headscale/.well-known/openid-configuration
```

Si Authentik responde pero Headscale no alcanza la URL pública, probablemente tu
router no hace NAT loopback: con `SSL_MODE=front` define `FRONT_PROXY_IP`.

**Errores de `redirect_uri` tras cambiar el dominio.** Vuelve a ejecutar
`./install.sh`: aplica de nuevo el blueprint de Authentik con las URLs nuevas
(Authentik no lo hace solo cuando sólo cambian variables de entorno).

**Los clientes dicen `x509: certificate signed by unknown authority`.** Usas
`SSL_MODE=selfsigned`: instala `caddy-root-ca.crt` en el cliente o pasa a
Let's Encrypt.

**Los dispositivos conectan pero no se ven entre sí.** Revisa la política ACL
(con aislamiento, cada usuario sólo alcanza sus dispositivos) y que el UDP 3478
esté abierto para el relay DERP.

**La consola dice que la API key caducó.** La consola renueva sola su API key
de Headscale cuando le quedan 15 días (guarda la nueva en `data/web/api-key`),
así que solo pasa si el servidor estuvo apagado todo ese tiempo o alguien la
caducó a mano. Vuelve a ejecutar `./install.sh`: crea una nueva.

**Alguien entró con Google pero no puede usar la VPN.** Las cuentas nuevas de
Google no tienen grupo. Añádelas a `headscale-users` en Authentik.

¿Sigues atascado? [Abre un issue](https://github.com/insanerask77/headscale-easy/issues/new/choose)
con la salida de `make health` y los logs relevantes (quita los secretos).
