"""LLM-based search query generator for evidence searches.

This module uses the configured LLM provider to generate intelligent
search queries for finding clinical evidence about DTx products.
"""
import json
import re
from typing import List, Dict

from langchain_core.messages import HumanMessage, SystemMessage

from .llm_provider import LLMProvider


class SearchQueryGenerator:
    """Generate optimized search queries for evidence databases.
    
    Uses LLM to understand DTx context and generate targeted queries
    that work well across PubMed, ClinicalTrials.gov, DRKS, and ISRCTN.
    """
    
    SYSTEM_PROMPT = """You are a clinical literature search expert. Your task is to generate 
effective search queries for finding clinical trials and research papers about digital therapeutics.

For each DTx product, generate queries that will find:
1. Randomized Controlled Trials (RCTs)
2. Real-World Evidence (RWE) studies
3. Clinical validation studies

CRITICAL RULES:
1. The FIRST query MUST be the exact core product name in QUOTES for exact phrase matching
   - Example: For "Cara Care für Reizdarm", first query = "\"Cara Care\""
   - Example: For "deprexis", first query = "\"deprexis\""
   - This ensures we find only studies mentioning the exact product name
2. Generate 3-5 SHORT, SIMPLE queries (under 50 characters each ideally)
3. Use ENGLISH terms only (translate German names if needed)
4. Focus on the EXACT product name - this is most important
5. DO NOT include generic condition terms without the product name
6. Avoid complex Boolean operators (AND/OR) - keep queries simple

Query types to generate:
- FIRST: Exact product name in quotes (e.g., "\"Cara Care\"")
- Product name + company (e.g., "Cara Care HiDoc")
- Product name + condition (e.g., "Cara Care IBS")
- Product name + study type (e.g., "Cara Care clinical trial")

WRONG examples (too generic, will return unrelated results):
- "IBS app" (no product name)
- "digital therapeutic depression" (no product name)
- "clinical trial irritable bowel" (no product name)

Return ONLY a JSON array of query strings. No explanations."""

    def __init__(self):
        """Initialize the query generator."""
        self.llm = LLMProvider.get_llm(temperature=0.1, max_tokens=500)
    
    async def generate_queries(self, dtx_data: Dict) -> List[str]:
        """Generate search queries for a DTx using LLM.
        
        Args:
            dtx_data: Dictionary containing DTx metadata.
            
        Returns:
            List of search query strings, with first query being exact quoted name.
        """
        dtx_name = dtx_data.get("dtx_name", "")
        dtx_name_de = dtx_data.get("dtx_name_de", "")
        company = dtx_data.get("company_provider", "")
        icd_codes = dtx_data.get("clinical_area_icd10", [])
        description = dtx_data.get("description", "")[:500]
        
        # Get core product name for exact matching
        core_name = self.clean_dtx_name_for_query(dtx_name)
        
        # Build the prompt
        prompt = f"""Generate search queries for this Digital Therapeutic:

Product Name: {dtx_name}
Core Product Name (use this for queries): {core_name}
German Name: {dtx_name_de if dtx_name_de != dtx_name else 'Same as above'}
Company: {company}
Clinical Areas (ICD-10): {', '.join(icd_codes[:5]) if icd_codes else 'Not specified'}
Description: {description}

Generate 4-5 short, effective search queries as a JSON array.
REMEMBER: First query MUST be the exact product name in quotes: "\"{core_name}\""
Example output: ["\\"deprexis\\"", "deprexis GAIA", "deprexis clinical trial", "deprexis depression"]"""

        try:
            messages = [
                SystemMessage(content=self.SYSTEM_PROMPT),
                HumanMessage(content=prompt)
            ]
            
            response = await self.llm.ainvoke(messages)
            content = response.content.strip()
            
            # Parse JSON from response
            if "```" in content:
                # Extract from code block
                match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', content, re.DOTALL)
                if match:
                    content = match.group(1)
            
            queries = json.loads(content)
            
            if isinstance(queries, list) and len(queries) >= 2:
                # Clean and limit queries
                queries = [str(q).strip()[:100] for q in queries if q]
                
                # Ensure first query is exact quoted name
                queries = self._ensure_exact_name_first(queries, core_name)
                
                return queries[:6]
            
        except Exception as e:
            print(f"    LLM query generation error: {e}")
        
        # Fallback to rule-based generation
        return self._generate_fallback_queries(dtx_data)
    
    def _ensure_exact_name_first(self, queries: List[str], core_name: str) -> List[str]:
        """Ensure the first query is the exact quoted product name.
        
        This is critical for exact phrase matching - the first query should always
        be the bare product name in quotes so APIs search for that exact phrase.
        
        Args:
            queries: List of queries from LLM.
            core_name: Clean core product name.
            
        Returns:
            Queries with exact quoted name first.
        """
        if not core_name or len(core_name) < 2:
            return queries
        
        # Create the exact quoted query
        exact_query = f'"{core_name}"'
        
        # Check if first query is already the exact quoted name
        first_clean = queries[0].strip('"').strip("'").strip() if queries else ""
        if first_clean.lower() == core_name.lower():
            # Already correct, just ensure proper double quotes
            queries[0] = exact_query
            return queries
        
        # Remove any existing exact query from list (case-insensitive)
        queries = [q for q in queries if q.strip('"').strip("'").strip().lower() != core_name.lower()]
        
        # Also ensure all other queries that contain the product name use proper quoting
        # when the product name appears as a phrase
        processed_queries = []
        for q in queries:
            # If this query is just the product name variations, ensure quotes
            q_clean = q.strip('"').strip("'").strip()
            if q_clean.lower() == core_name.lower():
                processed_queries.append(exact_query)
            else:
                processed_queries.append(q)
        
        # Insert exact query at beginning
        return [exact_query] + processed_queries
    
    def _generate_fallback_queries(self, dtx_data: Dict) -> List[str]:
        """Generate fallback queries without LLM.
        
        Args:
            dtx_data: Dictionary containing DTx metadata.
            
        Returns:
            List of search query strings, with first being exact quoted name.
        """
        queries = []
        
        dtx_name = dtx_data.get("dtx_name", "")
        company = dtx_data.get("company_provider", "")
        
        if dtx_name:
            # Get clean core name
            core_name = self.clean_dtx_name_for_query(dtx_name)
            
            if len(core_name) >= 3:
                # FIRST query: exact quoted name for precise matching
                queries.append(f'"{core_name}"')
                # Additional queries with product name
                queries.append(f"{core_name} clinical trial")
                queries.append(f"{core_name} study")
        
        if company and dtx_name:
            core_name = self.clean_dtx_name_for_query(dtx_name)
            # Extract company name
            clean_company = re.sub(
                r'\s+(GmbH|AG|Inc|Ltd|LLC|s\.r\.o\.).*$', 
                '', company, flags=re.IGNORECASE
            ).strip()
            clean_company = clean_company.split(",")[0].strip()
            clean_company = clean_company.split("\n")[-1].strip()
            
            if len(clean_company) > 4 and clean_company.lower() not in ["deutschland", "germany", "usa"]:
                # Include product name with company to avoid false positives
                queries.append(f"{core_name} {clean_company}")
        
        # Deduplicate
        seen = set()
        unique = []
        for q in queries:
            if q.lower() not in seen:
                seen.add(q.lower())
                unique.append(q)
        
        return unique[:5]
    
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
        
        # Remove trailing condition descriptions in German (e.g., "für Reizdarm", "bei Depression")
        clean = re.sub(
            r'\s+(für|bei|zur|gegen|im)\s+\w+(\s+\w+)?$',
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
