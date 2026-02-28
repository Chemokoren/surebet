"""
Source Quality Manager.

Continuously evaluates, classifies, flags, and self-updates the
sources_config.json based on real prediction accuracy tracked in the DB.

Responsibilities:
  1. CLASSIFY  — assign quality_tier (high / mid / low) from accuracy
  2. FLAG      — mark underperforming sources after observation period
  3. DISABLE   — auto-disable sources flagged for too long
  4. REWEIGHT  — adjust quality_weight dynamically from accuracy
  5. DISCOVER  — search for and propose new high-quality sites
  6. SYNC      — keep JSON config and DB PredictionSource in sync

Called daily by Celery task `evaluate_sources_task`.
"""

import logging
from datetime import date, timedelta
from typing import Optional

from django.utils import timezone

logger = logging.getLogger(__name__)


class SourceQualityManager:
    """
    Self-managing quality engine that reads/writes sources_config.json
    and syncs with PredictionSource model data.
    """

    # ── 1. CLASSIFY ─────────────────────────────────────────────────────

    @classmethod
    def classify_all(cls) -> dict:
        """
        Reclassify every source's quality_tier based on its DB accuracy.
        Updates the JSON config and returns a summary.
        """
        from apps.predictions.scrapers.builtin import (
            load_sources_config, save_sources_config,
        )
        from apps.predictions.models_intelligence import PredictionSource

        config = load_sources_config()
        settings = config.get('quality_settings', {})
        high_thresh = settings.get('high_quality_threshold', 0.58)
        mid_thresh = settings.get('mid_quality_threshold', 0.45)
        min_preds = settings.get('min_predictions_to_classify', 20)

        changes = {'promoted': [], 'demoted': [], 'unchanged': []}

        for site in config.get('sources', []):
            slug = site.get('slug')
            try:
                source = PredictionSource.objects.get(slug=slug)
            except PredictionSource.DoesNotExist:
                continue

            if source.total_predictions < min_preds:
                changes['unchanged'].append(slug)
                continue

            old_tier = site.get('quality_tier', 'low')
            accuracy = source.recent_accuracy or source.overall_accuracy

            # Determine new tier
            if accuracy >= high_thresh:
                new_tier = 'high'
            elif accuracy >= mid_thresh:
                new_tier = 'mid'
            else:
                new_tier = 'low'

            site['quality_tier'] = new_tier

            # Compute quality_weight from accuracy (0.3 → 1.0 scale)
            site['quality_weight'] = round(
                max(0.30, min(1.0, accuracy * 1.4)), 2
            )

            if new_tier != old_tier:
                direction = 'promoted' if _tier_rank(new_tier) > _tier_rank(old_tier) else 'demoted'
                changes[direction].append(f"{slug}: {old_tier} → {new_tier}")
                logger.info(
                    f"Quality: {slug} {direction} {old_tier} → {new_tier} "
                    f"(accuracy={accuracy:.1%})"
                )
            else:
                changes['unchanged'].append(slug)

        # Re-sort sources by quality_weight (highest first)
        config['sources'] = sorted(
            config.get('sources', []),
            key=lambda s: s.get('quality_weight', 0),
            reverse=True,
        )

        # Update meta
        config['_meta']['last_quality_audit'] = str(date.today())
        config['_meta']['total_active'] = sum(
            1 for s in config['sources'] if s.get('enabled')
        )

        save_sources_config(config)
        return changes

    # ── 2. FLAG ─────────────────────────────────────────────────────────

    @classmethod
    def flag_underperformers(cls) -> list:
        """
        Flag sources whose accuracy has been below mid_quality_threshold
        for longer than flag_after_days_below_mid days.
        Returns list of newly flagged source slugs.
        """
        from apps.predictions.scrapers.builtin import (
            load_sources_config, save_sources_config,
        )
        from apps.predictions.models_intelligence import PredictionSource

        config = load_sources_config()
        settings = config.get('quality_settings', {})
        mid_thresh = settings.get('mid_quality_threshold', 0.45)
        flag_days = settings.get('flag_after_days_below_mid', 14)

        newly_flagged = []

        for site in config.get('sources', []):
            if site.get('flagged') or not site.get('enabled'):
                continue

            slug = site.get('slug')
            try:
                source = PredictionSource.objects.get(slug=slug)
            except PredictionSource.DoesNotExist:
                continue

            accuracy = source.recent_accuracy or source.overall_accuracy

            if accuracy < mid_thresh and source.total_predictions >= 20:
                # Check if observation period started
                obs_start = site.get('observation_start')
                if not obs_start:
                    site['observation_start'] = str(date.today())
                    continue

                # Check if enough days have passed
                obs_date = date.fromisoformat(obs_start)
                if (date.today() - obs_date).days >= flag_days:
                    site['flagged'] = True
                    site['flagged_reason'] = (
                        f"accuracy {accuracy:.1%} below {mid_thresh:.0%} "
                        f"for {flag_days}+ days"
                    )
                    site['flagged_date'] = str(date.today())
                    newly_flagged.append(slug)
                    logger.warning(
                        f"Quality: FLAGGED {slug} — {site['flagged_reason']}"
                    )
            else:
                # Reset observation if accuracy recovered
                site['observation_start'] = None

        if newly_flagged:
            save_sources_config(config)

        return newly_flagged

    # ── 3. DISABLE ──────────────────────────────────────────────────────

    @classmethod
    def disable_stale_flagged(cls) -> list:
        """
        Auto-disable sources that have been flagged for longer than
        auto_disable_after_days_flagged.
        """
        from apps.predictions.scrapers.builtin import (
            load_sources_config, save_sources_config,
        )
        from apps.predictions.models_intelligence import PredictionSource

        config = load_sources_config()
        settings = config.get('quality_settings', {})
        disable_days = settings.get('auto_disable_after_days_flagged', 30)

        disabled = []

        for site in config.get('sources', []):
            if not site.get('flagged') or not site.get('enabled'):
                continue

            flagged_date_str = site.get('flagged_date')
            if not flagged_date_str:
                continue

            flagged_date = date.fromisoformat(flagged_date_str)
            if (date.today() - flagged_date).days >= disable_days:
                site['enabled'] = False
                site['flagged_reason'] = (
                    f"auto-disabled after {disable_days} days flagged"
                )
                disabled.append(site['slug'])

                # Also retire in DB
                try:
                    source = PredictionSource.objects.get(slug=site['slug'])
                    source.status = 'retired'
                    source.scrape_enabled = False
                    source.save(update_fields=['status', 'scrape_enabled'])
                except PredictionSource.DoesNotExist:
                    pass

                logger.warning(f"Quality: DISABLED {site['slug']}")

        if disabled:
            config['_meta']['total_disabled'] = sum(
                1 for s in config['sources'] if not s.get('enabled')
            )
            save_sources_config(config)

        return disabled

    # ── 4. TRACK FAILURES ───────────────────────────────────────────────

    @classmethod
    def record_scrape_result(cls, slug: str, success: bool,
                             predictions_count: int = 0):
        """
        Called after each scrape attempt to track consecutive failures.
        Auto-flags after max_consecutive_failures.
        """
        from apps.predictions.scrapers.builtin import (
            load_sources_config, save_sources_config,
        )

        config = load_sources_config()
        settings = config.get('quality_settings', {})
        max_failures = settings.get('max_consecutive_failures', 7)

        for site in config.get('sources', []):
            if site.get('slug') != slug:
                continue

            if success:
                site['consecutive_failures'] = 0
                site['last_success'] = str(date.today())
            else:
                site['consecutive_failures'] = site.get('consecutive_failures', 0) + 1

                if site['consecutive_failures'] >= max_failures and not site.get('flagged'):
                    site['flagged'] = True
                    site['flagged_reason'] = (
                        f"{site['consecutive_failures']} consecutive scrape failures"
                    )
                    site['flagged_date'] = str(date.today())
                    logger.warning(
                        f"Quality: FLAGGED {slug} — "
                        f"{site['consecutive_failures']} failures"
                    )

            save_sources_config(config)
            return

    # ── 5. DISCOVER NEW SOURCES ─────────────────────────────────────────

    @classmethod
    def discover_candidates(cls) -> list:
        """
        Search for new high-quality prediction sites that could replace
        low-quality or disabled ones.

        Returns list of candidate site dicts (not yet added to config).
        Discovery is based on a curated seed list of potential sources.
        """
        CANDIDATE_POOL = [
            {
                'slug': 'windrawwin',
                'name': 'WinDrawWin',
                'website': 'https://www.windrawwin.com',
                'description': 'Comprehensive free football predictions',
                'urls': {
                    'PL': 'https://www.windrawwin.com/predictions/premier-league/',
                    'LL': 'https://www.windrawwin.com/predictions/la-liga/',
                },
                'extraction': {
                    'format': 'percentage_table',
                    'row_pattern': r'<tr[^>]*class="[^"]*pointed[^"]*"[^>]*>(.*?)</tr>',
                    'team_pattern': r'<a[^>]*>([^<]+)</a>',
                    'probability_pattern': r'(\d{1,3})%',
                    'min_teams': 2,
                    'min_probabilities': 3,
                    'prob_order': ['home', 'draw', 'away'],
                },
            },
            {
                'slug': 'zulubet',
                'name': 'ZuluBet',
                'website': 'https://www.zulubet.com',
                'description': 'Statistical predictions platform',
                'urls': {
                    '_all': 'https://www.zulubet.com/today-predictions/',
                },
                'extraction': {
                    'format': 'tip_table',
                    'row_pattern': r'<tr[^>]*>(.*?)</tr>',
                    'cell_pattern': r'<td[^>]*>(.*?)</td>',
                    'home_cell': 1,
                    'away_cell': 2,
                    'tip_cell': 5,
                    'min_cells': 6,
                },
            },
            {
                'slug': 'bettingclosed',
                'name': 'BettingClosed',
                'website': 'https://www.bettingclosed.com',
                'description': 'Mathematical predictions with confidence scores',
                'urls': {
                    '_all': 'https://www.bettingclosed.com/football-predictions/',
                },
                'extraction': {
                    'format': 'percentage_card',
                    'block_pattern': r'class="[^"]*prediction[^"]*"[^>]*>(.*?)</(?:div|article)',
                    'team_pattern': r'class="[^"]*team[^"]*"[^>]*>([^<]+)',
                    'probability_pattern': r'(\d{1,3})%',
                    'min_teams': 2,
                    'min_probabilities': 3,
                    'prob_order': ['home', 'draw', 'away'],
                },
            },
            {
                'slug': 'footballpredictions',
                'name': 'FootballPredictions.NET',
                'website': 'https://footballpredictions.net',
                'description': 'AI-powered football predictions',
                'urls': {
                    '_all': 'https://footballpredictions.net/predictions/',
                },
                'extraction': {
                    'format': 'tip_card',
                    'block_pattern': r'class="[^"]*match-card[^"]*"[^>]*>(.*?)</div>',
                    'team_pattern': r'class="[^"]*team[^"]*"[^>]*>([^<]+)',
                    'tip_pattern': r'class="[^"]*tip[^"]*"[^>]*>([^<]+)',
                    'min_teams': 2,
                },
            },
            {
                'slug': 'betstudy',
                'name': 'BetStudy',
                'website': 'https://www.betstudy.com',
                'description': 'Statistics-based football analysis and predictions',
                'urls': {
                    '_all': 'https://www.betstudy.com/predictions/',
                },
                'extraction': {
                    'format': 'percentage_table',
                    'row_pattern': r'<tr[^>]*>(.*?)</tr>',
                    'team_pattern': r'<td[^>]*>(.*?)</td>',
                    'probability_pattern': r'(\d{1,3})%',
                    'min_teams': 2,
                    'min_probabilities': 3,
                    'prob_order': ['home', 'draw', 'away'],
                },
            },
        ]

        from apps.predictions.scrapers.builtin import load_sources_config
        config = load_sources_config()
        existing_slugs = {s['slug'] for s in config.get('sources', [])}

        candidates = []
        for c in CANDIDATE_POOL:
            if c['slug'] not in existing_slugs:
                candidates.append(c)

        return candidates

    @classmethod
    def add_discovered_source(cls, candidate: dict) -> bool:
        """
        Add a new discovered source to the config in learning/observation mode.
        """
        from apps.predictions.scrapers.builtin import (
            load_sources_config, save_sources_config,
        )
        from apps.predictions.services.learning_engine import LearningEngine

        config = load_sources_config()
        settings = config.get('quality_settings', {})
        max_sources = settings.get('max_sources', 30)

        if len([s for s in config['sources'] if s['enabled']]) >= max_sources:
            logger.info("Quality: max_sources limit reached, not adding new source")
            return False

        new_site = {
            'slug': candidate['slug'],
            'name': candidate['name'],
            'website': candidate.get('website', ''),
            'description': candidate.get('description', ''),
            'enabled': True,
            'quality_tier': 'low',
            'quality_weight': 0.40,
            'requires_js': False,
            'rate_limit_seconds': 3.0,
            'consecutive_failures': 0,
            'last_success': None,
            'flagged': False,
            'flagged_reason': None,
            'flagged_date': None,
            'observation_start': str(date.today()),
            'urls': candidate.get('urls', {}),
            'extraction': candidate.get('extraction', {}),
        }

        config['sources'].append(new_site)
        save_sources_config(config)

        # Also register in DB
        LearningEngine.add_source(
            name=candidate['name'],
            slug=candidate['slug'],
            source_type='website',
            website_url=candidate.get('website', ''),
            priority=80,
        )

        logger.info(f"Quality: ADDED new source '{candidate['name']}' for observation")
        return True

    @classmethod
    def replace_low_with_candidates(cls) -> dict:
        """
        Find disabled/low-quality sources and try to replace them
        with discovered candidates.
        """
        from apps.predictions.scrapers.builtin import load_sources_config

        config = load_sources_config()
        disabled_count = sum(
            1 for s in config['sources']
            if not s.get('enabled') or s.get('quality_tier') == 'low'
        )

        if disabled_count == 0:
            return {'replaced': 0, 'candidates_added': []}

        candidates = cls.discover_candidates()
        added = []
        for c in candidates[:disabled_count]:
            if cls.add_discovered_source(c):
                added.append(c['slug'])

        return {'replaced': len(added), 'candidates_added': added}

    # ── 6. FULL AUDIT (called by Celery task) ───────────────────────────

    @classmethod
    def run_full_audit(cls) -> dict:
        """
        Run the complete quality management pipeline:
          1. Classify all sources by accuracy
          2. Flag underperformers
          3. Disable stale flagged sources
          4. Attempt to replace disabled with new candidates
        """
        results = {}
        results['classified'] = cls.classify_all()
        results['flagged'] = cls.flag_underperformers()
        results['disabled'] = cls.disable_stale_flagged()

        # Auto-discover replacements for disabled sources
        from apps.predictions.scrapers.builtin import load_sources_config
        config = load_sources_config()
        settings = config.get('quality_settings', {})
        if settings.get('auto_discover_enabled', True):
            results['discovery'] = cls.replace_low_with_candidates()

        logger.info(f"Quality audit complete: {results}")
        return results


# ── Helpers ─────────────────────────────────────────────────────────────

def _tier_rank(tier: str) -> int:
    """Numeric rank for tier comparison."""
    return {'high': 3, 'mid': 2, 'low': 1}.get(tier, 0)
