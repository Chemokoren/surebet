"""
Core / Matches domain models.

League      – Football competition (Premier League, La Liga, etc.)
Team        – Club participating in a league
Season      – A league's year or period
Match       – A scheduled or completed fixture
MatchResult – Actual result after a match is finished (for accuracy tracking)
"""

import uuid
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator


# ──────────────────────────────────────────────
# League
# ──────────────────────────────────────────────

class League(models.Model):
    """
    Represents a football competition.
    Supports both year-round domestic leagues (Premier League, La Liga, etc.)
    and seasonal/continental competitions (Champions League, Europa League).

    `api_id` maps to external data sources (e.g. football-data.org league ID).
    `is_active` lets admin enable/disable leagues without code changes.
    `is_seasonal` marks competitions that only occur during certain months.
    `league_type` categorises between domestic, continental, and international.
    `season_months` is a JSON list of months (1–12) when the league is in season.
    """

    LEAGUE_TYPE_CHOICES = [
        ('domestic', 'Domestic League'),
        ('continental', 'Continental Competition'),
        ('international', 'International Competition'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=10, unique=True, help_text="Short code, e.g. PL, LL, BL1, UCL")
    country = models.CharField(max_length=60)
    logo_url = models.URLField(blank=True, default='')
    api_id = models.IntegerField(null=True, blank=True, unique=True, help_text="External API league ID")
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0, help_text="Display order – lower = higher priority")

    # Seasonal / Continental support
    is_seasonal = models.BooleanField(
        default=False,
        help_text="True for competitions that only occur during certain months (e.g. Champions League)",
    )
    league_type = models.CharField(
        max_length=15,
        choices=LEAGUE_TYPE_CHOICES,
        default='domestic',
        help_text="Category of competition",
    )
    season_months = models.JSONField(
        default=list, blank=True,
        help_text=(
            "JSON list of month numbers (1-12) when this league is active. "
            "Empty = year-round. E.g. [9,10,11,12,1,2,3,4,5,6] for Champions League."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'leagues'
        ordering = ['priority', 'name']

    def __str__(self):
        return f"{self.name} ({self.code})"

    # ── helpers ─────────────────────────────────

    @property
    def is_currently_in_season(self) -> bool:
        """
        Check if this league is currently in season.
        Year-round leagues (season_months=[]) are always in season.
        Seasonal leagues check against current month.
        """
        if not self.is_seasonal or not self.season_months:
            return True
        current_month = timezone.now().month
        return current_month in self.season_months

    def has_matches_this_week(self) -> bool:
        """
        Check if the league has any scheduled matches within the current week
        (Mon-Sun). Used to give seasonal leagues priority when they have games.
        """
        from datetime import timedelta
        now = timezone.now()
        # Get Monday of this week
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        sunday = monday + timedelta(days=7)
        return self.matches.filter(
            match_date__gte=monday,
            match_date__lt=sunday,
            status__in=['scheduled', 'timed'],
        ).exists()


# ──────────────────────────────────────────────
# Season
# ──────────────────────────────────────────────

class Season(models.Model):
    """A league's season period, e.g. 2025-2026."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name='seasons')
    year = models.CharField(max_length=9, help_text="e.g. 2025-2026")
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)

    class Meta:
        db_table = 'seasons'
        unique_together = ['league', 'year']
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.league.code} {self.year}"


# ──────────────────────────────────────────────
# Team
# ──────────────────────────────────────────────

class Team(models.Model):
    """
    Football club.

    `elo_rating` is recalculated after each match result.
    `api_id` maps to external data sources.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    short_name = models.CharField(max_length=30, blank=True, default='')
    code = models.CharField(max_length=5, blank=True, default='')
    logo_url = models.URLField(blank=True, default='')
    league = models.ForeignKey(League, on_delete=models.SET_NULL, null=True, related_name='teams')
    country = models.CharField(max_length=60, blank=True, default='')
    api_id = models.IntegerField(null=True, blank=True, unique=True)
    elo_rating = models.FloatField(default=1500.0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'teams'
        ordering = ['name']

    def __str__(self):
        return self.name


# ──────────────────────────────────────────────
# Match
# ──────────────────────────────────────────────

class Match(models.Model):
    """
    A single fixture.

    Lifecycle: SCHEDULED → IN_PLAY → FINISHED  (or POSTPONED / CANCELLED)
    `is_locked` prevents predictions from being modified after kickoff.
    """

    STATUS_CHOICES = [
        ('scheduled', 'Scheduled'),
        ('timed', 'Timed'),
        ('in_play', 'In Play'),
        ('paused', 'Paused'),
        ('finished', 'Finished'),
        ('postponed', 'Postponed'),
        ('cancelled', 'Cancelled'),
        ('suspended', 'Suspended'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    league = models.ForeignKey(League, on_delete=models.CASCADE, related_name='matches')
    season = models.ForeignKey(Season, on_delete=models.SET_NULL, null=True, blank=True, related_name='matches')
    matchday = models.IntegerField(null=True, blank=True)

    home_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='home_matches')
    away_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='away_matches')

    match_date = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='scheduled', db_index=True)

    # Result (populated after match finishes)
    home_score = models.IntegerField(null=True, blank=True)
    away_score = models.IntegerField(null=True, blank=True)
    home_half_score = models.IntegerField(null=True, blank=True)
    away_half_score = models.IntegerField(null=True, blank=True)

    # Locking
    is_locked = models.BooleanField(default=False, help_text="True after kickoff – no more prediction changes")

    # External reference
    api_id = models.IntegerField(null=True, blank=True, unique=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'matches'
        ordering = ['match_date']
        indexes = [
            models.Index(fields=['status', 'match_date']),
            models.Index(fields=['league', 'match_date']),
        ]

    def __str__(self):
        return f"{self.home_team} vs {self.away_team} ({self.match_date.strftime('%Y-%m-%d')})"

    @property
    def actual_outcome(self) -> str | None:
        """Return 'home_win' | 'draw' | 'away_win' | None."""
        if self.home_score is None or self.away_score is None:
            return None
        if self.home_score > self.away_score:
            return 'home_win'
        elif self.home_score == self.away_score:
            return 'draw'
        return 'away_win'

    def lock(self) -> None:
        """Lock predictions for this match (called at kickoff)."""
        if not self.is_locked:
            self.is_locked = True
            self.save(update_fields=['is_locked', 'updated_at'])


# ──────────────────────────────────────────────
# League Access Rule
# ──────────────────────────────────────────────

class LeagueAccessRule(models.Model):
    """
    Admin-configurable subscription quota distribution per league.

    When a subscriber has a daily limit of N predictions, these rules
    determine how many of those N slots are allocated to each league.

    Rules:
      - Leagues with subscription_share_pct > 0 receive that % of N first.
      - Remaining slots are split equally among leagues with pct = 0.
      - If a priority league has no fixtures that day, its share is
        redistributed equally across the other active leagues.

    Default (seeded by migration):
      Premier League → 50 %
      All other leagues → 0 % (equal share of the remaining 50 %)
    """

    league = models.OneToOneField(
        League,
        on_delete=models.CASCADE,
        related_name='access_rule',
    )
    subscription_share_pct = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(100.0)],
        help_text=(
            "Percentage (0–100) of the subscriber's daily prediction limit "
            "to allocate to this league. "
            "0 = equal share with other zero-pct leagues. "
            "Example: set Premier League to 50 → EPL gets half the daily quota."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'league_access_rules'
        ordering = ['-subscription_share_pct', 'league__priority']

    def __str__(self):
        share = (
            f"{self.subscription_share_pct:.0f}%"
            if self.subscription_share_pct > 0
            else "equal share"
        )
        return f"{self.league.name}: {share}"


# Import alias model so Django discovers it in the same app
from apps.core.models_aliases import TeamAlias  # noqa: E402, F401
