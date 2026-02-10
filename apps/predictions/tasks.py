"""
Celery tasks for the predictions app.

Scheduled via django-celery-beat:
    - generate_daily_predictions: every day at 06:00 UTC
    - retrain_models: every Sunday at 02:00 UTC
    - check_model_drift: every day at 03:00 UTC
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='predictions.generate_daily')
def generate_daily_predictions():
    """
    Generate predictions for all scheduled matches today.
    Should run after data ingestion has fetched fixtures.
    """
    from apps.predictions.services.prediction_service import PredictionService

    count = PredictionService.generate_daily_predictions()
    logger.info(f"Daily predictions generated: {count}")
    return {'predictions_created': count}


@shared_task(name='predictions.retrain_models')
def retrain_models(full: bool = False):
    """
    Retrain ML models.

    Args:
        full: If True, retrain on full historical data.
              If False, incremental (last 30 days).
    """
    from apps.predictions.ml.training_pipeline import TrainingPipeline
    from apps.predictions.services.model_registry import ModelRegistry

    if full:
        version = TrainingPipeline.run_full_training(seasons=3)
    else:
        version = TrainingPipeline.run_incremental_update(days=30)

    if version:
        ModelRegistry.invalidate_cache()
        logger.info(f"New model version activated: {version.name}")
    else:
        logger.info("No model improvement, keeping current version")

    return {'new_version': version.name if version else None}


@shared_task(name='predictions.check_drift')
def check_model_drift():
    """
    Check if prediction accuracy has degraded significantly.
    Sends alert if drift detected (threshold: 5% degradation).
    """
    from apps.predictions.ml.training_pipeline import TrainingPipeline

    result = TrainingPipeline.check_accuracy_drift(threshold=0.05)

    if result.get('drift_detected'):
        logger.warning(f"MODEL DRIFT DETECTED: {result}")
        # Trigger automatic retraining
        retrain_models.delay(full=False)
    else:
        logger.info(f"Drift check OK: {result}")

    return result


@shared_task(name='predictions.warm_feature_cache')
def warm_feature_cache():
    """
    Pre-compute and cache features for today's scheduled matches.
    Should run before daily prediction generation.
    """
    from django.utils import timezone
    from apps.core.models import Match
    from apps.predictions.ml.feature_store import FeatureStore

    today = timezone.now().date()
    matches = Match.objects.filter(
        match_date__date=today,
        status='scheduled',
    ).select_related('home_team', 'away_team', 'league')

    count = FeatureStore.warm_cache(matches)
    return {'features_cached': count}


@shared_task(name='predictions.resolve_predictions')
def resolve_predictions():
    """
    Resolve pending predictions based on finished match results.
    Should run after match results are ingested.
    """
    from apps.analytics.services.accuracy_tracker import AnalyticsService

    AnalyticsService._resolve_predictions()
    logger.info("Prediction resolution complete")
    return {'status': 'ok'}
