"""
Stripe payment strategy.

Handles one-time payments (Checkout Sessions) and subscriptions.
"""

import logging
from decimal import Decimal
from typing import Optional

import stripe
from django.conf import settings

from .base import PaymentStrategy, PaymentRequest, PaymentResult

logger = logging.getLogger(__name__)


class StripeStrategy(PaymentStrategy):
    """
    Concrete Stripe strategy.

    Supports:
    - One-time payments via Checkout Session
    - Subscription payments via Stripe Billing
    - Webhook signature verification
    """

    def __init__(self):
        config = getattr(settings, 'PAYMENT_CONFIG', {}).get('STRIPE', {})
        stripe.api_key = config.get('SECRET_KEY', '')
        self.public_key = config.get('PUBLIC_KEY', '')
        self.webhook_secret = config.get('WEBHOOK_SECRET', '')

    @property
    def provider_name(self) -> str:
        return 'stripe'

    def initiate_payment(self, request: PaymentRequest) -> PaymentResult:
        """
        Create a Stripe Checkout Session.
        Returns a redirect URL for the user.
        """
        try:
            # Convert amount to cents (Stripe uses smallest currency unit)
            amount_cents = int(request.amount * 100)

            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': request.currency.lower(),
                        'product_data': {
                            'name': request.description or 'FuturaPredict Credits',
                        },
                        'unit_amount': amount_cents,
                    },
                    'quantity': 1,
                }],
                mode='payment',
                success_url=request.return_url or f'{settings.SITE_URL}/account/payment/success/',
                cancel_url=request.cancel_url or f'{settings.SITE_URL}/account/payment/cancel/',
                customer_email=request.email,
                metadata={
                    'user_id': str(request.user_id),
                    **request.metadata,
                },
            )

            return PaymentResult(
                success=True,
                provider_transaction_id=session.id,
                status='pending',
                message='Redirect to Stripe Checkout',
                redirect_url=session.url,
                raw_response={'session_id': session.id},
            )
        except stripe.error.StripeError as e:
            logger.error(f"Stripe initiate error: {e}")
            return PaymentResult(
                success=False,
                status='failed',
                message=str(e.user_message or e),
            )
        except Exception as e:
            logger.error(f"Stripe error: {e}")
            return PaymentResult(success=False, status='failed', message=str(e))

    def verify_payment(self, provider_transaction_id: str) -> PaymentResult:
        """Retrieve Stripe Checkout Session status."""
        try:
            session = stripe.checkout.Session.retrieve(provider_transaction_id)

            if session.payment_status == 'paid':
                return PaymentResult(
                    success=True,
                    provider_transaction_id=session.id,
                    status='completed',
                    message='Payment verified',
                    raw_response=dict(session),
                )
            else:
                return PaymentResult(
                    success=False,
                    provider_transaction_id=session.id,
                    status='pending',
                    message=f'Payment status: {session.payment_status}',
                    raw_response=dict(session),
                )
        except Exception as e:
            logger.error(f"Stripe verify error: {e}")
            return PaymentResult(success=False, status='failed', message=str(e))

    def handle_webhook(self, payload: dict, headers: dict) -> PaymentResult:
        """
        Process Stripe webhook event.
        Verifies signature and extracts payment info.
        """
        try:
            sig_header = headers.get('Stripe-Signature', '')
            raw_body = payload.get('_raw_body', b'')

            event = stripe.Webhook.construct_event(
                raw_body, sig_header, self.webhook_secret,
            )

            event_type = event['type']
            data_object = event['data']['object']

            if event_type == 'checkout.session.completed':
                return PaymentResult(
                    success=True,
                    provider_transaction_id=data_object['id'],
                    status='completed',
                    message='Checkout session completed',
                    raw_response=dict(data_object),
                )
            elif event_type == 'payment_intent.payment_failed':
                return PaymentResult(
                    success=False,
                    provider_transaction_id=data_object.get('id', ''),
                    status='failed',
                    message='Payment failed',
                    raw_response=dict(data_object),
                )
            else:
                return PaymentResult(
                    success=True,
                    status='pending',
                    message=f'Unhandled event type: {event_type}',
                    raw_response=dict(data_object),
                )
        except stripe.error.SignatureVerificationError:
            logger.warning("Stripe webhook signature verification failed")
            return PaymentResult(success=False, status='failed', message='Invalid signature')
        except Exception as e:
            logger.error(f"Stripe webhook error: {e}")
            return PaymentResult(success=False, status='failed', message=str(e))

    def refund(self, provider_transaction_id: str, amount: Optional[Decimal] = None) -> PaymentResult:
        """Process a Stripe refund (full or partial)."""
        try:
            # Retrieve the payment intent from the checkout session
            session = stripe.checkout.Session.retrieve(provider_transaction_id)
            payment_intent_id = session.payment_intent

            refund_params = {'payment_intent': payment_intent_id}
            if amount is not None:
                refund_params['amount'] = int(amount * 100)

            refund = stripe.Refund.create(**refund_params)

            return PaymentResult(
                success=True,
                provider_transaction_id=refund.id,
                status='completed',
                message='Refund processed',
                raw_response=dict(refund),
            )
        except Exception as e:
            logger.error(f"Stripe refund error: {e}")
            return PaymentResult(success=False, status='failed', message=str(e))

    # ── Subscription helpers ────────────────────

    def create_subscription(self, customer_email: str, price_id: str,
                            user_id: int, return_url: str = '') -> PaymentResult:
        """Create a Stripe Billing subscription via Checkout."""
        try:
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{'price': price_id, 'quantity': 1}],
                mode='subscription',
                success_url=return_url or f'{settings.SITE_URL}/account/subscription/success/',
                cancel_url=f'{settings.SITE_URL}/account/subscription/',
                customer_email=customer_email,
                metadata={'user_id': str(user_id)},
            )

            return PaymentResult(
                success=True,
                provider_transaction_id=session.id,
                status='pending',
                redirect_url=session.url,
                raw_response={'session_id': session.id},
            )
        except Exception as e:
            logger.error(f"Stripe subscription error: {e}")
            return PaymentResult(success=False, status='failed', message=str(e))
