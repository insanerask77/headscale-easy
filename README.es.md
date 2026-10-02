<p align="center">
  <img src="web/static/favicon.svg" width="72" alt="Logo de Headscale Easy">
</p>

<h1 align="center">Headscale Easy</h1>

<p align="center">
  <b>Headscale, con todo lo que lo rodea.</b><br>
  Una capa de despliegue y gestión para <a href="https://github.com/juanfont/headscale">Headscale</a>, el servidor de control open source de Tailscale:
  instalador, HTTPS, cuentas e inicio de sesión, una consola web estilo Tailscale y copias de seguridad. Se instala con un solo comando.
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
Headscale Easy usa la imagen **oficial y sin modificar** de Headscale: no es un
fork ni un sustituto. Lo que lleva tiempo es el "pegamento" alrededor cuando
quieres una instalación completa y autoalojada para varias personas:

> auth + DNS + HTTPS + gestión de dispositivos + copias = un proyecto de fin de semana

Headscale Easy empaqueta ese pegamento, en el espíritu de
[wg-easy](https://github.com/wg-easy/wg-easy) para WireGuard:

- **Despliegue automatizado**: un instalador interactivo escribe y conecta
  todas las piezas; vuelve a ejecutarlo para cambiar ajustes.
- **Una consola web** para el día a día, inspirada en el panel de Tailscale,
  donde cada miembro gestiona sólo sus dispositivos.
- **Configuración centralizada**: un `.env`, un dominio.
- **Auth, DNS, HTTPS y copias** configurados con valores seguros por defecto.
- **Todo autoalojado**: en los dispositivos usas las apps oficiales de
  Tailscale; sólo el servidor es tuyo.

Si ya estás a gusto usando Headscale a mano, no necesitas este proyecto.
Más en [¿Por qué Headscale Easy?](https://insanerask77.github.io/headscale-easy/es/why/), con el flujo completo de principio a fin.

### Headscale Easy y Headplane

[Headplane](https://github.com/tale/headplane) es una interfaz web consolidada
y completa para un Headscale **que ya tienes**: una buena elección si ya usas
Headscale. Headscale Easy **instala y conecta todo el stack** (Headscale,
HTTPS, Authentik opcional con 2FA e invitaciones, copias) e incluye su propia
consola. Mira la [comparativa detallada](https://insanerask77.github.io/headscale-easy/es/why/#headscale-easy-and-headplane).

## ✨ Funcionalidades

- 🖥️ **Consola web estilo Tailscale** en `/admin`: máquinas, usuarios, DNS,
  control de acceso y claves. Tema oscuro y claro, funciona en el móvil.
- 👤 **Cuentas de usuario reales** con contraseña ([Authentik](https://goauthentik.io)
  integrado) y **login con Google** opcional, o tu propio proveedor OIDC
  (Keycloak, Authelia, Google…).
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
  (Nginx Proxy Manager, nginx, Traefik, Caddy: se genera la configuración) o
  HTTP en una LAN.
- 🌍 **Inglés, español, francés, alemán y portugués** en la consola (instalador: inglés y español; ¡se aceptan más idiomas!).
- 💾 **Copias diarias** de todo (base de datos, claves, cuentas, configuración) y
  restauración con un solo comando, también en un servidor nuevo.
- 🪶 **Ligero**: la consola es Python de la librería estándar, sin compilación,
  sin framework JavaScript y sin base de datos propia.

## 🚀 Inicio rápido

Necesitas un Linux con Docker (el instalador puede instalarlo) y, para HTTPS
real, un dominio que apunte a él.

1. **Clona** el repositorio.
2. **Ejecuta el instalador**: `./install.sh`.
3. **Configura el dominio** y quién pone el HTTPS.
4. **Configura el inicio de sesión**: Authentik integrado, tu proveedor OIDC o ninguno.
5. **Entra** en `https://tu-dominio/admin`.
6. **Conecta tu primer dispositivo** con la app oficial de Tailscale.

```bash
git clone https://github.com/insanerask77/headscale-easy.git
cd headscale-easy
./install.sh
```

El instalador pregunta unas pocas cosas (idioma, dominio, quién pone el HTTPS,
cómo inician sesión los usuarios), escribe la configuración, arranca todo y te
muestra las URLs y las primeras credenciales. Vuelve a ejecutarlo cuando quieras
cambiar algo: los datos se conservan.

Después abre `https://tu-dominio/admin` y conecta dispositivos con la app
oficial de Tailscale:

```bash
tailscale up --login-server=https://tu-dominio
```

En móviles y apps de escritorio elige **"Use an alternate server"** /
**"Change server"** e introduce la misma URL. La página **Añadir dispositivo** de
la consola muestra los pasos para cada sistema.

**Antes de producción**, repasa la [guía de producción y bastionado](https://insanerask77.github.io/headscale-easy/es/hardening/):
cortafuegos, HTTPS, inicio de sesión y doble factor, restringir la consola, el
socket de Docker, secretos, copias fuera del servidor y actualizaciones.

### Puertos

| Puerto | Protocolo | Uso |
|--------|-----------|-----|
| 80 / 443 | TCP | Consola web, plano de control, Let's Encrypt |
| 3478 | UDP | STUN del relay DERP integrado (debe ser accesible) |

## 🧪 Prueba la imagen todo en uno (preview)

Headscale 2.0 llega como un solo contenedor: Headscale + Caddy + la consola,
configurado desde el navegador, sin socket de Docker ni instalador. Es una
preview; el instalador de arriba sigue siendo la forma soportada de usarlo.

```bash
docker run -d --name headscale-easy -p 80:80 -p 443:443 -p 3478:3478/udp \
  -v hse:/data ghcr.io/insanerask77/headscale-easy-aio
docker logs headscale-easy      # el token de configuración de un solo uso
```

Luego abre `http://<tu-servidor>/admin/setup`. Detalles, variables para el
arranque sin asistente y límites: [imagen todo en uno](https://insanerask77.github.io/headscale-easy/es/all-in-one/).

## 🧩 Cómo funciona

Los clientes de Tailscale sólo hablan con Headscale (imagen oficial). Caddy
sirve todo en un dominio: `/` para Headscale, `/admin` para la consola (que usa
la API REST de Headscale) y `/authentik` para Authentik (opcional, proveedor
OIDC de ambos). La consola no está en el camino del tráfico: si se para, los
dispositivos siguen funcionando. Detalles en
[Arquitectura y recursos](https://insanerask77.github.io/headscale-easy/es/architecture/).

### Consumo de recursos

Medido en reposo en una instalación pequeña:

| Configuración | Contenedores | RAM | Imágenes en disco |
|---|---:|---:|---:|
| Sólo Headscale, como referencia | 1 | ~20 MB | 113 MB |
| Headscale Easy sin Authentik | 3 | ~65 MB (+45 MB) | ~270 MB |
| Headscale Easy con Authentik | 6 | ~1,1 GB | ~2,6 GB |

Authentik (cuentas, doble factor, login con Google, invitaciones) es la parte
pesada y opcional; usa tu propio proveedor OIDC para prescindir de él.

## 📚 Documentación

**📖 [insanerask77.github.io/headscale-easy/es](https://insanerask77.github.io/headscale-easy/es/)**, en español e inglés.

- [Primeros pasos](https://insanerask77.github.io/headscale-easy/es/getting-started/): requisitos, instalación, primer dispositivo.
- [Configuración](https://insanerask77.github.io/headscale-easy/es/configuration/): opciones del instalador, modos de
  HTTPS, proxies por delante, proveedores de login, DNS, referencia de `.env`.
- [Operación](https://insanerask77.github.io/headscale-easy/es/operations/): usuarios y admins, máquinas, actualizaciones,
  copias de seguridad, resolución de problemas.
- [Producción y bastionado](https://insanerask77.github.io/headscale-easy/es/hardening/): lista de comprobación, cortafuegos,
  HTTPS, inicio de sesión, restringir la consola, socket de Docker, secretos,
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
