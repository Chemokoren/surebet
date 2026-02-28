"""
Celery task definitions for FuturaPredict's automated pipeline.

Schedule (all UTC):
  00:05  fetch_and_predict_scheduled   – fetch next 6 days + generate predictions
  00:30  compute_accuracy_stats_task   – daily accuracy snapshot
  06:00  check_accuracy_drift_task     – drift monitor, triggers retrain if needed
  Every 20 min (07–23)  update_live_scores_task  – in-play / half-time updates
  23:30  resolve_finished_matches_task – end-of-day: resolve + ELO + maybe retrain
  Mon 03:00  weekly_full_retrain_task  – deep retrain on 3 seasons of history
"""

import logging
from datetime import date, timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# 1.  FETCH & PREDICT  (00:05 daily)
# ══════════════════════════════════════════════════════════════════════════════

@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def fetch_and_predict_scheduled(self):
    """
    Midnight pipeline:
      - Fetch fixtures from ESPN for today + next 5 days (6 days total).
      - Generate predictions for every newly ingested match.
    Retries up to 3 times with a 5-minute delay on transient failures.
    """
    from apps.core.services.data_ingestion import DataIngestionService
    from apps.predictions.services.prediction_service import PredictionService

    today = timezone.now().date()
    total_fetched = 0
    total_predicted = 0

    logger.info("[Task:fetch_and_predict] Starting 6-day fixture + prediction pipeline")

    service = DataIngestionService()

    for offset in range(6):
        target = today + timedelta(days=offset)
        try:
            ingest = service.fetch_fixtures_for_date(target)
            fetched = ingest['created'] + ingest['updated']
            total_fetched += fetched

            count = PredictionService.generate_daily_predictions(target_date=target)
            total_predicted += count

            logger.info(
                f"[Task:fetch_and_predict] {target}: "
                f"+{ingest['created']} fixtures, {count} predictions"
            )
        except Exception as exc:
            logger.error(f"[Task:fetch_and_predict] Error for {target}: {exc}")
            try:
                raise self.retry(exc=exc)
            except self.MaxRetriesExceededError:
                logger.critical(f"[Task:fetch_and_predict] Max retries exceeded for {target}")

    msg = (
        f"fetch_and_predict complete: "
        f"{total_fetched} fixtures, {total_predicted} predictions (6 days)"
    )
    logger.info(f"[Task:fetch_and_predict] {msg}")
    return msg


# ══════════════════════════════════════════════════════════════════════════════
# 2.  LIVE SCORE UPDATES  (every 20 min, 07:00–23:59)
# ══════════════════════════════════════════════════════════════════════════════

@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def update_live_scores_task(self):
    """
    Refresh in-play scores and statuses for today's matches.
    Locks predictions at kick-off and captures half-time / full-time scores.
    """
    from apps.core.services.data_ingestion import DataIngestionService

    logger.info("[Task:update_live_scores] Refreshing live scores...")

    try:
        service = DataIngestionService()
        result = service.update_live_scores()
        msg = (
            f"Live scores updated: {result['updated']} matches, "
            f"{result['locked']} newly locked"
        )
        logger.info(f"[Task:update_live_scores] {msg}")
        return msg
    except Exception as exc:
        logger.error(f"[Task:update_live_scores] Error: {exc}")
        raise self.retry(exc=exc)


# ══════════════════════════════════════════════════════════════════════════════
# 3.  END-OF-DAY RESOLUTION  (23:30 daily)
# ══════════════════════════════════════════════════════════════════════════════

@shared_task(bind=True, max_retries=2, default_retry_delay=600)
def resolve_finished_matches_task(self):
    """
    End-of-game-day pipeline:
      1. Fetch final scores from ESPN for yesterday + today.
      2. Resolve every Prediction (set is_correct).
      3. Update team ELO ratings.
      4. Write daily AccuracyRecord snapshots.
      5. Trigger incremental model retraining if threshold met.
    """
    from apps.core.services.results_resolution import ResultsResolutionService

    today     = timezone.now().date()
    yesterday = today - timedelta(days=1)

    logger.info("[Task:resolve_finished_matches] Starting end-of-day resolution")

    results = {}
    for target in [yesterday, today]:
        try:
            stats = ResultsResolutionService.resolve_date(target)
            results[str(target)] = stats
            logger.info(
                f"[Task:resolve_finished_matches] {target}: "
                f"resolved={stats['resolved']}, "
                f"correct={stats['correct']}, "
                f"elo_updated={stats['elo_updated']}, "
                f"retrain={stats['retrain_triggered']}"
            )
        except Exception as exc:
            logger.error(
                f"[Task:resolve_finished_matches] Error resolving {target}: {exc}"
            )
            try:
                raise self.retry(exc=exc)
            except self.MaxRetriesExceededError:
                pass

    return results


# ══════════════════════════════════════════════════════════════════════════════
# 4.  DAILY ACCURACY STATS  (00:30 daily)
# ══════════════════════════════════════════════════════════════════════════════

@shared_task
def compute_accuracy_stats_task():
    """
    Write AccuracyRecord snapshots for yesterday's resolved predictions.
    Runs just after midnight so yesterday's data is complete.
    """
    from apps.core.services.results_resolution import ResultsResolutionService

    yesterday = timezone.now().date() - timedelta(days=1)
    logger.info(f"[Task:compute_accuracy_stats] Snapshotting accuracy for {yesterday}")

    try:
        ResultsResolutionService._save_accuracy_snapshot(yesterday)
        return f"Accuracy snapshot written for {yesterday}"
    except Exception as exc:
        logger.error(f"[Task:compute_accuracy_stats] Error: {exc}")
        raise


# ══════════════════════════════════════════════════════════════════════════════
# 5.  DRIFT MONITOR  (06:00 daily)
# ══════════════════════════════════════════════════════════════════════════════

@shared_task
def check_accuracy_drift_task():
    """
    Compare recent prediction accuracy (last 7 days) against the all-time
    baseline.  If it dropped by > 5 %, trigger incremental model retraining.
    """
    from apps.core.services.results_resolution import ResultsResolutionService

    logger.info("[Task:check_accuracy_drift] Running drift check...")

    try:
        report = ResultsResolutionService.check_and_handle_drift()
        msg = (
            f"Drift check: current={report.get('current_accuracy', 'N/A')}, "
            f"baseline={report.get('baseline_accuracy', 'N/A')}, "
            f"drift_detected={report.get('drift_detected')}, "
            f"retrain_triggered={report.get('retrain_triggered')}"
        )
        logger.info(f"[Task:check_accuracy_drift] {msg}")
        return report
    except Exception as exc:
        logger.error(f"[Task:check_accuracy_drift] Error: {exc}")
        raise


# ══════════════════════════════════════════════════════════════════════════════
# 6.  WEEKLY FULL RETRAIN  (Monday 03:00)
# ══════════════════════════════════════════════════════════════════════════════

@shared_task(bind=True, max_retries=1, default_retry_delay=1800)
def weekly_full_retrain_task(self):
    """
    Full model retraining on up to 3 seasons of historical data.
    Trains XGBoost + Neural Network, registers a new ModelVersion if
    the ensemble accuracy improved.  Runs every Monday at 03:00 UTC.
    """
    from apps.core.services.results_resolution import ResultsResolutionService

    logger.info("[Task:weekly_full_retrain] Starting weekly full model retrain")

    try:
        result = ResultsResolutionService.run_full_weekly_retrain()
        msg = (
            f"Weekly retrain: success={result['success']}, "
            + (f"model={result.get('model')}, acc={result.get('accuracy')}"
               if result['success'] else f"reason={result.get('reason')}")
        )
        logger.info(f"[Task:weekly_full_retrain] {msg}")
        return result
    except Exception as exc:
        logger.error(f"[Task:weekly_full_retrain] Error: {exc}")
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.critical("[Task:weekly_full_retrain] Max retries exceeded")
            raise


# ══════════════════════════════════════════════════════════════════════════════
# 7.  LEGACY COMPATIBILITY (kept to avoid breaking existing beat entries)
# ══════════════════════════════════════════════════════════════════════════════

@shared_task
def generate_daily_predictions_task():
    """Deprecated alias → delegates to fetch_and_predict_scheduled."""
    logger.warning(
        "[Task:generate_daily_predictions_task] "
        "This task is deprecated. Use fetch_and_predict_scheduled instead."
    )
    return fetch_and_predict_scheduled.apply_async()


@shared_task
def verify_predictions_availability_task():
    """
    Verify predictions exist for today; generate if missing.
    Kept for backward compat with existing Celery Beat database entries.
    """
    from apps.predictions.models import Prediction
    from apps.core.models import Match
    from apps.predictions.services.prediction_service import PredictionService

    target_date = timezone.now().date()
    logger.info(f"[Task:verify] Checking prediction availability for {target_date}")

    matches_count = Match.objects.filter(match_date__date=target_date).count()
    if matches_count == 0:
        return "No matches scheduled today."

    pred_count = Prediction.objects.filter(match__match_date__date=target_date).count()

    if pred_count == 0:
        logger.warning(f"[Task:verify] No predictions for {target_date} — emergency generate")
        count = PredictionService.generate_daily_predictions(target_date=target_date)
        return f"Emergency: generated {count} predictions"

    return f"OK: {pred_count} predictions for {target_date}"


# ══════════════════════════════════════════════════════════════════════════════
# 8.  EXTERNAL INTELLIGENCE SCRAPING  (every 6 hours)
# ══════════════════════════════════════════════════════════════════════════════

@shared_task(
    bind=True,
    name='predictions.scrape_external_predictions',
    max_retries=2,
    default_retry_delay=300,
)
def scrape_external_predictions(self):
    """
    Scrape predictions from all enabled external sources.
    Runs every 6 hours to collect fresh predictions.
    """
    try:
        from apps.predictions.services.learning_engine import LearningEngine
        stats = LearningEngine.scrape_all_sources()
        logger.info(f"[Task:scrape_ext] {stats}")
        return (
            f"Scraped {stats['sources_scraped']} sources, "
            f"stored {stats['predictions_stored']} predictions, "
            f"{len(stats['errors'])} errors"
        )
    except Exception as exc:
        logger.error(f"[Task:scrape_ext] Failed: {exc}")
        raise self.retry(exc=exc)


# ══════════════════════════════════════════════════════════════════════════════
# 9.  SOURCE EVALUATION  (daily at 04:00)
# ══════════════════════════════════════════════════════════════════════════════

@shared_task(name='predictions.evaluate_sources')
def evaluate_sources_task():
    """
    Daily evaluation of prediction sources:
      1. Resolve external predictions against actual outcomes.
      2. Record daily accuracy snapshots.
      3. Check learning-phase sources for auto-promotion.
      4. Run quality audit (classify, flag, disable, discover).
    """
    from apps.predictions.services.learning_engine import LearningEngine
    from apps.predictions.scrapers.quality_manager import SourceQualityManager

    # Step 1: resolve
    resolved = LearningEngine.resolve_external_predictions()
    logger.info(f"[Task:eval_sources] Resolved {resolved} external predictions")

    # Step 2: daily accuracy record
    from datetime import timedelta
    yesterday = timezone.now().date() - timedelta(days=1)
    LearningEngine.record_daily_accuracy(yesterday)

    # Step 3: auto-promote / retire
    results = LearningEngine.evaluate_learning_sources()
    logger.info(f"[Task:eval_sources] Evaluation: {results}")

    # Step 4: quality audit (classify, flag, disable, discover)
    audit_results = SourceQualityManager.run_full_audit()
    logger.info(f"[Task:eval_sources] Quality audit: {audit_results}")

    return (
        f"Resolved={resolved}, Promoted={len(results['promoted'])}, "
        f"Remaining={len(results['remaining'])}, Rejected={len(results['rejected'])}, "
        f"Flagged={len(audit_results.get('flagged', []))}, "
        f"Disabled={len(audit_results.get('disabled', []))}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 10.  RESOLVE EXTERNAL PREDICTIONS  (after match results)
# ══════════════════════════════════════════════════════════════════════════════

@shared_task(name='predictions.resolve_external_predictions')
def resolve_external_predictions_task():
    """
    Resolve pending external predictions against actual match outcomes.
    Can be chained after resolve_finished_matches_task.
    """
    from apps.predictions.services.learning_engine import LearningEngine
    resolved = LearningEngine.resolve_external_predictions()
    return f"Resolved {resolved} external predictions"

