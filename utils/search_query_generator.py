"""Simple deterministic search query generator for evidence searches.

This module generates exactly 2 targeted queries for finding clinical evidence:
1. Exact quoted DTx product name
2. DTx name AND company name (for broader but targeted search)

This replaces the previous LLM-based approach which generated too many queries
leading to false positives.
"""
import re
from typing import List, Dict, Tuple

from .company_name import normalize_company_name


class SearchQueryGenerator:
    """Generate exactly 2 targeted search queries for evidence databases.
    
    Uses deterministic rules (no LLM) to create precise queries that work
    across PubMed, ClinicalTrials.gov, DRKS, and ISRCTN.
    
    Query Strategy:
    1. Query 1: Exact quoted product name (highest precision)
       Example: "deprexis"
    2. Query 2: Product name AND company (broader but still targeted)
       Example: "deprexis" AND "GAIA"
    """
    
    def __init__(self):
        """Initialize the query generator."""
        pass  # No LLM needed anymore
    
    async def generate_queries(self, dtx_data: Dict) -> List[str]:
        """Generate exactly 2 search queries for a DTx.
        
        Args:
            dtx_data: Dictionary containing DTx metadata.
            
        Returns:
            List of 1-2 search query strings.
        """
        dtx_name = dtx_data.get("dtx_name", "")
        company = dtx_data.get("company_provider", "")
        
        # Get clean names
        core_name = self.clean_dtx_name_for_query(dtx_name)
        clean_company = self._clean_company_name(company)
        
        queries = []
        
        # Query 1: Exact quoted product name (always include)
        if core_name and len(core_name) >= 2:
            queries.append(f'"{core_name}"')
        
        # Query 2: Product name AND company (if company is meaningful)
        if core_name and clean_company and len(clean_company) >= 3:
            # Avoid duplicate if company name is same as product name
            if clean_company.lower() not in core_name.lower():
                queries.append(f'"{core_name}" AND "{clean_company}"')
        
        return queries
    
    def generate_queries_sync(self, dtx_data: Dict) -> List[str]:
        """Synchronous version of generate_queries.
        
        Args:
            dtx_data: Dictionary containing DTx metadata.
            
        Returns:
            List of 1-2 search query strings.
        """
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self.generate_queries(dtx_data)
        )
    
    def get_query_components(self, dtx_data: Dict) -> Tuple[str, str]:
        """Get the core name and company name components.
        
        Useful for scrapers that need to build queries differently
        based on their API syntax.
        
        Args:
            dtx_data: Dictionary containing DTx metadata.
            
        Returns:
            Tuple of (core_product_name, clean_company_name)
        """
        dtx_name = dtx_data.get("dtx_name", "")
        company = dtx_data.get("company_provider", "")
        
        core_name = self.clean_dtx_name_for_query(dtx_name)
        clean_company = self._clean_company_name(company)
        
        return core_name, clean_company
    
    def _clean_company_name(self, company: str) -> str:
        """Extract a clean company name suitable for searching.
        
        Delegates to the shared ``normalize_company_name`` (which strips DiGA
        status notes / boilerplate / product-name fragments and keeps the real
        manufacturer). Then trims the trailing legal-entity suffix so the query
        term is the distinctive part of the name.
        
        Args:
            company: Raw or normalized company/provider string.
            
        Returns:
            Cleaned company name for searching, or "" when none can be derived.
        """
        clean = normalize_company_name(company)
        if not clean:
            return ""
        
        # Drop a trailing legal-entity suffix so the search term is the
        # distinctive part of the name (keep the rest of the name intact).
        clean = re.sub(
            r'\s*(GmbH|mbH|AG|UG|SE|KG|e\.V\.|B\.V\.|s\.r\.o\.|Ltd\.?|Inc\.?|LLC|Corp\.?)\s*$',
            '', clean, flags=re.IGNORECASE
        ).strip()
        
        # Guard: never return a bare generic word as the company term.
        generic = {
            "deutschland", "germany", "usa", "europe", "international",
            "software", "health", "healthcare", "digital", "medical",
            "therapeutics", "therapy", "solutions", "services", "technologies",
        }
        if clean.lower() in generic:
            return ""
        
        return clean
    
    def clean_dtx_name_for_query(self, dtx_name: str) -> str:
        """Extract clean product name suitable for searching.
        
        Args:
            dtx_name: Full DTx name.
            
        Returns:
            Cleaned name for searching.
        """
        if not dtx_name:
            return ""
        
        # Remove trademark symbols
        clean = re.sub(r'[®™]', '', dtx_name).strip()
        
        # Take first part before separators
        clean = clean.split(" - ")[0].split(":")[0].strip()
        
        # Remove trailing condition descriptions in German 
        # (e.g., "für Reizdarm", "bei Depression")
        clean = re.sub(
            r'\s+(für|bei|zur|gegen|im)\s+\w+(\s+\w+)*$',
            '', clean, flags=re.IGNORECASE
        ).strip()
        
        # Remove common German suffixes and articles
        clean = re.sub(
            r'\s+(App|Therapie|die|der|das|Meine|aktive|im\s+Erwachsenenalter)\s*$',
            '', clean, flags=re.IGNORECASE
        ).strip()
        
        # Remove trailing descriptors like "powered by X" or "proved by X"
        clean = re.sub(
            r'\s+(powered|proved|certified)\s+by\s+.*$',
            '', clean, flags=re.IGNORECASE
        ).strip()
        
        return clean
    
    def format_query_for_source(self, query: str, source: str) -> str:
        """Format a query for a specific evidence source.
        
        Different sources have different query syntax requirements.
        
        Args:
            query: Base query string (e.g., '"deprexis" AND "GAIA"')
            source: Source name ('pubmed', 'clinicaltrials', 'drks', 'isrctn')
            
        Returns:
            Query formatted for the specific source.
        """
        source = source.lower()
        
        if source == "pubmed":
            # PubMed uses standard Boolean syntax
            return query
        
        elif source == "clinicaltrials":
            # ClinicalTrials.gov API v2 uses similar syntax
            return query
        
        elif source == "drks":
            # DRKS search - convert AND to space for combined search
            # The form search typically works better with space-separated terms
            return query.replace(" AND ", " ")
        
        elif source == "isrctn":
            # ISRCTN - similar to DRKS
            return query.replace(" AND ", " ")
        
        return query
    
    def ensure_quoted(self, query: str) -> str:
        """Ensure a query term is wrapped in double quotes for exact phrase matching.
        
        Args:
            query: Query string that may or may not have quotes.
            
        Returns:
            Query with double quotes for exact phrase matching.
        """
        # Strip existing quotes (single or double)
        clean = query.strip().strip('"').strip("'").strip()
        
        if not clean:
            return query
        
        # Return with double quotes
        return f'"{clean}"'
