from django.contrib import admin
from .models import League, Season, Team, Match, LeagueAccessRule

@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'country', 'priority', 'is_active')
    list_filter = ('is_active', 'priority')
    search_fields = ('name', 'code')


@admin.register(LeagueAccessRule)
class LeagueAccessRuleAdmin(admin.ModelAdmin):
    list_display = ('league', 'subscription_share_pct', 'updated_at')
    list_editable = ('subscription_share_pct',)
    ordering = ('-subscription_share_pct', 'league__priority')
    help_text = (
        "Set the % of a subscriber's daily quota allocated to each league. "
        "Leagues set to 0 share the remaining quota equally. "
        "If the priority league has no fixtures today, its share is redistributed."
    )

@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ('league', 'year', 'is_current', 'start_date', 'end_date')
    list_filter = ('is_current', 'league')

@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'league', 'elo_rating')
    search_fields = ('name', 'code')
    list_filter = ('league',)

@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ('home_team', 'away_team', 'match_date', 'status', 'is_locked', 'home_score', 'away_score')
    list_filter = ('status', 'league', 'is_locked', 'match_date')
    search_fields = ('home_team__name', 'away_team__name')
    date_hierarchy = 'match_date'
