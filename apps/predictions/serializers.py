"""
Serializers for Prediction API resources.
"""

from rest_framework import serializers

from apps.predictions.models import Prediction, PredictionExplanation
from apps.core.models import Match, Team, League


class LeagueSerializer(serializers.ModelSerializer):
    class Meta:
        model = League
        fields = ['id', 'name', 'code', 'country', 'priority']


class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ['id', 'name', 'code', 'logo_url', 'elo_rating']


class MatchSerializer(serializers.ModelSerializer):
    home_team = TeamSerializer(read_only=True)
    away_team = TeamSerializer(read_only=True)
    league = LeagueSerializer(read_only=True)

    class Meta:
        model = Match
        fields = [
            'id', 'match_date', 'status', 'league',
            'home_team', 'away_team', 'home_score', 'away_score'
        ]


class PredictionExplanationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PredictionExplanation
        fields = [
            'factor_name', 'factor_value',
            'impact_score', 'impact_direction', 'display_order'
        ]


class PredictionSerializer(serializers.ModelSerializer):
    match = MatchSerializer(read_only=True)
    explanations = serializers.SerializerMethodField()
    is_access_locked = serializers.SerializerMethodField()

    class Meta:
        model = Prediction
        fields = [
            'id', 'match', 'predicted_outcome', 'confidence_score',
            'home_win_prob', 'draw_prob', 'away_win_prob',
            'tier', 'is_correct', 'resolved_at',
            'explanations', 'is_access_locked'
        ]

    def get_explanations(self, obj):
        # Only show for detailed view or if user has access (handled by view usually)
        # But here we return them; the view might filter fields if needed.
        # For simplicity, we define them, but the frontend might hide them if locked.
        qs = obj.explanations.all().order_by('display_order')
        return PredictionExplanationSerializer(qs, many=True).data

    def get_is_access_locked(self, obj):
        """
        Helper field for frontend to know if this content requires subscription.
        Does not perform the actual check (user context might be needed),
        but indicates if the prediction itself is premium.
        """
        request = self.context.get('request')
        if not request:
            return obj.tier == 'premium'
        
        # If user has access, it's not "locked"
        # Access logic is complex (SubscriptionService), so typically
        # we let the view handle full access control or use a cached property.
        # This is a simple indicator.
        if obj.tier == 'free':
            return False
            
        # If premium, checks if user has rights
        if request.user.is_authenticated:
            # Check if user has active subscription or purchased this prediction
            # This logic is heavy for a list serializer, might skip or simplify.
            # Simplified:
            if hasattr(request, 'has_subscription') and request.has_subscription:
                return False
        
        return True

    def to_representation(self, instance):
        """
        Obscure detailed data for locked premium predictions in list views.
        """
        data = super().to_representation(instance)
        request = self.context.get('request')

        # If locked (premium and no access), maskprobabilities/explanations
        if data['is_access_locked'] and instance.tier == 'premium':
            # Mask details
            data['home_win_prob'] = None
            data['draw_prob'] = None
            data['away_win_prob'] = None
            data['explanations'] = []
            # Optionally mask outcome if strict
            # data['predicted_outcome'] = 'locked' 
        
        return data
