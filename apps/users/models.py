"""
User domain models.

UserProfile      – Extended user info (region, currency, geo-based pricing)
PredictionUsage  – Tracks prediction credits consumed per user
LoginBonus       – Records signup bonus grants
"""

import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.core.validators import MinValueValidator


# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

EAST_AFRICA_COUNTRIES = {'KE', 'TZ', 'UG', 'RW', 'BI', 'SS', 'ET', 'SO'}

REGION_CHOICES = [
    ('east_africa', 'East Africa'),
    ('west_africa', 'West Africa'),
    ('europe', 'Europe'),
    ('americas', 'Americas'),
    ('asia', 'Asia'),
    ('global', 'Global / Other'),
]

CURRENCY_CHOICES = [
    ('KES', 'Kenyan Shilling'),
    ('TZS', 'Tanzanian Shilling'),
    ('UGX', 'Ugandan Shilling'),
    ('USD', 'US Dollar'),
    ('EUR', 'Euro'),
    ('GBP', 'British Pound'),
]


class UserProfile(models.Model):
    """
    Extended profile attached 1-to-1 with Django's User model.

    Stores:
    - region / country / currency  → drives pricing logic
    - prediction credits           → current balance
    - signup bonus tracking        → prevents double-granting
    - IP-based location cache      → avoids repeated geo lookups
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )

    # Geo / Pricing
    region = models.CharField(max_length=20, choices=REGION_CHOICES, default='global')
    country_code = models.CharField(max_length=3, blank=True, default='')
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default='USD')
    detected_ip = models.GenericIPAddressField(null=True, blank=True)
    geo_cached_at = models.DateTimeField(null=True, blank=True)

    # Credits & Bonus
    prediction_credits = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    signup_bonus_granted = models.BooleanField(default=False)
    signup_bonus_credits = models.IntegerField(default=3)  # 3 free after signup
    anonymous_views_used = models.IntegerField(default=0)  # max 2 before login gate

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_profiles'
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'

    def __str__(self):
        return f"{self.user.username} – {self.region} ({self.currency})"

    # ── helpers ─────────────────────────────────

    @property
    def is_east_africa(self) -> bool:
        return self.country_code.upper() in EAST_AFRICA_COUNTRIES

    def grant_signup_bonus(self) -> int:
        """Grant signup bonus credits once. Returns number of credits added."""
        if self.signup_bonus_granted:
            return 0
        self.prediction_credits += self.signup_bonus_credits
        self.signup_bonus_granted = True
        self.save(update_fields=['prediction_credits', 'signup_bonus_granted', 'updated_at'])
        return self.signup_bonus_credits

    def consume_credit(self) -> bool:
        """Atomically consume 1 prediction credit. Returns True if successful."""
        from django.db.models import F
        updated = UserProfile.objects.filter(
            pk=self.pk,
            prediction_credits__gt=0,
        ).update(prediction_credits=F('prediction_credits') - 1)
        if updated:
            self.refresh_from_db()
            return True
        return False

    def add_credits(self, amount: int) -> None:
        """Add credits atomically."""
        from django.db.models import F
        UserProfile.objects.filter(pk=self.pk).update(
            prediction_credits=F('prediction_credits') + amount,
        )
        self.refresh_from_db()

    def update_geo(self, ip: str, country_code: str, region: str, currency: str) -> None:
        """Cache geo info from IP lookup."""
        self.detected_ip = ip
        self.country_code = country_code.upper()
        self.region = region
        self.currency = currency
        self.geo_cached_at = timezone.now()
        self.save(update_fields=[
            'detected_ip', 'country_code', 'region', 'currency', 'geo_cached_at', 'updated_at',
        ])


class PredictionUsage(models.Model):
    """
    Per-day prediction consumption log.
    Enables daily-limit enforcement for monthly subscribers and audit trail.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='prediction_usage',
    )
    prediction = models.ForeignKey(
        'predictions.Prediction',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    date = models.DateField(default=timezone.now)
    credits_used = models.IntegerField(default=1)
    is_free = models.BooleanField(default=False)
    accessed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'prediction_usage'
        ordering = ['-accessed_at']
        indexes = [
            models.Index(fields=['user', 'date']),
        ]

    def __str__(self):
        return f"{self.user.username} – {self.date} ({self.credits_used} credits)"


class LoginBonus(models.Model):
    """
    Immutable record of each bonus grant (signup or promotional).
    """

    BONUS_TYPES = [
        ('signup', 'Signup Bonus'),
        ('referral', 'Referral Bonus'),
        ('promo', 'Promotional Bonus'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bonuses',
    )
    bonus_type = models.CharField(max_length=20, choices=BONUS_TYPES)
    credits_granted = models.IntegerField()
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'login_bonuses'
        ordering = ['-granted_at']

    def __str__(self):
        return f"{self.user.username} +{self.credits_granted} ({self.bonus_type})"
