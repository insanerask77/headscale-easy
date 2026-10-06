<p align="center">
  <img src="web/static/favicon.svg" width="72" alt="Logo de Headscale Easy">
</p>

<h1 align="center">Headscale Easy</h1>

<p align="center">
  <b>Headscale, con todo lo que lo rodea.</b><br>
  Una capa de despliegue y gestión para <a href="https://github.com/juanfont/headscale">Headscale</a>, el servidor de control open source de Tailscale:
  HTTPS, cuentas e inicio de sesión, una consola web estilo Tailscale y copias de seguridad. Se instala con un solo archivo de Docker Compose.
</p>

<p align="center">
  <a href="https://github.com/insanerask77/headscale-easy/actions/workflows/ci.yml"><img src="https://github.com/insanerask77/headscale-easy/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/insanerask77/headscale-easy/releases"><img src="https://img.shields.io/github/v/release/insanerask77/headscale-easy?sort=semver" alt="Versión"></a>
  <a href="https://insanerask77.github.io/headscale-easy/es/"><img src="https://img.shields.io/badge/docs-github.io-3f6fe0" alt="Documentación"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/licencia-MIT-green" alt="Licencia MIT"></a>
  <a href="https://ko-fi.com/rafaelmadolell"><img src="https://img.shields.io/badge/Ko--fi-apóyame-FF5E5B?logo=kofi&logoColor=white" alt="Apóyame en Ko-fi"></a>
</p>

<p align="center">
  <a href="README.md">English</a> · <b>Español</b>
</p>

> [!WARNING]
> **Aviso de seguridad.** Es software de red sensible en seguridad y un
> proyecto joven (septiembre de 2026) con un solo mantenedor. Se ha usado
> desarrollo asistido por IA de forma extensa y **no ha pasado ninguna
> auditoría de seguridad independiente**. Revisa y bastiona tu despliegue antes
> de exponerlo a Internet: consulta [SECURITY.md](SECURITY.md), la
> [guía de bastionado](https://insanerask77.github.io/headscale-easy/es/hardening/) y el [uso de IA](AI_USAGE.md).

<p align="center">
  <img src="docs/images/machines-dark.png" alt="Headscale Easy — Máquinas" width="900">
</p>

---

## 🤔 ¿Por qué Headscale Easy?

[Headscale](https://github.com/juanfont/headscale) funciona bien por sí solo, y
Headscale Easy usa el binario **oficial y sin modificar** de Headscale: no es un
fork ni un sustituto. Lo que lleva tiempo es el "pegamento" alrededor cuando
quieres una instalación completa y autoalojada para varias personas:

> auth + DNS + HTTPS + gestión de dispositivos + copias = un proyecto de fin de semana

Headscale Easy empaqueta ese pegamento, en el espíritu de
[wg-easy](https://github.com/wg-easy/wg-easy) para WireGuard:

- **Un solo contenedor**: Headscale, HTTPS y la consola en una sola imagen,
  configurada desde el navegador; descarga la imagen nueva para actualizar.
- **Una consola web** para el día a día, inspirada en el panel de Tailscale,
  donde cada miembro gestiona sólo sus dispositivos.
- **Configuración centralizada**: unas pocas variables o el asistente, un dominio.
- **Auth, DNS, HTTPS y copias** configurados con valores seguros por defecto.
- **Todo autoalojado**: en los dispositivos usas las apps oficiales de
  Tailscale; sólo el servidor es tuyo.

Si ya estás a gusto usando Headscale a mano, no necesitas este proyecto.
Más en [¿Por qué Headscale Easy?](https://insanerask77.github.io/headscale-easy/es/why/), con el flujo completo de principio a fin.

### Headscale Easy y Headplane

[Headplane](https://github.com/tale/headplane) es una interfaz web consolidada
y completa para un Headscale **que ya tienes**: una buena elección si ya usas
Headscale. Headscale Easy **instala y conecta todo el stack** (Headscale,
HTTPS, cuentas locales con 2FA e invitaciones, copias) e incluye su propia
consola. Mira la [comparativa detallada](https://insanerask77.github.io/headscale-easy/es/why/#headscale-easy-and-headplane).

## ✨ Funcionalidades

- 🖥️ **Consola web estilo Tailscale** en `/admin`: máquinas, usuarios, DNS,
  control de acceso y claves. Tema oscuro y claro, funciona en el móvil.
- 👤 **Cuentas de usuario reales** integradas: contraseña, **doble factor (TOTP)**
  opcional, invitaciones, enlaces de restablecimiento y registro; o tu propio
  proveedor OIDC (Authentik, Keycloak, Pocket ID, Google…).
- 🔒 **Cada usuario tiene su propia VPN privada**: los miembros sólo ven y
  alcanzan sus dispositivos (ACL `autogroup:self`); los admins lo gestionan todo.
- 📱 **Máquinas**: estado, direcciones, sistema y versión del cliente con aviso de
  actualización, renombrar, expirar, eliminar, caducidad de clave, etiquetas,
  **rutas de subred y exit nodes**, filtros, búsqueda y exportación CSV.
- 🔑 **Claves de autenticación** (de un uso, reutilizables, efímeras) y API keys;
  registro de dispositivos por Auth ID.
- 🌐 **DNS**: MagicDNS, dominio de la tailnet, nameservers, split DNS, dominios de
  búsqueda; validado con `headscale configtest` y revertido si Headscale lo rechaza.
- 📝 **Control de acceso**: editor visual de reglas, grupos y dueños de
  etiquetas, un simulador de acceso ("¿puede ana llegar a nas:445?"), y el
  editor de HuJSON en crudo como vía de escape completa.
- 🔐 **HTTPS a tu manera**: Let's Encrypt, autofirmado, detrás de tu proxy
  (Nginx Proxy Manager, nginx, Traefik, Caddy: con ejemplos listos) o
  HTTP en una LAN.
- 🌍 **Inglés, español, francés, alemán y portugués** en la consola (¡se aceptan más idiomas!).
- 💾 **Copias diarias** de todo (base de datos, claves, cuentas, configuración) y
  restauración con un solo comando, también en un servidor nuevo.
- 🪶 **Ligero**: un contenedor, unos 70 MB de RAM; la consola es Python de la
  librería estándar, sin compilación y sin framework JavaScript.

## 📸 Capturas

| Máquinas (tema claro) | Detalle de una máquina |
|---|---|
| ![Máquinas, tema claro](docs/images/machines-light.png) | ![Detalle de una máquina](docs/images/machine-detail.png) |
| **Usuarios** | **DNS** |
| ![Usuarios](docs/images/users.png) | ![DNS](docs/images/dns.png) |
| **Controles de acceso** | **Claves** |
| ![Controles de acceso](docs/images/access-controls.png) | ![Claves](docs/images/keys.png) |
| **Inicio de sesión** | **Añadir dispositivo** |
| ![Inicio de sesión](docs/images/sign-in.png) | ![Añadir dispositivo](docs/images/add-device.png) |

## 🚀 Inicio rápido

Necesitas un Linux con Docker y el plugin Compose (v2.24 o más reciente) y, para
HTTPS real, un dominio que apunte a él.

```bash
mkdir headscale-easy && cd headscale-easy
curl -fsSLO https://raw.githubusercontent.com/insanerask77/headscale-easy/main/compose.yaml
docker compose up -d
```

Esa es toda la instalación: un contenedor, con la versión 2.0 fijada en `compose.yaml`.
Después:

1. **Abre el asistente** en `http://<tu-servidor>/admin/setup` y escribe el token de
   un solo uso (`docker compose logs`). Crea el administrador, pon nombre a la
   tailnet, indica la dirección pública y el HTTPS (Let's Encrypt necesita un
   dominio y los puertos 80/443 abiertos) y elige el relay (DERP), el modo de
   registro y las copias.
2. **Entra** en `https://tu-dominio/admin`.
3. **Conecta tu primer dispositivo** con la app oficial de Tailscale.

¿Prefieres responder de antemano? Copia [`.env.example`](.env.example) a `.env`
(dirección pública, HTTPS, administrador) antes de `docker compose up -d`: todas las
líneas son opcionales. Para actualizar, cambia `HSE_VERSION` (o la etiqueta en
`compose.yaml`) y ejecuta `docker compose pull && docker compose up -d`; tus ajustes y
datos viven en volúmenes y nunca se tocan.

```bash
tailscale up --login-server=https://tu-dominio
```

En móviles y apps de escritorio elige **"Use an alternate server"** /
**"Change server"** e introduce la misma URL. La página **Añadir dispositivo** de
la consola muestra los pasos para cada sistema.

**Antes de producción**, repasa la [guía de producción y bastionado](https://insanerask77.github.io/headscale-easy/es/hardening/):
cortafuegos, HTTPS, inicio de sesión y doble factor, restringir la consola,
secretos, copias fuera del servidor y actualizaciones.

### Puertos

| Puerto | Protocolo | Uso |
|--------|-----------|-----|
| 80 / 443 | TCP | Consola web, plano de control, Let's Encrypt |
| 3478 | UDP | STUN del relay DERP integrado (debe ser accesible) |

## ⚙️ Configuraciones avanzadas

La instalación simple de arriba basta para la mayoría. Cuando necesites más, cada
opción es un complemento pequeño de la misma app Compose: un PostgreSQL externo, tu
propio proveedor de identidad (Authentik, Pocket ID, Keycloak, Google), un proxy
delante, copias remotas. Mira [Configuraciones avanzadas](https://insanerask77.github.io/headscale-easy/es/advanced/)
y [`advanced/`](advanced/).

Sin Compose, la misma imagen corre con `docker run -d --name headscale-easy -p 80:80
-p 443:443 -p 3478:3478/udp -v hse:/data ghcr.io/insanerask77/headscale-easy:2.0.0`; la
página de la [imagen todo en uno](https://insanerask77.github.io/headscale-easy/es/all-in-one/)
lista las variables para un arranque sin asistente.

## 🧩 Cómo funciona

```
               ┌──────────────── headscale-easy (un contenedor) ────────────────┐
:80/:443 ────▶ │ caddy ──┬─ /       ──▶ headscale (binario oficial, proceso hijo) │
:3478/udp ───▶ │         └─ /admin  ──▶ consola (Python)                         │
               │ supervisor: arranca y reinicia los tres, configtest, copias     │
               │ /data: headscale/ caddy/ console/ config/ backups/              │
               └─────────────────────────────────────────────────────────────────┘
       opcional, fuera: proveedor OIDC · PostgreSQL · proxy delante · copia remota
```

Los clientes de Tailscale sólo hablan con Headscale: la consola no está en el
camino del tráfico y, si se para, los dispositivos siguen funcionando. **No hay
socket de Docker**: un pequeño supervisor dentro del contenedor valida y reinicia
Headscale cuando la consola se lo pide. Todo vive en un dominio y el contenedor
corre como usuario sin privilegios y sin capabilities añadidas. Detalles en
[Arquitectura y recursos](https://insanerask77.github.io/headscale-easy/es/architecture/).

### Consumo de recursos

Medido por CI con una imagen recién construida (detalles y método en
[Arquitectura y recursos](https://insanerask77.github.io/headscale-easy/es/architecture/)):

| | Medido | Límite de CI |
|---|---:|---:|
| Contenedores | **1** | — |
| Tamaño de la imagen | **232 MB** | 250 MB |
| RAM en reposo | **71 MB** | 100 MB |
| RAM mientras corre una copia | **71 MB** | 100 MB |

Headscale solo consume unos 20 MB en reposo: Caddy, la consola, el supervisor y las
copias añaden unos 50 MB. Un proveedor de identidad es opcional y corre fuera: usa
el que ya tengas.

## 📚 Documentación

**📖 [insanerask77.github.io/headscale-easy/es](https://insanerask77.github.io/headscale-easy/es/)**, en español e inglés.

- [Primeros pasos](https://insanerask77.github.io/headscale-easy/es/getting-started/): requisitos, instalación, primer dispositivo.
- [Configuración](https://insanerask77.github.io/headscale-easy/es/configuration/): modos de HTTPS, inicio de sesión
  y roles, DNS, base de datos y la referencia de variables de entorno.
- [Operación](https://insanerask77.github.io/headscale-easy/es/operations/): usuarios y admins, máquinas, actualizaciones,
  copias de seguridad, resolución de problemas.
- [Producción y bastionado](https://insanerask77.github.io/headscale-easy/es/hardening/): lista de comprobación, cortafuegos,
  HTTPS, inicio de sesión, restringir la consola, secretos,
  copias, actualizaciones, logs.
- [¿Por qué Headscale Easy?](https://insanerask77.github.io/headscale-easy/es/why/): el problema que resuelve, comparativa
  con Headplane, flujo completo.
- [Arquitectura y recursos](https://insanerask77.github.io/headscale-easy/es/architecture/): qué hace cada componente y
  cuánto consume.
- [Hoja de ruta](ROADMAP.md) · [Contribuir](CONTRIBUTING.md) · [Seguridad](SECURITY.md) · [Uso de IA](AI_USAGE.md) · [Cambios](CHANGELOG.md)

Las páginas nuevas (producción, comparativa, arquitectura, uso de IA) están de
momento sólo en inglés.

## 🤖 Uso de IA

Headscale Easy se ha construido con ayuda extensa de
[Claude Code](https://claude.com/claude-code): generó o modificó una parte
importante del código y ayudó con depuración, refactorización, traducciones y
documentación. El mantenedor diseñó el proyecto, revisó, probó e integró cada
cambio, y es responsable del código y de las decisiones finales. Los commits en
los que participó llevan la línea `Co-Authored-By: Claude`. Se recomienda
encarecidamente una revisión independiente: consulta [AI_USAGE.md](AI_USAGE.md).

## 🙌 Apoya el proyecto

Headscale Easy lo crea y mantiene en su tiempo libre
**[Rafa Madolell](https://github.com/insanerask77)**. Si te ahorra tiempo o dinero:

- ⭐ **Dale una estrella al repo**: ayuda muchísimo a que otros lo encuentren.
- ☕ **[Invítame a un café en Ko-fi](https://ko-fi.com/rafaelmadolell)**.
- 🐛 Reporta errores, propone mejoras o envía un pull request.
- 🌍 Traduce la consola a tu idioma (ver [CONTRIBUTING](CONTRIBUTING.md#translations)).

## 👥 Contribuidores

Gracias a quienes han enviado un arreglo o una mejora:

- [@vhryniv](https://github.com/vhryniv): user agent propio en las peticiones
  OIDC salientes, para que el SSO funcione detrás de proxies que bloquean el
  de Python por defecto
  ([#21](https://github.com/insanerask77/headscale-easy/pull/21)).

## ⚖️ Licencia

[MIT](LICENSE) © 2026 [Rafa Madolell](https://github.com/insanerask77).

Headscale Easy es un proyecto independiente, sin relación con Tailscale Inc. ni
con el proyecto Headscale. "Tailscale" es una marca de Tailscale Inc.
