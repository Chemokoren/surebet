"""
Analytics domain models.

AccuracyRecord   – Immutable daily/weekly/monthly accuracy snapshot
RevenueSnapshot  – Revenue aggregation per region and plan
PredictionStats  – Per-league prediction performance
"""

import uuid
from django.db import models
from django.utils import timezone


class AccuracyRecord(models.Model):
    """
    Immutable accuracy snapshot.

    Calculated periodically (daily, weekly, monthly) and never modified.
    Powers the public accuracy dashboard and trust signals.
    """

    PERIOD_CHOICES = [
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('all_time', 'All Time'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    league = models.ForeignKey(
        'core.League',
        on_delete=models.CASCADE,
        related_name='accuracy_records',
        null=True, blank=True,
        help_text="Null = overall (all leagues)",
    )
    period = models.CharField(max_length=15, choices=PERIOD_CHOICES)
    period_start = models.DateField()
    period_end = models.DateField()

    # Counts
    total_predictions = models.IntegerField(default=0)
    correct_predictions = models.IntegerField(default=0)
    incorrect_predictions = models.IntegerField(default=0)
    pending_predictions = models.IntegerField(default=0)

    # Rates
    accuracy_rate = models.FloatField(default=0.0, help_text="Percentage 0-100")
    home_win_accuracy = models.FloatField(default=0.0)
    draw_accuracy = models.FloatField(default=0.0)
    away_win_accuracy = models.FloatField(default=0.0)

    # Model info
    model_version = models.CharField(max_length=50, blank=True, default='')
    avg_confidence = models.FloatField(default=0.0)

    # Metadata
    calculated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'accuracy_records'
        ordering = ['-period_start']
        indexes = [
            models.Index(fields=['period', 'period_start']),
            models.Index(fields=['league', 'period']),
        ]

    def __str__(self):
        league_name = self.league.name if self.league else 'Overall'
        return f"Accuracy: {league_name} {self.period} {self.period_start} → {self.accuracy_rate:.1f}%"


class RevenueSnapshot(models.Model):
    """
    Revenue aggregation for admin dashboard.
    Calculated daily by Celery task.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    date = models.DateField(db_index=True)
    region = models.CharField(max_length=20, default='global')

    # Revenue by type
    total_revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    subscription_revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit_revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    single_prediction_revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Counts
    total_transactions = models.IntegerField(default=0)
    new_subscribers = models.IntegerField(default=0)
    churned_subscribers = models.IntegerField(default=0)
    active_users = models.IntegerField(default=0)

    # Top performers
    most_profitable_tier = models.CharField(max_length=100, blank=True, default='')
    currency = models.CharField(max_length=5, default='USD')

    calculated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'revenue_snapshots'
        ordering = ['-date']
        unique_together = ['date', 'region']

    def __str__(self):
        return f"Revenue: {self.region} {self.date} – {self.currency} {self.total_revenue}"


class PredictionStats(models.Model):
    """
    Per-league prediction performance summary.
    Updated daily for admin analytics.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    league = models.ForeignKey(
        'core.League',
        on_delete=models.CASCADE,
        related_name='prediction_stats',
    )
    date = models.DateField(db_index=True)

    total_predictions = models.IntegerField(default=0)
    predictions_consumed = models.IntegerField(default=0)
    unique_users = models.IntegerField(default=0)
    avg_confidence = models.FloatField(default=0.0)
    most_predicted_outcome = models.CharField(max_length=10, blank=True, default='')

    calculated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'prediction_stats'
        ordering = ['-date']
        unique_together = ['league', 'date']

    def __str__(self):
        return f"Stats: {self.league.code} {self.date}"
