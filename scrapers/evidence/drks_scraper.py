"""DRKS (German Clinical Trials Register) evidence scraper using Playwright.

This module searches DRKS for clinical trials using web scraping since there
is no public API. It extracts study data and downloads JSON when available.
"""
import asyncio
import json
import re
from typing import List, Dict, Optional
from urllib.parse import quote_plus, urljoin

from playwright.async_api import async_playwright, Browser, Page

from .base_evidence_scraper import BaseEvidenceScraper


class DRKSScraper(BaseEvidenceScraper):
    """Scraper for DRKS (Deutsches Register Klinischer Studien).
    
    DRKS is the German Clinical Trials Register, a primary registry recognized
    by WHO. Since there's no public API, we use Playwright for web scraping.
    """
    
    SOURCE_NAME = "drks"
    
    # DRKS URLs
    BASE_URL = "https://drks.de"
    SEARCH_URL = "https://drks.de/search/en"
    
    def __init__(self, evidence_dir: str = "evidence"):
        """Initialize the DRKS scraper.
        
        Args:
            evidence_dir: Root directory for storing evidence files.
        """
        super().__init__(evidence_dir)
        self._playwright = None
        self._browser: Optional[Browser] = None
    
    async def _get_browser(self) -> Browser:
        """Get or create Playwright browser instance."""
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True
            )
        return self._browser
    
    async def _create_page(self) -> Page:
        """Create a new browser page."""
        browser = await self._get_browser()
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        return await context.new_page()
    
    async def close(self):
        """Clean up resources."""
        await super().close()
        
        if self._browser:
            try:
                await self._browser.close()
            except:
                pass
            self._browser = None
        
        if self._playwright:
            try:
                await self._playwright.stop()
            except:
                pass
            self._playwright = None
    
    async def search(self, query: str, max_results: int = 50) -> List[Dict]:
        """Search DRKS for trials matching the query.
        
        DRKS requires form submission - URL parameters don't work.
        
        Args:
            query: Search query string.
            max_results: Maximum number of results to return.
            
        Returns:
            List of trial dictionaries with metadata.
        """
        page = await self._create_page()
        
        try:
            # Navigate to search page
            await page.goto(self.SEARCH_URL, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)
            
            # Fill the search input (first text input on the page)
            search_input = page.locator("input[type='text']").first
            await search_input.fill(query)
            await asyncio.sleep(0.5)
            
            # Click the Search button (exact match to avoid Reset Search button)
            search_button = page.get_by_role("button", name="Search", exact=True)
            await search_button.click()
            
            # Wait for results to load
            await asyncio.sleep(3)
            await page.wait_for_load_state("networkidle")
            
            # Extract search results
            results = await self._extract_search_results(page, max_results)
            
            return results
            
        except Exception as e:
            print(f"    DRKS search error: {e}")
            return []
        finally:
            await page.close()
    
    async def _extract_search_results(self, page: Page, max_results: int) -> List[Dict]:
        """Extract trial information from search results page.
        
        Args:
            page: Playwright page with search results.
            max_results: Maximum number of results to extract.
            
        Returns:
            List of trial dictionaries.
        """
        results = []
        
        # JavaScript to extract search results from DRKS grid
        extraction_js = """
        () => {
            const trials = [];
            
            // DRKS uses div[role="gridcell"] for results
            // Find all links to trial details pages
            const trialLinks = document.querySelectorAll('a[href*="/trial/DRKS"]');
            
            for (const link of trialLinks) {
                const href = link.href || '';
                
                // Extract DRKS ID from URL
                const drksMatch = href.match(/DRKS\\d{8}/);
                if (!drksMatch) continue;
                
                const drksId = drksMatch[0];
                
                // Get title from link text
                const title = link.textContent.trim();
                
                // Get the parent row to find status
                const row = link.closest('[role="row"], tr, .result-row');
                let status = 'Unknown';
                if (row) {
                    const rowText = row.textContent || '';
                    if (rowText.includes('Recruiting complete') || rowText.includes('complete')) status = 'Completed';
                    else if (rowText.includes('Recruiting ongoing') || rowText.includes('ongoing')) status = 'Recruiting';
                    else if (rowText.includes('Suspended')) status = 'Suspended';
                }
                
                trials.push({
                    drks_id: drksId,
                    title: title.slice(0, 500),
                    status: status,
                    url: href
                });
            }
            
            return trials;
        }
        """
        
        try:
            raw_results = await page.evaluate(extraction_js)
            
            # Deduplicate and limit results
            seen_ids = set()
            for trial in raw_results:
                drks_id = trial.get("drks_id")
                if drks_id and drks_id not in seen_ids and len(results) < max_results:
                    seen_ids.add(drks_id)
                    
                    # Format the result
                    results.append({
                        "study_id": drks_id,
                        "drks_id": drks_id,
                        "title": trial.get("title", ""),
                        "status": trial.get("status", "Unknown"),
                        "source": "DRKS",
                        "url": trial.get("url") or f"{self.BASE_URL}/search/en/trial/{drks_id}/details"
                    })
            
        except Exception as e:
            print(f"    Error extracting DRKS results: {e}")
        
        return results
    
    async def get_study_details(self, study_id: str) -> Optional[Dict]:
        """Get detailed information for a specific trial by DRKS ID.
        
        Args:
            study_id: DRKS ID (e.g., "DRKS00012345").
            
        Returns:
            Dictionary with trial details or None.
        """
        page = await self._create_page()
        
        try:
            # Navigate to study page - DRKS uses /trial/ID/details format
            study_url = f"{self.BASE_URL}/search/en/trial/{study_id}/details"
            await page.goto(study_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)
            
            # Extract study details
            details = await self._extract_study_details(page, study_id)
            
            return details
            
        except Exception as e:
            print(f"    Error fetching DRKS study {study_id}: {e}")
            return None
        finally:
            await page.close()
    
    async def _extract_study_details(self, page: Page, drks_id: str) -> Dict:
        """Extract detailed study information from study page.
        
        Args:
            page: Playwright page with study details.
            drks_id: DRKS ID of the study.
            
        Returns:
            Dictionary with study details.
        """
        # JavaScript to extract study details from DRKS detail page
        extraction_js = """
        () => {
            const data = {};
            const text = document.body.innerText;
            
            // Title - H2 contains the actual study title
            const h2 = document.querySelector('h2');
            data.title = h2 ? h2.textContent.trim() : '';
            
            // Helper to get text after a label
            const getValueAfterLabel = (labelText) => {
                const allText = document.body.innerText;
                const regex = new RegExp(labelText + '\\s*\\n\\s*([^\\n]+)', 'i');
                const match = allText.match(regex);
                return match ? match[1].trim() : '';
            };
            
            // Status
            if (text.includes('Recruiting complete') || text.includes('study complete')) data.status = 'Completed';
            else if (text.includes('Recruiting ongoing')) data.status = 'Recruiting';
            else if (text.includes('Recruiting suspended')) data.status = 'Suspended';
            else if (text.includes('Not yet recruiting')) data.status = 'Not yet recruiting';
            else data.status = 'Unknown';
            
            // Study type
            data.study_type = getValueAfterLabel('Study type');
            
            // Purpose
            data.primary_purpose = getValueAfterLabel('Purpose');
            
            // Allocation (usually shown for interventional studies)
            data.allocation = getValueAfterLabel('Allocation');
            
            // Phase
            data.phase = getValueAfterLabel('Phase');
            
            // Enrollment/Target size
            data.enrollment = getValueAfterLabel('Target sample size') || getValueAfterLabel('Sample size');
            
            // ICD codes
            const icdMatches = text.match(/[A-Z]\\d{2}(?:\\.\\d{1,2})?/g) || [];
            data.icd_codes = [...new Set(icdMatches)].filter(c => 
                c.startsWith('F') || c.startsWith('C') || c.startsWith('E') || 
                c.startsWith('I') || c.startsWith('J') || c.startsWith('K') ||
                c.startsWith('M') || c.startsWith('G')
            ).slice(0, 10);
            
            // Brief summary
            const summaryMatch = text.match(/Brief summary in (?:lay|scientific) language\\s*\\n\\s*([\\s\\S]*?)(?:Brief summary|Health condition|Interventions|$)/i);
            data.brief_summary = summaryMatch ? summaryMatch[1].trim().slice(0, 1000) : '';
            
            // Health condition
            const conditionMatch = text.match(/Health condition or problem studied\\s*\\n([\\s\\S]*?)(?:Interventions|$)/i);
            data.health_condition = conditionMatch ? conditionMatch[1].trim().slice(0, 300) : '';
            
            // Intervention
            const interventionMatch = text.match(/Interventions,\\s*Observational Groups\\s*\\n([\\s\\S]*?)(?:Primary outcome|Outcomes|$)/i);
            data.intervention = interventionMatch ? interventionMatch[1].trim().slice(0, 500) : '';
            
            // Primary outcome
            data.primary_outcome = getValueAfterLabel('Primary outcome');
            
            // Secondary outcomes
            data.secondary_outcomes = getValueAfterLabel('Secondary outcome');
            
            // Primary sponsor
            data.sponsor = getValueAfterLabel('Primary sponsor');
            
            // Countries
            data.countries = [];
            if (text.includes('Germany')) data.countries.push('Germany');
            if (text.includes('Austria')) data.countries.push('Austria');
            if (text.includes('Switzerland')) data.countries.push('Switzerland');
            
            // Dates
            data.start_date = getValueAfterLabel('Date of first enrollment');
            data.end_date = getValueAfterLabel('Estimated date of last enrollment');
            
            // Registration type (Retrospective/Prospective)
            data.registration_type = getValueAfterLabel('Retrospective/prospective');
            
            return data;
        }
        """
        
        try:
            raw_data = await page.evaluate(extraction_js)
            
            return {
                "study_id": drks_id,
                "drks_id": drks_id,
                "title": raw_data.get("title", ""),
                "status": raw_data.get("status", "Unknown"),
                "study_type": raw_data.get("study_type", ""),
                "primary_purpose": raw_data.get("primary_purpose", ""),
                "allocation": raw_data.get("allocation", ""),
                "phase": raw_data.get("phase", ""),
                "enrollment": raw_data.get("enrollment", ""),
                "conditions": raw_data.get("icd_codes", []),
                "health_condition": raw_data.get("health_condition", ""),
                "brief_summary": raw_data.get("brief_summary", ""),
                "intervention": raw_data.get("intervention", "")[:500] if raw_data.get("intervention") else "",
                "primary_outcome": raw_data.get("primary_outcome", "")[:500] if raw_data.get("primary_outcome") else "",
                "secondary_outcomes": raw_data.get("secondary_outcomes", "")[:500] if raw_data.get("secondary_outcomes") else "",
                "sponsor": raw_data.get("sponsor", ""),
                "countries": raw_data.get("countries", ["Germany"]),
                "start_date": raw_data.get("start_date", ""),
                "end_date": raw_data.get("end_date", ""),
                "registration_type": raw_data.get("registration_type", ""),
                "source": "DRKS",
                "url": f"{self.BASE_URL}/search/en/trial/{drks_id}/details"
            }
            
        except Exception as e:
            print(f"    Error extracting DRKS study details: {e}")
            return {
                "study_id": drks_id,
                "drks_id": drks_id,
                "title": "",
                "status": "Unknown",
                "source": "DRKS",
                "url": f"{self.BASE_URL}/search/en/trial/{drks_id}/details"
            }
    
    def is_likely_rct(self, trial: Dict) -> bool:
        """Quick check if a trial is likely an RCT based on design info.
        
        Args:
            trial: Trial dictionary.
            
        Returns:
            True if trial appears to be an RCT.
        """
        # Check allocation
        allocation = trial.get("allocation", "").lower()
        if "randomized" in allocation or "randomised" in allocation:
            return True
        
        # Check study type
        study_type = trial.get("study_type", "").lower()
        if "interventional" in study_type:
            return True
        if "observational" in study_type:
            return False
        
        # Check title for clues
        title = trial.get("title", "").lower()
        if "randomized" in title or "randomised" in title:
            return True
        if "rct" in title:
            return True
        if "observational" in title or "registry" in title:
            return False
        
        # Default to RCT for DRKS (primarily clinical trials)
        return True
    
    async def search_and_save(
        self,
        queries: List[str],
        country: str,
        dtx_name: str,
        classifier,
        max_results_per_query: int = 50
    ) -> Dict[str, int]:
        """Search DRKS, classify results, and save.
        
        Args:
            queries: List of search query strings.
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            classifier: LLM classifier for RCT/RWE determination
            max_results_per_query: Max results per query
            
        Returns:
            Dictionary with counts.
        """
        all_results = []
        seen_ids = set()
        
        # Search with each query
        for query in queries:
            try:
                results = await self.search(query, max_results_per_query)
                
                # Deduplicate by DRKS ID
                for result in results:
                    drks_id = result.get("drks_id")
                    if drks_id and drks_id not in seen_ids:
                        seen_ids.add(drks_id)
                        
                        # Get detailed info for each result
                        details = await self.get_study_details(drks_id)
                        if details:
                            all_results.append(details)
                        else:
                            all_results.append(result)
                        
                        await asyncio.sleep(1)  # Rate limiting
                
            except Exception as e:
                print(f"    Error searching DRKS for '{query[:50]}...': {e}")
        
        if not all_results:
            return {"rct": 0, "rwe": 0, "total": 0}
        
        print(f"    Found {len(all_results)} unique trials, classifying...")
        
        # Classify and organize results
        rct_results = []
        rwe_results = []
        
        for result in all_results:
            try:
                # Use preliminary check to guide LLM
                likely_rct = self.is_likely_rct(result)
                
                classification = await classifier.classify(result, hint_rct=likely_rct)
                result["classification"] = classification
                
                if classification.get("classification") == "RCT":
                    rct_results.append(result)
                else:
                    rwe_results.append(result)
                    
            except Exception as e:
                # Use preliminary check as fallback
                if self.is_likely_rct(result):
                    result["classification"] = {
                        "classification": "RCT",
                        "confidence": 50,
                        "reason": f"Design suggests RCT. Error: {e}"
                    }
                    rct_results.append(result)
                else:
                    result["classification"] = {
                        "classification": "RWE",
                        "confidence": 50,
                        "reason": f"Design suggests observational. Error: {e}"
                    }
                    rwe_results.append(result)
        
        # Save results
        if rct_results:
            self.save_metadata(country, dtx_name, "RCT", {
                "studies": rct_results,
                "count": len(rct_results),
                "queries_used": queries
            }, "studies.json")
            print(f"    Saved {len(rct_results)} RCT trials")
        
        if rwe_results:
            self.save_metadata(country, dtx_name, "RWE", {
                "studies": rwe_results,
                "count": len(rwe_results),
                "queries_used": queries
            }, "studies.json")
            print(f"    Saved {len(rwe_results)} RWE trials")
        
        return {
            "rct": len(rct_results),
            "rwe": len(rwe_results),
            "total": len(all_results)
        }
