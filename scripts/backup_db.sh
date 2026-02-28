#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker-compose-prd.yaml"
BACKUP_DIR="${ROOT_DIR}/backups/db"
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE=""

usage() {
  cat <<'EOF'
Usage: scripts/backup_db.sh [--compose-file FILE] [--backup-dir DIR] [--output FILE]

Creates a PostgreSQL logical backup from the running `db` container and writes
it to a gzip-compressed SQL file.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose-file)
      COMPOSE_FILE="$2"
      shift 2
      ;;
    --backup-dir)
      BACKUP_DIR="$2"
      shift 2
      ;;
    --output)
      BACKUP_FILE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

mkdir -p "${BACKUP_DIR}"

if [[ -z "${BACKUP_FILE}" ]]; then
  BACKUP_FILE="${BACKUP_DIR}/futurapredict_${TS}.sql.gz"
fi

echo "Creating DB backup: ${BACKUP_FILE}"

docker compose -f "${COMPOSE_FILE}" exec -T db sh -lc \
  'PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump --clean --if-exists --no-owner --no-privileges -U "${POSTGRES_USER}" -d "${POSTGRES_DB}"' \
  | gzip > "${BACKUP_FILE}"

echo "Backup complete: ${BACKUP_FILE}"

