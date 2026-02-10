from rest_framework import viewsets, status, views
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.utils import timezone
from django.shortcuts import render, redirect
from django.views.generic import TemplateView, ListView, CreateView, FormView, View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.views import LogoutView
from django.urls import reverse_lazy
from django.http import HttpResponseRedirect, JsonResponse
from django.contrib.auth.forms import AuthenticationForm
from apps.users.forms import EmailAuthenticationForm, UserRegistrationForm
from datetime import datetime, timedelta
from django.contrib import messages
from apps.payments.services.payment_service import PaymentService
from apps.payments.models import PricingTier, SubscriptionPlan, PaymentChannel, PaymentTransaction, UserSubscription
from apps.users.models import UserProfile


from django.db.models import Q
from apps.core.models import Match, League, Team
from apps.predictions.models import Prediction
from apps.analytics.models import AccuracyRecord
from apps.payments.services.subscription_service import SubscriptionService

class HomeView(LoginRequiredMixin, TemplateView):
    """Home/Dashboard view"""
    template_name = 'pages/home.html'
    login_url = '/account/login/'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.now().date()
        
        # 1. Fetch Today's Highlights
        context['todays_matches'] = Match.objects.filter(
            match_date__date=today,
            status='scheduled'
        ).select_related('home_team', 'away_team', 'league').order_by('match_date')[:5]
        
        # 2. Recent Accuracy (Last 7 Days)
        context['accuracy'] = AccuracyRecord.objects.filter(
            period='weekly'
        ).order_by('-period_start').first()
        
        # 3. User Stats
        if self.request.user.is_authenticated:
            context['usage'] = SubscriptionService.get_daily_usage(self.request.user)
            context['active_sub'] = SubscriptionService.get_active_subscription(self.request.user)
            
        return context


class PredictionsView(LoginRequiredMixin, TemplateView):
    """Predictions page view"""
    template_name = 'pages/predictions.html'
    login_url = '/account/login/'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.now().date()
        
        # Filter params
        league_code = self.request.GET.get('league')
        
        # Base Query
        qs = Prediction.objects.filter(
            match__match_date__date__gte=today,
            match__status='scheduled'
        ).select_related('match', 'match__home_team', 'match__away_team', 'match__league')
        
        if league_code:
            qs = qs.filter(match__league__code=league_code)
            
        context['predictions'] = qs.order_by('match__match_date')
        context['leagues'] = League.objects.filter(is_active=True)
        context['selected_league'] = league_code
        
        # Check access for each prediction (naive implementation for template)
        # In production, this might be done via AJAX to avoid N+1 or bulk checked
        context['user_credits'] = 0
        if self.request.user.is_authenticated:
             from apps.users.models import UserProfile
             profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
             context['user_credits'] = profile.prediction_credits

        return context


class TeamAnalysisView(LoginRequiredMixin, TemplateView):
    """Team analysis page view"""
    template_name = 'pages/team_analysis.html'
    login_url = '/account/login/'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Simple list of teams for the dropdown/search
        context['teams'] = Team.objects.all().order_by('name')[:50] 
        return context


class SubscriptionView(LoginRequiredMixin, TemplateView):
    """Subscription management view"""
    template_name = 'account/subscription.html'
    login_url = '/account/login/'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Fetch available plans (exclude credit packs)
        context['plans'] = SubscriptionPlan.objects.filter(is_active=True).order_by('price')
        
        # User's current sub
        context['active_sub'] = SubscriptionService.get_active_subscription(self.request.user)
        
        # Payment channels (for checkout modal)
        profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
        region = getattr(profile, 'region', 'global')
        context['channels'] = PaymentService.get_available_channels(region)
        return context

    def post(self, request, *args, **kwargs):
        """Handle subscription initialization"""
        plan_id = request.POST.get('plan_id')
        provider = request.POST.get('provider')
        phone = request.POST.get('phone', '')

        if not plan_id or not provider:
            messages.error(request, "Please select a plan and payment method.")
            return redirect('subscription')

        try:
            # Initiate
            txn, result = PaymentService.initiate_subscription(
                user=request.user,
                plan_id=plan_id,
                provider=provider,
                phone_number=phone,
                return_url=request.build_absolute_uri('/account/payment/success/'),
            )
            
            if result.redirect_url:
                return redirect(result.redirect_url)
            elif result.success:
                 messages.success(request, "Payment initiated. Please check your phone/email.")
                 return redirect('payment_success')
            else:
                 messages.error(request, f"Payment failed: {result.message}")
                 return redirect('subscription')
                 
        except Exception as e:
            messages.error(request, f"System error: {str(e)}")
            return redirect('subscription')


class ProfileView(LoginRequiredMixin, TemplateView):
    """User profile view"""
    template_name = 'account/profile.html'
    login_url = '/account/login/'


class LoginView(FormView):
    """User login view - handles both GET and POST"""
    template_name = 'account/login.html'
    form_class = EmailAuthenticationForm
    success_url = reverse_lazy('home')
    
    def dispatch(self, request, *args, **kwargs):
        """Redirect if already logged in"""
        if request.user.is_authenticated:
            return HttpResponseRedirect(self.get_success_url())
        return super().dispatch(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Get next URL from GET parameters for redirect after login
        context['next'] = self.request.GET.get('next', '')
        return context
    
    def form_valid(self, form):
        """Log the user in and redirect"""
        user = form.get_user()
        login(self.request, user)
        
        # Redirect to next URL if provided, otherwise go to home
        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url:
            return HttpResponseRedirect(next_url)
        return HttpResponseRedirect(self.get_success_url())


class LogoutView(LogoutView):
    """User logout view"""
    next_page = reverse_lazy('login')


class RegisterView(CreateView):
    """User registration view"""
    template_name = 'account/register.html'
    form_class = UserRegistrationForm
    success_url = reverse_lazy('login')
    
    def form_valid(self, form):
        """Handle successful registration"""
        user = form.save()
        login(self.request, user)  # Auto-login after registration
        return redirect('home')

    def dispatch(self, request, *args, **kwargs):
        """Redirect if already logged in"""
        if request.user.is_authenticated:
            return redirect('home')
        return super().dispatch(request, *args, **kwargs)


class PaymentView(LoginRequiredMixin, TemplateView):
    """Credit purchase view"""
    template_name = 'account/payment.html'
    login_url = '/account/login/'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Determine user region
        from apps.users.models import UserProfile
        profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
        region = getattr(profile, 'region', 'global')
        
        # Get Tiers
        context['tiers'] = PaymentService.get_pricing_tiers(region)
        
        # Get Channels
        context['channels'] = PaymentService.get_available_channels(region)
        
        return context

    def post(self, request, *args, **kwargs):
        tier_id = request.POST.get('tier_id')
        provider = request.POST.get('provider')
        phone = request.POST.get('phone', '')

        if not tier_id or not provider:
            messages.error(request, "Invalid selection.")
            return redirect('payment_page')

        try:
            txn, result = PaymentService.initiate_credit_purchase(
                user=request.user,
                pricing_tier_id=tier_id,
                provider=provider,
                phone_number=phone,
                return_url=request.build_absolute_uri('/account/payment/success/'),
            )
            
            if result.redirect_url:
                return redirect(result.redirect_url)
            elif result.success:
                 messages.success(request, "Payment initiated.")
                 return redirect('payment_success')
            else:
                 messages.error(request, f"Payment failed: {result.message}")
                 return redirect('payment_page')
                 
        except Exception as e:
            messages.error(request, f"Error: {str(e)}")
            return redirect('payment_page')


class PaymentSuccessView(LoginRequiredMixin, TemplateView):
    template_name = 'account/payment_success.html'

class PaymentCancelView(LoginRequiredMixin, TemplateView):
    template_name = 'account/payment_cancel.html'


class DataExportView(LoginRequiredMixin, View):
    """
    GDPR: Export all user data as JSON.
    """
    def get(self, request):
        user = request.user
        profile, _ = UserProfile.objects.get_or_create(user=user)
        
        # Subscriptions
        subs = UserSubscription.objects.filter(user=user).values(
            'plan__name', 'status', 'start_date', 'expires_at'
        )
        
        # Transactions
        txns = PaymentTransaction.objects.filter(user=user).values(
            'amount', 'currency', 'status', 'completed_at', 'provider'
        )
        
        data = {
            'username': user.username,
            'email': user.email,
            'date_joined': user.date_joined.isoformat(),
            'profile': {
                'country': profile.country,
                'region': profile.region,
                'credits': profile.prediction_credits,
                'phone': profile.phone_number,
            },
            'subscriptions': list(subs),
            'payment_history': list(txns),
        }
        
        response = JsonResponse(data, json_dumps_params={'default': str})
        response['Content-Disposition'] = f'attachment; filename="data_export_{user.username}.json"'
        return response


class AccountDeleteView(LoginRequiredMixin, View):
    """
    GDPR: Allow user to delete their account (soft delete/deactivation).
    """
    def post(self, request):
        user = request.user
        
        # Log outcome
        message = f"User {user.username} requested account deletion."
        
        try:
            # Soft delete - deactivate
            user.is_active = False
            user.save()
            
            # Additional cleanup or PII scrubbing could go here
            logout(request)
            messages.success(request, "Your account has been deactivated.")
            return redirect('home')
        except Exception as e:
            messages.error(request, f"Error deleting account: {str(e)}")
            return redirect('profile')


# API Views
class PredictionAPIViewSet(viewsets.ViewSet):
    """API endpoint for predictions"""
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['post'])
    def filter(self, request):
        """Filter predictions based on criteria"""
        filters = request.data
        
        # Mock data for demonstration
        predictions = [
            {
                'id': 1,
                'home_team': 'Manchester United',
                'away_team': 'Liverpool',
                'league': 'Premier League',
                'league_class': 'premier-league',
                'predicted_outcome': 'home_win',
                'confidence_score': 78,
                'match_date': '2026-02-15 20:00'
            },
            {
                'id': 2,
                'home_team': 'Barcelona',
                'away_team': 'Real Madrid',
                'league': 'La Liga',
                'league_class': 'la-liga',
                'predicted_outcome': 'draw',
                'confidence_score': 65,
                'match_date': '2026-02-16 21:00'
            },
        ]
        
        return Response({'predictions': predictions})
    
    @action(detail=False, methods=['get'])
    def upcoming(self, request):
        """Get upcoming predictions"""
        predictions = []
        return Response({'predictions': predictions})
    
    @action(detail=True, methods=['post'])
    def save(self, request, pk=None):
        """Save prediction to favorites"""
        return Response({'message': 'Prediction saved'}, status=status.HTTP_200_OK)
