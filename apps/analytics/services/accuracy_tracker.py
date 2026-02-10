"""
Analytics Service.

Handles accuracy tracking and revenue reporting.
- Calculates daily/weekly accuracy rates.
- Updates immutable AccuracyRecord snapshots.
- Tracks prediction success/failure based on match results.
"""

from datetime import date, timedelta
from django.db.models import Avg, Count, Sum, Q
from django.utils import timezone

from apps.core.models import Match, League
from apps.predictions.models import Prediction
from apps.analytics.models import AccuracyRecord, RevenueSnapshot
from apps.payments.models import PaymentTransaction


class AnalyticsService:

    @classmethod
    def update_accuracy_stats(cls):
        """
        Update accuracy records for Yesterday, This Week, This Month.
        Called by Celery daily (e.g. at 02:00 UTC).
        """
        today = timezone.now().date()
        yesterday = today - timedelta(days=1)
        
        # 1. Resolve pending predictions first
        cls._resolve_predictions()

        # 2. Calculate and store daily stats
        cls._calculate_period_stats(yesterday, yesterday, 'daily')

        # 3. Calculate weekly (last 7 days)
        week_start = today - timedelta(days=7)
        cls._calculate_period_stats(week_start, yesterday, 'weekly')
        
        # 4. Calculate monthly (last 30 days)
        month_start = today - timedelta(days=30)
        cls._calculate_period_stats(month_start, yesterday, 'monthly')

        # 5. All Time
        all_time_start = date(2023, 1, 1) # Project start
        cls._calculate_period_stats(all_time_start, yesterday, 'all_time')

    @classmethod
    def _resolve_predictions(cls):
        """
        Mark predictions as correct/incorrect based on finished matches.
        """
        pending = Prediction.objects.filter(
            is_correct__isnull=True,
            match__status='finished',
            match__home_score__isnull=False
        ).select_related('match')

        for p in pending:
            actual = p.match.actual_outcome
            if actual:
                p.resolve(actual)

    @classmethod
    def _calculate_period_stats(cls, start_date, end_date, period_name):
        """
        Calculate accuracy for a given date range and store record.
        """
        # Overall Stats
        cls._create_record(None, start_date, end_date, period_name)

        # Per League Stats
        leagues = League.objects.filter(is_active=True)
        for league in leagues:
            cls._create_record(league, start_date, end_date, period_name)

    @classmethod
    def _create_record(cls, league, start, end, period):
        """
        Create (or update) an AccuracyRecord.
        """
        filters = Q(match__match_date__date__gte=start) & \
                  Q(match__match_date__date__lte=end) & \
                  Q(is_correct__isnull=False)

        if league:
            filters &= Q(match__league=league)

        qs = Prediction.objects.filter(filters)
        total = qs.count()
        
        if total == 0:
            return

        correct = qs.filter(is_correct=True).count()
        accuracy = (correct / total) * 100 if total > 0 else 0
        
        # Outcome specific accuracy
        home_total = qs.filter(predicted_outcome='home_win').count()
        home_correct = qs.filter(predicted_outcome='home_win', is_correct=True).count()
        home_acc = (home_correct / home_total * 100) if home_total > 0 else 0

        draw_total = qs.filter(predicted_outcome='draw').count()
        draw_correct = qs.filter(predicted_outcome='draw', is_correct=True).count()
        draw_acc = (draw_correct / draw_total * 100) if draw_total > 0 else 0

        away_total = qs.filter(predicted_outcome='away_win').count()
        away_correct = qs.filter(predicted_outcome='away_win', is_correct=True).count()
        away_acc = (away_correct / away_total * 100) if away_total > 0 else 0

        avg_conf = qs.aggregate(Avg('confidence_score'))['confidence_score__avg'] or 0

        AccuracyRecord.objects.update_or_create(
            league=league,
            period=period,
            period_start=start,
            defaults={
                'period_end': end,
                'total_predictions': total,
                'correct_predictions': correct,
                'incorrect_predictions': total - correct,
                'accuracy_rate': round(accuracy, 2),
                'home_win_accuracy': round(home_acc, 2),
                'draw_accuracy': round(draw_acc, 2),
                'away_win_accuracy': round(away_acc, 2),
                'avg_confidence': round(avg_conf, 2),
            }
        )

    @classmethod
    def generate_revenue_snapshot(cls, target_date=None):
        """
        Generate daily revenue report (Admin only).
        """
        if not target_date:
            target_date = timezone.now().date() - timedelta(days=1)

        txns = PaymentTransaction.objects.filter(
            status='completed',
            completed_at__date=target_date
        )

        # Global totals
        total_rev = txns.aggregate(Sum('amount'))['amount__sum'] or 0
        subscriptions = txns.filter(payment_type='subscription').aggregate(Sum('amount'))['amount__sum'] or 0
        credits = txns.filter(payment_type='credit_purchase').aggregate(Sum('amount'))['amount__sum'] or 0
        
        # Simple implementation: one global snapshot for now (can expand to regions)
        RevenueSnapshot.objects.update_or_create(
            date=target_date,
            region='global',
            defaults={
                'total_revenue': total_rev,
                'subscription_revenue': subscriptions,
                'credit_revenue': credits,
                'total_transactions': txns.count(),
                'currency': 'USD' # Simplified currency handling for snapshot
            }
        )
