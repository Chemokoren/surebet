"""
Main URL configuration for futurapredict.
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import TemplateView
from django.contrib.auth import views as auth_views

# Import views
from apps.api.views import (
    HomeView, PredictionsView, PredictionDetailView, TeamAnalysisView,
    SubscriptionView, ProfileView, PaymentView, LoginView, LogoutView,
    DataExportView, AccountDeleteView, UnlockPredictionView,
    PaymentSuccessView, PaymentCancelView, RegisterView
)
from apps.analytics.views import HistoricalDataView
from apps.users.views import GoogleAuthStartView, GoogleAuthCallbackView

# Webhook views
from apps.payments.webhooks.mpesa_webhook import MpesaCallbackView
from apps.payments.webhooks.stripe_webhook import StripeWebhookView

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # Frontend pages
    path('', HomeView.as_view(), name='home'),
    path('predictions/', PredictionsView.as_view(), name='predictions'),
    path('predictions/<uuid:pk>/', PredictionDetailView.as_view(), name='prediction_detail'),
    path('predictions/<uuid:pk>/unlock/', UnlockPredictionView.as_view(), name='unlock_prediction'),
    path('analytics/teams/', TeamAnalysisView.as_view(), name='team_analysis'),
    path('analytics/historical/', HistoricalDataView.as_view(), name='historical_data'),
    path('analytics/accuracy/', TemplateView.as_view(template_name='pages/accuracy_dashboard.html'), name='accuracy_dashboard'),
    
    # Account pages
    path('account/', include([
        path('login/', LoginView.as_view(), name='login'),
        path('logout/', LogoutView.as_view(), name='logout'),
        # Password reset flow (uses Django's built-in auth views)
        path('password-reset/', auth_views.PasswordResetView.as_view(), name='password_reset'),
        path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
        path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
        path('reset/done/', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
        # User registration (signup)
        path('register/', RegisterView.as_view(), name='register'),
        path('google/login/', GoogleAuthStartView.as_view(), name='google_auth_start'),
        path('google/callback/', GoogleAuthCallbackView.as_view(), name='google_auth_callback'),
        path('signup-success/', TemplateView.as_view(template_name='account/signup_success.html'), name='signup_success'),
        path('profile/', ProfileView.as_view(), name='profile'),
        path('subscription/', SubscriptionView.as_view(), name='subscription'),
        path('payment/', PaymentView.as_view(), name='payment'),
        path('payment/success/', PaymentSuccessView.as_view(), name='payment_success'),
        path('payment/cancel/', PaymentCancelView.as_view(), name='payment_cancel'),
        # GDPR
        path('export/', DataExportView.as_view(), name='data_export'),
        path('delete/', AccountDeleteView.as_view(), name='account_delete'),
    ])),
    
    # Payment Webhooks (CSRF-exempt, no auth required)
    path('webhooks/', include([
        path('mpesa/callback/', MpesaCallbackView.as_view(), name='mpesa_callback'),
        path('stripe/', StripeWebhookView.as_view(), name='stripe_webhook'),
    ])),
    
    # API v1 endpoints
    path('v1/', include('apps.api.v1.urls')),

    # PWA
    path('manifest.json', TemplateView.as_view(
        template_name='manifest.json',
        content_type='application/json'
    ), name='manifest'),
    
    path('offline/', TemplateView.as_view(template_name='offline.html'), name='offline'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
