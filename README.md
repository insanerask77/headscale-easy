# Headscale + Mi VPN - Despliegue Todo-en-Uno

<div align="center">

![Headscale](https://img.shields.io/badge/Headscale-Latest-blue?logo=tailscale)
![Mi VPN](https://img.shields.io/badge/Panel-Mi%20VPN-green)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker)
![License](https://img.shields.io/badge/License-MIT-yellow)

**Solución de despliegue automatizado para Headscale (control plane self-hosted compatible con Tailscale) + Mi VPN (panel web con la estructura de la consola de Tailscale)**

[Instalación Rápida](#-instalación-rápida) • [Características](#-características) • [Configuración](#-configuración) • [Uso](#-uso) • [Troubleshooting](#-troubleshooting)

</div>

---

## 📋 Tabla de Contenidos

- [Acerca del Proyecto](#-acerca-del-proyecto)
- [Características](#-características)
- [Requisitos](#-requisitos)
- [Instalación Rápida](#-instalación-rápida)
- [Configuración](#-configuración)
  - [Un solo modo de despliegue](#un-solo-modo-de-despliegue)
  - [Quién pone el HTTPS](#quién-pone-el-https)
  - [Un proxy por delante](#un-proxy-por-delante)
- [Uso](#-uso)
  - [El panel Mi VPN](#el-panel-mi-vpn)
  - [Login de usuarios con Authentik](#login-de-usuarios-con-authentik)
- [Arquitectura](#-arquitectura)
- [Reconfiguración](#-reconfiguración)
- [Backup y Restauración](#-backup-y-restauración)
- [Troubleshooting](#-troubleshooting)
- [Desinstalación](#-desinstalación)
- [Contribuir](#-contribuir)
- [Licencia](#-licencia)

---

## 🎯 Acerca del Proyecto

Este proyecto proporciona un **instalador interactivo todo-en-uno** que despliega:

- **[Headscale](https://github.com/juanfont/headscale)**: Control plane open-source compatible con Tailscale
- **Mi VPN** ([portal/](portal/)): panel web propio, con la estructura de la consola de Tailscale. Cada usuario gestiona sus dispositivos; los administradores, toda la VPN
- **[Caddy](https://caddyserver.com/)**: Reverse proxy, con TLS automático si lo eliges
- **[Authentik](https://goauthentik.io/)** (opcional): cuentas con usuario/contraseña y login con Google

Todo funcional con **un solo comando** (`./install.sh`), sin necesidad de editar archivos de configuración manualmente.

### ¿Por qué usar esto?

✅ **Cero configuración manual**: El instalador te guía paso a paso  
✅ **SSL automático**: Certificados Let's Encrypt o autofirmados  
✅ **Login de usuarios**: Authentik integrado (usuario/contraseña y Google) u OIDC propio  
✅ **Idempotente**: Puedes reconfigurar sin perder datos  
✅ **Producción ready**: Configuración segura por defecto  

---

## ✨ Características

### 🚀 Instalación

- ✅ Instalador interactivo en bash puro (sin dependencias adicionales)
- ✅ Detección automática de distribución Linux (Debian/Ubuntu, Fedora/RHEL, Arch)
- ✅ Instalación automática de Docker si no está presente
- ✅ Validación de inputs con valores por defecto sensatos
- ✅ Generación automática de secretos criptográficos
- ✅ Idempotente: ejecutar múltiples veces es seguro

### 🔐 Seguridad

- ✅ SSL/TLS con Let's Encrypt (certificado automático)
- ✅ Certificado autofirmado para redes privadas
- ✅ Headers de seguridad (HSTS, X-Frame-Options, etc.)
- ✅ Secretos generados automáticamente (nunca hardcodeados)
- ✅ Login con usuario/contraseña y Google vía Authentik integrado, u OIDC propio (Keycloak, etc.)
- ✅ Contenedores con mínimos privilegios (cap_drop, security_opt)

### 🎨 Interfaz

- ✅ Panel web propio (Mi VPN) con la estructura de la consola de Tailscale
- ✅ Usuarios: sólo ven y gestionan sus dispositivos y claves
- ✅ Administradores: dispositivos, usuarios, rutas, tags, ACL, DNS y API keys
- ✅ Tema claro/oscuro según el navegador

### 🛠️ Operación

- ✅ Docker Compose v2 (última versión)
- ✅ Healthchecks para todos los servicios
- ✅ Logs estructurados en JSON
- ✅ Volúmenes persistentes para datos críticos
- ✅ Reinicio automático de contenedores
- ✅ Desinstalador con opción de purga total

---

## 📦 Requisitos

### Mínimos

- **Sistema Operativo**: Linux (Debian/Ubuntu, Fedora/RHEL, Arch, o cualquier distro con Docker)
- **Docker**: >= 20.10 (se instala automáticamente si falta)
- **Docker Compose**: plugin v2 (se instala con Docker)
- **RAM**: >= 1GB
- **Disco**: >= 2GB libres
- **Puertos**:
  - `80/tcp` y `443/tcp` (si SSL habilitado)
  - `3000/tcp` (si SSL deshabilitado)
  - `3478/udp` (DERP/STUN, debe ser accesible externamente)

### Recomendados

- **Dominio**: con DNS apuntando al servidor (para Let's Encrypt)
- **RAM**: >= 2GB
- **CPU**: >= 2 cores

### Opcional

- **OIDC Provider**: no hace falta si eliges Authentik integrado; si no, Keycloak, Auth0, etc.
- **RAM extra con Authentik**: ~1 GB (Authentik server + worker + PostgreSQL)

---

## 🚀 Instalación Rápida

### Opción 1: Instalación Interactiva (Recomendada)

```bash
# 1. Clonar el repositorio
git clone https://github.com/tu-usuario/tailscale-selfhosted.git
cd tailscale-selfhosted

# 2. Ejecutar el instalador interactivo
./install.sh

# 3. ¡Listo! Accede a la URL mostrada al final
```

El instalador te preguntará:
- Dominio o IP de acceso
- **Quién pone el HTTPS**: Caddy con Let's Encrypt, Caddy con certificado
  autofirmado, un proxy que ya tengas por delante, o nadie (sólo HTTP).
  Ver [Quién pone el HTTPS](#quién-pone-el-https)
- Puertos a usar (con defaults razonables)
- Nombre de tu organización/tailnet
- Cómo inician sesión los usuarios: sólo API key, Authentik integrado
  (usuario/contraseña y Google) u OIDC propio.
  Ver [Login de usuarios con Authentik](#login-de-usuarios-con-authentik)

Al finalizar, los servicios estarán corriendo y listos para usar.

### Opción 2: Instalación Silenciosa (Avanzada)

Si prefieres configurar manualmente:

```bash
# 1. Clonar el repositorio
git clone https://github.com/tu-usuario/tailscale-selfhosted.git
cd tailscale-selfhosted

# 2. Copiar y editar el archivo de configuración
cp .env.example .env
nano .env

# 3. Generar configuraciones
export $(cat .env | xargs)
envsubst < templates/headscale-config.yaml.tmpl > headscale-config.yaml
envsubst < templates/Caddyfile.tmpl > Caddyfile

# 4. Levantar los servicios
docker compose up -d
```

> Por esta vía tendrás que escribir a mano `docker-compose.override.yml` con
> los puertos de Caddy: `docker-compose.yml` no los declara a propósito (ver
> [Puertos utilizados](#puertos-utilizados)).

---

## ⚙️ Configuración

### Un solo modo de despliegue

No hay modos que elegir. **Caddy arranca siempre** y enruta un único dominio:

```
cliente ──▶ Caddy ─┬─▶ /           Headscale   (control plane)
                   ├─▶ /mi-vpn     Mi VPN      (panel web; /admin redirige aquí)
                   └─▶ /authentik  Authentik   (sólo con AUTH_PROVIDER=authentik)
```

Headscale y el panel **no publican ningún puerto en el host**: sólo los
alcanza Caddy por la red interna de Docker. La única excepción es el UDP
DERP/STUN (3478), que va directo porque ningún proxy HTTP lo transporta.

### Quién pone el HTTPS

Es la única decisión de arquitectura, y se guarda en `SSL_MODE`:

| `SSL_MODE` | Quién termina TLS | URL pública | Puertos que publica Caddy |
|---|---|---|---|
| `letsencrypt` | Caddy, con certificado de Let's Encrypt | `https://vpn.midominio.com` | 80, 443, 443/udp |
| `selfsigned` | Caddy, con su CA interna (`tls internal`) | `https://vpn.midominio.com` | 80, 443, 443/udp |
| `front` | Un proxy que ya tienes por delante | `https://vpn.midominio.com` | 80 |
| `none` | Nadie | `http://vpn.midominio.com` | 80 |

- **`letsencrypt`** — lo normal si la máquina es alcanzable desde internet.
  Necesita DNS público apuntando aquí y los puertos 80/443 abiertos. Caddy
  renueva solo y redirige HTTP → HTTPS.
- **`selfsigned`** — HTTPS sin dependencias externas. Hay que instalar la CA
  de Caddy en **cada** cliente Tailscale o no conectarán; ver
  [Certificado autofirmado](#certificado-autofirmado-hay-que-instalar-la-ca-en-cada-cliente).
  El instalador exporta la CA en `caddy-root-ca.crt`.
- **`front`** — NPM, nginx, Traefik u otro Caddy terminan el TLS en otra
  máquina y reenvían aquí por HTTP. Ver [Un proxy por delante](#un-proxy-por-delante).
- **`none`** — sin cifrar. Para `http://localhost`, una LAN de confianza o un
  acceso que ya viaja por otra VPN.

Con `front` y `none`, el `Caddyfile` usa el site address `:80` en vez del
dominio, así que responde a **cualquier** `Host` (localhost, la IP, el
hostname con el que lo llame el proxy). Una dirección sin esquema ni host
tampoco dispara la emisión automática de certificados, por lo que no hace
falta `auto_https off`. El instalador no ofrece Let's Encrypt si `DOMAIN` es
una IP o `localhost`, porque el reto ACME no puede completarse para eso.

> ⚠️ Con `SSL_MODE=none` el plano de control viaja en claro, incluidas las
> pre-auth keys y las sesiones del panel. Úsalo sólo en una red de confianza.

### Un proxy por delante

Con `SSL_MODE=front` el instalador pregunta qué proxy usas y **escupe el
snippet ya rellenado** en `reverse-proxy/`:

| Respuesta | Fichero generado |
|---|---|
| Nginx Proxy Manager | `reverse-proxy/NGINX-PROXY-MANAGER.md` — los campos del Proxy Host y el bloque para la caja *Advanced* |
| nginx | `reverse-proxy/nginx-<dominio>.conf` — un `server{}` completo para `/etc/nginx/conf.d/` |
| Traefik | `reverse-proxy/traefik-<dominio>.yml` — configuración dinámica para el file provider |
| Caddy | `reverse-proxy/Caddyfile` — el bloque del Caddy de borde |

```
cliente ──HTTPS──▶ NPM ──HTTP──▶ Caddy ─┬─▶ /        Headscale
        (borde)                         └─▶ /mi-vpn  Mi VPN
```

Como Caddy ya enruta por ruta, **el proxy de delante tiene un solo destino**:
`BACKEND_HOST:HTTP_PORT`. No necesita saber nada de `/mi-vpn` ni de CORS, y por
eso el snippet es corto. Lo único que no puede faltar en cualquiera de ellos:

- **Paso del `Upgrade`** (en NPM, la casilla *Websockets Support*). `/ts2021`,
  el protocolo de control actual, viaja sobre una conexión *upgraded*; sin
  esto ningún nodo llega a registrarse. HTTP/2 prohíbe esas cabeceras, así que
  el salto hacia el backend tiene que ser HTTP/1.1.
- **`proxy_read_timeout 3600s` y `proxy_buffering off`.** `/machine/map` es un
  long-poll permanente; con el timeout por defecto (60 s) los nodos se
  reconectan en bucle.
- **`client_max_body_size 0`.** Los mapas de red pueden ser grandes.

> ⚠️ **El puerto UDP DERP (3478) no pasa por ningún reverse proxy HTTP.**
> Ábrelo directamente contra esta máquina, o desactiva `derp.server.enabled`
> en `headscale-config.yaml` y usa los relays públicos de Tailscale.

**IP real de los clientes.** Headscale lleva `trusted_proxies: 172.16.0.0/12`
(el rango de las redes bridge de Docker, donde vive Caddy). Con un proxy más
por delante, la cadena `X-Forwarded-For` tiene dos saltos: añade también el
CIDR de esa máquina en `headscale-config.yaml` o todos los nodos aparecerán en
los logs con la misma IP. Headscale rechaza el prefijo `/0`.

### Variables de Entorno Principales

El archivo `.env` (generado por `install.sh`) contiene todas las configuraciones:

| Variable | Descripción | Ejemplo |
|----------|-------------|---------|
| `DOMAIN` | Dominio o IP con el que se accede al stack | `vpn.midominio.com` |
| `SSL_MODE` | Quién pone el HTTPS (ver tabla de arriba) | `letsencrypt`, `selfsigned`, `front`, `none` |
| `SERVER_URL` | URL pública del control plane (`--login-server`) | `https://vpn.midominio.com` |
| `URL_SCHEME` | Derivado de `SSL_MODE` | `https` o `http` |
| `ACME_EMAIL` | Email para Let's Encrypt | `admin@midominio.com` |
| `FRONT_PROXY` | Qué snippet generar (sólo con `SSL_MODE=front`) | `npm`, `nginx`, `traefik`, `caddy` |
| `BACKEND_HOST` | IP de esta máquina vista desde el proxy | `192.168.1.10` |
| `HTTP_PORT` / `HTTPS_PORT` | Puertos que publica Caddy | `80` / `443` |
| `TAILNET_NAME` | Nombre de la organización | `myorg` |
| `AUTH_PROVIDER` | Cómo inician sesión los usuarios | `none`, `authentik`, `external` |
| `COMPOSE_PROFILES` | Servicios opcionales (lo deriva el instalador) | `authentik` o vacío |
| `GOOGLE_CLIENT_ID` | Login con Google en Authentik (vacío = no) | `123-abc.apps.googleusercontent.com` |
| `ENABLE_OIDC` | Habilitar OIDC (derivado de `AUTH_PROVIDER`) | `true` o `false` |
| `OIDC_ISSUER_URL` | URL del proveedor OIDC | `https://auth.example.com/realms/master` |
| `PORTAL_API_KEY_LOGIN` | El panel acepta también la API key (admins) | `true` o `false` |
| `PORTAL_ADMIN_EMAILS` | Administradores con un proveedor OIDC propio | `jefe@midominio.com` |
| `NETWORK_ISOLATION` | Cada usuario sólo alcanza sus dispositivos | `true` o `false` |

Ver [.env.example](.env.example) para la lista completa de variables.

### Puertos Utilizados

| Puerto | Protocolo | Servicio | Descripción |
|--------|-----------|----------|-------------|
| 80 | TCP | Caddy | HTTP. Con certificado propio sólo hace el reto ACME y redirige a HTTPS; con `front` o `none` sirve el tráfico |
| 443 | TCP/UDP | Caddy | HTTPS / HTTP/3. Sólo se publica si Caddy tiene el certificado |
| 3478 | UDP | Headscale | DERP/STUN — **siempre directo, nunca vía proxy** |
| 8000 | TCP | Mi VPN | Panel web — interno, no se publica |
| 8080 | TCP | Headscale | API HTTP — interno, no se publica |
| 50443 | TCP | Headscale | gRPC — interno |
| 9090 | TCP | Headscale | Metrics — interno, opcional |

Los puertos de Caddy se publican desde `docker-compose.override.yml`, que
genera el instalador, y no desde `docker-compose.yml`: Compose **fusiona** las
listas de `ports` añadiendo, nunca quitando, así que un override no podría
retirar el 443 cuando no hay certificado.

---

## 📖 Uso

### Primeros Pasos

El instalador ya crea el **usuario administrador** y la **API key**, y muestra
esta última por pantalla al terminar. Guárdala: Headscale sólo la revela en el
momento de crearla.

1. **Entrar en el panel**:
   ```
   Abre en tu navegador: https://vpn.midominio.com/mi-vpn/
   ```
   Con Authentik o un OIDC propio, inicia sesión con tu cuenta; sin OIDC, pega
   la API key. Ver [El panel Mi VPN](#el-panel-mi-vpn).

2. **Generar una clave de pre-autenticación**:
   ```bash
   # Ojo: desde Headscale 0.29 --user espera el ID numérico, no el nombre
   docker exec headscale headscale users list      # busca el ID de 'admin'
   docker exec headscale headscale preauthkeys create \
     --user 1 \
     --reusable \
     --expiration 24h
   ```
   El helper acepta el nombre y resuelve el ID por ti:
   ```bash
   ./scripts/utils.sh preauth:create admin
   ```

3. **Conectar un dispositivo**:
   ```bash
   # En tu dispositivo con Tailscale instalado
   tailscale up --login-server=https://vpn.midominio.com --authkey=<tu-clave>
   ```

### Certificado autofirmado: hay que instalar la CA en cada cliente

Con `SSL_MODE=selfsigned`, Caddy firma con su CA interna. El navegador solo
muestra un aviso que puedes saltarte, pero **el cliente Tailscale directamente
se niega a conectar**:

```
Received error: fetch control key: Get "https://vpn.midominio.com/key?v=142":
x509: certificate signed by unknown authority
```

El instalador exporta la CA raíz a `./caddy-root-ca.crt`. Instálala en el
almacén de confianza de cada dispositivo **antes** de `tailscale up`:

```bash
# Linux (Debian/Ubuntu)
sudo cp caddy-root-ca.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates

# macOS
sudo security add-trusted-cert -d -k /Library/Keychains/System.keychain caddy-root-ca.crt

# Windows (PowerShell como administrador)
Import-Certificate -FilePath caddy-root-ca.crt -CertStoreLocation Cert:\LocalMachine\Root
```

Si la pierdes:

```bash
docker exec caddy cat /data/caddy/pki/authorities/local/root.crt > caddy-root-ca.crt
```

> ⚠️ Android e iOS no permiten que Tailscale use CAs propias. Para móviles
> necesitas **Let's Encrypt** (`SSL_MODE=letsencrypt`), que no requiere instalar
> nada en el cliente.

### Las tres claves del stack (no las confundas)

Los tres tipos de clave empiezan por `hskey-` y es fácil pegar la que no toca.
Hacerlo produce un error 500 confuso del tipo
`auth ID has invalid length: expected 38, got 101`.

| Clave | Prefijo | Longitud | Para qué sirve |
|---|---|---|---|
| **API key** | `hskey-api-…` | 87 | Acceso de administrador a la API de Headscale (y al panel sin OIDC) |
| **Pre-auth key** | `hskey-auth-…` | 88 | `tailscale up --authkey=…` |
| **Auth ID** | `hskey-authreq-…` | 38 | Registrar un dispositivo en el panel: Dispositivos → *Registrar con Auth ID* |

El **Auth ID** no se genera con ningún comando: lo imprime `tailscale up` cuando
lo lanzas **sin** `--authkey`:

```bash
tailscale up --login-server=https://vpn.midominio.com
# -> To authenticate, visit: https://vpn.midominio.com/register/hskey-authreq-XXXXXXXXXXXXXXXXXXXXXXXX
```

Esa última parte (`hskey-authreq-…`, 38 caracteres) es lo que se pega en el
panel; también acepta la URL completa. Lo habitual, de todos modos, es que el
propio usuario abra esa URL e inicie sesión: el Auth ID es para registrar a
mano un dispositivo a nombre de otro.

### Dos URLs distintas: control plane y panel

Headscale y el panel son servicios separados, pero comparten dominio: Caddy
enruta por ruta.

| | URL |
|---|---|
| Control plane (Headscale) | `https://vpn.midominio.com` |
| Panel (Mi VPN) | `https://vpn.midominio.com/mi-vpn/` |

La primera es la que va en `--login-server`. `/mi-vpn` va al panel (y `/admin`,
donde estaba Headplane, redirige ahí) y **todo lo demás** a Headscale, porque
los clientes Tailscale usan la raíz del dominio (`/key`, `/ts2021`,
`/machine/*`, `/derp`, `/bootstrap-dns`…).

### API key de Headscale

El panel habla con Headscale con la API key que genera el instalador y guarda
en `.env` (`HEADSCALE_API_KEY`). Sin OIDC es además la credencial para entrar
en el panel como administrador (no hay usuario/contraseña propios). En el
panel, Ajustes → Claves → *API keys* las lista, crea y caduca, y marca la que
usa el propio panel para que no la caduques por error. Por línea de comandos:

```bash
docker exec headscale headscale apikeys create --expiration 90d
docker exec headscale headscale apikeys list
docker exec headscale headscale apikeys expire --prefix <prefijo>
```

> ⚠️ Da control total sobre el tailnet. Trátala como una contraseña de
> administrador y ten en cuenta que caduca (90 días por defecto,
> configurable con `APIKEY_EXPIRATION`). **Cuando caduca, el panel deja de
> funcionar**: reejecuta `./install.sh`, que la renueva.

### El panel Mi VPN

`https://<DOMAIN>/mi-vpn/` es el panel de la VPN ([portal/](portal/), Python
sin dependencias), con la estructura de la consola de Tailscale. Sustituye a
Headplane, que ya no forma parte del stack (`/admin` redirige aquí). Tema
oscuro o claro según el navegador.

**Lo que ve cada usuario** (sólo lo suyo):

| Sección | Qué hay (equivalente en Tailscale) |
|---------|------------------------------------|
| **Dispositivos** | Tabla con búsqueda (tecla `/`) y filtro por estado; IPs, SO, versión de Tailscale con aviso de actualización, última conexión e insignias (caducado, caducidad desactivada, nodo de salida, subredes, efímero, tags). Menú `···`: editar nombre, copiar IP, caducar clave, quitar. *(Machines)* |
| **Detalle** | Propietario, SO, versión, arquitectura, método de registro, clave de nodo, fechas y caducidad; direcciones IPv4/IPv6 y nombre MagicDNS; rutas anunciadas y su estado; relay DERP preferido y endpoints. *(Machine page)* |
| **Añadir dispositivo** | Instrucciones por sistema (Linux, Windows, macOS, iOS, Android) apuntando a este servidor. *(Add device)* |
| **DNS** | Nombre de la red, MagicDNS y el nombre de cada dispositivo, servidores de nombres, split DNS y dominios de búsqueda. *(DNS)* |
| **Ajustes → Claves** | Claves de autenticación de un solo uso o reutilizables, efímeras, de 1 a 90 días; se muestran una única vez; revocar. *(Settings → Keys)* |
| **Ajustes → General** | Cuenta, rol y grupos, cambio de contraseña (Authentik) y tema. *(Settings → General)* |

**Lo que añade un administrador** (lo que antes hacía Headplane):

| Sección | Qué hay |
|---------|---------|
| **Dispositivos** | Los de **todos** los usuarios, con filtro por usuario. *Registrar con Auth ID* (para un `tailscale up` sin clave, a nombre de quien elijas). En el menú y el detalle: editar tags, activar/desactivar la caducidad de la clave y **aprobar rutas** (subredes y nodo de salida). |
| **Usuarios** | Usuarios de Headscale, con sus dispositivos. Crear usuarios locales (para servidores con claves), renombrar, eliminar (si no tienen dispositivos) y generar claves a su nombre. Con Authentik, acceso directo al alta de cuentas. |
| **Control de acceso** | Editor de la política ACL (HuJSON) con *Comprobar* y *Guardar*; Headscale valida antes de aplicar y los errores se muestran tal cual. |
| **DNS** | **Editable**: MagicDNS, nombre de la red, usar los servidores de nombres en los dispositivos, servidores globales (IP o DoH), split DNS y dominios de búsqueda. |
| **Ajustes → Claves** | Las claves de todos los usuarios, y las **API keys** de Headscale (crear, caducar). La que usa el propio panel está marcada y no se puede caducar desde aquí. |

**Cómo se entra**, según `AUTH_PROVIDER`:

| `AUTH_PROVIDER` | Inicio de sesión | Quién es administrador |
|---|---|---|
| `authentik` | Cuenta de Authentik (cliente OIDC propio `mi-vpn`) | Grupos `vpn-admins` y `authentik Admins` |
| `external` | Tu proveedor OIDC, con el mismo cliente que Headscale. Registra también `https://<DOMAIN>/mi-vpn/callback` como Redirect URI | Los emails de `PORTAL_ADMIN_EMAILS` |
| `none` | API key de Headscale | Siempre (sólo hay sesiones de admin) |

Con OIDC, `PORTAL_API_KEY_LOGIN=true` añade además el login con API key como
acceso de emergencia si el proveedor falla. Cerrar sesión en el panel cierra
también la del proveedor, así que en el mismo navegador se puede entrar después
con otro usuario.

**Cómo funciona el DNS editable.** La sección `dns:` de
`headscale-config.yaml` va entre dos marcadores (`# >>> dns` … `# <<< dns`) y
es lo único que el panel reescribe. Al guardar: escribe el bloque, ejecuta
`headscale configtest`, y si pasa reinicia Headscale y espera a que esté sano
(unos 10 s sin plano de control; las conexiones ya establecidas entre
dispositivos siguen funcionando). Si Headscale rechaza la configuración o no
arranca, restaura la anterior. `install.sh` conserva ese bloque al regenerar la
configuración, así que los cambios no se pierden al reinstalar.

> ⚠️ Para eso el panel monta el socket de Docker, y **acceso al socket de
> Docker equivale a root en el host**. El panel sólo lo usa para
> `headscale configtest` y reiniciar el contenedor `headscale`, corre con el uid
> dueño del proyecto (no root) y con el sistema de ficheros en sólo lectura,
> pero tenlo presente: una vulnerabilidad en el panel podría escalar al host.

**Datos que usa**: la API REST de Headscale para todo lo que se modifica; el
SO, la versión y el relay DERP, que esa API no da, del `Hostinfo` de la base
de datos de Headscale, montada **en sólo lectura** (con PostgreSQL esas
columnas quedan vacías). Si hay salida a internet, consulta además los nombres
de los relays públicos y la última versión de Tailscale.

**Seguridad**: el panel localiza a cada usuario en Headscale por su
identificador OIDC, no por el nombre, y comprueba en el servidor el rol y la
propiedad de cada dispositivo, clave o usuario que se toca. Un usuario no
puede ver ni modificar lo de otro aunque manipule la petición.

### Login de usuarios con Authentik

Headscale no tiene usuarios con contraseña: sin OIDC, al panel sólo entra un
administrador con la API key y los dispositivos se registran con pre-auth
keys. Con `AUTH_PROVIDER=authentik` el instalador añade
[Authentik](https://goauthentik.io) al stack y lo deja configurado:

- Vive en `https://<DOMAIN>/authentik/`, el mismo dominio. No hace falta DNS
  ni certificado nuevos, ni tocar el proxy de delante. No usa `/auth`
  porque Headscale ya la ocupa (`/auth/{id}`).
- El blueprint [authentik/blueprints/headscale.yaml](authentik/blueprints/headscale.yaml)
  crea las aplicaciones OIDC (VPN y Mi VPN), los grupos, el tema, el
  formulario de alta y, si se configuró, el login con Google.

**Quién puede entrar lo deciden los grupos de Authentik:**

| Grupo | VPN (`tailscale up`) | Panel Mi VPN |
|-------|----------------------|--------------|
| `headscale-users` | ✅ | ✅ sus dispositivos |
| `vpn-admins` | ✅ | ✅ administrador |
| `authentik Admins` (akadmin) | ✅ | ✅ administrador |
| sin grupo | ❌ | ❌ "Permission denied" |

**Dar acceso a alguien:** abre `https://<DOMAIN>/alta-usuario`. Es un
formulario que pide nombre, usuario, email, contraseña (mínimo 10 caracteres)
y el acceso: *VPN* (`headscale-users`) o *VPN + administrador de la VPN*
(`vpn-admins`). Al enviarlo, la cuenta queda lista y "Continuar" lleva a
Usuarios del panel. Sólo pueden usarlo los administradores; no hace falta
entrar en el panel de Authentik. También está en el panel (Usuarios → *Dar de
alta una cuenta*) y como aplicación "Alta de usuarios" en
`https://<DOMAIN>/authentik/if/user/`.

El usuario `akadmin` y su contraseña inicial los muestra el instalador al
acabar. Para todo lo demás de las cuentas (bajas, cambiar grupos, restablecer
contraseñas) está `https://<DOMAIN>/authentik/if/admin/`.

> Las instalaciones anteriores tenían un grupo `headplane-admins`: el blueprint
> lo elimina y crea `vpn-admins`. Si tenía miembros, añádelos al nuevo grupo.

#### Aislamiento de red por usuario

Con `NETWORK_ISOLATION=true` (por defecto), el instalador aplica esta
política ACL a Headscale:

```json
{"acls": [{"action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:self:*"]}]}
```

Los dispositivos de un usuario se ven entre sí, pero no ven los de nadie
más, admins incluidos. La app de Tailscale de cada dispositivo muestra
también sólo los del propio usuario. La política sólo se aplica si Headscale
todavía no tiene una: si ya la editaste en el panel (Control de acceso), se
respeta.

#### Tema "Headscale Dashboard"

Las pantallas de Authentik (login, alta de usuarios) y el portal Mi VPN
llevan un tema propio con aspecto Tailscale y el logo "Headscale Dashboard"
(una rejilla de puntos que forma una H), en **oscuro o claro según el
navegador**. En Authentik cada usuario puede fijarlo en sus ajustes; en Mi VPN,
con el botón ☀/☾ de la barra superior (se recuerda en ese navegador). Si el
navegador no indica preferencia, el portal usa el oscuro. Los ficheros están
en [authentik/branding/](authentik/branding/):

| Fichero | Qué es |
|---------|--------|
| `logo-light.svg` / `logo-dark.svg` | Logo de la pantalla de login, para cada tema |
| `favicon.svg` | Icono de la pestaña |
| `background.svg` | Fondo (trama de puntos) |
| `custom.css` | Colores, tipografía y formas |

Para cambiar el logo, el favicon o el fondo basta con reemplazar el fichero:
se sirven directamente. El portal tiene su propia hoja de estilos
([portal/static/style.css](portal/static/style.css)), con la misma paleta. Si editas `custom.css`, aplícalo con
`docker exec authentik-worker ak apply_blueprint custom/headscale.yaml`
(tarda 1-2 minutos), o ejecuta `./install.sh`.

**Registrar un dispositivo:** `tailscale up --login-server=https://<DOMAIN>`
sin `--authkey`. Abre el login de Authentik en el navegador y, tras la
confirmación de Headscale, el nodo queda a nombre de ese usuario.

#### Login con Google

Requiere HTTPS: Google no acepta Redirect URIs `http://`. En
[Google Cloud Console](https://console.cloud.google.com/apis/credentials) crea
un *OAuth client ID* de tipo *Web application* con:

- Authorized JavaScript origins: `https://<DOMAIN>`
- Authorized redirect URIs: `https://<DOMAIN>/authentik/source/oauth/callback/google/`

Pega el Client ID y el secret cuando el instalador lo pida (o ponlos en
`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` y reejecuta `./install.sh`).
Aparecerá el botón de Google en el login de Authentik.

Quien entra con Google por primera vez obtiene cuenta en Authentik **sin
grupo**, así que no accede a nada hasta que lo añadas a uno. Si su email
coincide con el de un usuario local, entra como ese usuario.

#### Cómo llega Headscale a Authentik

Headscale y el panel validan el issuer contra la URL **pública**
(`https://<DOMAIN>/authentik/application/o/headscale/`), así que tienen que
alcanzarla desde sus contenedores. El instalador lo resuelve en
`docker-compose.override.yml`:

- `letsencrypt`, `selfsigned`, `none`: `extra_hosts` apunta `DOMAIN` al host
  (`host-gateway`), donde Caddy publica sus puertos. No depende del NAT
  loopback del router. Con `selfsigned` monta además la CA de Caddy
  (`data/caddy-ca/`).
- `front`: pasa por el proxy de delante, así que el instalador **se detiene
  hasta que lo hayas configurado**. Si `DOMAIN` resuelve a una IP pública y
  tu router no hace NAT loopback, indica `FRONT_PROXY_IP` (la IP del proxy
  en la LAN).
- `DOMAIN=localhost` no es compatible: dentro de un contenedor, `localhost`
  es el propio contenedor.

Si un firewall en el host (ufw, firewalld) filtra el tráfico de las redes de
Docker hacia los puertos de Caddy, Headscale no arrancará. Ver
[troubleshooting](#headscale-no-arranca-con-authentik).

### Gestión de Usuarios

```bash
# Listar usuarios
docker exec headscale headscale users list

# Crear usuario
docker exec headscale headscale users create <nombre>

# Eliminar usuario
docker exec headscale headscale users destroy <nombre>
```

### Gestión de Nodos

```bash
# Listar nodos
docker exec headscale headscale nodes list

# Listar nodos de un usuario
docker exec headscale headscale nodes list --user admin

# Eliminar nodo
docker exec headscale headscale nodes delete --identifier <id>

# Expirar nodo
docker exec headscale headscale nodes expire --identifier <id>
```

### Gestión de Rutas

```bash
# Listar rutas
docker exec headscale headscale routes list

# Aprobar ruta
docker exec headscale headscale routes enable --route <id>

# Desaprobar ruta
docker exec headscale headscale routes disable --route <id>
```

### Ver Logs

```bash
# Todos los servicios
docker compose logs -f

# Solo Headscale
docker compose logs -f headscale

# Solo el panel Mi VPN
docker compose logs -f portal

# Solo Caddy
docker compose logs -f caddy
```

### Estado de Servicios

```bash
# Ver estado
docker compose ps

# Reiniciar servicios
docker compose restart

# Detener servicios
docker compose down

# Iniciar servicios
docker compose up -d
```

---

## 🏗️ Arquitectura

### Diagrama de Flujo

```
Internet
    │
    │ HTTPS (443)                                  UDP (3478) DERP/STUN
    ▼                                                      │
┌─────────┐  /            ┌───────────────┐                │
│  Caddy  │──────────────▶│   Headscale   │◀───────────────┘
│ (Proxy) │               │(Control Plane)│
└─────────┘               └───────────────┘
    │  /mi-vpn                ▲   ▲
    ▼                  API    │   │ configtest y reinicio (socket de Docker)
┌─────────┐───────────────────┘   │ tras editar el DNS
│ Mi VPN  │───────────────────────┘
│ (panel) │── BD de Headscale (sólo lectura: SO, versión, DERP)
└─────────┘
    │  /authentik (opcional)
    ▼
┌───────────┐
│ Authentik │  cuentas, grupos, login con Google
└───────────┘
```

### Componentes

1. **Headscale**: Control plane que gestiona la red mesh
   - Asigna IPs a los nodos
   - Gestiona ACLs y rutas
   - Proporciona servidor DERP embebido
   - Expone la API REST que usa el panel

2. **Mi VPN** (`portal`, contenedor `mi-vpn`): panel web, siempre activo
   - Usuarios: sus dispositivos y claves. Administradores: toda la VPN
   - Login con OIDC (Authentik o propio) y/o API key
   - Imagen oficial de Python, sólo biblioteca estándar, sin estado propio
   - Sustituye a Headplane

3. **Caddy**: Reverse proxy (arranca siempre)
   - Enruta el único dominio: `/` → Headscale, `/mi-vpn/` → panel (`/admin`
     redirige ahí) y, si Authentik está activado, `/authentik/` → Authentik y
     `/alta-usuario` → formulario de alta
   - Certificado automático de Let's Encrypt o CA interna, según `SSL_MODE`
   - Redirección HTTP → HTTPS cuando es él quien tiene el certificado
   - Headers de seguridad

4. **Authentik** (opcional, profile `authentik`): proveedor de identidad
   - Usuarios con contraseña, grupos y login con Google
   - `authentik-server`, `authentik-worker` y `authentik-postgresql`
   - Sin puertos publicados: se sirve a través de Caddy

### Volúmenes

```
headscale-data/          → Base de datos SQLite y claves de Headscale
headscale-socket/        → Socket Unix de la CLI de Headscale
caddy-data/              → Certificados SSL (Let's Encrypt)
caddy-config/            → Configuración persistente de Caddy
data/caddy-logs/         → Logs de acceso de Caddy
authentik-db/            → PostgreSQL de Authentik (usuarios, grupos, config)
authentik-data/          → Datos de Authentik (/data)
authentik-media/         → Ficheros subidos desde la UI de Authentik (iconos, logos)
data/caddy-ca/           → CA de Caddy para Headscale y el panel (selfsigned + Authentik)
headplane-data/          → (instalaciones anteriores) datos de Headplane; ya no se usa
```

---

## 🔄 Reconfiguración

Para cambiar la configuración después de la instalación:

### Método 1: Re-ejecutar el Instalador

```bash
./install.sh
```

El instalador detectará la instalación existente y te permitirá reconfigurar sin perder datos.

### Método 2: Editar Manualmente

```bash
# 1. Editar variables de entorno
nano .env

# 2. Regenerar configuraciones (si cambiaste variables que afectan a los .yaml)
#    Mejor con ./install.sh: además conserva el bloque DNS que edita el panel
export $(cat .env | xargs)
envsubst < templates/headscale-config.yaml.tmpl > headscale-config.yaml

# 3. Reiniciar servicios
docker compose down
docker compose up -d
```

### Cambiar quién pone el HTTPS

Re-ejecuta el instalador y responde otra cosa a la pregunta del HTTPS:

```bash
./install.sh
```

El instalador lee el `.env` actual, propone los valores existentes como
predeterminados y regenera todo lo que cambie: `headscale-config.yaml`,
`Caddyfile`, `docker-compose.override.yml` y `reverse-proxy/`.

> ⚠️ Cambiar `SERVER_URL` (por ejemplo al pasar de `http://ip:8080` a
> `https://ts.midominio.com`) **invalida el registro de los nodos ya
> conectados**: apuntan a la URL antigua y tendrás que volver a ejecutar
> `tailscale up --login-server=<nueva-url>` en cada uno.

---

## 💾 Backup y Restauración

### ¿Qué Respaldar?

Es crítico respaldar:

1. **Base de datos de Headscale** (contiene usuarios, nodos, rutas):
   - `data/` (directorio completo)
   - O volumen Docker: `headscale-data`

2. **Configuraciones**:
   - `.env`
   - `headscale-config.yaml` (incluye el DNS editado desde el panel)

3. **Certificados SSL** (opcional, se regeneran automáticamente):
   - Volumen Docker: `caddy-data`

4. **Authentik** (si está activado): usuarios, contraseñas y grupos viven en
   su PostgreSQL, no en Headscale:
   ```bash
   docker exec authentik-postgresql pg_dump -U authentik authentik > authentik-$(date +%F).sql
   ```
   Guarda también `AUTHENTIK_SECRET_KEY` y `AUTHENTIK_PG_PASS` de `.env`: sin
   ellos el volcado no sirve para restaurar.

### Crear Backup

```bash
# Método 1: Backup de directorios locales
tar -czf headscale-backup-$(date +%Y%m%d).tar.gz \
  .env \
  headscale-config.yaml \
  data/

# Método 2: Backup de volúmenes Docker
docker run --rm \
  -v headscale-data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/headscale-data-backup-$(date +%Y%m%d).tar.gz -C /data .
```

### Restaurar Backup

```bash
# 1. Detener servicios
docker compose down

# 2. Restaurar archivos
tar -xzf headscale-backup-YYYYMMDD.tar.gz

# 3. Restaurar volumen (si usaste método 2)
docker run --rm \
  -v headscale-data:/data \
  -v $(pwd):/backup \
  alpine sh -c "cd /data && tar xzf /backup/headscale-data-backup-YYYYMMDD.tar.gz"

# 4. Reiniciar servicios
docker compose up -d
```

### Automatizar Backups

Crear un cron job:

```bash
# Editar crontab
crontab -e

# Agregar backup diario a las 2 AM
0 2 * * * cd /ruta/a/tailscale-selfhosted && tar -czf /backups/headscale-backup-$(date +\%Y\%m\%d).tar.gz .env *.yaml data/
```

---

## 🔧 Troubleshooting

### Los servicios no inician

**Síntoma**: `docker compose ps` muestra contenedores detenidos o en estado "unhealthy"

**Solución**:
```bash
# Ver logs de todos los servicios
docker compose logs -f

# Ver logs de un servicio específico
docker compose logs -f headscale

# Verificar healthcheck
docker inspect headscale | grep -A 20 Health
```

### Puerto UDP 3478 bloqueado

**Síntoma**: Los clientes no pueden conectarse entre sí (solo al servidor)

**Verificación**:
```bash
# Verificar que el puerto está abierto
sudo netstat -unl | grep 3478

# Verificar firewall
sudo ufw status  # Ubuntu/Debian
sudo firewall-cmd --list-all  # Fedora/RHEL
```

**Solución**:
```bash
# Ubuntu/Debian
sudo ufw allow 3478/udp

# Fedora/RHEL
sudo firewall-cmd --permanent --add-port=3478/udp
sudo firewall-cmd --reload

# iptables directo
sudo iptables -A INPUT -p udp --dport 3478 -j ACCEPT
```

### Problemas con un proxy por delante (`SSL_MODE=front`)

| Síntoma | Causa | Solución |
|---------|-------|----------|
| Los nodos se desconectan y reconectan cada ~60 s | `/machine/map` es un long-poll y el proxy lo corta con su `proxy_read_timeout` por defecto | `proxy_read_timeout 3600s;` y `proxy_buffering off;` en el proxy |
| `tailscale up` se queda colgado sin error | `/ts2021` necesita un `Upgrade` de HTTP/1.1 | Activa *Websockets Support* en NPM, o las cabeceras `Upgrade`/`Connection` en nginx |
| El panel carga sin estilos o redirige mal | Se reescribió el prefijo `/mi-vpn` en el proxy | Reenvía el dominio entero sin `rewrite`: Caddy ya enruta |
| `413 Request Entity Too Large` al registrar un nodo | El mapa de red supera el límite del proxy | `client_max_body_size 0;` |
| Todos los nodos aparecen con la misma IP en los logs | Falta el CIDR del proxy en `trusted_proxies` | Añádelo en `headscale-config.yaml` y `docker compose restart headscale` |
| El navegador pierde la sesión del panel al recargar | `HEADSCALE_PUBLIC_URL` en `http://` pero se sirve por HTTPS | Re-ejecuta el instalador: la cookie es `Secure` sólo si la URL pública es `https://` |
| Los nodos conectan pero no se ven entre sí | UDP 3478 cerrado (no pasa por el proxy) | Ábrelo directo contra esta máquina |

Comprobación rápida desde la máquina del proxy, saltándose el proxy y hablando
directamente con Caddy:

```bash
# El control plane responde con su clave pública
curl -s http://<BACKEND_HOST>:<HTTP_PORT>/key?v=142

# El panel responde en /mi-vpn/ (sin sesión redirige al login: 303)
curl -sI http://<BACKEND_HOST>:<HTTP_PORT>/mi-vpn/ | head -1
```

Si estos dos funcionan pero el dominio público no, el problema está en el
proxy; si fallan, está en esta máquina (firewall, o Caddy no arrancó:
`docker compose logs caddy`).

### Let's Encrypt falla (DNS no propagado)

**Síntoma**: Caddy no puede obtener certificado, logs muestran error ACME

**Verificación**:
```bash
# Verificar que el DNS apunta correctamente
nslookup vpn.midominio.com

# Verificar que los puertos 80/443 son accesibles desde internet
# (desde otra máquina)
curl -I http://vpn.midominio.com
```

**Solución**:
```bash
# Opción 1: Esperar a que el DNS se propague (puede tomar hasta 48h)
# Caddy reintentará automáticamente

# Opción 2: Usar certificado autofirmado temporalmente.
# Re-ejecuta el instalador y elige "Caddy, con certificado autofirmado":
# él regenera el Caddyfile con las directivas correctas.
./install.sh
```

> No basta con cambiar `SSL_MODE` en `.env`: el `Caddyfile` no se genera sólo
> con `envsubst` desde `.env`, el instalador calcula además el site address,
> la directiva `tls`, el HSTS y el bloque de redirección.

### El panel muestra "Algo ha fallado"

**Síntoma**: `/mi-vpn/` carga pero las páginas muestran un error genérico.

La causa habitual es que el panel no puede hablar con Headscale: Headscale
caído o la API key de `.env` caducada (90 días por defecto).

```bash
docker compose ps headscale portal
docker compose logs --tail=50 portal        # verás la traza completa del error
docker exec headscale headscale apikeys list # ¿la de .env sigue vigente?
```

**Solución**: si la API key caducó, `./install.sh` genera otra y reinicia el
panel. Si Headscale está caído, `docker compose logs headscale`.

### El panel no deja editar el DNS

**Síntoma**: en DNS aparece un aviso en lugar del formulario.

- *"config.yaml no tiene el bloque DNS gestionado"*: la configuración viene
  de una versión anterior. Reejecuta `./install.sh` una vez.
- *"no tiene permiso de escritura"*: `PORTAL_UID`/`PORTAL_GID` de `.env` no
  coinciden con el dueño de `headscale-config.yaml`. Reejecuta `./install.sh`,
  que los detecta.
- *"no tiene acceso a Docker"*: `DOCKER_GID` no es el grupo de
  `/var/run/docker.sock` (`stat -c %g /var/run/docker.sock`).

### Certificado autofirmado no es confiable

**Síntoma**: El navegador muestra advertencia de seguridad con certificado autofirmado

**Solución**:

Esto es **normal** con certificados autofirmados. Tienes 3 opciones:

1. **Aceptar la advertencia** (seguro en redes privadas):
   - Chrome/Edge: Clic en "Avanzado" → "Continuar"
   - Firefox: Clic en "Avanzado" → "Aceptar el riesgo"

2. **Instalar el certificado en tu sistema**:
   ```bash
   # Obtener certificado
   docker exec caddy cat /data/caddy/certificates/acme-v02.api.letsencrypt.org-directory/vpn.midominio.com/vpn.midominio.com.crt > cert.crt
   
   # Instalar (varía según SO)
   # Ubuntu/Debian:
   sudo cp cert.crt /usr/local/share/ca-certificates/
   sudo update-ca-certificates
   
   # Firefox: Importar manualmente en Settings → Certificates
   ```

3. **Usar Let's Encrypt** (requiere dominio válido):
   ```bash
   ./install.sh  # Re-ejecutar y elegir Let's Encrypt
   ```

### Headscale no arranca con Authentik

Con OIDC, Headscale no arranca (`only_start_if_oidc_is_available`) hasta que
descarga la configuración del issuer desde la URL pública. Síntoma: el
contenedor se reinicia en bucle y `docker compose logs headscale` muestra un
error OIDC.

```bash
# ¿Authentik sirve el proveedor? (desde la red interna)
docker exec caddy wget -qO- http://authentik-server:9000/authentik/application/o/headscale/.well-known/openid-configuration

# ¿Se aplicó el blueprint? Busca errores de "headscale.yaml"
docker compose logs authentik-worker | grep -i blueprint
```

- Si lo primero falla, el blueprint no se ha aplicado: revisa
  Authentik → Customization → Blueprints.
- Si lo primero funciona pero Headscale sigue sin arrancar, no alcanza la URL
  pública desde su contenedor. Revisa el firewall del host hacia los puertos
  de Caddy desde las redes de Docker, `FRONT_PROXY_IP` con `SSL_MODE=front`, y
  que exista `data/caddy-ca/root.crt` con `selfsigned`.

**Error 400 en `/authentik/application/o/authorize/` tras cambiar el dominio**
(`redirect_uri_no_match` en los logs de Authentik): el proveedor conserva las
redirect URIs antiguas, porque Authentik sólo reaplica un blueprint cuando
cambia el fichero, no sus variables. `./install.sh` lo reaplica solo; a mano:
`docker exec authentik-worker ak apply_blueprint custom/headscale.yaml`.

**"Permission denied" de Authentik al iniciar sesión**: el usuario no está en
`headscale-users` ni en `vpn-admins`.

**"Invalid grant_type for provider" en los logs de Authentik**: el proveedor
se creó sin `grant_types`. El blueprint actual lo define; reinicia el worker
para que lo reaplique: `docker compose restart authentik-worker`.

### Base de datos corrupta

**Síntoma**: Headscale no inicia, logs muestran error de SQLite

**Solución**:
```bash
# 1. Detener servicios
docker compose down

# 2. Hacer backup de la BD actual
docker run --rm \
  -v headscale-data:/data \
  -v $(pwd):/backup \
  alpine cp /data/db.sqlite /backup/db.sqlite.backup

# 3. Intentar reparar
docker run --rm \
  -v headscale-data:/data \
  alpine sh -c "cd /data && sqlite3 db.sqlite 'PRAGMA integrity_check;'"

# 4. Si falla, restaurar desde backup
# (o empezar de cero, perdiendo datos)

# 5. Reiniciar
docker compose up -d
```

### Los clientes no pueden comunicarse entre sí

**Síntoma**: Los clientes se conectan a Headscale pero no pueden hacer ping entre ellos

**Verificación**:
```bash
# 1. Verificar que los nodos están registrados
docker exec headscale headscale nodes list

# 2. Verificar rutas
docker exec headscale headscale routes list

# 3. Verificar ACLs (si están configuradas)
```

**Solución**:
```bash
# 1. Verificar que no hay ACLs bloqueando
# Editar headscale-config.yaml si es necesario

# 2. Verificar que el servidor DERP está funcionando
# Ver logs de headscale
docker compose logs -f headscale | grep DERP

# 3. Verificar que el puerto UDP 3478 está accesible (ver arriba)
```

---

## 🗑️ Desinstalación

### Detener servicios sin eliminar datos

```bash
docker compose down
```

### Desinstalación completa

```bash
# Ejecutar script de desinstalación
./uninstall.sh

# Responder 'yes' a la confirmación
```

### Desinstalación con purga total de datos

```bash
# Elimina también volúmenes, configuraciones y datos
./uninstall.sh --purge

# ⚠️ ADVERTENCIA: Esto es IRREVERSIBLE
```

---

## 🤝 Contribuir

Las contribuciones son bienvenidas. Para contribuir:

1. Fork el proyecto
2. Crea una rama para tu feature (`git checkout -b feature/AmazingFeature`)
3. Commit tus cambios (`git commit -m 'Add some AmazingFeature'`)
4. Push a la rama (`git push origin feature/AmazingFeature`)
5. Abre un Pull Request

### Roadmap

- [ ] Soporte para PostgreSQL como base de datos
- [ ] Script de migración desde instalaciones manuales
- [ ] Dashboard de monitoreo (Prometheus + Grafana)
- [ ] Soporte para alta disponibilidad (HA)
- [x] Login con usuario/contraseña y Google (Authentik integrado)
- [ ] Script de actualización automática de versiones

---

## 📄 Licencia

Este proyecto está bajo la Licencia MIT. Ver el archivo [LICENSE](LICENSE) para más detalles.

---

## 🙏 Agradecimientos

- [Headscale](https://github.com/juanfont/headscale) - Control plane open-source
- [Headplane](https://github.com/tale/headplane) - UI web que usaba este proyecto antes de Mi VPN
- [Tailscale](https://tailscale.com/) - Por crear el protocolo WireGuard mesh
- [Caddy](https://caddyserver.com/) - Servidor web con HTTPS automático

---

## 📞 Soporte

Si tienes problemas:

1. Revisa la sección [Troubleshooting](#-troubleshooting)
2. Busca en [Issues](https://github.com/tu-usuario/tailscale-selfhosted/issues) existentes
3. Abre un [nuevo Issue](https://github.com/tu-usuario/tailscale-selfhosted/issues/new) con:
   - Descripción del problema
   - Output de `docker compose logs`
   - Output de `docker compose ps`
   - Tu archivo `.env` (sin secretos)

---

<div align="center">

**[⬆ Volver arriba](#headscale--mi-vpn---despliegue-todo-en-uno)**

Hecho con ❤️ para la comunidad open-source

</div>


#### TEST Container
```bash
docker run -it --rm \
  --name=tailscaled \
  -v /dev/net/tun:/dev/net/tun \
  --network=host \
  --cap-add=NET_ADMIN \
  --cap-add=NET_RAW \
  --entrypoint /bin/sh \
  tailscale/tailscale \
  -c 'tailscaled > /dev/null 2>&1 & exec /bin/sh'
```