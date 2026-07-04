"""DiGA (German Digital Health Applications) directory scraper.

This module uses Playwright for reliable, reproducible scraping:
- List scraping: Extracts all DTx entries with basic info
- Detail scraping: Extracts detailed info by clicking expand buttons and parsing DOM
"""
import asyncio
import json
import re
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path

from .base_scraper import BaseScraper
from utils.company_name import normalize_company_name
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
            
            // Find status and company.
            // Active/provisional cards put "<status> | <company>" inside the
            // subheader. Delisted ("gestrichen") cards instead carry only the
            // company span in the subheader plus a separate .entity-app__retired
            // notice, so the status/company must be read differently.
            const infoDiv = heading?.parentElement;
            const subheader = card.querySelector('.entity-app__subheader') || infoDiv;
            const retired = card.querySelector('.entity-app__retired');
            let status = '';
            let company = '';

            const subText = subheader ? subheader.textContent : '';
            if (retired || /gestrichen/i.test(card.textContent)) {
                status = 'Gestrichen';
            } else if (subText.includes('Dauerhaft aufgenommen')) {
                status = 'Dauerhaft aufgenommen';
            } else if (subText.includes('Vorläufig aufgenommen')) {
                status = 'Vorläufig aufgenommen';
            }

            if (subheader) {
                // Company is the last subheader <span> that is not the "|"
                // separator and not a status label (e.g. "GAIA AG, Deutschland").
                const spans = Array.from(subheader.querySelectorAll('span'))
                    .map(s => s.textContent.trim())
                    .filter(t => t && t !== '|' &&
                                 !t.includes('aufgenommen') && t !== 'Gestrichen');
                if (spans.length) company = spans[spans.length - 1];
            }
            if (!company && subText.includes('|')) {
                const parts = subText.split('|');
                company = parts[parts.length - 1].trim();
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
        
        # Get ICD-10 codes to preserve during translation. Copy into a new list
        # so we never mutate dtx["clinical_area_icd10"] (previously this appended
        # trial-registration IDs into the stored ICD-10 codes).
        preserve_terms = list(dtx.get("clinical_area_icd10", []) or [])
        preserve_terms.extend(dtx.get("trial_registration_ids", []) or [])
        
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
            # Navigate to the directory with filter to include ALL entries (including delisted).
            # An empty type=[] only returns active + provisional, so we explicitly select
            # all three status types (active, draft, retired) to include "Gestrichen" entries.
            all_filter = self.status_filters.get("all") or "type=%5B%22active%22%2C%22draft%22%2C%22retired%22%5D"
            url_with_all_filter = f"{self.base_url}?{all_filter}"
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
    
    # JavaScript for extracting detail page information
    DETAIL_EXTRACTION_JS = """
    () => {
        const data = {};
        
        // Get all text content for searching
        const bodyText = document.body.innerText;
        
        // DTx Name - find the main content h1 (not navigation)
        // The main content area usually has a specific structure
        const mainH1 = document.querySelector('main h1') || 
                       document.querySelector('article h1') ||
                       document.querySelector('[class*="content"] h1') ||
                       document.querySelector('[class*="detail"] h1');
        
        if (mainH1) {
            data.dtx_name_de = mainH1.textContent.trim();
        } else {
            // Fallback: find h1 that contains the DiGA name (not "Menü")
            const allH1s = document.querySelectorAll('h1');
            for (const h1 of allH1s) {
                const text = h1.textContent.trim();
                if (text && text !== 'Menü' && text.length > 3 && text.length < 200) {
                    data.dtx_name_de = text;
                    break;
                }
            }
        }
        
        // Status
        if (bodyText.includes('Dauerhaft aufgenommen')) {
            data.listing_status_de = 'Dauerhaft aufgenommen';
        } else if (bodyText.includes('Vorläufig aufgenommen')) {
            data.listing_status_de = 'Vorläufig aufgenommen';
        } else if (bodyText.includes('Gestrichen') || bodyText.includes('gestrichen')) {
            data.listing_status_de = 'Gestrichen';
        }
        
        // Company/Provider - look for text after company patterns.
        // The capture classes allow only spaces/tabs (not newlines) so the
        // company match never bleeds across lines into status notes / product
        // names. The Python side additionally normalizes the result.
        const companyPatterns = [
            /Hersteller[ \\t:]+([A-Za-zäöüÄÖÜß0-9 \\t\\.\\-&]*(?:GmbH|AG|UG|B\\.V\\.|s\\.r\\.o\\.|Ltd\\.?|Inc\\.?)[^\\n]*)/i,
            /([A-Za-zäöüÄÖÜß0-9 \\t\\.\\-&]*(?:GmbH|AG|UG|B\\.V\\.|s\\.r\\.o\\.|Ltd\\.?|Inc\\.?)),?[ \\t]*([A-Za-zäöüÄÖÜß]+)\\b/
        ];
        for (const pattern of companyPatterns) {
            const match = bodyText.match(pattern);
            if (match) {
                data.company_provider = match[1] ? match[1].trim() : match[0].trim();
                break;
            }
        }
        
        // Company website - find external links that look like company sites
        const links = Array.from(document.querySelectorAll('a[href^="http"]'));
        const companyWebsitePatterns = ['gaia', 'health', 'med', 'care', 'app', 'digital', 'therapy'];
        for (const link of links) {
            const href = link.href.toLowerCase();
            if (!href.includes('diga.bfarm.de') && 
                !href.includes('apple.com') && 
                !href.includes('play.google.com') &&
                !href.includes('clinicaltrials.gov') &&
                !href.includes('drks.de') &&
                !href.includes('github.com')) {
                // Check if the link text suggests it's a company website
                const linkText = link.textContent.toLowerCase();
                if (linkText.includes('webseite') || linkText.includes('website') || 
                    linkText.includes('homepage') || linkText.includes('hersteller')) {
                    data.company_website = link.href;
                    break;
                }
                // Or if URL contains company-like patterns
                if (companyWebsitePatterns.some(p => href.includes(p))) {
                    data.company_website = link.href;
                    break;
                }
            }
        }
        
        // ICD-10 codes will be extracted separately from the Fachkreise table
        // Initialize as empty - will be populated by extractIndikationFromTable()
        data.clinical_area_icd10 = [];
        
        // App Store URL
        const appStoreLink = document.querySelector('a[href*="apps.apple.com"]');
        data.app_store_url = appStoreLink ? appStoreLink.href : null;
        
        // Play Store URL
        const playStoreLink = document.querySelector('a[href*="play.google.com"]');
        data.play_store_url = playStoreLink ? playStoreLink.href : null;
        
        // Web App URL - look for specific web app patterns
        const webAppPatterns = ['webapp', 'web-app', 'browser', 'online'];
        for (const link of links) {
            const href = link.href.toLowerCase();
            const text = link.textContent.toLowerCase();
            if (webAppPatterns.some(p => href.includes(p) || text.includes(p))) {
                if (!href.includes('diga.bfarm.de')) {
                    data.web_app_url = link.href;
                    break;
                }
            }
        }
        
        // Price - look for Herstellerpreis with more flexible matching
        const pricePatterns = [
            /Herstellerpreis[^\\d]*([\\d]+[,.]\\d{2})\\s*(?:€|EUR)/i,
            /Preis[^\\d]*([\\d]+[,.]\\d{2})\\s*(?:€|EUR)/i,
            /([\\d]+[,.]\\d{2})\\s*€/
        ];
        for (const pattern of pricePatterns) {
            const match = bodyText.match(pattern);
            if (match) {
                data.price_eur = match[1].replace(',', '.');
                break;
            }
        }
        
        // Risk class is NOT extracted here: it lives in the collapsed
        // "Weitere Informationen zur digitalen Gesundheitsanwendung" section,
        // which is not expanded yet at this point. It is extracted separately
        // after that accordion is opened (see _extract_risk_class).
        
        // Languages - look for Sprache(n) section
        const langMatch = bodyText.match(/(?:Sprache|Sprachen)[:\\s]*([^\\n]+)/i);
        if (langMatch) {
            const langText = langMatch[1];
            // Extract language names
            const langs = langText.match(/[A-ZÄÖÜ][a-zäöüß]+/g) || [];
            data.languages = langs.filter(l => l.length > 3).slice(0, 5);
        }
        if (!data.languages || data.languages.length === 0) {
            data.languages = ['Deutsch'];
        }
        
        // Date of first listing is NOT extracted here: it lives in the
        // collapsed "Weitere Informationen zur digitalen Gesundheitsanwendung"
        // section (field "Erstmalige Aufnahme in das DiGA-Verzeichnis").
        // Reading the raw bodyText here grabbed the page "Stand"/retrieved
        // date instead of the real listing date. It is now extracted
        // separately after that section is opened (see _extract_listing_date).
        
        // Trial registration IDs (NCT and DRKS numbers)
        const nctPattern = /NCT\\d{8}/g;
        const nctMatches = bodyText.match(nctPattern) || [];
        const drksPattern = /DRKS\\d{8}/g;
        const drksMatches = bodyText.match(drksPattern) || [];
        data.trial_registration_ids = [...new Set([...nctMatches, ...drksMatches])];
        
        // Reason for delisting (if applicable)
        if (data.listing_status_de === 'Gestrichen') {
            // Look for delisting reason in the text
            const delistMatch = bodyText.match(/(?:gestrichen|Streichung|Grund)[^]*?(?:weil|da|aufgrund|wegen)[^.]*\\./i);
            if (delistMatch) {
                data.reason_for_delisting = delistMatch[0].slice(0, 500);
            }
        } else {
            data.reason_for_delisting = null;
        }
        
        // Description - get meaningful paragraph content
        const mainContent = document.querySelector('main') || document.body;
        const paragraphs = mainContent.querySelectorAll('p');
        for (const p of paragraphs) {
            const text = p.textContent.trim();
            // Look for descriptive text (not navigation, not too short)
            if (text.length > 80 && text.length < 2000 && 
                !text.includes('Cookie') && !text.includes('Datenschutz')) {
                data.description = text;
                break;
            }
        }
        
        return data;
    }
    """
    
    # JavaScript for extracting ICD-10 codes from the "Informationen für Fachkreise" table
    # This extracts codes ONLY from the "Indikation" column, not Kontraindikation
    INDIKATION_TABLE_EXTRACTION_JS = """
    () => {
        const icdCodes = new Set();
        
        // Find all tables on the page (after Fachkreise section is expanded)
        const tables = document.querySelectorAll('table');
        
        for (const table of tables) {
            // Get header row to find the Indikation column index
            const headerRow = table.querySelector('thead tr') || table.querySelector('tr');
            if (!headerRow) continue;
            
            const headers = Array.from(headerRow.querySelectorAll('th, td'));
            let indikationIndex = -1;
            
            // Find the column index for "Indikation" (not "Kontraindikation")
            for (let i = 0; i < headers.length; i++) {
                const headerText = headers[i].textContent.trim().toLowerCase();
                // Match "Indikation" but NOT "Kontraindikation"
                if (headerText === 'indikation' || 
                    (headerText.includes('indikation') && !headerText.includes('kontra'))) {
                    indikationIndex = i;
                    break;
                }
            }
            
            if (indikationIndex === -1) continue;
            
            // Extract ICD-10 codes from the Indikation column in all data rows
            const dataRows = table.querySelectorAll('tbody tr, tr:not(:first-child)');
            for (const row of dataRows) {
                const cells = row.querySelectorAll('td');
                if (cells.length > indikationIndex) {
                    const cellText = cells[indikationIndex].textContent;
                    // Match ICD-10 codes: letter followed by 2 digits, optionally followed by .digits
                    const icdPattern = /[A-Z]\\d{2}(?:\\.\\d{1,2})?/g;
                    const matches = cellText.match(icdPattern) || [];
                    for (const code of matches) {
                        // Filter to valid medical ICD-10 prefixes
                        const prefix = code.charAt(0);
                        if (['F', 'E', 'G', 'I', 'J', 'K', 'M', 'N', 'R', 'Z', 'T', 'S', 'L', 'H', 'A', 'B', 'C', 'D'].includes(prefix)) {
                            icdCodes.add(code);
                        }
                    }
                }
            }
        }
        
        return Array.from(icdCodes).sort();
    }
    """
    
    # JavaScript to read the medical-device risk class once the
    # "Weitere Informationen zur digitalen Gesundheitsanwendung" section is open.
    # Values on the page look like "Risikoklasse I nach MDR" / "... IIa nach MDR".
    RISK_CLASS_EXTRACTION_JS = """
    () => {
        const text = document.body.innerText || "";
        // Primary: the value line "Risikoklasse <class> nach MDR".
        let m = text.match(/Risikoklasse\\s+(IIb|IIa|III|II|I)\\s+nach\\s+MDR/i);
        if (m) return m[1];
        // Fallback: label "Risikoklasse Medizinprodukt" then the value.
        m = text.match(/Risikoklasse\\s+Medizinprodukt[\\s:]*Risikoklasse\\s+(IIb|IIa|III|II|I)\\b/i);
        if (m) return m[1];
        return null;
    }
    """
    
    # JavaScript to read the real date of first listing from the
    # "Weitere Informationen zur digitalen Gesundheitsanwendung" section, which
    # exposes the labeled field "Erstmalige Aufnahme in das DiGA-Verzeichnis"
    # followed by a dd.mm.yyyy date. This field is present for every currently
    # listed DiGA (unlike the "Änderungshistorie", whose oldest entries are not
    # always retained). Returned as ISO yyyy-mm-dd, or null if not present.
    LISTING_DATE_EXTRACTION_JS = r"""
    () => {
        const text = document.body.innerText || "";
        const m = text.match(/Erstmalige\s+Aufnahme\s+in\s+das\s+DiGA-Verzeichnis\s*[:\n]?\s*(\d{2})\.(\d{2})\.(\d{4})/i);
        if (m) return `${m[3]}-${m[2]}-${m[1]}`;
        return null;
    }
    """

    @staticmethod
    def _format_mdr_risk_class(raw: Optional[str]) -> Optional[str]:
        """Format a bare MDR class into the labeled regulatory form.

        German DiGA are CE-marked under the EU MDR, so a bare "IIa" becomes
        "Risk Class IIa according to MDR". Returns None if there is no class.
        """
        if not raw:
            return None
        return f"Risk Class {raw.strip()} according to MDR"

    async def _extract_risk_class(self, page) -> Optional[str]:
        """Expand the medical-device section and read the MDR risk class.
        
        The risk class lives under "Weitere Informationen zur digitalen
        Gesundheitsanwendung", which is collapsed by default. We open that
        accordion (and any nested expanders), then scan the text.
        
        Args:
            page: A Playwright page already navigated to a DiGA detail page.
        
        Returns:
            The labeled risk class ("Risk Class IIa according to MDR", ...) or
            None if not found.
        """
        async def try_extract():
            try:
                val = await page.evaluate(self.RISK_CLASS_EXTRACTION_JS)
                return val.strip() if isinstance(val, str) and val.strip() else None
            except Exception:
                return None
        
        async def expand_collapsed():
            """Open every collapsed accordion (skip nav/menu toggles).
            
            Only clicks elements with aria-expanded="false", so it never closes
            a section that is already open - safe to call repeatedly and works
            whether or not the parent section was pre-expanded by the caller.
            """
            opened = False
            try:
                expanders = await page.locator('[aria-expanded="false"]').all()
            except Exception:
                expanders = []
            for exp in expanders:
                try:
                    label = (await exp.inner_text()) or ""
                except Exception:
                    label = ""
                if any(skip in label for skip in ("Menü", "Sprache", "Navigation")):
                    continue
                try:
                    await exp.click(timeout=1500)
                    opened = True
                    await asyncio.sleep(0.2)
                except Exception:
                    continue
            return opened
        
        async def click_named_section():
            """Click the medical-device section header once (a toggle)."""
            for selector in (
                'button:has-text("Weitere Informationen zur digitalen Gesundheitsanwendung")',
                'a:has-text("Weitere Informationen zur digitalen Gesundheitsanwendung")',
                'summary:has-text("Weitere Informationen zur digitalen Gesundheitsanwendung")',
                ':text("Weitere Informationen zur digitalen Gesundheitsanwendung")',
            ):
                try:
                    loc = page.locator(selector).first
                    if await loc.count() > 0:
                        await loc.click(timeout=2500)
                        await asyncio.sleep(1.2)
                        return True
                except Exception:
                    continue
            return False
        
        # Maybe it is already visible.
        risk = await try_extract()
        if risk:
            return self._format_mdr_risk_class(risk)
        
        # The "Weitere Informationen..." section may already be open (the detail
        # scrape pre-expands accordions) or still collapsed (standalone backfill).
        # First expand any collapsed accordions - this surfaces the nested
        # "Angaben zum Medizinprodukt" panel WITHOUT toggling an open section
        # shut. Only if the risk text is still missing do we click the named
        # header, retrying so an accidental toggle is re-opened next pass.
        for _ in range(3):
            await expand_collapsed()
            risk = await try_extract()
            if risk:
                return self._format_mdr_risk_class(risk)
            await click_named_section()
            risk = await try_extract()
            if risk:
                return self._format_mdr_risk_class(risk)
        
        return self._format_mdr_risk_class(await try_extract())
    
    async def scrape_risk_class(self, source_url: str) -> Optional[str]:
        """Open a DiGA detail page and return just its labeled MDR risk class.
        
        Used by the one-time backfill command; reuses the same extraction as the
        full detail scrape so behavior stays consistent.
        """
        if not source_url:
            return None
        page = await self._create_page()
        try:
            await page.goto(source_url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            await asyncio.sleep(1)
            return await self._extract_risk_class(page)
        except Exception as e:
            print(f"      Error extracting risk class from {source_url}: {e}")
            return None
        finally:
            await page.close()

    async def _extract_listing_date(self, page) -> Optional[str]:
        """Read the date of first listing from the medical-device section.

        The value lives under "Weitere Informationen zur digitalen
        Gesundheitsanwendung" as the labeled field "Erstmalige Aufnahme in das
        DiGA-Verzeichnis" - the same section that holds the risk class. It is
        collapsed by default, so we open it (mirroring ``_extract_risk_class``)
        before reading. The earlier code read the page before this was open and
        captured the retrieved/"Stand" date instead.

        Args:
            page: A Playwright page already navigated to a DiGA detail page.

        Returns:
            The date of first listing as ISO ``yyyy-mm-dd`` or None if not found.
        """
        async def try_extract():
            try:
                val = await page.evaluate(self.LISTING_DATE_EXTRACTION_JS)
                return val.strip() if isinstance(val, str) and val.strip() else None
            except Exception:
                return None

        async def expand_collapsed():
            """Open every collapsed accordion (skip nav/menu toggles)."""
            try:
                expanders = await page.locator('[aria-expanded="false"]').all()
            except Exception:
                expanders = []
            for exp in expanders:
                try:
                    label = (await exp.inner_text()) or ""
                except Exception:
                    label = ""
                if any(skip in label for skip in ("Menü", "Sprache", "Navigation")):
                    continue
                try:
                    await exp.click(timeout=1500)
                    await asyncio.sleep(0.2)
                except Exception:
                    continue

        async def click_named_section():
            """Click the medical-device section header once (a toggle)."""
            for selector in (
                'button:has-text("Weitere Informationen zur digitalen Gesundheitsanwendung")',
                'a:has-text("Weitere Informationen zur digitalen Gesundheitsanwendung")',
                'summary:has-text("Weitere Informationen zur digitalen Gesundheitsanwendung")',
                ':text("Weitere Informationen zur digitalen Gesundheitsanwendung")',
            ):
                try:
                    loc = page.locator(selector).first
                    if await loc.count() > 0:
                        await loc.click(timeout=2500)
                        await asyncio.sleep(1.2)
                        return True
                except Exception:
                    continue
            return False

        # Maybe it is already visible (the detail scrape opens this section for
        # the risk class before calling here).
        date = await try_extract()
        if date:
            return date

        for _ in range(3):
            await expand_collapsed()
            date = await try_extract()
            if date:
                return date
            await click_named_section()
            date = await try_extract()
            if date:
                return date

        return await try_extract()

    async def scrape_listing_date(self, source_url: str) -> Optional[str]:
        """Open a DiGA detail page and return just its date of first listing.

        Used by the one-time backfill command; reuses the same extraction as the
        full detail scrape so behavior stays consistent.
        """
        if not source_url:
            return None
        page = await self._create_page()
        try:
            await page.goto(source_url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            await asyncio.sleep(1)
            return await self._extract_listing_date(page)
        except Exception as e:
            print(f"      Error extracting listing date from {source_url}: {e}")
            return None
        finally:
            await page.close()
    
    async def scrape_dtx_details(self, dtx_basic: Dict) -> Dict:
        """Scrape detailed information for a single DTx using Playwright.
        
        This method uses Playwright directly for reliable, reproducible scraping:
        - Clicks "Mehr anzeigen" buttons to expand hidden sections
        - Clicks "Informationen für Fachkreise" to access the Module table
        - Extracts ICD-10 codes from the "Indikation" column in the table
        - Extracts other information using JavaScript DOM queries
        
        Args:
            dtx_basic: Dictionary with basic DTx info including source_url.
            
        Returns:
            Dictionary with complete DTx information.
        """
        source_url = dtx_basic.get("source_url")
        if not source_url:
            return dtx_basic
        
        page = await self._create_page()
        
        try:
            # Navigate to the detail page
            await page.goto(source_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(1)
            
            # Click all "Mehr anzeigen" (Show more) buttons to expand sections
            try:
                mehr_buttons = await page.locator('button:has-text("Mehr anzeigen")').all()
                for button in mehr_buttons:
                    try:
                        await button.click()
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass  # Button might not be clickable
            except Exception:
                pass  # No buttons found or error clicking
            
            # Also try clicking any accordion/expand buttons
            try:
                expand_buttons = await page.locator('[aria-expanded="false"]').all()
                for button in expand_buttons[:5]:  # Limit to first 5
                    try:
                        await button.click()
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass
            except Exception:
                pass
            
            # Wait for any dynamic content to load
            await asyncio.sleep(1)
            
            # Extract details using JavaScript
            details = await page.evaluate(self.DETAIL_EXTRACTION_JS)
            
            # Read the MDR risk class BEFORE switching to the Fachkreise tab.
            # The risk class lives in the consumer view ("Weitere Informationen
            # zur digitalen Gesundheitsanwendung"); clicking "Informationen für
            # Fachkreise" swaps that view out, so the section disappears. Extract
            # it here while the consumer section is still present.
            risk_class_value = None
            try:
                risk_class_value = await self._extract_risk_class(page)
                if risk_class_value:
                    print(f"      Risk class: {risk_class_value}")
            except Exception as e:
                print(f"      Could not extract risk class: {e}")

            # Date of first listing lives in the "Änderungshistorie" accordion.
            # Extract it here (still in the consumer view) via the dedicated
            # accordion-opening helper; the buggy in-page fallback was removed.
            listing_date_value = None
            try:
                listing_date_value = await self._extract_listing_date(page)
                if listing_date_value:
                    print(f"      Date of first listing: {listing_date_value}")
            except Exception as e:
                print(f"      Could not extract listing date: {e}")
            
            # Click "Informationen für Fachkreise" to access the Module table with ICD-10 codes
            icd10_codes = []
            try:
                # Try multiple selectors for the Fachkreise button/link
                fachkreise_selectors = [
                    'button:has-text("Informationen für Fachkreise")',
                    'a:has-text("Informationen für Fachkreise")',
                    '[class*="accordion"]:has-text("Fachkreise")',
                    'summary:has-text("Fachkreise")',
                    'div:has-text("Informationen für Fachkreise"):not(:has(div))',
                ]
                
                clicked = False
                for selector in fachkreise_selectors:
                    try:
                        fachkreise_btn = page.locator(selector).first
                        if await fachkreise_btn.count() > 0:
                            await fachkreise_btn.click()
                            clicked = True
                            print("      Clicked 'Informationen für Fachkreise'")
                            await asyncio.sleep(1.5)  # Wait for section to expand
                            break
                    except Exception:
                        continue
                
                if not clicked:
                    # Try clicking by text content directly
                    try:
                        await page.get_by_text("Informationen für Fachkreise", exact=False).first.click()
                        clicked = True
                        print("      Clicked 'Informationen für Fachkreise' via text")
                        await asyncio.sleep(1.5)
                    except Exception:
                        pass
                
                # Extract ICD-10 codes from the Indikation table column
                if clicked:
                    icd10_codes = await page.evaluate(self.INDIKATION_TABLE_EXTRACTION_JS)
                    if icd10_codes:
                        print(f"      Extracted {len(icd10_codes)} ICD-10 codes from Indikation table")
                
            except Exception as e:
                print(f"      Could not access Fachkreise section: {e}")
            
            # Merge with basic info
            result = dtx_basic.copy()
            if details:
                result.update({k: v for k, v in details.items() if v is not None})
            
            # Normalize the manufacturer name so only the clean company is stored
            # (strips DiGA status notes / boilerplate / product-name fragments).
            if result.get("company_provider"):
                result["company_provider"] = normalize_company_name(
                    result["company_provider"]
                )
            
            # Set ICD-10 codes from table extraction (overrides the empty default)
            if icd10_codes:
                result["clinical_area_icd10"] = icd10_codes
            
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
            result.setdefault("company_website", None)
            result.setdefault("clinical_area_icd10", [])
            result.setdefault("app_store_url", None)
            result.setdefault("play_store_url", None)
            result.setdefault("web_app_url", None)
            result.setdefault("price_eur", None)
            result.setdefault("languages", ["Deutsch"])
            result.setdefault("date_of_first_listing", None)
            result.setdefault("trial_registration_ids", [])
            result.setdefault("reason_for_delisting", None)
            result.setdefault("description", None)
            result.setdefault("risk_class", None)
            if risk_class_value:
                result["risk_class"] = risk_class_value
            # Real date of first listing from the Änderungshistorie accordion.
            if listing_date_value:
                result["date_of_first_listing"] = listing_date_value
            
            # Unified Phase 1 fields (shared shape across Germany / USA).
            status_de = result.get("listing_status_de", "") or ""
            is_removed = "estrichen" in status_de.lower()  # (G/g)estrichen
            # Stable id from the DiGA directory number in the source URL.
            verzeichnis_id = None
            src = result.get("source_url", "") or ""
            id_match = re.search(r"/verzeichnis/(\w+)", src)
            if id_match:
                verzeichnis_id = id_match.group(1)
            if not verzeichnis_id:
                # Rare fallback: slug from the product name.
                name_slug = re.sub(r"[^a-z0-9]+", "-", result.get("dtx_name", "").lower()).strip("-")
                verzeichnis_id = name_slug[:50] or "unknown"
            result["id"] = f"de-{verzeichnis_id}"
            result["listing"] = "Removed" if is_removed else "Active"
            result["diga_listing"] = "Removed" if is_removed else result.get("listing_status", "Unknown")
            result["removed_from_diga_listing"] = (
                (result.get("reason_for_delisting") or "Removed from DiGA directory")
                if is_removed else "active"
            )
            result["category"] = result.get("dtx_category")
            
            return result
            
        except Exception as e:
            print(f"      Error scraping details: {e}")
            # Return basic info on error
            return dtx_basic
        finally:
            await page.close()
    
    async def scrape_list_only(self) -> List[Dict]:
        """Scrape only the list of DTx without details.
        
        Useful for quick inventory or incremental update checks.
        
        Returns:
            List of dictionaries with basic DTx info.
        """
        return await self.scrape_dtx_list()
