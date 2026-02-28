"""
Data Ingestion Service.

Fetches real daily fixtures from multiple external sources:
  1. ESPN API (primary – free, no API key required)
  2. football-data.org (secondary – free tier requires API key in settings)
  3. OpenLigaDB (fallback for Bundesliga – free, no key)

Flow:
    1. Fetch today's fixtures from ESPN (all 5 leagues)
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
from django.db.models import Q
from django.utils import timezone
from django.db import transaction

from apps.core.models import League, Team, Match, Season

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# API Configuration
# ──────────────────────────────────────────────

# football-data.org (needs API key for EPL / La Liga / Serie A / Ligue 1)
FOOTBALL_DATA_BASE_URL = 'https://api.football-data.org/v4'

# football-data.org league codes mapped to our League.code values
LEAGUE_CODE_MAP = {
    'PL': 'PL',       # Premier League
    'PD': 'LL',       # La Liga (Primera Division)
    'BL1': 'BL1',     # Bundesliga
    'SA': 'SA',       # Serie A
    'FL1': 'FL1',     # Ligue 1
    'CL': 'UCL',      # UEFA Champions League
    'EL': 'UEL',      # UEFA Europa League
    'ECL': 'UECL',    # UEFA Europa Conference League
}

# ── ESPN API (completely free, no key needed) ─────────────────────────────────
ESPN_BASE_URL = 'https://site.api.espn.com/apis/site/v2/sports/soccer'

# Our League.code → ESPN league slug
ESPN_LEAGUE_SLUGS = {
    'PL':   'eng.1',                     # Premier League
    'LL':   'esp.1',                     # La Liga
    'SA':   'ita.1',                     # Serie A
    'BL1':  'ger.1',                     # Bundesliga
    'FL1':  'fra.1',                     # Ligue 1
    'UCL':  'uefa.champions',            # UEFA Champions League
    'UEL':  'uefa.europa',               # UEFA Europa League
    'UECL': 'uefa.europa.conf',          # UEFA Europa Conference League
}

ESPN_STATUS_MAP = {
    'STATUS_SCHEDULED':   'scheduled',
    'STATUS_IN_PROGRESS': 'in_play',
    'STATUS_HALFTIME':    'paused',
    'STATUS_FINAL':       'finished',
    'STATUS_FULL_TIME':   'finished',
    'STATUS_POSTPONED':   'postponed',
    'STATUS_CANCELLED':   'cancelled',
    'STATUS_SUSPENDED':   'suspended',
    'STATUS_ABANDONED':   'cancelled',
    'STATUS_DELAYED':     'scheduled',
}


class DataIngestionService:
    """
    Service for fetching and storing real match data.
    Uses ESPN API as primary source (no key needed), falls back to
    football-data.org when an API key is configured.
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
        """
        Fetch fixtures for a specific date from ESPN (primary) and
        football-data.org (fallback when key is available).

        Seasonal leagues (e.g. Champions League) are only fetched when
        they are in season, unless matches already exist for that date.
        """
        stats = {'created': 0, 'updated': 0, 'errors': []}

        for our_code in ESPN_LEAGUE_SLUGS.keys():
            try:
                # Check if seasonal league is in season before fetching
                league = League.objects.filter(code=our_code, is_active=True).first()
                if league and league.is_seasonal and not league.is_currently_in_season:
                    logger.debug(f"Skipping {our_code}: seasonal league not in season")
                    continue

                # Try ESPN first (always free, no key needed)
                result = self._fetch_espn_fixtures(our_code, target_date)
                stats['created'] += result['created']
                stats['updated'] += result['updated']

                # If ESPN returned nothing AND we have a football-data.org key,
                # fall back to that API for richer data
                if result['created'] == 0 and result['updated'] == 0 and self.api_key:
                    api_code = {v: k for k, v in LEAGUE_CODE_MAP.items()}.get(our_code, our_code)
                    date_str = target_date.strftime('%Y-%m-%d')
                    result2 = self._fetch_league_fixtures(api_code, our_code, date_str)
                    stats['created'] += result2['created']
                    stats['updated'] += result2['updated']

            except Exception as e:
                error_msg = f"Error fetching {our_code}: {e}"
                logger.error(error_msg)
                stats['errors'].append(error_msg)

        logger.info(
            f"Ingestion complete for {target_date}: "
            f"created={stats['created']}, updated={stats['updated']}, "
            f"errors={len(stats['errors'])}"
        )
        return stats

    def fetch_fixtures_for_range(self, start: date, end: date) -> dict:
        """Fetch fixtures for a date range (inclusive)."""
        stats = {'created': 0, 'updated': 0, 'errors': []}
        current = start
        while current <= end:
            day_stats = self.fetch_fixtures_for_date(current)
            stats['created'] += day_stats['created']
            stats['updated'] += day_stats['updated']
            stats['errors'].extend(day_stats.get('errors', []))
            current += timedelta(days=1)
        return stats

    def update_live_scores(self) -> dict:
        """
        Fetch live/recently finished matches and update scores.
        Also locks predictions for in-play matches.
        """
        stats = {'updated': 0, 'locked': 0, 'errors': []}
        today = date.today()

        for our_code in ESPN_LEAGUE_SLUGS.keys():
            try:
                result = self._update_scores_espn(our_code, today)
                stats['updated'] += result['updated']
                stats['locked'] += result['locked']
            except Exception as e:
                stats['errors'].append(f"Error updating {our_code}: {e}")

        return stats

    def sync_teams_and_leagues(self) -> dict:
        """
        Sync league and team data.
        Ensures all 5 leagues exist in the DB with correct priorities.
        """
        stats = {'leagues': 0, 'teams': 0, 'errors': []}

        league_defaults = [
            # ── Continental (priority 0 = highest) ─────────────────────
            {'name': 'UEFA Champions League',         'code': 'UCL',  'country': 'Europe', 'priority': 0, 'api_id': 2001,
             'league_type': 'continental', 'is_seasonal': True,
             'season_months': [9, 10, 11, 12, 1, 2, 3, 4, 5, 6]},
            {'name': 'UEFA Europa League',            'code': 'UEL',  'country': 'Europe', 'priority': 0, 'api_id': 2146,
             'league_type': 'continental', 'is_seasonal': True,
             'season_months': [9, 10, 11, 12, 1, 2, 3, 4, 5, 6]},
            {'name': 'UEFA Europa Conference League', 'code': 'UECL', 'country': 'Europe', 'priority': 0, 'api_id': 2154,
             'league_type': 'continental', 'is_seasonal': True,
             'season_months': [9, 10, 11, 12, 1, 2, 3, 4, 5, 6]},
            # ── Domestic (priority 1–5) ────────────────────────────────
            {'name': 'Premier League',                'code': 'PL',   'country': 'England', 'priority': 1, 'api_id': 2021,
             'league_type': 'domestic', 'is_seasonal': False, 'season_months': []},
            {'name': 'La Liga',                       'code': 'LL',   'country': 'Spain',   'priority': 2, 'api_id': 2014,
             'league_type': 'domestic', 'is_seasonal': False, 'season_months': []},
            {'name': 'Serie A',                       'code': 'SA',   'country': 'Italy',   'priority': 3, 'api_id': 2019,
             'league_type': 'domestic', 'is_seasonal': False, 'season_months': []},
            {'name': 'Bundesliga',                    'code': 'BL1',  'country': 'Germany', 'priority': 4, 'api_id': 2002,
             'league_type': 'domestic', 'is_seasonal': False, 'season_months': []},
            {'name': 'Ligue 1',                       'code': 'FL1',  'country': 'France',  'priority': 5, 'api_id': 2015,
             'league_type': 'domestic', 'is_seasonal': False, 'season_months': []},
        ]

        for ld in league_defaults:
            defaults = {
                'name': ld['name'],
                'country': ld['country'],
                'priority': ld['priority'],
                'api_id': ld['api_id'],
                'is_active': True,
                'league_type': ld.get('league_type', 'domestic'),
                'is_seasonal': ld.get('is_seasonal', False),
                'season_months': ld.get('season_months', []),
            }
            league, created = League.objects.update_or_create(
                code=ld['code'],
                defaults=defaults,
            )
            if created:
                stats['leagues'] += 1
                logger.info(f"Created league: {league.name}")
            else:
                logger.info(f"Updated league: {league.name} priority → {ld['priority']}")

        # Also sync via football-data.org if key is available
        if self.api_key:
            for api_code, our_code in LEAGUE_CODE_MAP.items():
                try:
                    result = self._sync_league(api_code, our_code)
                    stats['teams'] += result.get('teams', 0)
                except Exception as e:
                    stats['errors'].append(f"Sync error {our_code}: {e}")

        return stats

    # ── ESPN API (Primary, Free) ─────────────────

    def _fetch_espn_fixtures(self, our_code: str, target_date: date) -> dict:
        """
        Fetch fixtures for a league and date from the ESPN public API.
        No API key required.
        """
        stats = {'created': 0, 'updated': 0}
        espn_slug = ESPN_LEAGUE_SLUGS.get(our_code)
        if not espn_slug:
            return stats

        league = self._ensure_league(our_code)
        if not league:
            return stats

        date_str = target_date.strftime('%Y%m%d')
        url = f"{ESPN_BASE_URL}/{espn_slug}/scoreboard"
        params = {'dates': date_str}

        try:
            response = requests.get(url, params=params, timeout=15)
            if response.status_code != 200:
                logger.warning(f"ESPN returned {response.status_code} for {our_code} on {target_date}")
                return stats

            data = response.json()
            events = data.get('events', [])

            for event in events:
                try:
                    result = self._process_espn_event(event, league)
                    if result == 'created':
                        stats['created'] += 1
                    elif result == 'updated':
                        stats['updated'] += 1
                except Exception as exc:
                    logger.error(f"ESPN event processing error ({our_code}): {exc}")

        except requests.RequestException as exc:
            logger.error(f"ESPN request failed for {our_code}: {exc}")

        return stats

    @transaction.atomic
    def _process_espn_event(self, event: dict, league: League) -> str:
        """Process a single ESPN event and create/update a Match record."""
        competition = event.get('competitions', [{}])[0]
        competitors = competition.get('competitors', [])

        home_data = next((c for c in competitors if c.get('homeAway') == 'home'), None)
        away_data = next((c for c in competitors if c.get('homeAway') == 'away'), None)

        if not home_data or not away_data:
            return 'skipped'

        # Parse match datetime
        raw_date = event.get('date', '')
        try:
            match_date = datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            logger.warning(f"ESPN: invalid date '{raw_date}' for event {event.get('id')}")
            return 'skipped'

        # Get/create teams
        home_team = self._get_or_create_team_espn(home_data.get('team', {}), league)
        away_team = self._get_or_create_team_espn(away_data.get('team', {}), league)
        if not home_team or not away_team:
            return 'skipped'

        # Map status
        status_type = competition.get('status', {}).get('type', {})
        espn_status_name = status_type.get('name', 'STATUS_SCHEDULED')
        status = ESPN_STATUS_MAP.get(espn_status_name, 'scheduled')

        # Scores (only if finished/in_play)
        home_score = None
        away_score = None
        if status in ('finished', 'in_play', 'paused'):
            try:
                home_score = int(home_data.get('score', 0) or 0)
                away_score = int(away_data.get('score', 0) or 0)
            except (ValueError, TypeError):
                pass

        # Dedup: match existing record by teams + date (regardless of api_id source)
        match_date_only = match_date.date()
        existing = Match.objects.filter(
            home_team=home_team,
            away_team=away_team,
            match_date__date=match_date_only,
        ).first()

        if existing:
            changed = False
            if existing.status != status:
                existing.status = status
                changed = True
            if home_score is not None and existing.home_score != home_score:
                existing.home_score = home_score
                existing.away_score = away_score
                changed = True
            if changed:
                existing.save()
            if status in ('in_play', 'paused', 'finished') and not existing.is_locked:
                existing.lock()
            return 'updated'

        # Create new match
        Match.objects.create(
            league=league,
            home_team=home_team,
            away_team=away_team,
            match_date=match_date,
            status=status,
            home_score=home_score,
            away_score=away_score,
        )
        return 'created'

    def _get_or_create_team_espn(self, team_data: dict, league: League) -> Optional[Team]:
        """Get or create a Team from ESPN team data."""
        name = team_data.get('displayName', '') or team_data.get('name', '')
        if not name:
            return None

        short_name = team_data.get('shortDisplayName', '') or team_data.get('abbreviation', '')
        logo_url   = team_data.get('logo', '')

        # Try name-based lookup within this league first
        team = Team.objects.filter(
            Q(name__iexact=name) | Q(short_name__iexact=short_name),
            league=league,
        ).first()

        if not team:
            # Broader search across all leagues (handles teams that changed leagues)
            team = Team.objects.filter(Q(name__iexact=name)).first()

        if not team:
            team = Team.objects.create(
                name=name,
                short_name=short_name,
                league=league,
                logo_url=logo_url,
            )

        return team

    def _update_scores_espn(self, our_code: str, target_date: date) -> dict:
        """Update scores and statuses for today's matches using ESPN."""
        stats = {'updated': 0, 'locked': 0}
        espn_slug = ESPN_LEAGUE_SLUGS.get(our_code)
        if not espn_slug:
            return stats

        date_str = target_date.strftime('%Y%m%d')
        url = f"{ESPN_BASE_URL}/{espn_slug}/scoreboard"
        params = {'dates': date_str}

        try:
            response = requests.get(url, params=params, timeout=15)
            if response.status_code != 200:
                return stats

            data = response.json()
            for event in data.get('events', []):
                try:
                    competition = event.get('competitions', [{}])[0]
                    competitors = competition.get('competitors', [])
                    home_data = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                    away_data = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                    if not home_data or not away_data:
                        continue

                    home_name = home_data.get('team', {}).get('displayName', '')
                    away_name = away_data.get('team', {}).get('displayName', '')

                    match = Match.objects.filter(
                        home_team__name__iexact=home_name,
                        away_team__name__iexact=away_name,
                        match_date__date=target_date,
                    ).first()
                    if not match:
                        continue

                    status_name = competition.get('status', {}).get('type', {}).get('name', '')
                    new_status = ESPN_STATUS_MAP.get(status_name, match.status)

                    changed = False
                    if match.status != new_status:
                        match.status = new_status
                        changed = True

                    if new_status in ('finished', 'in_play', 'paused'):
                        try:
                            hs = int(home_data.get('score', 0) or 0)
                            as_ = int(away_data.get('score', 0) or 0)
                            if match.home_score != hs or match.away_score != as_:
                                match.home_score = hs
                                match.away_score = as_
                                changed = True
                        except (ValueError, TypeError):
                            pass

                    if changed:
                        match.save()
                        stats['updated'] += 1

                    if new_status in ('in_play', 'paused', 'finished') and not match.is_locked:
                        match.lock()
                        stats['locked'] += 1

                except Exception as exc:
                    logger.error(f"ESPN score update error: {exc}")

        except requests.RequestException as exc:
            logger.error(f"ESPN live score request failed: {exc}")

        return stats

    # ── League Bootstrap Helper ──────────────────

    def _ensure_league(self, our_code: str) -> Optional[League]:
        """Ensure a League record exists for the given code."""
        defaults_map = {
            # Continental (priority 0)
            'UCL':  {'name': 'UEFA Champions League',         'country': 'Europe',  'priority': 0, 'api_id': 2001,
                     'league_type': 'continental', 'is_seasonal': True,
                     'season_months': [9, 10, 11, 12, 1, 2, 3, 4, 5, 6]},
            'UEL':  {'name': 'UEFA Europa League',            'country': 'Europe',  'priority': 0, 'api_id': 2146,
                     'league_type': 'continental', 'is_seasonal': True,
                     'season_months': [9, 10, 11, 12, 1, 2, 3, 4, 5, 6]},
            'UECL': {'name': 'UEFA Europa Conference League', 'country': 'Europe',  'priority': 0, 'api_id': 2154,
                     'league_type': 'continental', 'is_seasonal': True,
                     'season_months': [9, 10, 11, 12, 1, 2, 3, 4, 5, 6]},
            # Domestic
            'PL':   {'name': 'Premier League',  'country': 'England', 'priority': 1, 'api_id': 2021,
                     'league_type': 'domestic', 'is_seasonal': False, 'season_months': []},
            'LL':   {'name': 'La Liga',         'country': 'Spain',   'priority': 2, 'api_id': 2014,
                     'league_type': 'domestic', 'is_seasonal': False, 'season_months': []},
            'SA':   {'name': 'Serie A',         'country': 'Italy',   'priority': 3, 'api_id': 2019,
                     'league_type': 'domestic', 'is_seasonal': False, 'season_months': []},
            'BL1':  {'name': 'Bundesliga',      'country': 'Germany', 'priority': 4, 'api_id': 2002,
                     'league_type': 'domestic', 'is_seasonal': False, 'season_months': []},
            'FL1':  {'name': 'Ligue 1',         'country': 'France',  'priority': 5, 'api_id': 2015,
                     'league_type': 'domestic', 'is_seasonal': False, 'season_months': []},
        }
        defaults = defaults_map.get(our_code)
        if not defaults:
            return League.objects.filter(code=our_code).first()

        league, _ = League.objects.get_or_create(
            code=our_code,
            defaults={**defaults, 'is_active': True},
        )
        return league

    # ── football-data.org (Secondary) ───────────

    def _fetch_league_fixtures(
        self, api_code: str, our_code: str,
        date_from: str, date_to: str = None,
    ) -> dict:
        """Fetch and store fixtures for a specific league via football-data.org."""
        # Use OpenLigaDB for Bundesliga (BL1) as free backup
        if our_code == 'BL1' and not self.api_key:
            return self._fetch_openligadb_bl1(date_from, date_to)

        stats = {'created': 0, 'updated': 0}

        url = f"{FOOTBALL_DATA_BASE_URL}/competitions/{api_code}/matches"
        params = {'dateFrom': date_from, 'dateTo': date_to or date_from}

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
        league = self._ensure_league('BL1')

        try:
            target_date = datetime.strptime(date_from, '%Y-%m-%d')
            year = target_date.year
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

            start_dt = datetime.strptime(date_from, '%Y-%m-%d')
            end_dt = datetime.strptime(date_to or date_from, '%Y-%m-%d').replace(
                hour=23, minute=59, second=59
            )

            for m in matches:
                match_dt_str = m.get('matchDateTimeUTC', '')
                try:
                    match_dt = datetime.fromisoformat(match_dt_str.replace('Z', '+00:00'))
                except Exception:
                    continue

                if not (start_dt.date() <= match_dt.date() <= end_dt.date()):
                    continue

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

                is_finished = m.get('matchIsFinished', False)
                status = 'finished' if is_finished else 'scheduled'

                home_score = None
                away_score = None
                if is_finished:
                    results = m.get('matchResults', [])
                    final = next((r for r in results if r.get('resultTypeID') == 2), None)
                    if final:
                        home_score = final.get('pointsTeam1')
                        away_score = final.get('pointsTeam2')

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
        """Create or update a single match from football-data.org API data."""
        api_id = data.get('id')
        if not api_id:
            return 'skipped'

        home_data = data.get('homeTeam', {})
        away_data = data.get('awayTeam', {})

        home_team = self._get_or_create_team(home_data, league)
        away_team = self._get_or_create_team(away_data, league)

        if not home_team or not away_team:
            return 'skipped'

        utc_date = data.get('utcDate', '')
        try:
            match_date = datetime.fromisoformat(utc_date.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            logger.error(f"Invalid date: {utc_date}")
            return 'skipped'

        api_status = data.get('status', 'SCHEDULED')
        status = self._map_status(api_status)

        score = data.get('score', {})
        full_time = score.get('fullTime', {})
        half_time = score.get('halfTime', {})

        season_data = data.get('season', {})
        season = None
        if season_data:
            season, _ = Season.objects.get_or_create(
                league=league,
                year=str(season_data.get('id', '')),
                defaults={
                    'start_date': season_data.get('startDate', match_date.date()),
                    'end_date':   season_data.get('endDate', match_date.date()),
                    'is_current': season_data.get('currentMatchday') is not None,
                },
            )

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

        if status in ('in_play', 'paused', 'finished') and not match.is_locked:
            match.lock()

        return 'created' if created else 'updated'

    def _get_or_create_team(self, team_data: dict, league: League) -> Optional[Team]:
        """Get or create a team from football-data.org team data."""
        api_id = team_data.get('id')
        if not api_id:
            return None

        team, _ = Team.objects.update_or_create(
            api_id=api_id,
            defaults={
                'name':       team_data.get('name', 'Unknown'),
                'short_name': team_data.get('shortName', ''),
                'code':       team_data.get('tla', ''),
                'logo_url':   team_data.get('crest', ''),
                'league':     league,
            },
        )
        return team

    def _update_scores_for_league(self, api_code: str, our_code: str) -> dict:
        """Update scores for in-play and recently finished matches via football-data.org."""
        stats = {'updated': 0, 'locked': 0}

        today = date.today()
        pending_matches = Match.objects.filter(
            league__code=our_code,
            match_date__date=today,
            status__in=['scheduled', 'timed', 'in_play', 'paused'],
        )

        if not pending_matches.exists():
            return stats

        url = f"{FOOTBALL_DATA_BASE_URL}/competitions/{api_code}/matches"
        params = {
            'dateFrom': today.strftime('%Y-%m-%d'),
            'dateTo':   today.strftime('%Y-%m-%d'),
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
        """Sync league and team data from football-data.org."""
        stats = {'leagues': 0, 'teams': 0}

        url = f"{FOOTBALL_DATA_BASE_URL}/competitions/{api_code}/teams"
        response = requests.get(url, headers=self.headers, timeout=30)

        if response.status_code != 200:
            return stats

        data = response.json()
        comp = data.get('competition', {})

        League.objects.update_or_create(
            code=our_code,
            defaults={
                'name':    comp.get('name', our_code),
                'country': comp.get('area', {}).get('name', ''),
                'logo_url': comp.get('emblem', ''),
                'api_id':  comp.get('id'),
            },
        )
        stats['leagues'] = 1

        for team_data in data.get('teams', []):
            league = League.objects.filter(code=our_code).first()
            if league:
                self._get_or_create_team(team_data, league)
                stats['teams'] += 1

        return stats

    @staticmethod
    def _map_status(api_status: str) -> str:
        """Map football-data.org status to our Match.status."""
        return {
            'SCHEDULED': 'scheduled',
            'TIMED':     'timed',
            'IN_PLAY':   'in_play',
            'PAUSED':    'paused',
            'FINISHED':  'finished',
            'POSTPONED': 'postponed',
            'CANCELLED': 'cancelled',
            'SUSPENDED': 'suspended',
        }.get(api_status.upper(), 'scheduled')

    # ── Stale Data Prevention ────────────────────

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
