# Copias remotas

El contenedor ya hace una copia cada noche y la guarda en su volumen (mira
[Operación → Copias de seguridad](../operations.md#backups)). Una copia en el mismo servidor no sobrevive a
perder el servidor. Este complemento es un segundo contenedor pequeño que copia cada archivo nuevo a otro sitio.

```bash
docker compose -f compose.yaml -f advanced/backup-remote.yaml up -d
```

Solo ve el volumen de copias, en **solo lectura**, sube cada archivo una vez y aplica una retención en el destino.
rclone y rsync viven en ese contenedor, no en la imagen principal.

## A dónde

Pon `BACKUP_REMOTE` en `.env` y lo que necesite el destino en `./remote-config/` (`HSE_REMOTE_CONFIG` cambia la
carpeta).

| Destino | `BACKUP_REMOTE` | En `./remote-config/` |
|---|---|---|
| Un remoto de rclone: S3, B2, SFTP, Google Drive y [decenas más](https://rclone.org/overview/) | `s3:my-bucket/headscale-easy` | `rclone.conf` (lo escribe `rclone config`) |
| S3 sin archivo de configuración | `:s3,provider=AWS,env_auth=true,region=eu-west-1:my-bucket/dir`, más `BACKUP_AWS_ACCESS_KEY_ID` y `BACKUP_AWS_SECRET_ACCESS_KEY` | nada |
| rsync por SSH | `rsync:user@host:/srv/backups` (el directorio debe existir) | `id_ed25519`, y opcionalmente `known_hosts` (sin él se acepta la primera clave del host); `BACKUP_REMOTE_SSH_PORT` cambia el puerto |

`BACKUP_REMOTE_KEEP_DAYS` fija la retención en el destino (por defecto `BACKUP_KEEP_DAYS`) y
`BACKUP_SYNC_INTERVAL` cada cuántos minutos busca archivos nuevos (por defecto 15).

Usa una clave o un bucket que pueda escribir pero **no borrar**, si puedes: así un servidor comprometido no puede
borrar sus propias copias (fija la retención en las reglas de ciclo de vida del bucket). Si una subida falla, la
copia local se conserva y el complemento lo cuenta en `docker logs headscale-easy-backup-remote`.

Para restaurar desde el destino, descarga el archivo y sigue
[Todo en uno → Copias y restauración](../all-in-one.md#backing-up-and-restoring).

## Qué se ejecutó

`scripts/compose-smoke.sh` ejecuta este overlay con un directorio local como destino: la primera copia se sube
una vez y reiniciar el complemento no sube nada de nuevo.

**No se ejecutó:** S3, B2, SFTP ni rsync por SSH contra un servidor real.
