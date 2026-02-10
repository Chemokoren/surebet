"""
Invoice / Receipt Service.

Generates transaction receipts for completed payments.
"""

import logging
from typing import Optional

from django.utils import timezone

from apps.payments.models import PaymentTransaction

logger = logging.getLogger(__name__)


class InvoiceService:
    """
    Generates receipts and handles invoice-related operations.
    """

    @classmethod
    def generate_receipt(cls, transaction: PaymentTransaction) -> dict:
        """
        Generate a receipt dict for a completed transaction.

        Returns structured data suitable for template rendering or PDF generation.
        """
        return {
            'receipt_number': f"FP-{transaction.reference_id[:8].upper()}",
            'date': transaction.completed_at or transaction.initiated_at,
            'user': {
                'name': transaction.user.get_full_name() or transaction.user.username,
                'email': transaction.user.email,
            },
            'payment': {
                'amount': float(transaction.amount),
                'currency': transaction.currency,
                'provider': transaction.get_provider_display() if hasattr(transaction, 'get_provider_display') else transaction.provider,
                'provider_ref': transaction.provider_transaction_id or '',
                'type': transaction.payment_type,
                'status': transaction.status,
            },
            'item': cls._describe_purchase(transaction),
            'platform': {
                'name': 'FuturaPredict Pro+',
                'url': 'https://futurapredict.com',
                'support_email': 'support@futurapredict.com',
            },
        }

    @classmethod
    def _describe_purchase(cls, transaction: PaymentTransaction) -> dict:
        """Describe what was purchased."""
        metadata = transaction.metadata or {}

        if transaction.payment_type == 'subscription':
            return {
                'description': f"Subscription: {metadata.get('plan_name', 'Pro Plan')}",
                'details': f"Duration: {metadata.get('interval', 'monthly')}",
            }
        elif transaction.payment_type == 'credit_purchase':
            return {
                'description': f"Prediction Credits: {metadata.get('credits', '?')} credits",
                'details': f"Tier: {metadata.get('tier_name', '')}",
            }
        else:
            return {
                'description': 'FuturaPredict Purchase',
                'details': '',
            }

    @classmethod
    def get_user_receipts(cls, user, limit: int = 20) -> list[dict]:
        """Get all receipts for a user."""
        transactions = PaymentTransaction.objects.filter(
            user=user,
            status='completed',
        ).order_by('-completed_at')[:limit]

        return [cls.generate_receipt(t) for t in transactions]

    @classmethod
    def get_receipt_by_reference(cls, reference_id: str) -> Optional[dict]:
        """Look up a receipt by transaction reference."""
        try:
            txn = PaymentTransaction.objects.get(reference_id=reference_id)
            return cls.generate_receipt(txn)
        except PaymentTransaction.DoesNotExist:
            return None
