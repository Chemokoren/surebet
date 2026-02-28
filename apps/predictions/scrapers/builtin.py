"""
Built-in scrapers for the top prediction websites.

Each scraper targets a publicly available prediction page and extracts
match predictions.  Scrapers are designed to be resilient — if a site's
format changes, the scraper logs warnings and returns what it can.

These scrapers respect rate limits and use respectful scraping practices.
They target publicly available prediction data only.
"""

import logging
import re
from datetime import date
from typing import List, Optional

from .base import BaseScraper

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# 1. PredictZ  (predictz.com)
#    High-volume AI predictions, current form stats
# ══════════════════════════════════════════════════════════════════════════════

class PredictZScraper(BaseScraper):
    """Scrapes predictz.com for match predictions."""

    RATE_LIMIT_SECONDS = 3.0

    LEAGUE_URLS = {
        'PL':   'https://www.predictz.com/predictions/england/premier-league/',
        'LL':   'https://www.predictz.com/predictions/spain/la-liga/',
        'SA':   'https://www.predictz.com/predictions/italy/serie-a/',
        'BL1':  'https://www.predictz.com/predictions/germany/bundesliga/',
        'FL1':  'https://www.predictz.com/predictions/france/ligue-1/',
        'UCL':  'https://www.predictz.com/predictions/champions-league/',
        'UEL':  'https://www.predictz.com/predictions/europa-league/',
        'UECL': 'https://www.predictz.com/predictions/conference-league/',
    }

    def scrape_predictions(self, target_date: date) -> List[dict]:
        predictions = []
        for league_code, url in self.LEAGUE_URLS.items():
            try:
                response = self._rate_limited_get(url)
                if not response:
                    continue
                page_preds = self._parse_page(response.text, league_code, target_date)
                predictions.extend(page_preds)
            except Exception as e:
                logger.error(f"PredictZ scrape error for {league_code}: {e}")
        return predictions

    def _parse_page(self, html: str, league_code: str, target_date: date) -> List[dict]:
        """Parse PredictZ prediction page HTML."""
        predictions = []
        try:
            # PredictZ uses table rows with prediction percentages
            # Pattern: home_team | prediction % | away_team
            rows = re.findall(
                r'<tr[^>]*>.*?'
                r'class="[^"]*tpointed[^"]*"[^>]*>(.*?)</tr>',
                html, re.DOTALL
            )
            for row in rows:
                pred = self._parse_row(row, league_code)
                if pred:
                    predictions.append(pred)
        except Exception as e:
            logger.debug(f"PredictZ parse error: {e}")
        return predictions

    def _parse_row(self, row_html: str, league_code: str) -> Optional[dict]:
        """Parse a single prediction row."""
        try:
            # Extract team names
            teams = re.findall(r'<a[^>]*>([^<]+)</a>', row_html)
            if len(teams) < 2:
                return None

            # Extract percentages (home%, draw%, away%)
            pcts = re.findall(r'(\d{1,3})%', row_html)
            if len(pcts) >= 3:
                h, d, a = float(pcts[0])/100, float(pcts[1])/100, float(pcts[2])/100
            else:
                return None

            # Determine outcome
            if h >= d and h >= a:
                outcome = 'home_win'
            elif a >= h and a >= d:
                outcome = 'away_win'
            else:
                outcome = 'draw'

            return {
                'home_team': teams[0].strip(),
                'away_team': teams[1].strip(),
                'league_code': league_code,
                'predicted_outcome': outcome,
                'home_win_prob': h,
                'draw_prob': d,
                'away_win_prob': a,
                'confidence': max(h, d, a) * 100,
                'raw_data': {'source_html_snippet': row_html[:500]},
            }
        except Exception:
            return None

    def parse_prediction(self, raw: dict, match=None) -> Optional[dict]:
        return raw if raw.get('predicted_outcome') else None


# ══════════════════════════════════════════════════════════════════════════════
# 2. Forebet  (forebet.com)
#    Mathematical predictions with probabilities
# ══════════════════════════════════════════════════════════════════════════════

class ForebetScraper(BaseScraper):
    """Scrapes forebet.com for probability-based predictions."""

    RATE_LIMIT_SECONDS = 3.0

    LEAGUE_URLS = {
        'PL':   'https://www.forebet.com/en/football-predictions/england/premier-league',
        'LL':   'https://www.forebet.com/en/football-predictions/spain/la-liga',
        'SA':   'https://www.forebet.com/en/football-predictions/italy/serie-a',
        'BL1':  'https://www.forebet.com/en/football-predictions/germany/bundesliga',
        'FL1':  'https://www.forebet.com/en/football-predictions/france/ligue-1',
        'UCL':  'https://www.forebet.com/en/football-predictions/champions-league',
        'UEL':  'https://www.forebet.com/en/football-predictions/europa-league',
    }

    def scrape_predictions(self, target_date: date) -> List[dict]:
        predictions = []
        for league_code, url in self.LEAGUE_URLS.items():
            try:
                response = self._rate_limited_get(url)
                if not response:
                    continue
                page_preds = self._extract_predictions(response.text, league_code)
                predictions.extend(page_preds)
            except Exception as e:
                logger.error(f"Forebet scrape error for {league_code}: {e}")
        return predictions

    def _extract_predictions(self, html: str, league_code: str) -> List[dict]:
        """Extract predictions from Forebet's page."""
        predictions = []
        try:
            # Forebet uses prediction containers with class 'rcnt'
            blocks = re.findall(
                r'<div class="rcnt[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
                html, re.DOTALL
            )
            for block in blocks:
                pred = self._parse_block(block, league_code)
                if pred:
                    predictions.append(pred)
        except Exception as e:
            logger.debug(f"Forebet parse error: {e}")
        return predictions

    def _parse_block(self, block_html: str, league_code: str) -> Optional[dict]:
        try:
            teams = re.findall(r'class="homeTeam[^"]*"[^>]*>(.*?)</span>|class="awayTeam[^"]*"[^>]*>(.*?)</span>', block_html)
            pcts = re.findall(r'class="fprob[^"]*"[^>]*>(\d+)', block_html)

            if len(pcts) >= 3:
                h, d, a = float(pcts[0])/100, float(pcts[1])/100, float(pcts[2])/100
                home_name = teams[0][0] if teams and teams[0][0] else 'Unknown'
                away_name = teams[1][1] if len(teams) > 1 and teams[1][1] else 'Unknown'

                if h >= d and h >= a:
                    outcome = 'home_win'
                elif a >= h and a >= d:
                    outcome = 'away_win'
                else:
                    outcome = 'draw'

                return {
                    'home_team': home_name.strip(),
                    'away_team': away_name.strip(),
                    'league_code': league_code,
                    'predicted_outcome': outcome,
                    'home_win_prob': h, 'draw_prob': d, 'away_win_prob': a,
                    'confidence': max(h, d, a) * 100,
                    'raw_data': {},
                }
        except Exception:
            pass
        return None

    def parse_prediction(self, raw: dict, match=None) -> Optional[dict]:
        return raw if raw.get('predicted_outcome') else None


# ══════════════════════════════════════════════════════════════════════════════
# 3. Vitibet  (vitibet.com)
#    Simple index-based predictions
# ══════════════════════════════════════════════════════════════════════════════

class VitibetScraper(BaseScraper):
    """Scrapes vitibet.com for quick index predictions."""

    RATE_LIMIT_SECONDS = 2.0
    BASE_URL = 'https://www.vitibet.com/index.php?claession=soccer&action=2'

    def scrape_predictions(self, target_date: date) -> List[dict]:
        predictions = []
        try:
            response = self._rate_limited_get(self.BASE_URL)
            if not response:
                return predictions
            predictions = self._extract_predictions(response.text, target_date)
        except Exception as e:
            logger.error(f"Vitibet scrape error: {e}")
        return predictions

    def _extract_predictions(self, html: str, target_date: date) -> List[dict]:
        predictions = []
        try:
            rows = re.findall(r'<tr[^>]*bgcolor[^>]*>(.*?)</tr>', html, re.DOTALL)
            for row in rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                if len(cells) >= 5:
                    home = re.sub(r'<[^>]+>', '', cells[0]).strip()
                    away = re.sub(r'<[^>]+>', '', cells[2]).strip()
                    tip = re.sub(r'<[^>]+>', '', cells[4]).strip()

                    if home and away and tip:
                        outcome = 'home_win' if '1' in tip else ('away_win' if '2' in tip else 'draw')
                        predictions.append({
                            'home_team': home,
                            'away_team': away,
                            'predicted_outcome': outcome,
                            'home_win_prob': None,
                            'draw_prob': None,
                            'away_win_prob': None,
                            'confidence': None,
                            'raw_data': {'tip': tip},
                        })
        except Exception as e:
            logger.debug(f"Vitibet parse error: {e}")
        return predictions

    def parse_prediction(self, raw: dict, match=None) -> Optional[dict]:
        return raw if raw.get('predicted_outcome') else None


# ══════════════════════════════════════════════════════════════════════════════
# 4. Betensured  (betensured.com)
#    Reliable forecasts
# ══════════════════════════════════════════════════════════════════════════════

class BetensuredScraper(BaseScraper):
    """Scrapes betensured.com for match predictions."""

    RATE_LIMIT_SECONDS = 3.0

    def scrape_predictions(self, target_date: date) -> List[dict]:
        url = f'https://www.betensured.com/predictions/{target_date.strftime("%Y-%m-%d")}'
        predictions = []
        try:
            response = self._rate_limited_get(url)
            if not response:
                return predictions
            predictions = self._extract_predictions(response.text)
        except Exception as e:
            logger.error(f"Betensured scrape error: {e}")
        return predictions

    def _extract_predictions(self, html: str) -> List[dict]:
        predictions = []
        try:
            blocks = re.findall(r'class="prediction-card[^"]*"(.*?)</div>\s*</div>', html, re.DOTALL)
            for block in blocks:
                teams = re.findall(r'class="team-name[^"]*"[^>]*>([^<]+)', block)
                tip = re.findall(r'class="tip[^"]*"[^>]*>([^<]+)', block)
                if len(teams) >= 2 and tip:
                    tip_val = tip[0].strip()
                    outcome = 'home_win' if '1' in tip_val else ('away_win' if '2' in tip_val else 'draw')
                    predictions.append({
                        'home_team': teams[0].strip(),
                        'away_team': teams[1].strip(),
                        'predicted_outcome': outcome,
                        'home_win_prob': None,
                        'draw_prob': None,
                        'away_win_prob': None,
                        'confidence': None,
                        'raw_data': {'tip': tip_val},
                    })
        except Exception as e:
            logger.debug(f"Betensured parse error: {e}")
        return predictions

    def parse_prediction(self, raw: dict, match=None) -> Optional[dict]:
        return raw if raw.get('predicted_outcome') else None


# ══════════════════════════════════════════════════════════════════════════════
# Stubs for remaining scrapers (extend as site formats are analysed)
# ══════════════════════════════════════════════════════════════════════════════

class SportsMoleScraper(BaseScraper):
    """Stub: Sports Mole prediction scraper. Implement when site structure is analysed."""
    def scrape_predictions(self, target_date: date) -> List[dict]:
        logger.info("SportsMoleScraper not yet fully implemented")
        return []
    def parse_prediction(self, raw: dict, match=None) -> Optional[dict]:
        return raw if raw.get('predicted_outcome') else None


class WhoScoredScraper(BaseScraper):
    """Stub: WhoScored scraper. Uses Opta data — may require JS rendering."""
    def scrape_predictions(self, target_date: date) -> List[dict]:
        logger.info("WhoScoredScraper not yet fully implemented")
        return []
    def parse_prediction(self, raw: dict, match=None) -> Optional[dict]:
        return raw if raw.get('predicted_outcome') else None


class FootballWhispersScraper(BaseScraper):
    """Stub: Football Whispers scraper."""
    def scrape_predictions(self, target_date: date) -> List[dict]:
        logger.info("FootballWhispersScraper not yet fully implemented")
        return []
    def parse_prediction(self, raw: dict, match=None) -> Optional[dict]:
        return raw if raw.get('predicted_outcome') else None


class FootyStatsScraper(BaseScraper):
    """Stub: FootyStats scraper (xG, corners, cards data)."""
    def scrape_predictions(self, target_date: date) -> List[dict]:
        logger.info("FootyStatsScraper not yet fully implemented")
        return []
    def parse_prediction(self, raw: dict, match=None) -> Optional[dict]:
        return raw if raw.get('predicted_outcome') else None


class UnderstatScraper(BaseScraper):
    """Stub: Understat xG model scraper."""
    def scrape_predictions(self, target_date: date) -> List[dict]:
        logger.info("UnderstatScraper not yet fully implemented")
        return []
    def parse_prediction(self, raw: dict, match=None) -> Optional[dict]:
        return raw if raw.get('predicted_outcome') else None


class DimersScraper(BaseScraper):
    """Stub: Dimers ML probability scraper."""
    def scrape_predictions(self, target_date: date) -> List[dict]:
        logger.info("DimersScraper not yet fully implemented")
        return []
    def parse_prediction(self, raw: dict, match=None) -> Optional[dict]:
        return raw if raw.get('predicted_outcome') else None


class SquawkaScraper(BaseScraper):
    """Stub: Squawka tactical comparison scraper."""
    def scrape_predictions(self, target_date: date) -> List[dict]:
        logger.info("SquawkaScraper not yet fully implemented")
        return []
    def parse_prediction(self, raw: dict, match=None) -> Optional[dict]:
        return raw if raw.get('predicted_outcome') else None
