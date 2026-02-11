from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.predictions.views import PredictionViewSet, LeagueViewSet
from apps.api.v1.views.analytics import TeamAnalysisViewSet

app_name = 'api_v1'

router = DefaultRouter()
router.register(r'predictions', PredictionViewSet, basename='prediction')
router.register(r'leagues', LeagueViewSet, basename='league')
router.register(r'teams', TeamAnalysisViewSet, basename='team-analysis')

urlpatterns = [
    path('', include(router.urls)),
]
