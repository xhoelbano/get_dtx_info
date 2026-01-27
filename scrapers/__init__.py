"""DTx scrapers package."""
from .base_scraper import BaseScraper
from .diga_scraper import DiGAScraper
from .app_store_scraper import AppStoreScraper
from .evidence_scraper import EvidenceScraper
from .usa_scraper import USAScraper

__all__ = ["BaseScraper", "DiGAScraper", "AppStoreScraper", "EvidenceScraper", "USAScraper"]
