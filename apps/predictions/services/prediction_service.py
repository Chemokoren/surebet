"""
Prediction Service.

Orchestrates the prediction pipeline:
1.  Fetches scheduled matches for the day.
2.  Generates feature vectors using FeatureEngineeringService.
3.  Runs the active ML ensemble to get probabilities.
4.  Fetches consensus signals from external sources / historical data.
5.  Blends ML output with consensus for improved calibration.
6.  Stores Prediction records with confidence scores and explanations.
7.  Assigns tiers (Free vs Premium) based on confidence/strategy.
"""

import logging
import random
from datetime import date
from typing import List

from django.db import transaction
from django.utils import timezone

from apps.core.models import Match
from apps.core.services.feature_engineering import FeatureEngineeringService
from apps.predictions.models import Prediction, ModelVersion, PredictionExplanation

logger = logging.getLogger(__name__)


class PredictionService:
    """
    Core service for generating and managing predictions.
    """

    @classmethod
    def generate_daily_predictions(cls, target_date: date = None) -> int:
        """
        Generate predictions for all scheduled matches on target_date.
        Returns count of predictions created.
        """
        if target_date is None:
            target_date = timezone.now().date()

        matches = Match.objects.filter(
            match_date__date=target_date,
            status='scheduled',
            prediction__isnull=True,  # Only generate if missing
        ).select_related('home_team', 'away_team', 'league')

        if not matches.exists():
            logger.info(f"No pending matches found for {target_date}")
            return 0

        # Get active model version
        model_version = ModelVersion.objects.filter(is_active=True).first()
        if not model_version:
            # Fallback/Default if no model is trained yet
            model_version, _ = ModelVersion.objects.get_or_create(
                name='system_default',
                version='1.0',
                defaults={'is_active': True, 'model_type': 'ensemble'}
            )

        count = 0
        for match in matches:
            try:
                cls._generate_single_prediction(match, model_version)
                count += 1
            except Exception as e:
                logger.error(f"Failed to predict match {match.id}: {e}")

        logger.info(f"Generated {count} predictions for {target_date}")
        return count

    @classmethod
    def _generate_single_prediction(cls, match: Match, model_version: ModelVersion):
        """
        Generate prediction for a single match using the ML ensemble.
        Falls back to heuristic if ensemble is unavailable.
        """
        # Try the full ML ensemble pipeline first
        try:
            from apps.predictions.services.ensemble import EnsembleService
            result = EnsembleService.predict_match(match)
        except Exception as e:
            logger.warning(f"Ensemble unavailable, using heuristic fallback: {e}")
            result = cls._heuristic_fallback(match)

        probs = result['probabilities']
        outcome = result['predicted_outcome']
        confidence = result['confidence']
        features = result.get('features', {})
        explanations_data = result.get('explanations', [])

        # ── Intelligence Engine: blend with external source consensus ──
        try:
            # Try the full learning engine first (supervised + unsupervised)
            from apps.predictions.services.learning_engine import LearningEngine
            intelligence = LearningEngine.get_weighted_consensus(match)

            if intelligence:
                # Blend: 70% our model + 30% intelligence (higher trust for vetted sources)
                blend_w = 0.30
                blended = {
                    'home': (1 - blend_w) * probs['home'] + blend_w * intelligence['home_win_prob'],
                    'draw': (1 - blend_w) * probs['draw'] + blend_w * intelligence['draw_prob'],
                    'away': (1 - blend_w) * probs['away'] + blend_w * intelligence['away_win_prob'],
                }
                total = blended['home'] + blended['draw'] + blended['away']
                probs = {k: v / total for k, v in blended.items()}

                features['intelligence'] = {
                    'engine': 'learning_engine',
                    'sources_used': intelligence['sources_used'],
                    'agreement_ratio': intelligence['agreement_ratio'],
                    'outliers': intelligence.get('outliers', []),
                    'intel_home': intelligence['home_win_prob'],
                    'intel_draw': intelligence['draw_prob'],
                    'intel_away': intelligence['away_win_prob'],
                }
                logger.debug(
                    f"Intelligence Engine blended for {match}: "
                    f"sources={intelligence['sources_used']}, "
                    f"agreement={intelligence['agreement_ratio']}"
                )
            else:
                # Fallback: basic consensus (ESPN + H2H + ELO)
                from apps.predictions.services.consensus import ConsensusService
                consensus = ConsensusService.get_consensus(match)
                if consensus:
                    probs = ConsensusService.blend_with_model(probs, consensus)
                    features['consensus'] = {
                        'engine': 'basic_consensus',
                        'sources_used': consensus['sources_used'],
                        'consensus_home': consensus['home_win_prob'],
                        'consensus_draw': consensus['draw_prob'],
                        'consensus_away': consensus['away_win_prob'],
                        'consensus_confidence': consensus['confidence'],
                        'source_details': consensus.get('source_details', []),
                    }

            # Re-derive outcome from blended probabilities
            if probs['home'] > probs['away'] and probs['home'] > probs['draw']:
                outcome = 'home_win'
                confidence = probs['home'] * 100
            elif probs['away'] > probs['home'] and probs['away'] > probs['draw']:
                outcome = 'away_win'
                confidence = probs['away'] * 100
            else:
                outcome = 'draw'
                confidence = probs['draw'] * 100
            confidence = min(round(confidence, 2), 95.0)

        except Exception as e:
            logger.warning(f"Intelligence blending skipped for {match}: {e}")

        # Create Prediction Record
        with transaction.atomic():
            prediction = Prediction.objects.create(
                match=match,
                model_version=model_version,
                home_win_prob=probs['home'],
                draw_prob=probs['draw'],
                away_win_prob=probs['away'],
                predicted_outcome=outcome,
                confidence_score=confidence,
                tier=cls._determine_tier(confidence, match.league),
                feature_snapshot=features,
            )

            # Create explanations from ML pipeline
            cls._create_explanations(prediction, explanations_data)

    @classmethod
    def _heuristic_fallback(cls, match: Match) -> dict:
        """
        Simple heuristic prediction when ML models aren't trained yet.
        Uses ELO + form to generate reasonable probabilities, then
        builds comprehensive explanations via the ExplainerService.
        """
        features = FeatureEngineeringService.generate_features(match)
        elo_diff  = features.get('elo_diff', 0)
        home_form = features.get('home_form_index', 0)
        away_form = features.get('away_form_index', 0)

        # Base probabilities
        p_home = 0.35
        p_away = 0.35
        p_draw = 0.30

        # Cold Start Randomness
        if home_form == 0 and away_form == 0:
            home_bias = random.random() * 0.15
            p_home += home_bias
            p_draw -= home_bias / 2
            p_away -= home_bias / 2

        # Apply features
        p_home += (elo_diff / 1000.0) + (home_form * 0.08)
        p_away -= (elo_diff / 1000.0) - (away_form * 0.08)

        # Add general noise
        p_home += random.uniform(-0.05, 0.05)
        p_away += random.uniform(-0.05, 0.05)

        # Re-calculate draw to sum to 1 before normalization
        p_draw = 1.0 - p_home - p_away

        # Normalize with floor
        total = max(p_home + p_away + p_draw, 0.01)
        p_home = max(p_home / total, 0.10)
        p_away = max(p_away / total, 0.10)
        p_draw = max(p_draw / total, 0.10)

        total = p_home + p_away + p_draw
        p_home /= total
        p_away /= total
        p_draw /= total

        if p_home > p_away and p_home > p_draw:
            outcome, confidence = 'home_win', p_home * 100
        elif p_away > p_home and p_away > p_draw:
            outcome, confidence = 'away_win', p_away * 100
        else:
            outcome, confidence = 'draw', p_draw * 100

        confidence = min(confidence, 95.0)

        # Generate comprehensive explanations via ExplainerService
        from apps.predictions.services.explainer import ExplainerService
        prediction_result = {
            'predicted_outcome': outcome,
            'confidence': round(confidence, 2),
            'probabilities': {'home': round(p_home, 4), 'draw': round(p_draw, 4), 'away': round(p_away, 4)},
        }
        explanations = ExplainerService.explain(
            features=features,
            prediction_result=prediction_result,
            xgboost_model=None,
            top_n=8,
            home_team_name=match.home_team.name,
            away_team_name=match.away_team.name,
        )

        return {
            'probabilities': {
                'home': round(p_home, 4),
                'draw': round(p_draw, 4),
                'away': round(p_away, 4),
            },
            'predicted_outcome': outcome,
            'confidence': round(confidence, 2),
            'features': features,
            'explanations': explanations,
        }

    @classmethod
    def _determine_tier(cls, confidence: float, league) -> str:
        """
        Assign prediction tier.
        High confidence (>70%) usually Premium.
        High profile leagues usually Premium.
        """
        if confidence > 75.0:
            return 'premium'
        
        # 20% random chance of being free even if good, to hook users
        if random.random() < 0.2:
            return 'free'
            
        return 'premium'

    @classmethod
    def _create_explanations(cls, prediction: Prediction, explanations_data: list):
        """
        Create PredictionExplanation records from structured explanation data.
        """
        if not explanations_data:
            return

        explanations = []
        for exp in explanations_data:
            explanations.append(PredictionExplanation(
                prediction=prediction,
                factor_name=exp.get('factor_name', 'Unknown'),
                factor_value=exp.get('factor_value', ''),
                impact_score=exp.get('impact_score', 0.0),
                impact_direction=exp.get('impact_direction', 'neutral'),
                display_order=exp.get('display_order', 0),
            ))

        if explanations:
            PredictionExplanation.objects.bulk_create(explanations)

