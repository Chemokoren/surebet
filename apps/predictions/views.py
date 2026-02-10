from rest_framework import viewsets, permissions, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from datetime import timedelta

from apps.predictions.models import Prediction
from apps.core.models import League, Match
from apps.payments.services.subscription_service import SubscriptionService
from apps.predictions.serializers import PredictionSerializer


class PredictionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API for retrieving match predictions.
    Filters: league, tier, date range.
    """
    serializer_class = PredictionSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]  # Free predictions visible

    @method_decorator(cache_page(60 * 5))
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        """
        Return upcoming predictions or recent historical.
        """
        user = self.request.user
        qs = Prediction.objects.select_related('match', 'match__home_team', 'match__away_team', 'match__league')
        
        # Default filtering
        today = timezone.now().date()
        date_from = self.request.query_params.get('start_date', today)  # Default today
        date_to = self.request.query_params.get('end_date', today + timedelta(days=2))

        qs = qs.filter(match__match_date__date__gte=date_from)
        qs = qs.filter(match__match_date__date__lte=date_to)

        # Filter by league
        league = self.request.query_params.get('league')
        if league:
            qs = qs.filter(match__league__code=league)

        return qs.order_by('match__match_date')

    def retrieve(self, request, *args, **kwargs):
        """
        Check access rights before returning a single prediction detail.
        """
        prediction = self.get_object()
        
        # Access Logic from SubscriptionService
        access = SubscriptionService.can_access_prediction(request.user, prediction, session=request.session)
        
        if not access['allowed']:
            return Response({
                'error': 'Access Denied',
                'reason': access['reason'],
                'requires_payment': access['requires_payment'],
                'requires_login': access['requires_login']
            }, status=status.HTTP_403_FORBIDDEN)
        
        # Consume if needed (POST action usually preferred, but for retrieve view logging usage)
        if access['remaining_credits'] != -1:  # -1 means free/unlimited
             SubscriptionService.consume_prediction(request.user, prediction, session=request.session)

        serializer = self.get_serializer(prediction)
        return Response(serializer.data)


class LeagueViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = League.objects.filter(is_active=True).order_by('priority')
    permission_classes = [permissions.AllowAny]
    serializer_class = __import__('apps.predictions.serializers', fromlist=['LeagueSerializer']).LeagueSerializer

    @method_decorator(cache_page(60 * 60))  # 1 hour cache for leagues
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
