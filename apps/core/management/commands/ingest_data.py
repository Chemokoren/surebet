"""
Data Ingestion Management Command.

Fetches fixtures and results from external APIs.
Usage:
    python manage.py ingest_data --today          # Fetch today's fixtures
    python manage.py ingest_data --results        # Update finished match results
    python manage.py ingest_data --league PL      # Specific league only
    python manage.py ingest_data --backfill 7     # Backfill last N days
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.services.data_ingestion import DataIngestionService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Ingest match data from external APIs'

    def add_arguments(self, parser):
        parser.add_argument(
            '--today', action='store_true',
            help="Fetch today's scheduled fixtures",
        )
        parser.add_argument(
            '--results', action='store_true',
            help='Update results for finished matches',
        )
        parser.add_argument(
            '--league', type=str, default=None,
            help='Filter by league code (e.g. PL, LL, BL1)',
        )
        parser.add_argument(
            '--backfill', type=int, default=0,
            help='Backfill fixtures for the last N days',
        )
        parser.add_argument(
            '--stale', action='store_true',
            help='Remove stale/unplayed matches',
        )

    def handle(self, *args, **options):
        service = DataIngestionService()

        if options['stale']:
            removed = service.remove_stale_unplayed_matches()
            self.stdout.write(self.style.SUCCESS(f"Removed {removed} stale matches"))
            return

        if options['today']:
            self.stdout.write("Fetching today's fixtures...")
            count = service.fetch_todays_fixtures()
            self.stdout.write(self.style.SUCCESS(f"Ingested {count} fixtures"))

        if options['results']:
            self.stdout.write("Updating match results...")
            count = service.update_match_results()
            self.stdout.write(self.style.SUCCESS(f"Updated {count} match results"))

        if options['backfill'] > 0:
            days = options['backfill']
            self.stdout.write(f"Backfilling last {days} days...")
            total = 0
            for d in range(days):
                target = timezone.now().date() - timedelta(days=d)
                count = service.fetch_fixtures_for_date(target)
                total += count
            self.stdout.write(self.style.SUCCESS(f"Backfilled {total} fixtures"))

        if not any([options['today'], options['results'], options['backfill']]):
            self.stdout.write(self.style.WARNING(
                "No action specified. Use --today, --results, --backfill N, or --stale"
            ))
