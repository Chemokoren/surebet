"""
PayPal payment strategy.

Handles one-time payments via PayPal Checkout (Orders API v2).
"""

import logging
import requests
from decimal import Decimal
from typing import Optional

from django.conf import settings

from .base import PaymentStrategy, PaymentRequest, PaymentResult

logger = logging.getLogger(__name__)


class PayPalStrategy(PaymentStrategy):
    """
    Concrete PayPal strategy using Orders API v2.

    Configuration from settings.PAYMENT_CONFIG['PAYPAL']:
        - CLIENT_ID
        - SECRET
        - ENVIRONMENT (sandbox | live)
    """

    SANDBOX_URL = 'https://api-m.sandbox.paypal.com'
    LIVE_URL = 'https://api-m.paypal.com'

    def __init__(self):
        config = getattr(settings, 'PAYMENT_CONFIG', {}).get('PAYPAL', {})
        self.client_id = config.get('CLIENT_ID', '')
        self.secret = config.get('SECRET', '')
        self.environment = config.get('ENVIRONMENT', 'sandbox')

    @property
    def provider_name(self) -> str:
        return 'paypal'

    @property
    def base_url(self) -> str:
        return self.LIVE_URL if self.environment == 'live' else self.SANDBOX_URL

    def _get_access_token(self) -> str:
        """Obtain OAuth2 token from PayPal."""
        try:
            response = requests.post(
                f"{self.base_url}/v1/oauth2/token",
                data={'grant_type': 'client_credentials'},
                auth=(self.client_id, self.secret),
                headers={'Accept': 'application/json'},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()['access_token']
        except Exception as e:
            logger.error(f"PayPal OAuth failed: {e}")
            raise

    def initiate_payment(self, request: PaymentRequest) -> PaymentResult:
        """Create a PayPal order and return approval URL."""
        try:
            token = self._get_access_token()

            order_payload = {
                'intent': 'CAPTURE',
                'purchase_units': [{
                    'amount': {
                        'currency_code': request.currency.upper(),
                        'value': str(request.amount),
                    },
                    'description': request.description or 'FuturaPredict Credits',
                    'custom_id': str(request.user_id),
                }],
                'application_context': {
                    'return_url': request.return_url or f'{settings.SITE_URL}/account/payment/success/',
                    'cancel_url': request.cancel_url or f'{settings.SITE_URL}/account/payment/cancel/',
                    'brand_name': 'FuturaPredict Pro+',
                    'user_action': 'PAY_NOW',
                },
            }

            response = requests.post(
                f"{self.base_url}/v2/checkout/orders",
                json=order_payload,
                headers={
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/json',
                },
                timeout=30,
            )
            data = response.json()

            if response.status_code in (200, 201):
                approve_url = ''
                for link in data.get('links', []):
                    if link.get('rel') == 'approve':
                        approve_url = link['href']
                        break

                return PaymentResult(
                    success=True,
                    provider_transaction_id=data['id'],
                    status='pending',
                    message='Redirect to PayPal for approval',
                    redirect_url=approve_url,
                    raw_response=data,
                )
            else:
                return PaymentResult(
                    success=False,
                    status='failed',
                    message=data.get('message', 'PayPal order creation failed'),
                    raw_response=data,
                )
        except Exception as e:
            logger.error(f"PayPal initiate error: {e}")
            return PaymentResult(success=False, status='failed', message=str(e))

    def verify_payment(self, provider_transaction_id: str) -> PaymentResult:
        """Capture a PayPal order after user approval."""
        try:
            token = self._get_access_token()

            response = requests.post(
                f"{self.base_url}/v2/checkout/orders/{provider_transaction_id}/capture",
                headers={
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/json',
                },
                timeout=30,
            )
            data = response.json()

            if data.get('status') == 'COMPLETED':
                return PaymentResult(
                    success=True,
                    provider_transaction_id=provider_transaction_id,
                    status='completed',
                    message='Payment captured',
                    raw_response=data,
                )
            else:
                return PaymentResult(
                    success=False,
                    provider_transaction_id=provider_transaction_id,
                    status='pending',
                    message=f'Order status: {data.get("status")}',
                    raw_response=data,
                )
        except Exception as e:
            logger.error(f"PayPal verify error: {e}")
            return PaymentResult(success=False, status='failed', message=str(e))

    def handle_webhook(self, payload: dict, headers: dict) -> PaymentResult:
        """Process PayPal webhook notification."""
        try:
            event_type = payload.get('event_type', '')
            resource = payload.get('resource', {})

            if event_type == 'CHECKOUT.ORDER.APPROVED':
                order_id = resource.get('id', '')
                return self.verify_payment(order_id)
            elif event_type == 'PAYMENT.CAPTURE.COMPLETED':
                return PaymentResult(
                    success=True,
                    provider_transaction_id=resource.get('id', ''),
                    status='completed',
                    message='Payment captured via webhook',
                    raw_response=payload,
                )
            else:
                return PaymentResult(
                    success=True,
                    status='pending',
                    message=f'Unhandled event: {event_type}',
                    raw_response=payload,
                )
        except Exception as e:
            logger.error(f"PayPal webhook error: {e}")
            return PaymentResult(success=False, status='failed', message=str(e))

    def refund(self, provider_transaction_id: str, amount: Optional[Decimal] = None, currency_code: str = 'USD') -> PaymentResult:
        """Refund a PayPal capture."""
        try:
            token = self._get_access_token()

            refund_payload = {}
            if amount is not None:
                refund_payload = {
                    'amount': {
                        'value': str(amount),
                        'currency_code': currency_code,
                    }
                }

            response = requests.post(
                f"{self.base_url}/v2/payments/captures/{provider_transaction_id}/refund",
                json=refund_payload,
                headers={
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/json',
                },
                timeout=30,
            )
            data = response.json()

            if data.get('status') == 'COMPLETED':
                return PaymentResult(
                    success=True,
                    provider_transaction_id=data.get('id', ''),
                    status='completed',
                    message='Refund processed',
                    raw_response=data,
                )
            else:
                return PaymentResult(
                    success=False,
                    status='failed',
                    message=f'Refund status: {data.get("status")}',
                    raw_response=data,
                )
        except Exception as e:
            logger.error(f"PayPal refund error: {e}")
            return PaymentResult(success=False, status='failed', message=str(e))
