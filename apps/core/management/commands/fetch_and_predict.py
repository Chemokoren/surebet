"""
fetch_and_predict – Combined fixture ingestion + prediction generation.

Fetches today's (and optionally upcoming days') fixtures from ESPN / football-data.org,
then generates ML predictions for all newly ingested matches.

Usage examples:
    # Today only
    python manage.py fetch_and_predict

    # Today + next 5 days (ideal for monthly subscribers)
    python manage.py fetch_and_predict --days 5

    # Specific date
    python manage.py fetch_and_predict --date 2026-02-25

    # Only ingest, skip prediction generation
    python manage.py fetch_and_predict --ingest-only

    # Fix league priorities without fetching
    python manage.py fetch_and_predict --fix-leagues
"""

import logging
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.services.data_ingestion import DataIngestionService
from apps.predictions.services.prediction_service import PredictionService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Fetch fixtures from ESPN/football-data.org and generate predictions'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days', type=int, default=1,
            help='Number of days ahead to fetch + predict (default: 1 = today only)',
        )
        parser.add_argument(
            '--date', type=str, default=None,
            help='Specific date to fetch (YYYY-MM-DD). Overrides --days.',
        )
        parser.add_argument(
            '--ingest-only', action='store_true',
            help='Only fetch fixtures, skip prediction generation',
        )
        parser.add_argument(
            '--predict-only', action='store_true',
            help='Only generate predictions for already-ingested matches',
        )
        parser.add_argument(
            '--fix-leagues', action='store_true',
            help='Fix league priorities (EPL→1, La Liga→2, Serie A→3, BL1→4, L1→5) and exit',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='Re-generate predictions even if they already exist',
        )

    def handle(self, *args, **options):
        service = DataIngestionService()

        # ── Fix league priorities only ───────────────────────────────────
        if options['fix_leagues']:
            self.stdout.write('Fixing league priorities ...')
            result = service.sync_teams_and_leagues()
            self.stdout.write(self.style.SUCCESS(
                f"Done. Leagues synced: {result['leagues']}, teams: {result['teams']}"
            ))
            return

        # ── Determine target dates ───────────────────────────────────────
        if options['date']:
            try:
                from datetime import datetime
                target = datetime.strptime(options['date'], '%Y-%m-%d').date()
                dates = [target]
            except ValueError:
                self.stderr.write(self.style.ERROR(
                    f"Invalid date format '{options['date']}'. Use YYYY-MM-DD."
                ))
                return
        else:
            today = date.today()
            days = max(1, options['days'])
            dates = [today + timedelta(days=d) for d in range(days)]

        self.stdout.write(
            f"Processing {len(dates)} day(s): "
            f"{dates[0]} → {dates[-1]}"
        )

        total_fetched = 0
        total_predicted = 0

        for target_date in dates:
            self.stdout.write(f"\n── {target_date} ──────────────────────────────")

            # ── 1. Ingest fixtures ───────────────────────────────────────
            if not options['predict_only']:
                self.stdout.write(f"  Fetching fixtures from ESPN ...")
                ingest_result = service.fetch_fixtures_for_date(target_date)
                fetched = ingest_result['created'] + ingest_result['updated']
                total_fetched += fetched
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ Ingested: {ingest_result['created']} new, "
                        f"{ingest_result['updated']} updated"
                    )
                )
                if ingest_result.get('errors'):
                    for err in ingest_result['errors']:
                        self.stdout.write(self.style.WARNING(f"  ⚠ {err}"))

            # ── 2. Generate predictions ──────────────────────────────────
            if not options['ingest_only']:
                from apps.predictions.models import Prediction
                from apps.core.models import Match

                # When --force, delete existing predictions for this date
                if options['force']:
                    deleted = Prediction.objects.filter(
                        match__match_date__date=target_date
                    ).delete()
                    if deleted[0]:
                        self.stdout.write(
                            self.style.WARNING(
                                f"  Deleted {deleted[0]} existing predictions (--force)"
                            )
                        )

                matches_count = Match.objects.filter(
                    match_date__date=target_date,
                    status__in=['scheduled', 'timed'],
                    prediction__isnull=True,
                ).count()

                if matches_count == 0:
                    existing = Prediction.objects.filter(
                        match__match_date__date=target_date
                    ).count()
                    self.stdout.write(
                        f"  Predictions: {existing} already exist, 0 pending matches."
                    )
                else:
                    self.stdout.write(f"  Generating predictions for {matches_count} matches ...")
                    count = PredictionService.generate_daily_predictions(target_date=target_date)
                    total_predicted += count
                    self.stdout.write(
                        self.style.SUCCESS(f"  ✓ Generated {count} predictions")
                    )

        # ── Summary ──────────────────────────────────────────────────────
        self.stdout.write('\n' + '═' * 50)
        self.stdout.write(self.style.SUCCESS(
            f"Complete: {total_fetched} fixtures fetched, "
            f"{total_predicted} predictions generated"
        ))

