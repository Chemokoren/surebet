"""
Training Pipeline.

Orchestrates end-to-end model training:
    1. Collect historical match data
    2. Generate features for all finished matches
    3. Train XGBoost + Neural models
    4. Evaluate and register new model version
    5. Detect accuracy drift and trigger alerts
"""

import logging
from datetime import timedelta
from typing import Optional

import numpy as np
import pandas as pd
from django.conf import settings
from django.utils import timezone

from apps.core.models import Match, League
from apps.core.services.feature_engineering import FeatureEngineeringService
from apps.predictions.models import ModelVersion, Prediction

logger = logging.getLogger(__name__)


class TrainingPipeline:
    """
    End-to-end ML training orchestrator.
    """

    MIN_TRAINING_SAMPLES = 200

    @classmethod
    def run_full_training(cls, seasons: int = 3) -> Optional[ModelVersion]:
        """
        Execute full training pipeline:
        1. Collect historical data
        2. Generate feature matrix
        3. Train models
        4. Evaluate
        5. Register new ModelVersion if improved
        """
        logger.info("=== Starting full training pipeline ===")

        # Step 1: Collect data
        X, y = cls._build_training_dataset(seasons)
        if X is None or len(X) < cls.MIN_TRAINING_SAMPLES:
            logger.warning(
                f"Insufficient training data: {len(X) if X is not None else 0} samples "
                f"(need {cls.MIN_TRAINING_SAMPLES})"
            )
            return None

        logger.info(f"Training dataset: {len(X)} samples, {len(X.columns)} features")

        # Step 2: Train models
        metrics = cls._train_models(X, y)

        # Step 3: Register if improved
        model_version = cls._register_if_improved(metrics)

        logger.info("=== Training pipeline complete ===")
        return model_version

    @classmethod
    def run_incremental_update(cls, days: int = 30) -> Optional[ModelVersion]:
        """
        Retrain on recent data only (last N days).
        Faster than full training, suitable for weekly retraining.
        """
        logger.info(f"Starting incremental training (last {days} days)")

        cutoff = timezone.now() - timedelta(days=days)
        matches = Match.objects.filter(
            status='finished',
            home_score__isnull=False,
            match_date__gte=cutoff,
        ).select_related('home_team', 'away_team', 'league')

        if matches.count() < 50:
            logger.warning("Not enough recent matches for incremental training")
            return None

        X, y = cls._matches_to_features(matches)
        if X is None:
            return None

        metrics = cls._train_models(X, y)
        return cls._register_if_improved(metrics)

    # ── Data Collection ─────────────────────────

    @classmethod
    def _build_training_dataset(cls, seasons: int) -> tuple:
        """Build feature matrix from N seasons of historical data."""
        cutoff_days = seasons * 365
        cutoff = timezone.now() - timedelta(days=cutoff_days)

        matches = Match.objects.filter(
            status='finished',
            home_score__isnull=False,
            match_date__gte=cutoff,
            league__is_active=True,
        ).select_related('home_team', 'away_team', 'league').order_by('match_date')

        logger.info(f"Found {matches.count()} finished matches for training")
        return cls._matches_to_features(matches)

    @classmethod
    def _matches_to_features(cls, matches) -> tuple:
        """Convert match queryset to (X, y) DataFrames."""
        rows = []
        outcomes = []

        for match in matches:
            try:
                features = FeatureEngineeringService.generate_features(match)
                if not features:
                    continue

                outcome = match.actual_outcome
                if outcome is None:
                    continue

                rows.append(features)
                outcomes.append(outcome)
            except Exception as e:
                logger.debug(f"Skip match {match.id}: {e}")
                continue

        if not rows:
            return None, None

        X = pd.DataFrame(rows).fillna(0)
        y = pd.Series(outcomes)

        logger.info(f"Feature matrix: {X.shape}, outcome distribution: {y.value_counts().to_dict()}")
        return X, y

    # ── Model Training ──────────────────────────

    @classmethod
    def _train_models(cls, X: pd.DataFrame, y: pd.Series) -> dict:
        """Train all sub-models and return metrics."""
        all_metrics = {}

        # XGBoost
        try:
            from apps.predictions.ml.models.xgboost_model import XGBoostPredictor
            xgb = XGBoostPredictor()
            xgb_metrics = xgb.train(X, y)
            all_metrics['xgboost'] = xgb_metrics

            model_path = cls._model_path('xgboost_latest.joblib')
            xgb.save(model_path)
        except Exception as e:
            logger.error(f"XGBoost training failed: {e}")

        # Neural
        try:
            from apps.predictions.ml.models.neural_model import NeuralPredictor
            nn = NeuralPredictor(use_tensorflow=True)
            nn_metrics = nn.train(X, y)
            all_metrics['neural'] = nn_metrics

            model_path = cls._model_path('neural_latest.joblib')
            nn.save(model_path)
        except Exception as e:
            logger.error(f"Neural training failed: {e}")

        return all_metrics

    # ── Model Registration ──────────────────────

    @classmethod
    def _register_if_improved(cls, metrics: dict) -> Optional[ModelVersion]:
        """
        Compare new metrics against the current active model.
        Register a new version if accuracy improved.
        """
        current = ModelVersion.objects.filter(is_active=True).first()
        current_accuracy = 0.0
        if current and current.metrics:
            current_accuracy = current.metrics.get('accuracy', 0.0)

        # Pick best sub-model accuracy
        best_accuracy = max(
            (m.get('accuracy', 0) for m in metrics.values()),
            default=0,
        )

        avg_accuracy = np.mean([m.get('accuracy', 0) for m in metrics.values()])

        if avg_accuracy > current_accuracy or current is None:
            # Deactivate old
            ModelVersion.objects.filter(is_active=True).update(is_active=False)

            # Create new version
            version_num = ModelVersion.objects.count() + 1
            new_version = ModelVersion.objects.create(
                name=f'ensemble_v{version_num}',
                version=str(version_num),
                model_type='ensemble',
                metrics={
                    'accuracy': round(avg_accuracy, 4),
                    'sub_models': metrics,
                },
                is_active=True,
                trained_at=timezone.now(),
            )
            logger.info(
                f"New model registered: {new_version.name} "
                f"(accuracy: {avg_accuracy:.4f} vs previous: {current_accuracy:.4f})"
            )
            return new_version
        else:
            logger.info(
                f"New model not better: {avg_accuracy:.4f} <= {current_accuracy:.4f}"
            )
            return None

    # ── Drift Detection ─────────────────────────

    @classmethod
    def check_accuracy_drift(cls, threshold: float = 0.05) -> dict:
        """
        Compare recent prediction accuracy against historical baseline.

        Returns:
            {
                'drift_detected': bool,
                'current_accuracy': float,
                'baseline_accuracy': float,
                'degradation': float,
                'recommendation': str,
            }
        """
        # Recent (last 7 days)
        recent_cutoff = timezone.now() - timedelta(days=7)
        recent = Prediction.objects.filter(
            is_correct__isnull=False,
            resolved_at__gte=recent_cutoff,
        )
        recent_total = recent.count()
        if recent_total < 20:
            return {
                'drift_detected': False,
                'message': f'Insufficient recent data ({recent_total} predictions)',
            }

        recent_correct = recent.filter(is_correct=True).count()
        current_accuracy = recent_correct / recent_total

        # Baseline (all time)
        all_resolved = Prediction.objects.filter(is_correct__isnull=False)
        baseline_total = all_resolved.count()
        baseline_correct = all_resolved.filter(is_correct=True).count()
        baseline_accuracy = baseline_correct / baseline_total if baseline_total > 0 else 0

        degradation = baseline_accuracy - current_accuracy
        drift_detected = degradation > threshold

        result = {
            'drift_detected': drift_detected,
            'current_accuracy': round(current_accuracy, 4),
            'baseline_accuracy': round(baseline_accuracy, 4),
            'degradation': round(degradation, 4),
            'recent_samples': recent_total,
            'recommendation': '',
        }

        if drift_detected:
            result['recommendation'] = (
                f"Accuracy dropped by {degradation:.1%}. "
                f"Recommend retraining the model with recent data."
            )
            logger.warning(f"DRIFT DETECTED: {result}")
        else:
            result['recommendation'] = 'Model accuracy is within acceptable range.'

        return result

    # ── Utilities ────────────────────────────────

    @staticmethod
    def _model_path(filename: str) -> str:
        """Get file path for model storage."""
        import os
        ml_config = getattr(settings, 'ML_CONFIG', {})
        base = ml_config.get('MODEL_REGISTRY_PATH', os.path.join(settings.BASE_DIR, 'models'))
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, filename)
