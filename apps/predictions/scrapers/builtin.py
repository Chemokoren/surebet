"""
Universal Config-Driven Scraper.

Instead of writing a separate class for each site, this single scraper
reads extraction rules from sources_config.json and dynamically scrapes
any site whose patterns are defined there.

Architecture:
  sources_config.json  →  UniversalScraper  →  normalised predictions
                              ↑
                    SourceQualityManager (updates JSON automatically)

Supported extraction formats:
  - percentage_table:  HTML table rows with H/D/A percentages
  - percentage_block:  Div blocks with probability values
  - percentage_card:   Card layout with % + optional tip fallback
  - tip_table:         HTML table rows with 1/X/2 tips
  - tip_card:          Div/article cards with tip text
  - article_listing:   Index page → individual article pages with score lines
"""

import json
import logging
import os
import re
from datetime import date
from typing import List, Optional

from .base import BaseScraper

logger = logging.getLogger(__name__)

# Path to the JSON config (relative to this file)
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'sources_config.json')


class UniversalScraper(BaseScraper):
    """
    A single data-driven scraper that handles any site defined in
    sources_config.json.  The extraction format and CSS/regex patterns
    are read from the JSON config — no per-site Python class needed.

    Usage:
        scraper = UniversalScraper(source=source_obj, config=site_config_dict)
        predictions = scraper.scrape_predictions(target_date)
    """

    def __init__(self, source=None, config: dict = None):
        super().__init__(source=source, config=config or {})
        # Site-specific config from JSON
        self._site = config or {}
        self.RATE_LIMIT_SECONDS = self._site.get('rate_limit_seconds', 3.0)

    # ── Public API ──────────────────────────────────────────────────────

    def scrape_predictions(self, target_date: date) -> List[dict]:
        """
        Dispatch to the right extraction method based on the site's
        'extraction.format' field in the JSON config.
        """
        extraction = self._site.get('extraction', {})
        fmt = extraction.get('format', '')

        if fmt == 'js_required':
            slug = self._site.get('slug', '?')
            logger.info(f"UniversalScraper: {slug} requires JS rendering — skipping")
            return []

        urls = self._site.get('urls', {})
        if not urls:
            return []

        if fmt == 'article_listing':
            return self._scrape_article_listing(urls, extraction, target_date)

        # All other formats iterate over league URLs
        return self._scrape_urls(urls, extraction, fmt, target_date)

    def parse_prediction(self, raw: dict, match=None) -> Optional[dict]:
        return raw if raw.get('predicted_outcome') else None

    # ── URL iteration ───────────────────────────────────────────────────

    def _scrape_urls(self, urls: dict, extraction: dict, fmt: str,
                     target_date: date) -> List[dict]:
        """Iterate over league URLs and extract predictions."""
        predictions = []
        slug = self._site.get('slug', '?')

        for league_code, url in urls.items():
            # Resolve special URL types
            url = self._resolve_url(url, league_code, target_date)
            if not url:
                continue

            try:
                response = self._rate_limited_get(url)
                if not response:
                    continue

                page_preds = self._extract_by_format(
                    response.text, extraction, fmt, league_code
                )
                predictions.extend(page_preds)

            except Exception as e:
                logger.error(f"UniversalScraper[{slug}] error for {league_code}: {e}")

        return predictions

    def _resolve_url(self, url: str, league_code: str, target_date: date) -> Optional[str]:
        """Resolve URL templates like {date}."""
        if league_code == '_all' or league_code == '_listing':
            pass  # Use URL as-is
        elif league_code == '_dated':
            url = url.replace('{date}', target_date.strftime('%Y-%m-%d'))
        return url

    # ── Format dispatchers ──────────────────────────────────────────────

    def _extract_by_format(self, html: str, ext: dict, fmt: str,
                           league_code: str) -> List[dict]:
        """Dispatch extraction to the right method by format."""
        try:
            if fmt == 'percentage_table':
                return self._extract_percentage_table(html, ext, league_code)
            elif fmt == 'percentage_block':
                return self._extract_percentage_block(html, ext, league_code)
            elif fmt == 'percentage_card':
                return self._extract_percentage_card(html, ext, league_code)
            elif fmt == 'tip_table':
                return self._extract_tip_table(html, ext, league_code)
            elif fmt == 'tip_card':
                return self._extract_tip_card(html, ext, league_code)
            else:
                logger.warning(f"Unknown extraction format: {fmt}")
                return []
        except Exception as e:
            logger.debug(f"Extraction error ({fmt}): {e}")
            return []

    # ── Extraction implementations ──────────────────────────────────────

    def _extract_percentage_table(self, html: str, ext: dict,
                                  league_code: str) -> List[dict]:
        """
        Extract from table rows containing percentage probabilities.
        Config keys: row_pattern, team_pattern, probability_pattern, prob_order
        """
        predictions = []
        row_pat = ext.get('row_pattern', '')
        team_pat = ext.get('team_pattern', '')
        prob_pat = ext.get('probability_pattern', r'(\d{1,3})%')
        prob_order = ext.get('prob_order', ['home', 'draw', 'away'])
        min_teams = ext.get('min_teams', 2)
        min_probs = ext.get('min_probabilities', 3)

        rows = re.findall(row_pat, html, re.DOTALL) if row_pat else []

        for row in rows:
            teams = re.findall(team_pat, row)
            # Clean HTML from team names
            teams = [self._clean_html(t) if isinstance(t, str) else
                     self._clean_html(next((x for x in t if x), ''))
                     for t in teams]
            teams = [t for t in teams if t]
            if len(teams) < min_teams:
                continue

            pcts = re.findall(prob_pat, row)
            if len(pcts) < min_probs:
                continue

            probs = {}
            for i, key in enumerate(prob_order):
                if i < len(pcts):
                    probs[key] = float(pcts[i]) / 100.0

            h = probs.get('home', 0.33)
            d = probs.get('draw', 0.33)
            a = probs.get('away', 0.33)

            predictions.append({
                'home_team': teams[0].strip(),
                'away_team': teams[1].strip(),
                'league_code': league_code if league_code[0] != '_' else None,
                'predicted_outcome': self._outcome_from_probs(h, d, a),
                'home_win_prob': h, 'draw_prob': d, 'away_win_prob': a,
                'confidence': max(h, d, a) * 100,
                'raw_data': {'source': self._site.get('slug')},
            })

        return predictions

    def _extract_percentage_block(self, html: str, ext: dict,
                                  league_code: str) -> List[dict]:
        """
        Extract from div blocks with probability values.
        Config keys: block_pattern, team_pattern, probability_pattern
        """
        predictions = []
        block_pat = ext.get('block_pattern', '')
        team_pat = ext.get('team_pattern', '')
        prob_pat = ext.get('probability_pattern', r'(\d+)')
        prob_order = ext.get('prob_order', ['home', 'draw', 'away'])

        blocks = re.findall(block_pat, html, re.DOTALL) if block_pat else []

        for block in blocks:
            teams = re.findall(team_pat, block)
            teams = [self._clean_html(t) if isinstance(t, str) else
                     self._clean_html(next((x for x in t if x), ''))
                     for t in teams]
            teams = [t for t in teams if t and t != 'Unknown']
            if len(teams) < 2:
                continue

            pcts = re.findall(prob_pat, block)
            if len(pcts) < 3:
                continue

            probs = {}
            for i, key in enumerate(prob_order):
                if i < len(pcts):
                    probs[key] = float(pcts[i]) / 100.0

            h = probs.get('home', 0.33)
            d = probs.get('draw', 0.33)
            a = probs.get('away', 0.33)

            predictions.append({
                'home_team': teams[0].strip(),
                'away_team': teams[-1].strip(),
                'league_code': league_code if league_code[0] != '_' else None,
                'predicted_outcome': self._outcome_from_probs(h, d, a),
                'home_win_prob': h, 'draw_prob': d, 'away_win_prob': a,
                'confidence': max(h, d, a) * 100,
                'raw_data': {'source': self._site.get('slug')},
            })

        return predictions

    def _extract_percentage_card(self, html: str, ext: dict,
                                 league_code: str) -> List[dict]:
        """
        Card layout with percentages and optional tip fallback.
        Tries percentages first; if not enough, falls back to tip_pattern.
        """
        predictions = []
        block_pat = ext.get('block_pattern', '')
        team_pat = ext.get('team_pattern', '')
        prob_pat = ext.get('probability_pattern', r'(\d{1,3})%')
        tip_pat = ext.get('tip_pattern', '')
        prob_order = ext.get('prob_order', ['home', 'draw', 'away'])

        blocks = re.findall(block_pat, html, re.DOTALL) if block_pat else []

        for block in blocks:
            teams = re.findall(team_pat, block)
            teams = [self._clean_html(t).strip() for t in teams if self._clean_html(t).strip()]
            if len(teams) < 2:
                continue

            pcts = re.findall(prob_pat, block)
            if len(pcts) >= 3:
                probs = {}
                for i, key in enumerate(prob_order):
                    if i < len(pcts):
                        probs[key] = float(pcts[i]) / 100.0
                h = probs.get('home', 0.33)
                d = probs.get('draw', 0.33)
                a = probs.get('away', 0.33)
                outcome = self._outcome_from_probs(h, d, a)
                confidence = max(h, d, a) * 100
            elif tip_pat:
                tip_match = re.search(tip_pat, block)
                if not tip_match:
                    continue
                outcome = self._outcome_from_tip(tip_match.group(1))
                if not outcome:
                    continue
                h, d, a = None, None, None
                confidence = None
            else:
                continue

            predictions.append({
                'home_team': teams[0], 'away_team': teams[1],
                'league_code': league_code if league_code[0] != '_' else None,
                'predicted_outcome': outcome,
                'home_win_prob': h, 'draw_prob': d, 'away_win_prob': a,
                'confidence': confidence,
                'raw_data': {'source': self._site.get('slug')},
            })

        return predictions

    def _extract_tip_table(self, html: str, ext: dict,
                           league_code: str) -> List[dict]:
        """
        Extract from table rows with tip values (1/X/2).
        Config keys: row_pattern, cell_pattern, home_cell, away_cell, tip_cell
        """
        predictions = []
        row_pat = ext.get('row_pattern', '')
        cell_pat = ext.get('cell_pattern', r'<td[^>]*>(.*?)</td>')
        home_idx = ext.get('home_cell', 0)
        away_idx = ext.get('away_cell', 2)
        tip_idx = ext.get('tip_cell', 4)
        min_cells = ext.get('min_cells', 5)

        rows = re.findall(row_pat, html, re.DOTALL) if row_pat else []

        for row in rows:
            cells = re.findall(cell_pat, row, re.DOTALL)
            if len(cells) < min_cells:
                continue

            home = self._clean_html(cells[home_idx])
            away = self._clean_html(cells[away_idx])
            tip = self._clean_html(cells[tip_idx])

            if not home or not away or not tip:
                continue

            outcome = self._outcome_from_tip(tip)
            if not outcome:
                continue

            predictions.append({
                'home_team': home, 'away_team': away,
                'league_code': league_code if league_code[0] != '_' else None,
                'predicted_outcome': outcome,
                'home_win_prob': None, 'draw_prob': None, 'away_win_prob': None,
                'confidence': None,
                'raw_data': {'tip': tip, 'source': self._site.get('slug')},
            })

        return predictions

    def _extract_tip_card(self, html: str, ext: dict,
                          league_code: str) -> List[dict]:
        """
        Extract from card/div blocks with team names and a tip.
        Config keys: block_pattern, team_pattern, tip_pattern
        """
        predictions = []
        block_pat = ext.get('block_pattern', '')
        team_pat = ext.get('team_pattern', '')
        tip_pat = ext.get('tip_pattern', '')

        blocks = re.findall(block_pat, html, re.DOTALL) if block_pat else []

        for block in blocks:
            teams = re.findall(team_pat, block)
            teams = [self._clean_html(t).strip() for t in teams if self._clean_html(t).strip()]
            if len(teams) < 2:
                continue

            tip_match = re.search(tip_pat, block)
            if not tip_match:
                continue

            tip = tip_match.group(1).strip()
            outcome = self._outcome_from_tip(tip)
            if not outcome:
                continue

            predictions.append({
                'home_team': teams[0], 'away_team': teams[1],
                'league_code': league_code if league_code[0] != '_' else None,
                'predicted_outcome': outcome,
                'home_win_prob': None, 'draw_prob': None, 'away_win_prob': None,
                'confidence': None,
                'raw_data': {'tip': tip, 'source': self._site.get('slug')},
            })

        return predictions

    # ── Article listing (e.g. Sports Mole) ──────────────────────────────

    def _scrape_article_listing(self, urls: dict, ext: dict,
                                target_date: date) -> List[dict]:
        """
        Scrape an article listing page, follow individual article links,
        and parse score-based predictions from each article.
        """
        predictions = []
        listing_url = urls.get('_listing', '')
        if not listing_url:
            return predictions

        slug = self._site.get('slug', '?')
        link_pat = ext.get('link_pattern', '')
        title_pat = ext.get('title_team_pattern', '')
        pred_pat = ext.get('prediction_pattern', '')
        score_pat = ext.get('score_pattern', r'(\d+)\s*[-–]\s*(\d+)')
        max_articles = ext.get('max_articles', 15)

        try:
            response = self._rate_limited_get(listing_url)
            if not response:
                return predictions

            article_links = re.findall(link_pat, response.text)[:max_articles]

            for link in article_links:
                pred = self._parse_article(
                    link, title_pat, pred_pat, score_pat
                )
                if pred:
                    predictions.append(pred)

        except Exception as e:
            logger.error(f"UniversalScraper[{slug}] article listing error: {e}")

        return predictions

    def _parse_article(self, url: str, title_pat: str,
                       pred_pat: str, score_pat: str) -> Optional[dict]:
        """Parse a single prediction article."""
        try:
            response = self._rate_limited_get(url)
            if not response:
                return None

            html = response.text
            title_match = re.search(title_pat, html, re.IGNORECASE)
            if not title_match:
                return None

            home = self._clean_html(title_match.group(1))
            away = self._clean_html(title_match.group(2))

            pred_match = re.search(pred_pat, html)
            if not pred_match:
                return None

            prediction_text = self._clean_html(pred_match.group(1))
            score = re.search(score_pat, prediction_text)
            if not score:
                return None

            h_goals, a_goals = int(score.group(1)), int(score.group(2))
            if h_goals > a_goals:
                outcome = 'home_win'
            elif a_goals > h_goals:
                outcome = 'away_win'
            else:
                outcome = 'draw'

            return {
                'home_team': home, 'away_team': away,
                'predicted_outcome': outcome,
                'home_win_prob': None, 'draw_prob': None, 'away_win_prob': None,
                'confidence': None,
                'raw_data': {
                    'url': url,
                    'prediction_text': prediction_text[:200],
                    'source': self._site.get('slug'),
                },
            }
        except Exception as e:
            logger.debug(f"Article parse error: {e}")
            return None


# ═══════════════════════════════════════════════════════════════════════
# Config Loader — used by the registry and quality manager
# ═══════════════════════════════════════════════════════════════════════

def load_sources_config() -> dict:
    """Load and parse sources_config.json."""
    try:
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Failed to load sources config: {e}")
        return {'sources': [], 'quality_settings': {}}


def save_sources_config(config: dict):
    """Atomically write updated config back to JSON file."""
    tmp_path = CONFIG_PATH + '.tmp'
    try:
        with open(tmp_path, 'w') as f:
            json.dump(config, f, indent=2, default=str)
        os.replace(tmp_path, CONFIG_PATH)
    except Exception as e:
        logger.error(f"Failed to save sources config: {e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def get_enabled_sources_sorted() -> List[dict]:
    """
    Get all enabled sources sorted by quality_weight (highest first).
    This is the primary entry point for the scraping orchestrator.
    """
    config = load_sources_config()
    sources = config.get('sources', [])
    enabled = [s for s in sources if s.get('enabled', False)]
    return sorted(enabled, key=lambda s: s.get('quality_weight', 0), reverse=True)
