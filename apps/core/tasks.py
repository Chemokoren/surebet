"""
Celery Tasks.

Orchestrates daily workflows:
1.  Match Ingestion (04:00 UTC)
2.  Prediction Generation (05:00 UTC)
3.  Live Score Updates (Every 10 mins)
4.  Accuracy Updates (00:00 UTC)
5.  Subscription Maintenance (Daily)
"""

import logging
from celery import shared_task
from django.utils import timezone

from apps.core.services.data_ingestion import DataIngestionService
from apps.predictions.services.prediction_service import PredictionService
from apps.analytics.services.accuracy_tracker import AnalyticsService
from apps.payments.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)


# ── Data & Predictions ──────────────────────

@shared_task(name='fetch_daily_fixtures')
def fetch_daily_fixtures():
    """Fetch fixtures for today from external API."""
    logger.info("Starting daily fixture fetch")
    service = DataIngestionService()
    service.fetch_todays_fixtures()
    # Fetch tomorrow as well for early predictions
    service.fetch_fixtures_for_date(timezone.now().date() + timezone.timedelta(days=1))


@shared_task(name='generate_daily_predictions')
def generate_daily_predictions():
    """Generate ML predictions for today's matches."""
    logger.info("Starting daily prediction generation")
    PredictionService.generate_daily_predictions()


@shared_task(name='update_live_scores')
def update_live_scores():
    """Update scores for live matches and lock predictions."""
    # Run every 10-15 minutes
    try:
        service = DataIngestionService()
        stats = service.update_live_scores()
        logger.info(f"Live score update: {stats}")
    except Exception as e:
        logger.error(f"Live score task failed: {e}")


# ── Analytics & Revenue ─────────────────────

@shared_task(name='update_accuracy_stats')
def update_accuracy_stats():
    """Update public accuracy records."""
    logger.info("Starting accuracy stats update")
    AnalyticsService.update_accuracy_stats()
    AnalyticsService.generate_revenue_snapshot()


# ── Subscriptions & Users ───────────────────

@shared_task(name='check_subscription_expiries')
def check_subscription_expiries():
    """Check for expired subscriptions and transition states."""
    logger.info("Checking subscription expiries")
    count = SubscriptionService.check_and_handle_expirations()
    logger.info(f"Processed {count} subscription expiries")


@shared_task(name='cleanup_stale_matches')
def cleanup_stale_matches():
    """Remove or mark as cancelled matches that never happened."""
    DataIngestionService.remove_stale_unplayed_matches()
