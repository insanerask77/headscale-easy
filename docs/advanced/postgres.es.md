# PostgreSQL

SQLite es la opción por defecto y la correcta para la mayoría: nada que ejecutar y nada que respaldar aparte.
Usa PostgreSQL cuando ya tienes uno, quieres sus herramientas o quieres la base de datos fuera del host.

**Elige antes de añadir dispositivos.** Headscale no tiene ninguna herramienta para pasar un tailnet de SQLite a
PostgreSQL (ni al revés): otra base de datos significa un tailnet nuevo y registrar de nuevo los dispositivos.

## Un servidor que ya tienes

```bash
docker compose -f compose.yaml -f advanced/postgres.yaml up -d
```

```bash
# .env
HEADSCALE_PG_HOST=db.example.com
HEADSCALE_PG_PASS=...            # el propietario de la base de datos; Headscale crea y migra sus tablas con él
HEADSCALE_PG_RO_PASS=...         # el rol de solo lectura de la consola (abajo)
#HEADSCALE_PG_PORT=5432  HEADSCALE_PG_NAME=headscale  HEADSCALE_PG_USER=headscale
#HEADSCALE_PG_SSLMODE=require    # el valor por defecto de este overlay; verify-full si llegas al servidor por red
```

Crea antes una base de datos vacía y su propietario (`CREATE ROLE headscale LOGIN PASSWORD '…'; CREATE DATABASE headscale OWNER headscale;`).

## Todavía sin servidor

```bash
docker compose -f compose.yaml -f advanced/postgres-bundled.yaml up -d
```

Las mismas variables, sin el host: el overlay arranca un contenedor `postgres` (`POSTGRES_VERSION`, por defecto
17) con su propio volumen y Headscale habla con él por la red del proyecto. Usa este archivo **en vez de**
`advanced/postgres.yaml`.

## Dos roles

| Rol | Quién lo usa | Qué puede hacer |
|---|---|---|
| `headscale` (`HEADSCALE_PG_USER`) | Headscale | todo en su base de datos |
| `headscale_ro` (`HEADSCALE_PG_RO_USER`) | la consola | `SELECT id, host_info, endpoints` de `nodes`, en transacciones de solo lectura. Nada más |

La consola solo necesita lo que cada dispositivo cuenta de sí mismo (sistema, versión de Tailscale, relay,
endpoints): nunca las claves de los nodos, los usuarios ni las claves de API, así que no las recibe.

**La imagen crea sola el rol de solo lectura**, cuando Headscale está sano y antes de cada arranque de la
consola, con el SQL de
[`templates/headscale-pg-readonly.sql`](https://github.com/insanerask77/headscale-easy/blob/main/templates/headscale-pg-readonly.sql).
Es idempotente: un reinicio repara un rol borrado. Si el propietario no puede crear roles (algunos servicios
gestionados), la imagen registra un `ERROR` y la consola vuelve a usar las credenciales del propietario:
sigue funcionando, sin la separación. En ese caso crea el rol como administrador:

```bash
HSE_RO_USER=headscale_ro HSE_RO_PASS='la contraseña' \
  psql -v ON_ERROR_STOP=1 -h <host> -U <admin> -d headscale -f templates/headscale-pg-readonly.sql
```

## Copias de seguridad

`hse backup` y las copias programadas ejecutan `pg_dump` (el cliente PostgreSQL 18 va en la imagen) y guardan
`headscale.sql` en el archivo. Una restauración desde la consola, o `hse restore <archivo> --with-postgres`, lo
carga con Headscale parado (borra y vuelve a crear los objetos de su base de datos). `pg_dump` vuelca servidores
**hasta su propia versión mayor**: aquí, de la 13 a la 18. Con un servidor más nuevo la copia falla con una
frase que lo dice; usa un PostgreSQL 18 o anterior. El volcado omite la línea que PostgreSQL 17 y posteriores
añaden y que un PostgreSQL 16 rechazaría, así que una copia se restaura en cualquier servidor de la 13 a la 18.

Las cuentas de la consola, las claves y la configuración no están en PostgreSQL: están en los otros archivos de la
copia.

## Qué se ejecutó

`scripts/advanced-smoke.sh postgres 16|17|18` con `advanced/postgres-bundled.yaml` y un PostgreSQL real de cada
versión, sobre la imagen construida desde este repositorio. En cada una: Headscale sano; el proceso de la
consola corre con `headscale_ro`; ese rol lee `id, host_info, endpoints` y se le niega `node_key`, `users`,
`api_keys` y cualquier escritura; `hse backup` escribe `headscale.sql`; un usuario borrado después de la copia
vuelve con `hse restore --with-postgres` y la consola vuelve a estar sana.

**No se ejecutó:** `advanced/postgres.yaml` contra un PostgreSQL gestionado (los modos `require` / `verify-full`
y un propietario que no puede crear roles), ni un cliente Tailscale real registrándose sobre PostgreSQL en esta
ronda.
