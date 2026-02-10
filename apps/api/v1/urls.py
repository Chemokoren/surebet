"""
API v1 URL configuration for predictions, teams, and analytics endpoints.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.api.views import RegisterView

app_name = 'api_v1'

# API Routers
router = DefaultRouter()

urlpatterns = [
    path('', include(router.urls)),

    # Predictions endpoints (to be implemented)
    # path('predictions/', include([...]),

    # Analytics endpoints (to be implemented)
    # path('analytics/', include([...]),

    # Subscriptions endpoints (to be implemented)
    # path('subscriptions/', include([...]),

    # Account / authentication API endpoints
    path('account/register/', RegisterView.as_view(), name='account_register'),
]
