"""
Custom middleware for FuturaPredict.

SubscriptionMiddleware – Attaches subscription/credits info to requests
and auto-detects user geo-location for pricing.
"""

import logging

from django.utils.deprecation import MiddlewareMixin
from django.utils import timezone

logger = logging.getLogger(__name__)


class SubscriptionMiddleware(MiddlewareMixin):
    """
    Middleware that:
    1. Detects user geolocation from IP (on first request or if stale)
    2. Attaches subscription status and credit balance to the request
    3. Caches geo info in session to avoid repeated lookups
    """

    # Skip geo lookup for these paths (static, admin, webhooks)
    SKIP_PATHS = ('/static/', '/media/', '/admin/', '/webhooks/', '/favicon.ico')

    def process_request(self, request):
        # Skip for non-user paths
        if any(request.path.startswith(p) for p in self.SKIP_PATHS):
            return None

        # Attach geo info
        if request.user.is_authenticated:
            self._ensure_geo_detected(request)
            self._attach_subscription_info(request)
        else:
            self._attach_anonymous_info(request)

        return None

    def _ensure_geo_detected(self, request):
        """
        Detect user's geolocation from IP if not cached.
        """
        session_geo = request.session.get('user_geo')

        if session_geo:
            request.user_region = session_geo.get('region', 'global')
            request.user_currency = session_geo.get('currency', 'USD')
            return

        try:
            from apps.users.geolocation import GeoLocationService

            geo = GeoLocationService.update_user_profile_geo(request.user, request)

            request.session['user_geo'] = {
                'region': geo.get('region', 'global'),
                'currency': geo.get('currency', 'USD'),
                'country': geo.get('country_code', ''),
                'detected_at': timezone.now().isoformat(),
            }
            request.user_region = geo.get('region', 'global')
            request.user_currency = geo.get('currency', 'USD')
        except Exception as e:
            logger.debug(f"Geo detection skipped: {e}")
            request.user_region = 'global'
            request.user_currency = 'USD'

    def _attach_subscription_info(self, request):
        """
        Attach subscription/credits info to the request.
        """
        try:
            from apps.payments.services.subscription_service import SubscriptionService
            from apps.users.models import UserProfile

            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            request.prediction_credits = profile.prediction_credits

            active_sub = SubscriptionService.get_active_subscription(request.user)
            request.has_subscription = active_sub is not None
            request.subscription = active_sub
        except Exception as e:
            logger.debug(f"Subscription info skipped: {e}")
            request.prediction_credits = 0
            request.has_subscription = False
            request.subscription = None

    def _attach_anonymous_info(self, request):
        """Detect geo and set defaults for anonymous users."""
        session_geo = request.session.get('user_geo')

        if session_geo:
            request.user_region = session_geo.get('region', 'global')
            request.user_currency = session_geo.get('currency', 'USD')
        else:
            # Perform geo detection for anonymous users too
            try:
                from apps.users.geolocation import GeoLocationService

                ip = GeoLocationService.get_client_ip(request)
                geo = GeoLocationService.detect_from_ip(ip)

                request.session['user_geo'] = {
                    'region': geo.get('region', 'global'),
                    'currency': geo.get('currency', 'USD'),
                    'country': geo.get('country_code', ''),
                    'detected_at': timezone.now().isoformat(),
                }
                request.user_region = geo.get('region', 'global')
                request.user_currency = geo.get('currency', 'USD')
            except Exception as e:
                logger.debug(f"Anonymous geo detection skipped: {e}")
                request.user_region = 'global'
                request.user_currency = 'USD'

        request.prediction_credits = 0
        request.has_subscription = False
        request.subscription = None

    def process_response(self, request, response):
        return response
