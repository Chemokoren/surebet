from django.contrib import admin
from django.utils import timezone
from datetime import timedelta

from .models import Prediction, PredictionExplanation, ModelVersion
from .models_intelligence import (
    PredictionSource,
    ExternalPrediction,
    SourceAccuracyRecord,
)


# ── Existing Prediction Admin ─────────────────────────────────────────────────

class ExplanationInline(admin.TabularInline):
    model = PredictionExplanation
    extra = 0

@admin.register(Prediction)
class PredictionAdmin(admin.ModelAdmin):
    list_display = (
        'match', 'predicted_outcome', 'confidence_score', 'tier', 
        'home_win_prob', 'away_win_prob', 'is_correct'
    )
    list_filter = ('tier', 'predicted_outcome', 'is_correct', 'model_version', 'match__league')
    search_fields = ('match__home_team__name', 'match__away_team__name')
    date_hierarchy = 'match__match_date'
    inlines = [ExplanationInline]

@admin.register(ModelVersion)
class ModelVersionAdmin(admin.ModelAdmin):
    list_display = ('name', 'version', 'model_type', 'is_active', 'created_at')
    list_filter = ('is_active', 'model_type')


# ══════════════════════════════════════════════════════════════════════════════
# INTELLIGENCE ENGINE ADMIN
# ══════════════════════════════════════════════════════════════════════════════


class AccuracyRecordInline(admin.TabularInline):
    model = SourceAccuracyRecord
    extra = 0
    readonly_fields = (
        'date', 'total_predictions', 'correct_predictions', 'accuracy',
        'accuracy_7d', 'accuracy_30d',
    )
    ordering = ['-date']
    max_num = 30  # Show last 30 days


@admin.register(PredictionSource)
class PredictionSourceAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'source_type', 'status', 'priority',
        'overall_accuracy_pct', 'recent_accuracy_pct',
        'total_predictions', 'blend_weight',
        'scrape_enabled', 'last_scraped_at',
    )
    list_filter = ('status', 'source_type', 'scrape_enabled')
    list_editable = ('priority', 'blend_weight', 'scrape_enabled', 'status')
    search_fields = ('name', 'slug', 'website_url')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = (
        'total_predictions', 'correct_predictions',
        'overall_accuracy', 'recent_accuracy',
        'last_scraped_at', 'created_at', 'updated_at',
    )
    inlines = [AccuracyRecordInline]

    fieldsets = (
        ('Identity', {
            'fields': ('name', 'slug', 'source_type', 'website_url', 'description'),
        }),
        ('Scraping Configuration', {
            'fields': ('scrape_url', 'scraper_class', 'scrape_config',
                       'scrape_enabled', 'scrape_interval_hours'),
            'classes': ('collapse',),
        }),
        ('Status & Evaluation', {
            'fields': ('status', 'priority', 'blend_weight',
                       'learning_start_date', 'learning_end_date',
                       'min_accuracy_to_activate'),
        }),
        ('Performance (Auto-Updated)', {
            'fields': ('total_predictions', 'correct_predictions',
                       'overall_accuracy', 'recent_accuracy',
                       'last_scraped_at'),
            'classes': ('collapse',),
        }),
    )

    actions = ['promote_to_active', 'pause_sources', 'start_learning_phase']

    def overall_accuracy_pct(self, obj):
        return f"{obj.overall_accuracy:.1%}"
    overall_accuracy_pct.short_description = 'Overall Acc.'
    overall_accuracy_pct.admin_order_field = 'overall_accuracy'

    def recent_accuracy_pct(self, obj):
        return f"{obj.recent_accuracy:.1%}"
    recent_accuracy_pct.short_description = 'Recent Acc. (30d)'
    recent_accuracy_pct.admin_order_field = 'recent_accuracy'

    @admin.action(description="Promote selected sources to Active")
    def promote_to_active(self, request, queryset):
        updated = queryset.update(status='active')
        self.message_user(request, f"Promoted {updated} source(s) to Active.")

    @admin.action(description="Pause selected sources")
    def pause_sources(self, request, queryset):
        updated = queryset.update(status='paused')
        self.message_user(request, f"Paused {updated} source(s).")

    @admin.action(description="Start 90-day learning phase")
    def start_learning_phase(self, request, queryset):
        today = timezone.now().date()
        for source in queryset:
            source.status = 'learning'
            source.learning_start_date = today
            source.learning_end_date = today + timedelta(days=90)
            source.save()
        self.message_user(request, f"Started learning phase for {queryset.count()} source(s).")


@admin.register(ExternalPrediction)
class ExternalPredictionAdmin(admin.ModelAdmin):
    list_display = (
        'source', 'match', 'predicted_outcome',
        'home_win_prob', 'draw_prob', 'away_win_prob',
        'is_correct', 'scraped_at',
    )
    list_filter = ('source', 'predicted_outcome', 'is_correct')
    search_fields = (
        'match__home_team__name', 'match__away_team__name',
        'source__name',
    )
    date_hierarchy = 'scraped_at'
    readonly_fields = ('is_correct', 'resolved_at', 'scraped_at', 'raw_data')


@admin.register(SourceAccuracyRecord)
class SourceAccuracyRecordAdmin(admin.ModelAdmin):
    list_display = (
        'source', 'date', 'total_predictions', 'correct_predictions',
        'accuracy_pct', 'accuracy_7d_pct', 'accuracy_30d_pct',
    )
    list_filter = ('source',)
    date_hierarchy = 'date'

    def accuracy_pct(self, obj):
        return f"{obj.accuracy:.1%}"
    accuracy_pct.short_description = 'Accuracy'

    def accuracy_7d_pct(self, obj):
        return f"{obj.accuracy_7d:.1%}" if obj.accuracy_7d is not None else '—'
    accuracy_7d_pct.short_description = '7d Avg'

    def accuracy_30d_pct(self, obj):
        return f"{obj.accuracy_30d:.1%}" if obj.accuracy_30d is not None else '—'
    accuracy_30d_pct.short_description = '30d Avg'

