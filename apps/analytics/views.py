from django.shortcuts import render
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Sum, Avg, Q
from django.utils import timezone
from datetime import timedelta

from apps.core.models import Match
from apps.predictions.models import Prediction
from .models import AccuracyRecord


class HistoricalDataView(LoginRequiredMixin, TemplateView):
    """Display historical match data and predictions."""
    
    template_name = 'pages/historical_data.html'
    login_url = 'login'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get recent matches with predictions
        matches = Match.objects.all().order_by('-match_date')[:100]
        
        # Get accuracy records
        accuracy_records = AccuracyRecord.objects.all().order_by('-period_start')[:30]
        
        # Get prediction stats
        predictions = Prediction.objects.all()
        
        context.update({
            'recent_matches': matches,
            'accuracy_records': accuracy_records,
            'total_predictions': predictions.count(),
            'total_correct': predictions.filter(is_correct=True).count(),
            'total_incorrect': predictions.filter(is_correct=False).count(),
            'total_pending': predictions.filter(resolved_at__isnull=True).count(),
        })
        
        return context
