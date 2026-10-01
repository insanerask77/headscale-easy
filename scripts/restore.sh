#!/usr/bin/env bash
# =============================================================================
#  Headscale Easy — restore a backup made by the backup container
#  https://github.com/insanerask77/headscale-easy
#
#  Usage: ./scripts/restore.sh <backups/headscale-easy-YYYYmmdd-HHMMSS.tar.gz> [--yes]
#         (or: make restore file=...). The file may also be on the remote
#         copy: make restore file=s3:bucket/dir/headscale-easy-....tar.gz
#
#  Stops the stack, puts back the configuration (the current files are kept as
#  <file>.before-restore-<time>), Headscale's database and private keys,
#  Authentik's database and Caddy's internal CA, and starts the stack again.
#  Works on a fresh host too: clone the repository, run this, done.
# =============================================================================

set -euo pipefail
# Globs must include dotfiles: .env is the most important file of a backup
shopt -s dotglob nullglob

PROJECT_DIR="${HSE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
HEADSCALE_VOLUME="${HSE_HEADSCALE_VOLUME:-headscale-data}"
CADDY_VOLUME="${HSE_CADDY_VOLUME:-caddy-data}"
PG_HOST="${HSE_PG_HOST:-authentik-postgresql}"
BACKUP_IMAGE="${HSE_BACKUP_IMAGE:-headscale-easy-backup:local}"
USE_COMPOSE="${HSE_USE_COMPOSE:-1}"   # 0 = do not stop/start the stack (tests)

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'; BOLD='\033[1m'
UI_LANG=$(grep -E '^UI_LANG=' "$PROJECT_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' || true)
t() { if [[ "${UI_LANG:-en}" == "es" ]]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
info() { echo -e "${BLUE}ℹ${NC} $1"; }
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
die()  { echo -e "${RED}✗${NC} $1" >&2; exit 1; }

ARCHIVE="${1:-}"
ASSUME_YES="${2:-}"
[[ -n "$ARCHIVE" ]] || die "Usage: $0 <backup.tar.gz | remote:path/backup.tar.gz> [--yes]"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT


# A remote backup (BACKUP_REMOTE: an rclone remote such as s3:bucket/dir/file.tar.gz,
# or rsync:user@host:/dir/file.tar.gz) is downloaded first through the backup image.
if [[ ! -e "$ARCHIVE" && "$ARCHIVE" =~ ^[A-Za-z0-9_.:-]*: ]]; then
    info "$(t "Downloading $ARCHIVE ..." "Descargando $ARCHIVE ...")"
    mkdir -p "$WORK/dl"
    (cd "$PROJECT_DIR" && docker compose run --rm --no-deps -v "$WORK/dl:/restore-out" \
        --entrypoint /usr/local/bin/remote.sh backup fetch "$ARCHIVE" /restore-out) \
        || die "$(t "Could not download the backup" "No se pudo descargar la copia")"
    ARCHIVE=$(ls "$WORK"/dl/*.tar.gz 2>/dev/null | head -1 || true)
fi
[[ -n "$ARCHIVE" && -f "$ARCHIVE" ]] || die "Usage: $0 <backup.tar.gz | remote:path/backup.tar.gz> [--yes]"
ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")"

mkdir -p "$WORK/x"
tar -xzf "$ARCHIVE" -C "$WORK/x"
B="$WORK/x/$(ls "$WORK/x" | head -1)"
# Headscale on SQLite (db.sqlite) or on PostgreSQL (headscale.sql, pg_dump)
if [[ -f "$B/headscale/db.sqlite" ]]; then
    hs_db="db.sqlite"
elif [[ -f "$B/headscale/headscale.sql" ]]; then
    hs_db="headscale.sql"
else
    die "$(t "Not a Headscale Easy backup" "No es una copia de Headscale Easy"): $ARCHIVE"
fi

echo -e "${BOLD}Headscale Easy — $(t "restore" "restaurar")${NC} $(basename "$ARCHIVE")"
keys=("$B"/headscale/*.key)
echo "  headscale: ${hs_db} + ${#keys[@]} keys"
[[ -f "$B/authentik/authentik.sql" ]] && echo "  authentik: authentik.sql"
[[ -d "$B/caddy/pki" ]] && echo "  caddy: pki"
config_files=("$B"/config/*)
echo "  config: ${config_files[*]##*/}"
warn "$(t "Current users, machines and settings are replaced by the backup's." "Los usuarios, máquinas y ajustes actuales se sustituyen por los de la copia.")"
if [[ "$ASSUME_YES" != "--yes" ]]; then
    read -r -p "$(t "Continue? [y/N]" "¿Continuar? [s/N]"): " answer
    [[ "$(echo "${answer:-n}" | tr '[:upper:]' '[:lower:]')" =~ ^(y|yes|s|si|sí)$ ]] || { info "$(t "Cancelled" "Cancelado")"; exit 0; }
fi

cd "$PROJECT_DIR"
if ! docker image inspect "$BACKUP_IMAGE" >/dev/null 2>&1; then
    info "$(t "Building the backup image..." "Construyendo la imagen de copias...")"
    docker compose build -q backup
fi
if [[ "$USE_COMPOSE" == "1" ]]; then
    info "$(t "Stopping the stack..." "Deteniendo el stack...")"
    # headscale-postgresql keeps running: the dump is loaded into it
    docker compose --profile authentik --profile backup stop headscale web backup authentik-server authentik-worker 2>/dev/null || true
fi

# 1. Configuration: .env holds the secrets the databases depend on (e.g.
#    Authentik's secret key), so it goes back too. Current files are kept.
stamp=$(date +%Y%m%d-%H%M%S)
for f in "$B"/config/*; do
    [[ -f "$f" ]] || continue
    name=$(basename "$f")
    target="$PROJECT_DIR/${name//_//}"          # data_web_api-key -> data/web/api-key
    mkdir -p "$(dirname "$target")"
    if [[ -f "$target" ]] && ! cmp -s "$f" "$target"; then
        cp -p "$target" "$target.before-restore-$stamp"
    fi
    cp -p "$f" "$target"
done
chmod 600 "$PROJECT_DIR/.env" 2>/dev/null || true
ok "$(t "Configuration restored" "Configuración restaurada")"

# The web UI's activity log
if [[ -f "$B/web/audit.db" ]]; then
    mkdir -p "$PROJECT_DIR/data/web"
    rm -f "$PROJECT_DIR/data/web/audit.db-wal" "$PROJECT_DIR/data/web/audit.db-shm"
    cp -p "$B/web/audit.db" "$PROJECT_DIR/data/web/audit.db"
    ok "$(t "Activity log restored" "Registro de actividad restaurado")"
fi

# 2. Headscale: private keys (same keys = devices stay registered) and, on
#    SQLite, the database file. A stale db.sqlite never stays next to a
#    PostgreSQL restore.
docker volume create "$HEADSCALE_VOLUME" >/dev/null
docker run --rm -v "$HEADSCALE_VOLUME:/d" -v "$B/headscale:/b:ro" --entrypoint sh "$BACKUP_IMAGE" -c \
    'rm -f /d/db.sqlite /d/db.sqlite-wal /d/db.sqlite-shm && cp /b/*.key /d/ && chmod 600 /d/*.key &&
     if [ -f /b/db.sqlite ]; then cp /b/db.sqlite /d/ && chmod 644 /d/db.sqlite; fi && chown root:root /d/*'

# On PostgreSQL: the web UI's read-only role first (the dump grants it
# access to "nodes"), then the dump, which drops and recreates every object
# (pg_dump --clean), then the role again (idempotent). Connection settings
# come from the restored .env.
if [[ "$hs_db" == "headscale.sql" ]]; then
    env_get() { grep -E "^$1=" "$PROJECT_DIR/.env" | tail -1 | cut -d= -f2- | tr -d '"'; }
    hs_pg_host="${HSE_HS_PG_HOST:-$(env_get HEADSCALE_PG_HOST)}"; hs_pg_host="${hs_pg_host:-headscale-postgresql}"
    hs_pg_port=$(env_get HEADSCALE_PG_PORT); hs_pg_port="${hs_pg_port:-5432}"
    hs_pg_name=$(env_get HEADSCALE_PG_NAME); hs_pg_name="${hs_pg_name:-headscale}"
    hs_pg_user=$(env_get HEADSCALE_PG_USER); hs_pg_user="${hs_pg_user:-headscale}"
    hs_pg_sslmode=$(env_get HEADSCALE_PG_SSLMODE); hs_pg_sslmode="${hs_pg_sslmode:-disable}"
    hs_ro_user=$(env_get HEADSCALE_PG_RO_USER); hs_ro_user="${hs_ro_user:-headscale_ro}"
    network="${HSE_NETWORK:-$(env_get NETWORK_NAME)}"; network="${network:-headscale-net}"
    if [[ "$USE_COMPOSE" == "1" && "$(env_get HEADSCALE_PG_EXTERNAL)" != "true" ]]; then
        docker compose --profile postgres up -d headscale-postgresql >/dev/null
    fi
    export PGHOST="$hs_pg_host" PGPORT="$hs_pg_port" PGUSER="$hs_pg_user" PGDATABASE="$hs_pg_name" PGSSLMODE="$hs_pg_sslmode"
    PGPASSWORD=$(env_get HEADSCALE_PG_PASS); HSE_RO_USER="$hs_ro_user"; HSE_RO_PASS=$(env_get HEADSCALE_PG_RO_PASS)
    export PGPASSWORD HSE_RO_USER HSE_RO_PASS
    pg_env=(-e PGHOST -e PGPORT -e PGUSER -e PGDATABASE -e PGSSLMODE -e PGPASSWORD -e HSE_RO_USER -e HSE_RO_PASS)
    for _ in $(seq 1 30); do
        docker run --rm --network "$network" "${pg_env[@]}" --entrypoint pg_isready "$BACKUP_IMAGE" -q && break
        sleep 2
    done
    ro_sql="$PROJECT_DIR/templates/headscale-pg-readonly.sql"
    pg_psql() {
        docker run --rm --network "$network" "${pg_env[@]}" -v "$B/headscale:/b:ro" -v "$ro_sql:/ro.sql:ro" \
            --entrypoint pg-client.sh "$BACKUP_IMAGE" psql -q -v ON_ERROR_STOP=1 "$@" >/dev/null
    }
    pg_psql -f /ro.sql
    pg_psql -f /b/headscale.sql
    pg_psql -f /ro.sql
    unset PGPASSWORD HSE_RO_PASS
fi
ok "$(t "Headscale database and keys restored" "Base de datos y claves de Headscale restauradas")"

# 3. Authentik: the dump drops and recreates every object (pg_dump --clean)
if [[ -f "$B/authentik/authentik.sql" ]]; then
    pg_pass=$(grep -E '^AUTHENTIK_PG_PASS=' "$PROJECT_DIR/.env" | cut -d= -f2- | tr -d '"')
    network="${HSE_NETWORK:-$(grep -E '^NETWORK_NAME=' "$PROJECT_DIR/.env" | cut -d= -f2 | tr -d '"')}"
    network="${network:-headscale-net}"
    if [[ "$USE_COMPOSE" == "1" ]]; then
        docker compose --profile authentik up -d authentik-postgresql >/dev/null
    fi
    for _ in $(seq 1 30); do
        docker run --rm --network "$network" -e PGPASSWORD="$pg_pass" --entrypoint pg_isready "$BACKUP_IMAGE" \
            -q -h "$PG_HOST" -U authentik && break
        sleep 2
    done
    docker run --rm --network "$network" -e PGPASSWORD="$pg_pass" -e PGHOST="$PG_HOST" -e PGUSER=authentik \
        -v "$B/authentik:/b:ro" --entrypoint pg-client.sh "$BACKUP_IMAGE" \
        psql -q -d authentik -v ON_ERROR_STOP=1 -f /b/authentik.sql >/dev/null
    ok "$(t "Authentik database restored" "Base de datos de Authentik restaurada")"
fi

# 4. Caddy's internal CA (SSL_MODE=selfsigned)
if [[ -d "$B/caddy/pki" ]]; then
    docker volume create "$CADDY_VOLUME" >/dev/null
    docker run --rm -v "$CADDY_VOLUME:/data" -v "$B/caddy:/b:ro" --entrypoint sh "$BACKUP_IMAGE" -c \
        'mkdir -p /data/caddy && rm -rf /data/caddy/pki && cp -rp /b/pki /data/caddy/'
    ok "$(t "Caddy's CA restored" "CA de Caddy restaurada")"
fi

if [[ "$USE_COMPOSE" == "1" ]]; then
    info "$(t "Starting the stack..." "Arrancando el stack...")"
    docker compose up -d
fi
ok "$(t "Restore complete" "Restauración completada")"
