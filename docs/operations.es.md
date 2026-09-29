# Operación

## Usuarios y admins { #users-and-admins }

Headscale Easy tiene dos roles:

- **Miembros**: ven y gestionan sólo sus máquinas y sus claves.
- **Admins**: ven todas las máquinas y usuarios y gestionan el DNS, la política
  ACL y las API keys.

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
todos). Busca por nombre, propietario, dirección, etiqueta o versión, y acota la
lista con **Filtros** (estado, propietario, necesita actualización, tiene rutas,
clave caducada). El botón de descarga exporta la lista actual en CSV.

Las etiquetas bajo cada nombre indican lo que tiene de especial una máquina:

| Etiqueta | Significado |
|---|---|
| Caducidad desactivada | Su clave no caduca nunca |
| Caducada | Su clave caducó: tiene que volver a iniciar sesión |
| Efímera | Se elimina sola cuando se desconecta |
| Subredes / Exit Node | Anuncia rutas; en naranja, alguna espera aprobación |
| `tag:…` | Etiquetas ACL |

El menú **⋯** (y la página de la máquina) permite renombrarla, expirar su clave
(obliga a iniciar sesión de nuevo), desactivar la caducidad, editar etiquetas,
aprobar **rutas de subred y exit nodes** o eliminarla. Los miembros pueden
renombrar, expirar y eliminar sus máquinas; rutas, etiquetas y caducidad son
sólo para admins, como en Tailscale.

Las apps de Tailscale que no pueden leer el nombre del dispositivo (iPhone,
iPad, Apple TV y la versión de la App Store para Mac) se registran como
`localhost`. Headscale Easy las renombra una vez a `<propietario>-<dispositivo>`,
por ejemplo `ana-iphone` o `leo-mac`; un nombre que pongas después no se vuelve a
cambiar. Pon `AUTO_RENAME_LOCALHOST=false` en `.env` para desactivarlo.

La flecha junto a la versión se pone roja cuando hay un cliente de Tailscale más
nuevo (pasa el ratón por encima para ver cuál).

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

```bash
make backup                 # en ./backups
make backup dir=/mnt/nas    # en otro sitio
```

Guarda `.env` y la configuración generada, el volumen de datos de Headscale
(base de datos y claves) y, con Authentik, un volcado de su base de datos. Los
ficheros contienen secretos: guárdalos en un lugar seguro.

Para restaurar en otra máquina: copia el repositorio y `.env`, restaura el
volumen `headscale-data` y la base de datos de Authentik, y ejecuta `./install.sh`.

## Desinstalar { #uninstalling }

```bash
./uninstall.sh           # elimina los contenedores, conserva datos y configuración
./uninstall.sh --purge   # borra también volúmenes, configuración y ./data
```

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

**La consola dice que la API key caducó.** Vuelve a ejecutar `./install.sh`
(crea una nueva) o crea una con `make apikey`, ponla en `.env` como
`HEADSCALE_API_KEY` y ejecuta `docker compose up -d web`.

**Alguien entró con Google pero no puede usar la VPN.** Las cuentas nuevas de
Google no tienen grupo. Añádelas a `headscale-users` en Authentik.

¿Sigues atascado? [Abre un issue](https://github.com/insanerask77/headscale-easy/issues/new/choose)
con la salida de `make health` y los logs relevantes (quita los secretos).
