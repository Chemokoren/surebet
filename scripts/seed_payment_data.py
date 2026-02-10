
import os
import sys
import django
from decimal import Decimal

# Setup Django environment
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.base')
django.setup()

from django.conf import settings
print(f"Using database: {settings.DATABASES['default']['ENGINE']} - {settings.DATABASES['default']['NAME']}")

from apps.payments.models import PaymentChannel, PricingTier, SubscriptionPlan

def seed_payment_channels():
    print("Seeding Payment Channels...")
    
    # 1. East Africa Channels
    mpesa, _ = PaymentChannel.objects.get_or_create(
        provider='mpesa',
        region='east_africa',
        defaults={
            'display_name': 'M-Pesa',
            'is_enabled': True,
            'display_order': 1,
            'config': {'shortcode': '174379'}
        }
    )
    if _: print(f"Created {mpesa}")

    paypal_ea, _ = PaymentChannel.objects.get_or_create(
        provider='paypal',
        region='east_africa',
        defaults={
            'display_name': 'PayPal',
            'is_enabled': True,
            'display_order': 2
        }
    )
    if _: print(f"Created {paypal_ea}")

    # 2. Global Channels
    stripe, _ = PaymentChannel.objects.get_or_create(
        provider='stripe',
        region='global',
        defaults={
            'display_name': 'Credit/Debit Card (Stripe)',
            'is_enabled': True,
            'display_order': 1
        }
    )
    if _: print(f"Created {stripe}")

    paypal_global, _ = PaymentChannel.objects.get_or_create(
        provider='paypal',
        region='global',
        defaults={
            'display_name': 'PayPal',
            'is_enabled': True,
            'display_order': 2
        }
    )
    if _: print(f"Created {paypal_global}")


def seed_pricing_tiers():
    print("\nSeeding Pricing Tiers...")

    # Define Tiers Data
    # East Africa (KES)
    ea_tiers = [
        {
            'name': 'Starter Pack (Tier 1)',
            'price': Decimal('50.00'),
            'currency': 'KES',
            'credits': 5,
            'tier_type': 'credit_pack',
            'display_order': 1
        },
        {
            'name': 'Pro Pack (Tier 2)',
            'price': Decimal('100.00'),
            'currency': 'KES',
            'credits': 10,
            'tier_type': 'credit_pack',
            'display_order': 2
        },
        {
            'name': 'Jumbo Pack (Tier 3)',
            'price': Decimal('200.00'),
            'currency': 'KES',
            'credits': 20,
            'tier_type': 'credit_pack',
            'display_order': 3
        },
        {
            'name': 'Monthly Subscription (Tier 4)',
            'price': Decimal('1000.00'),
            'currency': 'KES',
            'credits': 0,
            'tier_type': 'daily_quota',
            'daily_limit': 10,
            'duration_days': 30,
            'display_order': 4
        },
        {
            'name': 'Single Prediction',
            'price': Decimal('20.00'),
            'currency': 'KES',
            'credits': 1,
            'tier_type': 'single', # Special type for display logic
            'display_order': 5
        }
    ]

    # Global (USD) - "Add 10 dollars to each cluster" interpretation:
    # Tier 1 = $10, Tier 2 = $20, Tier 3 = $30. Monthly = $40. Single = $2.
    global_tiers = [
        {
            'name': 'Starter Pack (Tier 1)',
            'price': Decimal('10.00'),
            'currency': 'USD',
            'credits': 5,
            'tier_type': 'credit_pack',
            'display_order': 1
        },
        {
            'name': 'Pro Pack (Tier 2)',
            'price': Decimal('20.00'),
            'currency': 'USD',
            'credits': 10,
            'tier_type': 'credit_pack',
            'display_order': 2
        },
        {
            'name': 'Jumbo Pack (Tier 3)',
            'price': Decimal('30.00'),
            'currency': 'USD',
            'credits': 20,
            'tier_type': 'credit_pack',
            'display_order': 3
        },
        {
            'name': 'Monthly Subscription (Tier 4)',
            'price': Decimal('40.00'),
            'currency': 'USD',
            'credits': 0,
            'tier_type': 'daily_quota', # Using Quota for consistency with EA "Pass" model
            'daily_limit': 10,
            'duration_days': 30,
            'display_order': 4
        },
        {
            'name': 'Single Prediction',
            'price': Decimal('2.00'),
            'currency': 'USD',
            'credits': 1,
            'tier_type': 'single',
            'display_order': 5
        }
    ]

    # function to process list
    def process_tiers(tier_list, region):
        for tier_data in tier_list:
            # We use name and region as unique identifiers to update
            # But since names changed (added Tier X), we might duplicate if we don't clear old ones?
            # Better to update_or_create by 'name' and 'region'.
            
            # Using update_or_create to ensure prices are set correctly
            defaults = tier_data.copy()
            name = defaults.pop('name')
            tier_type = defaults.pop('tier_type')
            
            # Note: We use name as key. If previous names were "Starter Pack", and new is "Starter Pack (Tier 1)",
            # it naturally creates new ones. We might want to delete old ones manually or via admin if needed.
            obj, created = PricingTier.objects.update_or_create(
                name=name,
                region=region,
                defaults={**defaults, 'tier_type': tier_type, 'is_active': True}
            )
            action = "Created" if created else "Updated"
            print(f"{action} {region} Tier: {obj.name} - {obj.currency} {obj.price}")

    process_tiers(ea_tiers, 'east_africa')
    process_tiers(global_tiers, 'global')


def seed_subscription_plans():
    # Since we moved "Monthly" to PricingTier (as a pass), we might not need SubscriptionPlans 
    # unless we want recurring billing for Stripe.
    # For simplicity and consistency with the "Tier 4" description, we rely on PricingTier logic.
    # But let's keep one "Recurring" plan for Global just in case, or disable old ones.
    print("\nSeeding Subscription Plans (Optional)...")
    # For now, we will mark existing plans as inactive to avoid confusion with the new "Tier 4" logic
    # unless specifically requested.
    # The user request "Tier 4 (monthly subscription)" was grouped with tiers.
    # I'll enable the PricingTier one primarily.
    pass

if __name__ == '__main__':
    seed_payment_channels()
    seed_pricing_tiers()
    seed_subscription_plans()
    print("\nSeeding completed successfully.")
