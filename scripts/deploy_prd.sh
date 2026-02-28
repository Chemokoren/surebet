#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker-compose-prd.yaml"
HEALTH_URL="http://127.0.0.1:8004/"
HEALTH_RETRIES=18
HEALTH_SLEEP=10
BACKUP_DIR="${ROOT_DIR}/backups/db"
AUTO_ENSURE_TODAY_PREDICTIONS="${AUTO_ENSURE_TODAY_PREDICTIONS:-1}"

usage() {
  cat <<'EOF'
Usage: scripts/deploy_prd.sh [--compose-file FILE] [--health-url URL] [--backup-dir DIR]

Flow:
1) Ensure db service is running.
2) Create DB backup snapshot.
3) Deploy updated containers (up -d --build).
4) Run health check; on failure auto-restore DB from snapshot.
5) After successful health check, ensure today's predictions exist; if none,
   auto-trigger prediction generation for today.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose-file)
      COMPOSE_FILE="$2"
      shift 2
      ;;
    --health-url)
      HEALTH_URL="$2"
      shift 2
      ;;
    --backup-dir)
      BACKUP_DIR="$2"
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

get_today_prediction_count() {
  docker compose -f "${COMPOSE_FILE}" exec -T web python manage.py shell -c \
    "from django.utils import timezone; from apps.predictions.models import Prediction; print(Prediction.objects.filter(match__match_date__date=timezone.localdate(), match__status__in=['scheduled','timed']).count())" \
    | tr -d '\r' | tail -n1
}

ensure_today_predictions() {
  local count
  count="$(get_today_prediction_count)"
  echo "Today's prediction count: ${count}"

  if [[ "${count}" =~ ^[0-9]+$ ]] && [[ "${count}" -gt 0 ]]; then
    echo "Today's predictions already exist. No backfill needed."
    return 0
  fi

  echo "No predictions found for today. Triggering backfill..."
  docker compose -f "${COMPOSE_FILE}" exec -T web python manage.py fetch_and_predict --days 1

  count="$(get_today_prediction_count)"
  echo "Today's prediction count after backfill: ${count}"
  if [[ "${count}" =~ ^[0-9]+$ ]] && [[ "${count}" -gt 0 ]]; then
    echo "Prediction backfill succeeded."
    return 0
  fi

  echo "Backfill completed but still found zero predictions for today." >&2
  return 1
}

echo "Starting production deploy using ${COMPOSE_FILE}"
echo "Ensuring database service is running..."
docker compose -f "${COMPOSE_FILE}" up -d db

BACKUP_OUTPUT="$(bash "${ROOT_DIR}/scripts/backup_db.sh" --compose-file "${COMPOSE_FILE}" --backup-dir "${BACKUP_DIR}")"
BACKUP_FILE="$(echo "${BACKUP_OUTPUT}" | awk -F': ' '/Backup complete:/ {print $2}' | tail -n1)"

if [[ -z "${BACKUP_FILE}" || ! -f "${BACKUP_FILE}" ]]; then
  echo "Failed to detect backup file path from backup_db.sh output." >&2
  exit 1
fi

echo "Deploying application containers..."
docker compose -f "${COMPOSE_FILE}" up -d --build

echo "Running health checks against ${HEALTH_URL}..."
healthy=0
for i in $(seq 1 "${HEALTH_RETRIES}"); do
  if curl -fsS "${HEALTH_URL}" >/dev/null; then
    healthy=1
    break
  fi
  echo "Health check ${i}/${HEALTH_RETRIES} failed; retrying in ${HEALTH_SLEEP}s..."
  sleep "${HEALTH_SLEEP}"
done

if [[ "${healthy}" -eq 1 ]]; then
  if [[ "${AUTO_ENSURE_TODAY_PREDICTIONS}" == "1" ]]; then
    ensure_today_predictions
  else
    echo "Skipping prediction presence check (AUTO_ENSURE_TODAY_PREDICTIONS=${AUTO_ENSURE_TODAY_PREDICTIONS})."
  fi
  echo "Deploy successful. Health check passed."
  exit 0
fi

echo "Deploy health check failed. Restoring database from snapshot..."
bash "${ROOT_DIR}/scripts/restore_db.sh" --compose-file "${COMPOSE_FILE}" --input "${BACKUP_FILE}"
docker compose -f "${COMPOSE_FILE}" restart web worker beat

echo "Automatic rollback completed (DB restored). Please inspect application logs."
exit 1

