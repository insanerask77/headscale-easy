# Primeros pasos

## Requisitos

- Un Linux (basta un VPS pequeño: 1 vCPU, 1 GB de RAM sin Authentik, 2 GB con él).
- Docker con el plugin de Compose; el instalador se ofrece a instalarlo.
- Para HTTPS real: un dominio que apunte a la máquina y los puertos 80/443 abiertos.
- El **UDP 3478** accesible desde internet (STUN del relay DERP integrado).

## Instalación

```bash
git clone https://github.com/insanerask77/headscale-easy.git
cd headscale-easy
./install.sh
```

El instalador hace unas pocas preguntas (idioma, dominio, quién pone el HTTPS,
cómo inician sesión los usuarios), escribe la configuración, arranca todo y
muestra las URLs y las primeras credenciales:

```text
  Panel web:        https://vpn.example.com/admin/
  Plano de control: https://vpn.example.com

  Inicio de sesión:
    Usuario: akadmin   Contraseña: ••••••••••••
    Da de alta usuarios en https://vpn.example.com/add-user o desde Usuarios en el panel.
```

!!! tip "Cambiar la configuración más adelante"
    Vuelve a ejecutar `./install.sh` cuando quieras. Tus respuestas anteriores
    son los valores por defecto y no se pierde ningún dato.

!!! note "Instalación desatendida"
    `./install.sh --non-interactive` no pregunta nada: cada respuesta es su
    valor por defecto, tomado del `.env` existente o, si no hay, de variables de
    entorno con los mismos nombres que en `.env`, por ejemplo
    `DOMAIN=vpn.example.com SSL_MODE=letsencrypt AUTH_PROVIDER=authentik ./install.sh --non-interactive`.
    Un valor no válido lo detiene en lugar de volver a preguntar. Los tests de
    extremo a extremo instalan el stack así.

Todas las opciones están en [Configuración](configuration.md).

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
