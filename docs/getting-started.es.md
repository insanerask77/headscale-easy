# Primeros pasos

## En cinco minutos

1. **Descarga:** `mkdir headscale-easy && cd headscale-easy && curl -fsSLO https://raw.githubusercontent.com/insanerask77/headscale-easy/main/compose.yaml`
2. **Arráncalo:** `docker compose up -d`. Un contenedor, con la versión 2.0 fijada en el archivo.
3. **Configúralo:** abre `http://<tu-servidor>/console/setup` y escribe el token de un solo uso (de
   `docker compose logs`): administrador, dirección pública y HTTPS, nombre de la tailnet, relay
   (DERP), registro y copias.
4. **Entra:** abre `https://<tu-dominio>/console`.
5. **Conecta tu primer dispositivo:** `tailscale up --login-server=https://<tu-dominio>`.

Esto te da un servidor que funciona. Antes de fiarte de él, o de exponerlo a otras personas,
repasa [Producción y bastionado](hardening.md).

## Requisitos

- Un Linux (basta un VPS pequeño: 1 vCPU y 1 GB de RAM; el contenedor en reposo usa menos de
  100 MB).
- Docker con el plugin de Compose (v2.24 o más reciente).
- Para HTTPS real: un dominio que apunte a la máquina y los puertos 80/443 abiertos.
- El **UDP 3478** accesible desde internet (STUN del relay DERP integrado).

## Instalación

```bash
mkdir headscale-easy && cd headscale-easy
curl -fsSLO https://raw.githubusercontent.com/insanerask77/headscale-easy/main/compose.yaml
docker compose up -d
```

`compose.yaml` es toda la app: un servicio, dos volúmenes con nombre (`hse-data` y
`hse-backups`), sin capabilities añadidas y sin socket de Docker. Abre el asistente y
responde ahí; el token de un solo uso está en `docker compose logs headscale-easy`.

Para responder de antemano (o arrancar sin asistente), pon las respuestas en un `.env` al lado:

```bash
curl -fsSL https://raw.githubusercontent.com/insanerask77/headscale-easy/main/.env.example -o .env
chmod 600 .env        # luego descomenta y edita lo que necesites
```

Por ejemplo `HSE_PUBLIC_URL`, `HSE_TLS` (`auto`: Let's Encrypt, necesita `ACME_EMAIL`; `internal`:
un certificado de la CA propia de Caddy; `off`: HTTP sin cifrar, o lo termina un proxy que ya
tienes) y `HSE_ADMIN_EMAIL` con `HSE_ADMIN_PASSWORD` para saltarte el asistente: el servidor
arranca con esa cuenta.

!!! tip "Actualizar"
    Cambia `HSE_VERSION` en `.env` (o la etiqueta en `compose.yaml`) y ejecuta
    `docker compose pull && docker compose up -d`. Los datos viven en los volúmenes y no se tocan;
    haz antes una copia con `docker exec headscale-easy hse backup`.

Todo lo demás (un proveedor de identidad, un proxy delante, PostgreSQL, copias remotas) está en
[Configuraciones avanzadas](advanced/index.md). Mira [Imagen todo en uno](all-in-one.md) y
[Configuración](configuration.md) para todos los ajustes.

## Puertos

| Puerto | Protocolo | Uso |
|--------|-----------|-----|
| 80 / 443 | TCP | Consola web, plano de control, Let's Encrypt |
| 3478 | UDP | STUN del relay DERP integrado |

## Conecta tu primer dispositivo

Abre `https://<tu-dominio>/console`, inicia sesión y pulsa **Añadir dispositivo**:
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
