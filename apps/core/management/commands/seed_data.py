"""
Populate initial data for FuturaPredict Pro+.

1. Create target Leagues
2. Create standard Subscription Plans
3. Create Geo-Pricing Tiers (East Africa vs Global)
4. Create Payment Channels (M-Pesa for East Africa, Stripe/PayPal Global)
5. Create ML Model Version stub
"""

from django.core.management.base import BaseCommand
from django.conf import settings
from apps.core.models import League, Season, Team
from apps.payments.models import SubscriptionPlan, PricingTier, PaymentChannel
from apps.predictions.models import ModelVersion

class Command(BaseCommand):
    help = 'Bootstrap initial system data'

    def handle(self, *args, **options):
        self.create_leagues()
        self.create_plans()
        self.create_pricing_tiers()
        self.create_payment_channels()
        self.create_model_stub()
        self.stdout.write(self.style.SUCCESS("System bootstrapping complete!"))

    def create_leagues(self):
        # Display order: EPL → La Liga → Serie A → Bundesliga → Ligue 1
        leagues_data = [
            {'name': 'Premier League', 'code': 'PL',  'country': 'England', 'priority': 1, 'api_id': 2021},
            {'name': 'La Liga',        'code': 'LL',  'country': 'Spain',   'priority': 2, 'api_id': 2014},
            {'name': 'Serie A',        'code': 'SA',  'country': 'Italy',   'priority': 3, 'api_id': 2019},
            {'name': 'Bundesliga',     'code': 'BL1', 'country': 'Germany', 'priority': 4, 'api_id': 2002},
            {'name': 'Ligue 1',        'code': 'FL1', 'country': 'France',  'priority': 5, 'api_id': 2015},
        ]
        count = 0
        for item in leagues_data:
            obj, created = League.objects.update_or_create(
                code=item['code'],
                defaults={k: v for k, v in item.items() if k != 'code'},
            )
            if created: count += 1
        self.stdout.write(f"Created/updated {count} leagues.")

    def create_plans(self):
        plans = [
             {'name': 'Monthly Pro', 'slug': 'monthly-pro', 'interval': 'monthly', 'price': 9.99, 'currency': 'USD', 'daily_prediction_limit': 100},
             {'name': 'Quarterly Pro', 'slug': 'quarterly-pro', 'interval': 'quarterly', 'price': 24.99, 'currency': 'USD', 'daily_prediction_limit': 100},
             {'name': 'Yearly Pro', 'slug': 'yearly-pro', 'interval': 'yearly', 'price': 89.99, 'currency': 'USD', 'daily_prediction_limit': 100},
        ]
        count = 0
        for p in plans:
            obj, created = SubscriptionPlan.objects.get_or_create(slug=p['slug'], defaults=p)
            if created: count += 1
        self.stdout.write(f"Created {count} subscription plans.")

    def create_pricing_tiers(self):
        # East Africa Pricing (KES)
        ea_tiers = [
            {'name': 'Starter Pack', 'price': 50, 'currency': 'KES', 'credits': 5, 'region': 'east_africa', 'type': 'credit_pack'},
            {'name': 'Value Pack', 'price': 100, 'currency': 'KES', 'credits': 10, 'region': 'east_africa', 'type': 'credit_pack'},
            {'name': 'Weekly Pass', 'price': 200, 'currency': 'KES', 'credits': 20, 'region': 'east_africa', 'type': 'credit_pack'},
            {'name': 'Monthly Pass', 'price': 1000, 'currency': 'KES', 'daily_limit': 10, 'duration': 30, 'region': 'east_africa', 'type': 'daily_quota'},
            {'name': 'Single Prediction', 'price': 20, 'currency': 'KES', 'credits': 1, 'region': 'east_africa', 'type': 'single'}
        ]
        
        # Global Pricing (USD)
        global_tiers = []

        count = 0
        for t in ea_tiers:
            obj, created = PricingTier.objects.update_or_create(
                name=t['name'], region=t['region'],
                defaults={'price': t['price'], 'currency': t['currency'], 'credits': t.get('credits',0), 
                          'daily_limit': t.get('daily_limit',0), 'duration_days': t.get('duration',0), 
                          'tier_type': t['type']}
            )
            if created: count += 1
        
        for t in global_tiers:
             obj, created = PricingTier.objects.update_or_create(
                name=t['name'], region=t['region'],
                defaults={'price': t['price'], 'currency': t['currency'], 'credits': t.get('credits',0), 'tier_type': t['type']}
            )
             if created: count += 1
             
        self.stdout.write(f"Created {count} pricing tiers.")

    def create_payment_channels(self):
        channels = [
            {'provider': 'mpesa', 'region': 'east_africa', 'display_name': 'M-Pesa (Safaricom)', 'enabled': True},
            {'provider': 'stripe', 'region': 'global', 'display_name': 'Credit Card (Stripe)', 'enabled': True},
            {'provider': 'paypal', 'region': 'global', 'display_name': 'PayPal', 'enabled': True},
            # Allow stripe/paypal in EA as secondary options
             {'provider': 'stripe', 'region': 'east_africa', 'display_name': 'Credit Card', 'enabled': True, 'priority': 2},
        ]
        
        count = 0
        for c in channels:
            priority = c.pop('priority', 0)
            obj, created = PaymentChannel.objects.update_or_create(
                provider=c['provider'], region=c['region'],
                defaults={'display_name': c['display_name'], 'is_enabled': c['enabled'], 'display_order': priority}
            )
            if created: count += 1
        self.stdout.write(f"Created {count} payment channels.")

    def create_model_stub(self):
         ModelVersion.objects.get_or_create(
            name='system_default', version='1.0',
            defaults={'is_active': True, 'model_type': 'ensemble'}
        )
