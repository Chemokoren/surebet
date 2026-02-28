"""
Team Analytics Service.

Calculates detailed performance metrics for individual teams including:
- Win/draw/loss rate
- Clean sheets
- Goals scored/conceded
- Recent form (last 5 matches)
- Head-to-head records
- Home/away splits
"""

import logging
from datetime import timedelta
from django.db.models import Q, F, Count, Sum, Avg
from django.utils import timezone

from apps.core.models import Match, Team
from apps.predictions.models import Prediction

logger = logging.getLogger(__name__)


class TeamAnalyticsService:
    """
    Calculates team performance metrics for analysis and display.
    """

    # Premium feature flags - controls which metrics require paid access
    PREMIUM_METRICS = {
        'advanced_form',      # Detailed form analysis
        'head_to_head',       # H2H records
        'home_away_split',    # Home vs away statistics
        'prediction_accuracy', # Team-specific prediction accuracy
    }

    @classmethod
    def get_team_overview(cls, team: Team, include_premium: bool = False) -> dict:
        """
        Get complete team overview with optional premium metrics.

        Args:
            team: Team instance
            include_premium: Whether to include premium-gated metrics

        Returns:
            {
                'team': {team info},
                'season_stats': {basic stats},
                'form': [recent matches],
                'premium_metrics': {if include_premium} or None,
                'access': {'can_access': bool, 'reason': str},
            }
        """
        return {
            'team': {
                'id': str(team.id),
                'name': team.name,
                'short_name': team.short_name,
                'logo_url': team.logo_url,
                'country': team.country,
                'elo_rating': team.elo_rating,
                'league': {
                    'id': str(team.league.id),
                    'name': team.league.name,
                    'code': team.league.code,
                } if team.league else None,
            },
            'season_stats': cls.get_season_stats(team),
            'form': cls.get_recent_form(team),
            'premium_metrics': cls.get_premium_metrics(team) if include_premium else None,
            'access': {
                'requires_premium': True,
                'premium_sections': list(cls.PREMIUM_METRICS),
            },
        }

    @classmethod
    def get_season_stats(cls, team: Team, days: int = 365) -> dict:
        """
        Get season statistics (free-tier).

        Includes:
        - Win/Draw/Loss counts and rates
        - Goals scored/conceded
        - Clean sheets
        """
        cutoff = timezone.now() - timedelta(days=days)

        # Get all matches for team (home and away)
        home_matches = list(Match.objects.filter(
            home_team=team,
            status='finished',
            match_date__gte=cutoff,
        ))
        away_matches = list(Match.objects.filter(
            away_team=team,
            status='finished',
            match_date__gte=cutoff,
        ))

        all_matches = sorted(home_matches + away_matches, key=lambda m: m.match_date)

        # Calculate metrics
        wins = 0
        draws = 0
        losses = 0
        goals_for = 0
        goals_against = 0
        clean_sheets = 0
        btts_count = 0
        failed_to_score = 0
        over_2_5 = 0
        first_half_goals = 0
        second_half_goals = 0

        current_win_streak = 0
        current_unbeaten_streak = 0
        current_losing_streak = 0

        for match in all_matches:
            is_home = match.home_team_id == team.id
            
            gf = match.home_score if is_home else match.away_score
            ga = match.away_score if is_home else match.home_score
            
            hf_gf = match.home_half_score if is_home else match.away_half_score
            hf_ga = match.away_half_score if is_home else match.home_half_score
            
            gf = gf or 0
            ga = ga or 0

            goals_for += gf
            goals_against += ga

            if hf_gf is not None and hf_ga is not None:
                first_half_goals += (hf_gf + hf_ga)
                second_half_goals += ((gf + ga) - (hf_gf + hf_ga))

            if gf > 0 and ga > 0:
                btts_count += 1
            if gf == 0:
                failed_to_score += 1
            if (gf + ga) > 2.5:
                over_2_5 += 1

            if gf > ga:
                wins += 1
                current_win_streak += 1
                current_unbeaten_streak += 1
                current_losing_streak = 0
            elif gf == ga:
                draws += 1
                current_win_streak = 0
                current_unbeaten_streak += 1
                current_losing_streak = 0
            else:
                losses += 1
                current_win_streak = 0
                current_unbeaten_streak = 0
                current_losing_streak += 1

            if ga == 0:
                clean_sheets += 1

        total_matches = wins + draws + losses
        win_rate = (wins / total_matches * 100) if total_matches > 0 else 0
        draw_rate = (draws / total_matches * 100) if total_matches > 0 else 0
        loss_rate = (losses / total_matches * 100) if total_matches > 0 else 0

        return {
            'total_matches': total_matches,
            'wins': wins,
            'draws': draws,
            'losses': losses,
            'win_rate': round(win_rate, 1),
            'draw_rate': round(draw_rate, 1),
            'loss_rate': round(loss_rate, 1),
            'goals_for': goals_for,
            'goals_against': goals_against,
            'goal_difference': goals_for - goals_against,
            'goals_per_match': round(goals_for / total_matches, 2) if total_matches > 0 else 0,
            'conceded_per_match': round(goals_against / total_matches, 2) if total_matches > 0 else 0,
            'clean_sheets': clean_sheets,
            'clean_sheet_rate': round(clean_sheets / total_matches * 100, 1) if total_matches > 0 else 0,
            'btts_rate': round(btts_count / total_matches * 100, 1) if total_matches > 0 else 0,
            'failed_to_score_rate': round(failed_to_score / total_matches * 100, 1) if total_matches > 0 else 0,
            'over_2_5_rate': round(over_2_5 / total_matches * 100, 1) if total_matches > 0 else 0,
            'first_half_goals': first_half_goals,
            'second_half_goals': second_half_goals,
            'current_win_streak': current_win_streak,
            'current_unbeaten_streak': current_unbeaten_streak,
            'current_losing_streak': current_losing_streak,
            'period_days': days,
        }

    @classmethod
    def get_recent_form(cls, team: Team, num_matches: int = 5) -> list:
        """
        Get recent match form (last N matches).

        Returns list of matches with result indicator (W/D/L).
        """
        home_matches = Match.objects.filter(
            home_team=team,
            status='finished',
        ).order_by('-match_date')[:num_matches]

        away_matches = Match.objects.filter(
            away_team=team,
            status='finished',
        ).order_by('-match_date')[:num_matches]

        # Combine and sort
        all_matches = sorted(
            list(home_matches) + list(away_matches),
            key=lambda m: m.match_date,
            reverse=True,
        )[:num_matches]

        form = []
        for match in all_matches:
            is_home = match.home_team_id == team.id

            if is_home:
                result = 'W' if match.home_score > match.away_score else (
                    'D' if match.home_score == match.away_score else 'L'
                )
                opponent = match.away_team
                score_for = match.home_score
                score_against = match.away_score
            else:
                result = 'W' if match.away_score > match.home_score else (
                    'D' if match.away_score == match.home_score else 'L'
                )
                opponent = match.home_team
                score_for = match.away_score
                score_against = match.home_score

            form.append({
                'match_date': match.match_date.isoformat(),
                'opponent': {
                    'id': str(opponent.id),
                    'name': opponent.name,
                    'logo_url': opponent.logo_url,
                },
                'result': result,
                'score_for': score_for,
                'score_against': score_against,
                'venue': 'home' if is_home else 'away',
            })

        return form

    @classmethod
    def get_premium_metrics(cls, team: Team) -> dict:
        """
        Get premium-tier analytics features.

        Includes:
        - Advanced form analysis
        - Head-to-head records
        - Home/away splits
        - Prediction accuracy
        """
        return {
            'head_to_head': cls.get_head_to_head_stats(team),
            'home_away_split': cls.get_home_away_split(team),
            'advanced_form': cls.get_advanced_form_analysis(team),
            'prediction_accuracy': cls.get_team_prediction_accuracy(team),
        }

    @classmethod
    def get_head_to_head_stats(cls, team: Team, limit: int = 10) -> dict:
        """
        Get head-to-head records against recent opponents (PREMIUM).
        """
        # Get recent matches
        home_matches = Match.objects.filter(
            home_team=team,
            status='finished',
        ).order_by('-match_date')[:limit]

        away_matches = Match.objects.filter(
            away_team=team,
            status='finished',
        ).order_by('-match_date')[:limit]

        # Aggregate head-to-head records
        h2h_stats = {}

        for match in home_matches:
            opponent = match.away_team
            key = f"{team.id}_vs_{opponent.id}"

            if key not in h2h_stats:
                h2h_stats[key] = {
                    'opponent': {
                        'id': str(opponent.id),
                        'name': opponent.name,
                        'logo_url': opponent.logo_url,
                    },
                    'matches': 0,
                    'wins': 0,
                    'draws': 0,
                    'losses': 0,
                }

            h2h_stats[key]['matches'] += 1
            if match.home_score > match.away_score:
                h2h_stats[key]['wins'] += 1
            elif match.home_score == match.away_score:
                h2h_stats[key]['draws'] += 1
            else:
                h2h_stats[key]['losses'] += 1

        for match in away_matches:
            opponent = match.home_team
            key = f"{team.id}_vs_{opponent.id}"

            if key not in h2h_stats:
                h2h_stats[key] = {
                    'opponent': {
                        'id': str(opponent.id),
                        'name': opponent.name,
                        'logo_url': opponent.logo_url,
                    },
                    'matches': 0,
                    'wins': 0,
                    'draws': 0,
                    'losses': 0,
                }

            h2h_stats[key]['matches'] += 1
            if match.away_score > match.home_score:
                h2h_stats[key]['wins'] += 1
            elif match.away_score == match.home_score:
                h2h_stats[key]['draws'] += 1
            else:
                h2h_stats[key]['losses'] += 1

        return {
            'records': list(h2h_stats.values()),
            'total_opponents_faced': len(h2h_stats),
        }

    @classmethod
    def get_home_away_split(cls, team: Team, days: int = 365) -> dict:
        """
        Get home vs away statistics breakdown (PREMIUM).
        """
        cutoff = timezone.now() - timedelta(days=days)

        home_matches = Match.objects.filter(
            home_team=team,
            status='finished',
            match_date__gte=cutoff,
        )
        away_matches = Match.objects.filter(
            away_team=team,
            status='finished',
            match_date__gte=cutoff,
        )

        def calc_stats(matches, is_home: bool) -> dict:
            wins = draws = losses = goals_for = goals_against = clean_sheets = 0

            for match in matches:
                if is_home:
                    goals_for += match.home_score or 0
                    goals_against += match.away_score or 0
                    if match.home_score > match.away_score:
                        wins += 1
                    elif match.home_score == match.away_score:
                        draws += 1
                    else:
                        losses += 1
                    if match.away_score == 0:
                        clean_sheets += 1
                else:
                    goals_for += match.away_score or 0
                    goals_against += match.home_score or 0
                    if match.away_score > match.home_score:
                        wins += 1
                    elif match.away_score == match.home_score:
                        draws += 1
                    else:
                        losses += 1
                    if match.home_score == 0:
                        clean_sheets += 1

            total = wins + draws + losses
            return {
                'matches': total,
                'wins': wins,
                'draws': draws,
                'losses': losses,
                'win_rate': round(wins / total * 100, 1) if total > 0 else 0,
                'goals_for': goals_for,
                'goals_against': goals_against,
                'goal_difference': goals_for - goals_against,
                'clean_sheets': clean_sheets,
            }

        return {
            'home': calc_stats(home_matches, True),
            'away': calc_stats(away_matches, False),
        }

    @classmethod
    def get_advanced_form_analysis(cls, team: Team, num_matches: int = 10) -> dict:
        """
        Get advanced form analysis with trends (PREMIUM).
        """
        form = cls.get_recent_form(team, num_matches)

        # Calculate trends
        if len(form) >= 3:
            recent_3_wins = sum(1 for m in form[:3] if m['result'] == 'W')
            recent_5_wins = sum(1 for m in form[:5] if m['result'] == 'W')
        else:
            recent_3_wins = recent_5_wins = 0

        return {
            'recent_matches': form,
            'form_trend': 'Improving' if recent_3_wins >= 2 else (
                'Declining' if recent_3_wins <= 1 else 'Stable'
            ),
            'recent_3_record': recent_3_wins,
            'recent_5_record': recent_5_wins,
        }

    @classmethod
    def get_team_prediction_accuracy(cls, team: Team, days: int = 90) -> dict:
        """
        Get FuturaPredict accuracy for matches involving this team (PREMIUM).
        """
        cutoff = timezone.now() - timedelta(days=days)

        # Get predictions for matches involving team
        predictions = Prediction.objects.filter(
            match__status='finished',
            is_correct__isnull=False,
            resolved_at__gte=cutoff,
        ).filter(
            Q(match__home_team=team) | Q(match__away_team=team)
        )

        if not predictions.exists():
            return {
                'total_predictions': 0,
                'correct': 0,
                'accuracy_rate': 0,
                'by_tier': {
                    'free': {'total': 0, 'correct': 0, 'accuracy': 0},
                    'premium': {'total': 0, 'correct': 0, 'accuracy': 0},
                },
            }

        total = predictions.count()
        correct = predictions.filter(is_correct=True).count()
        accuracy = (correct / total * 100) if total > 0 else 0

        # By tier
        free_preds = predictions.filter(tier='free')
        premium_preds = predictions.filter(tier='premium')

        free_correct = free_preds.filter(is_correct=True).count()
        free_total = free_preds.count()
        free_accuracy = (free_correct / free_total * 100) if free_total > 0 else 0

        premium_correct = premium_preds.filter(is_correct=True).count()
        premium_total = premium_preds.count()
        premium_accuracy = (premium_correct / premium_total * 100) if premium_total > 0 else 0

        return {
            'total_predictions': total,
            'correct': correct,
            'accuracy_rate': round(accuracy, 1),
            'by_tier': {
                'free': {
                    'total': free_total,
                    'correct': free_correct,
                    'accuracy': round(free_accuracy, 1),
                },
                'premium': {
                    'total': premium_total,
                    'correct': premium_correct,
                    'accuracy': round(premium_accuracy, 1),
                },
            },
            'period_days': days,
        }

    @classmethod
    def search_teams(cls, query: str, league=None) -> list:
        """
        Search teams by name.

        Returns list of teams matching query.
        """
        qs = Team.objects.all()

        if league:
            qs = qs.filter(league=league)

        # Search by name or short_name
        qs = qs.filter(
            Q(name__icontains=query) | Q(short_name__icontains=query)
        ).order_by('name')[:20]

        return [
            {
                'id': str(team.id),
                'name': team.name,
                'short_name': team.short_name,
                'logo_url': team.logo_url,
                'league': {
                    'id': str(team.league.id),
                    'name': team.league.name,
                    'code': team.league.code,
                } if team.league else None,
            }
            for team in qs
        ]
