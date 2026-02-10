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


# ──────────────────────────────────────────────
# League
# ──────────────────────────────────────────────

class League(models.Model):
    """
    Represents a football competition.
    Initial set: Premier League, La Liga, Bundesliga, Serie A, Ligue 1.

    `api_id` maps to external data sources (e.g. football-data.org league ID).
    `is_active` lets admin enable/disable leagues without code changes.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=10, unique=True, help_text="Short code, e.g. PL, LL, BL1")
    country = models.CharField(max_length=60)
    logo_url = models.URLField(blank=True, default='')
    api_id = models.IntegerField(null=True, blank=True, unique=True, help_text="External API league ID")
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=0, help_text="Display order – lower = higher priority")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'leagues'
        ordering = ['priority', 'name']

    def __str__(self):
        return f"{self.name} ({self.code})"


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
