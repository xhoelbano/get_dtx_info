"""App Store and Play Store review scraper using Playwright.

Scrapes ratings and review counts from Google Play Store and Apple App Store
for DTx apps that have store URLs in the database.
"""
import asyncio
import re
from typing import Dict, List, Optional

from .base_scraper import BaseScraper


class AppStoreScraper(BaseScraper):
    """Scraper for app store reviews (Google Play and Apple App Store)."""
    
    # JavaScript for extracting Play Store data
    PLAY_STORE_JS = """
    () => {
        const data = {
            rating: null,
            review_count: null,
            source: "Google Play Store"
        };
        
        const bodyText = document.body.innerText;
        
        // Method 1: Look for rating in specific elements (most reliable)
        // Play Store shows rating prominently near the app title
        const ratingElements = document.querySelectorAll('[class*="rating"], [class*="score"], [itemprop="ratingValue"]');
        for (const el of ratingElements) {
            const text = el.textContent.trim();
            const match = text.match(/^([0-9],[0-9]|[0-9]\\.[0-9])$/);
            if (match) {
                data.rating = parseFloat(match[1].replace(',', '.'));
                break;
            }
        }
        
        // Method 2: Look for pattern in body text - German uses comma as decimal
        if (!data.rating) {
            // Pattern: standalone "4,5" or "4.5" near beginning (the main rating)
            const ratingMatch = bodyText.match(/^[\\s\\S]{0,500}?([0-9],[0-9]|[0-9]\\.[0-9])(?:\\s|$)/m);
            if (ratingMatch) {
                data.rating = parseFloat(ratingMatch[1].replace(',', '.'));
            }
        }
        
        // Method 3: Look for aria-label with rating
        if (!data.rating) {
            const ariaElements = document.querySelectorAll('[aria-label]');
            for (const el of ariaElements) {
                const label = el.getAttribute('aria-label') || '';
                const match = label.match(/([0-9],[0-9]|[0-9]\\.[0-9])\\s*(?:star|Stern|von\\s*5)/i);
                if (match) {
                    data.rating = parseFloat(match[1].replace(',', '.'));
                    break;
                }
            }
        }
        
        // Review count: look for pattern with "Rezensionen" or "reviews"
        const reviewMatch = bodyText.match(/([0-9.,]+)\\s*(?:Rezensionen|reviews|Bewertungen)/i);
        if (reviewMatch) {
            let count = reviewMatch[1].replace(/\\./g, '').replace(/,/g, '');
            data.review_count = parseInt(count);
        }
        
        return data;
    }
    """
    
    # JavaScript for extracting App Store data
    APP_STORE_JS = """
    () => {
        const data = {
            rating: null,
            review_count: null,
            source: "Apple App Store"
        };
        
        const bodyText = document.body.innerText;
        
        // Method 1: Look for rating figure element (App Store specific)
        const figcaption = document.querySelector('figcaption.we-rating-count');
        if (figcaption) {
            const text = figcaption.textContent;
            const match = text.match(/([0-9],[0-9]|[0-9]\\.[0-9])/);
            if (match) {
                data.rating = parseFloat(match[1].replace(',', '.'));
            }
        }
        
        // Method 2: Look for rating in aria-label
        if (!data.rating) {
            const ratingLink = document.querySelector('a[href*="see-all/user-reviews"]');
            if (ratingLink) {
                const label = ratingLink.getAttribute('aria-label') || ratingLink.textContent;
                const match = label.match(/([0-9],[0-9]|[0-9]\\.[0-9])/);
                if (match) {
                    data.rating = parseFloat(match[1].replace(',', '.'));
                }
            }
        }
        
        // Method 3: Pattern in body text - "X,X von 5" or "X.X out of 5"
        if (!data.rating) {
            const ratingMatch = bodyText.match(/([0-9],[0-9]|[0-9]\\.[0-9])\\s*(?:von|out of)\\s*5/i);
            if (ratingMatch) {
                data.rating = parseFloat(ratingMatch[1].replace(',', '.'));
            }
        }
        
        // Method 4: Look for standalone rating number
        if (!data.rating) {
            const ratingMatch = bodyText.match(/(?:Bewertung|Rating)[:\\s]*([0-9],[0-9]|[0-9]\\.[0-9])/i);
            if (ratingMatch) {
                data.rating = parseFloat(ratingMatch[1].replace(',', '.'));
            }
        }
        
        // Review count: look for "X Bewertungen" or "X Ratings"
        const reviewMatch = bodyText.match(/([0-9.,]+)\\s*(?:Bewertungen|Ratings|Reviews)/i);
        if (reviewMatch) {
            let count = reviewMatch[1].replace(/\\./g, '').replace(/,/g, '');
            data.review_count = parseInt(count);
        }
        
        return data;
    }
    """
    
    def __init__(self, config_path: str = "config/germany.json"):
        """Initialize the app store scraper."""
        super().__init__(config_path)
    
    async def scrape(self, dtx_list: List[Dict] = None, **kwargs) -> List[Dict]:
        """Scrape store ratings for all DTx entries that have store URLs.
        
        Args:
            dtx_list: List of DTx entries with play_store_url and/or app_store_url.
            
        Returns:
            Updated DTx list with store ratings populated.
        """
        if not dtx_list:
            return []
        
        updated_list = []
        total = len(dtx_list)
        
        for i, dtx in enumerate(dtx_list, 1):
            dtx_name = dtx.get('dtx_name', 'Unknown')
            play_url = dtx.get('play_store_url')
            app_url = dtx.get('app_store_url')
            
            # Skip if no store URLs
            if not play_url and not app_url:
                updated_list.append(dtx)
                continue
            
            print(f"  [{i}/{total}] Fetching ratings for: {dtx_name[:50]}...")
            
            # Scrape Play Store
            if play_url:
                play_data = await self.scrape_play_store(play_url)
                if play_data:
                    dtx['reviews_playstore'] = {
                        'rating': play_data.get('rating'),
                        'review_count': play_data.get('review_count'),
                        'url': play_url
                    }
            
            # Scrape App Store
            if app_url:
                app_data = await self.scrape_app_store(app_url)
                if app_data:
                    dtx['reviews_appstore'] = {
                        'rating': app_data.get('rating'),
                        'review_count': app_data.get('review_count'),
                        'url': app_url
                    }
            
            updated_list.append(dtx)
            
            # Small delay between requests
            await asyncio.sleep(0.5)
        
        return updated_list
    
    async def scrape_play_store(self, url: str) -> Optional[Dict]:
        """Scrape review information from Google Play Store using Playwright.
        
        Args:
            url: Google Play Store app URL.
            
        Returns:
            Dictionary with rating and review count, or None if failed.
        """
        if not url:
            return None
        
        page = await self._create_page()
        
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(2)  # Allow dynamic content to load
            
            # Extract data using JavaScript
            data = await page.evaluate(self.PLAY_STORE_JS)
            return data
            
        except Exception as e:
            print(f"      Error scraping Play Store: {e}")
            return {"rating": None, "review_count": None, "source": "Google Play Store", "error": str(e)}
        finally:
            await page.close()
    
    async def scrape_app_store(self, url: str) -> Optional[Dict]:
        """Scrape review information from Apple App Store using Playwright.
        
        Args:
            url: Apple App Store app URL.
            
        Returns:
            Dictionary with rating and review count, or None if failed.
        """
        if not url:
            return None
        
        page = await self._create_page()
        
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(2)  # Allow dynamic content to load
            
            # Extract data using JavaScript
            data = await page.evaluate(self.APP_STORE_JS)
            return data
            
        except Exception as e:
            print(f"      Error scraping App Store: {e}")
            return {"rating": None, "review_count": None, "source": "Apple App Store", "error": str(e)}
        finally:
            await page.close()
