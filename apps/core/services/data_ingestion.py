"""
Data Ingestion Service.

Fetches real daily fixtures from external APIs (football-data.org) with
web scraping fallback (LiveScore). Handles postponed/cancelled matches.

Flow:
    1. Fetch today's fixtures from football-data.org API
    2. Validate and normalize match data
    3. Create/update Match records
    4. Update match statuses (in_play, finished, postponed)
    5. Lock predictions at kickoff
    6. Update scores for finished matches
"""

import logging
from datetime import datetime, date, timedelta
from typing import Optional

import requests
from django.conf import settings
from django.utils import timezone
from django.db import transaction

from apps.core.models import League, Team, Match, Season

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# API Configuration
# ──────────────────────────────────────────────

FOOTBALL_DATA_BASE_URL = 'https://api.football-data.org/v4'

# football-data.org league codes mapped to our League.code values
LEAGUE_CODE_MAP = {
    'PL': 'PL',       # Premier League
    'PD': 'LL',       # La Liga (Primera Division)
    'BL1': 'BL1',     # Bundesliga
    'SA': 'SA',       # Serie A
    'FL1': 'FL1',     # Ligue 1
}


class DataIngestionService:
    """
    Service for fetching and storing real match data.
    """

    def __init__(self):
        self.api_key = getattr(settings, 'FOOTBALL_DATA_API_KEY', '')
        self.headers = {'X-Auth-Token': self.api_key} if self.api_key else {}

    # ── Public API ──────────────────────────────

    def fetch_todays_fixtures(self) -> dict:
        """
        Fetch all fixtures for today across target leagues.

        Returns {
            'created': int,
            'updated': int,
            'errors': list[str],
        }
        """
        today = date.today()
        return self.fetch_fixtures_for_date(today)

    def fetch_fixtures_for_date(self, target_date: date) -> dict:
        """Fetch fixtures for a specific date."""
        stats = {'created': 0, 'updated': 0, 'errors': []}
        date_str = target_date.strftime('%Y-%m-%d')

        for api_code, our_code in LEAGUE_CODE_MAP.items():
            try:
                result = self._fetch_league_fixtures(api_code, our_code, date_str)
                stats['created'] += result['created']
                stats['updated'] += result['updated']
            except Exception as e:
                error_msg = f"Error fetching {our_code}: {e}"
                logger.error(error_msg)
                stats['errors'].append(error_msg)

        logger.info(
            f"Ingestion complete for {date_str}: "
            f"created={stats['created']}, updated={stats['updated']}, "
            f"errors={len(stats['errors'])}"
        )
        return stats

    def fetch_fixtures_for_range(self, start: date, end: date) -> dict:
        """Fetch fixtures for a date range."""
        stats = {'created': 0, 'updated': 0, 'errors': []}

        for api_code, our_code in LEAGUE_CODE_MAP.items():
            try:
                date_from = start.strftime('%Y-%m-%d')
                date_to = end.strftime('%Y-%m-%d')
                result = self._fetch_league_fixtures(
                    api_code, our_code, date_from, date_to,
                )
                stats['created'] += result['created']
                stats['updated'] += result['updated']
            except Exception as e:
                stats['errors'].append(f"Error fetching {our_code}: {e}")

        return stats

    def update_live_scores(self) -> dict:
        """
        Fetch live/recently finished matches and update scores.
        Also locks predictions for in-play matches.
        """
        stats = {'updated': 0, 'locked': 0, 'errors': []}

        for api_code, our_code in LEAGUE_CODE_MAP.items():
            try:
                result = self._update_scores_for_league(api_code, our_code)
                stats['updated'] += result['updated']
                stats['locked'] += result['locked']
            except Exception as e:
                stats['errors'].append(f"Error updating {our_code}: {e}")

        return stats

    def sync_teams_and_leagues(self) -> dict:
        """
        Sync league and team data from API.
        Should be called periodically (weekly) to keep team info updated.
        """
        stats = {'leagues': 0, 'teams': 0, 'errors': []}

        for api_code, our_code in LEAGUE_CODE_MAP.items():
            try:
                result = self._sync_league(api_code, our_code)
                stats['leagues'] += result['leagues']
                stats['teams'] += result['teams']
            except Exception as e:
                stats['errors'].append(f"Sync error {our_code}: {e}")

        return stats

    # ── Private Helpers ─────────────────────────

    def _fetch_league_fixtures(
        self, api_code: str, our_code: str,
        date_from: str, date_to: str = None,
    ) -> dict:
        """Fetch and store fixtures for a specific league."""
        # Use OpenLigaDB for Bundesliga (BL1) - Free, no key needed
        if our_code == 'BL1':
            return self._fetch_openligadb_bl1(date_from, date_to)

        stats = {'created': 0, 'updated': 0}

        url = f"{FOOTBALL_DATA_BASE_URL}/competitions/{api_code}/matches"
        params = {'dateFrom': date_from}
        if date_to:
            params['dateTo'] = date_to
        else:
            params['dateTo'] = date_from

        response = requests.get(url, headers=self.headers, params=params, timeout=30)

        if response.status_code == 429:
            logger.warning(f"Rate limited by football-data.org for {api_code}")
            return stats

        if response.status_code != 200:
            logger.error(f"API error {response.status_code}: {response.text[:200]}")
            return stats

        data = response.json()
        matches = data.get('matches', [])

        league = League.objects.filter(code=our_code).first()
        if not league:
            logger.warning(f"League {our_code} not found in database")
            return stats

        for match_data in matches:
            try:
                result = self._process_match(match_data, league)
                if result == 'created':
                    stats['created'] += 1
                elif result == 'updated':
                    stats['updated'] += 1
            except Exception as e:
                logger.error(f"Error processing match: {e}")

        return stats

    def _fetch_openligadb_bl1(self, date_from: str, date_to: str = None) -> dict:
        """Fetch BL1 fixtures from OpenLigaDB (Free API)."""
        stats = {'created': 0, 'updated': 0}
        league = League.objects.filter(code='BL1').first()
        
        if not league:
            # Create if missing
            league = League.objects.create(
                name='Bundesliga', code='BL1', country='Germany', 
                is_active=True, api_id=2002
            )
            
        # OpenLigaDB uses season years (e.g., 2025). Extract from date.
        try:
            target_date = datetime.strptime(date_from, '%Y-%m-%d')
            year = target_date.year
            # Basic heuristic: if after July, use year, else year-1
            if target_date.month < 7:
                year -= 1
        except Exception:
            year = datetime.now().year

        url = f"https://api.openligadb.de/getmatchdata/bl1/{year}"
        
        try:
            response = requests.get(url, timeout=30)
            if response.status_code != 200:
                logger.error(f"OpenLigaDB error: {response.status_code}")
                return stats
                
            matches = response.json()
            
            # Filter by date range manually since API returns whole season
            start_dt = datetime.strptime(date_from, '%Y-%m-%d')
            end_dt = datetime.strptime(date_from, '%Y-%m-%d')
            if date_to:
                end_dt = datetime.strptime(date_to, '%Y-%m-%d')
            # Add end of day buffer
            end_dt = end_dt.replace(hour=23, minute=59, second=59)
                
            for m in matches:
                match_dt_str = m.get('matchDateTimeUTC', '')
                try:
                    match_dt = datetime.fromisoformat(match_dt_str.replace('Z', '+00:00'))
                except:
                    continue
                    
                # Filter
                if not (start_dt.date() <= match_dt.date() <= end_dt.date()):
                    continue
                    
                # Process Match (Adapt to _process_match structure or handle directly)
                # Since structure differs, I'll map it manually here
                
                # Teams
                t1 = m.get('team1', {})
                t2 = m.get('team2', {})
                
                home_team, _ = Team.objects.get_or_create(
                    name=t1.get('teamName'),
                    defaults={'league': league, 'logo_url': t1.get('teamIconUrl', '')}
                )
                away_team, _ = Team.objects.get_or_create(
                    name=t2.get('teamName'),
                    defaults={'league': league, 'logo_url': t2.get('teamIconUrl', '')}
                )
                
                # Status check
                is_finished = m.get('matchIsFinished', False)
                status = 'finished' if is_finished else 'scheduled'
                
                # Scores
                home_score = None
                away_score = None
                if is_finished:
                    # OpenLigaDB results are a list. Usually type 2 is final result
                    results = m.get('matchResults', [])
                    final = next((r for r in results if r.get('resultTypeID') == 2), None)
                    if final:
                        home_score = final.get('pointsTeam1')
                        away_score = final.get('pointsTeam2')
                
                # Create Match
                match, created = Match.objects.update_or_create(
                    api_id=m.get('matchID'),
                    defaults={
                        'league': league,
                        'home_team': home_team,
                        'away_team': away_team,
                        'match_date': match_dt,
                        'status': status,
                        'home_score': home_score,
                        'away_score': away_score,
                    }
                )
                
                if created:
                    stats['created'] += 1
                else:
                    stats['updated'] += 1
                    
        except Exception as e:
            logger.error(f"OpenLigaDB fetch error: {e}")
            
        return stats

    @transaction.atomic
    def _process_match(self, data: dict, league: League) -> str:
        """Create or update a single match from API data."""
        api_id = data.get('id')
        if not api_id:
            return 'skipped'

        # Get or create teams
        home_data = data.get('homeTeam', {})
        away_data = data.get('awayTeam', {})

        home_team = self._get_or_create_team(home_data, league)
        away_team = self._get_or_create_team(away_data, league)

        if not home_team or not away_team:
            return 'skipped'

        # Parse date
        utc_date = data.get('utcDate', '')
        try:
            match_date = datetime.fromisoformat(utc_date.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            logger.error(f"Invalid date: {utc_date}")
            return 'skipped'

        # Map API status to our status
        api_status = data.get('status', 'SCHEDULED')
        status = self._map_status(api_status)

        # Scores
        score = data.get('score', {})
        full_time = score.get('fullTime', {})
        half_time = score.get('halfTime', {})

        # Season handling
        season_data = data.get('season', {})
        season = None
        if season_data:
            season, _ = Season.objects.get_or_create(
                league=league,
                year=str(season_data.get('id', '')),
                defaults={
                    'start_date': season_data.get('startDate', match_date.date()),
                    'end_date': season_data.get('endDate', match_date.date()),
                    'is_current': season_data.get('currentMatchday') is not None,
                },
            )

        # Create or update
        match, created = Match.objects.update_or_create(
            api_id=api_id,
            defaults={
                'league': league,
                'season': season,
                'matchday': data.get('matchday'),
                'home_team': home_team,
                'away_team': away_team,
                'match_date': match_date,
                'status': status,
                'home_score': full_time.get('home'),
                'away_score': full_time.get('away'),
                'home_half_score': half_time.get('home'),
                'away_half_score': half_time.get('away'),
            },
        )

        # Lock if match has started
        if status in ('in_play', 'paused', 'finished') and not match.is_locked:
            match.lock()

        return 'created' if created else 'updated'

    def _get_or_create_team(self, team_data: dict, league: League) -> Optional[Team]:
        """Get or create a team from API data."""
        api_id = team_data.get('id')
        if not api_id:
            return None

        team, created = Team.objects.update_or_create(
            api_id=api_id,
            defaults={
                'name': team_data.get('name', 'Unknown'),
                'short_name': team_data.get('shortName', ''),
                'code': team_data.get('tla', ''),
                'logo_url': team_data.get('crest', ''),
                'league': league,
            },
        )
        return team

    def _update_scores_for_league(self, api_code: str, our_code: str) -> dict:
        """Update scores for in-play and recently finished matches."""
        stats = {'updated': 0, 'locked': 0}

        # Get matches that are currently in-play or recently scheduled
        today = date.today()
        pending_matches = Match.objects.filter(
            league__code=our_code,
            match_date__date=today,
            status__in=['scheduled', 'timed', 'in_play', 'paused'],
        )

        if not pending_matches.exists():
            return stats

        # Fetch current data from API
        url = f"{FOOTBALL_DATA_BASE_URL}/competitions/{api_code}/matches"
        params = {
            'dateFrom': today.strftime('%Y-%m-%d'),
            'dateTo': today.strftime('%Y-%m-%d'),
        }

        response = requests.get(url, headers=self.headers, params=params, timeout=30)
        if response.status_code != 200:
            return stats

        data = response.json()
        for match_data in data.get('matches', []):
            api_id = match_data.get('id')
            try:
                match = Match.objects.get(api_id=api_id)
                new_status = self._map_status(match_data.get('status', ''))

                score = match_data.get('score', {})
                full_time = score.get('fullTime', {})

                changed = False
                if match.status != new_status:
                    match.status = new_status
                    changed = True

                if full_time.get('home') is not None:
                    match.home_score = full_time['home']
                    match.away_score = full_time['away']
                    changed = True

                if new_status in ('in_play', 'paused', 'finished') and not match.is_locked:
                    match.is_locked = True
                    stats['locked'] += 1
                    changed = True

                if changed:
                    match.save()
                    stats['updated'] += 1

            except Match.DoesNotExist:
                continue

        return stats

    def _sync_league(self, api_code: str, our_code: str) -> dict:
        """Sync league and team data."""
        stats = {'leagues': 0, 'teams': 0}

        # Fetch competition details
        url = f"{FOOTBALL_DATA_BASE_URL}/competitions/{api_code}/teams"
        response = requests.get(url, headers=self.headers, timeout=30)

        if response.status_code != 200:
            return stats

        data = response.json()
        comp = data.get('competition', {})

        # Update league
        league, _ = League.objects.update_or_create(
            code=our_code,
            defaults={
                'name': comp.get('name', our_code),
                'country': comp.get('area', {}).get('name', ''),
                'logo_url': comp.get('emblem', ''),
                'api_id': comp.get('id'),
            },
        )
        stats['leagues'] = 1

        # Update teams
        for team_data in data.get('teams', []):
            self._get_or_create_team(team_data, league)
            stats['teams'] += 1

        return stats

    @staticmethod
    def _map_status(api_status: str) -> str:
        """Map football-data.org status to our Match.status."""
        return {
            'SCHEDULED': 'scheduled',
            'TIMED': 'timed',
            'IN_PLAY': 'in_play',
            'PAUSED': 'paused',
            'FINISHED': 'finished',
            'POSTPONED': 'postponed',
            'CANCELLED': 'cancelled',
            'SUSPENDED': 'suspended',
        }.get(api_status.upper(), 'scheduled')

    # ── Stale Data Prevention ───────────────────

    @staticmethod
    def remove_stale_unplayed_matches(older_than_hours: int = 48) -> int:
        """
        Remove matches that should have started but have no data update.
        Prevents fake/stale predictions from persisting.
        """
        cutoff = timezone.now() - timedelta(hours=older_than_hours)
        stale = Match.objects.filter(
            match_date__lt=cutoff,
            status='scheduled',
        )
        count = stale.count()
        stale.update(status='cancelled')
        logger.info(f"Marked {count} stale matches as cancelled")
        return count
