<p align="center">
  <img src="web/static/favicon.svg" width="72" alt="Logo de Headscale Easy">
</p>

<h1 align="center">Headscale Easy</h1>

<p align="center">
  <b>La alternativa open source a Tailscale.</b><br>
  Tu propio servidor <a href="https://github.com/juanfont/headscale">Headscale</a> con una consola web integrada que se ve y se usa como la de Tailscale:
  cuentas de usuario, login con Google y HTTPS incluidos. Se instala con un solo comando.
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

<p align="center">
  <img src="docs/images/machines-dark.png" alt="Headscale Easy — Máquinas" width="900">
</p>

---

Tailscale es genial, pero su servidor de coordinación es cerrado y lo alojan
ellos. [Headscale](https://github.com/juanfont/headscale) es la implementación
open source de ese servidor, pero sólo tiene línea de comandos, y montar a mano
HTTPS, usuarios y single sign-on lleva una tarde.

**Headscale Easy** es a Headscale lo que [wg-easy](https://github.com/wg-easy/wg-easy)
es a WireGuard: Headscale y todo lo que lo rodea, instalado y configurado por un
script interactivo y gestionado desde una consola web inspirada en el panel de
Tailscale. En los dispositivos usas las apps oficiales de Tailscale; sólo el
servidor es tuyo.

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
- 🌍 **Inglés y español** en el instalador y la consola (¡se aceptan más idiomas!).
- 💾 **Copias diarias** de todo (base de datos, claves, cuentas, configuración) y
  restauración con un solo comando, también en un servidor nuevo.
- 🪶 **Ligero**: la consola es Python de la librería estándar, sin compilación,
  sin framework JavaScript y sin base de datos propia.

## 🚀 Inicio rápido

Necesitas un Linux con Docker (el instalador puede instalarlo) y, para HTTPS
real, un dominio que apunte a él.

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

### Puertos

| Puerto | Protocolo | Uso |
|--------|-----------|-----|
| 80 / 443 | TCP | Consola web, plano de control, Let's Encrypt |
| 3478 | UDP | STUN del relay DERP integrado (debe ser accesible) |

## 📚 Documentación

**📖 [insanerask77.github.io/headscale-easy/es](https://insanerask77.github.io/headscale-easy/es/)**, en español e inglés.

- [Primeros pasos](https://insanerask77.github.io/headscale-easy/es/getting-started/): requisitos, instalación, primer dispositivo.
- [Configuración](https://insanerask77.github.io/headscale-easy/es/configuration/): opciones del instalador, modos de
  HTTPS, proxies por delante, proveedores de login, DNS, referencia de `.env`.
- [Operación](https://insanerask77.github.io/headscale-easy/es/operations/): usuarios y admins, máquinas, actualizaciones,
  copias de seguridad, resolución de problemas.
- [Hoja de ruta](ROADMAP.md) · [Contribuir](CONTRIBUTING.md) · [Seguridad](SECURITY.md) · [Cambios](CHANGELOG.md)

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
