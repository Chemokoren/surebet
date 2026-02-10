"""
Team Metrics Service.

Calculates and maintains team-level statistics for display and features.
"""

import logging
from datetime import timedelta

from django.db.models import Count, Sum, Avg, Q, F
from django.utils import timezone

from apps.core.models import Team, Match, League

logger = logging.getLogger(__name__)


class TeamMetricsService:
    """
    Calculates team performance metrics for analysis pages and ML features.
    """

    @classmethod
    def get_team_stats(cls, team: Team, last_n_matches: int = 10) -> dict:
        """
        Comprehensive stats for a single team.
        """
        matches = cls._recent_matches(team, last_n_matches)

        if not matches.exists():
            return cls._empty_stats(team)

        return {
            'team': {
                'id': str(team.pk),
                'name': team.name,
                'league': team.league.name if team.league else '',
                'elo_rating': team.elo_rating,
            },
            'form': cls._calculate_form(team, matches),
            'goals': cls._calculate_goals(team, matches),
            'home_away': cls._home_away_split(team, last_n_matches),
            'streaks': cls._calculate_streaks(team, matches),
            'head_to_head': {},  # Populated on demand
        }

    @classmethod
    def get_head_to_head(cls, team_a: Team, team_b: Team, limit: int = 10) -> dict:
        """
        Head-to-head record between two teams.
        """
        matches = Match.objects.filter(
            Q(home_team=team_a, away_team=team_b) |
            Q(home_team=team_b, away_team=team_a),
            status='finished',
        ).order_by('-match_date')[:limit]

        a_wins = 0
        b_wins = 0
        draws = 0
        results = []

        for m in matches:
            if m.home_score is None or m.away_score is None:
                continue

            is_a_home = m.home_team_id == team_a.pk

            if m.home_score > m.away_score:
                winner = team_a.name if is_a_home else team_b.name
                if is_a_home:
                    a_wins += 1
                else:
                    b_wins += 1
            elif m.away_score > m.home_score:
                winner = team_b.name if is_a_home else team_a.name
                if is_a_home:
                    b_wins += 1
                else:
                    a_wins += 1
            else:
                winner = 'Draw'
                draws += 1

            results.append({
                'date': m.match_date.isoformat(),
                'home': m.home_team.name,
                'away': m.away_team.name,
                'score': f"{m.home_score}-{m.away_score}",
                'winner': winner,
            })

        total = a_wins + b_wins + draws
        return {
            'team_a': team_a.name,
            'team_b': team_b.name,
            'total_matches': total,
            'team_a_wins': a_wins,
            'team_b_wins': b_wins,
            'draws': draws,
            'dominance': round(a_wins / total, 2) if total else 0.5,
            'recent_matches': results,
        }

    @classmethod
    def update_elo_ratings(cls, match: Match) -> None:
        """
        Update ELO ratings after a match result.
        K-factor: 32 (standard for football).
        """
        if match.home_score is None or match.away_score is None:
            return

        K = 32
        home = match.home_team
        away = match.away_team

        # Expected scores
        exp_home = 1 / (1 + 10 ** ((away.elo_rating - home.elo_rating) / 400))
        exp_away = 1 - exp_home

        # Actual scores (1 = win, 0.5 = draw, 0 = loss)
        if match.home_score > match.away_score:
            actual_home, actual_away = 1.0, 0.0
        elif match.away_score > match.home_score:
            actual_home, actual_away = 0.0, 1.0
        else:
            actual_home, actual_away = 0.5, 0.5

        # New ratings
        home.elo_rating = round(home.elo_rating + K * (actual_home - exp_home))
        away.elo_rating = round(away.elo_rating + K * (actual_away - exp_away))

        home.save(update_fields=['elo_rating'])
        away.save(update_fields=['elo_rating'])

        logger.debug(
            f"ELO updated: {home.name}={home.elo_rating}, "
            f"{away.name}={away.elo_rating}"
        )

    # ── Internal Helpers ────────────────────────

    @classmethod
    def _recent_matches(cls, team: Team, limit: int):
        return Match.objects.filter(
            Q(home_team=team) | Q(away_team=team),
            status='finished',
        ).select_related('home_team', 'away_team', 'league').order_by('-match_date')[:limit]

    @classmethod
    def _calculate_form(cls, team: Team, matches) -> dict:
        """W/D/L sequence and points."""
        form_string = []
        points = 0

        for m in matches:
            if m.home_score is None or m.away_score is None:
                continue

            is_home = m.home_team_id == team.pk
            team_scored = m.home_score if is_home else m.away_score
            team_conceded = m.away_score if is_home else m.home_score

            if team_scored > team_conceded:
                form_string.append('W')
                points += 3
            elif team_scored == team_conceded:
                form_string.append('D')
                points += 1
            else:
                form_string.append('L')

        total = len(form_string)
        return {
            'sequence': form_string[:5],
            'points_last_n': points,
            'win_rate': round(form_string.count('W') / total, 2) if total else 0,
            'draw_rate': round(form_string.count('D') / total, 2) if total else 0,
            'loss_rate': round(form_string.count('L') / total, 2) if total else 0,
        }

    @classmethod
    def _calculate_goals(cls, team: Team, matches) -> dict:
        """Goals scored/conceded stats."""
        scored = []
        conceded = []

        for m in matches:
            if m.home_score is None:
                continue
            is_home = m.home_team_id == team.pk
            scored.append(m.home_score if is_home else m.away_score)
            conceded.append(m.away_score if is_home else m.home_score)

        import numpy as np
        return {
            'avg_scored': round(np.mean(scored), 2) if scored else 0,
            'avg_conceded': round(np.mean(conceded), 2) if conceded else 0,
            'total_scored': sum(scored),
            'total_conceded': sum(conceded),
            'clean_sheets': sum(1 for c in conceded if c == 0),
        }

    @classmethod
    def _home_away_split(cls, team: Team, limit: int) -> dict:
        """Performance breakdown: home vs away."""
        home_matches = Match.objects.filter(
            home_team=team, status='finished',
        ).order_by('-match_date')[:limit]

        away_matches = Match.objects.filter(
            away_team=team, status='finished',
        ).order_by('-match_date')[:limit]

        home_wins = sum(1 for m in home_matches if m.home_score and m.away_score is not None and m.home_score > m.away_score)
        away_wins = sum(1 for m in away_matches if m.home_score is not None and m.away_score and m.away_score > m.home_score)

        return {
            'home_win_rate': round(home_wins / home_matches.count(), 2) if home_matches.count() else 0,
            'away_win_rate': round(away_wins / away_matches.count(), 2) if away_matches.count() else 0,
            'home_matches': home_matches.count(),
            'away_matches': away_matches.count(),
        }

    @classmethod
    def _calculate_streaks(cls, team: Team, matches) -> dict:
        """Current win/unbeaten/loss streaks."""
        current_streak = {'type': None, 'count': 0}
        unbeaten = 0

        for m in matches:
            if m.home_score is None:
                continue
            is_home = m.home_team_id == team.pk
            scored = m.home_score if is_home else m.away_score
            conceded = m.away_score if is_home else m.home_score

            if scored > conceded:
                result = 'W'
            elif scored == conceded:
                result = 'D'
            else:
                result = 'L'

            if current_streak['type'] is None:
                current_streak = {'type': result, 'count': 1}
            elif current_streak['type'] == result:
                current_streak['count'] += 1
            else:
                break

        # Count unbeaten from start
        for m in matches:
            if m.home_score is None:
                continue
            is_home = m.home_team_id == team.pk
            scored = m.home_score if is_home else m.away_score
            conceded = m.away_score if is_home else m.home_score
            if scored >= conceded:
                unbeaten += 1
            else:
                break

        return {
            'current': current_streak,
            'unbeaten_run': unbeaten,
        }

    @classmethod
    def _empty_stats(cls, team: Team) -> dict:
        return {
            'team': {
                'id': str(team.pk), 'name': team.name,
                'league': team.league.name if team.league else '',
                'elo_rating': team.elo_rating,
            },
            'form': {'sequence': [], 'points_last_n': 0, 'win_rate': 0, 'draw_rate': 0, 'loss_rate': 0},
            'goals': {'avg_scored': 0, 'avg_conceded': 0, 'total_scored': 0, 'total_conceded': 0, 'clean_sheets': 0},
            'home_away': {'home_win_rate': 0, 'away_win_rate': 0},
            'streaks': {'current': {'type': None, 'count': 0}, 'unbeaten_run': 0},
        }
