"""
Payment Strategy – Abstract interface.

All concrete payment strategies implement this interface.
New payment methods can be added WITHOUT modifying core payment logic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class PaymentRequest:
    """Normalised payment request passed to any strategy."""
    user_id: int
    amount: Decimal
    currency: str
    phone_number: str = ''        # M-Pesa specific
    email: str = ''
    description: str = ''
    metadata: dict = None
    return_url: str = ''
    cancel_url: str = ''

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class PaymentResult:
    """Normalised result returned by any strategy."""
    success: bool
    transaction_id: str = ''
    provider_transaction_id: str = ''
    status: str = 'pending'       # pending | completed | failed
    message: str = ''
    redirect_url: str = ''        # For strategies requiring redirect (Stripe/PayPal)
    raw_response: dict = None

    def __post_init__(self):
        if self.raw_response is None:
            self.raw_response = {}


class PaymentStrategy(ABC):
    """
    Strategy interface for payment processing.

    To add a new payment method:
    1. Create a new class in apps/payments/strategies/<provider>.py
    2. Implement all abstract methods below
    3. Register in PaymentChannel admin (no code changes to core logic)
    """

    @abstractmethod
    def initiate_payment(self, request: PaymentRequest) -> PaymentResult:
        """
        Start a payment flow.
        For push-based (M-Pesa STK): triggers a prompt to the user's phone.
        For redirect-based (Stripe/PayPal): returns a checkout URL.
        """
        ...

    @abstractmethod
    def verify_payment(self, provider_transaction_id: str) -> PaymentResult:
        """Query provider to verify a transaction's status."""
        ...

    @abstractmethod
    def handle_webhook(self, payload: dict, headers: dict) -> PaymentResult:
        """Process incoming webhook from the payment provider."""
        ...

    @abstractmethod
    def refund(self, provider_transaction_id: str, amount: Optional[Decimal] = None) -> PaymentResult:
        """Initiate a refund (full or partial)."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name string, e.g. 'mpesa', 'stripe'."""
        ...
