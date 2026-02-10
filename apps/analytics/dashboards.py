"""
Admin Analytics Dashboards.

Provides aggregated analytics data for the admin panel.
"""

import logging
from datetime import timedelta
from collections import defaultdict

from django.db.models import Sum, Count, Avg, F, Q
from django.utils import timezone

from apps.payments.models import PaymentTransaction, SubscriptionPlan, PricingTier, UserSubscription
from apps.predictions.models import Prediction
from apps.analytics.models import AccuracyRecord, RevenueSnapshot
from apps.users.models import PredictionUsage, UserProfile

logger = logging.getLogger(__name__)


class AdminDashboard:
    """
    Generates admin dashboard analytics.
    """

    @classmethod
    def revenue_overview(cls, days: int = 30) -> dict:
        """
        Revenue breakdown by plan, region, and tier.

        Returns:
            {
                'total_revenue': float,
                'by_plan': [{'plan': str, 'revenue': float, 'count': int}],
                'by_region': [{'region': str, 'revenue': float, 'count': int}],
                'by_tier': [{'tier': str, 'revenue': float, 'count': int}],
                'most_profitable_tier': str,
                'daily_trend': [{'date': str, 'revenue': float}],
            }
        """
        cutoff = timezone.now() - timedelta(days=days)
        txns = PaymentTransaction.objects.filter(
            status='completed',
            completed_at__gte=cutoff,
        )

        # Total
        total = txns.aggregate(Sum('amount'))['amount__sum'] or 0

        # By plan (subscription type)
        by_plan = list(
            txns.filter(payment_type='subscription')
            .values(plan_name=F('metadata__plan_name'))
            .annotate(revenue=Sum('amount'), count=Count('id'))
            .order_by('-revenue')
        )

        # By region
        by_region = list(
            txns.values('region')
            .annotate(revenue=Sum('amount'), count=Count('id'))
            .order_by('-revenue')
        )

        # By pricing tier (credit purchases)
        by_tier = list(
            txns.filter(payment_type='credit_purchase')
            .values(tier_name=F('metadata__tier_name'))
            .annotate(revenue=Sum('amount'), count=Count('id'))
            .order_by('-revenue')
        )

        most_profitable = by_tier[0]['tier_name'] if by_tier else 'N/A'

        # Daily trend
        from django.db.models.functions import TruncDate
        daily_trend = list(
            txns.annotate(date=TruncDate('completed_at'))
            .values('date')
            .annotate(revenue=Sum('amount'))
            .order_by('date')
        )

        return {
            'total_revenue': float(total),
            'by_plan': by_plan,
            'by_region': by_region,
            'by_tier': by_tier,
            'most_profitable_tier': most_profitable,
            'daily_trend': daily_trend,
            'period_days': days,
        }

    @classmethod
    def user_activity(cls, days: int = 30) -> dict:
        """
        Prediction usage analytics per user.
        """
        cutoff = timezone.now() - timedelta(days=days)

        usage = PredictionUsage.objects.filter(date__gte=cutoff.date())

        # Top consumers
        top_users = list(
            usage.values('user__username', 'user__email')
            .annotate(
                total_used=Count('id'),
                paid=Count('id', filter=Q(is_free=False)),
                free=Count('id', filter=Q(is_free=True)),
            )
            .order_by('-total_used')[:20]
        )

        # Daily active users
        dau = list(
            usage.values('date')
            .annotate(users=Count('user', distinct=True))
            .order_by('date')
        )

        return {
            'top_users': top_users,
            'daily_active_users': dau,
            'total_predictions_consumed': usage.count(),
            'paid_vs_free': {
                'paid': usage.filter(is_free=False).count(),
                'free': usage.filter(is_free=True).count(),
            },
        }

    @classmethod
    def accuracy_vs_revenue(cls) -> dict:
        """
        Correlation between prediction accuracy and revenue.
        """
        snapshots = RevenueSnapshot.objects.order_by('date')
        accuracy_records = AccuracyRecord.objects.filter(
            league__isnull=True, period='daily',
        ).order_by('period_start')

        data_points = []
        for acc in accuracy_records:
            rev = snapshots.filter(date=acc.period_start).first()
            if rev:
                data_points.append({
                    'date': acc.period_start.isoformat(),
                    'accuracy': float(acc.accuracy_rate),
                    'revenue': float(rev.total_revenue),
                })

        return {
            'data_points': data_points,
            'count': len(data_points),
        }

    @classmethod
    def subscription_health(cls) -> dict:
        """Active subscription breakdown."""
        active = UserSubscription.objects.filter(status='active')

        by_plan = list(
            active.values('plan__name')
            .annotate(count=Count('id'))
            .order_by('-count')
        )

        total_active = active.count()
        expiring_soon = active.filter(
            expires_at__lte=timezone.now() + timedelta(days=7),
        ).count()

        churn = UserSubscription.objects.filter(
            status__in=['cancelled', 'expired'],
            updated_at__gte=timezone.now() - timedelta(days=30),
        ).count()

        return {
            'total_active': total_active,
            'by_plan': by_plan,
            'expiring_within_7_days': expiring_soon,
            'churn_last_30_days': churn,
        }

    @classmethod
    def regional_breakdown(cls) -> dict:
        """User distribution by region."""
        return list(
            UserProfile.objects.values('region', 'country')
            .annotate(users=Count('id'))
            .order_by('-users')
        )
