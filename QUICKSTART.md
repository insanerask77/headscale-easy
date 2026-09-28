# Quickstart - Headscale + Mi VPN

Guía de inicio rápido para tener Headscale + el panel Mi VPN funcionando en menos de 5 minutos.

## 🚀 Instalación en 3 Pasos

### 1. Ejecutar el Instalador

```bash
./install.sh
```

### 2. Responder las Preguntas

Hay **un solo modo de despliegue**: Caddy delante, enrutando un único dominio
(`/` → Headscale, `/mi-vpn` → panel). Lo único que se decide es **quién
pone el HTTPS**:

| Respuesta | Elígela si... |
|-----------|---------------|
| **Caddy, con Let's Encrypt** | La máquina es alcanzable desde internet, el DNS ya apunta aquí y tienes los puertos 80/443 abiertos. **Empieza por aquí.** |
| **Caddy, con autofirmado** | Quieres HTTPS sin dependencias externas. Hay que instalar la CA de Caddy en cada cliente Tailscale o no conectarán. |
| **Un proxy por delante** | Ya tienes Nginx Proxy Manager, nginx, Traefik u otro Caddy terminando el TLS. El instalador te da el snippet listo. |
| **Nadie: sólo HTTP** | `http://localhost`, una LAN de confianza o un acceso que ya va por otra VPN. |

El resto de preguntas:

- **Dominio o IP**: `vpn.midominio.com`, `192.168.1.100` o `localhost`
- **Email** (sólo con Let's Encrypt): para los avisos de renovación
- **Qué proxy tienes delante** y **la IP de esta máquina vista por él** (sólo
  si elegiste "un proxy por delante")
- **Puertos**: Enter para los valores por defecto
- **Nombre del Tailnet**: `myorg` (o el nombre de tu empresa)
- **¿Cómo inician sesión los usuarios?**: `1` (sólo API key) para empezar; `2` (Authentik) si quieres usuario/contraseña o login con Google

### 3. ¡Listo!

Al finalizar verás las URLs y, si no usas OIDC, la API key para entrar en el panel:

```
✓ Headscale + Mi VPN están corriendo
HTTPS: letsencrypt

Panel (Mi VPN): https://vpn.midominio.com/mi-vpn/
Control plane (Headscale): https://vpn.midominio.com
```

Si elegiste "un proxy por delante", esas URLs **todavía no responden**: falta
configurar la otra máquina. El instalador deja el snippet en `reverse-proxy/`:

```bash
ls reverse-proxy/
# NGINX-PROXY-MANAGER.md          guía de los campos del Proxy Host
# nginx-vpn.midominio.com.conf    un server{} completo para nginx
# traefik-vpn.midominio.com.yml   config dinámica para el file provider
# Caddyfile                       el bloque del Caddy de borde
```

Se genera **sólo el que corresponda** al proxy que elegiste. Cópialo a la
máquina del proxy y aplícalo allí.

---

## 📝 Primeros Pasos

### 1. Entrar en el panel

```
https://vpn.midominio.com/mi-vpn/        # con HTTPS (Caddy o un proxy delante)
http://localhost/mi-vpn/                 # sin HTTPS
```

> Con Authentik, entra con `akadmin` y la contraseña que muestra el
> instalador. Sin OIDC, pega la **API key** que muestra al terminar: es la
> única credencial, y sólo para administradores. Para tener cuentas con
> usuario/contraseña, elige Authentik en el instalador (ver README, "El panel
> Mi VPN" y "Login de usuarios con Authentik").

### 2. Generar Clave de Conexión

```bash
./scripts/utils.sh preauth:create admin
```

O directamente, teniendo en cuenta que desde Headscale 0.29 `--user` espera el
**ID numérico** del usuario, no su nombre:

```bash
docker exec headscale headscale users list     # busca el ID de 'admin'
docker exec headscale headscale preauthkeys create \
  --user 1 \
  --reusable \
  --expiration 24h
```

Copia la clave que se muestra (empieza por `hskey-auth-`).

### 3. Conectar un Dispositivo

En tu ordenador/móvil con [Tailscale instalado](https://tailscale.com/download):

```bash
# Con HTTPS: el control plane vive en la raíz del dominio, sin puerto
tailscale up --login-server=https://vpn.midominio.com --authkey=<tu-clave>

# Sin HTTPS: igual, pero por HTTP (también sin puerto, lo sirve Caddy)
tailscale up --login-server=http://vpn.midominio.com --authkey=<tu-clave>
```

> La URL exacta la muestra el instalador al terminar, y está en `.env` como
> `HEADSCALE_PUBLIC_URL`. **No es la misma** que la de la interfaz web.

---

## 🔧 Comandos Útiles

### Ver Estado

```bash
docker compose ps
```

### Ver Logs

```bash
# Todos los servicios
docker compose logs -f

# Solo Headscale
docker compose logs -f headscale
```

### Listar Dispositivos Conectados

```bash
docker exec headscale headscale nodes list
```

### Reiniciar Servicios

```bash
docker compose restart
```

### Detener Todo

```bash
docker compose down
```

### Iniciar de Nuevo

```bash
docker compose up -d
```

---

## 🛠️ Script de Utilidades

Incluye un script con comandos útiles:

```bash
./scripts/utils.sh help

# Ejemplos:
./scripts/utils.sh status
./scripts/utils.sh users:create admin
./scripts/utils.sh preauth:create admin
./scripts/utils.sh backup
```

---

## ❓ Problemas Comunes

### "Connection refused" al acceder a la UI

**Solución**: Espera 30 segundos para que los servicios terminen de iniciar:

```bash
docker compose ps  # Verifica que estén "healthy"
```

### Con un proxy por delante: el dominio no responde

**Comprueba primero si el fallo está en el proxy o en el backend.** Desde la
máquina del proxy, hablando directamente con Caddy:

```bash
curl -s http://<BACKEND_HOST>:<HTTP_PORT>/key?v=142         # Headscale
curl -sI http://<BACKEND_HOST>:<HTTP_PORT>/mi-vpn/ | head -1  # panel
```

- **Fallan los dos**: el problema está en esta máquina. Revisa el firewall y
  que Caddy haya arrancado: `docker compose logs caddy`.
- **Funcionan pero el dominio público no**: el problema está en el proxy.
  Revisa el snippet de `reverse-proxy/`; lo más habitual es que falte
  *Websockets Support* o el `proxy_read_timeout` largo.

### Con proxy externo: los nodos se reconectan cada minuto

**Causa**: `/machine/map` es un long-poll permanente y el proxy lo corta con su
`proxy_read_timeout` por defecto (60 s).

**Solución**: en el bloque *Advanced* del proxy, `proxy_read_timeout 3600s;` y
`proxy_buffering off;` — ya vienen en los ficheros de `reverse-proxy/`.

### Let's Encrypt falla

**Causa**: DNS no apunta correctamente o puertos 80/443 bloqueados.

**Solución rápida**: Usa certificado autofirmado temporalmente:

```bash
./install.sh  # Re-ejecutar y elegir "autofirmado"
```

### `tailscale up` falla con `x509: certificate signed by unknown authority`

**Causa**: estás usando certificado autofirmado y el cliente Tailscale no
confía en la CA interna de Caddy. A diferencia del navegador, aquí no se puede
"aceptar el riesgo": no conecta.

**Solución**: instala `./caddy-root-ca.crt` (lo exporta el instalador) en el
dispositivo:

```bash
sudo cp caddy-root-ca.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates
```

En móviles no es posible: usa Let's Encrypt. Ver
[README.md](README.md#certificado-autofirmado-hay-que-instalar-la-ca-en-cada-cliente).

### Error 500 `auth ID has invalid length: expected 38, got 101`

**Causa**: has pegado la **API key** (`hskey-api-…`, 87 caracteres) donde
va un **Auth ID** de 38 caracteres (`hskey-authreq-…`), por ejemplo en
Dispositivos → *Registrar con Auth ID* del panel o en `headscale auth register`.

**Solución**: lanza `tailscale up` **sin** `--authkey` en el dispositivo:

```bash
tailscale up --login-server=https://vpn.midominio.com
# -> To authenticate, visit: .../register/hskey-authreq-XXXXXXXXXXXXXXXXXXXXXXXX
```

Pega en el panel sólo el `hskey-authreq-…` (o la URL completa). O sáltate ese paso usando
una pre-auth key con `--authkey` (paso 2 de arriba).

### Los dispositivos no se conectan entre sí

**Solución**: Verifica que el puerto UDP 3478 esté abierto:

```bash
sudo ufw allow 3478/udp
```

---

## 📚 Más Información

- Documentación completa: Ver [README.md](README.md)
- Troubleshooting detallado: [README.md#troubleshooting](README.md#troubleshooting)
- Reconfigurar: Re-ejecuta `./install.sh`
- Backup: `./scripts/utils.sh backup`

---

## 🗑️ Desinstalar

```bash
./uninstall.sh         # Mantiene datos
./uninstall.sh --purge # Elimina todo
```

---

**¡Eso es todo! Tu red privada VPN está lista para usar.**
