"""
Predictions domain models.

Prediction            – ML-generated Win/Draw/Loss probabilities for a match
PredictionExplanation – SHAP-style feature explanations per prediction
ModelVersion          – Tracks which model version produced each prediction
"""

import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator


class ModelVersion(models.Model):
    """
    Registry of trained ML model versions.
    Enables rollback, A/B testing, and accuracy tracking per model.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, help_text="e.g. ensemble_v3.2")
    version = models.CharField(max_length=30)
    model_type = models.CharField(
        max_length=30,
        choices=[
            ('xgboost', 'XGBoost'),
            ('neural', 'Neural Network'),
            ('elo', 'ELO Hybrid'),
            ('bayesian', 'Bayesian'),
            ('ensemble', 'Ensemble'),
        ],
    )
    file_path = models.CharField(max_length=500, blank=True, default='')
    metrics = models.JSONField(
        default=dict, blank=True,
        help_text="Training metrics: accuracy, f1, log_loss, etc.",
    )
    is_active = models.BooleanField(default=False, help_text="Currently used for predictions")
    trained_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'model_versions'
        ordering = ['-created_at']
        unique_together = ['name', 'version']

    def __str__(self):
        return f"{self.name} v{self.version} ({'active' if self.is_active else 'inactive'})"


class Prediction(models.Model):
    """
    ML-generated prediction for a scheduled match.

    Stores Win / Draw / Loss probabilities plus overall confidence.
    `predicted_outcome` is the selected outcome with highest probability.
    `is_correct` is set after the match finishes (immutable for transparency).
    `is_locked` prevents edits after kickoff.
    """

    OUTCOME_CHOICES = [
        ('home_win', 'Home Win'),
        ('draw', 'Draw'),
        ('away_win', 'Away Win'),
    ]

    TIER_CHOICES = [
        ('free', 'Free'),
        ('premium', 'Premium'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    match = models.OneToOneField(
        'core.Match',
        on_delete=models.CASCADE,
        related_name='prediction',
    )
    model_version = models.ForeignKey(
        ModelVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='predictions',
    )

    # Probabilities (0.0 – 1.0)
    home_win_prob = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
    )
    draw_prob = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
    )
    away_win_prob = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
    )

    # Derived
    predicted_outcome = models.CharField(max_length=10, choices=OUTCOME_CHOICES)
    confidence_score = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(100.0)],
        help_text="Confidence as percentage (0-100)",
    )

    # Gating
    tier = models.CharField(max_length=10, choices=TIER_CHOICES, default='free')

    # Accuracy tracking (set after match finishes — immutable)
    is_correct = models.BooleanField(null=True, blank=True, help_text="null=pending, True/False=resolved")
    resolved_at = models.DateTimeField(null=True, blank=True)

    # Locking
    is_locked = models.BooleanField(default=False)

    # Features snapshot (for reproducibility)
    feature_snapshot = models.JSONField(
        default=dict, blank=True,
        help_text="Input features used for this prediction",
    )

    # Metadata
    generated_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'predictions'
        ordering = ['-generated_at']
        indexes = [
            models.Index(fields=['match', 'tier']),
            models.Index(fields=['is_correct', 'generated_at']),
        ]

    def __str__(self):
        return f"Prediction: {self.match} → {self.predicted_outcome} ({self.confidence_score:.0f}%)"

    def resolve(self, actual_outcome: str) -> None:
        """
        Compare predicted vs actual outcome. Sets is_correct (immutable once set).
        Called by the accuracy tracking service after match finishes.
        """
        if self.is_correct is not None:
            return  # Already resolved – immutable
        self.is_correct = (self.predicted_outcome == actual_outcome)
        self.resolved_at = timezone.now()
        self.save(update_fields=['is_correct', 'resolved_at', 'updated_at'])


class PredictionExplanation(models.Model):
    """
    SHAP-style explanation of why a prediction was made.

    Each record is one influencing factor (e.g. "Home team form: +12% confidence").
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    prediction = models.ForeignKey(
        Prediction,
        on_delete=models.CASCADE,
        related_name='explanations',
    )
    factor_name = models.CharField(max_length=100, help_text="e.g. 'Recent Form', 'Head-to-Head'")
    factor_value = models.CharField(max_length=200, help_text="e.g. 'W-W-D-L-W (last 5)'")
    impact_score = models.FloatField(help_text="SHAP value or normalised impact (-1.0 to 1.0)")
    impact_direction = models.CharField(
        max_length=10,
        choices=[('positive', 'Positive'), ('negative', 'Negative'), ('neutral', 'Neutral')],
    )
    display_order = models.IntegerField(default=0)

    class Meta:
        db_table = 'prediction_explanations'
        ordering = ['display_order', '-impact_score']

    def __str__(self):
        return f"{self.factor_name}: {self.impact_score:+.3f} ({self.prediction.match})"
