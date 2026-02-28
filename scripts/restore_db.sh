#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker-compose-prd.yaml"
BACKUP_FILE=""

usage() {
  cat <<'EOF'
Usage: scripts/restore_db.sh --input BACKUP_FILE [--compose-file FILE]

Restores PostgreSQL from a gzip-compressed SQL backup created by backup_db.sh.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose-file)
      COMPOSE_FILE="$2"
      shift 2
      ;;
    --input)
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

if [[ -z "${BACKUP_FILE}" ]]; then
  echo "Missing required argument: --input BACKUP_FILE" >&2
  usage
  exit 1
fi

if [[ ! -f "${BACKUP_FILE}" ]]; then
  echo "Backup file not found: ${BACKUP_FILE}" >&2
  exit 1
fi

echo "Restoring DB from: ${BACKUP_FILE}"

gunzip -c "${BACKUP_FILE}" | docker compose -f "${COMPOSE_FILE}" exec -T db sh -lc \
  'PGPASSWORD="${POSTGRES_PASSWORD}" psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER}" -d "${POSTGRES_DB}"'

echo "Restore complete."

