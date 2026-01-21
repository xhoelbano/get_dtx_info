"""Base scraper class with Playwright for web scraping.

This module provides Playwright-based browser automation for reliable,
reproducible scraping of structured data from websites.
"""
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Playwright for direct browser control
from playwright.async_api import async_playwright, Browser as PlaywrightBrowser, Page


class BaseScraper(ABC):
    """Base class for all scrapers using Playwright.
    
    Uses Playwright for reliable, deterministic data extraction.
    """
    
    def __init__(self, config_path: str = None):
        """Initialize the scraper with configuration.
        
        Args:
            config_path: Path to the country configuration JSON file.
        """
        load_dotenv()
        
        self.config = {}
        if config_path:
            self.config = self._load_config(config_path)
        
        # Playwright resources
        self._playwright = None
        self._playwright_browser: Optional[PlaywrightBrowser] = None
    
    def _load_config(self, config_path: str) -> dict:
        """Load configuration from JSON file."""
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    
    async def _get_playwright_browser(self) -> PlaywrightBrowser:
        """Get or create a Playwright browser instance.
        
        Returns:
            Playwright browser instance.
        """
        if self._playwright_browser is None:
            self._playwright = await async_playwright().start()
            self._playwright_browser = await self._playwright.chromium.launch(
                headless=True,  # Set to False for debugging
            )
        return self._playwright_browser
    
    async def _create_page(self) -> Page:
        """Create a new Playwright page.
        
        Returns:
            New Playwright page instance.
        """
        browser = await self._get_playwright_browser()
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        return await context.new_page()
    
    async def close(self):
        """Close browser and clean up resources."""
        if self._playwright_browser:
            try:
                await self._playwright_browser.close()
            except Exception:
                pass
            self._playwright_browser = None
        
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
    
    @abstractmethod
    async def scrape(self, **kwargs):
        """Main scraping method to be implemented by subclasses."""
        pass
