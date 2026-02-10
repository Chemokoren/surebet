"""
Stripe Webhook View.

Receives and verifies Stripe webhook events.
"""

import json
import logging

import stripe
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from apps.payments.services.payment_service import PaymentService
from apps.payments.models import PaymentTransaction

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class StripeWebhookView(View):
    """
    Endpoint for Stripe webhooks.

    URL: /webhooks/stripe/
    Method: POST

    Handles events:
    - checkout.session.completed
    - invoice.payment_succeeded
    - invoice.payment_failed
    - customer.subscription.deleted
    """

    HANDLED_EVENTS = {
        'checkout.session.completed',
        'invoice.payment_succeeded',
        'invoice.payment_failed',
        'customer.subscription.deleted',
        'customer.subscription.updated',
        'payment_intent.succeeded',
        'charge.refunded',
    }

    def post(self, request, *args, **kwargs):
        payload = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')

        # Verify webhook signature
        webhook_secret = settings.PAYMENT_CONFIG.get('STRIPE', {}).get('WEBHOOK_SECRET', '')

        if webhook_secret:
            try:
                event = stripe.Webhook.construct_event(
                    payload, sig_header, webhook_secret,
                )
            except stripe.error.SignatureVerificationError:
                logger.error("Stripe webhook signature verification failed")
                return HttpResponse(status=400)
            except ValueError:
                logger.error("Invalid Stripe webhook payload")
                return HttpResponse(status=400)
        else:
            # Development mode — no signature check
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                return HttpResponse(status=400)

        event_type = event.get('type', '')
        logger.info(f"Stripe webhook received: {event_type}")

        if event_type not in self.HANDLED_EVENTS:
            logger.info(f"Ignoring unhandled event type: {event_type}")
            return JsonResponse({'status': 'ignored'})

        try:
            data = event.get('data', {}).get('object', {})
            self._handle_event(event_type, data)
            return JsonResponse({'status': 'ok'})
        except PaymentTransaction.DoesNotExist:
            logger.warning(f"No transaction found for webhook event: {event_type}")
            return JsonResponse({'status': 'ok'})
        except Exception as e:
            logger.error(f"Stripe webhook processing error: {e}", exc_info=True)
            return HttpResponse(status=500)

    def _handle_event(self, event_type: str, data: dict):
        """Route event to appropriate handler."""
        if event_type == 'checkout.session.completed':
            self._handle_checkout_completed(data)
        elif event_type == 'invoice.payment_succeeded':
            self._handle_invoice_paid(data)
        elif event_type == 'invoice.payment_failed':
            self._handle_invoice_failed(data)
        elif event_type in ('customer.subscription.deleted', 'customer.subscription.updated'):
            self._handle_subscription_change(data)
        elif event_type == 'charge.refunded':
            self._handle_refund(data)

    def _handle_checkout_completed(self, session: dict):
        """Handle successful checkout."""
        PaymentService.process_webhook(
            provider='stripe',
            payload={
                'session_id': session.get('id'),
                'payment_intent': session.get('payment_intent'),
                'customer': session.get('customer'),
                'amount_total': session.get('amount_total', 0) / 100,
                'currency': session.get('currency', 'usd').upper(),
                'status': 'completed',
                'metadata': session.get('metadata', {}),
            },
        )

    def _handle_invoice_paid(self, invoice: dict):
        """Handle subscription renewal payment."""
        PaymentService.process_webhook(
            provider='stripe',
            payload={
                'invoice_id': invoice.get('id'),
                'subscription': invoice.get('subscription'),
                'customer': invoice.get('customer'),
                'amount_paid': invoice.get('amount_paid', 0) / 100,
                'status': 'completed',
                'type': 'subscription_renewal',
            },
        )

    def _handle_invoice_failed(self, invoice: dict):
        """Handle failed subscription payment."""
        PaymentService.process_webhook(
            provider='stripe',
            payload={
                'invoice_id': invoice.get('id'),
                'subscription': invoice.get('subscription'),
                'customer': invoice.get('customer'),
                'status': 'failed',
                'type': 'subscription_renewal',
            },
        )

    def _handle_subscription_change(self, subscription: dict):
        """Handle subscription cancelled or updated."""
        status = subscription.get('status')
        PaymentService.process_webhook(
            provider='stripe',
            payload={
                'subscription_id': subscription.get('id'),
                'customer': subscription.get('customer'),
                'status': 'cancelled' if status == 'canceled' else status,
                'type': 'subscription_update',
            },
        )

    def _handle_refund(self, charge: dict):
        """Handle refund processed."""
        PaymentService.process_webhook(
            provider='stripe',
            payload={
                'charge_id': charge.get('id'),
                'amount_refunded': charge.get('amount_refunded', 0) / 100,
                'status': 'refunded',
            },
        )
