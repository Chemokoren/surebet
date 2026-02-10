"""
M-Pesa Webhook (Callback) View.

Receives STK Push callback from Safaricom Daraja API.
"""

import json
import logging

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from apps.payments.services.payment_service import PaymentService

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class MpesaCallbackView(View):
    """
    Endpoint for M-Pesa STK Push callback.

    URL: /webhooks/mpesa/callback/
    Method: POST
    """

    def post(self, request, *args, **kwargs):
        try:
            payload = json.loads(request.body)
            logger.info(f"M-Pesa callback received: {json.dumps(payload)[:500]}")

            # Extract the STK callback
            stk_callback = (
                payload
                .get('Body', {})
                .get('stkCallback', {})
            )

            result_code = stk_callback.get('ResultCode')
            checkout_id = stk_callback.get('CheckoutRequestID', '')
            merchant_id = stk_callback.get('MerchantRequestID', '')

            if result_code == 0:
                # Success – extract metadata
                metadata = stk_callback.get('CallbackMetadata', {}).get('Item', [])
                meta_dict = {}
                for item in metadata:
                    meta_dict[item.get('Name', '')] = item.get('Value', '')

                mpesa_receipt = meta_dict.get('MpesaReceiptNumber', '')
                amount = meta_dict.get('Amount', 0)
                phone = meta_dict.get('PhoneNumber', '')

                # Process through payment service
                PaymentService.process_webhook(
                    provider='mpesa',
                    headers={},
                    payload={
                        'checkout_request_id': checkout_id,
                        'merchant_request_id': merchant_id,
                        'receipt_number': mpesa_receipt,
                        'amount': amount,
                        'phone': phone,
                        'status': 'completed',
                    },
                )
                logger.info(f"M-Pesa payment confirmed: {mpesa_receipt}")

            else:
                # Failed or cancelled
                description = stk_callback.get('ResultDesc', 'Payment failed')
                PaymentService.process_webhook(
                    provider='mpesa',
                    headers={},
                    payload={
                        'checkout_request_id': checkout_id,
                        'status': 'failed',
                        'error': description,
                    },
                )
                logger.warning(f"M-Pesa payment failed: {description}")

            # Always respond 200 to Safaricom
            return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})

        except Exception as e:
            logger.error(f"M-Pesa callback error: {e}", exc_info=True)
            return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})
