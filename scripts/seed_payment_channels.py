"""
Seed payment channels for the payment system.

This script creates/updates payment channels in the database for:
- East Africa: M-Pesa
- Global: PayPal and WhatsApp
"""

import os
import sys
import django

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.base')
django.setup()

from apps.payments.models import PaymentChannel


def seed_payment_channels():
    """Create or update payment channels."""
    
    channels = [
        # East Africa Channels
        {
            'provider': 'mpesa',
            'region': 'east_africa',
            'display_name': 'M-Pesa',
            'is_enabled': True,
            'display_order': 1,
            'config': {
                'description': 'Pay via M-Pesa STK Push',
                'supported_currencies': ['KES']
            }
        },
        
        # Global Channels
        {
            'provider': 'paypal',
            'region': 'global',
            'display_name': 'PayPal',
            'is_enabled': True,
            'display_order': 1,
            'config': {
                'description': 'Pay with your PayPal account',
                'supported_currencies': ['USD', 'EUR', 'GBP']
            }
        },
        {
            'provider': 'stripe',
            'region': 'global',
            'display_name': 'Credit Card (Stripe)',
            'is_enabled': True,
            'display_order': 2,
            'config': {
                'description': 'Pay with Visa, MasterCard, etc.',
                'supported_currencies': ['USD', 'EUR', 'GBP']
            }
        },
        {
            'provider': 'whatsapp',
            'region': 'global',
            'display_name': 'WhatsApp Pay',
            'is_enabled': False,  # Disabled - to be onboarded later
            'display_order': 3,
            'config': {
                'description': 'Pay securely via WhatsApp',
                'supported_currencies': ['USD', 'EUR', 'GBP']
            }
        },
    ]
    
    for channel_data in channels:
        channel, created = PaymentChannel.objects.update_or_create(
            provider=channel_data['provider'],
            region=channel_data['region'],
            defaults={
                'display_name': channel_data['display_name'],
                'is_enabled': channel_data['is_enabled'],
                'display_order': channel_data['display_order'],
                'config': channel_data['config'],
            }
        )
        
        action = 'Created' if created else 'Updated'
        print(f"{action} payment channel: {channel.display_name} ({channel.region})")


if __name__ == '__main__':
    print("Seeding payment channels...")
    seed_payment_channels()
    print("\nPayment channels seeded successfully!")
    
    # Display summary
    print("\n=== Payment Channels Summary ===")
    print("\nEast Africa:")
    ea_channels = PaymentChannel.objects.filter(region='east_africa', is_enabled=True)
    for ch in ea_channels:
        print(f"  - {ch.display_name} ({ch.provider})")
    
    print("\nGlobal (Rest of World):")
    global_channels = PaymentChannel.objects.filter(region='global', is_enabled=True)
    for ch in global_channels:
        print(f"  - {ch.display_name} ({ch.provider})")
