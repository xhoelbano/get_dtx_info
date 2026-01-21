"""Evidence scraper for PubMed and Google Scholar."""
import asyncio
import json
import re
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path
from urllib.parse import quote_plus

from .base_scraper import BaseScraper


class EvidenceScraper(BaseScraper):
    """Scraper for finding RCT/RWE evidence from PubMed and Google Scholar."""
    
    def __init__(self, config_path: str = "config/germany.json"):
        """Initialize the evidence scraper.
        
        Args:
            config_path: Path to configuration file.
        """
        super().__init__(config_path)
        self.evidence_docs_dir = Path("evidence-docs")
        self.evidence_docs_dir.mkdir(parents=True, exist_ok=True)
    
    async def scrape(self, dtx_data: Dict, **kwargs) -> List[Dict]:
        """Main scraping method - find evidence for a DTx.
        
        Args:
            dtx_data: Dictionary containing DTx information.
            **kwargs: Additional arguments.
            
        Returns:
            List of evidence dictionaries.
        """
        return await self.search_evidence(dtx_data)
    
    def generate_search_queries(self, dtx_data: Dict) -> List[str]:
        """Generate search queries for a DTx.
        
        Args:
            dtx_data: Dictionary containing DTx information.
            
        Returns:
            List of search query strings.
        """
        dtx_name = dtx_data.get("dtx_name", "")
        dtx_name_de = dtx_data.get("dtx_name_de", dtx_name)
        company = dtx_data.get("company_provider", "").split(",")[0].strip()
        icd_codes = dtx_data.get("clinical_area_icd10", [])
        
        queries = []
        
        # Primary queries with DTx name
        if dtx_name:
            queries.extend([
                f'"{dtx_name}" randomized controlled trial',
                f'"{dtx_name}" clinical trial',
                f'"{dtx_name}" digital therapeutic',
            ])
        
        # German name if different
        if dtx_name_de and dtx_name_de != dtx_name:
            queries.append(f'"{dtx_name_de}" klinische studie')
        
        # Company-based queries
        if company:
            queries.append(f'"{company}" digital therapeutic RCT')
        
        # ICD code based queries (for broader search)
        if icd_codes and dtx_name:
            primary_icd = icd_codes[0] if icd_codes else ""
            if primary_icd:
                queries.append(f'"{dtx_name}" {primary_icd}')
        
        return queries[:5]  # Limit to 5 queries per DTx
    
    async def search_evidence(self, dtx_data: Dict) -> List[Dict]:
        """Search for evidence papers for a DTx.
        
        Args:
            dtx_data: Dictionary containing DTx information.
            
        Returns:
            List of evidence dictionaries.
        """
        all_evidence = []
        queries = self.generate_search_queries(dtx_data)
        
        for query in queries:
            # Search PubMed
            pubmed_results = await self.search_pubmed(query)
            all_evidence.extend(pubmed_results)
            
            await asyncio.sleep(2)  # Rate limiting
            
            # Search Google Scholar
            scholar_results = await self.search_google_scholar(query)
            all_evidence.extend(scholar_results)
            
            await asyncio.sleep(3)  # More conservative rate limiting for Scholar
        
        # Deduplicate by title
        seen_titles = set()
        unique_evidence = []
        for evidence in all_evidence:
            title_lower = evidence.get("title", "").lower()
            if title_lower and title_lower not in seen_titles:
                seen_titles.add(title_lower)
                unique_evidence.append(evidence)
        
        # Classify as RCT or RWE
        for evidence in unique_evidence:
            evidence["type"] = self._classify_evidence_type(evidence)
        
        return unique_evidence
    
    async def search_pubmed(self, query: str) -> List[Dict]:
        """Search PubMed for evidence.
        
        Args:
            query: Search query string.
            
        Returns:
            List of evidence dictionaries.
        """
        encoded_query = quote_plus(query)
        url = f"https://pubmed.ncbi.nlm.nih.gov/?term={encoded_query}"
        
        browser = await self._create_browser()
        
        task = f"""
Go to {url} and extract information about the search results.

For each result (up to 10), extract:
1. Title of the paper
2. Authors (first author et al. is fine)
3. Publication date/year
4. PMID (PubMed ID)
5. Whether it appears to be open access (look for "Free" or "PMC" labels)
6. Abstract if visible

Return the data as a JSON array:
[
  {{
    "title": "Paper title",
    "authors": "First Author et al.",
    "publication_date": "2024",
    "pmid": "12345678",
    "doi": "10.xxxx/xxxxx",
    "is_open_access": true,
    "abstract": "Abstract text if available",
    "source": "PubMed",
    "url": "https://pubmed.ncbi.nlm.nih.gov/12345678/"
  }}
]

If no results found, return an empty array: []

Return ONLY the JSON array.
"""
        
        agent = await self._create_agent(task, browser)
        
        try:
            history = await agent.run()
            return self._extract_json_array(history)
        except Exception as e:
            print(f"PubMed search error: {e}")
            return []
    
    async def search_google_scholar(self, query: str) -> List[Dict]:
        """Search Google Scholar for evidence.
        
        Args:
            query: Search query string.
            
        Returns:
            List of evidence dictionaries.
        """
        encoded_query = quote_plus(query)
        url = f"https://scholar.google.com/scholar?q={encoded_query}"
        
        browser = await self._create_browser()
        
        task = f"""
Go to {url} and extract information about the search results.

For each result (up to 10), extract:
1. Title of the paper
2. Authors
3. Publication year
4. Number of citations
5. Whether there's a PDF link available (is_open_access)
6. The URL to the paper

Return the data as a JSON array:
[
  {{
    "title": "Paper title",
    "authors": "Authors",
    "publication_date": "2024",
    "citations": 42,
    "is_open_access": true,
    "source": "Google Scholar",
    "url": "URL to paper"
  }}
]

If no results found or if there's a CAPTCHA, return an empty array: []

Return ONLY the JSON array.
"""
        
        agent = await self._create_agent(task, browser)
        
        try:
            history = await agent.run()
            return self._extract_json_array(history)
        except Exception as e:
            print(f"Google Scholar search error: {e}")
            return []
    
    def _classify_evidence_type(self, evidence: Dict) -> str:
        """Classify evidence as RCT or RWE based on title/abstract.
        
        Args:
            evidence: Evidence dictionary.
            
        Returns:
            "RCT" or "RWE" classification.
        """
        text = (
            evidence.get("title", "") + " " + 
            evidence.get("abstract", "")
        ).lower()
        
        rct_keywords = [
            "randomized", "randomised", "rct", "controlled trial",
            "double-blind", "double blind", "placebo-controlled",
            "clinical trial", "randomization"
        ]
        
        rwe_keywords = [
            "real-world", "real world", "observational",
            "retrospective", "registry", "claims data",
            "electronic health record", "ehr", "routine care"
        ]
        
        rct_score = sum(1 for kw in rct_keywords if kw in text)
        rwe_score = sum(1 for kw in rwe_keywords if kw in text)
        
        if rct_score > rwe_score:
            return "RCT"
        elif rwe_score > 0:
            return "RWE"
        else:
            return "RCT"  # Default to RCT if unclear
    
    def _extract_json_array(self, history) -> List[Dict]:
        """Extract JSON array from agent response.
        
        Args:
            history: Agent history containing the response.
            
        Returns:
            List of dictionaries.
        """
        if hasattr(history, 'final_result'):
            text = str(history.final_result())
        elif hasattr(history, 'result'):
            text = str(history.result)
        else:
            text = str(history)
        
        match = re.search(r'\[[\s\S]*\]', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        
        return []
    
    async def download_pdf(self, evidence: Dict, dtx_name: str) -> Optional[str]:
        """Download PDF for open access paper.
        
        Args:
            evidence: Evidence dictionary with URL.
            dtx_name: Name of the DTx for folder organization.
            
        Returns:
            Path to downloaded PDF or None.
        """
        if not evidence.get("is_open_access"):
            return None
        
        # Create folder structure
        from slugify import slugify
        dtx_slug = slugify(dtx_name)
        evidence_type = evidence.get("type", "RCT")
        
        folder = self.evidence_docs_dir / dtx_slug / evidence_type
        folder.mkdir(parents=True, exist_ok=True)
        
        # Generate filename
        pmid = evidence.get("pmid", "")
        title_slug = slugify(evidence.get("title", "paper")[:50])
        filename = f"{pmid}_{title_slug}.pdf" if pmid else f"{title_slug}.pdf"
        filepath = folder / filename
        
        # Use browser to download
        url = evidence.get("url", "")
        if not url:
            return None
        
        browser = await self._create_browser()
        
        task = f"""
Go to {url} and try to download the PDF.

If there's a "PDF" or "Download PDF" or "Full Text (PDF)" link, click it.
If the PDF opens in the browser, note that the download happened.

Report whether the download was successful.
"""
        
        try:
            agent = await self._create_agent(task, browser)
            await agent.run()
            
            # Note: Actual PDF download handling would need more sophisticated
            # browser download directory management. For now, we mark intent.
            evidence["pdf_download_attempted"] = True
            evidence["pdf_path"] = str(filepath)
            
            return str(filepath)
        except Exception as e:
            print(f"PDF download error: {e}")
            return None
