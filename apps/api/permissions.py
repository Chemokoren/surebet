"""
API Permissions.

Custom DRF permission classes for prediction access control.
"""

from rest_framework import permissions


class HasPredictionAccess(permissions.BasePermission):
    """
    Check if user has credits/subscription to access premium predictions.
    Free predictions are always accessible.
    """

    def has_object_permission(self, request, view, obj):
        from apps.payments.services.subscription_service import SubscriptionService

        # Free tier predictions are always accessible
        if hasattr(obj, 'tier') and obj.tier == 'free':
            return True

        # Anonymous users can't access premium
        if not request.user.is_authenticated:
            return False

        # Check subscription/credits
        access = SubscriptionService.can_access_prediction(
            request.user, obj, session=request.session,
        )
        return access.get('allowed', False)


class IsAdminOrStaff(permissions.BasePermission):
    """Restrict to admin/staff users."""

    def has_permission(self, request, view):
        return (
            request.user and
            request.user.is_authenticated and
            (request.user.is_staff or request.user.is_superuser)
        )


class IsOwnerOrAdmin(permissions.BasePermission):
    """Allow owners of an object or admin users."""

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff or request.user.is_superuser:
            return True

        # Check common owner fields
        if hasattr(obj, 'user'):
            return obj.user == request.user
        if hasattr(obj, 'owner'):
            return obj.owner == request.user

        return False


class PredictionRateThrottle(permissions.BasePermission):
    """
    Additional layer: enforce daily prediction consumption limits.
    Works alongside DRF throttling for API-level rate limiting.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return True  # Anonymous handled separately

        from apps.payments.services.subscription_service import SubscriptionService
        usage = SubscriptionService.get_daily_usage(request.user)
        return usage.get('remaining', 0) > 0 or usage.get('unlimited', False)
