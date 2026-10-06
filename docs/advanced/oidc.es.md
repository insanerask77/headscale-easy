# Tu propio proveedor de acceso

Las cuentas locales (contraseña y doble factor en la consola) son lo normal y no necesitan nada más. Si tu gente ya
tiene un proveedor de identidad, pon `OIDC_ISSUER`, `OIDC_CLIENT_ID` y `OIDC_CLIENT_SECRET` y entra en la consola
y registra dispositivos (`tailscale up --login-server …`) a través de él. Las cuentas locales siguen funcionando a
su lado.

El proveedor solo da acceso. La consola y Headscale confían en su identidad y sus grupos y no gestionan nada
dentro de él: invitaciones, restablecimientos y doble factor siguen siendo de las cuentas locales de la consola.

**Registra dos URI de redirección** en el proveedor, para un cliente que sirve a ambos:

| URI | La usa |
|---|---|
| `https://vpn.example.com/admin/callback` | la consola |
| `https://vpn.example.com/oidc/callback` | Headscale |
| `https://vpn.example.com/admin/` | cierre de sesión (donde el proveedor lista las URI posteriores al cierre) |

Headscale no arranca hasta que puede leer `<issuer>/.well-known/openid-configuration`: el proveedor tiene que ser
alcanzable **desde el contenedor**, en esa dirección y con un certificado en el que el contenedor confíe.

## Quién puede entrar y quién es qué

| Variable | Qué |
|---|---|
| `HSE_OIDC_ALLOWED_DOMAINS`, `HSE_OIDC_ALLOWED_USERS`, `HSE_OIDC_ALLOWED_GROUPS` | Quién puede entrar (`oidc.allowed_*` de Headscale), separados por comas. **Vacío significa todo el que el proveedor deje pasar**: bien para tu propio Authentik o Keycloak, mal para Google, donde vale cualquier cuenta |
| `PORTAL_ADMIN_GROUPS`, `PORTAL_NETWORK_ADMIN_GROUPS`, `PORTAL_AUDITOR_GROUPS` | Quién es qué en la consola, por los grupos que envía tu proveedor (grupo de administradores por defecto: `vpn-admins`) |
| `PORTAL_ADMIN_EMAILS` | Administradores por correo, para proveedores sin grupos. Solo cuenta para una dirección que el proveedor llama **verificada** |
| `OIDC_SCOPE` | Ámbitos que se piden al entrar (por defecto `openid profile email`; añade `groups` si el proveedor solo libera grupos bajo petición) |

Headscale identifica a un usuario OIDC por la **URL del emisor** del proveedor más el id del usuario: elige la
dirección del emisor una vez y no la cambies.

## Authentik

```bash
docker compose -f compose.yaml -f advanced/oidc/authentik.yaml up -d
```

Ejecuta Authentik (servidor, worker y su propio PostgreSQL) junto a la imagen y lo sirve en
`https://<tu dominio>/authentik/` **a través del Caddy de la propia imagen**
(`HSE_AUTHENTIK_UPSTREAM=authentik-server:9000`), así que el emisor es
`https://vpn.example.com/authentik/application/o/headscale/` y no cambia nunca.

```bash
# .env: genera cada secreto con   openssl rand -base64 36 | tr -d '/+=' | cut -c1-40
HSE_PUBLIC_URL=https://vpn.example.com
HSE_DOMAIN=vpn.example.com
AUTHENTIK_SECRET_KEY=...   AUTHENTIK_PG_PASS=...   OIDC_CLIENT_SECRET=...
AUTHENTIK_ADMIN_EMAIL=admin@example.com   AUTHENTIK_BOOTSTRAP_PASSWORD=...   # el primer administrador de Authentik ('akadmin')
#GOOGLE_CLIENT_ID=...  GOOGLE_CLIENT_SECRET=...     # "Entrar con Google" dentro de Authentik
```

El primer arranque tarda **varios minutos** (Authentik crea su base de datos y aplica el blueprint); mientras tanto
la imagen reinicia Headscale y aparece como `unhealthy`. Después abre `https://vpn.example.com/authentik/` como
`akadmin`. El blueprint
([`advanced/oidc/authentik/blueprints/headscale.yaml`](https://github.com/insanerask77/headscale-easy/blob/main/advanced/oidc/authentik/blueprints/headscale.yaml))
crea la aplicación OIDC y los grupos `headscale-users` (pueden unirse al tailnet) y `vpn-admins` (además
administran la consola); añade a la gente a ellos. Detrás de un proxy tuyo, pon también `HSE_SELF_IP` con la
dirección del proxy para que la imagen llegue a su propio nombre público a través de él.

¿Ya tienes un Authentik? Sáltate el overlay: pon `OIDC_ISSUER` con su emisor, o sírvelo bajo tu dominio
ejecutándolo junto al contenedor y poniendo `HSE_AUTHENTIK_UPSTREAM=<host>:9000`.

## Pocket ID

[Pocket ID](https://pocket-id.org) es un proveedor pequeño (unos 100 MB, sin servidor de base de datos) que da
acceso con passkeys. Las passkeys necesitan HTTPS y Pocket ID necesita un dominio propio, y dos dominios no pueden
tener a la vez el puerto 443, así que el overlay pone un Caddy pequeño delante de los dos. La imagen corre con
`HSE_TLS=off` y confía solo en la dirección fija de ese Caddy.

```bash
# .env
HSE_DOMAIN=vpn.example.com   ID_DOMAIN=id.example.com   ACME_EMAIL=admin@example.com
POCKET_ID_ENCRYPTION_KEY=...   # openssl rand -base64 32; guárdala: sin ella no se pueden leer los datos de Pocket ID
OIDC_ISSUER=https://id.example.com   PORTAL_ADMIN_EMAILS=admin@example.com
```

El orden importa: Headscale no arranca con un emisor que no responde, y el id y el secreto del cliente solo existen
cuando Pocket ID ya está en marcha.

1. Apunta los dos dominios al host; los puertos 80 y 443 deben llegar a él.
2. `docker compose -f compose.yaml -f advanced/oidc/pocket-id.yaml up -d proxy pocket-id`
3. Abre `https://id.example.com/setup`, crea el administrador y su passkey.
4. **Administration → OIDC Clients → Add**: nombre *Headscale Easy*, las dos URL de callback de arriba, callback
   de cierre `https://vpn.example.com/admin/`, cliente público desactivado, PKCE activado. Copia el id y el secreto
   en `.env` como `OIDC_CLIENT_ID` y `OIDC_CLIENT_SECRET`.
5. **Administration → Application configuration**: activa **Emails verified**, o la consola (con razón) ignora
   `PORTAL_ADMIN_EMAILS` y todo el mundo es miembro.
6. `docker compose -f compose.yaml -f advanced/oidc/pocket-id.yaml up -d` y *Sign in with SSO* en
   `https://vpn.example.com/admin/`.

Mantén Pocket ID cerrado (**Allow user sign-ups** en *Disabled*, el valor por defecto) para que solo entren las
personas que creas o invitas. Los roles por grupo necesitan `OIDC_SCOPE=openid profile email groups`;
`PORTAL_ADMIN_EMAILS` es lo más sencillo.

## Keycloak

Keycloak corre donde ya lo ejecutes. En un realm (por ejemplo `hse`, emisor `https://sso.example.com/realms/hse`)
crea un cliente:

| Campo | Valor |
|---|---|
| Client type / ID | OpenID Connect / `headscale` |
| Client authentication | **On** (cliente confidencial), solo Standard flow |
| Valid redirect URIs | las dos callbacks de arriba |
| Valid post logout redirect URIs | `https://vpn.example.com/admin/` |
| Método PKCE | `S256` (Advanced settings) |

Roles por grupo: crea un grupo `vpn-admins` y, en el ámbito dedicado del cliente, añade un mapper **Group
Membership** con *Token Claim Name* `groups`, *Full group path* **desactivado** y *Add to userinfo* **activado** (la
consola lee los grupos de ahí). Los usuarios necesitan un correo marcado como *Email verified*. Después, en `.env`:

```bash
OIDC_ISSUER=https://sso.example.com/realms/hse
OIDC_CLIENT_ID=headscale
OIDC_CLIENT_SECRET=<el secreto de Credentials>
```

Deja **User registration** desactivado en el realm y no actives un acceso social que admita a cualquiera.

## Google

```bash
OIDC_ISSUER=https://accounts.google.com
OIDC_CLIENT_ID=<client id>.apps.googleusercontent.com
OIDC_CLIENT_SECRET=<el secreto>
PORTAL_ADMIN_EMAILS=tu@tu-empresa.com
```

Crea el cliente OAuth en Google Cloud (*Credentials → OAuth client ID → Web application*) con las dos URI de
redirección de arriba. **Lee esto antes:** Google deja entrar a cualquier cuenta de Google salvo que lo limites.

| Tu Google | ¿Se puede usar directamente? |
|---|---|
| **Workspace**, pantalla de consentimiento de tipo **Internal** | Sí: solo entra tu organización |
| Cuentas Gmail personales, pantalla de consentimiento **External** | **No**, salvo que pongas también `HSE_OIDC_ALLOWED_DOMAINS` o `HSE_OIDC_ALLOWED_USERS` |

Google no envía grupos: los roles salen de `PORTAL_ADMIN_EMAILS`; todos los demás son miembros. Sin Workspace, pon
Google detrás de un proveedor que decida quién entra (el blueprint de Authentik tiene "Entrar con Google" como
fuente).

## Qué se ejecutó

- **Overlay de Pocket ID** (`advanced/oidc/pocket-id.yaml`, Pocket ID `v1`): con un Caddyfile de prueba en HTTP
  plano (aquí no hay dominio público), el Caddy, Pocket ID y la imagen arrancan y quedan sanos; Pocket ID responde
  su documento de descubrimiento a través del Caddy, la imagen responde `/key` y redirige `/admin` a su página de
  acceso a través del Caddy, y la imagen publica solo el UDP 3478.
- `scripts/validate.sh` ejecuta `docker compose config` sobre los overlays de Authentik y Pocket ID y sobre las
  combinaciones con el proxy, PostgreSQL y las copias remotas.

**No se ejecutó en esta ronda, solo se leyó:** una persona entrando con cualquier proveedor (Authentik, Pocket ID,
Keycloak, Google), el primer arranque de Authentik y su blueprint recortado en una instancia viva, el mapper de
Keycloak, la pantalla de consentimiento de Google, HTTPS con Let's Encrypt, ni un cliente Tailscale real
registrándose con un proveedor. Los nombres de campos y menús de arriba son los de Pocket ID 1.16, Keycloak 26 y la
consola de Google, tomados de su documentación, y pueden haber cambiado. Trata estas secciones como una lista para
confirmar.
