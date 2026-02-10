from django.utils.deprecation import MiddlewareMixin

class SubscriptionMiddleware(MiddlewareMixin):
    """Minimal stub middleware used by settings while full implementation is pending.

    This middleware simply passes requests through. Replace with real
    subscription validation logic when available.
    """
    def process_request(self, request):
        return None

    def process_response(self, request, response):
        return response
