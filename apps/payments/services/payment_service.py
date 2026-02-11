"""
Payment Service – Orchestrates payment flow using the Strategy Pattern.

Responsibilities:
    1. Detect user's region → determine available payment channels
    2. Select the correct PaymentStrategy based on chosen channel
    3. Initiate payment, handle results, and grant credits/subscription
    4. Process webhooks and update transactions

No core logic changes needed to add a new payment provider.
"""

import logging
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.payments.models import (
    PaymentChannel, PaymentTransaction, PricingTier,
    SubscriptionPlan, UserSubscription,
)
from apps.payments.strategies.base import PaymentStrategy, PaymentRequest, PaymentResult
from apps.payments.strategies.mpesa import MpesaStrategy
from apps.payments.strategies.stripe import StripeStrategy
from apps.payments.strategies.paypal import PayPalStrategy
from apps.payments.strategies.whatsapp import WhatsAppStrategy
from apps.users.models import UserProfile

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Strategy Registry
# ──────────────────────────────────────────────

STRATEGY_REGISTRY: dict[str, type[PaymentStrategy]] = {
    'mpesa': MpesaStrategy,
    'stripe': StripeStrategy,
    'paypal': PayPalStrategy,
    'whatsapp': WhatsAppStrategy,
}


def get_strategy(provider: str) -> PaymentStrategy:
    """Instantiate the correct strategy by provider name."""
    cls = STRATEGY_REGISTRY.get(provider)
    if cls is None:
        raise ValueError(f"Unknown payment provider: {provider}")
    return cls()


class PaymentService:
    """
    Facade for all payment operations.
    """

    # ── Channel selection ───────────────────────

    @staticmethod
    def get_available_channels(region: str) -> list[PaymentChannel]:
        """
        Return enabled payment channels for a region.
        Falls back to 'global' channels if no region-specific ones exist.
        """
        channels = list(
            PaymentChannel.objects.filter(region=region, is_enabled=True).order_by('display_order')
        )
        if not channels:
            channels = list(
                PaymentChannel.objects.filter(region='global', is_enabled=True).order_by('display_order')
            )
        return channels

    @staticmethod
    def get_pricing_tiers(region: str):
        """Return active pricing tiers for a region."""
        # Return QuerySet if possible for further filtering
        qs = PricingTier.objects.filter(region=region, is_active=True).order_by('display_order', 'price')
        
        if not qs.exists():
            qs = PricingTier.objects.filter(region='global', is_active=True).order_by('display_order', 'price')
            
        return qs

    # ── Payment initiation ──────────────────────

    @classmethod
    def initiate_credit_purchase(
        cls,
        user,
        pricing_tier_id: str,
        provider: str,
        phone_number: str = '',
        return_url: str = '',
    ) -> tuple[PaymentTransaction, PaymentResult]:
        """
        Start a credit purchase flow.

        1. Look up PricingTier
        2. Create PaymentTransaction (pending)
        3. Call strategy.initiate_payment()
        4. Return transaction + result
        """
        tier = PricingTier.objects.get(pk=pricing_tier_id, is_active=True)

        # Detect Region
        profile = getattr(user, 'userprofile', None)
        region = getattr(profile, 'region', 'global') if profile else 'global'

        # Select Channel (Prioritize Region, then Global)
        qs = PaymentChannel.objects.filter(provider=provider, is_enabled=True)
        channel = qs.filter(region=region).first()
        if not channel:
            channel = qs.filter(region='global').first()
        if not channel:
             # Last resort
             channel = qs.first()
        if not channel:
            raise ValueError(f"No active payment channel found for provider {provider}")
        strategy = get_strategy(provider)

        # Create transaction
        txn = PaymentTransaction.objects.create(
            user=user,
            channel=channel,
            payment_type='credit_purchase' if tier.tier_type != 'daily_quota' else 'subscription',
            amount=tier.price,
            currency=tier.currency,
            status='pending',
            pricing_tier=tier,
            credits_granted=tier.credits,
            provider=provider,
        )

        # Build request
        request = PaymentRequest(
            user_id=user.id,
            amount=tier.price,
            currency=tier.currency,
            phone_number=phone_number,
            email=user.email,
            description=f'{tier.name} – {tier.credits or tier.daily_limit} predictions',
            metadata={'transaction_id': str(txn.id), 'tier_id': str(tier.id)},
            return_url=return_url,
        )

        result = strategy.initiate_payment(request)

        # Update transaction with provider reference
        if result.provider_transaction_id:
            txn.provider_transaction_id = result.provider_transaction_id
            txn.status = 'processing' if result.success else 'failed'
            txn.save(update_fields=['provider_transaction_id', 'status'])

        if not result.success:
            txn.fail(result.raw_response)

        return txn, result

    @classmethod
    def initiate_subscription(
        cls,
        user,
        plan_id: str,
        provider: str,
        phone_number: str = '',
        return_url: str = '',
    ) -> tuple[PaymentTransaction, PaymentResult]:
        """Start a subscription payment flow."""
        plan = SubscriptionPlan.objects.get(pk=plan_id, is_active=True)
        
        # Detect Region
        profile = getattr(user, 'userprofile', None)
        region = getattr(profile, 'region', 'global') if profile else 'global'

        # Select Channel
        qs = PaymentChannel.objects.filter(provider=provider, is_enabled=True)
        channel = qs.filter(region=region).first()
        if not channel:
            channel = qs.filter(region='global').first()
        if not channel:
             channel = qs.first()
        if not channel:
            raise ValueError(f"No active payment channel found for provider {provider}")
        strategy = get_strategy(provider)

        txn = PaymentTransaction.objects.create(
            user=user,
            channel=channel,
            payment_type='subscription',
            amount=plan.price,
            currency=plan.currency,
            status='pending',
            subscription_plan=plan,
            provider=provider,
        )

        request = PaymentRequest(
            user_id=user.id,
            amount=plan.price,
            currency=plan.currency,
            phone_number=phone_number,
            email=user.email,
            description=f'{plan.name} Subscription',
            metadata={'transaction_id': str(txn.id), 'plan_id': str(plan.id)},
            return_url=return_url,
        )

        result = strategy.initiate_payment(request)

        if result.provider_transaction_id:
            txn.provider_transaction_id = result.provider_transaction_id
            txn.status = 'processing' if result.success else 'failed'
            txn.save(update_fields=['provider_transaction_id', 'status'])

        if not result.success:
            txn.fail(result.raw_response)

        return txn, result

    # ── Payment confirmation ────────────────────

    @classmethod
    @transaction.atomic
    def confirm_payment(cls, txn: PaymentTransaction, result: PaymentResult) -> None:
        """
        Called after webhook confirmation or manual verification.
        Grants credits or activates subscription.
        """
        if txn.status == 'completed':
            logger.warning(f"Transaction {txn.id} already completed")
            return

        txn.complete(
            provider_txn_id=result.provider_transaction_id,
            response=result.raw_response,
        )

        # Grant credits
        if txn.pricing_tier:
            tier = txn.pricing_tier
            profile = UserProfile.objects.select_for_update().get(user=txn.user)

            if tier.tier_type == 'daily_quota':
                # Create/extend subscription
                sub, created = UserSubscription.objects.get_or_create(
                    user=txn.user,
                    plan=cls._get_or_create_credit_plan(tier),
                    defaults={'status': 'pending'},
                )
                sub.activate(duration_days=tier.duration_days)
            else:
                # Add credits
                profile.add_credits(tier.credits)

        # Activate subscription plan
        elif txn.subscription_plan:
            plan = txn.subscription_plan
            sub, created = UserSubscription.objects.get_or_create(
                user=txn.user,
                plan=plan,
                defaults={'status': 'pending'},
            )
            duration = {'monthly': 30, 'quarterly': 90, 'yearly': 365}.get(plan.interval, 30)
            sub.activate(duration_days=duration)

        logger.info(f"Payment confirmed: {txn.id} for user {txn.user.username}")

    @staticmethod
    def _get_or_create_credit_plan(tier: PricingTier) -> SubscriptionPlan:
        """Create a subscription plan wrapper for daily-quota credit tiers."""
        plan, _ = SubscriptionPlan.objects.get_or_create(
            slug=f'credit-tier-{tier.id!s:.8}',
            defaults={
                'name': tier.name,
                'interval': 'monthly',
                'price': tier.price,
                'currency': tier.currency,
                'region': tier.region,
                'daily_prediction_limit': tier.daily_limit,
            },
        )
        return plan

    # ── Webhook handler ─────────────────────────

    @classmethod
    def process_webhook(cls, provider: str, payload: dict, headers: dict) -> PaymentResult:
        """
        Process incoming webhook from a payment provider.
        Routes to correct strategy and confirms payment.
        """
        strategy = get_strategy(provider)
        result = strategy.handle_webhook(payload, headers)

        if result.success and result.provider_transaction_id:
            try:
                txn = PaymentTransaction.objects.get(
                    provider_transaction_id=result.provider_transaction_id,
                )
                cls.confirm_payment(txn, result)
            except PaymentTransaction.DoesNotExist:
                logger.warning(
                    f"No transaction found for provider ID: {result.provider_transaction_id}"
                )

        return result
