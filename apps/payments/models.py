"""
Payments domain models.

SubscriptionPlan     – Admin-managed pricing tiers
UserSubscription     – User's active subscription state
PaymentTransaction   – Normalised transaction log (all providers)
PaymentChannel       – Admin-toggled payment method per region
PricingTier          – Credit pack pricing (geo-aware)
"""

import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.core.validators import MinValueValidator


# ──────────────────────────────────────────────
# Pricing Tier (Credit Packs)
# ──────────────────────────────────────────────

class PricingTier(models.Model):
    """
    A purchasable credit pack.

    East Africa pricing example:
        Tier 1: 50 KSH → 5 predictions
        Tier 2: 100 KSH → 10 predictions
        Tier 3: 200 KSH → 20 predictions
        Tier 4: 1000 KSH → 10/day for 30 days
        Single: 20 KSH → 1 prediction

    Admin can create/edit without code changes.
    """

    TIER_TYPES = [
        ('credit_pack', 'Credit Pack'),
        ('daily_quota', 'Daily Quota'),
        ('single', 'Single Prediction'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    tier_type = models.CharField(max_length=20, choices=TIER_TYPES)
    region = models.CharField(
        max_length=20,
        choices=[
            ('east_africa', 'East Africa'),
            ('global', 'Global'),
        ],
        default='global',
    )
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=5, default='USD')

    # What the user gets
    credits = models.IntegerField(default=0, help_text="One-time prediction credits")
    daily_limit = models.IntegerField(default=0, help_text="Predictions per day (for daily_quota type)")
    duration_days = models.IntegerField(default=0, help_text="Validity period in days (for daily_quota)")

    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'pricing_tiers'
        ordering = ['region', 'display_order', 'price']

    def __str__(self):
        return f"{self.name} – {self.currency} {self.price} ({self.region})"


# ──────────────────────────────────────────────
# Subscription Plan
# ──────────────────────────────────────────────

class SubscriptionPlan(models.Model):
    """
    Recurring subscription managed by admin.
    """

    INTERVAL_CHOICES = [
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('yearly', 'Yearly'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    interval = models.CharField(max_length=15, choices=INTERVAL_CHOICES, default='monthly')
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=5, default='USD')
    region = models.CharField(max_length=20, default='global')
    daily_prediction_limit = models.IntegerField(default=10)
    features = models.JSONField(default=list, blank=True, help_text="List of included feature labels")
    is_active = models.BooleanField(default=True)
    stripe_price_id = models.CharField(max_length=100, blank=True, default='')
    display_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'subscription_plans'
        ordering = ['display_order', 'price']

    def __str__(self):
        return f"{self.name} – {self.currency} {self.price}/{self.interval}"


# ──────────────────────────────────────────────
# User Subscription
# ──────────────────────────────────────────────

class UserSubscription(models.Model):
    """
    A user's subscription lifecycle.

    States: active → grace → expired / cancelled
    """

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('grace', 'Grace Period'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
        ('pending', 'Pending Payment'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='subscriptions',
    )
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name='subscribers')
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    started_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    grace_ends_at = models.DateTimeField(null=True, blank=True)
    auto_renew = models.BooleanField(default=True)
    daily_predictions_used_today = models.IntegerField(default=0)
    last_daily_reset = models.DateField(null=True, blank=True)

    # External reference
    stripe_subscription_id = models.CharField(max_length=100, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_subscriptions'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} – {self.plan.name} ({self.status})"

    @property
    def is_usable(self) -> bool:
        """Can the user access premium predictions right now?"""
        if self.status == 'active':
            return self.expires_at is None or self.expires_at > timezone.now()
        if self.status == 'grace':
            return self.grace_ends_at is not None and self.grace_ends_at > timezone.now()
        return False

    def reset_daily_quota(self) -> None:
        """Reset daily usage if day has changed."""
        today = timezone.now().date()
        if self.last_daily_reset != today:
            self.daily_predictions_used_today = 0
            self.last_daily_reset = today
            self.save(update_fields=['daily_predictions_used_today', 'last_daily_reset', 'updated_at'])

    def consume_daily(self) -> bool:
        """Try to use 1 daily prediction. Returns False if limit reached."""
        self.reset_daily_quota()
        if self.daily_predictions_used_today >= self.plan.daily_prediction_limit:
            return False
        self.daily_predictions_used_today += 1
        self.save(update_fields=['daily_predictions_used_today', 'updated_at'])
        return True

    def activate(self, duration_days: int = 30) -> None:
        """Activate subscription."""
        now = timezone.now()
        self.status = 'active'
        self.started_at = now
        self.expires_at = now + timezone.timedelta(days=duration_days)
        self.save(update_fields=['status', 'started_at', 'expires_at', 'updated_at'])

    def enter_grace(self, grace_days: int = 3) -> None:
        """Enter grace period after expiration."""
        self.status = 'grace'
        self.grace_ends_at = timezone.now() + timezone.timedelta(days=grace_days)
        self.save(update_fields=['status', 'grace_ends_at', 'updated_at'])

    def expire(self) -> None:
        """Mark subscription as expired."""
        self.status = 'expired'
        self.save(update_fields=['status', 'updated_at'])

    def cancel(self) -> None:
        """Cancel subscription."""
        self.status = 'cancelled'
        self.cancelled_at = timezone.now()
        self.save(update_fields=['status', 'cancelled_at', 'updated_at'])


# ──────────────────────────────────────────────
# Payment Channel
# ──────────────────────────────────────────────

class PaymentChannel(models.Model):
    """
    Admin-toggled payment method enabled per region.
    No code changes needed to add/remove channels from a region.
    """

    PROVIDER_CHOICES = [
        ('mpesa', 'M-Pesa STK Push'),
        ('stripe', 'Stripe'),
        ('paypal', 'PayPal'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    region = models.CharField(max_length=20, default='global')
    display_name = models.CharField(max_length=100)
    is_enabled = models.BooleanField(default=True)
    config = models.JSONField(
        default=dict, blank=True,
        help_text="Provider-specific config overrides (non-secret)",
    )
    display_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'payment_channels'
        ordering = ['region', 'display_order']
        unique_together = ['provider', 'region']

    def __str__(self):
        return f"{self.display_name} ({self.region}) – {'ON' if self.is_enabled else 'OFF'}"


# ──────────────────────────────────────────────
# Payment Transaction
# ──────────────────────────────────────────────

class PaymentTransaction(models.Model):
    """
    Normalised transaction record across all payment providers.
    Immutable once confirmed – provides audit trail.
    """

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
        ('cancelled', 'Cancelled'),
    ]

    PAYMENT_TYPES = [
        ('credit_purchase', 'Credit Purchase'),
        ('subscription', 'Subscription Payment'),
        ('single_prediction', 'Single Prediction'),
        ('renewal', 'Subscription Renewal'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='transactions',
    )
    channel = models.ForeignKey(
        PaymentChannel,
        on_delete=models.SET_NULL,
        null=True,
        related_name='transactions',
    )

    payment_type = models.CharField(max_length=25, choices=PAYMENT_TYPES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=5)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')

    # What was purchased
    pricing_tier = models.ForeignKey(
        PricingTier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    subscription_plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    credits_granted = models.IntegerField(default=0)

    # Provider references
    provider_transaction_id = models.CharField(max_length=200, blank=True, default='')
    provider_response = models.JSONField(default=dict, blank=True)
    provider = models.CharField(max_length=20, blank=True, default='')

    # Timestamps
    initiated_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)

    # Metadata
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'payment_transactions'
        ordering = ['-initiated_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['provider', 'provider_transaction_id']),
        ]

    def __str__(self):
        return f"Txn {self.id!s:.8} – {self.user.username} {self.currency} {self.amount} ({self.status})"

    def complete(self, provider_txn_id: str = '', response: dict = None) -> None:
        """Mark transaction as completed and grant credits/subscription."""
        self.status = 'completed'
        self.completed_at = timezone.now()
        if provider_txn_id:
            self.provider_transaction_id = provider_txn_id
        if response:
            self.provider_response = response
        self.save(update_fields=[
            'status', 'completed_at', 'provider_transaction_id', 'provider_response',
        ])

    def fail(self, response: dict = None) -> None:
        """Mark transaction as failed."""
        self.status = 'failed'
        self.failed_at = timezone.now()
        if response:
            self.provider_response = response
        self.save(update_fields=['status', 'failed_at', 'provider_response'])
