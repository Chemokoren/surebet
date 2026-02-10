"""
Template context processor for region, currency, and payment channels.

Injects `user_region`, `user_currency`, and `payment_channels` into
every template context based on auto-detected geolocation.
"""

from apps.payments.models import PaymentChannel


# East Africa region detection
EAST_AFRICA_REGION = 'east_africa'
GLOBAL_REGION = 'global'

# Default payment channels per region (used when no DB channels exist)
DEFAULT_CHANNELS = {
    EAST_AFRICA_REGION: [
        {'provider': 'mpesa', 'display_name': 'M-Pesa', 'icon': 'fas fa-mobile-alt', 'is_default': True},
        {'provider': 'paypal', 'display_name': 'PayPal', 'icon': 'fab fa-paypal', 'is_default': False},
    ],
    GLOBAL_REGION: [
        {'provider': 'paypal', 'display_name': 'PayPal', 'icon': 'fab fa-paypal', 'is_default': True},
        {'provider': 'stripe', 'display_name': 'Stripe (Card)', 'icon': 'fas fa-credit-card', 'is_default': False},
    ],
}

CURRENCY_SYMBOLS = {
    'KES': 'KSh',
    'USD': '$',
    'TZS': 'TSh',
    'UGX': 'USh',
    'EUR': '€',
    'GBP': '£',
}


def region_context(request):
    """
    Inject region, currency and payment channel info into every template.
    """
    region = getattr(request, 'user_region', GLOBAL_REGION)
    currency = getattr(request, 'user_currency', 'USD')

    # Try to get channels from DB first
    channels = list(
        PaymentChannel.objects.filter(
            region=region, is_enabled=True
        ).order_by('display_order')
    )

    if not channels:
        # Fallback to global channels from DB
        channels = list(
            PaymentChannel.objects.filter(
                region=GLOBAL_REGION, is_enabled=True
            ).order_by('display_order')
        )

    # Build channel data for templates
    if channels:
        channel_data = []
        for idx, ch in enumerate(channels):
            icon = 'fas fa-mobile-alt' if ch.provider == 'mpesa' else (
                'fab fa-paypal' if ch.provider == 'paypal' else 'fas fa-credit-card'
            )
            channel_data.append({
                'provider': ch.provider,
                'display_name': ch.display_name,
                'icon': icon,
                'is_default': idx == 0,  # First channel is default
                'id': str(ch.id),
            })
    else:
        # Use hardcoded defaults if no DB channels exist
        channel_data = DEFAULT_CHANNELS.get(region, DEFAULT_CHANNELS[GLOBAL_REGION])

    is_east_africa = region == EAST_AFRICA_REGION
    currency_symbol = CURRENCY_SYMBOLS.get(currency, currency)

    return {
        'user_region': region,
        'user_currency': currency,
        'currency_symbol': currency_symbol,
        'is_east_africa': is_east_africa,
        'payment_channels': channel_data,
        'default_channel': next((c for c in channel_data if c.get('is_default')), channel_data[0] if channel_data else None),
    }
