"""
Management command: backfill_explanations

Generates and persists PredictionExplanation records for every Prediction
that currently has none. Safe to re-run — skips predictions that already
have explanations.

Usage:
    python manage.py backfill_explanations
    python manage.py backfill_explanations --limit 50   # process at most N predictions
    python manage.py backfill_explanations --dry-run    # preview without saving
"""

import logging

from django.core.management.base import BaseCommand

from apps.predictions.models import Prediction, PredictionExplanation
from apps.predictions.services.explainer import ExplainerService
from apps.core.services.feature_engineering import FeatureEngineeringService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Backfill Key Prediction Factor explanations for all predictions that are missing them."

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Maximum number of predictions to process (default: all).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print what would be done without writing to the database.',
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Delete and regenerate explanations even for predictions that already have them.',
        )

    def handle(self, *args, **options):
        limit     = options['limit']
        dry_run   = options['dry_run']
        overwrite = options['overwrite']

        if overwrite:
            qs = (
                Prediction.objects
                .select_related('match', 'match__home_team', 'match__away_team', 'match__league')
            )
        else:
            # Only predictions without any explanation records
            qs = (
                Prediction.objects
                .filter(explanations__isnull=True)
                .select_related('match', 'match__home_team', 'match__away_team', 'match__league')
                .distinct()
            )

        if limit:
            qs = qs[:limit]

        total = qs.count()
        mode = "overwrite all" if overwrite else "missing only"
        self.stdout.write(
            self.style.NOTICE(f"Found {total} prediction(s) to process (mode: {mode}).")
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry-run mode — no data will be written."))

        ok = 0
        failed = 0

        for prediction in qs.iterator():
            label = (
                f"{prediction.match.home_team.name} vs "
                f"{prediction.match.away_team.name} "
                f"({prediction.match.match_date.date() if prediction.match.match_date else 'unknown date'})"
            )
            try:
                # If overwriting, delete existing explanations first
                if overwrite and not dry_run:
                    prediction.explanations.all().delete()

                # Always regenerate fresh features from DB for best accuracy.
                # The stored snapshot may be partial or stale.
                features = FeatureEngineeringService.generate_features(prediction.match)
                if not features:
                    features = prediction.feature_snapshot or {}

                prediction_result = {
                    'predicted_outcome': prediction.predicted_outcome,
                    'confidence': prediction.confidence_score,
                    'probabilities': {
                        'home': prediction.home_win_prob,
                        'draw': prediction.draw_prob,
                        'away': prediction.away_win_prob,
                    },
                }

                explanations_data = ExplainerService.explain(
                    features=features,
                    prediction_result=prediction_result,
                    xgboost_model=None,
                    top_n=8,
                    home_team_name=prediction.match.home_team.name,
                    away_team_name=prediction.match.away_team.name,
                )

                if not dry_run:
                    rows = [
                        PredictionExplanation(
                            prediction=prediction,
                            factor_name=e['factor_name'],
                            factor_value=e['factor_value'],
                            impact_score=e['impact_score'],
                            impact_direction=e['impact_direction'],
                            display_order=e['display_order'],
                        )
                        for e in explanations_data
                    ]
                    PredictionExplanation.objects.bulk_create(rows, ignore_conflicts=True)

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓  {label}  →  {len(explanations_data)} factor(s)"
                        + (" [dry-run]" if dry_run else "")
                    )
                )
                ok += 1

            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(f"  ✗  {label}  →  {exc}")
                )
                logger.exception(f"backfill_explanations failed for prediction {prediction.pk}: {exc}")
                failed += 1

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(f"Done. {ok} succeeded, {failed} failed.")
        )

