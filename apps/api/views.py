from rest_framework import viewsets, status, views
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.utils import timezone
from django.shortcuts import render, redirect
from django.views.generic import TemplateView, ListView, CreateView, FormView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login
from django.contrib.auth.views import LogoutView
from django.urls import reverse_lazy
from django.http import HttpResponseRedirect
from django.contrib.auth.forms import AuthenticationForm
from apps.users.forms import EmailAuthenticationForm
from datetime import datetime, timedelta


class HomeView(LoginRequiredMixin, TemplateView):
    """Home/Dashboard view"""
    template_name = 'pages/home.html'
    login_url = '/account/login/'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['user_notification_count'] = 0
        return context


class PredictionsView(LoginRequiredMixin, TemplateView):
    """Predictions page view"""
    template_name = 'pages/predictions.html'
    login_url = '/account/login/'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['user_notification_count'] = 0
        return context


class TeamAnalysisView(LoginRequiredMixin, TemplateView):
    """Team analysis page view"""
    template_name = 'pages/team_analysis.html'
    login_url = '/account/login/'


class SubscriptionView(LoginRequiredMixin, TemplateView):
    """Subscription management view"""
    template_name = 'account/subscription.html'
    login_url = '/account/login/'


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


class RegisterView(views.APIView):
    """User registration API endpoint"""
    permission_classes = [AllowAny]
    
    def post(self, request):
        """Handle registration via form POST or JSON API request"""
        username = request.data.get('username')
        email = request.data.get('email')
        password = request.data.get('password')
        
        if not all([username, email, password]):
            return Response(
                {'error': 'username, email, and password are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if user already exists
        if User.objects.filter(username=username).exists():
            return Response(
                {'error': 'Username already exists'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if User.objects.filter(email=email).exists():
            return Response(
                {'error': 'Email already exists'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create user
        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password
            )
            return Response(
                {'message': 'Registration successful', 'user_id': user.id},
                status=status.HTTP_201_CREATED
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class PaymentView(LoginRequiredMixin, TemplateView):
    """Payment processing view"""
    template_name = 'account/payment.html'
    login_url = '/account/login/'


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
