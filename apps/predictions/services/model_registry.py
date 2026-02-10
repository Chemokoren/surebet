"""
Model Registry.

Manages ML model versions: load, activate, rollback, and A/B test.
"""

import logging
import os
from typing import Optional

from django.conf import settings

from apps.predictions.models import ModelVersion
from apps.predictions.ml.models.ensemble_model import EnsemblePredictor

logger = logging.getLogger(__name__)

# Singleton cache for the active ensemble
_active_ensemble: Optional[EnsemblePredictor] = None
_active_version_id: Optional[str] = None


class ModelRegistry:
    """
    Central registry for managing trained ML model versions.
    """

    @classmethod
    def get_active_ensemble(cls) -> EnsemblePredictor:
        """
        Return the currently active ensemble predictor.
        Caches in module-level singleton to avoid reloading on every request.
        """
        global _active_ensemble, _active_version_id

        active = ModelVersion.objects.filter(is_active=True).first()
        if active is None:
            logger.warning("No active model version found — using ELO-only fallback")
            ensemble = EnsemblePredictor()
            ensemble.load_models()  # ELO only
            return ensemble

        # Return cached if same version
        if _active_ensemble and str(active.pk) == _active_version_id:
            return _active_ensemble

        # Load fresh
        ensemble = EnsemblePredictor()
        ml_config = getattr(settings, 'ML_CONFIG', {})
        model_dir = ml_config.get(
            'MODEL_REGISTRY_PATH',
            os.path.join(settings.BASE_DIR, 'models'),
        )

        xgb_path = os.path.join(model_dir, 'xgboost_latest.joblib')
        nn_path = os.path.join(model_dir, 'neural_latest.joblib')

        ensemble.load_models(
            xgboost_path=xgb_path if os.path.exists(xgb_path) else '',
            neural_path=nn_path if os.path.exists(nn_path) else '',
        )

        _active_ensemble = ensemble
        _active_version_id = str(active.pk)

        logger.info(f"Loaded ensemble for model version: {active.name} v{active.version}")
        return ensemble

    @classmethod
    def invalidate_cache(cls) -> None:
        """Force reload on next request (after retraining)."""
        global _active_ensemble, _active_version_id
        _active_ensemble = None
        _active_version_id = None
        logger.info("Model registry cache invalidated")

    @classmethod
    def activate_version(cls, version_id: str) -> ModelVersion:
        """
        Activate a specific model version (deactivating all others).
        Used for rollback or A/B testing.
        """
        ModelVersion.objects.filter(is_active=True).update(is_active=False)
        version = ModelVersion.objects.get(pk=version_id)
        version.is_active = True
        version.save(update_fields=['is_active'])
        cls.invalidate_cache()
        logger.info(f"Activated model version: {version.name} v{version.version}")
        return version

    @classmethod
    def rollback(cls) -> Optional[ModelVersion]:
        """
        Rollback to the previous model version.
        """
        versions = ModelVersion.objects.order_by('-created_at')[:2]
        if len(versions) < 2:
            logger.warning("No previous version to rollback to")
            return None

        previous = versions[1]
        return cls.activate_version(str(previous.pk))

    @classmethod
    def list_versions(cls, limit: int = 10) -> list[dict]:
        """List recent model versions with their metrics."""
        versions = ModelVersion.objects.order_by('-created_at')[:limit]
        return [
            {
                'id': str(v.pk),
                'name': v.name,
                'version': v.version,
                'model_type': v.model_type,
                'is_active': v.is_active,
                'accuracy': v.metrics.get('accuracy', 0) if v.metrics else 0,
                'trained_at': v.trained_at,
                'created_at': v.created_at,
            }
            for v in versions
        ]
