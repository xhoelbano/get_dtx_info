"""Base scraper class with Playwright and browser-use setup.

This module provides a hybrid approach:
- Playwright for reliable, deterministic scraping of structured data (lists, tables)
- browser-use for complex AI-powered interactions (clicking dynamic buttons, handling popups)
"""
import os
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Playwright for direct browser control
from playwright.async_api import async_playwright, Browser as PlaywrightBrowser, Page

# browser-use for AI-powered interactions
from browser_use import Agent, Browser

# Import the correct Azure OpenAI class
try:
    from browser_use import ChatAzureOpenAI
except ImportError:
    from langchain_openai import AzureChatOpenAI as ChatAzureOpenAI


class BaseScraper(ABC):
    """Base class for all scrapers with hybrid Playwright + browser-use setup.
    
    Uses Playwright for reliable data extraction and browser-use for AI assistance.
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
        
        # browser-use resources
        self.browser = None
        self.llm = None
        self._setup_llm()
        
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
    
    def _setup_llm(self):
        """Setup the Azure OpenAI LLM."""
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
        self.llm = ChatAzureOpenAI(
            model=deployment,
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
        )
    
    # ========== Playwright Methods (for reliable data extraction) ==========
    
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
    
    # ========== browser-use Methods (for AI-powered interactions) ==========
    
    async def _create_browser(self) -> Browser:
        """Create and return a browser-use instance for AI interactions."""
        if self.browser is None:
            self.browser = Browser()
        return self.browser
    
    async def _create_agent(self, task: str, browser: Browser = None) -> Agent:
        """Create a browser-use agent with the given task.
        
        Args:
            task: The task description for the agent.
            browser: Optional browser instance. If not provided, creates a new one.
            
        Returns:
            Agent instance configured with the task.
        """
        if browser is None:
            browser = await self._create_browser()
        
        return Agent(
            task=task,
            llm=self.llm,
            browser=browser,
        )
    
    # ========== Cleanup ==========
    
    async def close(self):
        """Close all browser instances and clean up resources."""
        # Close Playwright
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
        
        # Close browser-use
        if self.browser:
            try:
                if hasattr(self.browser, 'close'):
                    await self.browser.close()
                elif hasattr(self.browser, 'stop'):
                    await self.browser.stop()
            except Exception:
                pass
            self.browser = None
    
    @abstractmethod
    async def scrape(self, **kwargs):
        """Main scraping method to be implemented by subclasses."""
        pass
