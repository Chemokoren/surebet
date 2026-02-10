from rest_framework import viewsets, status, views
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import TemplateView, ListView, CreateView, FormView, View, DetailView
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
from apps.users.models import UserProfile, PredictionUsage


from django.db.models import Q
from apps.core.models import Match, League, Team
from apps.predictions.models import Prediction
from apps.analytics.models import AccuracyRecord
from apps.payments.services.subscription_service import SubscriptionService

class HomeView(TemplateView):
    """Home/Dashboard view"""
    template_name = 'pages/home.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.now().date()
        
        # 1. Fetch Top Predictions (Real Data)
        # 1. Prediction Release Logic (12:00 AM - Midnight)
        now = timezone.now()
        release_hour = 0
        is_released = now.hour >= release_hour
        
        qs = Prediction.objects.none()
        if is_released:
            qs = Prediction.objects.filter(
                match__match_date__date=today,
                match__status='scheduled'
            ).select_related('match', 'match__home_team', 'match__away_team', 'match__league').order_by('-confidence_score')[:6]
            
        if not qs.exists():
             context['status_message'] = "Finalizing daily predictions... Check back soon"
             # Fallback: Show yesterday's winners
             yesterday = today - timedelta(days=1)
             context['past_predictions'] = Prediction.objects.filter(
                match__match_date__date=yesterday,
                is_correct=True
             ).select_related('match', 'match__home_team', 'match__away_team').order_by('-confidence_score')[:3]
        
        # 2. Process Access Logic
        processed_preds = []
        anon_limit = 2
        
        # Get unlocked IDs for authenticated user
        unlocked_ids = set()
        if self.request.user.is_authenticated:
            unlocked_ids = set(PredictionUsage.objects.filter(
                user=self.request.user,
                prediction__in=qs
            ).values_list('prediction_id', flat=True))

        for idx, pred in enumerate(qs):
            is_locked = True
            
            if self.request.user.is_authenticated:
                access = SubscriptionService.can_access_prediction(self.request.user, pred)
                
                # Logic: First 2 are free for everyone (to entice). 
                # Others: Locked if not explicitly unlocked via usage AND not free/allowed by sub/credits implicit check
                
                if idx < anon_limit:
                     is_locked = False
                elif pred.id in unlocked_ids or pred.tier == 'free':
                    is_locked = False
                elif access['remaining_credits'] == -1: # Free/Unlimited Sub
                     # Auto-unlock for unlimited subs? Or require click? 
                     # Let's require click to track "read" status in Usage.
                     is_locked = True 
                else:
                    is_locked = True
            else:
                # Anonymous: First 2 are free
                if idx < anon_limit:
                    is_locked = False
                else:
                    is_locked = True
            
            pred.is_access_locked = is_locked
            processed_preds.append(pred)
            
        context['predictions'] = processed_preds
        
        # 3. Pricing Tiers (for Sales Funnel)
        region = getattr(self.request, 'user_region', 'global')
        
        # Credit Packs (Tier 1-3)
        context['packs'] = PricingTier.objects.filter(
            region=region, 
            is_active=True,
            tier_type='credit_pack'
        ).order_by('price')
        
        # Monthly Subscription (Tier 4)
        context['monthly_tier'] = PricingTier.objects.filter(
            region=region,
            is_active=True,
            tier_type='daily_quota'
        ).first()
        
        # Single Tier (for "Unlock" buttons)
        context['single_tier'] = PricingTier.objects.filter(
            region=region,
            tier_type='single',
            is_active=True
        ).first()
        
        # 4. User Stats
        if self.request.user.is_authenticated:
            usage = SubscriptionService.get_daily_usage(self.request.user)
            # Add credit balance
            profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
            # Fix: prediction_credits already includes bonus if granted
            usage['remaining_credits'] = profile.prediction_credits 
            context['usage'] = usage
            context['active_sub'] = SubscriptionService.get_active_subscription(self.request.user)
            
        return context


class UnlockPredictionView(LoginRequiredMixin, View):
    """Handle consuming a credit to unlock a prediction"""
    
    def post(self, request, pk):
        prediction = get_object_or_404(Prediction, pk=pk)
        success = SubscriptionService.consume_prediction(request.user, prediction)
        
        if success:
            messages.success(request, "Prediction unlocked successfully!")
        else:
            messages.error(request, "Insufficient credits to unlock this prediction.")
            
        # Redirect back to home or referring page
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect('home')


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


class PredictionDetailView(LoginRequiredMixin, DetailView):
    """Detail view for a single prediction"""
    model = Prediction
    template_name = 'pages/prediction_detail.html'
    context_object_name = 'prediction'
    login_url = '/account/login/'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        prediction = self.object
        
        # Check access
        access = SubscriptionService.can_access_prediction(self.request.user, prediction)
        
        context['is_locked'] = not access['allowed']
        context['access_reason'] = access.get('reason')
        context['requires_payment'] = access.get('requires_payment')
        
        # If user has access but hasn't consumed credit yet (and needs to), prompt them
        # For now, we'll assume viewing detail implies consumption if not already done
        if access['allowed'] and access.get('requires_payment') and access.get('remaining_credits', 0) > 0:
             # Logic to consume/deduct credit could be here or in a separate action
             # For a simple flow, we might just show the content if allowed.
             pass
             
        # Related predictions (same league)
        context['related_predictions'] = Prediction.objects.filter(
            match__league=prediction.match.league,
            match__match_date__gte=timezone.now()
        ).exclude(id=prediction.id)[:3]
        
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
        
        # Get region from middleware (or fallback to global)
        region = getattr(self.request, 'user_region', 'global')
        
        # Fetch available plans for the region
        context['plans'] = SubscriptionPlan.objects.filter(
            is_active=True, 
            region=region
        ).order_by('price')
        
        # User's current sub
        context['active_sub'] = SubscriptionService.get_active_subscription(self.request.user)
        
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
        # Get Tiers - Exclude 'single' type as requested
        tiers_qs = PaymentService.get_pricing_tiers(region).exclude(tier_type='single')
        
        # If a specific tier_id is requested, prioritize it in the list (or ensure it's selected in template)
        selected_tier_id = self.request.GET.get('tier_id')
        tiers = list(tiers_qs)
        
        if selected_tier_id:
            # Sort to put selected tier first
            tiers.sort(key=lambda t: str(t.id) != selected_tier_id)
            
        context['tiers'] = tiers
        
        # Get Channels
        context['available_channels'] = PaymentService.get_available_channels(region)
        context['payment_channels'] = context['available_channels'] # Alias for template
        
        return context

    def post(self, request, *args, **kwargs):
        tier_id = request.POST.get('tier_id')
        provider = request.POST.get('provider')
        phone = request.POST.get('phone', '')

        if not tier_id or not provider:
            messages.error(request, "Invalid selection.")
            return redirect('payment')

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
                 return redirect('payment')
                 
        except Exception as e:
            messages.error(request, f"Error: {str(e)}")
            return redirect('payment')


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
