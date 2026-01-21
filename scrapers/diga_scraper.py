"""DiGA (German Digital Health Applications) directory scraper.

This module uses a hybrid approach:
- Playwright for reliable list scraping (all DTx entries with basic info)
- browser-use for detail page scraping (AI-powered button clicking, dynamic content)
"""
import asyncio
import json
import re
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path

from .base_scraper import BaseScraper
from browser_use import Agent, Browser
from utils.translator import Translator


class DiGAScraper(BaseScraper):
    """Scraper for the German DiGA directory (diga.bfarm.de).
    
    Uses Playwright for the main list (reliable, complete data) and
    browser-use for detail pages (AI assistance for dynamic content).
    """
    
    # Fields that should be translated from German to English
    FIELDS_TO_TRANSLATE = ["description", "reason_for_delisting"]
    
    # JavaScript code for extracting DiGA entries from the directory page
    EXTRACTION_JS = """
    () => {
        const entries = [];
        const infoLinks = document.querySelectorAll('a[href*="/de/verzeichnis/"]');
        
        for (const link of infoLinks) {
            const href = link.getAttribute('href');
            if (!href || !href.match(/\\/de\\/verzeichnis\\/\\d+$/)) continue;
            
            // Find the card container with h1
            let card = link.parentElement;
            while (card && !card.querySelector('h1')) {
                card = card.parentElement;
            }
            if (!card) continue;
            
            // Extract name from h1
            const heading = card.querySelector('h1');
            const name = heading ? heading.textContent.trim() : 'Unknown';
            
            // Find status and company
            const infoDiv = heading?.parentElement;
            let status = '';
            let company = '';
            
            if (infoDiv) {
                const text = infoDiv.textContent;
                if (text.includes('Dauerhaft aufgenommen')) status = 'Dauerhaft aufgenommen';
                else if (text.includes('Vorläufig aufgenommen')) status = 'Vorläufig aufgenommen';
                else if (text.includes('Gestrichen')) status = 'Gestrichen';
                
                const parts = text.split('|');
                if (parts.length > 1) {
                    company = parts[parts.length - 1].trim();
                }
            }
            
            entries.push({
                dtx_name: name,
                company_provider: company || 'Unknown',
                listing_status_de: status || 'Gestrichen',
                source_url: 'https://diga.bfarm.de' + href
            });
        }
        
        // Deduplicate by URL
        const seen = new Set();
        return entries.filter(e => {
            if (seen.has(e.source_url)) return false;
            seen.add(e.source_url);
            return true;
        });
    }
    """
    
    def __init__(self, config_path: str = "config/germany.json"):
        """Initialize the DiGA scraper.
        
        Args:
            config_path: Path to Germany configuration file.
        """
        super().__init__(config_path)
        self.base_url = self.config.get("dtx_directory_url", "https://diga.bfarm.de/de/verzeichnis")
        self.status_filters = self.config.get("status_filters", {})
        self.category_translations = self.config.get("category_translations", {})
        self.status_translations = self.config.get("status_translations", {})
        self.translator = None
    
    async def scrape(
        self, 
        mode: str = "full", 
        translate: bool = True,
        existing_data: Dict = None,
        skip_details: bool = False,
        **kwargs
    ) -> Dict:
        """Main scraping method.
        
        Args:
            mode: "full" for complete refresh, "incremental" for updates only.
            translate: Whether to translate German text to English.
            existing_data: Existing DTx data for incremental comparison.
            skip_details: If True, only scrape the list without detail pages.
            **kwargs: Additional arguments.
            
        Returns:
            Dictionary containing all scraped DTx data.
        """
        print(f"Starting DiGA scrape in {mode} mode...")
        
        # Initialize translator if needed
        if translate:
            self.translator = Translator(source_lang="de", target_lang="en")
        
        # Step 1: Get list of all DTx with basic info
        dtx_list = await self.scrape_dtx_list()
        print(f"Found {len(dtx_list)} DTx entries")
        
        # If skip_details, return just the list
        if skip_details:
            print("Skipping detail scraping (--skip-details flag)")
            return {
                "metadata": {
                    "country": self.config.get("country", "Germany"),
                    "source": "DiGA-Verzeichnis (BfArM)"
                },
                "dtx_list": dtx_list
            }
        
        # For incremental mode, filter to only new/changed DTx
        if mode == "incremental" and existing_data:
            dtx_list = self._filter_updated_dtx(dtx_list, existing_data)
            print(f"Incremental mode: {len(dtx_list)} DTx need updating")
        
        # Step 2: For each DTx, get detailed information
        detailed_dtx_list = []
        for i, dtx in enumerate(dtx_list):
            print(f"Scraping details for {i+1}/{len(dtx_list)}: {dtx['dtx_name']}")
            try:
                detailed_dtx = await self.scrape_dtx_details(dtx)
                
                # Step 3: Translate fields if enabled
                if translate:
                    detailed_dtx = await self._translate_dtx_fields(detailed_dtx)
                
                detailed_dtx_list.append(detailed_dtx)
            except Exception as e:
                print(f"Error scraping {dtx['dtx_name']}: {e}")
                # Still add the basic info
                detailed_dtx_list.append(dtx)
            
            # Small delay between requests to be respectful
            await asyncio.sleep(1)
        
        return {
            "metadata": {
                "country": self.config.get("country", "Germany"),
                "source": "DiGA-Verzeichnis (BfArM)"
            },
            "dtx_list": detailed_dtx_list
        }
    
    def _filter_updated_dtx(self, dtx_list: List[Dict], existing_data: Dict) -> List[Dict]:
        """Filter DTx list to only include new or updated entries.
        
        Args:
            dtx_list: List of DTx from current scrape.
            existing_data: Existing DTx data from previous scrape.
            
        Returns:
            Filtered list of DTx that need updating.
        """
        existing_urls = {
            dtx.get("source_url"): dtx 
            for dtx in existing_data.get("dtx_list", [])
        }
        
        filtered = []
        for dtx in dtx_list:
            url = dtx.get("source_url")
            
            # Include if:
            # 1. It's a new DTx (URL not in existing data)
            # 2. The status has changed
            # 3. It hasn't been scraped in detail yet
            if url not in existing_urls:
                print(f"  New DTx: {dtx.get('dtx_name')}")
                filtered.append(dtx)
            else:
                existing_dtx = existing_urls[url]
                
                # Check if status changed
                if dtx.get("listing_status_de") != existing_dtx.get("listing_status_de"):
                    print(f"  Status changed: {dtx.get('dtx_name')}")
                    filtered.append(dtx)
                # Check if detailed info is missing
                elif not existing_dtx.get("date_of_first_listing"):
                    print(f"  Missing details: {dtx.get('dtx_name')}")
                    filtered.append(dtx)
        
        return filtered
    
    async def _translate_dtx_fields(self, dtx: Dict) -> Dict:
        """Translate specific fields from German to English.
        
        Args:
            dtx: Dictionary containing DTx data.
            
        Returns:
            DTx dictionary with translated fields.
        """
        if not self.translator:
            return dtx
        
        # Get ICD-10 codes to preserve during translation
        preserve_terms = dtx.get("clinical_area_icd10", [])
        preserve_terms.extend(dtx.get("trial_registration_ids", []))
        
        # Translate specified fields
        for field in self.FIELDS_TO_TRANSLATE:
            if field in dtx and dtx[field]:
                try:
                    dtx[field] = await self.translator.translate(
                        dtx[field], 
                        preserve_terms=preserve_terms
                    )
                except Exception as e:
                    print(f"Translation error for {field}: {e}")
        
        # Translate DTx name (create English version)
        if dtx.get("dtx_name_de") and not dtx.get("dtx_name"):
            try:
                dtx["dtx_name"] = await self.translator.translate(
                    dtx["dtx_name_de"],
                    preserve_terms=preserve_terms
                )
            except Exception as e:
                dtx["dtx_name"] = dtx["dtx_name_de"]
        
        # Use mapping for category translation
        if dtx.get("dtx_category"):
            dtx["dtx_category"] = self.category_translations.get(
                dtx["dtx_category"],
                dtx["dtx_category"]
            )
        
        return dtx
    
    async def scrape_dtx_list(self) -> List[Dict]:
        """Scrape the list of all DTx from the directory using Playwright.
        
        Uses direct DOM manipulation for reliable, complete data extraction.
        This method:
        1. Opens the DiGA directory page
        2. Applies "All" filter to include delisted entries
        3. Scrolls to load all entries (lazy loading)
        4. Extracts data directly from the DOM using JavaScript
        
        Returns:
            List of dictionaries with basic DTx info (name, URL, status).
        """
        print("Scraping DTx list using Playwright (direct DOM extraction)...")
        
        page = await self._create_page()
        
        try:
            # Navigate to the directory with filter to include ALL entries (including delisted)
            # The type=[] parameter shows all status types
            url_with_all_filter = f"{self.base_url}?type=%5B%5D"
            print(f"  Navigating to {url_with_all_filter}")
            await page.goto(url_with_all_filter, wait_until="domcontentloaded", timeout=60000)
            
            # Wait for the page content to load
            await page.wait_for_load_state("networkidle", timeout=30000)
            await asyncio.sleep(3)  # Allow dynamic content to render
            
            # Check initial count
            count_text = await page.evaluate("""
                () => {
                    const match = document.body.innerText.match(/(\\d+) von (\\d+) DiGA/);
                    return match ? { displayed: parseInt(match[1]), total: parseInt(match[2]) } : null;
                }
            """)
            if count_text:
                print(f"  Initial state: {count_text['displayed']} of {count_text['total']} DiGA visible")
            
            # Scroll to load all entries (lazy loading)
            print("  Scrolling to load all entries...")
            previous_count = 0
            for scroll_attempt in range(20):  # Max 20 scroll attempts
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(0.5)
                
                # Check if all entries are loaded
                current_count = await page.evaluate("""
                    () => document.querySelectorAll('a[href*="/de/verzeichnis/"]').length
                """)
                
                if current_count == previous_count:
                    # No new entries loaded, probably done
                    break
                previous_count = current_count
            
            # Final count check
            final_count = await page.evaluate("""
                () => {
                    const match = document.body.innerText.match(/(\\d+) von (\\d+) DiGA/);
                    return match ? { displayed: parseInt(match[1]), total: parseInt(match[2]) } : null;
                }
            """)
            if final_count:
                print(f"  Final state: {final_count['displayed']} of {final_count['total']} DiGA visible")
            
            # Extract all entries using JavaScript
            print("  Extracting data from DOM...")
            entries = await page.evaluate(self.EXTRACTION_JS)
            
            print(f"  Extracted {len(entries)} DTx entries")
            
            # Add timestamp and translate status
            for dtx in entries:
                dtx["last_scraped"] = datetime.utcnow().isoformat() + "Z"
                dtx["listing_status"] = self.status_translations.get(
                    dtx.get("listing_status_de", ""),
                    dtx.get("listing_status_de", "Unknown")
                )
            
            return entries
            
        except Exception as e:
            print(f"Error scraping DTx list: {e}")
            raise
        finally:
            await page.close()
    
    async def scrape_dtx_details(self, dtx_basic: Dict) -> Dict:
        """Scrape detailed information for a single DTx using browser-use.
        
        This method uses browser-use (AI-powered) because detail pages require:
        - Clicking "Mehr anzeigen" buttons to expand hidden sections
        - Handling dynamic content that appears after interactions
        - Navigating complex, varying page structures
        
        Args:
            dtx_basic: Dictionary with basic DTx info including source_url.
            
        Returns:
            Dictionary with complete DTx information.
        """
        source_url = dtx_basic.get("source_url")
        if not source_url:
            return dtx_basic
        
        print(f"    Using browser-use AI to extract details from {source_url}")
        
        browser = await self._create_browser()
        
        task = f"""
Go to {source_url} and extract ALL information about this Digital Health Application (DiGA).

You need to:
1. Click on all "Mehr anzeigen" buttons to expand all sections, especially:
   - "Weitere Informationen zur digitalen Gesundheitsanwendung"
   - "Informationen zum positiven Versorgungseffekt" 
   - "Änderungshistorie"

2. Extract the following information:
   - DTx Name (from h1 heading)
   - Company/Provider name and country
   - Website URL of the company
   - Listing status (Dauerhaft aufgenommen, Vorläufig aufgenommen, or delisted)
   - ICD-10 codes (from "Anzuwenden bei" section) - get ALL codes
   - Platforms: App Store URL, Play Store URL, Web App URL
   - Price (Herstellerpreis)
   - Available languages
   - Date of first listing (from Änderungshistorie - look for "DiGA in Verzeichnis aufgenommen")
   - Trial registration numbers (NCT numbers from "Informationen zum positiven Versorgungseffekt")
   - If delisted: reason for delisting (from "Bewertungsentscheidung des BfArM")

Return the data as JSON with this structure:
{{
  "dtx_name": "name",
  "dtx_name_de": "German name",
  "company_provider": "company name, country",
  "company_website": "URL",
  "listing_status_de": "status in German",
  "date_of_first_listing": "YYYY-MM-DD",
  "clinical_area_icd10": ["F90.0", "F90.1"],
  "app_store_url": "URL or null",
  "play_store_url": "URL or null", 
  "web_app_url": "URL or null",
  "price_eur": "551.70",
  "languages": ["Deutsch"],
  "trial_registration_ids": ["NCT06221930"],
  "reason_for_delisting": "reason or null",
  "description": "brief description of the DiGA"
}}

Return ONLY the JSON object, no additional text.
"""
        
        agent = await self._create_agent(task, browser)
        history = await agent.run()
        
        # Extract the result
        details = self._extract_json_from_response(history, expect_array=False)
        
        # Merge with basic info
        result = dtx_basic.copy()
        result.update(details)
        
        # Translate status
        result["listing_status"] = self.status_translations.get(
            result.get("listing_status_de", ""),
            result.get("listing_status_de", "Unknown")
        )
        
        # Update timestamp
        result["last_scraped"] = datetime.utcnow().isoformat() + "Z"
        
        # Ensure all expected fields exist
        result.setdefault("company_founding_year", None)
        result.setdefault("dtx_category", None)
        result.setdefault("reviews_playstore", None)
        result.setdefault("reviews_appstore", None)
        
        return result
    
    def _extract_json_from_response(self, history, expect_array: bool = True) -> any:
        """Extract JSON data from agent response.
        
        Args:
            history: Agent history containing the response.
            expect_array: If True, expect a JSON array; otherwise expect object.
            
        Returns:
            Parsed JSON data.
        """
        text = ""
        attachment_path = None
        
        # Method 1: Check action_results for attachment file path and extracted_content
        if hasattr(history, 'action_results') and callable(history.action_results):
            try:
                results = history.action_results()
                for result in reversed(results):
                    if result and hasattr(result, 'is_done') and result.is_done:
                        # Check for attachment file
                        if hasattr(result, 'attachments') and result.attachments:
                            for att in result.attachments:
                                if att and att.endswith('.json'):
                                    attachment_path = att
                                    break
                        # Get extracted content
                        if hasattr(result, 'extracted_content') and result.extracted_content:
                            text = str(result.extracted_content)
                            print(f"DEBUG: Got text from is_done action_result, length: {len(text)}")
                        break
            except Exception as e:
                print(f"DEBUG: Error getting action_results: {e}")
        
        # Method 2: Use final_result() if we don't have text yet
        if not text and hasattr(history, 'final_result') and callable(history.final_result):
            try:
                result = history.final_result()
                if result:
                    if hasattr(result, 'text'):
                        text = str(result.text)
                    else:
                        text = str(result)
                    print(f"DEBUG: Got text from final_result(), length: {len(text)}")
            except Exception as e:
                print(f"DEBUG: Error getting final_result: {e}")
        
        # Method 3: Try to read the attachment file directly (most reliable!)
        if attachment_path:
            import os
            if os.path.exists(attachment_path):
                try:
                    with open(attachment_path, 'r', encoding='utf-8') as f:
                        file_content = f.read()
                    data = json.loads(file_content)
                    if expect_array and isinstance(data, list):
                        print(f"DEBUG: Parsed JSON from attachment file: {len(data)} items")
                        return data
                    elif not expect_array and isinstance(data, dict):
                        print(f"DEBUG: Parsed JSON object from attachment file")
                        return data
                except Exception as e:
                    print(f"DEBUG: Could not read attachment file {attachment_path}: {e}")
        
        # Debug output
        if text:
            print(f"DEBUG: First 1000 chars of text:\n{text[:1000]}")
        
        # Method 4: Look for JSON in the Attachments section first (complete data)
        # The browser-use agent often puts truncated JSON first, then full JSON after "Attachments:"
        if text:
            # Find JSON after attachment filename markers like "diga_entries.json:\n\n["
            attachment_markers = ['.json:\n\n[', '.json:\n[', 'Attachments:\n\n[', 'Attachments:\n[']
            for marker in attachment_markers:
                marker_pos = text.find(marker)
                if marker_pos != -1:
                    # Find the start of JSON after the marker  
                    json_start = text.find('[', marker_pos) if expect_array else text.find('{', marker_pos)
                    if json_start != -1:
                        extracted = self._parse_json_with_bracket_matching(text[json_start:], expect_array)
                        if extracted is not None:
                            print(f"DEBUG: Extracted JSON from attachments section: {len(extracted) if isinstance(extracted, list) else 'object'}")
                            return extracted
        
        # Method 5: Try direct JSON parse
        if text:
            try:
                data = json.loads(text)
                if expect_array and isinstance(data, list):
                    print(f"DEBUG: Direct JSON parse succeeded: {len(data)} items")
                    return data
                elif not expect_array and isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        
        # Method 6: Use bracket matching on the full text (may get truncated version)
        if text:
            extracted = self._parse_json_with_bracket_matching(text, expect_array)
            if extracted is not None:
                print(f"DEBUG: Bracket matching succeeded: {len(extracted) if isinstance(extracted, list) else 'object'}")
                return extracted
        
        # Method 7: Parse markdown-formatted extraction results from action_results
        # This is a fallback when the agent uses placeholders instead of real JSON
        if expect_array and hasattr(history, 'action_results') and callable(history.action_results):
            try:
                results = history.action_results()
                all_entries = []
                for result in results:
                    if result and hasattr(result, 'extracted_content') and result.extracted_content:
                        content = str(result.extracted_content)
                        # Look for markdown entries like "**Name**\n   - Provider: ..."
                        entries = self._parse_markdown_entries(content)
                        all_entries.extend(entries)
                
                if all_entries:
                    # Deduplicate by source_url
                    seen_urls = set()
                    unique_entries = []
                    for entry in all_entries:
                        url = entry.get('source_url', '')
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            unique_entries.append(entry)
                    
                    print(f"DEBUG: Parsed {len(unique_entries)} entries from markdown extraction results")
                    return unique_entries
            except Exception as e:
                print(f"DEBUG: Error parsing markdown: {e}")
        
        print("DEBUG: No valid JSON found in response")
        return [] if expect_array else {}
    
    def _parse_markdown_entries(self, text: str) -> List[Dict]:
        """Parse DiGA entries from markdown-formatted extraction results.
        
        Args:
            text: Markdown text containing entries like:
                1. **Name**
                   - Provider: Company
                   - Listing Status: Status
                   - Detail Page URL: [url](full_url)
        
        Returns:
            List of parsed entries as dictionaries.
        """
        entries = []
        
        # Pattern to match numbered entries with name in bold
        # Handles both formats:
        # 1. **Name**\n   - Provider: ...
        # **Name**\n   - **Provider**: ...
        entry_pattern = re.compile(
            r'\d+\.\s*\*\*([^*]+)\*\*\s*\n'  # Name in bold after number
            r'(?:.*?(?:Provider|Company)[:\s]*([^\n]+)\n)?'  # Provider line
            r'(?:.*?(?:Listing Status|Status)[:\s]*([^\n]+)\n)?'  # Status line
            r'(?:.*?(?:Detail Page URL|URL)[^\(]*\(([^\)]+)\))?',  # URL in markdown link
            re.IGNORECASE | re.DOTALL
        )
        
        for match in entry_pattern.finditer(text):
            name = match.group(1).strip() if match.group(1) else None
            provider = match.group(2).strip() if match.group(2) else None
            status = match.group(3).strip() if match.group(3) else None
            url = match.group(4).strip() if match.group(4) else None
            
            if name and url:
                # Clean up provider (remove markdown formatting and artifacts)
                if provider:
                    provider = re.sub(r'\*+', '', provider).strip()
                    # Remove leading slashes and "Provider:" prefix
                    provider = re.sub(r'^[/\s]*(?:Provider)?[:\s]*', '', provider).strip()
                
                # Clean up status (remove markdown and leading artifacts)
                if status:
                    status = re.sub(r'\*+', '', status).strip()
                    # Remove leading colon and spaces
                    status = re.sub(r'^[:\s]+', '', status).strip()
                
                # Make sure URL is absolute
                if url and not url.startswith('http'):
                    url = f"https://diga.bfarm.de{url}"
                
                entries.append({
                    'dtx_name': name,
                    'company_provider': provider or 'Unknown',
                    'listing_status_de': status or 'Unknown',
                    'source_url': url
                })
        
        return entries
    
    def _parse_json_with_bracket_matching(self, text: str, expect_array: bool = True) -> any:
        """Parse JSON from text using bracket matching.
        
        Args:
            text: Text containing JSON.
            expect_array: If True, expect a JSON array; otherwise expect object.
            
        Returns:
            Parsed JSON data or None if parsing fails.
        """
        start_char = '[' if expect_array else '{'
        end_char = ']' if expect_array else '}'
        
        start = text.find(start_char)
        if start == -1:
            return None
        
        depth = 0
        in_string = False
        escape_next = False
        
        for i, char in enumerate(text[start:], start):
            if escape_next:
                escape_next = False
                continue
            if char == '\\':
                escape_next = True
                continue
            if char == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            
            if char == start_char:
                depth += 1
            elif char == end_char:
                depth -= 1
                if depth == 0:
                    json_str = text[start:i+1]
                    try:
                        data = json.loads(json_str)
                        if expect_array and isinstance(data, list):
                            return data
                        elif not expect_array and isinstance(data, dict):
                            return data
                    except json.JSONDecodeError:
                        return None
        
        return None
    
    async def scrape_list_only(self) -> List[Dict]:
        """Scrape only the list of DTx without details.
        
        Useful for quick inventory or incremental update checks.
        
        Returns:
            List of dictionaries with basic DTx info.
        """
        return await self.scrape_dtx_list()
