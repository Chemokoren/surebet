import os
import django
from django.utils import timezone
from datetime import timedelta
import sys

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.base')
django.setup()

from apps.core.models import Match, League, Team
from apps.predictions.models import Prediction

def seed():
    today = timezone.localdate()
    print(f"Correcting data for {today}...")

    # 1. Clear existing dummy matches for today
    # This removes the incorrect matches I created earlier
    deleted, _ = Match.objects.filter(match_date__date=today).delete()
    print(f"Deleted {deleted} incorrect matches + predictions.")

    # 2. Ensure Teams (Based on user request)
    league = League.objects.first()
    if not league:
        league = League.objects.create(name="Premier League", code="EPL", country="England", is_active=True)

    # Teams: Chelsea, Leeds United, Tottenham Hotspur, Newcastle United
    teams = {}
    for name in ["Chelsea", "Leeds United", "Tottenham Hotspur", "Newcastle United"]:
        t, _ = Team.objects.get_or_create(name=name, defaults={'league': league})
        teams[name] = t

    # 3. Create Matches
    # Chelsea vs Leeds United
    # Tottenham Hotspur vs Newcastle United
    
    # Use timezone-aware datetime
    tz = timezone.get_current_timezone()
    # Base start time (e.g. 19:45 today)
    start_time = timezone.now().replace(hour=19, minute=45, second=0, microsecond=0)
    
    match_configs = [
        ("Chelsea", "Leeds United", start_time),
        ("Tottenham Hotspur", "Newcastle United", start_time + timedelta(hours=2))
    ]

    created_matches = []
    for home_name, away_name, m_time in match_configs:
        home = teams[home_name]
        away = teams[away_name]
        
        match = Match.objects.create(
            home_team=home,
            away_team=away,
            match_date=m_time,
            league=league,
            status='scheduled'
        )
        created_matches.append(match)
        print(f"Created Match: {match}")

    # 4. Create Predictions
    for match in created_matches:
        Prediction.objects.create(
            match=match,
            home_win_prob=0.6,
            draw_prob=0.2,
            away_win_prob=0.2,
            predicted_outcome='home_win',
            confidence_score=85.0, # User expects ~85%
            tier='premium',
            is_locked=False
        )
        print(f"Created Prediction for {match}")

if __name__ == '__main__':
    seed()
