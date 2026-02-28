# Generated manually – Seeds UEFA Champions League into the leagues table
# and creates a corresponding LeagueAccessRule.

from django.db import migrations


def seed_champions_league(apps, schema_editor):
    """Create the UCL league record and its access rule."""
    League = apps.get_model('core', 'League')
    LeagueAccessRule = apps.get_model('core', 'LeagueAccessRule')

    ucl, created = League.objects.get_or_create(
        code='UCL',
        defaults={
            'name': 'UEFA Champions League',
            'country': 'Europe',
            'priority': 0,  # Highest priority when available
            'api_id': 2001,
            'is_active': True,
            'is_seasonal': True,
            'league_type': 'continental',
            'season_months': [9, 10, 11, 12, 1, 2, 3, 4, 5, 6],
        },
    )
    if created:
        # Create access rule: UCL gets 0% (on-demand / included in monthly)
        # This means it doesn't eat into the subscriber's daily quota
        LeagueAccessRule.objects.get_or_create(
            league=ucl,
            defaults={'subscription_share_pct': 0.0},
        )


def reverse_seed(apps, schema_editor):
    """Remove the UCL league record if reverting."""
    League = apps.get_model('core', 'League')
    League.objects.filter(code='UCL').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_add_champions_league_support'),
    ]

    operations = [
        migrations.RunPython(seed_champions_league, reverse_seed),
    ]
