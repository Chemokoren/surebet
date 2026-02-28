#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# FuturaPredict – Container Entrypoint
#
# Selects the service to run based on the SERVICE environment variable
# (default: web).  All services share this same image.
#
# Usage inside docker-compose.yaml:
#   command: web     → Gunicorn HTTP server
#   command: worker  → Celery worker
#   command: beat    → Celery Beat scheduler (run exactly ONE instance)
#   command: setup   → Run migrations + seed_data + initial fetch (run once)
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SERVICE="${1:-${SERVICE:-web}}"

# ── Wait for Postgres to accept connections ───────────────────────────────────
wait_for_postgres() {
    echo "⏳ Waiting for PostgreSQL at ${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}..."
    for i in $(seq 1 30); do
        python -c "
import sys, os
import psycopg2
try:
    psycopg2.connect(
        dbname=os.getenv('POSTGRES_DB','futurapredict'),
        user=os.getenv('POSTGRES_USER','futurapredict'),
        password=os.getenv('POSTGRES_PASSWORD','futurapredict_pass'),
        host=os.getenv('POSTGRES_HOST','db'),
        port=os.getenv('POSTGRES_PORT','5432'),
        connect_timeout=3,
    )
    sys.exit(0)
except Exception:
    sys.exit(1)
" && echo "✅ PostgreSQL is up." && return 0
        echo "   attempt ${i}/30 – retrying in 2s..."
        sleep 2
    done
    echo "❌ PostgreSQL did not become available in time." >&2
    exit 1
}

# ── Ensure migrations are applied (important for beat/worker startup) ─────────
ensure_migrations() {
    echo "🔄 Ensuring database migrations are applied..."
    for i in $(seq 1 10); do
        if python manage.py migrate --noinput; then
            echo "✅ Migrations ready."
            return 0
        fi
        echo "   migrate attempt ${i}/10 failed; retrying in 3s..."
        sleep 3
    done
    echo "❌ Could not apply migrations after multiple attempts." >&2
    exit 1
}

# ── Sync all leagues (domestic + continental) into the database ────────────────
sync_leagues() {
    echo "🏟️  Syncing leagues (domestic + continental)..."
    python manage.py shell -c "
from apps.core.services.data_ingestion import DataIngestionService
svc = DataIngestionService()
result = svc.sync_teams_and_leagues()
print(f'  leagues={result[\"leagues\"]}, teams={result[\"teams\"]}, errors={len(result.get(\"errors\", []))}')
" || echo "⚠️  League sync encountered errors (non-fatal)."
    echo "✅ Leagues synced."
}

# ── Fetch fixtures + generate predictions for upcoming days ───────────────────
ensure_predictions() {
    if [[ "${AUTO_ENSURE_TODAY_PREDICTIONS_ON_BOOT:-1}" != "1" ]]; then
        echo "⏭️  Skipping boot-time prediction check."
        return 0
    fi

    FETCH_DAYS="${BOOT_FETCH_DAYS:-6}"

    echo "🔎 Checking predictions for upcoming ${FETCH_DAYS} days..."
    count="$(python manage.py shell -c "from django.utils import timezone; from apps.predictions.models import Prediction; print(Prediction.objects.filter(match__match_date__date__gte=timezone.localdate(), match__status__in=['scheduled','timed']).count())" | tail -n1 | tr -d '\r')"
    if [[ "${count}" =~ ^[0-9]+$ ]] && [[ "${count}" -gt 0 ]]; then
        echo "✅ Upcoming predictions already available (${count})."
        return 0
    fi

    echo "⚡ No upcoming predictions found; fetching fixtures + generating..."
    python manage.py fetch_and_predict --days "${FETCH_DAYS}" || true
    echo "✅ Fixture fetch + prediction generation complete."
}

# ── Service dispatch ──────────────────────────────────────────────────────────
case "$SERVICE" in

  # ── HTTP / Gunicorn ─────────────────────────────────────────────────────────
  web)
    wait_for_postgres
    ensure_migrations
    sync_leagues
    echo "🧠 Seeding intelligence sources..."
    python manage.py seed_prediction_sources --learning-days 90 || true
    ensure_predictions
    echo "📦 Collecting static files..."
    python manage.py collectstatic --noinput --clear 2>/dev/null || true
    echo "🚀 Starting Gunicorn on 0.0.0.0:${PORT:-8000}..."
    exec gunicorn futurapredict.wsgi:application \
        --bind "0.0.0.0:${PORT:-8000}" \
        --workers "${WEB_WORKERS:-3}" \
        --threads 2 \
        --timeout 120 \
        --keep-alive 5 \
        --access-logfile - \
        --error-logfile -
    ;;

  # ── Celery Worker ────────────────────────────────────────────────────────────
  worker)
    wait_for_postgres
    ensure_migrations
    echo "⚙️  Starting Celery worker (concurrency=${WORKER_CONCURRENCY:-4})..."
    exec celery -A config worker \
        --loglevel=info \
        --concurrency="${WORKER_CONCURRENCY:-4}" \
        --max-tasks-per-child=200 \
        --events
    ;;

  # ── Celery Beat (Scheduler) ──────────────────────────────────────────────────
  beat)
    wait_for_postgres
    ensure_migrations
    echo "🗓️  Starting Celery Beat scheduler..."
    exec celery -A config beat \
        --loglevel=info \
        --scheduler django_celery_beat.schedulers:DatabaseScheduler
    ;;

  # ── One-time Setup ───────────────────────────────────────────────────────────
  setup)
    wait_for_postgres
    ensure_migrations
    sync_leagues

    echo "🌱 Seeding initial data (leagues, plans, pricing)..."
    python manage.py seed_data

    echo "📅 Fetching fixtures + generating predictions for the next 6 days..."
    python manage.py fetch_and_predict --days 6

    echo "✅ Setup complete."
    ;;

  # ── Bootstrap for compose startup orchestration ──────────────────────────────
  bootstrap)
    wait_for_postgres
    ensure_migrations
    sync_leagues
    ensure_predictions
    echo "✅ Bootstrap complete."
    ;;

  # ── Django shell / management commands ───────────────────────────────────────
  manage)
    wait_for_postgres
    exec python manage.py "${@:2}"
    ;;

  *)
    echo "❌ Unknown SERVICE: '$SERVICE'" >&2
    echo "   Valid options: web | worker | beat | setup | manage" >&2
    exit 1
    ;;
esac


