"""
Explainer Service.

Generates human-readable, SHAP-based explanations for predictions.
Falls back to feature-importance heuristics if SHAP is unavailable.
"""

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class ExplainerService:
    """
    Generates data-driven explanations for each prediction.

    Uses SHAP values when a tree-based model (XGBoost) is available,
    otherwise falls back to feature-importance ranking.
    """

    # Feature name → human-readable label
    FEATURE_LABELS = {
        'elo_diff': 'Team Strength (ELO Rating)',
        'home_elo': 'Home Team Rating',
        'away_elo': 'Away Team Rating',
        'elo_home_expected': 'ELO Win Expectation',
        'home_form_index': 'Home Team Form',
        'away_form_index': 'Away Team Form',
        'home_win_rate': 'Home Win Rate (Recent)',
        'away_win_rate': 'Away Win Rate (Recent)',
        'home_home_win_rate': 'Home Record (at Home)',
        'away_away_win_rate': 'Away Record (on Road)',
        'home_advantage': 'Home Advantage',
        'h2h_home_dominance': 'Head-to-Head Record',
        'h2h_total': 'Head-to-Head History',
        'home_matches_7d': 'Home Schedule Congestion (7d)',
        'away_matches_7d': 'Away Schedule Congestion (7d)',
        'home_avg_goals_scored': 'Home Goals per Game',
        'away_avg_goals_scored': 'Away Goals per Game',
        'home_avg_goals_conceded': 'Home Goals Conceded',
        'away_avg_goals_conceded': 'Away Goals Conceded',
        'home_over25_rate': 'Home Over 2.5 Goals Rate',
        'away_over25_rate': 'Away Over 2.5 Goals Rate',
        'league_home_win_rate': 'League Home Win Trend',
        'league_draw_rate': 'League Draw Rate',
        'league_avg_goals': 'League Goals Average',
    }

    @classmethod
    def explain(cls, features: dict, prediction_result: dict,
                xgboost_model=None, top_n: int = 8,
                home_team_name: str = 'Home', away_team_name: str = 'Away') -> list[dict]:
        """
        Generate explanation factors for a prediction.

        Args:
            features: The feature dict used for prediction
            prediction_result: Output from ensemble predict()
            xgboost_model: Optional XGBoostPredictor (for SHAP)
            top_n: Number of top factors to return
            home_team_name: Display name for the home team
            away_team_name: Display name for the away team

        Returns:
            List of explanation dicts (always 6-8 entries):
            [
                {
                    'factor_name': str,
                    'factor_value': str,
                    'impact_score': float,
                    'impact_direction': 'positive'|'negative'|'neutral',
                    'display_order': int,
                },
                ...
            ]
        """
        # Try SHAP first
        if xgboost_model is not None:
            try:
                return cls._shap_explanation(features, prediction_result, xgboost_model, top_n)
            except Exception as e:
                logger.warning(f"SHAP explanation failed, falling back to heuristics: {e}")

        # Always-comprehensive heuristic fallback
        return cls._heuristic_explanation(
            features, prediction_result, top_n,
            home_team_name=home_team_name,
            away_team_name=away_team_name,
        )

    @classmethod
    def _shap_explanation(cls, features: dict, prediction_result: dict,
                          xgboost_model, top_n: int) -> list[dict]:
        """Generate SHAP-based explanations."""
        import shap
        import pandas as pd

        feature_vector = pd.DataFrame([features])
        for col in xgboost_model.feature_names:
            if col not in feature_vector.columns:
                feature_vector[col] = 0.0
        feature_vector = feature_vector[xgboost_model.feature_names]

        explainer = shap.TreeExplainer(xgboost_model.model)
        shap_values = explainer.shap_values(feature_vector)

        # Get predicted outcome index
        outcome = prediction_result.get('predicted_outcome', 'home_win')
        outcome_idx = {'home_win': 0, 'draw': 1, 'away_win': 2}.get(outcome, 0)

        # Extract SHAP values for the predicted class
        if isinstance(shap_values, list):
            values = shap_values[outcome_idx][0]
        else:
            values = shap_values[0, :, outcome_idx]

        # Sort by absolute impact
        indices = np.argsort(np.abs(values))[::-1][:top_n]

        explanations = []
        for order, idx in enumerate(indices):
            feature_name = xgboost_model.feature_names[idx]
            shap_val = float(values[idx])
            raw_val = features.get(feature_name, 0)

            explanations.append({
                'factor_name': cls.FEATURE_LABELS.get(feature_name, feature_name),
                'factor_value': cls._format_value(feature_name, raw_val),
                'impact_score': round(shap_val, 4),
                'impact_direction': 'positive' if shap_val > 0 else (
                    'negative' if shap_val < 0 else 'neutral'
                ),
                'display_order': order + 1,
            })

        return explanations

    @classmethod
    def _heuristic_explanation(cls, features: dict, prediction_result: dict,
                               top_n: int = 8,
                               home_team_name: str = 'Home',
                               away_team_name: str = 'Away') -> list[dict]:
        """
        Generate comprehensive, narrative explanations from feature values.
        Always produces 8 factors — never shows a blank section.
        Factors are always populated regardless of data sparsity.
        """
        outcome = prediction_result.get('predicted_outcome', 'home_win')
        explanations = []

        # ── 1. ELO / Team Strength ─────────────────────────────────────
        elo_diff  = features.get('elo_diff', 0)
        home_elo  = features.get('home_elo', 1500)
        away_elo  = features.get('away_elo', 1500)

        if abs(elo_diff) > 150:
            stronger = home_team_name if elo_diff > 0 else away_team_name
            value = (f"{stronger} holds a significant {abs(elo_diff):.0f}-point ELO advantage "
                     f"({home_elo:.0f} vs {away_elo:.0f})")
            direction = cls._home_or_away_direction(elo_diff > 0, outcome)
            impact = min(abs(elo_diff) / 400, 0.90)
        elif abs(elo_diff) > 40:
            stronger = home_team_name if elo_diff > 0 else away_team_name
            value = (f"{stronger} holds a slight {abs(elo_diff):.0f}-point ELO edge "
                     f"({home_elo:.0f} vs {away_elo:.0f})")
            direction = cls._home_or_away_direction(elo_diff > 0, outcome)
            impact = min(abs(elo_diff) / 600, 0.45)
        else:
            value = f"Evenly matched teams — ratings near-identical ({home_elo:.0f} vs {away_elo:.0f})"
            direction = 'neutral'
            impact = 0.15

        explanations.append({
            'factor_name': 'Team Strength (ELO)',
            'factor_value': value,
            'impact_score': round(impact, 4),
            'impact_direction': direction,
            'display_order': 1,
        })

        # ── 2. Home Team Recent Form ───────────────────────────────────
        home_form   = features.get('home_form_index', 0)
        home_wins   = int(features.get('home_wins_last_10', 0))
        home_draws  = int(features.get('home_draws_last_10', 0))
        home_losses = int(features.get('home_losses_last_10', 0))
        total_home  = home_wins + home_draws + home_losses

        if total_home == 0:
            value = f"{home_team_name} — no recent match data available"
            direction, impact = 'neutral', 0.10
        elif home_form >= 0.67:
            value = (f"In red-hot form — {home_wins}W {home_draws}D {home_losses}L "
                     f"in last {total_home} ({home_form:.0%} points ratio)")
            direction = 'positive' if outcome == 'home_win' else 'negative'
            impact = round(home_form * 0.85, 4)
        elif home_form >= 0.45:
            value = (f"Consistent but not spectacular — {home_wins}W {home_draws}D {home_losses}L "
                     f"in last {total_home}")
            direction = 'neutral'
            impact = 0.38
        else:
            value = (f"Struggling for form — {home_wins}W {home_draws}D {home_losses}L "
                     f"in last {total_home} ({home_form:.0%} points ratio)")
            direction = 'negative' if outcome == 'home_win' else 'positive'
            impact = round((1 - home_form) * 0.70, 4)

        explanations.append({
            'factor_name': f'{home_team_name} Recent Form',
            'factor_value': value,
            'impact_score': impact,
            'impact_direction': direction,
            'display_order': 2,
        })

        # ── 3. Away Team Recent Form ───────────────────────────────────
        away_form   = features.get('away_form_index', 0)
        away_wins   = int(features.get('away_wins_last_10', 0))
        away_draws  = int(features.get('away_draws_last_10', 0))
        away_losses = int(features.get('away_losses_last_10', 0))
        total_away  = away_wins + away_draws + away_losses

        if total_away == 0:
            value = f"{away_team_name} — no recent match data available"
            direction, impact = 'neutral', 0.10
        elif away_form >= 0.67:
            value = (f"Arriving in excellent form — {away_wins}W {away_draws}D {away_losses}L "
                     f"in last {total_away} ({away_form:.0%} points ratio)")
            direction = 'positive' if outcome == 'away_win' else 'negative'
            impact = round(away_form * 0.80, 4)
        elif away_form >= 0.45:
            value = (f"Steady performances — {away_wins}W {away_draws}D {away_losses}L "
                     f"in last {total_away}")
            direction = 'neutral'
            impact = 0.33
        else:
            value = (f"Out of form — {away_wins}W {away_draws}D {away_losses}L "
                     f"in last {total_away} ({away_form:.0%} points ratio)")
            direction = 'negative' if outcome == 'away_win' else 'positive'
            impact = round((1 - away_form) * 0.65, 4)

        explanations.append({
            'factor_name': f'{away_team_name} Recent Form',
            'factor_value': value,
            'impact_score': impact,
            'impact_direction': direction,
            'display_order': 3,
        })

        # ── 4. Head-to-Head Record ─────────────────────────────────────
        h2h_total      = int(features.get('h2h_total', 0))
        h2h_home_wins  = int(features.get('h2h_home_wins', 0))
        h2h_away_wins  = int(features.get('h2h_away_wins', 0))
        h2h_draws_val  = int(features.get('h2h_draws', 0))
        h2h_dominance  = features.get('h2h_home_dominance', 0.5)

        if h2h_total == 0:
            value = "No previous meetings — this is a first-time fixture"
            direction, impact = 'neutral', 0.10
        elif h2h_dominance > 0.60:
            value = (f"{home_team_name} dominate this fixture — "
                     f"{h2h_home_wins}W {h2h_draws_val}D {h2h_away_wins}L across {h2h_total} meetings")
            direction = 'positive' if outcome == 'home_win' else 'negative'
            impact = round(h2h_dominance * 0.65, 4)
        elif h2h_dominance < 0.40:
            value = (f"{away_team_name} have the psychological edge — "
                     f"{h2h_home_wins}W {h2h_draws_val}D {h2h_away_wins}L across {h2h_total} meetings")
            direction = 'positive' if outcome == 'away_win' else 'negative'
            impact = round((1 - h2h_dominance) * 0.60, 4)
        elif h2h_draws_val / h2h_total > 0.40:
            value = (f"A draw-heavy rivalry — {h2h_home_wins}W {h2h_draws_val}D {h2h_away_wins}L "
                     f"across {h2h_total} meetings ({h2h_draws_val / h2h_total:.0%} draws)")
            direction = 'positive' if outcome == 'draw' else 'neutral'
            impact = 0.35
        else:
            value = (f"Closely contested fixture — {h2h_home_wins}W {h2h_draws_val}D {h2h_away_wins}L "
                     f"across {h2h_total} meetings")
            direction = 'neutral'
            impact = 0.25

        explanations.append({
            'factor_name': 'Head-to-Head Record',
            'factor_value': value,
            'impact_score': impact,
            'impact_direction': direction,
            'display_order': 4,
        })

        # ── 5. Home Ground Advantage ───────────────────────────────────
        home_home_wr = features.get('home_home_win_rate', 0)

        if home_home_wr >= 0.65:
            value = (f"{home_team_name} are a fortress at home — "
                     f"wins {home_home_wr:.0%} of home fixtures")
            direction = 'positive' if outcome == 'home_win' else 'negative'
            impact = round(home_home_wr * 0.75, 4)
        elif home_home_wr >= 0.45:
            value = (f"Solid home performers — {home_home_wr:.0%} home win rate")
            direction = 'positive' if outcome == 'home_win' else 'neutral'
            impact = 0.35
        elif home_home_wr > 0:
            value = (f"Home form is a concern — only {home_home_wr:.0%} home win rate")
            direction = 'negative' if outcome == 'home_win' else 'positive'
            impact = round((1 - home_home_wr) * 0.55, 4)
        else:
            value = f"{home_team_name} playing at home — crowd support could be decisive"
            direction = 'positive' if outcome == 'home_win' else 'neutral'
            impact = 0.20

        explanations.append({
            'factor_name': 'Home Ground Advantage',
            'factor_value': value,
            'impact_score': impact,
            'impact_direction': direction,
            'display_order': 5,
        })

        # ── 6. Away Road Record ────────────────────────────────────────
        away_away_wr = features.get('away_away_win_rate', 0)

        if away_away_wr >= 0.55:
            value = (f"{away_team_name} are strong travellers — "
                     f"wins {away_away_wr:.0%} of away games")
            direction = 'positive' if outcome == 'away_win' else 'negative'
            impact = round(away_away_wr * 0.70, 4)
        elif away_away_wr >= 0.35:
            value = f"Decent on the road — {away_away_wr:.0%} away win rate"
            direction = 'neutral'
            impact = 0.28
        elif away_away_wr > 0:
            value = (f"{away_team_name} struggle away from home — "
                     f"only {away_away_wr:.0%} away win rate")
            direction = 'negative' if outcome == 'away_win' else 'positive'
            impact = round((1 - away_away_wr) * 0.50, 4)
        else:
            value = f"{away_team_name} on the road — away fixtures are always a test"
            direction = 'neutral'
            impact = 0.15

        explanations.append({
            'factor_name': 'Away Road Record',
            'factor_value': value,
            'impact_score': impact,
            'impact_direction': direction,
            'display_order': 6,
        })

        # ── 7. Goal-Scoring Form ───────────────────────────────────────
        home_goals   = features.get('home_avg_goals_scored', 0)
        away_goals   = features.get('away_avg_goals_scored', 0)
        home_concede = features.get('home_avg_goals_conceded', 0)
        away_concede = features.get('away_avg_goals_conceded', 0)

        if home_goals > 2.2 and away_concede > 1.5:
            value = (f"High-scoring clash expected — {home_team_name} score {home_goals:.1f}/game, "
                     f"{away_team_name} concede {away_concede:.1f}/game")
            direction = 'positive' if outcome == 'home_win' else 'neutral'
            impact = min((home_goals + away_concede) / 6.0, 0.80)
        elif away_goals > 2.0 and home_concede > 1.5:
            value = (f"{away_team_name} dangerous in attack — {away_goals:.1f} goals/game, "
                     f"host concedes {home_concede:.1f}/game")
            direction = 'positive' if outcome == 'away_win' else 'negative'
            impact = min((away_goals + home_concede) / 6.0, 0.75)
        elif home_concede < 0.8 and away_concede < 0.8:
            value = (f"Defensive battle expected — both sides stingy ({home_team_name} "
                     f"concede {home_concede:.1f}, {away_team_name} {away_concede:.1f}/game)")
            direction = 'positive' if outcome == 'draw' else 'neutral'
            impact = 0.40
        elif home_goals > 0 or away_goals > 0:
            value = (f"{home_team_name} average {home_goals:.1f} goals/game vs "
                     f"{away_team_name} {away_goals:.1f} goals/game")
            direction = 'neutral'
            impact = 0.28
        else:
            value = "Goal-scoring data being compiled for these sides"
            direction, impact = 'neutral', 0.12

        explanations.append({
            'factor_name': 'Goal-Scoring Form',
            'factor_value': value,
            'impact_score': round(impact, 4),
            'impact_direction': direction,
            'display_order': 7,
        })

        # ── 8. Squad Fatigue & Schedule ───────────────────────────────
        home_cong = int(features.get('home_matches_7d', 0))
        away_cong = int(features.get('away_matches_7d', 0))

        if home_cong >= 2 and away_cong < 2:
            value = (f"{home_team_name} may carry fatigue — "
                     f"{home_cong} games in last 7 days vs {away_team_name}'s {away_cong}")
            direction = 'negative' if outcome == 'home_win' else 'positive'
            impact = min(home_cong * 0.18, 0.55)
        elif away_cong >= 2 and home_cong < 2:
            value = (f"{away_team_name} on a heavy run — "
                     f"{away_cong} games in last 7 days vs {home_team_name}'s {home_cong}")
            direction = 'negative' if outcome == 'away_win' else 'positive'
            impact = min(away_cong * 0.18, 0.50)
        elif home_cong >= 2 and away_cong >= 2:
            value = (f"Both sides fatigued — {home_team_name}: {home_cong} games, "
                     f"{away_team_name}: {away_cong} games in last 7 days")
            direction = 'neutral'
            impact = 0.25
        else:
            value = f"Both squads well-rested and fresh for this fixture"
            direction = 'positive' if outcome == 'home_win' else 'neutral'
            impact = 0.15

        explanations.append({
            'factor_name': 'Squad Fatigue & Schedule',
            'factor_value': value,
            'impact_score': round(impact, 4),
            'impact_direction': direction,
            'display_order': 8,
        })

        return explanations[:top_n]

    # ── Helpers ──────────────────────────────────

    @staticmethod
    def _home_or_away_direction(favors_home: bool, outcome: str) -> str:
        """Return impact direction given which team is favoured and what was predicted."""
        if outcome == 'home_win':
            return 'positive' if favors_home else 'negative'
        elif outcome == 'away_win':
            return 'positive' if not favors_home else 'negative'
        return 'neutral'

    @staticmethod
    def _determine_direction(feature: str, value: float, outcome: str) -> str:
        """Determine if a feature supports or opposes the predicted outcome."""
        home_positive = {
            'elo_diff': value > 0,
            'home_form_index': value > 0.5,
            'home_home_win_rate': value > 0.5,
            'home_advantage': value > 0,
            'h2h_home_dominance': value > 0.5,
            'home_avg_goals_scored': value > 1.5,
        }

        if feature in home_positive:
            favors_home = home_positive[feature]
            if outcome == 'home_win':
                return 'positive' if favors_home else 'negative'
            elif outcome == 'away_win':
                return 'negative' if favors_home else 'positive'
            else:
                return 'neutral'

        return 'neutral'

    @staticmethod
    def _estimate_impact(feature: str, value: float) -> float:
        """Estimate normalized impact score for a feature."""
        scales = {
            'elo_diff': 500.0,
            'home_form_index': 1.0,
            'away_form_index': 1.0,
            'h2h_home_dominance': 1.0,
            'home_home_win_rate': 1.0,
            'away_away_win_rate': 1.0,
            'home_advantage': 1.0,
            'home_avg_goals_scored': 3.0,
            'away_avg_goals_scored': 3.0,
        }
        scale = scales.get(feature, 1.0)
        return min(abs(value / scale), 1.0)

    @staticmethod
    def _format_value(feature: str, value) -> str:
        """Format a feature value for human display."""
        if isinstance(value, float):
            if 'rate' in feature or 'index' in feature or 'dominance' in feature:
                return f"{value:.0%}"
            elif 'elo' in feature:
                return f"{value:.0f}"
            elif 'goals' in feature:
                return f"{value:.1f} per game"
            else:
                return f"{value:.2f}"
        return str(value)
