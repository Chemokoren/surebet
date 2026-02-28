"""
Scraper framework for external prediction sources.

Base class and built-in scrapers for the top prediction websites.
"""

from .base import BaseScraper
from .registry import ScraperRegistry

__all__ = ['BaseScraper', 'ScraperRegistry']
