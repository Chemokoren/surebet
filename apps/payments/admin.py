from django.contrib import admin
from .models import PaymentChannel, PricingTier, PaymentTransaction, UserSubscription, SubscriptionPlan

@admin.register(PaymentChannel)
class PaymentChannelAdmin(admin.ModelAdmin):
    list_display = ('provider', 'region', 'is_enabled', 'display_order')
    list_filter = ('region', 'is_enabled', 'provider')
    ordering = ('region', 'display_order')

@admin.register(PricingTier)
class PricingTierAdmin(admin.ModelAdmin):
    list_display = ('name', 'region', 'price', 'currency', 'tier_type', 'is_active')
    list_filter = ('region', 'tier_type', 'is_active', 'currency')
    search_fields = ('name',)

@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ('user', 'amount', 'currency', 'status', 'initiated_at', 'provider', 'payment_type')
    list_filter = ('status', 'provider', 'payment_type', 'initiated_at')
    search_fields = ('user__username', 'provider_transaction_id')
    date_hierarchy = 'initiated_at'

@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'region', 'price', 'interval', 'is_active')
    list_filter = ('region', 'interval', 'is_active')
    search_fields = ('name', 'slug')

@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'plan', 'status', 'expires_at', 'auto_renew')
    list_filter = ('status', 'auto_renew', 'expires_at')
    search_fields = ('user__username',)
    date_hierarchy = 'expires_at'
