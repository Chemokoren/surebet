"""
Feature Engineering Service.

Generates ML features for match prediction:
    - Recent form (last N matches)
    - Home vs away performance
    - Head-to-head record
    - ELO ratings
    - Schedule congestion
    - Goal-scoring patterns

Each feature generator returns a dict that gets merged into the final feature vector.
"""

import logging
from datetime import timedelta
from typing import Optional

from django.db.models import Q, Avg, Count, Sum, F
from django.utils import timezone

from apps.core.models import Match, Team, League

logger = logging.getLogger(__name__)


class FeatureEngineeringService:
    """
    Generates feature vectors for match predictions.
    """

    DEFAULT_LOOKBACK = 10  # Number of recent matches
    ELO_K_FACTOR = 32
    ELO_DEFAULT = 1500.0

    @classmethod
    def generate_features(cls, match: Match) -> dict:
        """
        Generate the complete feature vector for a match.
        Returns a flat dict of feature_name → float/int values.
        """
        features = {}

        try:
            # Recent form
            features.update(cls._recent_form(match.home_team, 'home', cls.DEFAULT_LOOKBACK))
            features.update(cls._recent_form(match.away_team, 'away', cls.DEFAULT_LOOKBACK))

            # Home/Away split performance
            features.update(cls._home_away_performance(match.home_team, match.away_team))

            # Head-to-head
            features.update(cls._head_to_head(match.home_team, match.away_team))

            # ELO ratings
            features.update(cls._elo_features(match.home_team, match.away_team))

            # Schedule congestion
            features.update(cls._schedule_congestion(match))

            # Goal patterns
            features.update(cls._goal_patterns(match.home_team, match.away_team))

            # League-level stats
            features.update(cls._league_features(match.league))

        except Exception as e:
            logger.error(f"Feature generation error for match {match.id}: {e}")

        return features

    # ── Individual Feature Generators ───────────

    @classmethod
    def _recent_form(cls, team: Team, prefix: str, n: int = 10) -> dict:
        """
        Last N matches: wins, draws, losses, goals, points.
        """
        recent = Match.objects.filter(
            Q(home_team=team) | Q(away_team=team),
            status='finished',
            home_score__isnull=False,
        ).order_by('-match_date')[:n]

        wins, draws, losses = 0, 0, 0
        goals_scored, goals_conceded = 0, 0
        points = 0

        for m in recent:
            is_home = m.home_team_id == team.pk
            gs = m.home_score if is_home else m.away_score
            gc = m.away_score if is_home else m.home_score

            goals_scored += gs
            goals_conceded += gc

            if gs > gc:
                wins += 1
                points += 3
            elif gs == gc:
                draws += 1
                points += 1
            else:
                losses += 1

        total = len(recent) or 1

        return {
            f'{prefix}_wins_last_{n}': wins,
            f'{prefix}_draws_last_{n}': draws,
            f'{prefix}_losses_last_{n}': losses,
            f'{prefix}_goals_scored_last_{n}': goals_scored,
            f'{prefix}_goals_conceded_last_{n}': goals_conceded,
            f'{prefix}_points_last_{n}': points,
            f'{prefix}_win_rate': wins / total,
            f'{prefix}_avg_goals_scored': goals_scored / total,
            f'{prefix}_avg_goals_conceded': goals_conceded / total,
            f'{prefix}_form_index': points / (total * 3),
        }

    @classmethod
    def _home_away_performance(cls, home_team: Team, away_team: Team) -> dict:
        """
        Home team's home record vs Away team's away record.
        """
        home_matches = Match.objects.filter(
            home_team=home_team,
            status='finished',
            home_score__isnull=False,
        ).order_by('-match_date')[:10]

        away_matches = Match.objects.filter(
            away_team=away_team,
            status='finished',
            away_score__isnull=False,
        ).order_by('-match_date')[:10]

        home_wins = sum(1 for m in home_matches if m.home_score > m.away_score)
        away_wins = sum(1 for m in away_matches if m.away_score > m.home_score)
        home_count = len(home_matches) or 1
        away_count = len(away_matches) or 1

        return {
            'home_home_win_rate': home_wins / home_count,
            'away_away_win_rate': away_wins / away_count,
            'home_advantage': home_wins / home_count - away_wins / away_count,
        }

    @classmethod
    def _head_to_head(cls, home_team: Team, away_team: Team, n: int = 10) -> dict:
        """
        Direct head-to-head record between the two teams.
        """
        h2h = Match.objects.filter(
            Q(home_team=home_team, away_team=away_team) |
            Q(home_team=away_team, away_team=home_team),
            status='finished',
            home_score__isnull=False,
        ).order_by('-match_date')[:n]

        home_wins, away_wins, draws = 0, 0, 0
        for m in h2h:
            if m.home_team_id == home_team.pk:
                if m.home_score > m.away_score:
                    home_wins += 1
                elif m.home_score < m.away_score:
                    away_wins += 1
                else:
                    draws += 1
            else:
                if m.away_score > m.home_score:
                    home_wins += 1
                elif m.away_score < m.home_score:
                    away_wins += 1
                else:
                    draws += 1

        total = len(h2h) or 1

        return {
            'h2h_home_wins': home_wins,
            'h2h_away_wins': away_wins,
            'h2h_draws': draws,
            'h2h_total': len(h2h),
            'h2h_home_dominance': home_wins / total,
        }

    @classmethod
    def _elo_features(cls, home_team: Team, away_team: Team) -> dict:
        """
        ELO rating features.
        """
        return {
            'home_elo': home_team.elo_rating,
            'away_elo': away_team.elo_rating,
            'elo_diff': home_team.elo_rating - away_team.elo_rating,
            'elo_home_expected': cls._expected_score(home_team.elo_rating, away_team.elo_rating),
        }

    @classmethod
    def _schedule_congestion(cls, match: Match) -> dict:
        """
        Number of matches each team played in the last 7, 14, 30 days.
        High congestion → fatigue → lower performance.
        """
        now = match.match_date
        features = {}

        for prefix, team in [('home', match.home_team), ('away', match.away_team)]:
            for days, label in [(7, '7d'), (14, '14d'), (30, '30d')]:
                count = Match.objects.filter(
                    Q(home_team=team) | Q(away_team=team),
                    match_date__gte=now - timedelta(days=days),
                    match_date__lt=now,
                    status='finished',
                ).count()
                features[f'{prefix}_matches_{label}'] = count

        return features

    @classmethod
    def _goal_patterns(cls, home_team: Team, away_team: Team) -> dict:
        """
        Goal scoring and conceding patterns.
        """
        features = {}

        for prefix, team in [('home', home_team), ('away', away_team)]:
            home_games = Match.objects.filter(
                home_team=team, status='finished',
                home_score__isnull=False,
            ).order_by('-match_date')[:5]

            away_games = Match.objects.filter(
                away_team=team, status='finished',
                away_score__isnull=False,
            ).order_by('-match_date')[:5]

            all_goals = []
            for m in home_games:
                all_goals.append(m.home_score + m.away_score)
            for m in away_games:
                all_goals.append(m.home_score + m.away_score)

            avg_total = sum(all_goals) / len(all_goals) if all_goals else 2.5

            # Over 2.5 tendency
            over_25 = sum(1 for g in all_goals if g > 2.5)
            features[f'{prefix}_avg_total_goals'] = avg_total
            features[f'{prefix}_over25_rate'] = over_25 / len(all_goals) if all_goals else 0.5

        return features

    @classmethod
    def _league_features(cls, league: League) -> dict:
        """
        League-level averages (home win rate, avg goals, etc.).
        """
        season_matches = Match.objects.filter(
            league=league,
            status='finished',
            home_score__isnull=False,
        ).order_by('-match_date')[:100]

        home_wins = sum(1 for m in season_matches if m.home_score > m.away_score)
        draws = sum(1 for m in season_matches if m.home_score == m.away_score)
        total = len(season_matches) or 1
        total_goals = sum(m.home_score + m.away_score for m in season_matches)

        return {
            'league_home_win_rate': home_wins / total,
            'league_draw_rate': draws / total,
            'league_avg_goals': total_goals / total,
        }

    # ── Utilities ───────────────────────────────

    @staticmethod
    def _expected_score(rating_a: float, rating_b: float) -> float:
        """Calculate expected score using ELO formula."""
        return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))

    @classmethod
    def update_elo(cls, match: Match) -> None:
        """
        Update ELO ratings after a match is finished.
        """
        if match.home_score is None or match.away_score is None:
            return

        home = match.home_team
        away = match.away_team

        expected_home = cls._expected_score(home.elo_rating, away.elo_rating)
        expected_away = 1 - expected_home

        if match.home_score > match.away_score:
            actual_home, actual_away = 1.0, 0.0
        elif match.home_score < match.away_score:
            actual_home, actual_away = 0.0, 1.0
        else:
            actual_home, actual_away = 0.5, 0.5

        home.elo_rating += cls.ELO_K_FACTOR * (actual_home - expected_home)
        away.elo_rating += cls.ELO_K_FACTOR * (actual_away - expected_away)

        home.save(update_fields=['elo_rating', 'updated_at'])
        away.save(update_fields=['elo_rating', 'updated_at'])
