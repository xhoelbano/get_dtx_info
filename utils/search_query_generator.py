"""LLM-based search query generator for evidence searches.

This module uses Azure OpenAI to generate intelligent search queries
for finding clinical evidence about DTx products.
"""
import json
import os
import re
from typing import List, Dict

from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage


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
        load_dotenv()
        self.llm = self._setup_llm()
    
    def _setup_llm(self) -> AzureChatOpenAI:
        """Setup the Azure OpenAI LLM."""
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
        return AzureChatOpenAI(
            model=deployment,
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
            temperature=0.1,
            max_tokens=500
        )
    
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
        
        Args:
            queries: List of queries from LLM.
            core_name: Clean core product name.
            
        Returns:
            Queries with exact quoted name first.
        """
        if not core_name:
            return queries
        
        exact_query = f'"{core_name}"'
        
        # Check if first query is already the exact name
        if queries and queries[0].strip('"') == core_name:
            # Already correct, just ensure quotes
            queries[0] = exact_query
            return queries
        
        # Remove any existing exact query from list
        queries = [q for q in queries if q.strip('"').lower() != core_name.lower()]
        
        # Insert exact query at beginning
        return [exact_query] + queries
    
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
        
        # Remove common German suffixes
        clean = re.sub(
            r'\s+(App|Therapie|für|bei|zur|die|der|das|Meine|aktive)\s*$',
            '', clean, flags=re.IGNORECASE
        ).strip()
        
        return clean
