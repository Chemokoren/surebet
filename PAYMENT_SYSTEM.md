# Payment System Implementation - Strategy Design Pattern

## Overview

This document describes the implementation of a region-aware payment system using the **Strategy Design Pattern**. The system supports three payment channels (M-Pesa, PayPal, and WhatsApp) with automatic region detection to show appropriate payment options based on user location.

## Architecture

### Strategy Design Pattern

The payment system implements the Strategy Design Pattern, which allows:
- **Easy addition of new payment providers** without modifying core logic
- **Runtime selection** of payment strategies based on region
- **Consistent interface** across all payment providers
- **Separation of concerns** between payment orchestration and provider-specific logic

### Components

#### 1. Payment Strategies (`apps/payments/strategies/`)

**Base Strategy (`base.py`)**
- Defines the `PaymentStrategy` abstract interface
- All payment providers implement this interface
- Methods: `initiate_payment()`, `verify_payment()`, `handle_webhook()`, `refund()`

**Concrete Strategies**
- `mpesa.py` - M-Pesa STK Push (East Africa)
- `paypal.py` - PayPal Checkout (Global)
- `whatsapp.py` - WhatsApp Pay (Global)
- `stripe.py` - Stripe Checkout (Optional)

#### 2. Payment Service (`apps/payments/services/payment_service.py`)

**Strategy Registry**
```python
STRATEGY_REGISTRY = {
    'mpesa': MpesaStrategy,
    'stripe': StripeStrategy,
    'paypal': PayPalStrategy,
    'whatsapp': WhatsAppStrategy,
}
```

**Key Methods**
- `get_available_channels(region)` - Returns enabled payment channels for a region
- `get_pricing_tiers(region)` - Returns pricing tiers for a region
- `initiate_credit_purchase()` - Initiates a payment using the appropriate strategy
- `confirm_payment()` - Processes payment confirmation and grants credits

#### 3. Models (`apps/payments/models.py`)

- `PaymentChannel` - Defines available payment methods per region
- `PricingTier` - Defines pricing and credits per region
- `PaymentTransaction` - Normalized transaction log across all providers

## Region-Based Payment Logic

### East Africa (Kenya, Uganda, Tanzania)
- **Currency**: KES (Kenyan Shillings)
- **Payment Channels**: M-Pesa only
- **Detection**: Automatic via IP geolocation middleware

### Global (Rest of World)
- **Currency**: USD (US Dollars)
- **Payment Channels**: PayPal and WhatsApp
- **Detection**: Default for non-East African IPs

### Implementation

The `PaymentView` in `apps/api/views.py` implements the region filtering:

```python
def get_context_data(self, **kwargs):
    # Detect region from middleware
    region = getattr(self.request, 'user_region', 'global')
    is_east_africa = region == 'east_africa'
    
    # Filter payment channels
    if is_east_africa:
        payment_channels = [ch for ch in available_channels if ch.provider == 'mpesa']
    else:
        payment_channels = [ch for ch in available_channels if ch.provider in ['paypal', 'whatsapp']]
```

## Adding a New Payment Provider

To add a new payment provider (e.g., Stripe, Flutterwave):

1. **Create Strategy Class** (`apps/payments/strategies/newprovider.py`)
```python
from .base import PaymentStrategy, PaymentRequest, PaymentResult

class NewProviderStrategy(PaymentStrategy):
    @property
    def provider_name(self) -> str:
        return 'newprovider'
    
    def initiate_payment(self, request: PaymentRequest) -> PaymentResult:
        # Implementation
        pass
    
    def verify_payment(self, provider_transaction_id: str) -> PaymentResult:
        # Implementation
        pass
    
    def handle_webhook(self, payload: dict, headers: dict) -> PaymentResult:
        # Implementation
        pass
    
    def refund(self, provider_transaction_id: str, amount=None) -> PaymentResult:
        # Implementation
        pass
```

2. **Register Strategy** in `payment_service.py`:
```python
from apps.payments.strategies.newprovider import NewProviderStrategy

STRATEGY_REGISTRY = {
    'mpesa': MpesaStrategy,
    'paypal': PayPalStrategy,
    'whatsapp': WhatsAppStrategy,
    'newprovider': NewProviderStrategy,  # Add here
}
```

3. **Add Provider Choice** to `PaymentChannel` model:
```python
PROVIDER_CHOICES = [
    ('mpesa', 'M-Pesa STK Push'),
    ('paypal', 'PayPal'),
    ('whatsapp', 'WhatsApp Pay'),
    ('newprovider', 'New Provider'),  # Add here
]
```

4. **Create Payment Channel** in database:
```python
PaymentChannel.objects.create(
    provider='newprovider',
    region='east_africa',  # or 'global'
    display_name='New Provider',
    is_enabled=True,
    display_order=1
)
```

**No changes needed to**:
- Core payment logic
- PaymentView
- Transaction models
- Webhook processing

## Payment Flow

### 1. User Initiates Payment
```
User → PaymentView → PaymentService.initiate_credit_purchase()
                   → Strategy.initiate_payment()
                   → Payment Provider API
```

### 2. Payment Processing
- **M-Pesa**: STK Push sent to phone
- **PayPal**: Redirect to PayPal checkout
- **WhatsApp**: Payment link sent via WhatsApp

### 3. Payment Confirmation
```
Provider Webhook → PaymentService.process_webhook()
                → Strategy.handle_webhook()
                → PaymentService.confirm_payment()
                → Grant Credits/Subscription
```

## Database Schema

### PaymentChannel
```sql
CREATE TABLE payment_channels (
    id UUID PRIMARY KEY,
    provider VARCHAR(20),  -- 'mpesa', 'paypal', 'whatsapp'
    region VARCHAR(20),    -- 'east_africa', 'global'
    display_name VARCHAR(100),
    is_enabled BOOLEAN,
    display_order INTEGER,
    config JSONB,
    UNIQUE(provider, region)
);
```

### PricingTier
```sql
CREATE TABLE pricing_tiers (
    id UUID PRIMARY KEY,
    name VARCHAR(100),
    tier_type VARCHAR(20),  -- 'credit_pack', 'daily_quota', 'single'
    region VARCHAR(20),
    price DECIMAL(10,2),
    currency VARCHAR(5),    -- 'KES', 'USD'
    credits INTEGER,
    is_active BOOLEAN
);
```

## Configuration

Payment providers are configured via `settings.PAYMENT_CONFIG`:

```python
PAYMENT_CONFIG = {
    'MPESA': {
        'CONSUMER_KEY': 'your_key',
        'CONSUMER_SECRET': 'your_secret',
        'SHORTCODE': '174379',
        'PASSKEY': 'your_passkey',
        'ENVIRONMENT': 'sandbox',  # or 'production'
    },
    'PAYPAL': {
        'CLIENT_ID': 'your_client_id',
        'SECRET': 'your_secret',
        'ENVIRONMENT': 'sandbox',  # or 'live'
    },
    'WHATSAPP': {
        'ACCESS_TOKEN': 'your_token',
        'PHONE_NUMBER_ID': 'your_phone_id',
        'BUSINESS_ACCOUNT_ID': 'your_account_id',
        'ENVIRONMENT': 'sandbox',  # or 'live'
    }
}
```

## Seeding Payment Data

Run the seed script to populate payment channels:

```bash
python scripts/seed_payment_channels.py
```

This creates:
- M-Pesa channel for East Africa
- PayPal and WhatsApp channels for Global

## Testing

### Test Region Detection
```python
# Simulate East Africa user
request.user_region = 'east_africa'
# Expected: Only M-Pesa shown, prices in KES

# Simulate Global user
request.user_region = 'global'
# Expected: PayPal and WhatsApp shown, prices in USD
```

### Test Payment Initiation
```python
txn, result = PaymentService.initiate_credit_purchase(
    user=user,
    pricing_tier_id=tier_id,
    provider='mpesa',  # or 'paypal', 'whatsapp'
    phone_number='+254712345678'
)
```

## Benefits of This Architecture

1. **Extensibility**: Add new payment providers without touching existing code
2. **Maintainability**: Each provider's logic is isolated
3. **Testability**: Mock strategies easily for testing
4. **Flexibility**: Runtime selection of payment method
5. **Region Awareness**: Automatic filtering based on user location
6. **Type Safety**: Normalized interfaces across all providers

## URL Access

The payment page is accessible at:
```
http://127.0.0.1:8000/account/payment/?tier_id=<tier_uuid>
```

- East Africa users see: M-Pesa (KES pricing)
- Global users see: PayPal & WhatsApp (USD pricing)

## Future Enhancements

1. **Add more payment providers** (Flutterwave, Paddle, Razorpay)
2. **Subscription management** with recurring payments
3. **Payment method preferences** per user
4. **Multi-currency support** with automatic conversion
5. **Payment analytics** and reporting
6. **Fraud detection** integration
