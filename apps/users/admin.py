from django.contrib import admin
from .models import UserProfile, LoginBonus, PredictionUsage

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'region', 'currency', 'prediction_credits')
    list_filter = ('region', 'currency')
    search_fields = ('user__username', 'user__email', 'user__profile__detected_ip')

@admin.register(LoginBonus)
class LoginBonusAdmin(admin.ModelAdmin):
    list_display = ('user', 'granted_at', 'credits_granted', 'bonus_type')
    list_filter = ('granted_at', 'bonus_type')
    date_hierarchy = 'granted_at'

@admin.register(PredictionUsage)
class PredictionUsageAdmin(admin.ModelAdmin):
    list_display = ('user', 'prediction', 'date', 'credits_used', 'is_free')
    list_filter = ('date', 'is_free')
    date_hierarchy = 'date'
