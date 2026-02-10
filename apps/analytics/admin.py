from django.contrib import admin
from .models import AccuracyRecord, RevenueSnapshot

@admin.register(AccuracyRecord)
class AccuracyRecordAdmin(admin.ModelAdmin):
    list_display = ('period_start', 'period_end', 'league', 'period', 'accuracy_rate', 'total_predictions')
    list_filter = ('period', 'league', 'period_start')
    ordering = ('-period_start',)
    readonly_fields = ('calculated_at',)

@admin.register(RevenueSnapshot)
class RevenueSnapshotAdmin(admin.ModelAdmin):
    list_display = ('date', 'region', 'total_revenue', 'currency')
    list_filter = ('date', 'region', 'currency')
    date_hierarchy = 'date'
