"""
Management command to seed the top 20 prediction sources.

Run:  python manage.py seed_prediction_sources
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.predictions.models_intelligence import PredictionSource


# The top 20 prediction websites as specified
TOP_SOURCES = [
    {
        'name': 'Sports Mole',
        'slug': 'sports-mole',
        'source_type': 'website',
        'website_url': 'https://www.sportsmole.co.uk/football/predictions/',
        'description': 'News-driven insights and deep previews.',
        'priority': 1,
        'scraper_class': 'apps.predictions.scrapers.builtin.SportsMoleScraper',
    },
    {
        'name': 'WhoScored',
        'slug': 'whoscored',
        'source_type': 'website',
        'website_url': 'https://www.whoscored.com/',
        'description': 'Powered by Opta data; best for player performance.',
        'priority': 2,
        'scraper_class': 'apps.predictions.scrapers.builtin.WhoScoredScraper',
    },
    {
        'name': 'PredictZ',
        'slug': 'predictz',
        'source_type': 'website',
        'website_url': 'https://www.predictz.com/',
        'description': 'High-volume AI predictions and current form stats.',
        'priority': 3,
        'scraper_class': 'apps.predictions.scrapers.builtin.PredictZScraper',
    },
    {
        'name': 'Football Whispers',
        'slug': 'football-whispers',
        'source_type': 'website',
        'website_url': 'https://www.footballwhispers.com/',
        'description': 'Sharp, analytical focus on major European leagues.',
        'priority': 4,
        'scraper_class': 'apps.predictions.scrapers.builtin.FootballWhispersScraper',
    },
    {
        'name': 'FootyStats',
        'slug': 'footystats',
        'source_type': 'website',
        'website_url': 'https://footystats.org/',
        'description': 'Best for corners, cards, and xG (Expected Goals).',
        'priority': 5,
        'scraper_class': 'apps.predictions.scrapers.builtin.FootyStatsScraper',
    },
    {
        'name': 'Understat',
        'slug': 'understat',
        'source_type': 'website',
        'website_url': 'https://understat.com/',
        'description': 'Pure focus on underlying performance and xG "Fairness."',
        'priority': 6,
        'scraper_class': 'apps.predictions.scrapers.builtin.UnderstatScraper',
    },
    {
        'name': 'Dimers',
        'slug': 'dimers',
        'source_type': 'model',
        'website_url': 'https://www.dimers.com/bet-hub/soccer',
        'description': 'Machine learning models for win/draw/loss percentages.',
        'priority': 7,
        'scraper_class': 'apps.predictions.scrapers.builtin.DimersScraper',
    },
    {
        'name': 'Squawka',
        'slug': 'squawka',
        'source_type': 'website',
        'website_url': 'https://www.squawka.com/',
        'description': 'Great for comparing team metrics before a match.',
        'priority': 8,
        'scraper_class': 'apps.predictions.scrapers.builtin.SquawkaScraper',
    },
    {
        'name': 'MrFixitsTips',
        'slug': 'mrfixitstips',
        'source_type': 'community',
        'website_url': 'https://www.mrfixitstips.co.uk/',
        'description': 'A fan-favorite for community chat and "Bet of the Day."',
        'priority': 9,
    },
    {
        'name': 'Betensured',
        'slug': 'betensured',
        'source_type': 'website',
        'website_url': 'https://www.betensured.com/',
        'description': 'Offers both free and premium high-accuracy tiers.',
        'priority': 10,
        'scraper_class': 'apps.predictions.scrapers.builtin.BetensuredScraper',
    },
    {
        'name': 'SportyTrader',
        'slug': 'sportytrader',
        'source_type': 'website',
        'website_url': 'https://www.sportytrader.com/',
        'description': 'Clean UI and simple, award-winning analysis.',
        'priority': 11,
    },
    {
        'name': 'SoccerStats',
        'slug': 'soccerstats',
        'source_type': 'website',
        'website_url': 'https://www.soccerstats.com/',
        'description': '"Old school" but packed with goal-timing data.',
        'priority': 12,
    },
    {
        'name': 'Eagle Predict',
        'slug': 'eagle-predict',
        'source_type': 'model',
        'website_url': 'https://eaglepredict.com/',
        'description': 'Rated highly for its claimed ~90% accuracy models.',
        'priority': 13,
    },
    {
        'name': 'RatingBet',
        'slug': 'ratingbet',
        'source_type': 'website',
        'website_url': 'https://www.ratingbet.com/',
        'description': 'Combines professional tipster picks with market trends.',
        'priority': 14,
    },
    {
        'name': 'SoccerVista',
        'slug': 'soccervista',
        'source_type': 'website',
        'website_url': 'https://www.soccervista.com/',
        'description': 'One of the longest-running sites with deep historical data.',
        'priority': 15,
    },
    {
        'name': 'Betalyst',
        'slug': 'betalyst',
        'source_type': 'website',
        'website_url': 'https://betalyst.com/',
        'description': 'Specializes in BTTS (Both Teams to Score) algorithms.',
        'priority': 16,
    },
    {
        'name': 'FreeSuperTips',
        'slug': 'freesupertips',
        'source_type': 'website',
        'website_url': 'https://www.freesupertips.co.uk/',
        'description': 'Excellent for high-odds accumulator suggestions.',
        'priority': 17,
    },
    {
        'name': 'Vitibet',
        'slug': 'vitibet',
        'source_type': 'website',
        'website_url': 'https://www.vitibet.com/',
        'description': 'Simple index-based predictions for hundreds of games.',
        'priority': 18,
        'scraper_class': 'apps.predictions.scrapers.builtin.VitibetScraper',
    },
    {
        'name': 'Tips180',
        'slug': 'tips180',
        'source_type': 'website',
        'website_url': 'https://www.tips180.com/',
        'description': 'Focuses on smart value bets and education for punters.',
        'priority': 19,
    },
    {
        'name': 'Sportsgambler',
        'slug': 'sportsgambler',
        'source_type': 'website',
        'website_url': 'https://www.sportsgambler.com/',
        'description': 'Great for identifying "Value" without a paywall.',
        'priority': 20,
    },
    # ── Top Pundits ───────────────────────────────────────────────────
    {
        'name': 'Paul Merson (Sky Sports)',
        'slug': 'paul-merson',
        'source_type': 'pundit',
        'website_url': 'https://www.skysports.com/football/news/predictions',
        'description': 'Sky Sports pundit — Premier League match predictions.',
        'priority': 21,
    },
    {
        'name': 'Mark Lawrenson (BBC)',
        'slug': 'mark-lawrenson',
        'source_type': 'pundit',
        'website_url': 'https://www.bbc.co.uk/sport/football/predictions',
        'description': 'BBC Sport predictions — long-running pundit column.',
        'priority': 22,
    },
    {
        'name': 'Michael Owen (BetVictor)',
        'slug': 'michael-owen',
        'source_type': 'pundit',
        'website_url': 'https://www.betvictor.com/en-gb/tips',
        'description': 'Ex-footballer match predictions for BetVictor.',
        'priority': 23,
    },
    {
        'name': 'Forebet',
        'slug': 'forebet',
        'source_type': 'model',
        'website_url': 'https://www.forebet.com/',
        'description': 'Mathematical prediction model with probability percentages.',
        'priority': 4,
        'scraper_class': 'apps.predictions.scrapers.builtin.ForebetScraper',
    },
]


class Command(BaseCommand):
    help = 'Seed the top 20+ prediction sources into the database (learning phase)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--learning-days',
            type=int,
            default=90,
            help='Duration of learning phase in days (default: 90)',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete all existing sources before seeding',
        )

    def handle(self, *args, **options):
        learning_days = options['learning_days']
        today = timezone.now().date()

        if options['reset']:
            deleted, _ = PredictionSource.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Deleted {deleted} existing sources."))

        created_count = 0
        updated_count = 0

        for source_data in TOP_SOURCES:
            defaults = {
                'name': source_data['name'],
                'source_type': source_data.get('source_type', 'website'),
                'website_url': source_data.get('website_url', ''),
                'description': source_data.get('description', ''),
                'priority': source_data.get('priority', 50),
                'scraper_class': source_data.get('scraper_class', ''),
                'status': 'learning',
                'learning_start_date': today,
                'learning_end_date': today + timedelta(days=learning_days),
                'scrape_enabled': bool(source_data.get('scraper_class')),
            }

            source, created = PredictionSource.objects.update_or_create(
                slug=source_data['slug'],
                defaults=defaults,
            )

            if created:
                created_count += 1
                self.stdout.write(f"  ✅ Created: {source.name}")
            else:
                updated_count += 1
                self.stdout.write(f"  🔄 Updated: {source.name}")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! {created_count} created, {updated_count} updated. "
            f"Learning phase ends: {today + timedelta(days=learning_days)}"
        ))
