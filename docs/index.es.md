---
hide:
  - navigation
  - toc
---

<div class="hse-hero" markdown>

![Headscale Easy](assets/logo.svg){ width="72" }

# Headscale Easy

**La alternativa open source a Tailscale.**<br>
Tu propio servidor Headscale con una consola web integrada que se ve y se usa
como la de Tailscale: cuentas de usuario, login con Google y HTTPS incluidos.
Se instala con un solo comando.

[Empezar](getting-started.md){ .md-button .md-button--primary }
[GitHub](https://github.com/insanerask77/headscale-easy){ .md-button }
[:simple-kofi: Invítame a un café en Ko-fi](https://ko-fi.com/rafaelmadolell){ .md-button }

</div>

![Headscale Easy — Máquinas](images/machines-dark.png){ .hse-shot }

Tailscale es genial, pero su servidor de coordinación es cerrado y lo alojan
ellos. [Headscale](https://github.com/juanfont/headscale) es la implementación
open source de ese servidor, pero sólo tiene línea de comandos, y montar a mano
HTTPS, usuarios y single sign-on lleva una tarde.

**Headscale Easy** es a Headscale lo que [wg-easy](https://github.com/wg-easy/wg-easy)
es a WireGuard: Headscale y todo lo que lo rodea, instalado por un script
interactivo y gestionado desde una consola inspirada en el panel de Tailscale.
En los dispositivos usas las apps oficiales de Tailscale; sólo el servidor es tuyo.

## Funcionalidades

<div class="grid cards" markdown>

-   :material-monitor-dashboard: **Consola estilo Tailscale**

    Máquinas, usuarios, DNS, control de acceso y claves en `/admin`. Tema oscuro
    y claro, funciona en el móvil.

-   :material-account-lock: **Cuentas de usuario reales**

    Contraseñas con Authentik integrado, **login con Google** opcional o tu
    propio proveedor OIDC.

-   :material-shield-account: **Una VPN privada por usuario**

    Los miembros sólo ven y alcanzan sus dispositivos; los admins lo gestionan todo.

-   :material-router-network: **Rutas y exit nodes**

    Aprueba rutas de subred y exit nodes, edita etiquetas, expira claves,
    renombra, filtra y exporta máquinas.

-   :material-dns: **DNS y ACL**

    MagicDNS, nameservers, split DNS y la política HuJSON, validados antes de
    aplicarse.

-   :material-lock-check: **HTTPS a tu manera**

    Let's Encrypt, autofirmado, detrás de tu proxy (se genera la configuración)
    o HTTP en una LAN.

-   :material-translate: **Inglés y español**

    En el instalador y en la consola. Se aceptan más idiomas.

-   :material-feather: **Ligero**

    La consola es Python de la librería estándar: sin compilación, sin framework
    JavaScript y sin base de datos propia.

</div>

## Capturas

=== "Detalle de máquina"
    ![Detalle de máquina](images/machine-detail.png){ .hse-shot }
=== "Tema claro"
    ![Máquinas, tema claro](images/machines-light.png){ .hse-shot }
=== "Usuarios"
    ![Usuarios](images/users.png){ .hse-shot }
=== "DNS"
    ![DNS](images/dns.png){ .hse-shot }
=== "Control de acceso"
    ![Control de acceso](images/access-controls.png){ .hse-shot }
=== "Claves"
    ![Claves](images/keys.png){ .hse-shot }
=== "Inicio de sesión"
    ![Inicio de sesión](images/sign-in.png){ .hse-shot }

## Apoya el proyecto

Headscale Easy lo crea y mantiene en su tiempo libre
**[Rafa Madolell](https://github.com/insanerask77)**. Si te ahorra tiempo o dinero,
⭐ [dale una estrella al repositorio](https://github.com/insanerask77/headscale-easy) e
☕ [invítame a un café en Ko-fi](https://ko-fi.com/rafaelmadolell).

<small>Headscale Easy es un proyecto independiente, sin relación con Tailscale Inc.
ni con el proyecto Headscale. "Tailscale" es una marca de Tailscale Inc.</small>
