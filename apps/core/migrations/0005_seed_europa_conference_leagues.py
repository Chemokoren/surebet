# Generated manually – Seeds UEFA Europa League and Conference League
# into the leagues table with corresponding LeagueAccessRules.

from django.db import migrations


def seed_europa_leagues(apps, schema_editor):
    """Create UEL and UECL league records and their access rules."""
    League = apps.get_model('core', 'League')
    LeagueAccessRule = apps.get_model('core', 'LeagueAccessRule')

    leagues_to_seed = [
        {
            'code': 'UEL',
            'defaults': {
                'name': 'UEFA Europa League',
                'country': 'Europe',
                'priority': 0,
                'api_id': 2146,
                'is_active': True,
                'is_seasonal': True,
                'league_type': 'continental',
                'season_months': [9, 10, 11, 12, 1, 2, 3, 4, 5, 6],
            },
        },
        {
            'code': 'UECL',
            'defaults': {
                'name': 'UEFA Europa Conference League',
                'country': 'Europe',
                'priority': 0,
                'api_id': 2154,
                'is_active': True,
                'is_seasonal': True,
                'league_type': 'continental',
                'season_months': [9, 10, 11, 12, 1, 2, 3, 4, 5, 6],
            },
        },
    ]

    for entry in leagues_to_seed:
        league, created = League.objects.get_or_create(
            code=entry['code'],
            defaults=entry['defaults'],
        )
        if created:
            # 0% share means they don't eat into daily quota;
            # accessed via monthly subscription or on-demand credits
            LeagueAccessRule.objects.get_or_create(
                league=league,
                defaults={'subscription_share_pct': 0.0},
            )


def reverse_seed(apps, schema_editor):
    """Remove UEL and UECL records if reverting."""
    League = apps.get_model('core', 'League')
    League.objects.filter(code__in=['UEL', 'UECL']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_seed_champions_league'),
    ]

    operations = [
        migrations.RunPython(seed_europa_leagues, reverse_seed),
    ]
