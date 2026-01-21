"""Evidence scraper for finding RCT/RWE papers.

Uses:
- PubMed E-utilities API (free, reliable) for paper search
- LLM for intelligent search query generation (translates German, understands clinical context)
- LLM for RCT/RWE classification when keywords are ambiguous
"""
import asyncio
import json
import os
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import httpx
from dotenv import load_dotenv

# LLM for query generation
try:
    from langchain_openai import AzureChatOpenAI
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False
    print("Warning: langchain-openai not available. Using rule-based query generation.")

# browser-use for Google Scholar (optional)
try:
    from browser_use import Agent, Browser
    BROWSER_USE_AVAILABLE = True
except ImportError:
    BROWSER_USE_AVAILABLE = False


class EvidenceScraper:
    """Scraper for finding RCT/RWE evidence from PubMed and Google Scholar.
    
    Strategy:
    - PubMed: Use E-utilities API (free, no rate limits for reasonable use)
    - Google Scholar: Use browser-use (simple search, LLM handles extraction)
    - Classification: Keywords first, then LLM for ambiguous cases
    """
    
    # PubMed E-utilities base URLs
    PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    
    # Keywords for RCT/RWE classification
    RCT_KEYWORDS = [
        "randomized", "randomised", "rct", "controlled trial",
        "double-blind", "double blind", "placebo-controlled",
        "clinical trial", "randomization", "randomisation",
        "phase ii", "phase iii", "phase 2", "phase 3"
    ]
    
    RWE_KEYWORDS = [
        "real-world", "real world", "observational",
        "retrospective", "registry", "claims data",
        "electronic health record", "ehr", "routine care",
        "cohort study", "cross-sectional", "case-control",
        "pragmatic trial", "naturalistic"
    ]
    
    # Generic terms that don't indicate the DTx is mentioned
    GENERIC_TERMS = [
        "digital therapeutic", "digital health", "mobile app",
        "smartphone app", "mhealth", "ehealth", "telemedicine",
        "telehealth", "mobile health", "app-based", "web-based"
    ]
    
    def __init__(self, config_path: str = "config/germany.json"):
        """Initialize the evidence scraper."""
        load_dotenv()
        
        self.config = {}
        if config_path and Path(config_path).exists():
            with open(config_path, "r", encoding="utf-8") as f:
                self.config = json.load(f)
        
        self.evidence_docs_dir = Path("evidence-docs")
        self.evidence_docs_dir.mkdir(parents=True, exist_ok=True)
        
        # HTTP client for API calls
        self._http_client: Optional[httpx.AsyncClient] = None
        
        # browser-use resources (for Google Scholar)
        self._browser: Optional[Browser] = None
        self._llm = None
        
        # Setup LLM for query generation (always needed)
        if LLM_AVAILABLE:
            self._setup_llm()
    
    def _setup_llm(self):
        """Setup Azure OpenAI LLM for query generation and classification."""
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
        self._llm = AzureChatOpenAI(
            model=deployment,
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
        )
    
    async def generate_search_queries_llm(self, dtx_data: Dict) -> List[str]:
        """Use LLM to generate optimal PubMed search queries.
        
        The LLM understands:
        - German to English translation
        - Clinical terminology from ICD codes
        - Brand names vs clinical terms
        - PubMed search syntax
        
        Args:
            dtx_data: Dictionary containing DTx information.
            
        Returns:
            List of search query strings optimized for PubMed.
        """
        if not self._llm:
            return []
        
        dtx_name = dtx_data.get("dtx_name", "")
        company = dtx_data.get("company_provider", "")
        icd_codes = dtx_data.get("clinical_area_icd10", [])
        description = dtx_data.get("description", "")[:800]  # Truncate for token limits
        
        prompt = f"""You are a medical literature search expert. Generate 3-4 PubMed search queries 
to find clinical evidence (RCTs and real-world studies) about this SPECIFIC Digital Therapeutic app:

App Name: {dtx_name}
Company: {company}
Clinical Area (ICD-10 codes): {icd_codes}
Description: {description}

IMPORTANT RULES:
1. Focus on finding papers that SPECIFICALLY mention this app by name
2. Use ENGLISH terms only (translate German to English)  
3. Keep queries SIMPLE - avoid complex AND/OR combinations
4. Each query should be SHORT (under 50 characters ideally)
5. PRIORITIZE the exact app name in quotes - this is most important
6. Query types to use:
   - Exact app name in quotes (e.g., "Kaia COPD")
   - App name + company (e.g., "Kaia health COPD")
   - App name + clinical term (e.g., "Kaia app chronic pain")

Return ONLY a JSON array of 3-4 simple query strings.
Example for "Kaia COPD: Meine aktive COPD Therapie": ["Kaia COPD app", "Kaia health COPD", "Kaia COPD pulmonary rehabilitation"]"""

        try:
            response = await self._llm.ainvoke(prompt)
            content = response.content.strip()
            
            # Parse JSON array from response
            # Handle potential markdown code blocks
            if "```" in content:
                match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', content, re.DOTALL)
                if match:
                    content = match.group(1)
            
            queries = json.loads(content)
            
            if isinstance(queries, list) and len(queries) >= 2:
                # Ensure queries are strings and not too long
                queries = [str(q)[:200] for q in queries if q]
                print(f"    LLM generated {len(queries)} queries")
                return queries[:5]
                
        except json.JSONDecodeError as e:
            print(f"    LLM query parse error: {e}")
        except Exception as e:
            print(f"    LLM query generation error: {e}")
        
        return []
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client for API calls."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client
    
    async def close(self):
        """Clean up resources."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        
        if self._browser:
            await self._browser.close()
            self._browser = None
    
    def _generate_search_queries_fallback(self, dtx_data: Dict) -> List[str]:
        """Rule-based fallback for query generation (if LLM fails).
        
        Args:
            dtx_data: Dictionary containing DTx information.
            
        Returns:
            List of search query strings.
        """
        dtx_name = dtx_data.get("dtx_name", "")
        company = dtx_data.get("company_provider", "").split(",")[0].strip()
        if "\n" in company:
            company = company.split("\n")[-1].strip()
        
        queries = []
        
        if dtx_name:
            clean_name = re.sub(r'[®™]', '', dtx_name).strip()
            core_name = clean_name.split(" - ")[0].split(":")[0].strip()
            core_name = re.sub(r'\s+(App|Therapie|für|bei|zur|die|der|das)\s*$', '', core_name, flags=re.IGNORECASE)
            
            brand_match = re.match(r'^([A-Za-z]+(?:\s+[A-Za-z]+)?)', core_name)
            brand_name = brand_match.group(1) if brand_match else None
            
            if len(core_name) >= 3:
                queries.append(f'"{core_name}" digital therapeutic')
                queries.append(f'"{core_name}" randomized trial')
            
            if brand_name and brand_name != core_name and len(brand_name) >= 5:
                queries.append(f'"{brand_name}" digital therapeutic')
        
        if company and len(company) > 5:
            clean_company = re.sub(r'\s+(GmbH|AG|Inc|Ltd|LLC|s\.r\.o\.).*$', '', company, flags=re.IGNORECASE).strip()
            if len(clean_company) > 4 and clean_company.lower() not in ["deutschland", "germany"]:
                queries.append(f'"{clean_company}" digital health')
        
        seen = set()
        unique_queries = []
        for q in queries:
            if q.lower() not in seen:
                seen.add(q.lower())
                unique_queries.append(q)
        
        return unique_queries[:4]
    
    async def generate_search_queries(self, dtx_data: Dict) -> List[str]:
        """Generate search queries for a DTx using LLM with fallback.
        
        Tries LLM-based generation first (smarter, handles translation),
        falls back to rule-based if LLM fails.
        
        Args:
            dtx_data: Dictionary containing DTx information.
            
        Returns:
            List of search query strings optimized for PubMed.
        """
        # Try LLM-based generation first
        if self._llm:
            try:
                queries = await self.generate_search_queries_llm(dtx_data)
                if queries and len(queries) >= 2:
                    return queries
            except Exception as e:
                print(f"    LLM failed, using fallback: {e}")
        
        # Fallback to rule-based
        print("    Using rule-based query generation")
        return self._generate_search_queries_fallback(dtx_data)
    
    async def search_evidence(self, dtx_data: Dict) -> Dict:
        """Search for evidence papers for a DTx.
        
        Args:
            dtx_data: Dictionary containing DTx information.
            
        Returns:
            Dictionary with RCT and RWE evidence lists.
        """
        all_evidence = []
        dtx_name = dtx_data.get("dtx_name", "Unknown")
        
        # Generate queries using LLM (with fallback)
        queries = await self.generate_search_queries(dtx_data)
        
        print(f"  Searching with {len(queries)} queries...")
        
        for i, query in enumerate(queries):
            print(f"    Query {i+1}/{len(queries)}: {query[:60]}...")
            
            # Search PubMed via API
            pubmed_results = await self.search_pubmed_api(query)
            print(f"      PubMed: {len(pubmed_results)} results")
            all_evidence.extend(pubmed_results)
            
            await asyncio.sleep(0.5)  # Rate limiting for PubMed API
        
        # Deduplicate by title similarity
        unique_evidence = self._deduplicate_evidence(all_evidence)
        print(f"  Total unique papers: {len(unique_evidence)}")
        
        # Filter to only papers that actually mention this DTx
        relevant_evidence, removed_count = await self._filter_relevant_evidence(unique_evidence, dtx_data)
        if removed_count > 0:
            print(f"  Relevance filter: kept {len(relevant_evidence)}, removed {removed_count} irrelevant papers")
        
        # Classify each paper as RCT or RWE
        rct_papers = []
        rwe_papers = []
        
        for evidence in relevant_evidence:
            evidence_type = self._classify_evidence_type(evidence)
            evidence["evidence_type"] = evidence_type
            evidence["dtx_name"] = dtx_name
            
            if evidence_type == "RCT":
                rct_papers.append(evidence)
            else:
                rwe_papers.append(evidence)
        
        print(f"  Classification: {len(rct_papers)} RCT, {len(rwe_papers)} RWE")
        
        return {
            "dtx_name": dtx_name,
            "search_date": datetime.utcnow().isoformat() + "Z",
            "queries_used": queries,
            "RCT": rct_papers,
            "RWE": rwe_papers
        }
    
    async def search_pubmed_api(self, query: str, max_results: int = 20) -> List[Dict]:
        """Search PubMed using E-utilities API.
        
        Args:
            query: Search query string.
            max_results: Maximum number of results to return.
            
        Returns:
            List of evidence dictionaries.
        """
        client = await self._get_http_client()
        
        try:
            # Step 1: Search for PMIDs
            search_params = {
                "db": "pubmed",
                "term": query,
                "retmax": max_results,
                "retmode": "json",
                "sort": "relevance"
            }
            
            search_response = await client.get(self.PUBMED_ESEARCH, params=search_params)
            search_response.raise_for_status()
            search_data = search_response.json()
            
            pmids = search_data.get("esearchresult", {}).get("idlist", [])
            
            if not pmids:
                return []
            
            # Step 2: Fetch paper details
            fetch_params = {
                "db": "pubmed",
                "id": ",".join(pmids),
                "retmode": "xml",
                "rettype": "abstract"
            }
            
            fetch_response = await client.get(self.PUBMED_EFETCH, params=fetch_params)
            fetch_response.raise_for_status()
            
            # Parse XML response
            return self._parse_pubmed_xml(fetch_response.text)
            
        except Exception as e:
            print(f"      PubMed API error: {e}")
            return []
    
    def _parse_pubmed_xml(self, xml_text: str) -> List[Dict]:
        """Parse PubMed XML response into evidence dictionaries."""
        results = []
        
        try:
            root = ET.fromstring(xml_text)
            
            for article in root.findall(".//PubmedArticle"):
                try:
                    medline = article.find(".//MedlineCitation")
                    if medline is None:
                        continue
                    
                    pmid = medline.findtext("PMID", "")
                    
                    article_elem = medline.find(".//Article")
                    if article_elem is None:
                        continue
                    
                    # Title
                    title = article_elem.findtext(".//ArticleTitle", "")
                    
                    # Abstract
                    abstract_elem = article_elem.find(".//Abstract")
                    abstract = ""
                    if abstract_elem is not None:
                        abstract_parts = []
                        for text_elem in abstract_elem.findall(".//AbstractText"):
                            label = text_elem.get("Label", "")
                            text = text_elem.text or ""
                            if label:
                                abstract_parts.append(f"{label}: {text}")
                            else:
                                abstract_parts.append(text)
                        abstract = " ".join(abstract_parts)
                    
                    # Authors
                    authors = []
                    for author in article_elem.findall(".//Author"):
                        lastname = author.findtext("LastName", "")
                        forename = author.findtext("ForeName", "")
                        if lastname:
                            authors.append(f"{lastname} {forename}".strip())
                    author_str = authors[0] + " et al." if len(authors) > 1 else ", ".join(authors)
                    
                    # Publication date
                    pub_date = article_elem.find(".//PubDate")
                    year = pub_date.findtext("Year", "") if pub_date is not None else ""
                    
                    # Journal
                    journal = article_elem.findtext(".//Journal/Title", "")
                    
                    # DOI
                    doi = ""
                    for article_id in article.findall(".//ArticleId"):
                        if article_id.get("IdType") == "doi":
                            doi = article_id.text or ""
                            break
                    
                    # Publication types (useful for classification)
                    pub_types = []
                    for pt in medline.findall(".//PublicationType"):
                        if pt.text:
                            pub_types.append(pt.text)
                    
                    results.append({
                        "title": title,
                        "authors": author_str,
                        "publication_year": year,
                        "journal": journal,
                        "pmid": pmid,
                        "doi": doi,
                        "abstract": abstract[:1000] if abstract else "",  # Truncate long abstracts
                        "publication_types": pub_types,
                        "source": "PubMed",
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                    })
                    
                except Exception as e:
                    continue  # Skip problematic articles
                    
        except ET.ParseError as e:
            print(f"      XML parse error: {e}")
        
        return results
    
    async def search_google_scholar(self, query: str, enabled: bool = False) -> List[Dict]:
        """Search Google Scholar using browser-use.
        
        Note: Google Scholar often blocks automated access. This is disabled
        by default. Use PubMed API as the primary source.
        
        Args:
            query: Search query string.
            enabled: Whether to actually run the search (default False).
            
        Returns:
            List of evidence dictionaries.
        """
        if not enabled or not BROWSER_USE_AVAILABLE or self._llm is None:
            return []
        
        encoded_query = quote_plus(query)
        url = f"https://scholar.google.com/scholar?q={encoded_query}"
        
        task = f"""
Go to {url} and extract the search results.

If you see a CAPTCHA or are blocked, return an empty array: []

For each result on the page (up to 10), extract:
1. Title of the paper
2. Authors
3. Publication year  
4. Number of citations (the number after "Cited by")
5. Source/Journal name
6. Whether there's a PDF link (look for [PDF] tag)

Return ONLY a JSON array like this:
[
  {{
    "title": "Paper title here",
    "authors": "Author names",
    "publication_year": "2024",
    "citations": 42,
    "journal": "Journal name",
    "has_pdf": true,
    "source": "Google Scholar"
  }}
]

Return ONLY the JSON array, nothing else.
"""
        
        try:
            if self._browser is None:
                self._browser = Browser()
            
            agent = Agent(
                task=task,
                llm=self._llm,
                browser=self._browser,
            )
            
            history = await agent.run()
            return self._extract_json_from_response(history)
            
        except Exception as e:
            print(f"      Google Scholar error: {e}")
            return []
    
    def _extract_json_from_response(self, history) -> List[Dict]:
        """Extract JSON array from browser-use agent response."""
        try:
            if hasattr(history, 'final_result'):
                text = str(history.final_result())
            elif hasattr(history, 'result'):
                text = str(history.result)
            else:
                text = str(history)
            
            # Find JSON array in response
            match = re.search(r'\[[\s\S]*?\]', text)
            if match:
                data = json.loads(match.group())
                # Add source field if missing
                for item in data:
                    if "source" not in item:
                        item["source"] = "Google Scholar"
                return data
        except (json.JSONDecodeError, Exception):
            pass
        
        return []
    
    def _deduplicate_evidence(self, evidence_list: List[Dict]) -> List[Dict]:
        """Deduplicate evidence by title similarity."""
        seen_titles = set()
        unique = []
        
        for evidence in evidence_list:
            # Normalize title for comparison
            title = evidence.get("title", "").lower()
            title_normalized = re.sub(r'[^\w\s]', '', title)[:100]
            
            if title_normalized and title_normalized not in seen_titles:
                seen_titles.add(title_normalized)
                unique.append(evidence)
        
        return unique
    
    def _extract_dtx_identifiers(self, dtx_data: Dict) -> List[str]:
        """Extract key identifiers from DTx for relevance matching.
        
        A paper is considered relevant ONLY if it mentions the app name.
        No hardcoded translations - we just extract what's in the data.
        
        Args:
            dtx_data: Dictionary containing DTx information.
            
        Returns:
            List of identifier strings (lowercased) to match against.
        """
        identifiers = []
        
        dtx_name = dtx_data.get("dtx_name", "")
        
        if dtx_name:
            # Remove trademark symbols
            clean_name = re.sub(r'[®™]', '', dtx_name).strip()
            
            # Extract core name (before " - " or ":")
            # e.g., "Kaia COPD" from "Kaia COPD: Meine aktive COPD Therapie"
            core_name = clean_name.split(" - ")[0].split(":")[0].strip()
            
            # Remove common German suffixes that aren't part of the product name
            core_name = re.sub(r'\s+(App|Therapie|für|bei|zur|die|der|das)\s*$', '', 
                             core_name, flags=re.IGNORECASE).strip()
            
            # The core name is the PRIMARY identifier
            if len(core_name) >= 3:
                identifiers.append(core_name.lower())
        
        # Remove duplicates while preserving order
        seen = set()
        unique_identifiers = []
        for ident in identifiers:
            if ident not in seen and len(ident) >= 3:
                seen.add(ident)
                unique_identifiers.append(ident)
        
        return unique_identifiers
    
    def _is_relevant_keyword_match(self, evidence: Dict, dtx_identifiers: List[str]) -> bool:
        """Quick keyword check if paper mentions the DTx identifier."""
        title = evidence.get("title", "").lower()
        abstract = evidence.get("abstract", "").lower()
        text = title + " " + abstract
        
        for identifier in dtx_identifiers:
            if identifier in text:
                return True
        return False
    
    async def _is_relevant_llm(self, evidence: Dict, dtx_name: str) -> bool:
        """Use LLM to check if paper is about the specific DTx app."""
        if not self._llm:
            return False
        
        title = evidence.get("title", "")
        abstract = evidence.get("abstract", "")[:500]  # Truncate for tokens
        
        prompt = f"""Is this paper specifically about the digital therapeutic app "{dtx_name}"?

Paper title: {title}
Abstract: {abstract}

Answer ONLY "yes" or "no". The paper must be specifically about this app (not just about the same medical condition)."""

        try:
            response = await self._llm.ainvoke(prompt)
            answer = response.content.strip().lower()
            return answer.startswith("yes")
        except Exception:
            return False
    
    async def _filter_relevant_evidence(self, evidence_list: List[Dict], dtx_data: Dict) -> Tuple[List[Dict], int]:
        """Filter evidence to only include papers about this specific DTx.
        
        Uses two-stage filtering:
        1. Quick keyword match (exact DTx name in text)
        2. LLM check for papers that might use different terminology
        
        Args:
            evidence_list: List of evidence dictionaries.
            dtx_data: Dictionary containing DTx information.
            
        Returns:
            Tuple of (filtered list, count of removed papers).
        """
        dtx_identifiers = self._extract_dtx_identifiers(dtx_data)
        dtx_name = dtx_data.get("dtx_name", "Unknown")
        
        if not dtx_identifiers:
            print(f"    Warning: No identifiers extracted for {dtx_name}")
            return evidence_list, 0
        
        print(f"    Filtering for relevance (identifier: {dtx_identifiers[0]})...")
        
        relevant = []
        needs_llm_check = []
        
        # First pass: quick keyword matching
        for evidence in evidence_list:
            if self._is_relevant_keyword_match(evidence, dtx_identifiers):
                relevant.append(evidence)
            else:
                needs_llm_check.append(evidence)
        
        # Second pass: LLM check for papers without exact keyword match
        if needs_llm_check and self._llm:
            print(f"    LLM checking {len(needs_llm_check)} papers without exact match...")
            for evidence in needs_llm_check:
                if await self._is_relevant_llm(evidence, dtx_name):
                    relevant.append(evidence)
        
        removed = len(evidence_list) - len(relevant)
        return relevant, removed
    
    def _classify_evidence_type(self, evidence: Dict) -> str:
        """Classify evidence as RCT or RWE.
        
        Uses:
        1. PubMed publication types (most reliable)
        2. Keyword matching in title/abstract
        3. Default to "Unknown" if unclear
        
        Args:
            evidence: Evidence dictionary.
            
        Returns:
            "RCT" or "RWE" classification.
        """
        # Check PubMed publication types first (most reliable)
        pub_types = evidence.get("publication_types", [])
        pub_types_lower = [pt.lower() for pt in pub_types]
        
        for pt in pub_types_lower:
            if "randomized controlled trial" in pt:
                return "RCT"
            if "clinical trial" in pt and "phase" in pt:
                return "RCT"
            if "observational" in pt or "cohort" in pt:
                return "RWE"
        
        # Keyword matching in title and abstract
        text = (
            evidence.get("title", "") + " " + 
            evidence.get("abstract", "")
        ).lower()
        
        rct_score = sum(1 for kw in self.RCT_KEYWORDS if kw in text)
        rwe_score = sum(1 for kw in self.RWE_KEYWORDS if kw in text)
        
        if rct_score > rwe_score:
            return "RCT"
        elif rwe_score > rct_score:
            return "RWE"
        elif rct_score > 0:
            return "RCT"  # Tie goes to RCT if any RCT keywords present
        else:
            return "Unknown"  # Truly ambiguous
    
    async def scrape(self, dtx_data: Dict, **kwargs) -> Dict:
        """Main scraping method - find evidence for a DTx.
        
        Args:
            dtx_data: Dictionary containing DTx information.
            **kwargs: Additional arguments.
            
        Returns:
            Dictionary with RCT and RWE evidence.
        """
        return await self.search_evidence(dtx_data)
