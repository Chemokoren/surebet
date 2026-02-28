"""
Scraper Registry – JSON-config driven.

Instead of mapping slugs to individual scraper classes, this registry
loads sources_config.json and returns a UniversalScraper pre-configured
with the site's extraction rules.

Admin-added sources can still specify a custom `scraper_class` Python path
which bypasses the JSON config entirely.
"""

import importlib
import logging

logger = logging.getLogger(__name__)


class ScraperRegistry:
    """
    Central registry that resolves scraper instances from JSON config.

    Usage:
        scraper_cls, site_config = ScraperRegistry.get_scraper('predictz')
        scraper = scraper_cls(source=source_obj, config=site_config)
        predictions = scraper.scrape_predictions(target_date)
    """

    # Cache the config to avoid re-reading JSON on every call
    _config_cache = None

    @classmethod
    def _load_config(cls):
        """Load JSON config (cached per process)."""
        if cls._config_cache is None:
            from apps.predictions.scrapers.builtin import load_sources_config
            cls._config_cache = load_sources_config()
        return cls._config_cache

    @classmethod
    def invalidate_cache(cls):
        """Force reload of JSON config on next access."""
        cls._config_cache = None

    @classmethod
    def get_scraper(cls, slug: str, custom_class: str = ''):
        """
        Resolve a scraper class and its config by slug.

        Priority:
          1. custom_class Python path (for admin-added custom scrapers)
          2. JSON config → UniversalScraper with site-specific config

        Returns:
            (scraper_class, site_config_dict) or (None, None)
        """
        # 1. Custom class override
        if custom_class:
            scraper_cls = cls._load_class(custom_class)
            return scraper_cls

        # 2. JSON config → UniversalScraper
        config = cls._load_config()
        for site in config.get('sources', []):
            if site.get('slug') == slug:
                from apps.predictions.scrapers.builtin import UniversalScraper
                return UniversalScraper

        logger.warning(f"No scraper config found for slug='{slug}'")
        return None

    @classmethod
    def get_site_config(cls, slug: str) -> dict:
        """Get the JSON config dict for a specific source slug."""
        config = cls._load_config()
        for site in config.get('sources', []):
            if site.get('slug') == slug:
                return site
        return {}

    @classmethod
    def list_available(cls) -> dict:
        """List all configured source slugs and their quality tiers."""
        config = cls._load_config()
        return {
            site['slug']: {
                'name': site.get('name', ''),
                'quality_tier': site.get('quality_tier', 'unknown'),
                'quality_weight': site.get('quality_weight', 0),
                'enabled': site.get('enabled', False),
                'requires_js': site.get('requires_js', False),
            }
            for site in config.get('sources', [])
        }

    @classmethod
    def list_enabled_sorted(cls) -> list:
        """
        Get all enabled sources sorted by quality_weight (highest first).
        This is the primary entry point for the scraping orchestrator.
        """
        from apps.predictions.scrapers.builtin import get_enabled_sources_sorted
        return get_enabled_sources_sorted()

    @staticmethod
    def _load_class(class_path: str):
        """Dynamically load a Python class from dotted path."""
        try:
            module_path, class_name = class_path.rsplit('.', 1)
            module = importlib.import_module(module_path)
            return getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            logger.error(f"Failed to load scraper '{class_path}': {e}")
            return None
