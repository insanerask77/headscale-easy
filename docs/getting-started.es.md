# Primeros pasos

## En cinco minutos

1. **Clona:** `git clone https://github.com/insanerask77/headscale-easy.git && cd headscale-easy`
2. **Ejecuta el instalador:** `./install.sh`. Pregunta la dirección pública y quién pone el
   HTTPS (Let's Encrypt por defecto), y arranca un contenedor.
3. **Configúralo:** abre la dirección que imprime y escribe el token de un solo uso (de
   `docker compose logs`): administrador, nombre de la tailnet, relay (DERP), registro y copias.
4. **Entra:** abre `https://<tu-dominio>/admin`.
5. **Conecta tu primer dispositivo:** `tailscale up --login-server=https://<tu-dominio>`.

Esto te da un servidor que funciona. Antes de fiarte de él, o de exponerlo a otras personas,
repasa [Producción y bastionado](hardening.md).

## Requisitos

- Un Linux (basta un VPS pequeño: 1 vCPU y 1 GB de RAM; el contenedor en reposo usa menos de
  100 MB).
- Docker con el plugin de Compose; el instalador se ofrece a instalarlo.
- Para HTTPS real: un dominio que apunte a la máquina y los puertos 80/443 abiertos.
- El **UDP 3478** accesible desde internet (STUN del relay DERP integrado).

## Instalación

```bash
git clone https://github.com/insanerask77/headscale-easy.git
cd headscale-easy
./install.sh
```

El instalador pregunta la dirección pública, quién pone el HTTPS (`auto`: Let's Encrypt, y pide un
correo; `internal`: un certificado de su propia CA; `off`: HTTP sin cifrar, o lo termina un proxy
que ya tienes) y, opcionalmente, un correo (y contraseña) de administrador para saltarse el asistente.
Escribe un compose y un `.env` pequeño (modo 600) en `./headscale-easy` (`--dir` lo cambia), ejecuta `docker compose up -d`, espera a que el contenedor esté sano e imprime la
dirección del asistente.

Sin preguntas:

```bash
HSE_PUBLIC_URL=https://vpn.example.com HSE_TLS=auto ACME_EMAIL=yo@example.com \
HSE_ADMIN_EMAIL=yo@example.com HSE_ADMIN_PASSWORD='una contraseña larga' \
  ./install.sh --yes
```

Con `HSE_ADMIN_EMAIL` no hay asistente: el servidor arranca con esa cuenta.

!!! tip "Actualizar"
    Vuelve a ejecutar `./install.sh` en el mismo directorio: solo descarga la imagen nueva y
    recrea el contenedor. El `.env` y los datos no se tocan.

Todo lo demás (un proveedor de identidad, un proxy delante, PostgreSQL, copias remotas) está en la
[edición avanzada](advanced.md). Mira [Imagen todo en uno](all-in-one.md) para todos los ajustes.

## Puertos

| Puerto | Protocolo | Uso |
|--------|-----------|-----|
| 80 / 443 | TCP | Consola web, plano de control, Let's Encrypt |
| 3478 | UDP | STUN del relay DERP integrado |

## Conecta tu primer dispositivo

Abre `https://<tu-dominio>/admin`, inicia sesión y pulsa **Añadir dispositivo**:
muestra los pasos para cada sistema. En resumen:

=== "Linux"
    ```bash
    curl -fsSL https://tailscale.com/install.sh | sh
    sudo tailscale up --login-server=https://<tu-dominio>
    ```
    Abre la URL que imprime e inicia sesión.

=== "Windows / macOS"
    Instala la app de Tailscale y en su menú elige **Settings → Change server**
    (macOS: mantén ++option++ y pulsa el icono del menú) e introduce
    `https://<tu-dominio>`. Inicia sesión cuando se abra el navegador.

=== "iOS / Android"
    Instala la app de Tailscale, toca el icono de perfil (arriba a la derecha)
    y luego *Log in*. Abre el menú de arriba a la derecha (iOS: ⋯ → *Use a
    custom coordination server*; Android: ⋮ → *Use an alternate server*),
    introduce `https://<tu-dominio>` y toca *Log in*. Hace falta un certificado
    de confianza pública (Let's Encrypt o tu propio proxy).

    La página **Añadir dispositivo** muestra la URL como código QR: escanéalo
    con la cámara del móvil para tenerla en el teléfono y pégala en la app (la
    app de Tailscale no lee códigos QR por sí misma).

=== "Servidores (clave)"
    Genera una clave en **Ajustes → Claves** y después:
    ```bash
    sudo tailscale up --login-server=https://<tu-dominio> --authkey=<clave>
    ```
    La clave nueva también se puede mostrar como códigos QR (la clave y este
    comando). En Android, pega una clave con ⋮ → *Use an auth key* después de
    configurar el servidor.

Una vez conectado, el dispositivo aparece en **Máquinas**. Lo siguiente: dar de
alta a más gente ([Operación](operations.md#users-and-admins)) y ajustar el
[control de acceso](configuration.md#network-isolation-and-acls).
