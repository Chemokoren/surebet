"""
Confidence Calibration Service.

Calibrates raw prediction confidence scores so that a "70% confident"
prediction actually wins ~70% of the time.  Uses isotonic regression
(non-parametric, monotonic) on historical resolved predictions.

If not enough data exists (< 200 resolved), returns uncalibrated scores.
"""

import logging
import os
from typing import Optional

import joblib
import numpy as np
from django.conf import settings
from django.utils import timezone
from sklearn.isotonic import IsotonicRegression

logger = logging.getLogger(__name__)

CALIBRATION_MODEL_PATH = os.path.join(
    getattr(settings, 'BASE_DIR', '.'), 'models', 'calibration_isotonic.pkl'
)

MIN_SAMPLES_FOR_CALIBRATION = 200


class CalibrationService:
    """
    Confidence calibration using isotonic regression.

    Usage:
        CalibrationService.fit()              # Train on resolved predictions
        CalibrationService.calibrate(0.72)    # → calibrated probability
    """

    _model: Optional[IsotonicRegression] = None

    @classmethod
    def fit(cls) -> dict:
        """
        Fit isotonic regression on historical (confidence, is_correct) pairs.
        Returns stats dict.
        """
        from apps.predictions.models import Prediction

        resolved = Prediction.objects.filter(
            is_correct__isnull=False,
            confidence_score__isnull=False,
        ).values_list('confidence_score', 'is_correct')

        raw_confidences = []
        outcomes = []
        for conf, correct in resolved:
            raw_confidences.append(conf / 100.0)  # normalise to 0-1
            outcomes.append(1.0 if correct else 0.0)

        n = len(raw_confidences)
        if n < MIN_SAMPLES_FOR_CALIBRATION:
            logger.info(
                f"Calibration: only {n} resolved predictions, "
                f"need {MIN_SAMPLES_FOR_CALIBRATION}. Skipping."
            )
            return {'status': 'skipped', 'samples': n}

        X = np.array(raw_confidences)
        y = np.array(outcomes)

        model = IsotonicRegression(
            y_min=0.0, y_max=1.0, increasing=True, out_of_bounds='clip'
        )
        model.fit(X, y)

        # Save model
        os.makedirs(os.path.dirname(CALIBRATION_MODEL_PATH), exist_ok=True)
        joblib.dump(model, CALIBRATION_MODEL_PATH)
        cls._model = model

        # Compute calibration error (ECE)
        ece = cls._expected_calibration_error(X, y, model, n_bins=10)

        logger.info(
            f"Calibration fitted on {n} samples. "
            f"ECE = {ece:.4f}"
        )
        return {'status': 'fitted', 'samples': n, 'ece': round(ece, 4)}

    @classmethod
    def calibrate(cls, raw_confidence: float) -> float:
        """
        Calibrate a raw confidence score (0-100) → calibrated score (0-100).
        Returns uncalibrated score if model not available.
        """
        model = cls._get_model()
        if model is None:
            return raw_confidence

        raw_prob = max(0.0, min(raw_confidence / 100.0, 1.0))
        try:
            calibrated = float(model.predict([raw_prob])[0])
        except Exception:
            return raw_confidence

        return round(calibrated * 100.0, 2)

    @classmethod
    def calibrate_probs(cls, h: float, d: float, a: float) -> tuple:
        """
        Calibrate individual outcome probabilities.
        Applies isotonic to max-prob, then proportionally adjusts others.
        Returns (cal_h, cal_d, cal_a).
        """
        model = cls._get_model()
        if model is None:
            return h, d, a

        max_prob = max(h, d, a)
        try:
            cal_max = float(model.predict([max_prob])[0])
        except Exception:
            return h, d, a

        # Scale factor
        if max_prob > 0:
            scale = cal_max / max_prob
        else:
            return h, d, a

        cal_h = h * scale
        cal_d = d * scale
        cal_a = a * scale

        # Renormalise to sum to 1
        total = cal_h + cal_d + cal_a
        if total > 0:
            cal_h /= total
            cal_d /= total
            cal_a /= total

        return round(cal_h, 4), round(cal_d, 4), round(cal_a, 4)

    @classmethod
    def _get_model(cls) -> Optional[IsotonicRegression]:
        """Load model from cache or disk."""
        if cls._model is not None:
            return cls._model
        if os.path.exists(CALIBRATION_MODEL_PATH):
            try:
                cls._model = joblib.load(CALIBRATION_MODEL_PATH)
                return cls._model
            except Exception as e:
                logger.warning(f"Failed to load calibration model: {e}")
        return None

    @staticmethod
    def _expected_calibration_error(
        confidences: np.ndarray, outcomes: np.ndarray,
        model: IsotonicRegression, n_bins: int = 10,
    ) -> float:
        """Compute Expected Calibration Error."""
        calibrated = model.predict(confidences)
        bins = np.linspace(0, 1, n_bins + 1)
        ece = 0.0
        for i in range(n_bins):
            mask = (calibrated >= bins[i]) & (calibrated < bins[i + 1])
            if mask.sum() == 0:
                continue
            bin_acc = outcomes[mask].mean()
            bin_conf = calibrated[mask].mean()
            ece += mask.sum() * abs(bin_acc - bin_conf)
        return ece / len(confidences)
