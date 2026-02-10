from django.contrib import admin
from .models import Prediction, PredictionExplanation, ModelVersion

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
