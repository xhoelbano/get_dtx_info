"""ISRCTN evidence scraper using Playwright.

This module searches ISRCTN (International Standard Randomised Controlled Trial Number)
registry for clinical trials using web scraping since the API is no longer accessible.
"""
import asyncio
import json
import re
from typing import List, Dict, Optional
from urllib.parse import quote_plus, urlencode

from playwright.async_api import async_playwright, Browser, Page

from .base_evidence_scraper import BaseEvidenceScraper


class ISRCTNScraper(BaseEvidenceScraper):
    """Scraper for ISRCTN registry using Playwright.
    
    ISRCTN is a primary clinical trial registry recognized by the WHO and ICMJE.
    It covers trials from UK, EU, and internationally.
    
    The API is no longer publicly accessible, so we use Playwright for web scraping.
    """
    
    SOURCE_NAME = "isrctn"
    
    # ISRCTN URLs
    BASE_URL = "https://www.isrctn.com"
    SEARCH_URL = "https://www.isrctn.com/search"
    
    def __init__(self, evidence_dir: str = "evidence"):
        """Initialize the ISRCTN scraper.
        
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
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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
    
    async def _handle_cookie_consent(self, page: Page):
        """Handle cookie consent dialog if present."""
        try:
            # Check for cookie consent dialog
            accept_button = page.locator('button:has-text("Accept all cookies")')
            if await accept_button.count() > 0:
                await accept_button.click()
                await asyncio.sleep(1)
        except:
            pass  # No cookie dialog or already accepted
    
    async def search(self, query: str, max_results: int = 50) -> List[Dict]:
        """Search ISRCTN for trials matching the query.
        
        Args:
            query: Search query string.
            max_results: Maximum number of results to return.
            
        Returns:
            List of trial dictionaries with metadata.
        """
        try:
            # Add overall timeout for the search operation
            return await asyncio.wait_for(
                self._search_impl(query, max_results),
                timeout=90  # 90 second timeout per search
            )
        except asyncio.TimeoutError:
            print(f"    ISRCTN search timeout for '{query[:30]}...'")
            return []
        except Exception as e:
            print(f"    ISRCTN search error: {e}")
            return []
    
    async def _search_impl(self, query: str, max_results: int) -> List[Dict]:
        """Internal search implementation with page management.
        
        ISRCTN uses standard web search conventions where double quotes
        indicate exact phrase matching. The quote_plus function properly
        encodes quotes as %22, which ISRCTN interprets correctly.
        
        Example: "Cara Care" -> %22Cara%20Care%22
        """
        page = await self._create_page()
        
        try:
            # Navigate to search results directly with query parameter
            # quote_plus properly encodes double quotes for exact phrase matching
            search_url = f"{self.SEARCH_URL}?q={quote_plus(query)}"
            await page.goto(search_url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(2)
            
            # Handle cookie consent
            await self._handle_cookie_consent(page)
            await asyncio.sleep(1)
            
            # Wait for results to load
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
        
        # JavaScript to extract search results from ISRCTN
        extraction_js = """
        () => {
            const trials = [];
            
            // ISRCTN search results contain links to trial pages in format /ISRCTNxxxxxxxx
            const trialLinks = document.querySelectorAll('a[href*="/ISRCTN"]');
            
            for (const link of trialLinks) {
                const href = link.getAttribute('href') || '';
                
                // Extract ISRCTN ID from URL (format: ISRCTNxxxxxxxx)
                const isrctnMatch = href.match(/ISRCTN\\d{8}/);
                if (!isrctnMatch) continue;
                
                const isrctnId = isrctnMatch[0];
                
                // Skip if this is a navigation/header link
                if (link.closest('nav, header, footer')) continue;
                
                // Get title - usually the link text itself or nearby heading
                let title = link.textContent.trim();
                
                // If the title is just the ID, try to get more context
                if (title === isrctnId || !title) {
                    const article = link.closest('article, .result, [class*="result"]');
                    if (article) {
                        const heading = article.querySelector('h2, h3, h4, [class*="title"]');
                        if (heading) title = heading.textContent.trim();
                    }
                }
                
                // Get the parent container for status info
                const container = link.closest('article, .result, [class*="result"], tr, li');
                let status = 'Unknown';
                let sponsor = '';
                let condition = '';
                
                if (container) {
                    const containerText = container.textContent || '';
                    
                    // Extract status
                    if (containerText.includes('Completed')) status = 'Completed';
                    else if (containerText.includes('No longer recruiting')) status = 'No longer recruiting';
                    else if (containerText.includes('Recruiting')) status = 'Recruiting';
                    else if (containerText.includes('Suspended')) status = 'Suspended';
                    else if (containerText.includes('Stopped')) status = 'Stopped';
                    
                    // Try to get sponsor
                    const sponsorMatch = containerText.match(/Sponsor[:\\s]+([^\\n]+)/i);
                    if (sponsorMatch) sponsor = sponsorMatch[1].trim().slice(0, 200);
                    
                    // Try to get condition
                    const conditionMatch = containerText.match(/Condition[:\\s]+([^\\n]+)/i);
                    if (conditionMatch) condition = conditionMatch[1].trim().slice(0, 200);
                }
                
                trials.push({
                    isrctn_id: isrctnId,
                    title: title.slice(0, 500),
                    status: status,
                    sponsor: sponsor,
                    condition: condition,
                    url: 'https://www.isrctn.com/' + isrctnId
                });
            }
            
            // Remove duplicates
            const seen = new Set();
            return trials.filter(t => {
                if (seen.has(t.isrctn_id)) return false;
                seen.add(t.isrctn_id);
                return true;
            });
        }
        """
        
        try:
            raw_results = await page.evaluate(extraction_js)
            
            # Limit results
            for trial in raw_results[:max_results]:
                isrctn_id = trial.get("isrctn_id")
                if isrctn_id:
                    results.append({
                        "study_id": isrctn_id,
                        "isrctn_id": isrctn_id,
                        "title": trial.get("title", ""),
                        "status": trial.get("status", "Unknown"),
                        "sponsor": trial.get("sponsor", ""),
                        "condition": trial.get("condition", ""),
                        "source": "ISRCTN",
                        "url": trial.get("url") or f"{self.BASE_URL}/{isrctn_id}"
                    })
            
        except Exception as e:
            print(f"    Error extracting ISRCTN results: {e}")
        
        return results
    
    async def get_study_details(self, study_id: str) -> Optional[Dict]:
        """Get detailed information for a specific trial by ISRCTN ID.
        
        Args:
            study_id: ISRCTN ID (e.g., "ISRCTN12345678").
            
        Returns:
            Dictionary with trial details or None.
        """
        try:
            # Add timeout for getting study details
            return await asyncio.wait_for(
                self._get_study_details_impl(study_id),
                timeout=60  # 60 second timeout per study
            )
        except asyncio.TimeoutError:
            print(f"    ISRCTN study details timeout for {study_id}")
            return None
        except Exception as e:
            print(f"    Error fetching ISRCTN study {study_id}: {e}")
            return None
    
    async def _get_study_details_impl(self, study_id: str) -> Optional[Dict]:
        """Internal implementation for getting study details."""
        page = await self._create_page()
        
        try:
            # Navigate to study page
            study_url = f"{self.BASE_URL}/{study_id}"
            await page.goto(study_url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(2)
            
            # Handle cookie consent
            await self._handle_cookie_consent(page)
            await asyncio.sleep(1)
            
            # Extract study details
            details = await self._extract_study_details(page, study_id)
            
            return details
            
        finally:
            try:
                await page.close()
            except:
                pass
    
    async def _extract_study_details(self, page: Page, isrctn_id: str) -> Dict:
        """Extract detailed study information from study page.
        
        Args:
            page: Playwright page with study details.
            isrctn_id: ISRCTN ID of the study.
            
        Returns:
            Dictionary with study details.
        """
        # JavaScript to extract study details from ISRCTN detail page
        extraction_js = """
        () => {
            const data = {};
            const text = document.body.innerText;
            
            // Title - H1 contains the main study title
            const h1 = document.querySelector('h1');
            data.title = h1 ? h1.textContent.trim() : '';
            
            // Helper to get value from table rows
            const getTableValue = (headerText) => {
                const rows = document.querySelectorAll('tr, [role="row"]');
                for (const row of rows) {
                    const header = row.querySelector('th, [role="rowheader"]');
                    const cell = row.querySelector('td, [role="cell"]');
                    if (header && cell) {
                        const headerTextContent = header.textContent.trim().toLowerCase();
                        if (headerTextContent.includes(headerText.toLowerCase())) {
                            return cell.textContent.trim();
                        }
                    }
                }
                return '';
            };
            
            // Helper to get definition from term
            const getDefinition = (termText) => {
                const terms = document.querySelectorAll('dt, [class*="term"]');
                for (const term of terms) {
                    if (term.textContent.toLowerCase().includes(termText.toLowerCase())) {
                        const def = term.nextElementSibling;
                        if (def && (def.tagName === 'DD' || def.classList.contains('definition'))) {
                            return def.textContent.trim();
                        }
                    }
                }
                return '';
            };
            
            // ISRCTN ID
            data.isrctn_id = getTableValue('ISRCTN') || '';
            
            // DOI
            data.doi = getTableValue('DOI') || '';
            
            // Sponsor
            data.sponsor = getTableValue('Sponsor') || '';
            
            // Funder
            data.funder = getTableValue('Funder') || '';
            
            // Dates
            data.submission_date = getDefinition('Submission date') || '';
            data.registration_date = getDefinition('Registration date') || '';
            data.last_edited = getDefinition('Last edited') || '';
            
            // Status
            data.recruitment_status = getDefinition('Recruitment status') || '';
            data.overall_status = getDefinition('Overall study status') || '';
            
            // Condition category
            data.condition_category = getDefinition('Condition category') || '';
            
            // Study design info
            data.primary_study_design = getTableValue('Primary study design') || '';
            data.study_design = getTableValue('Study design') || '';
            data.secondary_study_design = getTableValue('Secondary study design') || '';
            data.scientific_title = getTableValue('Scientific title') || '';
            data.study_hypothesis = getTableValue('Study hypothesis') || getTableValue('Study objectives') || '';
            
            // Ethics
            data.ethics_approval = getTableValue('Ethics approval') || '';
            
            // Conditions
            data.health_conditions = getTableValue('Health condition') || '';
            
            // Intervention
            data.intervention = getTableValue('Intervention') || '';
            data.intervention_type = getTableValue('Intervention type') || '';
            
            // Outcomes
            data.primary_outcome = getTableValue('Primary outcome') || '';
            data.secondary_outcomes = getTableValue('secondary outcome') || '';
            
            // Completion date
            data.completion_date = getTableValue('Completion date') || '';
            
            // Eligibility
            data.participant_type = getTableValue('Participant type') || '';
            data.age_group = getTableValue('Age group') || '';
            data.lower_age_limit = getTableValue('Lower age limit') || '';
            data.upper_age_limit = getTableValue('Upper age limit') || '';
            data.sex = getTableValue('Sex') || '';
            data.target_sample_size = getTableValue('Target sample size') || '';
            data.total_enrollment = getTableValue('Total final enrolment') || getTableValue('final enrolment') || '';
            data.inclusion_criteria = getTableValue('inclusion criteria') || '';
            data.exclusion_criteria = getTableValue('exclusion criteria') || '';
            data.first_enrollment = getTableValue('first enrolment') || '';
            data.last_enrollment = getTableValue('final enrolment') || '';
            
            // Countries
            data.countries = [];
            const countriesSection = text.match(/Countries of recruitment[\\s\\S]*?((?:[A-Z][a-z]+ ?)+)/i);
            if (countriesSection) {
                const countryText = countriesSection[1] || '';
                if (countryText.includes('United Kingdom')) data.countries.push('United Kingdom');
                if (countryText.includes('Germany')) data.countries.push('Germany');
                if (countryText.includes('United States')) data.countries.push('United States');
                if (countryText.includes('France')) data.countries.push('France');
                if (countryText.includes('Spain')) data.countries.push('Spain');
                if (countryText.includes('Italy')) data.countries.push('Italy');
            }
            // Fallback: check whole text
            if (data.countries.length === 0) {
                if (text.includes('United Kingdom')) data.countries.push('United Kingdom');
                if (text.includes('UK')) data.countries.push('United Kingdom');
            }
            
            // Contact info - find email anywhere on page
            const emailMatch = text.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}/);
            data.contact_email = emailMatch ? emailMatch[0] : '';
            
            // Results/Publications
            data.has_results = text.includes('Results Added') || text.includes('Results article');
            data.publication_links = [];
            const pubmedLinks = document.querySelectorAll('a[href*="pubmed.ncbi.nlm.nih.gov"]');
            pubmedLinks.forEach(link => {
                data.publication_links.push(link.href);
            });
            
            // Brief summary - look in paragraph after "Plain English summary"
            const allH2s = document.querySelectorAll('h2');
            for (const h2 of allH2s) {
                if (h2.textContent.includes('Plain English summary')) {
                    // Get the next sibling element or parent's next content
                    let next = h2.nextElementSibling;
                    while (next && next.tagName !== 'P' && next.tagName !== 'DIV') {
                        next = next.nextElementSibling;
                    }
                    if (next) {
                        data.brief_summary = next.textContent.trim().slice(0, 2000);
                    }
                    break;
                }
            }
            
            // Fallback: try to find summary in the main text
            if (!data.brief_summary) {
                const summaryMatch = text.match(/Background and study aims([\\s\\S]*?)(?:Who can participate|Where is the study|$)/i);
                if (summaryMatch) {
                    data.brief_summary = summaryMatch[1].trim().slice(0, 2000);
                }
            }
            
            return data;
        }
        """
        
        try:
            raw_data = await page.evaluate(extraction_js)
            
            # Determine study type for RCT classification hints
            study_design = (
                (raw_data.get("primary_study_design", "") + " " +
                 raw_data.get("study_design", "") + " " +
                 raw_data.get("secondary_study_design", "")).lower()
            )
            
            return {
                "study_id": isrctn_id,
                "isrctn_id": raw_data.get("isrctn_id") or isrctn_id,
                "doi": raw_data.get("doi", ""),
                "title": raw_data.get("title", ""),
                "scientific_title": raw_data.get("scientific_title", ""),
                "sponsor": raw_data.get("sponsor", ""),
                "funder": raw_data.get("funder", ""),
                "recruitment_status": raw_data.get("recruitment_status", ""),
                "overall_status": raw_data.get("overall_status", ""),
                "condition_category": raw_data.get("condition_category", ""),
                "primary_study_design": raw_data.get("primary_study_design", ""),
                "study_design": raw_data.get("study_design", ""),
                "secondary_study_design": raw_data.get("secondary_study_design", ""),
                "study_hypothesis": raw_data.get("study_hypothesis", "")[:500] if raw_data.get("study_hypothesis") else "",
                "ethics_approval": raw_data.get("ethics_approval", ""),
                "health_conditions": raw_data.get("health_conditions", ""),
                "intervention": raw_data.get("intervention", "")[:1000] if raw_data.get("intervention") else "",
                "intervention_type": raw_data.get("intervention_type", ""),
                "primary_outcome": raw_data.get("primary_outcome", "")[:1000] if raw_data.get("primary_outcome") else "",
                "secondary_outcomes": raw_data.get("secondary_outcomes", "")[:1000] if raw_data.get("secondary_outcomes") else "",
                "participant_info": {
                    "type": raw_data.get("participant_type", ""),
                    "age_group": raw_data.get("age_group", ""),
                    "lower_age": raw_data.get("lower_age_limit", ""),
                    "upper_age": raw_data.get("upper_age_limit", ""),
                    "sex": raw_data.get("sex", "")
                },
                "target_enrollment": raw_data.get("target_sample_size", ""),
                "total_enrollment": raw_data.get("total_enrollment", ""),
                "inclusion_criteria": raw_data.get("inclusion_criteria", "")[:500] if raw_data.get("inclusion_criteria") else "",
                "exclusion_criteria": raw_data.get("exclusion_criteria", "")[:500] if raw_data.get("exclusion_criteria") else "",
                "countries": raw_data.get("countries", []),
                "submission_date": raw_data.get("submission_date", ""),
                "registration_date": raw_data.get("registration_date", ""),
                "completion_date": raw_data.get("completion_date", ""),
                "first_enrollment": raw_data.get("first_enrollment", ""),
                "last_enrollment": raw_data.get("last_enrollment", ""),
                "brief_summary": raw_data.get("brief_summary", ""),
                "has_results": raw_data.get("has_results", False),
                "publication_links": raw_data.get("publication_links", []),
                "contact_email": raw_data.get("contact_email", ""),
                "source": "ISRCTN",
                "url": f"{self.BASE_URL}/{isrctn_id}"
            }
            
        except Exception as e:
            print(f"    Error extracting ISRCTN study details: {e}")
            return {
                "study_id": isrctn_id,
                "isrctn_id": isrctn_id,
                "title": "",
                "status": "Unknown",
                "source": "ISRCTN",
                "url": f"{self.BASE_URL}/{isrctn_id}"
            }
    
    def is_likely_rct(self, trial: Dict) -> bool:
        """Quick check if a trial is likely an RCT based on design info.
        
        ISRCTN is primarily for RCTs, but includes other studies too.
        
        Args:
            trial: Trial dictionary.
            
        Returns:
            True if trial appears to be an RCT.
        """
        # Check study design fields
        design_text = " ".join([
            trial.get("primary_study_design", ""),
            trial.get("study_design", ""),
            trial.get("secondary_study_design", ""),
            trial.get("title", "")
        ]).lower()
        
        # Strong RCT indicators
        if "randomised" in design_text or "randomized" in design_text:
            return True
        if "rct" in design_text:
            return True
        if "controlled trial" in design_text:
            return True
        
        # Check if interventional
        if "interventional" in design_text:
            return True
        
        # Check for observational indicators
        if "observational" in design_text:
            return False
        if "cohort" in design_text:
            return False
        if "registry" in design_text and "trial" not in design_text:
            return False
        if "case control" in design_text or "case-control" in design_text:
            return False
        
        # ISRCTN is primarily RCT registry, default to RCT
        return True
    
    async def search_and_save_candidates(
        self,
        queries: List[str],
        country: str,
        dtx_name: str,
        max_results_per_query: int = 50
    ) -> Dict[str, int]:
        """Search ISRCTN and save ALL results as candidates (Layer 1).
        
        No classification or relevance filtering - just collect raw data.
        Fetches detailed info for each study.
        
        Args:
            queries: List of search query strings.
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            max_results_per_query: Max results per query
            
        Returns:
            Dictionary with counts: {"total": N, "queries": [...]}
        """
        all_results = []
        seen_ids = set()
        
        # Get raw folder for candidates
        raw_folder = self._get_candidates_raw_folder(country, dtx_name)
        
        # Search with each query
        for query in queries:
            try:
                results = await self.search(query, max_results_per_query)
                
                # Deduplicate by ISRCTN ID only (no filtering)
                for result in results:
                    isrctn_id = result.get("isrctn_id")
                    if isrctn_id and isrctn_id not in seen_ids:
                        seen_ids.add(isrctn_id)
                        result["_matched_query"] = query
                        
                        # Get detailed info for each result
                        print(f"      Fetching details for {isrctn_id}...")
                        details = await self.get_study_details(isrctn_id)
                        study_data = details if details else result
                        study_data["_matched_query"] = query
                        
                        # Save raw JSON for this study
                        raw_path = self._save_raw_json_to_candidates(
                            study_data, isrctn_id, raw_folder
                        )
                        if raw_path:
                            study_data["_raw_json_path"] = str(raw_path)
                        
                        all_results.append(study_data)
                        await asyncio.sleep(1.5)  # Rate limiting
                
                await asyncio.sleep(1)  # Rate limiting between queries
                
            except Exception as e:
                print(f"    Error searching ISRCTN for '{query[:50]}...': {e}")
        
        # Save all candidates
        if all_results:
            self.save_candidates_metadata(country, dtx_name, {
                "studies": all_results,
                "count": len(all_results),
                "queries_used": queries,
                "dtx_name": dtx_name,
                "country": country
            }, "studies.json")
        
        return {
            "total": len(all_results),
            "queries": queries
        }
    
    def _save_raw_json_to_candidates(
        self, 
        study_data: Dict, 
        isrctn_id: str,
        raw_folder
    ):
        """Save raw JSON for a single study to candidates folder.
        
        Args:
            study_data: Study data to save.
            isrctn_id: ISRCTN study ID.
            raw_folder: Path to candidates raw folder.
            
        Returns:
            Path to the saved JSON file, or None if failed.
        """
        import json
        from pathlib import Path
        
        try:
            save_path = Path(raw_folder) / f"{isrctn_id}.json"
            
            # Skip if already downloaded
            if save_path.exists():
                return save_path
            
            # Save raw JSON
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(study_data, f, indent=2, ensure_ascii=False)
            
            return save_path
            
        except Exception as e:
            print(f"        Warning: Failed to save raw JSON for {isrctn_id}: {e}")
            return None
    
    async def search_and_save(
        self,
        queries: List[str],
        country: str,
        dtx_name: str,
        classifier,
        max_results_per_query: int = 50
    ) -> Dict[str, int]:
        """Search ISRCTN, classify results, and save.
        
        DEPRECATED: Use search_and_save_candidates for Layer 1.
        
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
                
                # Deduplicate by ISRCTN ID and check relevance
                for result in results:
                    isrctn_id = result.get("isrctn_id")
                    if isrctn_id and isrctn_id not in seen_ids:
                        seen_ids.add(isrctn_id)
                        
                        # Get detailed info for each result
                        print(f"      Fetching details for {isrctn_id}...")
                        details = await self.get_study_details(isrctn_id)
                        study_data = details if details else result
                        
                        # Check relevance before adding
                        if self.is_result_relevant(study_data, dtx_name):
                            # Track which query found this result
                            study_data["matched_query"] = query
                            all_results.append(study_data)
                        else:
                            filtered_count += 1
                        
                        await asyncio.sleep(1.5)  # Rate limiting - be respectful
                
                await asyncio.sleep(1)  # Rate limiting between queries
                
            except Exception as e:
                print(f"    Error searching ISRCTN for '{query[:50]}...': {e}")
        
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
                
                if classification.get("classification") == "RCT":
                    rct_results.append(result)
                else:
                    rwe_results.append(result)
                    
            except Exception as e:
                # Use preliminary check as fallback
                if self.is_likely_rct(result):
                    result["classification"] = {
                        "classification": "RCT",
                        "confidence": 60,
                        "reason": f"ISRCTN registry (primarily RCTs). Error: {e}"
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
