"""
External Intelligence Models.

PredictionSource       – Configurable external prediction site / pundit
ExternalPrediction     – Scraped prediction from an external source for a match
SourceAccuracyRecord   – Rolling accuracy snapshot per source (daily)
SourceLearningPhase    – Tracks a source during its initial evaluation period
"""

import uuid
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator


class PredictionSource(models.Model):
    """
    An external prediction website, pundit, or data feed.

    Admin can add new sources via Django Admin.  Each source goes through
    a 'learning' phase before being promoted to 'active' (used in blending).

    STATUS lifecycle:  learning → active → paused / retired
    """

    STATUS_CHOICES = [
        ('learning', 'Learning Phase'),       # Observing & tracking accuracy
        ('active', 'Active'),                 # Being used in prediction blending
        ('paused', 'Paused'),                 # Temporarily disabled
        ('retired', 'Retired'),               # No longer used
    ]

    SOURCE_TYPE_CHOICES = [
        ('website', 'Prediction Website'),
        ('pundit', 'Expert Pundit'),
        ('bookmaker', 'Bookmaker Odds'),
        ('model', 'External ML Model'),
        ('community', 'Community / Tipster'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150, unique=True, help_text="e.g. 'PredictZ', 'Paul Merson'")
    slug = models.SlugField(max_length=80, unique=True, help_text="URL-safe identifier")
    source_type = models.CharField(max_length=20, choices=SOURCE_TYPE_CHOICES, default='website')
    website_url = models.URLField(blank=True, default='', help_text="Homepage URL")
    scrape_url = models.URLField(blank=True, default='', help_text="URL pattern to scrape predictions from")
    description = models.TextField(blank=True, default='')

    # Scraping configuration
    scraper_class = models.CharField(
        max_length=200, blank=True, default='',
        help_text="Python path to scraper class, e.g. 'apps.predictions.scrapers.predictz.PredictZScraper'",
    )
    scrape_config = models.JSONField(
        default=dict, blank=True,
        help_text="Extra config for scraper (selectors, headers, rate limits, etc.)",
    )
    scrape_enabled = models.BooleanField(default=True, help_text="Enable automated scraping")
    scrape_interval_hours = models.IntegerField(default=12, help_text="How often to scrape (hours)")

    # Status & evaluation
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='learning')
    learning_start_date = models.DateField(null=True, blank=True)
    learning_end_date = models.DateField(null=True, blank=True, help_text="Auto-set to ~90 days after start")
    min_accuracy_to_activate = models.FloatField(
        default=0.50,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Minimum accuracy (0–1) required to be promoted from learning → active",
    )

    # Blending weight (only used when status='active')
    blend_weight = models.FloatField(
        default=0.5,
        validators=[MinValueValidator(0.0), MaxValueValidator(2.0)],
        help_text="Weight when blending this source's predictions (higher = more influence)",
    )

    # Quality metrics (auto-updated)
    total_predictions = models.IntegerField(default=0)
    correct_predictions = models.IntegerField(default=0)
    overall_accuracy = models.FloatField(default=0.0, help_text="Lifetime accuracy (0–1)")
    recent_accuracy = models.FloatField(default=0.0, help_text="Last 30 days accuracy (0–1)")

    # Priority / ranking
    priority = models.IntegerField(default=50, help_text="Lower = checked first. Top 20 sites get 1-20")

    # Audit
    last_scraped_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'prediction_sources'
        ordering = ['priority', 'name']

    def __str__(self):
        return f"{self.name} ({self.get_status_display()}) – {self.overall_accuracy:.0%}"

    @property
    def is_in_learning_phase(self) -> bool:
        if self.status != 'learning':
            return False
        if self.learning_end_date and timezone.now().date() > self.learning_end_date:
            return False  # Learning period expired
        return True

    @property
    def should_auto_activate(self) -> bool:
        """Check if source should be promoted from learning to active."""
        if self.status != 'learning':
            return False
        if not self.learning_end_date:
            return False
        if timezone.now().date() < self.learning_end_date:
            return False  # Still in learning phase
        return (
            self.total_predictions >= 50
            and self.overall_accuracy >= self.min_accuracy_to_activate
        )


class ExternalPrediction(models.Model):
    """
    A scraped or manually entered prediction from an external source
    for a specific match.  Compared against actual outcomes to track
    the source's accuracy.
    """

    OUTCOME_CHOICES = [
        ('home_win', 'Home Win'),
        ('draw', 'Draw'),
        ('away_win', 'Away Win'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(
        PredictionSource,
        on_delete=models.CASCADE,
        related_name='predictions',
    )
    match = models.ForeignKey(
        'core.Match',
        on_delete=models.CASCADE,
        related_name='external_predictions',
    )

    # Predicted probabilities (if available from the source)
    home_win_prob = models.FloatField(null=True, blank=True)
    draw_prob = models.FloatField(null=True, blank=True)
    away_win_prob = models.FloatField(null=True, blank=True)

    # Predicted outcome
    predicted_outcome = models.CharField(
        max_length=10, choices=OUTCOME_CHOICES,
        help_text="The source's predicted outcome",
    )
    confidence = models.FloatField(
        null=True, blank=True,
        help_text="Source's confidence (0-100) if provided",
    )

    # Raw data from scrape
    raw_data = models.JSONField(default=dict, blank=True, help_text="Raw scraped data")

    # Resolution
    is_correct = models.BooleanField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    # Metadata
    scraped_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'external_predictions'
        ordering = ['-scraped_at']
        unique_together = ['source', 'match']  # One prediction per source per match
        indexes = [
            models.Index(fields=['source', 'is_correct']),
            models.Index(fields=['match', 'source']),
        ]

    def __str__(self):
        return f"{self.source.name}: {self.match} → {self.predicted_outcome}"

    def resolve(self, actual_outcome: str):
        """Resolve this external prediction against the actual outcome."""
        if self.is_correct is not None:
            return
        self.is_correct = (self.predicted_outcome == actual_outcome)
        self.resolved_at = timezone.now()
        self.save(update_fields=['is_correct', 'resolved_at'])


class SourceAccuracyRecord(models.Model):
    """
    Daily accuracy snapshot for a prediction source.
    Enables historical accuracy analysis and trend visualisation.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(
        PredictionSource,
        on_delete=models.CASCADE,
        related_name='accuracy_records',
    )
    date = models.DateField()

    # Counts
    total_predictions = models.IntegerField(default=0)
    correct_predictions = models.IntegerField(default=0)
    accuracy = models.FloatField(default=0.0)

    # Breakdown
    home_correct = models.IntegerField(default=0)
    draw_correct = models.IntegerField(default=0)
    away_correct = models.IntegerField(default=0)

    # Rolling averages
    accuracy_7d = models.FloatField(null=True, blank=True, help_text="7-day rolling accuracy")
    accuracy_30d = models.FloatField(null=True, blank=True, help_text="30-day rolling accuracy")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'source_accuracy_records'
        unique_together = ['source', 'date']
        ordering = ['-date']

    def __str__(self):
        return f"{self.source.name} @ {self.date}: {self.accuracy:.0%} ({self.total_predictions} preds)"
