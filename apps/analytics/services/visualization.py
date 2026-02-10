"""
Visualization helpers for analytics.

Generates chart data structures for frontend rendering.
"""

import logging
from datetime import timedelta

from django.db.models import Sum, Count, Avg
from django.db.models.functions import TruncDate, TruncWeek
from django.utils import timezone

from apps.analytics.models import AccuracyRecord, RevenueSnapshot
from apps.predictions.models import Prediction
from apps.payments.models import PaymentTransaction

logger = logging.getLogger(__name__)


class ChartDataService:
    """
    Generates structured data for frontend charts (Chart.js / ApexCharts).
    """

    @classmethod
    def accuracy_timeline(cls, days: int = 90, league_code: str = None) -> dict:
        """
        Accuracy over time — line chart data.
        """
        qs = AccuracyRecord.objects.filter(period='daily').order_by('period_start')

        if league_code:
            qs = qs.filter(league__code=league_code)
        else:
            qs = qs.filter(league__isnull=True)

        cutoff = timezone.now().date() - timedelta(days=days)
        qs = qs.filter(period_start__gte=cutoff)

        return {
            'labels': [r.period_start.isoformat() for r in qs],
            'datasets': [
                {
                    'label': 'Overall Accuracy',
                    'data': [float(r.accuracy_rate) for r in qs],
                    'borderColor': '#4ade80',
                },
                {
                    'label': 'Home Win Accuracy',
                    'data': [float(r.home_win_accuracy) for r in qs],
                    'borderColor': '#60a5fa',
                },
                {
                    'label': 'Draw Accuracy',
                    'data': [float(r.draw_accuracy) for r in qs],
                    'borderColor': '#fbbf24',
                },
                {
                    'label': 'Away Win Accuracy',
                    'data': [float(r.away_win_accuracy) for r in qs],
                    'borderColor': '#f87171',
                },
            ],
            'type': 'line',
        }

    @classmethod
    def revenue_chart(cls, days: int = 30) -> dict:
        """
        Revenue over time — bar chart data.
        """
        cutoff = timezone.now() - timedelta(days=days)
        txns = (
            PaymentTransaction.objects
            .filter(status='completed', completed_at__gte=cutoff)
            .annotate(date=TruncDate('completed_at'))
            .values('date')
            .annotate(
                total=Sum('amount'),
                subscriptions=Sum('amount', filter=({'payment_type': 'subscription'})),
                credits=Sum('amount', filter=({'payment_type': 'credit_purchase'})),
            )
            .order_by('date')
        )

        return {
            'labels': [t['date'].isoformat() for t in txns],
            'datasets': [
                {
                    'label': 'Total Revenue',
                    'data': [float(t['total'] or 0) for t in txns],
                    'backgroundColor': '#8b5cf6',
                },
                {
                    'label': 'Subscriptions',
                    'data': [float(t['subscriptions'] or 0) for t in txns],
                    'backgroundColor': '#06b6d4',
                },
                {
                    'label': 'Credit Packs',
                    'data': [float(t['credits'] or 0) for t in txns],
                    'backgroundColor': '#f59e0b',
                },
            ],
            'type': 'bar',
        }

    @classmethod
    def prediction_distribution(cls, days: int = 30) -> dict:
        """
        Prediction outcome distribution — pie/doughnut chart.
        """
        cutoff = timezone.now() - timedelta(days=days)
        qs = Prediction.objects.filter(
            match__match_date__gte=cutoff,
        )

        home = qs.filter(predicted_outcome='home_win').count()
        draw = qs.filter(predicted_outcome='draw').count()
        away = qs.filter(predicted_outcome='away_win').count()

        return {
            'labels': ['Home Win', 'Draw', 'Away Win'],
            'datasets': [{
                'data': [home, draw, away],
                'backgroundColor': ['#4ade80', '#fbbf24', '#f87171'],
            }],
            'type': 'doughnut',
        }

    @classmethod
    def confidence_distribution(cls, days: int = 30) -> dict:
        """
        Confidence score distribution — histogram data.
        """
        cutoff = timezone.now() - timedelta(days=days)
        buckets = [(40, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 100)]

        counts = []
        for low, high in buckets:
            c = Prediction.objects.filter(
                match__match_date__gte=cutoff,
                confidence_score__gte=low,
                confidence_score__lt=high,
            ).count()
            counts.append(c)

        return {
            'labels': [f'{l}-{h}%' for l, h in buckets],
            'datasets': [{
                'label': 'Predictions',
                'data': counts,
                'backgroundColor': '#8b5cf6',
            }],
            'type': 'bar',
        }
