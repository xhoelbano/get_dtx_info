"""App Store and Play Store review scraper."""
import asyncio
import re
from typing import Dict, Optional

from .base_scraper import BaseScraper


class AppStoreScraper(BaseScraper):
    """Scraper for app store reviews (Google Play and Apple App Store)."""
    
    def __init__(self, config_path: str = "config/germany.json"):
        """Initialize the app store scraper.
        
        Args:
            config_path: Path to configuration file.
        """
        super().__init__(config_path)
    
    async def scrape(self, **kwargs) -> Dict:
        """Main scraping method - scrapes reviews for a single app.
        
        Args:
            **kwargs: Should include 'play_store_url' and/or 'app_store_url'.
            
        Returns:
            Dictionary with review counts.
        """
        result = {}
        
        if kwargs.get("play_store_url"):
            result["play_store"] = await self.scrape_play_store(kwargs["play_store_url"])
        
        if kwargs.get("app_store_url"):
            result["app_store"] = await self.scrape_app_store(kwargs["app_store_url"])
        
        return result
    
    async def scrape_play_store(self, url: str) -> Optional[Dict]:
        """Scrape review information from Google Play Store.
        
        Args:
            url: Google Play Store app URL.
            
        Returns:
            Dictionary with rating and review count, or None if failed.
        """
        if not url:
            return None
        
        browser = await self._create_browser()
        
        task = f"""
Go to {url} and extract the app review information.

Find and extract:
1. The average rating (e.g., "4.5")
2. The total number of reviews/ratings

Return the data as JSON:
{{
  "rating": 4.5,
  "review_count": 1234,
  "source": "Google Play Store"
}}

If you cannot find the information, return:
{{"rating": null, "review_count": null, "source": "Google Play Store", "error": "Could not extract data"}}

Return ONLY the JSON object.
"""
        
        agent = await self._create_agent(task, browser)
        
        try:
            history = await agent.run()
            result = self._extract_json_from_response(history)
            return result if result else None
        except Exception as e:
            return {"rating": None, "review_count": None, "source": "Google Play Store", "error": str(e)}
    
    async def scrape_app_store(self, url: str) -> Optional[Dict]:
        """Scrape review information from Apple App Store.
        
        Args:
            url: Apple App Store app URL.
            
        Returns:
            Dictionary with rating and review count, or None if failed.
        """
        if not url:
            return None
        
        browser = await self._create_browser()
        
        task = f"""
Go to {url} and extract the app review information.

Find and extract:
1. The average rating (e.g., "4.5")
2. The total number of ratings/reviews

Return the data as JSON:
{{
  "rating": 4.5,
  "review_count": 1234,
  "source": "Apple App Store"
}}

If you cannot find the information, return:
{{"rating": null, "review_count": null, "source": "Apple App Store", "error": "Could not extract data"}}

Return ONLY the JSON object.
"""
        
        agent = await self._create_agent(task, browser)
        
        try:
            history = await agent.run()
            result = self._extract_json_from_response(history)
            return result if result else None
        except Exception as e:
            return {"rating": None, "review_count": None, "source": "Apple App Store", "error": str(e)}
    
    def _extract_json_from_response(self, history) -> Optional[Dict]:
        """Extract JSON data from agent response.
        
        Args:
            history: Agent history containing the response.
            
        Returns:
            Parsed JSON data or None.
        """
        import json
        
        if hasattr(history, 'final_result'):
            text = str(history.final_result())
        elif hasattr(history, 'result'):
            text = str(history.result)
        else:
            text = str(history)
        
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        
        return None
