"""
Base scraper class for external prediction sources.

All scrapers inherit from BaseScraper and implement:
  - scrape_predictions(target_date) → list of prediction dicts
  - parse_prediction(raw) → normalised prediction dict

Scrapers handle rate limiting, retries, and User-Agent rotation
to be respectful of external sites.
"""

import logging
import random
import re
import time
import unicodedata
from abc import ABC, abstractmethod
from datetime import date
from typing import List, Optional

import requests
from django.utils import timezone

logger = logging.getLogger(__name__)

# Rotate user agents to be respectful
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
]


class BaseScraper(ABC):
    """
    Base class for all external prediction scrapers.

    Subclasses must implement:
      - scrape_predictions(target_date) → list of raw prediction dicts
      - parse_prediction(raw, match) → normalised dict or None

    The normalised dict should contain:
      {
        'predicted_outcome': 'home_win' | 'draw' | 'away_win',
        'home_win_prob': float | None,
        'draw_prob': float | None,
        'away_win_prob': float | None,
        'confidence': float | None,  (0-100)
        'raw_data': dict,            (original scraped data)
      }
    """

    # Rate limit: seconds between requests to this source
    RATE_LIMIT_SECONDS = 2.0

    # Maximum retries per request
    MAX_RETRIES = 3

    # Request timeout
    TIMEOUT = 20

    def __init__(self, source=None, config: dict = None):
        """
        Args:
            source: PredictionSource model instance (if available)
            config: Dict of extra configuration (from source.scrape_config)
        """
        self.source = source
        self.config = config or {}
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        self._last_request_time = 0

    def _rate_limited_get(self, url: str, **kwargs) -> Optional[requests.Response]:
        """Make a rate-limited GET request with retries."""
        # Enforce rate limit
        elapsed = time.time() - self._last_request_time
        if elapsed < self.RATE_LIMIT_SECONDS:
            time.sleep(self.RATE_LIMIT_SECONDS - elapsed)

        for attempt in range(self.MAX_RETRIES):
            try:
                self._last_request_time = time.time()
                response = self.session.get(url, timeout=self.TIMEOUT, **kwargs)
                if response.status_code == 200:
                    return response
                if response.status_code == 429:
                    # Rate limited — back off
                    wait = (attempt + 1) * 5
                    logger.warning(f"Rate limited by {url}, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                logger.warning(f"HTTP {response.status_code} from {url}")
                return None
            except requests.RequestException as e:
                logger.warning(f"Request failed ({attempt+1}/{self.MAX_RETRIES}): {e}")
                time.sleep(2 ** attempt)

        return None

    @abstractmethod
    def scrape_predictions(self, target_date: date) -> List[dict]:
        """
        Scrape predictions for the given date.
        Returns a list of raw prediction dicts.
        """
        pass

    @abstractmethod
    def parse_prediction(self, raw: dict, match=None) -> Optional[dict]:
        """
        Parse a raw scraped prediction into our normalised format.
        Returns None if the prediction can't be parsed.
        """
        pass

    def match_teams(self, scraped_home: str, scraped_away: str, matches) -> Optional[object]:
        """
        Find the matching Match object from our database.

        Resolution order:
          1. TeamAlias exact lookup (canonical fast path)
          2. Direct Team name/short_name/code match
          3. Fuzzy string matching (last resort)
        """
        from apps.core.models_aliases import TeamAlias

        # 1. Alias-based (fast, canonical)
        home_team = TeamAlias.resolve_or_fuzzy(scraped_home)
        away_team = TeamAlias.resolve_or_fuzzy(scraped_away)

        if home_team and away_team:
            for m in matches:
                if m.home_team_id == home_team.id and m.away_team_id == away_team.id:
                    return m

        # 2. Fuzzy string match against match list
        scraped_home_lower = self._normalise_name(scraped_home)
        scraped_away_lower = self._normalise_name(scraped_away)

        for match in matches:
            our_home = self._normalise_name(match.home_team.name)
            our_away = self._normalise_name(match.away_team.name)

            if (self._fuzzy_match(scraped_home_lower, our_home)
                    and self._fuzzy_match(scraped_away_lower, our_away)):
                # Auto-learn aliases for future fast lookups
                self._auto_learn_alias(scraped_home, match.home_team)
                self._auto_learn_alias(scraped_away, match.away_team)
                return match

        return None

    @staticmethod
    def _normalise_name(name: str) -> str:
        """Normalise team name: lowercase, strip accents, remove FC/SC etc."""
        name = name.strip().lower()
        # Strip accents: é→e, ü→u, etc.
        name = unicodedata.normalize('NFKD', name)
        name = ''.join(c for c in name if not unicodedata.combining(c))
        # Remove common suffixes
        for suffix in [' fc', ' cf', ' afc', ' sc', ' ac', ' fk', ' sk']:
            name = name.replace(suffix, '')
        return name.strip()

    @staticmethod
    def _fuzzy_match(name_a: str, name_b: str) -> bool:
        """Fuzzy team name comparison."""
        if name_a == name_b:
            return True
        if name_a in name_b or name_b in name_a:
            return True
        # Word overlap
        words_a = set(name_a.split())
        words_b = set(name_b.split())
        if words_a and words_b:
            overlap = words_a & words_b
            if len(overlap) >= max(1, min(len(words_a), len(words_b)) - 1):
                return True
        return False

    @staticmethod
    def _auto_learn_alias(scraped_name: str, team):
        """Auto-create alias when fuzzy match succeeds."""
        from apps.core.models_aliases import TeamAlias
        clean = scraped_name.strip().lower()
        if clean != team.name.strip().lower():
            try:
                TeamAlias.objects.get_or_create(
                    alias=clean,
                    defaults={'team': team, 'source': 'auto-scraper'},
                )
            except Exception:
                pass  # Silently skip if alias already exists for different team

    @staticmethod
    def _clean_html(text: str) -> str:
        """Strip all HTML tags from a string."""
        return re.sub(r'<[^>]+>', '', text).strip()

    @staticmethod
    def _outcome_from_probs(h: float, d: float, a: float) -> str:
        """Determine outcome from probabilities."""
        if h >= d and h >= a:
            return 'home_win'
        elif a >= h and a >= d:
            return 'away_win'
        return 'draw'

    @staticmethod
    def _outcome_from_tip(tip: str) -> Optional[str]:
        """Parse common tip formats: 1, X, 2, 1X, X2, Home, Away, Draw."""
        tip = tip.strip().upper()
        if tip in ('1', 'HOME', 'HOME WIN', 'H'):
            return 'home_win'
        elif tip in ('2', 'AWAY', 'AWAY WIN', 'A'):
            return 'away_win'
        elif tip in ('X', 'DRAW', 'D'):
            return 'draw'
        elif tip in ('1X',):
            return 'home_win'  # Lean home
        elif tip in ('X2',):
            return 'away_win'  # Lean away
        return None
