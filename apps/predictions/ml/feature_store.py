"""
Feature Store.

Caches computed features to avoid recalculating during inference.
Uses Redis for hot features and DB for cold storage.
"""

import json
import logging
from typing import Optional

from django.core.cache import caches
from django.conf import settings

logger = logging.getLogger(__name__)


class FeatureStore:
    """
    Two-tier feature cache:
    1. Redis (hot) — for today's matches being predicted
    2. JSON snapshots in Prediction.feature_snapshot (cold) — for reproducibility
    """

    CACHE_ALIAS = 'predictions'
    CACHE_TTL = 60 * 60 * 6  # 6 hours

    @classmethod
    def _get_cache(cls):
        """Get the predictions Redis cache backend."""
        try:
            return caches[cls.CACHE_ALIAS]
        except Exception:
            return caches['default']

    @classmethod
    def cache_key(cls, match_id: str) -> str:
        """Generate cache key for a match's features."""
        return f'features:match:{match_id}'

    @classmethod
    def get(cls, match_id: str) -> Optional[dict]:
        """
        Retrieve cached features for a match.
        Returns None if not cached.
        """
        cache = cls._get_cache()
        key = cls.cache_key(match_id)

        try:
            data = cache.get(key)
            if data:
                logger.debug(f"Feature cache HIT: {match_id}")
                return json.loads(data) if isinstance(data, str) else data
        except Exception as e:
            logger.warning(f"Feature cache read error: {e}")

        return None

    @classmethod
    def put(cls, match_id: str, features: dict, ttl: int = None) -> None:
        """
        Store features in cache.
        """
        cache = cls._get_cache()
        key = cls.cache_key(match_id)
        ttl = ttl or cls.CACHE_TTL

        try:
            cache.set(key, json.dumps(features, default=str), ttl)
            logger.debug(f"Feature cache SET: {match_id}")
        except Exception as e:
            logger.warning(f"Feature cache write error: {e}")

    @classmethod
    def invalidate(cls, match_id: str) -> None:
        """Remove cached features for a match."""
        cache = cls._get_cache()
        key = cls.cache_key(match_id)
        try:
            cache.delete(key)
        except Exception:
            pass

    @classmethod
    def get_or_generate(cls, match) -> dict:
        """
        Get features from cache, or generate and cache them.
        """
        match_id = str(match.pk)
        features = cls.get(match_id)

        if features is None:
            from apps.core.services.feature_engineering import FeatureEngineeringService
            features = FeatureEngineeringService.generate_features(match)
            if features:
                cls.put(match_id, features)

        return features or {}

    @classmethod
    def warm_cache(cls, matches) -> int:
        """
        Pre-compute and cache features for a batch of matches.
        Returns count of features cached.
        """
        from apps.core.services.feature_engineering import FeatureEngineeringService

        count = 0
        for match in matches:
            try:
                features = FeatureEngineeringService.generate_features(match)
                if features:
                    cls.put(str(match.pk), features)
                    count += 1
            except Exception as e:
                logger.error(f"Feature generation error for match {match.pk}: {e}")

        logger.info(f"Warmed feature cache for {count} matches")
        return count
