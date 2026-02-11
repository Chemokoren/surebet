from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from apps.core.services.data_ingestion import DataIngestionService
from .services.prediction_service import PredictionService
from .models import Prediction
from apps.core.models import Match
import logging

logger = logging.getLogger(__name__)

@shared_task
def generate_daily_predictions_task():
    """
    Generate predictions for the next day.
    Runs daily at 11:00 PM (23:00).
    """
    logger.info("Starting scheduled daily prediction pipeline...")
    
    # Target: Tomorrow matches
    tomorrow = timezone.now().date() + timedelta(days=1)
    
    # 1. Update Ingestion (Fetch fixtures for tomorrow)
    try:
        service = DataIngestionService()
        logger.info(f"Fetching fixtures for {tomorrow}...")
        service.fetch_fixtures_for_date(tomorrow)
    except Exception as e:
        logger.error(f"Data ingestion failed for {tomorrow}: {e}")
        # Proceed in case fixtures were ingested earlier

    # 2. Generate Predictions
    try:
        logger.info(f"Generating predictions for {tomorrow}...")
        count = PredictionService.generate_daily_predictions(target_date=tomorrow)
        logger.info(f"Successfully generated {count} predictions for {tomorrow}")
        return f"Generated {count} predictions for {tomorrow}"
    except Exception as e:
        logger.error(f"Prediction generation failed: {e}")
        raise

@shared_task
def verify_predictions_availability_task():
    """
    Verify predictions exist for the current day.
    Runs daily at 12:00 AM (00:00).
    Ensures that content is available for subscribers.
    """
    target_date = timezone.now().date()
    logger.info(f"Verifying prediction availability for {target_date}...")
    
    # Check if ANY matches exist for today first
    matches_count = Match.objects.filter(match_date__date=target_date).count()
    if matches_count == 0:
        logger.info(f"No matches scheduled for {target_date}. No predictions required.")
        return "No matches scheduled."

    # Check predictions
    pred_count = Prediction.objects.filter(match__match_date__date=target_date).count()
    
    if pred_count == 0:
        logger.warning(f"No predictions found for {target_date} despite {matches_count} scheduled matches! Triggering emergency generation.")
        try:
            # Emergency generation
            generated = PredictionService.generate_daily_predictions(target_date=target_date)
            if generated > 0:
                logger.info(f"Emergency generation successful: {generated} predictions created.")
                return f"Emergency: Generated {generated} predictions"
            else:
                logger.error("Emergency generation resulted in 0 predictions. Review logs for DataIngestion/ML errors.")
                return "Emergency: Failed (0 predictions)"
        except Exception as e:
            logger.critical(f"Critical failure during emergency generation: {e}")
            raise
    
    logger.info(f"Verification passed: {pred_count} predictions available for {target_date}.")
    return f"Verified {pred_count} predictions"
