"""
Ensemble Service.

High-level service that wires the Model Registry + Explainer
into the prediction pipeline. Called by PredictionService.
"""

import logging
from typing import Optional

from apps.predictions.services.model_registry import ModelRegistry
from apps.predictions.services.explainer import ExplainerService
from apps.predictions.ml.feature_store import FeatureStore

logger = logging.getLogger(__name__)


class EnsembleService:
    """
    Provides a clean interface for the prediction pipeline to call
    the ML ensemble and get explained results.
    """

    @classmethod
    def predict_match(cls, match) -> dict:
        """
        Generate a prediction for a match using the active ensemble.

        Returns:
            {
                'probabilities': {'home': float, 'draw': float, 'away': float},
                'predicted_outcome': str,
                'confidence': float,
                'explanations': list[dict],
                'features': dict,
                'model_contributions': dict,
            }
        """
        # 1. Get features (from cache or generate)
        features = FeatureStore.get_or_generate(match)
        if not features:
            logger.warning(f"No features generated for match {match.pk}")
            return cls._fallback_prediction(match)

        # 2. Get active ensemble
        ensemble = ModelRegistry.get_active_ensemble()

        # 3. Run prediction
        result = ensemble.predict(features)

        # 4. Generate explanations
        xgb_model = ensemble.xgboost if ensemble.xgboost else None
        explanations = ExplainerService.explain(
            features=features,
            prediction_result=result,
            xgboost_model=xgb_model,
            top_n=5,
        )

        return {
            'probabilities': {
                'home': result['home_win_prob'],
                'draw': result['draw_prob'],
                'away': result['away_win_prob'],
            },
            'predicted_outcome': result['predicted_outcome'],
            'confidence': result['confidence'],
            'explanations': explanations,
            'features': features,
            'model_contributions': result.get('model_contributions', {}),
        }

    @classmethod
    def _fallback_prediction(cls, match) -> dict:
        """
        Simple fallback when features can't be generated.
        Uses ELO ratings directly.
        """
        from apps.predictions.ml.models.ensemble_model import ELOPredictor

        elo = ELOPredictor()
        features = {
            'home_elo': match.home_team.elo_rating,
            'away_elo': match.away_team.elo_rating,
        }
        result = elo.predict(features)

        return {
            'probabilities': {
                'home': result['home_win_prob'],
                'draw': result['draw_prob'],
                'away': result['away_win_prob'],
            },
            'predicted_outcome': result['predicted_outcome'],
            'confidence': result['confidence'],
            'explanations': [{
                'factor_name': 'Team Ratings (ELO)',
                'factor_value': f"Home: {match.home_team.elo_rating:.0f} vs Away: {match.away_team.elo_rating:.0f}",
                'impact_score': 0.5,
                'impact_direction': 'positive',
                'display_order': 1,
            }],
            'features': features,
            'model_contributions': {'elo': {'weight': 1.0, 'prediction': result['predicted_outcome']}},
        }
