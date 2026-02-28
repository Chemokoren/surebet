"""
Scraper Registry – maps source slugs to scraper classes.

Admin-added sources can specify a custom `scraper_class` Python path.
Built-in scrapers are registered here by slug.
"""

import importlib
import logging

logger = logging.getLogger(__name__)


class ScraperRegistry:
    """
    Central registry of available scrapers.

    Usage:
        scraper_cls = ScraperRegistry.get_scraper('predictz')
        scraper = scraper_cls(source=source_obj, config=source_obj.scrape_config)
        predictions = scraper.scrape_predictions(target_date)
    """

    # Built-in scrapers (slug → module.ClassName)
    _BUILTIN = {
        'sports-mole':       'apps.predictions.scrapers.builtin.SportsMoleScraper',
        'whoscored':         'apps.predictions.scrapers.builtin.WhoScoredScraper',
        'predictz':          'apps.predictions.scrapers.builtin.PredictZScraper',
        'football-whispers': 'apps.predictions.scrapers.builtin.FootballWhispersScraper',
        'footystats':        'apps.predictions.scrapers.builtin.FootyStatsScraper',
        'understat':         'apps.predictions.scrapers.builtin.UnderstatScraper',
        'dimers':            'apps.predictions.scrapers.builtin.DimersScraper',
        'betensured':        'apps.predictions.scrapers.builtin.BetensuredScraper',
        'forebet':           'apps.predictions.scrapers.builtin.ForebetScraper',
        'vitibet':           'apps.predictions.scrapers.builtin.VitibetScraper',
    }

    @classmethod
    def get_scraper(cls, slug: str, custom_class: str = ''):
        """
        Resolve a scraper class by slug or custom Python path.

        Args:
            slug: Source slug (looked up in built-in registry)
            custom_class: Full Python path, e.g. 'apps.predictions.scrapers.builtin.PredictZScraper'

        Returns:
            Scraper class (not instantiated), or None
        """
        path = custom_class or cls._BUILTIN.get(slug)
        if not path:
            logger.warning(f"No scraper found for slug='{slug}', custom_class='{custom_class}'")
            return None

        try:
            module_path, class_name = path.rsplit('.', 1)
            module = importlib.import_module(module_path)
            return getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            logger.error(f"Failed to load scraper '{path}': {e}")
            return None

    @classmethod
    def list_available(cls) -> dict:
        """List all built-in scraper slugs and their classes."""
        return dict(cls._BUILTIN)
