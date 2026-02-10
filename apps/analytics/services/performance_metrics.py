"""
Performance Metrics Service.

Tracks per-league, per-model prediction performance.
"""

import logging
from datetime import timedelta

from django.db.models import Count, Q, Avg
from django.utils import timezone

from apps.predictions.models import Prediction, ModelVersion
from apps.analytics.models import PredictionStats, AccuracyRecord
from apps.core.models import League

logger = logging.getLogger(__name__)


class PerformanceMetricsService:
    """
    Calculates and stores granular prediction performance metrics.
    """

    @classmethod
    def update_league_stats(cls):
        """
        Calculate and store per-league prediction stats.
        Called daily by Celery.
        """
        today = timezone.now().date()

        for league in League.objects.filter(is_active=True):
            cls._calculate_league_stats(league, today)

        # Also calculate global stats (league=None equivalent)
        cls._calculate_global_stats(today)

    @classmethod
    def _calculate_league_stats(cls, league: League, calculation_date):
        """Calculate stats for a single league."""
        base_qs = Prediction.objects.filter(
            match__league=league,
            is_correct__isnull=False,
        )

        total = base_qs.count()
        if total == 0:
            return

        correct = base_qs.filter(is_correct=True).count()

        # Outcome breakdown
        home_total = base_qs.filter(predicted_outcome='home_win').count()
        home_correct = base_qs.filter(predicted_outcome='home_win', is_correct=True).count()

        draw_total = base_qs.filter(predicted_outcome='draw').count()
        draw_correct = base_qs.filter(predicted_outcome='draw', is_correct=True).count()

        away_total = base_qs.filter(predicted_outcome='away_win').count()
        away_correct = base_qs.filter(predicted_outcome='away_win', is_correct=True).count()

        avg_confidence = base_qs.aggregate(Avg('confidence_score'))['confidence_score__avg'] or 0

        # High confidence accuracy (>70%)
        high_conf_qs = base_qs.filter(confidence_score__gte=70)
        high_conf_total = high_conf_qs.count()
        high_conf_correct = high_conf_qs.filter(is_correct=True).count()

        PredictionStats.objects.update_or_create(
            league=league,
            defaults={
                'total_predictions': total,
                'correct_predictions': correct,
                'accuracy_rate': round((correct / total) * 100, 2),
                'home_win_accuracy': round((home_correct / home_total * 100), 2) if home_total else 0,
                'draw_accuracy': round((draw_correct / draw_total * 100), 2) if draw_total else 0,
                'away_win_accuracy': round((away_correct / away_total * 100), 2) if away_total else 0,
                'avg_confidence': round(avg_confidence, 2),
                'high_confidence_accuracy': round(
                    (high_conf_correct / high_conf_total * 100), 2
                ) if high_conf_total else 0,
                'calculated_at': timezone.now(),
            }
        )

    @classmethod
    def _calculate_global_stats(cls, calculation_date):
        """Calculate overall platform stats."""
        base_qs = Prediction.objects.filter(is_correct__isnull=False)

        total = base_qs.count()
        if total == 0:
            return

        correct = base_qs.filter(is_correct=True).count()
        avg_conf = base_qs.aggregate(Avg('confidence_score'))['confidence_score__avg'] or 0

        # Store as a "global" record (league=None)
        PredictionStats.objects.update_or_create(
            league=None,
            defaults={
                'total_predictions': total,
                'correct_predictions': correct,
                'accuracy_rate': round((correct / total) * 100, 2),
                'avg_confidence': round(avg_conf, 2),
                'calculated_at': timezone.now(),
            }
        )

    @classmethod
    def get_model_comparison(cls) -> list[dict]:
        """
        Compare performance across model versions.
        """
        versions = ModelVersion.objects.order_by('-created_at')[:10]
        results = []

        for version in versions:
            preds = Prediction.objects.filter(
                model_version=version,
                is_correct__isnull=False,
            )
            total = preds.count()
            correct = preds.filter(is_correct=True).count()

            results.append({
                'version': version.name,
                'model_type': version.model_type,
                'is_active': version.is_active,
                'predictions': total,
                'accuracy': round((correct / total * 100), 2) if total else 0,
                'created_at': version.created_at,
            })

        return results

    @classmethod
    def confidence_calibration(cls) -> list[dict]:
        """
        Check if confidence scores are well-calibrated.
        Groups predictions by confidence bucket and measures actual accuracy.
        """
        buckets = [(40, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 100)]
        calibration = []

        for low, high in buckets:
            qs = Prediction.objects.filter(
                confidence_score__gte=low,
                confidence_score__lt=high,
                is_correct__isnull=False,
            )
            total = qs.count()
            correct = qs.filter(is_correct=True).count()

            calibration.append({
                'bucket': f'{low}-{high}%',
                'predicted_confidence': (low + high) / 2,
                'actual_accuracy': round((correct / total * 100), 2) if total else 0,
                'sample_count': total,
                'well_calibrated': abs(
                    ((low + high) / 2) - ((correct / total * 100) if total else 0)
                ) < 10 if total else None,
            })

        return calibration
