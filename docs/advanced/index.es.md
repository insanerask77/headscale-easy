# Configuraciones avanzadas

La instalación simple es [`compose.yaml`](https://github.com/insanerask77/headscale-easy/blob/main/compose.yaml)
y un `.env` opcional: `docker compose up -d` y el asistente de configuración. No necesita nada de esta sección.

Todo lo de aquí es opcional y se añade con **un archivo más**, nunca editando `compose.yaml`. Compose combina
varios archivos, así que las opciones se pueden sumar:

```bash
docker compose -f compose.yaml -f advanced/proxy.yaml -f advanced/postgres.yaml up -d
```

Necesitas Docker Compose 2.24 o más reciente (los overlays usan `!override`).

| Quiero… | Añadir | Guía |
|---|---|---|
| un PostgreSQL que ya tengo, en vez de SQLite | `advanced/postgres.yaml` | [PostgreSQL](postgres.md) |
| un contenedor PostgreSQL a su lado | `advanced/postgres-bundled.yaml` (en vez de la línea anterior) | [PostgreSQL](postgres.md) |
| mi propio proxy para el HTTPS (nginx, Traefik, Caddy, Nginx Proxy Manager) | `advanced/proxy.yaml` | [Un proxy delante](proxy.md) |
| cada copia de seguridad en S3, B2, SFTP o un servidor | `advanced/backup-remote.yaml` | [Copias remotas](backup-remote.md) |
| acceso con Authentik, Pocket ID, Keycloak o Google | `advanced/oidc/authentik.yaml`, `advanced/oidc/pocket-id.yaml`, o solo variables | [Tu propio proveedor de acceso](oidc.md) |

Las variables van en el mismo `.env` de siempre; cada guía lista las que necesita y la lista completa es la
[referencia de variables de entorno](../configuration.md#reference).

## Qué dice cada guía que comprobó

Cada guía termina con lo que se ejecutó y lo que solo se leyó. Para las opciones que corren en tu máquina, el
repositorio las comprueba en cada cambio: `scripts/validate.sh` ejecuta `docker compose config` sobre cada
overlay y sobre las combinaciones de arriba, y `scripts/advanced-smoke.sh` arranca el overlay de PostgreSQL en
PostgreSQL 16, 17 y 18 y el de proxy detrás de un nginx real.
