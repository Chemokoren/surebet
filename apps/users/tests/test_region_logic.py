
from django.test import RequestFactory, TestCase, override_settings
from django.contrib.sessions.middleware import SessionMiddleware
from apps.users.middleware import SubscriptionMiddleware
from apps.users.context_processors import region_context
from apps.payments.models import PaymentChannel
from unittest.mock import patch

class RegionLogicTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        
        # Create test channels
        # Note: We need to use PaymentChannel model. 
        # Since we're in a TestCase, DB is available.
        PaymentChannel.objects.create(
            provider='mpesa', region='east_africa', 
            display_name='M-Pesa', is_enabled=True, display_order=1,
            config={'shortcode': '123456'}
        )
        PaymentChannel.objects.create(
            provider='paypal', region='global', 
            display_name='PayPal', is_enabled=True, display_order=1
        )

    def _process_request(self, request):
        from django.contrib.auth.models import AnonymousUser
        request.user = AnonymousUser()
        
        # Add session middleware
        session_middleware = SessionMiddleware(lambda r: None)
        session_middleware.process_request(request)
        request.session.save()
        
        # Process request with our middleware
        middleware = SubscriptionMiddleware(lambda r: None)
        middleware.process_request(request)
        return request

    @patch('apps.users.geolocation.GeoLocationService.detect_from_ip')
    def test_east_africa_detection(self, mock_detect):
        # Mock geo service to return East Africa
        mock_detect.return_value = {
            'region': 'east_africa',
            'currency': 'KES',
            'country_code': 'KE'
        }

        request = self.factory.get('/')
        self._process_request(request)

        # check middleware attached correct info
        self.assertEqual(request.user_region, 'east_africa')
        self.assertEqual(request.user_currency, 'KES')

        # Check context processor
        context = region_context(request)
        self.assertTrue(context['is_east_africa'])
        self.assertEqual(context['currency_symbol'], 'KSh')
        
        # Verify M-Pesa is present and default
        channels = context['payment_channels']
        self.assertTrue(any(c['provider'] == 'mpesa' for c in channels))
        default = context['default_channel']
        self.assertEqual(default['provider'], 'mpesa')

    @patch('apps.users.geolocation.GeoLocationService.detect_from_ip')
    def test_global_detection(self, mock_detect):
        # Mock geo service to return Global (e.g. US)
        mock_detect.return_value = {
            'region': 'global',
            'currency': 'USD',
            'country_code': 'US'
        }

        request = self.factory.get('/')
        self._process_request(request)

        self.assertEqual(request.user_region, 'global')
        self.assertEqual(request.user_currency, 'USD')

        context = region_context(request)
        self.assertFalse(context['is_east_africa'])
        self.assertEqual(context['currency_symbol'], '$')
        
        # Verify PayPal is present (global channel)
        channels = context['payment_channels']
        self.assertTrue(any(c['provider'] == 'paypal' for c in channels))
        # Verify M-Pesa is NOT present
        self.assertFalse(any(c['provider'] == 'mpesa' for c in channels))
