"""
M-Pesa STK Push payment strategy.

Implements Safaricom Daraja API for East Africa payments.
Flow: STK Push → User enters PIN → Callback → Credit/Subscription activation.
"""

import base64
import logging
import requests
from datetime import datetime
from decimal import Decimal
from typing import Optional

from django.conf import settings

from .base import PaymentStrategy, PaymentRequest, PaymentResult

logger = logging.getLogger(__name__)


class MpesaStrategy(PaymentStrategy):
    """
    Concrete M-Pesa STK Push strategy.

    Configuration loaded from settings.PAYMENT_CONFIG['MPESA']:
        - CONSUMER_KEY
        - CONSUMER_SECRET
        - SHORTCODE
        - PASSKEY
    """

    BASE_URL_SANDBOX = 'https://sandbox.safaricom.co.ke'
    BASE_URL_PRODUCTION = 'https://api.safaricom.co.ke'

    def __init__(self):
        config = getattr(settings, 'PAYMENT_CONFIG', {}).get('MPESA', {})
        self.consumer_key = config.get('CONSUMER_KEY', '')
        self.consumer_secret = config.get('CONSUMER_SECRET', '')
        self.shortcode = config.get('SHORTCODE', '')
        self.passkey = config.get('PASSKEY', '')
        self.callback_url = config.get('CALLBACK_URL', '')
        self.environment = config.get('ENVIRONMENT', 'sandbox')

    @property
    def provider_name(self) -> str:
        return 'mpesa'

    @property
    def base_url(self) -> str:
        if self.environment == 'production':
            return self.BASE_URL_PRODUCTION
        return self.BASE_URL_SANDBOX

    # ── OAuth token ─────────────────────────────

    def _get_access_token(self) -> str:
        """Obtain OAuth access token from Daraja API."""
        url = f"{self.base_url}/oauth/v1/generate?grant_type=client_credentials"
        credentials = base64.b64encode(
            f"{self.consumer_key}:{self.consumer_secret}".encode()
        ).decode()

        try:
            response = requests.get(
                url,
                headers={'Authorization': f'Basic {credentials}'},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()['access_token']
        except Exception as e:
            logger.error(f"M-Pesa OAuth failed: {e}")
            raise

    def _generate_password(self) -> tuple[str, str]:
        """Generate Lipa Na M-Pesa password and timestamp."""
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        password = base64.b64encode(
            f"{self.shortcode}{self.passkey}{timestamp}".encode()
        ).decode()
        return password, timestamp

    # ── Interface methods ───────────────────────

    def initiate_payment(self, request: PaymentRequest) -> PaymentResult:
        """
        Send STK Push to user's phone.
        User receives a prompt to enter M-Pesa PIN.
        """
        if not request.phone_number:
            return PaymentResult(
                success=False,
                status='failed',
                message='Phone number is required for M-Pesa payments',
            )

        try:
            token = self._get_access_token()
            password, timestamp = self._generate_password()

            phone = self._normalize_phone(request.phone_number)
            amount = int(request.amount)  # M-Pesa uses integer amounts

            payload = {
                'BusinessShortCode': self.shortcode,
                'Password': password,
                'Timestamp': timestamp,
                'TransactionType': 'CustomerPayBillOnline',
                'Amount': amount,
                'PartyA': phone,
                'PartyB': self.shortcode,
                'PhoneNumber': phone,
                'CallBackURL': self.callback_url,
                'AccountReference': f'FP-{request.user_id}',
                'TransactionDesc': request.description or 'FuturaPredict Credits',
            }

            response = requests.post(
                f"{self.base_url}/mpesa/stkpush/v1/processrequest",
                json=payload,
                headers={
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/json',
                },
                timeout=30,
            )
            data = response.json()

            if data.get('ResponseCode') == '0':
                return PaymentResult(
                    success=True,
                    provider_transaction_id=data.get('CheckoutRequestID', ''),
                    status='pending',
                    message='STK Push sent to your phone. Enter your M-Pesa PIN.',
                    raw_response=data,
                )
            else:
                return PaymentResult(
                    success=False,
                    status='failed',
                    message=data.get('ResponseDescription', 'STK Push failed'),
                    raw_response=data,
                )
        except Exception as e:
            logger.error(f"M-Pesa STK Push error: {e}")
            return PaymentResult(
                success=False,
                status='failed',
                message=f'Payment initiation failed: {str(e)}',
            )

    def verify_payment(self, provider_transaction_id: str) -> PaymentResult:
        """Query M-Pesa for STK Push transaction status."""
        try:
            token = self._get_access_token()
            password, timestamp = self._generate_password()

            payload = {
                'BusinessShortCode': self.shortcode,
                'Password': password,
                'Timestamp': timestamp,
                'CheckoutRequestID': provider_transaction_id,
            }

            response = requests.post(
                f"{self.base_url}/mpesa/stkpushquery/v1/query",
                json=payload,
                headers={
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/json',
                },
                timeout=30,
            )
            data = response.json()

            result_code = data.get('ResultCode')
            if result_code == '0' or result_code == 0:
                return PaymentResult(
                    success=True,
                    provider_transaction_id=provider_transaction_id,
                    status='completed',
                    message='Payment confirmed',
                    raw_response=data,
                )
            else:
                return PaymentResult(
                    success=False,
                    provider_transaction_id=provider_transaction_id,
                    status='failed',
                    message=data.get('ResultDesc', 'Payment verification failed'),
                    raw_response=data,
                )
        except Exception as e:
            logger.error(f"M-Pesa verify error: {e}")
            return PaymentResult(
                success=False,
                status='failed',
                message=str(e),
            )

    def handle_webhook(self, payload: dict, headers: dict) -> PaymentResult:
        """
        Process M-Pesa callback.
        Called by webhook endpoint when Safaricom posts result.
        """
        try:
            body = payload.get('Body', {}).get('stkCallback', {})
            result_code = body.get('ResultCode')
            checkout_id = body.get('CheckoutRequestID', '')

            if result_code == 0:
                # Extract M-Pesa receipt number from metadata
                items = body.get('CallbackMetadata', {}).get('Item', [])
                receipt = ''
                for item in items:
                    if item.get('Name') == 'MpesaReceiptNumber':
                        receipt = item.get('Value', '')

                return PaymentResult(
                    success=True,
                    provider_transaction_id=checkout_id,
                    transaction_id=receipt,
                    status='completed',
                    message='Payment received',
                    raw_response=payload,
                )
            else:
                return PaymentResult(
                    success=False,
                    provider_transaction_id=checkout_id,
                    status='failed',
                    message=body.get('ResultDesc', 'Payment failed'),
                    raw_response=payload,
                )
        except Exception as e:
            logger.error(f"M-Pesa webhook error: {e}")
            return PaymentResult(success=False, status='failed', message=str(e))

    def refund(self, provider_transaction_id: str, amount: Optional[Decimal] = None) -> PaymentResult:
        """M-Pesa reversal. Note: requires additional Daraja API setup."""
        logger.warning("M-Pesa refund not yet implemented – requires B2C API")
        return PaymentResult(
            success=False,
            status='failed',
            message='M-Pesa refunds require manual processing',
        )

    # ── Utilities ───────────────────────────────

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """Normalise phone to 254XXXXXXXXX format."""
        phone = phone.strip().replace(' ', '').replace('+', '')
        if phone.startswith('0'):
            phone = '254' + phone[1:]
        elif phone.startswith('7') or phone.startswith('1'):
            phone = '254' + phone
        return phone
