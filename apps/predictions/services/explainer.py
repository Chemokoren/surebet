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
                xgboost_model=None, top_n: int = 5) -> list[dict]:
        """
        Generate explanation factors for a prediction.

        Args:
            features: The feature dict used for prediction
            prediction_result: Output from ensemble predict()
            xgboost_model: Optional XGBoostPredictor (for SHAP)
            top_n: Number of top factors to return

        Returns:
            List of explanation dicts:
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

        # Fallback to heuristic explanation
        return cls._heuristic_explanation(features, prediction_result, top_n)

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
                               top_n: int) -> list[dict]:
        """
        Generate explanations using feature value heuristics.
        Ranks features by their magnitude and deviation from neutral.
        """
        outcome = prediction_result.get('predicted_outcome', 'home_win')
        explanations = []

        # Key factors to evaluate
        factors = [
            ('elo_diff', 'Team Strength (ELO)', lambda v: abs(v) > 50),
            ('home_form_index', 'Home Team Recent Form', lambda v: v > 0.6 or v < 0.3),
            ('away_form_index', 'Away Team Recent Form', lambda v: v > 0.6 or v < 0.3),
            ('h2h_home_dominance', 'Head-to-Head History', lambda v: v > 0.6 or v < 0.3),
            ('home_home_win_rate', 'Home Ground Record', lambda v: v > 0.6 or v < 0.3),
            ('away_away_win_rate', 'Away Road Record', lambda v: v > 0.5),
            ('home_advantage', 'Home Advantage Factor', lambda v: abs(v) > 0.2),
            ('home_matches_7d', 'Home Schedule Congestion', lambda v: v >= 3),
            ('away_matches_7d', 'Away Schedule Congestion', lambda v: v >= 3),
            ('home_avg_goals_scored', 'Home Goal-Scoring Form', lambda v: v > 1.8 or v < 0.8),
            ('away_avg_goals_scored', 'Away Goal-Scoring Form', lambda v: v > 1.8 or v < 0.8),
            ('league_home_win_rate', 'League Home Win Trend', lambda v: v > 0.5),
        ]

        for feature_key, label, is_significant in factors:
            value = features.get(feature_key, 0)
            if not is_significant(value):
                continue

            # Determine impact direction relative to predicted outcome
            direction = cls._determine_direction(feature_key, value, outcome)
            impact = cls._estimate_impact(feature_key, value)

            explanations.append({
                'factor_name': label,
                'factor_value': cls._format_value(feature_key, value),
                'impact_score': round(impact, 4),
                'impact_direction': direction,
                'display_order': 0,
            })

        # Sort by absolute impact and take top_n
        explanations.sort(key=lambda x: abs(x['impact_score']), reverse=True)
        for i, exp in enumerate(explanations[:top_n]):
            exp['display_order'] = i + 1

        return explanations[:top_n]

    # ── Helpers ──────────────────────────────────

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
