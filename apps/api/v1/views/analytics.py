"""
Analytics API endpoints.

Provides team analysis and performance data with premium/free access control.
"""

import logging
from rest_framework import viewsets, status, generics
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.shortcuts import get_object_or_404

from apps.core.models import Team
from apps.analytics.services.team_analytics_service import TeamAnalyticsService
from apps.payments.services.subscription_service import SubscriptionService
from apps.users.models import UserProfile, PredictionUsage

logger = logging.getLogger(__name__)


class TeamAnalysisViewSet(viewsets.GenericViewSet):
    """
    API viewset for team analysis with premium/free access control.
    
    GET /v1/teams/<id>/analysis/ - Get team analysis data
    POST /v1/teams/<id>/unlock_premium/ - Consume credit to unlock premium features
    """
    queryset = Team.objects.all()
    permission_classes = [IsAuthenticated]

    def list(self, request):
        """List all teams"""
        return Response({'error': 'Use /search/ endpoint'}, status=status.HTTP_400_BAD_REQUEST)

    def retrieve(self, request, pk=None):
        """Get team by ID - redirects to analysis action"""
        return self.analysis(request, pk=pk)

    @action(detail=True, methods=['get'])
    def analysis(self, request, pk=None):
        """
        Get comprehensive team analysis data.
        
        GET /api/v1/teams/<id>/analysis/
        
        Access Control:
        - Free-tier users see: season_stats, form
        - Premium users see: all data including premium_metrics
        
        Query params:
        - days: Period for stats (default 365)
        """
        team = get_object_or_404(Team, id=pk)
        user = request.user

        # Determine access level
        profile, _ = UserProfile.objects.get_or_create(user=user)
        active_sub = SubscriptionService.get_active_subscription(user)

        can_access_premium = active_sub is not None
        access_reason = None

        if active_sub:
            access_reason = f"Premium access via {active_sub.plan.name}"
        elif profile.prediction_credits > 0:
            access_reason = f"Free tier ({profile.prediction_credits} credits available for unlock)"
        else:
            access_reason = "Free tier only"

        # Get team overview
        team_analysis = TeamAnalyticsService.get_team_overview(
            team=team,
            include_premium=can_access_premium
        )

        return Response({
            'success': True,
            'data': team_analysis,
            'can_access_premium': can_access_premium,
            'access_reason': access_reason,
            'user_credits': profile.prediction_credits,
            'active_subscription': {
                'plan': active_sub.plan.name,
                'expires': active_sub.expires_at.isoformat(),
            } if active_sub else None,
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def unlock_premium(self, request, pk=None):
        """
        Consume 1 credit to unlock premium features for this team.
        POST /api/v1/teams/<id>/unlock_premium/
        """
        team = get_object_or_404(Team, id=pk)
        user = request.user
        profile, _ = UserProfile.objects.get_or_create(user=user)

        # Check if user already has active subscription
        active_sub = SubscriptionService.get_active_subscription(user)
        if active_sub:
            return Response({
                'success': False,
                'message': 'You already have premium access via subscription',
            }, status=status.HTTP_400_BAD_REQUEST)

        # Check credits
        if profile.prediction_credits < 1:
            return Response({
                'success': False,
                'message': 'Insufficient credits. You need at least 1 credit.',
                'remaining_credits': profile.prediction_credits,
            }, status=status.HTTP_400_BAD_REQUEST)

        # Consume credit
        profile.consume_credit()

        # Log the action (optional: create a TeamAnalysisAccess record if model exists)
        logger.info(f"User {user.id} unlocked premium analysis for team {team.id} using credit")

        return Response({
            'success': True,
            'message': 'Premium features unlocked for this team',
            'remaining_credits': profile.prediction_credits,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'])
    def search(self, request):
        """
        Search teams by name.
        
        GET /api/v1/teams/search/
        
        Query params:
        - q: Search query (required)
        - league_id: Filter by league (optional)
        """
        query = request.query_params.get('q', '').strip()

        if not query or len(query) < 2:
            return Response({
                'success': False,
                'message': 'Search query must be at least 2 characters',
            }, status=status.HTTP_400_BAD_REQUEST)

        league_id = request.query_params.get('league_id')
        league = None
        if league_id:
            from apps.core.models import League
            league = get_object_or_404(League, id=league_id)

        results = TeamAnalyticsService.search_teams(query, league)

        return Response({
            'success': True,
            'count': len(results),
            'results': results,
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):
        """
        Get basic team statistics (free-tier endpoint).
        
        GET /api/v1/teams/<id>/stats/
        """
        team = get_object_or_404(Team, id=pk)
        days = int(request.query_params.get('days', 365))

        stats = TeamAnalyticsService.get_season_stats(team, days)
        form = TeamAnalyticsService.get_recent_form(team)

        return Response({
            'success': True,
            'team': {
                'id': str(team.id),
                'name': team.name,
                'league': team.league.name if team.league else None,
            },
            'stats': stats,
            'form': form,
        }, status=status.HTTP_200_OK)
