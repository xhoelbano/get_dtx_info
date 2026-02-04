"""DRKS (German Clinical Trials Register) evidence scraper using Playwright.

This module searches DRKS for clinical trials using web scraping since there
is no public API. Downloads official JSON files from the DRKS download page.
"""
import asyncio
import json
import re
from pathlib import Path
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
        self._context = None  # Reuse single context to prevent memory leaks
    
    async def _get_browser(self) -> Browser:
        """Get or create Playwright browser instance."""
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True
            )
        return self._browser
    
    async def _get_context(self):
        """Get or create a reusable browser context."""
        if self._context is None:
            browser = await self._get_browser()
            self._context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            )
        return self._context
    
    async def _create_page(self) -> Page:
        """Create a new browser page in the shared context."""
        context = await self._get_context()
        return await context.new_page()
    
    async def close(self):
        """Clean up resources."""
        await super().close()
        
        if self._context:
            try:
                await self._context.close()
            except:
                pass
            self._context = None
        
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
        page = None
        try:
            # Add overall timeout for the search operation
            return await asyncio.wait_for(
                self._search_impl(query, max_results),
                timeout=90  # 90 second timeout per search
            )
        except asyncio.TimeoutError:
            print(f"    DRKS search timeout for '{query[:30]}...'")
            return []
        except Exception as e:
            print(f"    DRKS search error: {e}")
            return []
    
    async def _search_impl(self, query: str, max_results: int) -> List[Dict]:
        """Internal search implementation with page management."""
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
            
        finally:
            try:
                await page.close()
            except:
                pass
    
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
    
    def _get_raw_folder(self, country: str, dtx_name: str, evidence_type: str) -> Path:
        """Get or create the raw JSON folder for DRKS downloads.
        
        Args:
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            evidence_type: "RCT" or "RWE"
            
        Returns:
            Path to the raw folder.
        """
        folder = self._get_dtx_folder(country, dtx_name, evidence_type) / "raw"
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    
    async def get_study_details(self, study_id: str) -> Optional[Dict]:
        """Get detailed information for a specific trial by DRKS ID.
        
        Downloads the official JSON from DRKS download page.
        
        Args:
            study_id: DRKS ID (e.g., "DRKS00012345").
            
        Returns:
            Dictionary with trial details or None.
        """
        try:
            # Add timeout for getting study details
            return await asyncio.wait_for(
                self._download_study_json(study_id),
                timeout=60  # 60 second timeout per study
            )
        except asyncio.TimeoutError:
            print(f"    DRKS download timeout for {study_id}")
            return None
        except Exception as e:
            print(f"    Error downloading DRKS study {study_id}: {e}")
            return None
    
    async def _download_study_json(self, drks_id: str) -> Optional[Dict]:
        """Download official JSON from DRKS download page.
        
        Args:
            drks_id: DRKS ID of the study.
            
        Returns:
            Dictionary with study details from official JSON, or None.
        """
        page = await self._create_page()
        
        try:
            # Navigate to download page
            download_url = f"{self.BASE_URL}/search/en/trial/{drks_id}/download"
            await page.goto(download_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)
            
            # Click JSON radio button - find by label text
            json_label = page.locator('text=JSON').first
            await json_label.click()
            await asyncio.sleep(0.5)
            
            # Accept terms checkbox - find by text "I accept"
            terms_checkbox = page.locator('input[type="checkbox"]').first
            if not await terms_checkbox.is_checked():
                await terms_checkbox.check()
                await asyncio.sleep(0.5)
            
            # Wait for download button to be enabled
            download_button = page.locator('button:has-text("Download")').first
            await download_button.wait_for(state="visible", timeout=5000)
            
            # Setup download handler and click download
            async with page.expect_download(timeout=30000) as download_info:
                await download_button.click()
            
            download = await download_info.value
            
            # Read the downloaded content
            download_path = await download.path()
            with open(download_path, "r", encoding="utf-8") as f:
                raw_json = json.load(f)
            
            # Parse the official JSON into our format
            return self._parse_drks_json(raw_json, drks_id)
            
        except Exception as e:
            print(f"    Error downloading DRKS JSON for {drks_id}: {e}")
            return None
        finally:
            try:
                await page.close()
            except:
                pass
    
    async def download_and_save_study_json(
        self, 
        drks_id: str, 
        country: str, 
        dtx_name: str, 
        evidence_type: str
    ) -> Optional[Dict]:
        """Download and save official JSON from DRKS, returning parsed data.
        
        Args:
            drks_id: DRKS ID of the study.
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            evidence_type: "RCT" or "RWE"
            
        Returns:
            Dictionary with study details, or None.
        """
        page = await self._create_page()
        
        try:
            # Get raw folder path
            raw_folder = self._get_raw_folder(country, dtx_name, evidence_type)
            save_path = raw_folder / f"{drks_id}.json"
            
            # Skip if already downloaded
            if save_path.exists():
                print(f"      {drks_id} already downloaded, loading from cache")
                with open(save_path, "r", encoding="utf-8") as f:
                    raw_json = json.load(f)
                return self._parse_drks_json(raw_json, drks_id)
            
            # Navigate to download page
            download_url = f"{self.BASE_URL}/search/en/trial/{drks_id}/download"
            await page.goto(download_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)
            
            # Click JSON radio button - find by label text
            json_label = page.locator('text=JSON').first
            await json_label.click()
            await asyncio.sleep(0.5)
            
            # Accept terms checkbox - find by text "I accept"
            terms_checkbox = page.locator('input[type="checkbox"]').first
            if not await terms_checkbox.is_checked():
                await terms_checkbox.check()
                await asyncio.sleep(0.5)
            
            # Wait for download button to be enabled
            download_button = page.locator('button:has-text("Download")').first
            await download_button.wait_for(state="visible", timeout=5000)
            
            # Setup download handler and click download
            async with page.expect_download(timeout=30000) as download_info:
                await download_button.click()
            
            download = await download_info.value
            
            # Save to our folder
            await download.save_as(save_path)
            
            # Read and parse
            with open(save_path, "r", encoding="utf-8") as f:
                raw_json = json.load(f)
            
            return self._parse_drks_json(raw_json, drks_id)
            
        except Exception as e:
            print(f"    Error downloading DRKS JSON for {drks_id}: {e}")
            return None
        finally:
            try:
                await page.close()
            except:
                pass
    
    def _parse_drks_json(self, raw_json: Dict, drks_id: str) -> Dict:
        """Parse the official DRKS JSON into our standardized format.
        
        DRKS JSON structure:
        - drksId: The DRKS ID
        - trialStatus: Status like "UPDATED", "COMPLETED"
        - trialDescriptions: Array of {title, summary, scientificSummary} by locale
        - studyCharacteristic: {studyType, allocation, phase, ...}
        - recruitment: {recruitmentStatus, targetSize, ...}
        - studiedHealthConditions: Array of conditions with ICD codes
        - trialContacts: Array of contacts/sponsors
        
        Args:
            raw_json: Raw JSON data from DRKS download.
            drks_id: DRKS ID of the study.
            
        Returns:
            Standardized study dictionary.
        """
        try:
            study = raw_json if isinstance(raw_json, dict) else {}
            
            # Extract title and summary from trialDescriptions
            # Prefer English (en) or fall back to German (de)
            title = ""
            brief_summary = ""
            trial_descs = study.get("trialDescriptions", [])
            for desc in trial_descs:
                locale_info = desc.get("idLocale", {})
                locale = locale_info.get("locale", "") if isinstance(locale_info, dict) else ""
                
                if locale == "en":
                    title = desc.get("title", "")
                    brief_summary = desc.get("summary", "") or desc.get("scientificSummary", "")
                    break
                elif locale == "de" and not title:
                    title = desc.get("title", "")
                    brief_summary = desc.get("summary", "") or desc.get("scientificSummary", "")
            
            # If still no title, try first description
            if not title and trial_descs:
                title = trial_descs[0].get("title", "")
                brief_summary = trial_descs[0].get("summary", "") or trial_descs[0].get("scientificSummary", "")
            
            # Extract status from trialStatus or recruitment
            status_raw = study.get("trialStatus", "")
            recruitment = study.get("recruitment", {})
            if recruitment:
                recruitment_status = recruitment.get("recruitmentStatus", "")
                if recruitment_status:
                    status_raw = recruitment_status
            status = self._normalize_status(status_raw)
            
            # Extract study characteristics
            characteristics = study.get("studyCharacteristic", {})
            study_type = characteristics.get("studyType", "")
            allocation = characteristics.get("allocation", "")
            phase = characteristics.get("phase", "")
            
            # Extract enrollment from recruitment
            enrollment = ""
            if recruitment:
                enrollment = str(recruitment.get("targetSize", "") or "")
            
            # Extract health conditions and ICD codes
            conditions = []
            icd_codes = []
            health_conditions = study.get("studiedHealthConditions", [])
            for cond in health_conditions:
                if isinstance(cond, dict):
                    icd = cond.get("icdCode", "")
                    if icd:
                        icd_codes.append(icd)
                    name = cond.get("healthCondition", "") or cond.get("name", "")
                    if name:
                        conditions.append(name)
            
            # Extract interventions from observationalGroups
            intervention_parts = []
            obs_groups = study.get("observationalGroups", [])
            for group in obs_groups:
                if isinstance(group, dict):
                    name = group.get("name", "") or group.get("intervention", "")
                    if name:
                        intervention_parts.append(name)
            intervention = "; ".join(intervention_parts) if intervention_parts else ""
            
            # Extract sponsor from trialContacts
            sponsor = ""
            contacts = study.get("trialContacts", [])
            for contact in contacts:
                if isinstance(contact, dict):
                    role = contact.get("role", "").lower()
                    if "sponsor" in role or "primary" in role:
                        sponsor = contact.get("name", "") or contact.get("organization", "")
                        break
            
            # Extract countries from recruitment
            countries = ["Germany"]  # Default for DRKS
            if recruitment:
                recruit_countries = recruitment.get("countries", [])
                if recruit_countries:
                    countries = [c.get("name", c) if isinstance(c, dict) else str(c) for c in recruit_countries]
            
            # Extract dates
            start_date = ""
            end_date = ""
            if recruitment:
                start_date = recruitment.get("startDate", "") or recruitment.get("firstEnrollmentDate", "")
                end_date = recruitment.get("endDate", "") or recruitment.get("estimatedEndDate", "")
            
            return {
                "study_id": drks_id,
                "drks_id": drks_id,
                "title": str(title)[:500],
                "status": status,
                "study_type": str(study_type),
                "allocation": str(allocation),
                "phase": str(phase),
                "enrollment": enrollment,
                "conditions": icd_codes if icd_codes else conditions,
                "health_condition": ", ".join(conditions[:5]) if conditions else "",
                "brief_summary": str(brief_summary)[:1000],
                "intervention": str(intervention)[:500],
                "sponsor": str(sponsor),
                "countries": countries,
                "start_date": str(start_date),
                "end_date": str(end_date),
                "source": "DRKS",
                "url": f"{self.BASE_URL}/search/en/trial/{drks_id}/details",
                "_raw_json_available": True
            }
            
        except Exception as e:
            print(f"    Error parsing DRKS JSON for {drks_id}: {e}")
            return {
                "study_id": drks_id,
                "drks_id": drks_id,
                "title": "",
                "status": "Unknown",
                "source": "DRKS",
                "url": f"{self.BASE_URL}/search/en/trial/{drks_id}/details",
                "_raw_json_available": False
            }
    
    def _normalize_status(self, status_raw: str) -> str:
        """Normalize recruitment status to standard values.
        
        Args:
            status_raw: Raw status string from DRKS.
            
        Returns:
            Normalized status string.
        """
        status_lower = str(status_raw).lower()
        
        if "complete" in status_lower or "closed" in status_lower:
            return "Completed"
        elif "ongoing" in status_lower or "recruiting" in status_lower:
            return "Recruiting"
        elif "suspended" in status_lower:
            return "Suspended"
        elif "not yet" in status_lower:
            return "Not yet recruiting"
        elif "terminated" in status_lower:
            return "Terminated"
        else:
            return status_raw or "Unknown"
    
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
        """Search DRKS, download official JSON, classify results, and save.
        
        Includes relevance filtering and downloads official JSON files.
        
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
        filtered_count = 0
        
        # Search with each query
        for query in queries:
            try:
                results = await self.search(query, max_results_per_query)
                
                # Deduplicate by DRKS ID
                for result in results:
                    drks_id = result.get("drks_id")
                    if drks_id and drks_id not in seen_ids:
                        seen_ids.add(drks_id)
                        
                        # Download official JSON for each result
                        print(f"      Downloading JSON for {drks_id}...")
                        details = await self.get_study_details(drks_id)
                        study_data = details if details else result
                        
                        # Check relevance before adding
                        if self.is_result_relevant(study_data, dtx_name):
                            # Track which query found this result
                            study_data["matched_query"] = query
                            all_results.append(study_data)
                        else:
                            filtered_count += 1
                        
                        await asyncio.sleep(1.5)  # Rate limiting
                
            except Exception as e:
                print(f"    Error searching DRKS for '{query[:50]}...': {e}")
        
        if filtered_count > 0:
            print(f"    Filtered {filtered_count} irrelevant trials")
        
        if not all_results:
            return {"rct": 0, "rwe": 0, "total": 0, "filtered": filtered_count}
        
        print(f"    Found {len(all_results)} relevant trials, classifying...")
        
        # Classify and organize results
        rct_results = []
        rwe_results = []
        
        for result in all_results:
            try:
                # Use preliminary check to guide LLM
                likely_rct = self.is_likely_rct(result)
                
                classification = await classifier.classify(result, hint_rct=likely_rct)
                result["classification"] = classification
                
                evidence_type = classification.get("classification", "RWE")
                
                if evidence_type == "RCT":
                    rct_results.append(result)
                    # Save official JSON to raw folder
                    await self._save_raw_json_if_needed(result, country, dtx_name, "RCT")
                else:
                    rwe_results.append(result)
                    await self._save_raw_json_if_needed(result, country, dtx_name, "RWE")
                    
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
            "total": len(all_results),
            "filtered": filtered_count
        }
    
    async def _save_raw_json_if_needed(
        self, 
        result: Dict, 
        country: str, 
        dtx_name: str, 
        evidence_type: str
    ):
        """Save the raw JSON for a study if not already saved.
        
        Uses download_and_save_study_json for studies that haven't been saved yet.
        """
        drks_id = result.get("drks_id")
        if not drks_id:
            return
        
        raw_folder = self._get_raw_folder(country, dtx_name, evidence_type)
        save_path = raw_folder / f"{drks_id}.json"
        
        # Only download if not already saved
        if not save_path.exists():
            try:
                await self.download_and_save_study_json(drks_id, country, dtx_name, evidence_type)
            except Exception as e:
                # Non-critical, just log
                pass
