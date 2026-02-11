"""
Seed upcoming matches + predictions for FuturaPredict.

Creates realistic fixtures across multiple leagues for today + the next
few days so the predictions page always has fresh content.
"""

import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import Match, League, Team
from apps.predictions.models import Prediction, ModelVersion


# ── fixture data ──────────────────────────────────────────────

LEAGUE_TEAMS = {
    'PL': [
        'Arsenal', 'Aston Villa', 'Brighton', 'Chelsea',
        'Crystal Palace', 'Everton', 'Fulham', 'Liverpool',
        'Manchester City', 'Manchester United', 'Newcastle United',
        'Nottingham Forest', 'Tottenham Hotspur', 'West Ham United',
        'Wolverhampton', 'Bournemouth', 'Brentford', 'Leeds United',
    ],
    'LL': [
        'Real Madrid', 'Barcelona', 'Atletico Madrid', 'Real Sociedad',
        'Villarreal', 'Athletic Bilbao', 'Sevilla', 'Real Betis',
        'Girona', 'Valencia',
    ],
    'SA': [
        'AC Milan', 'Inter Milan', 'Juventus', 'Napoli',
        'AS Roma', 'Lazio', 'Fiorentina', 'Atalanta',
        'Bologna', 'Torino',
    ],
    'BL1': [
        'Bayern Munich', 'Borussia Dortmund', 'RB Leipzig',
        'Bayer Leverkusen', 'Eintracht Frankfurt', 'Wolfsburg',
    ],
    'FL1': [
        'Paris Saint-Germain', 'Marseille', 'Monaco', 'Lyon',
        'Lille', 'Nice',
    ],
}

KICKOFF_HOURS = [13, 15, 17, 19, 20, 21]  # Common kickoff times (UTC)


class Command(BaseCommand):
    help = 'Seed upcoming matches and generate predictions'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days', type=int, default=3,
            help='Number of days to seed (starting from today)',
        )
        parser.add_argument(
            '--per-day', type=int, default=4,
            help='Max matches to create per day',
        )
        parser.add_argument(
            '--clear', action='store_true',
            help='Delete all existing scheduled matches first',
        )

    def handle(self, *args, **options):
        days = options['days']
        per_day = options['per_day']
        clear = options['clear']

        if clear:
            deleted_p, _ = Prediction.objects.filter(
                match__status='scheduled'
            ).delete()
            deleted_m, _ = Match.objects.filter(status='scheduled').delete()
            self.stdout.write(
                self.style.WARNING(
                    f'Cleared {deleted_m} scheduled matches and '
                    f'{deleted_p} related predictions.'
                )
            )

        # Ensure model version exists
        model_version, _ = ModelVersion.objects.get_or_create(
            name='system_default', version='1.0',
            defaults={'is_active': True, 'model_type': 'ensemble'},
        )

        today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        total_created = 0

        for day_offset in range(days):
            target_day = today + timedelta(days=day_offset)
            matches_for_day = self._create_matches_for_day(
                target_day, per_day
            )
            for match in matches_for_day:
                self._create_prediction(match, model_version)
            total_created += len(matches_for_day)

        self.stdout.write(
            self.style.SUCCESS(
                f'Created {total_created} matches with predictions '
                f'over {days} day(s).'
            )
        )

    # ── helpers ────────────────────────────────────────────────

    def _create_matches_for_day(self, target_day, max_matches):
        """Create fixtures for a single day."""
        created = []
        league_codes = list(LEAGUE_TEAMS.keys())
        random.shuffle(league_codes)

        used_teams = set()

        for league_code in league_codes:
            if len(created) >= max_matches:
                break

            league = League.objects.filter(
                code=league_code, is_active=True
            ).first()
            if not league:
                continue

            team_names = LEAGUE_TEAMS[league_code][:]
            random.shuffle(team_names)

            # Pick pairs that haven't been used today
            pairs = []
            i = 0
            while i + 1 < len(team_names) and len(created) + len(pairs) < max_matches:
                a, b = team_names[i], team_names[i + 1]
                if a not in used_teams and b not in used_teams:
                    pairs.append((a, b))
                    used_teams.update([a, b])
                i += 2

            for home_name, away_name in pairs:
                home_team = self._ensure_team(home_name, league)
                away_team = self._ensure_team(away_name, league)

                hour = random.choice(KICKOFF_HOURS)
                minute = random.choice([0, 15, 30, 45])
                kick_off = target_day.replace(hour=hour, minute=minute)

                # Avoid duplicates
                if Match.objects.filter(
                    home_team=home_team,
                    away_team=away_team,
                    match_date=kick_off,
                ).exists():
                    continue

                match = Match.objects.create(
                    home_team=home_team,
                    away_team=away_team,
                    league=league,
                    match_date=kick_off,
                    status='scheduled',
                )
                created.append(match)
                self.stdout.write(
                    f'  ⚽  {match.home_team} vs {match.away_team} '
                    f'({league.code}) – {kick_off:%a %d %b %H:%M}'
                )

        return created

    @staticmethod
    def _ensure_team(name, league):
        team, _ = Team.objects.get_or_create(
            name=name,
            defaults={'league': league},
        )
        return team

    @staticmethod
    def _create_prediction(match, model_version):
        if hasattr(match, 'prediction'):
            return  # already predicted

        # Generate semi-random but plausible probabilities
        home_base = random.uniform(0.25, 0.55)
        draw_base = random.uniform(0.15, 0.30)
        away_base = 1.0 - home_base - draw_base
        if away_base < 0.1:
            away_base = 0.1
            total = home_base + draw_base + away_base
            home_base /= total
            draw_base /= total
            away_base /= total

        probs = {
            'home': round(home_base, 4),
            'draw': round(draw_base, 4),
            'away': round(away_base, 4),
        }

        best = max(probs, key=probs.get)
        outcome_map = {'home': 'home_win', 'draw': 'draw', 'away': 'away_win'}
        outcome = outcome_map[best]
        confidence = round(probs[best] * 100, 1)

        # Tier: high confidence → premium, random 30% free for engagement
        tier = 'free' if random.random() < 0.30 else 'premium'

        Prediction.objects.create(
            match=match,
            model_version=model_version,
            home_win_prob=probs['home'],
            draw_prob=probs['draw'],
            away_win_prob=probs['away'],
            predicted_outcome=outcome,
            confidence_score=confidence,
            tier=tier,
            is_locked=False,
        )
