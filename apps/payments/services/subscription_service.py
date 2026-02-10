"""
Subscription & Credit Management Service.

Handles:
    - Subscription lifecycle (activate, grace, expire, cancel)
    - Credit-based access enforcement
    - Daily quota management
    - Expiry handling
    - Feature gating (anonymous 2, signup 3, paid tiers)
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from apps.payments.models import UserSubscription, SubscriptionPlan
from apps.users.models import UserProfile, PredictionUsage

logger = logging.getLogger(__name__)


class SubscriptionService:
    """
    Central service for subscription and credit logic.
    """

    # ── Feature Gating Constants ────────────────

    ANONYMOUS_FREE_VIEWS = 2          # Free premium predictions before login
    SIGNUP_BONUS_CREDITS = 3          # Free premium predictions after signup
    GRACE_PERIOD_DAYS = 3

    # ── Access Checks ───────────────────────────

    @classmethod
    def can_access_prediction(cls, user, prediction, session=None) -> dict:
        """
        Determine if a user can view a prediction.

        Returns {
            'allowed': bool,
            'reason': str,
            'remaining_credits': int,
            'requires_login': bool,
            'requires_payment': bool,
        }
        """
        from apps.predictions.models import Prediction

        # Free-tier predictions are always visible
        if prediction.tier == 'free':
            return {
                'allowed': True,
                'reason': 'Free prediction',
                'remaining_credits': -1,
                'requires_login': False,
                'requires_payment': False,
            }

        # Anonymous user gate
        if not user or not user.is_authenticated:
            return cls._check_anonymous_access(session, prediction)

        # Authenticated user — check subscription first
        active_sub = cls.get_active_subscription(user)
        if active_sub:
            return cls._check_subscription_access(user, active_sub)

        # Check credits
        profile = cls._get_profile(user)
        if profile.prediction_credits > 0:
            return {
                'allowed': True,
                'reason': f'{profile.prediction_credits} credits remaining',
                'remaining_credits': profile.prediction_credits,
                'requires_login': False,
                'requires_payment': False,
            }

        return {
            'allowed': False,
            'reason': 'No credits or active subscription',
            'remaining_credits': 0,
            'requires_login': False,
            'requires_payment': True,
        }

    @classmethod
    def consume_prediction(cls, user, prediction, session=None) -> bool:
        """
        Consume a prediction credit/daily quota.
        Returns True if consumed successfully.
        """
        from apps.predictions.models import Prediction

        if prediction.tier == 'free':
            # Log usage for authenticated users
            if user and user.is_authenticated:
                PredictionUsage.objects.create(
                    user=user,
                    prediction=prediction,
                    credits_used=0,
                    is_free=True,
                )
            return True

        # Anonymous consumption
        if not user or not user.is_authenticated:
            if session:
                views = session.get('anonymous_views', 0)
                session['anonymous_views'] = views + 1
                session.modified = True
                return True
            return False

        # Try subscription daily quota first
        active_sub = cls.get_active_subscription(user)
        if active_sub:
            if active_sub.consume_daily():
                PredictionUsage.objects.create(
                    user=user,
                    prediction=prediction,
                    credits_used=0,
                    is_free=False,
                )
                return True

        # Try credits
        profile = cls._get_profile(user)
        if profile.consume_credit():
            PredictionUsage.objects.create(
                user=user,
                prediction=prediction,
                credits_used=1,
                is_free=False,
            )
            return True

        return False

    # ── Subscription Lifecycle ──────────────────

    @staticmethod
    def get_active_subscription(user) -> UserSubscription | None:
        """Get user's current active/grace subscription."""
        return UserSubscription.objects.filter(
            user=user,
            status__in=['active', 'grace'],
        ).select_related('plan').first()

    @classmethod
    def check_and_handle_expirations(cls) -> int:
        """
        Celery-called: check all subscriptions for expiry.
        Returns count of subscriptions transitioned.
        """
        now = timezone.now()
        count = 0

        # Active → Grace (expired but within grace)
        expired_active = UserSubscription.objects.filter(
            status='active',
            expires_at__lte=now,
        )
        for sub in expired_active:
            sub.enter_grace(grace_days=cls.GRACE_PERIOD_DAYS)
            count += 1
            logger.info(f"Subscription {sub.id} entered grace period")

        # Grace → Expired
        expired_grace = UserSubscription.objects.filter(
            status='grace',
            grace_ends_at__lte=now,
        )
        for sub in expired_grace:
            sub.expire()
            count += 1
            logger.info(f"Subscription {sub.id} expired")

        return count

    @classmethod
    def handle_unused_credit_expiry(cls, days: int = 90) -> int:
        """
        Expire unused credits older than `days`.
        Returns count of profiles affected.
        """
        cutoff = timezone.now() - timedelta(days=days)
        expired = UserProfile.objects.filter(
            prediction_credits__gt=0,
            updated_at__lt=cutoff,
        )
        count = expired.count()
        expired.update(prediction_credits=0)
        return count

    # ── Daily Limits ────────────────────────────

    @classmethod
    def get_daily_usage(cls, user) -> dict:
        """Get today's prediction usage for a user."""
        today = timezone.now().date()
        usage = PredictionUsage.objects.filter(
            user=user,
            date=today,
        ).aggregate(
            total=Sum('credits_used'),
        )

        active_sub = cls.get_active_subscription(user)
        daily_limit = active_sub.plan.daily_prediction_limit if active_sub else 0
        used_today = active_sub.daily_predictions_used_today if active_sub else 0

        return {
            'credits_used_today': usage['total'] or 0,
            'daily_limit': daily_limit,
            'daily_used': used_today,
            'daily_remaining': max(0, daily_limit - used_today),
        }

    # ── Private Helpers ─────────────────────────

    @classmethod
    def _check_anonymous_access(cls, session, prediction) -> dict:
        """Check if anonymous user has free views remaining."""
        if not session:
            return {
                'allowed': False,
                'reason': 'Session required for anonymous access',
                'remaining_credits': 0,
                'requires_login': True,
                'requires_payment': False,
            }
            
        views_used = session.get('anonymous_views', 0)
        remaining = cls.ANONYMOUS_FREE_VIEWS - views_used
        
        if remaining > 0:
             return {
                'allowed': True,
                'reason': f'{remaining} free anonymous views remaining',
                'remaining_credits': remaining,
                'requires_login': False,
                'requires_payment': False,
            }
            
        return {
            'allowed': False,
            'reason': 'Login required to view more premium predictions',
            'remaining_credits': 0,
            'requires_login': True,
            'requires_payment': False,
        }

    @classmethod
    def _check_subscription_access(cls, user, subscription: UserSubscription) -> dict:
        """Check if subscription allows access."""
        if not subscription.is_usable:
            return {
                'allowed': False,
                'reason': 'Subscription expired',
                'remaining_credits': 0,
                'requires_login': False,
                'requires_payment': True,
            }

        subscription.reset_daily_quota()
        remaining = subscription.plan.daily_prediction_limit - subscription.daily_predictions_used_today

        if remaining <= 0:
            return {
                'allowed': False,
                'reason': 'Daily prediction limit reached',
                'remaining_credits': 0,
                'requires_login': False,
                'requires_payment': False,
            }

        return {
            'allowed': True,
            'reason': f'{remaining} predictions remaining today',
            'remaining_credits': remaining,
            'requires_login': False,
            'requires_payment': False,
        }

    @staticmethod
    def _get_profile(user) -> UserProfile:
        """Get or create user profile."""
        profile, _ = UserProfile.objects.get_or_create(user=user)
        return profile
