"""
Ensemble Model – Combines XGBoost, Neural Network, and ELO predictions.

Uses weighted averaging with configurable weights from settings.ML_CONFIG.
"""

import logging
from typing import Optional

import numpy as np
from django.conf import settings

from .xgboost_model import XGBoostPredictor
from .neural_model import NeuralPredictor

logger = logging.getLogger(__name__)

OUTCOME_LABELS = {0: 'home_win', 1: 'draw', 2: 'away_win'}


class ELOPredictor:
    """
    ELO-based probability estimator.

    Doesn't need training — computes probabilities directly from
    ELO ratings using the logistic distribution.
    """

    HOME_ADVANTAGE = 65   # ~65 ELO points worth of home advantage
    DRAW_MARGIN = 0.08    # Probability mass allocated to draws

    def predict(self, features: dict) -> dict:
        """
        Calculate outcome probabilities from ELO ratings.
        """
        home_elo = features.get('home_elo', 1500)
        away_elo = features.get('away_elo', 1500)

        # Apply home advantage
        adjusted_diff = (home_elo + self.HOME_ADVANTAGE) - away_elo

        # Cold start noise for default/identical ELOs (likely new teams)
        if home_elo == 1500 and away_elo == 1500:
            import random
            # Simulate skill difference (-120 to +120) for variety
            noise = random.randint(-120, 120)
            adjusted_diff += noise


        # Logistic curve
        home_expected = 1.0 / (1.0 + 10 ** (-adjusted_diff / 400))
        away_expected = 1.0 - home_expected

        # Carve out draw probability from the expected scores
        draw_boost = self.DRAW_MARGIN * (1 - abs(home_expected - away_expected))
        p_home = max(0.05, home_expected - draw_boost / 2)
        p_away = max(0.05, away_expected - draw_boost / 2)
        p_draw = max(0.05, 1.0 - p_home - p_away)

        # Normalize
        total = p_home + p_draw + p_away
        p_home /= total
        p_draw /= total
        p_away /= total

        probs = [p_home, p_draw, p_away]
        predicted_class = int(np.argmax(probs))

        return {
            'home_win_prob': round(p_home, 4),
            'draw_prob': round(p_draw, 4),
            'away_win_prob': round(p_away, 4),
            'predicted_outcome': OUTCOME_LABELS[predicted_class],
            'confidence': round(float(max(probs)) * 100, 2),
        }


class EnsemblePredictor:
    """
    Weighted ensemble that combines multiple model predictions.

    Default weights (from settings.ML_CONFIG['ENSEMBLE_WEIGHTS']):
        xgboost: 0.4
        neural:  0.3
        bayesian: 0.2  (placeholder – uses ELO for now)
        elo:     0.1
    """

    def __init__(self):
        ml_config = getattr(settings, 'ML_CONFIG', {})
        weights = ml_config.get('ENSEMBLE_WEIGHTS', {})

        self.weights = {
            'xgboost': weights.get('xgboost', 0.4),
            'neural': weights.get('neural', 0.3),
            'elo': weights.get('elo', 0.1) + weights.get('bayesian', 0.2),
        }

        self.xgboost: Optional[XGBoostPredictor] = None
        self.neural: Optional[NeuralPredictor] = None
        self.elo = ELOPredictor()

        self._loaded = False

    def load_models(self, xgboost_path: str = '', neural_path: str = '') -> None:
        """
        Load trained sub-models from disk.
        If a model fails to load, its weight is redistributed.
        """
        active_weights = {}

        # XGBoost
        if xgboost_path:
            try:
                self.xgboost = XGBoostPredictor()
                self.xgboost.load(xgboost_path)
                active_weights['xgboost'] = self.weights['xgboost']
                logger.info("XGBoost model loaded for ensemble")
            except Exception as e:
                logger.warning(f"XGBoost load failed: {e}")
                self.xgboost = None

        # Neural
        if neural_path:
            try:
                self.neural = NeuralPredictor()
                self.neural.load(neural_path)
                active_weights['neural'] = self.weights['neural']
                logger.info("Neural model loaded for ensemble")
            except Exception as e:
                logger.warning(f"Neural load failed: {e}")
                self.neural = None

        # ELO always available
        active_weights['elo'] = self.weights['elo']

        # Redistribute weights if models missing
        if not self.xgboost:
            active_weights['elo'] += self.weights['xgboost']
        if not self.neural:
            active_weights['elo'] += self.weights['neural']

        # Normalize
        total_weight = sum(active_weights.values())
        self.weights = {k: v / total_weight for k, v in active_weights.items()}
        self._loaded = True

        logger.info(f"Ensemble weights: {self.weights}")

    def predict(self, features: dict) -> dict:
        """
        Generate ensemble prediction by weighted average of sub-models.

        Returns:
            {
                'home_win_prob': float,
                'draw_prob': float,
                'away_win_prob': float,
                'predicted_outcome': str,
                'confidence': float,
                'model_contributions': dict,
            }
        """
        contributions = {}
        weighted_probs = np.zeros(3)

        # ELO (always available)
        elo_pred = self.elo.predict(features)
        elo_weight = self.weights.get('elo', 0.3)
        elo_probs = np.array([
            elo_pred['home_win_prob'],
            elo_pred['draw_prob'],
            elo_pred['away_win_prob'],
        ])
        weighted_probs += elo_probs * elo_weight
        contributions['elo'] = {
            'weight': elo_weight,
            'prediction': elo_pred['predicted_outcome'],
        }

        # XGBoost
        if self.xgboost:
            try:
                xgb_pred = self.xgboost.predict(features)
                xgb_weight = self.weights.get('xgboost', 0.0)
                xgb_probs = np.array([
                    xgb_pred['home_win_prob'],
                    xgb_pred['draw_prob'],
                    xgb_pred['away_win_prob'],
                ])
                weighted_probs += xgb_probs * xgb_weight
                contributions['xgboost'] = {
                    'weight': xgb_weight,
                    'prediction': xgb_pred['predicted_outcome'],
                }
            except Exception as e:
                logger.error(f"XGBoost prediction error: {e}")
                weighted_probs += elo_probs * self.weights.get('xgboost', 0.0)

        # Neural
        if self.neural:
            try:
                nn_pred = self.neural.predict(features)
                nn_weight = self.weights.get('neural', 0.0)
                nn_probs = np.array([
                    nn_pred['home_win_prob'],
                    nn_pred['draw_prob'],
                    nn_pred['away_win_prob'],
                ])
                weighted_probs += nn_probs * nn_weight
                contributions['neural'] = {
                    'weight': nn_weight,
                    'prediction': nn_pred['predicted_outcome'],
                }
            except Exception as e:
                logger.error(f"Neural prediction error: {e}")
                weighted_probs += elo_probs * self.weights.get('neural', 0.0)

        # Normalize
        total = weighted_probs.sum()
        if total > 0:
            weighted_probs /= total

        predicted_class = int(np.argmax(weighted_probs))
        confidence = float(np.max(weighted_probs)) * 100

        return {
            'home_win_prob': round(float(weighted_probs[0]), 4),
            'draw_prob': round(float(weighted_probs[1]), 4),
            'away_win_prob': round(float(weighted_probs[2]), 4),
            'predicted_outcome': OUTCOME_LABELS[predicted_class],
            'confidence': round(min(confidence, 95.0), 2),
            'model_contributions': contributions,
        }
