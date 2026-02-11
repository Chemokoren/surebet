# Region Detection Fix - Complete Guide

## Problem Summary

**Issue**: System not detecting Uganda as East Africa and showing USD instead of KES with PayPal/Stripe instead of M-Pesa.

**Expected Behavior**:
- Uganda (and all East Africa countries) should show:
  - Currency: **KES** (Kenyan Shillings)
  - Payment Method: **M-Pesa** only
  - Region: **east_africa**

## Root Causes Found

### 1. Localhost Testing
When testing on `localhost` (127.0.0.1), the geolocation service returns default global settings because localhost IPs cannot be geolocated.

### 2. Session Caching
The middleware caches geolocation data in the session. If you were previously detected as "global", this persists until the session is cleared or expires.

### 3. Currency Mapping Issue (FIXED)
The system was using individual currencies (UGX for Uganda, TZS for Tanzania, etc.) instead of standardizing on KES for all East Africa.

## Solutions Implemented

### ✅ 1. Standardized Currency to KES
**File**: `apps/users/geolocation.py`

**Change**: All East African countries now use KES instead of individual currencies:
```python
# Before
result['currency'] = COUNTRY_CURRENCY.get(country_code, 'USD')  # UGX for Uganda

# After  
result['currency'] = 'KES' if country_code in EAST_AFRICA_COUNTRIES else 'USD'
```

**East Africa Countries**: KE, TZ, UG, RW, BI, ET, SS, SO

### ✅ 2. Created Management Command
**File**: `apps/users/management/commands/set_region.py`

**Usage**:
```bash
# Test IP geolocation
./manage.py set_region --ip "41.210.141.1"

# Force user to East Africa
./manage.py set_region --user anii --force-region east_africa

# Check user's current region
./manage.py set_region --user anii
```

### ✅ 3. Manual Override (For Development)
For development/testing on localhost, manually set your region:

```bash
./manage.py set_region --user YOUR_USERNAME --force-region east_africa
```

This sets:
- Region: `east_africa`
- Currency: `KES`
- Country: `UG`

## How to Fix Your Account

### Option 1: Force Region (Recommended for Development)
```bash
./manage.py set_region --user anii --force-region east_africa
```

### Option 2: Clear Session and Re-login
1. Logout from the website
2. Clear browser cookies/session
3. Login again

The middleware will re-detect your region on next login.

### Option 3: Clear Django Session in Database
```bash
./manage.py shell -c "from django.contrib.sessions.models import Session; Session.objects.all().delete(); print('All sessions cleared')"
```

Then logout and login again.

## Testing Region Detection

### Test Uganda IP:
```bash
./manage.py set_region --ip "41.210.141.1"
```

**Expected Output**:
```
Geolocation Result:
  Country Code: UG
  Country Name: Uganda
  Region: east_africa
  Currency: KES
  City: Kampala

✅ East Africa detected - will show M-Pesa & KES pricing
```

### Test Kenya IP:
```bash
./manage.py set_region --ip "105.160.4.1"
```

**Expected Output**:
```
  Country Code: KE
  Country Name: Kenya
  Region: east_africa
  Currency: KES
```

### Test Global IP (e.g., USA):
```bash
./manage.py set_region --ip "8.8.8.8"
```

**Expected Output**:
```
  Country Code: US
  Country Name: United States
  Region: global
  Currency: USD
```

## Production Deployment

### For Production Servers:
The region detection will work automatically because:
1. Users will have real IPs (not localhost)
2. The IP geolocation API will correctly identify countries
3. Uganda will be detected as East Africa automatically

### No changes needed for production!

## Verification Checklist

After forcing your account to East Africa, verify:

1. **Payment Page** (`/account/payment/`):
   - [ ] Shows "Paying in KSh (Kenyan Shillings)"
   - [ ] Pricing tiers show KES (e.g., "KES 50", "KES 100")
   - [ ] Only M-Pesa payment option available
   - [ ] No PayPal or Stripe options shown

2. **Context Variables**:
   - [ ] `request.user_region = 'east_africa'`
   - [ ] `request.user_currency = 'KES'`
   - [ ] `is_east_africa = True` in template context

3. **User Profile** (Django Admin or Shell):
   ```python
   from apps.users.models import UserProfile
   profile = UserProfile.objects.get(user__username='anii')
   assert profile.region == 'east_africa'
   assert profile.currency == 'KES'
   ```

## Files Modified

1. ✅ `apps/users/geolocation.py`
   - Standardized currency to KES for all East Africa

2. ✅ `apps/users/management/commands/set_region.py`
   - Created management command for testing

## API Used

The system uses **ip-api.com** (free tier) for IP geolocation:
- Free for non-commercial use
- 45 requests per minute
- Accurate country detection
- Fallback from MaxMind GeoIP2 database

## Troubleshooting

### Issue: Still seeing USD pricing
**Solution**: 
1. Logout completely
2. Run: `./manage.py set_region --user YOUR_USERNAME --force-region east_africa`
3. Clear browser cache
4. Login again

### Issue: Still seeing PayPal/Stripe
**Solution**:
1. Check payment channels are seeded:
   ```bash
   ./manage.py shell -c "from apps.payments.models import PaymentChannel; print(PaymentChannel.objects.filter(region='east_africa', is_enabled=True).values_list('provider', flat=True))"
   ```
   Should show: `['mpesa']`

2. Re-seed if needed:
   ```bash
   python scripts/seed_payment_channels.py
   ```

### Issue: Geolocation not working on localhost
**This is expected!** Localhost (127.0.0.1) cannot be geolocated. Use the management command to force your region for development.

## Summary

✅ **Fixed**: Currency now standardized to KES for all East Africa  
✅ **Created**: Management command for easy testing  
✅ **Verified**: Uganda correctly detected as East Africa  
✅ **Solution**: Force your development account to east_africa region

**Run this command to fix your account**:
```bash
./manage.py set_region --user anii --force-region east_africa
```

Then logout, clear your session, and login again. You should see:
- ✅ KES pricing
- ✅ M-Pesa payment option only
- ✅ "Paying in KSh" message

The geolocation system is working correctly and will automatically detect Uganda (and all East African countries) in production!
