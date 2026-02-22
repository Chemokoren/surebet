"""
Results Resolution Service.

Runs after each game-day to:
  1. Fetch final scores from ESPN (free API)
  2. Mark matches as 'finished' with correct scores
  3. Resolve every Prediction (set is_correct = True/False)
  4. Update team ELO ratings (online learning signal)
  5. Write an AccuracyRecord snapshot per league + overall
  6. Decide whether to trigger incremental model retraining
     (triggered when ≥ MIN_NEW_SAMPLES resolved since last training
      OR when accuracy drift > DRIFT_THRESHOLD)

This is the key data-collection loop that feeds the continuous
supervised-learning pipeline.
"""

import logging
from datetime import date, timedelta
from typing import Optional

from django.db.models import Q
from django.utils import timezone

from apps.core.models import Match, League
from apps.core.services.data_ingestion import DataIngestionService
from apps.core.services.feature_engineering import FeatureEngineeringService
from apps.predictions.models import Prediction

logger = logging.getLogger(__name__)

# ── Thresholds ──────────────────────────────────────────────────────────────
MIN_NEW_SAMPLES_FOR_RETRAIN = 100   # resolved predictions needed to trigger a retrain
DRIFT_THRESHOLD = 0.05              # 5 % accuracy drop triggers emergency retrain
MIN_TOTAL_RESOLVED = 200            # need at least this many to train at all


class ResultsResolutionService:
    """
    End-of-day orchestrator: fetch results → resolve predictions → update ELO
    → snapshot accuracy → decide whether to retrain ML models.
    """

    # ── Public API ──────────────────────────────────────────────────────────

    @classmethod
    def resolve_date(cls, target_date: date) -> dict:
        """
        Full resolution pipeline for a single game-day.

        Returns a stats dict:
            resolved    : int – predictions resolved (is_correct set)
            correct     : int – correct predictions
            incorrect   : int – wrong predictions
            elo_updated : int – ELO ratings updated
            retrain_triggered : bool
            errors      : list[str]
        """
        stats = {
            'date': str(target_date),
            'resolved': 0,
            'correct': 0,
            'incorrect': 0,
            'elo_updated': 0,
            'retrain_triggered': False,
            'errors': [],
        }

        logger.info(f"[ResultsResolution] Starting resolution for {target_date}")

        # ── Step 1: Refresh scores from ESPN ─────────────────────────────
        try:
            svc = DataIngestionService()
            ingest = svc.fetch_fixtures_for_date(target_date)
            logger.info(
                f"[ResultsResolution] Score refresh: "
                f"+{ingest['created']} new, ~{ingest['updated']} updated"
            )
        except Exception as exc:
            msg = f"Score refresh failed: {exc}"
            logger.error(f"[ResultsResolution] {msg}")
            stats['errors'].append(msg)

        # ── Step 2: Resolve predictions for finished matches ─────────────
        finished = Match.objects.filter(
            match_date__date=target_date,
            status='finished',
            home_score__isnull=False,
        ).select_related(
            'home_team', 'away_team', 'league', 'prediction'
        )

        for match in finished:
            try:
                actual = match.actual_outcome
                if actual is None:
                    continue

                # Resolve prediction (immutable once set)
                pred: Optional[Prediction] = getattr(match, 'prediction', None)
                if pred and pred.is_correct is None:
                    pred.resolve(actual)
                    if pred.is_correct:
                        stats['correct'] += 1
                    else:
                        stats['incorrect'] += 1
                    stats['resolved'] += 1

                # Update ELO ratings
                FeatureEngineeringService.update_elo(match)
                stats['elo_updated'] += 1

            except Exception as exc:
                msg = f"Error resolving match {match.id}: {exc}"
                logger.error(f"[ResultsResolution] {msg}")
                stats['errors'].append(msg)

        logger.info(
            f"[ResultsResolution] Resolved {stats['resolved']} predictions "
            f"({stats['correct']} correct / {stats['incorrect']} incorrect)"
        )

        # ── Step 3: Accuracy snapshot ─────────────────────────────────────
        if stats['resolved'] > 0:
            try:
                cls._save_accuracy_snapshot(target_date)
            except Exception as exc:
                logger.error(f"[ResultsResolution] Accuracy snapshot failed: {exc}")

        # ── Step 4: Decide whether to retrain ─────────────────────────────
        try:
            should, reason = cls._should_retrain()
            if should:
                logger.info(f"[ResultsResolution] Triggering model retrain: {reason}")
                stats['retrain_triggered'] = True
                cls._trigger_retrain()
        except Exception as exc:
            logger.error(f"[ResultsResolution] Retrain decision failed: {exc}")

        return stats

    @classmethod
    def resolve_yesterday(cls) -> dict:
        """Convenience wrapper for yesterday's game-day."""
        yesterday = date.today() - timedelta(days=1)
        return cls.resolve_date(yesterday)

    @classmethod
    def resolve_today(cls) -> dict:
        """Resolve any matches that already finished today (for intraday calls)."""
        return cls.resolve_date(date.today())

    # ── Accuracy Snapshots ──────────────────────────────────────────────────

    @classmethod
    def _save_accuracy_snapshot(cls, snapshot_date: date) -> None:
        """
        Write AccuracyRecord rows for every active league + an overall row.
        Uses only predictions resolved on or before snapshot_date.
        """
        from apps.analytics.models import AccuracyRecord
        from apps.predictions.models import ModelVersion

        active_model = ModelVersion.objects.filter(is_active=True).first()
        model_name = active_model.name if active_model else 'unknown'

        leagues = list(League.objects.filter(is_active=True)) + [None]  # None = overall

        for league in leagues:
            preds = Prediction.objects.filter(
                is_correct__isnull=False,
                match__match_date__date=snapshot_date,
            )
            if league:
                preds = preds.filter(match__league=league)

            total = preds.count()
            if total == 0:
                continue

            correct   = preds.filter(is_correct=True).count()
            incorrect = preds.filter(is_correct=False).count()
            pending_qs = Prediction.objects.filter(
                match__match_date__date=snapshot_date,
                is_correct__isnull=True,
            )
            if league:
                pending_qs = pending_qs.filter(match__league=league)
            pending = pending_qs.count()

            home_win_acc = cls._outcome_accuracy(preds, 'home_win')
            draw_acc     = cls._outcome_accuracy(preds, 'draw')
            away_win_acc = cls._outcome_accuracy(preds, 'away_win')

            avg_conf = (
                sum(p.confidence_score for p in preds) / total if total else 0
            )

            # Build lookup kwargs (league can be None = overall)
            lookup = {
                'period':       'daily',
                'period_start': snapshot_date,
                'period_end':   snapshot_date,
            }
            if league is not None:
                lookup['league'] = league
            else:
                lookup['league__isnull'] = True

            AccuracyRecord.objects.update_or_create(
                **lookup,
                defaults={
                    'league':                league,
                    'total_predictions':     total,
                    'correct_predictions':   correct,
                    'incorrect_predictions': incorrect,
                    'pending_predictions':   pending,
                    'accuracy_rate':   round(correct / total * 100, 2) if total else 0,
                    'home_win_accuracy': home_win_acc,
                    'draw_accuracy':     draw_acc,
                    'away_win_accuracy': away_win_acc,
                    'model_version':   model_name,
                    'avg_confidence':  round(avg_conf, 2),
                },
            )

        logger.info(f"[ResultsResolution] Accuracy snapshots written for {snapshot_date}")

    @staticmethod
    def _outcome_accuracy(preds, outcome: str) -> float:
        subset = preds.filter(predicted_outcome=outcome)
        total = subset.count()
        if not total:
            return 0.0
        correct = subset.filter(is_correct=True).count()
        return round(correct / total * 100, 2)

    # ── Retraining Logic ────────────────────────────────────────────────────

    @classmethod
    def _should_retrain(cls) -> tuple[bool, str]:
        """
        Decide whether to trigger incremental model retraining.

        Triggers when:
          A) Enough new resolved predictions since last training run.
          B) Accuracy drift > DRIFT_THRESHOLD in the last 7 days.

        Returns (bool, reason_str).
        """
        from apps.predictions.models import ModelVersion

        total_resolved = Prediction.objects.filter(is_correct__isnull=False).count()
        if total_resolved < MIN_TOTAL_RESOLVED:
            return False, f"Not enough data ({total_resolved} < {MIN_TOTAL_RESOLVED})"

        # Condition A: new samples since last training
        active_model = ModelVersion.objects.filter(is_active=True).first()
        if active_model and active_model.trained_at:
            new_since = Prediction.objects.filter(
                is_correct__isnull=False,
                resolved_at__gt=active_model.trained_at,
            ).count()
            if new_since >= MIN_NEW_SAMPLES_FOR_RETRAIN:
                return True, f"{new_since} new resolved predictions since last training"

        # Condition B: accuracy drift
        from apps.predictions.ml.training_pipeline import TrainingPipeline
        drift = TrainingPipeline.check_accuracy_drift(threshold=DRIFT_THRESHOLD)
        if drift.get('drift_detected'):
            return True, f"Drift detected: {drift.get('degradation', 0):.1%}"

        return False, "No trigger conditions met"

    @staticmethod
    def _trigger_retrain() -> None:
        """
        Run incremental model retraining using the last 90 days of data.
        Runs inline (for Celery tasks) — wrapped in try/except by callers.
        """
        from apps.predictions.ml.training_pipeline import TrainingPipeline
        result = TrainingPipeline.run_incremental_update(days=90)
        if result:
            logger.info(
                f"[ResultsResolution] Model retrained → {result.name} "
                f"(accuracy: {result.metrics.get('accuracy', 'N/A')})"
            )
        else:
            logger.info("[ResultsResolution] Incremental retrain ran but no improvement found.")

    # ── Drift Monitoring ────────────────────────────────────────────────────

    @classmethod
    def check_and_handle_drift(cls) -> dict:
        """
        Daily drift check. If drift > threshold, trigger retraining.
        Returns the drift report dict.
        """
        from apps.predictions.ml.training_pipeline import TrainingPipeline
        report = TrainingPipeline.check_accuracy_drift(threshold=DRIFT_THRESHOLD)

        if report.get('drift_detected'):
            logger.warning(f"[ResultsResolution] DRIFT DETECTED: {report}")
            try:
                cls._trigger_retrain()
                report['retrain_triggered'] = True
            except Exception as exc:
                logger.error(f"[ResultsResolution] Emergency retrain failed: {exc}")
                report['retrain_triggered'] = False
        else:
            report['retrain_triggered'] = False

        return report

    # ── Full weekly retraining ───────────────────────────────────────────────

    @staticmethod
    def run_full_weekly_retrain() -> dict:
        """
        Full retraining on the last 3 seasons of historical data.
        Called every week (Monday night) by Celery Beat.
        """
        from apps.predictions.ml.training_pipeline import TrainingPipeline
        logger.info("[ResultsResolution] Starting weekly full retrain...")

        model_version = TrainingPipeline.run_full_training(seasons=3)

        if model_version:
            logger.info(
                f"[ResultsResolution] Weekly retrain complete → {model_version.name} "
                f"accuracy: {model_version.metrics.get('accuracy', 'N/A')}"
            )
            return {
                'success': True,
                'model': model_version.name,
                'accuracy': model_version.metrics.get('accuracy'),
            }

        logger.info("[ResultsResolution] Weekly retrain: no improvement, keeping current model.")
        return {'success': False, 'reason': 'No improvement or insufficient data'}

