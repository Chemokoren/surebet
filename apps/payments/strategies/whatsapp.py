"""
WhatsApp payment strategy.

Handles payments via WhatsApp Business API integration.
This is typically used for regions outside East Africa where direct payment
integration via WhatsApp can be implemented.
"""

import logging
import requests
from decimal import Decimal
from typing import Optional

from django.conf import settings

from .base import PaymentStrategy, PaymentRequest, PaymentResult

logger = logging.getLogger(__name__)


class WhatsAppStrategy(PaymentStrategy):
    """
    Concrete WhatsApp payment strategy.
    
    This strategy integrates with WhatsApp Business API to process payments.
    The implementation can vary based on the payment provider integrated
    with WhatsApp (e.g., Meta Pay, or a custom integration).
    
    Configuration from settings.PAYMENT_CONFIG['WHATSAPP']:
        - ACCESS_TOKEN
        - PHONE_NUMBER_ID
        - BUSINESS_ACCOUNT_ID
        - WEBHOOK_VERIFY_TOKEN
        - ENVIRONMENT (sandbox | live)
    """

    SANDBOX_URL = 'https://graph.facebook.com/v18.0'
    LIVE_URL = 'https://graph.facebook.com/v18.0'

    def __init__(self):
        config = getattr(settings, 'PAYMENT_CONFIG', {}).get('WHATSAPP', {})
        self.access_token = config.get('ACCESS_TOKEN', '')
        self.phone_number_id = config.get('PHONE_NUMBER_ID', '')
        self.business_account_id = config.get('BUSINESS_ACCOUNT_ID', '')
        self.webhook_verify_token = config.get('WEBHOOK_VERIFY_TOKEN', '')
        self.environment = config.get('ENVIRONMENT', 'sandbox')

    @property
    def provider_name(self) -> str:
        return 'whatsapp'

    @property
    def base_url(self) -> str:
        # WhatsApp Business API uses the same URL for both environments
        return self.LIVE_URL if self.environment == 'live' else self.SANDBOX_URL

    def initiate_payment(self, request: PaymentRequest) -> PaymentResult:
        """
        Send payment request via WhatsApp message.
        
        This creates a payment link or interactive message that the user
        can click to complete the payment.
        """
        try:
            # Generate a payment reference/ID
            payment_ref = f"WA-{request.user_id}-{int(request.amount * 100)}"
            
            # Create payment link (this would typically integrate with a payment gateway)
            payment_link = self._generate_payment_link(request, payment_ref)
            
            # Send WhatsApp message with payment link
            message_payload = {
                "messaging_product": "whatsapp",
                "to": request.phone_number or request.email,  # Phone number in international format
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {
                        "text": f"Complete your payment of {request.currency} {request.amount}\n\n{request.description}"
                    },
                    "action": {
                        "buttons": [
                            {
                                "type": "reply",
                                "reply": {
                                    "id": payment_ref,
                                    "title": "Pay Now"
                                }
                            }
                        ]
                    }
                }
            }

            response = requests.post(
                f"{self.base_url}/{self.phone_number_id}/messages",
                json=message_payload,
                headers={
                    'Authorization': f'Bearer {self.access_token}',
                    'Content-Type': 'application/json',
                },
                timeout=30,
            )
            data = response.json()

            if response.status_code in (200, 201) and not data.get('error'):
                return PaymentResult(
                    success=True,
                    provider_transaction_id=payment_ref,
                    status='pending',
                    message='Payment request sent via WhatsApp. Please check your messages.',
                    redirect_url=payment_link,  # Can redirect to payment gateway
                    raw_response=data,
                )
            else:
                error_msg = data.get('error', {}).get('message', 'WhatsApp payment initiation failed')
                return PaymentResult(
                    success=False,
                    status='failed',
                    message=error_msg,
                    raw_response=data,
                )
        except Exception as e:
            logger.error(f"WhatsApp payment initiation error: {e}")
            return PaymentResult(
                success=False,
                status='failed',
                message=f'Payment initiation failed: {str(e)}',
            )

    def _generate_payment_link(self, request: PaymentRequest, payment_ref: str) -> str:
        """
        Generate a payment link for the user.
        
        This could integrate with various payment gateways (Stripe, PayPal, etc.)
        or a custom payment processor.
        """
        # For now, return the success URL with payment reference
        # In production, this would be a link to a payment gateway
        base_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
        return f"{base_url}/account/payment/whatsapp/process/?ref={payment_ref}"

    def verify_payment(self, provider_transaction_id: str) -> PaymentResult:
        """
        Verify WhatsApp payment status.
        
        This would typically query the integrated payment gateway
        or check the payment status in your database.
        """
        try:
            # In a real implementation, you would:
            # 1. Query your payment gateway for the transaction status
            # 2. Or check your database for payment confirmation
            
            logger.info(f"Verifying WhatsApp payment: {provider_transaction_id}")
            
            # Placeholder implementation
            return PaymentResult(
                success=True,
                provider_transaction_id=provider_transaction_id,
                status='pending',
                message='Payment verification in progress',
                raw_response={'status': 'pending'},
            )
        except Exception as e:
            logger.error(f"WhatsApp payment verification error: {e}")
            return PaymentResult(
                success=False,
                status='failed',
                message=str(e),
            )

    def handle_webhook(self, payload: dict, headers: dict) -> PaymentResult:
        """
        Process WhatsApp webhook notification.
        
        This handles callbacks from WhatsApp Business API and the integrated
        payment gateway.
        """
        try:
            # Verify webhook signature
            if not self._verify_webhook_signature(payload, headers):
                return PaymentResult(
                    success=False,
                    status='failed',
                    message='Invalid webhook signature',
                )

            # Handle different webhook types
            entry = payload.get('entry', [{}])[0]
            changes = entry.get('changes', [{}])[0]
            value = changes.get('value', {})
            
            # Check for payment status updates
            payment_status = value.get('payment_status', {})
            if payment_status:
                status = payment_status.get('status', '')
                transaction_id = payment_status.get('transaction_id', '')
                
                if status in ('completed', 'success'):
                    return PaymentResult(
                        success=True,
                        provider_transaction_id=transaction_id,
                        status='completed',
                        message='Payment confirmed via WhatsApp',
                        raw_response=payload,
                    )
                elif status in ('failed', 'cancelled'):
                    return PaymentResult(
                        success=False,
                        provider_transaction_id=transaction_id,
                        status='failed',
                        message=f'Payment {status}',
                        raw_response=payload,
                    )
            
            # Handle message callbacks
            messages = value.get('messages', [])
            if messages:
                message = messages[0]
                interactive = message.get('interactive', {})
                button_reply = interactive.get('button_reply', {})
                
                if button_reply:
                    payment_ref = button_reply.get('id', '')
                    # Here you would redirect user to payment gateway or process payment
                    return PaymentResult(
                        success=True,
                        provider_transaction_id=payment_ref,
                        status='processing',
                        message='User clicked payment button',
                        raw_response=payload,
                    )
            
            return PaymentResult(
                success=True,
                status='pending',
                message='Webhook received',
                raw_response=payload,
            )
        except Exception as e:
            logger.error(f"WhatsApp webhook error: {e}")
            return PaymentResult(success=False, status='failed', message=str(e))

    def _verify_webhook_signature(self, payload: dict, headers: dict) -> bool:
        """
        Verify webhook signature from WhatsApp.
        
        This ensures the webhook is actually from WhatsApp and not a malicious source.
        """
        # In production, implement proper signature verification
        # using the App Secret from your WhatsApp Business App
        return True

    def refund(self, provider_transaction_id: str, amount: Optional[Decimal] = None) -> PaymentResult:
        """
        Process refund via WhatsApp payment gateway.
        
        The implementation depends on the payment gateway integrated with WhatsApp.
        """
        logger.warning("WhatsApp refund not yet fully implemented")
        return PaymentResult(
            success=False,
            status='failed',
            message='WhatsApp refunds require manual processing through the integrated payment gateway',
        )
