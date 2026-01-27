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

IMPORTANT RULES:
1. Generate 3-5 SHORT, SIMPLE queries (under 50 characters each ideally)
2. Use ENGLISH terms only (translate German names if needed)
3. Focus on the EXACT product name - this is most important
4. Include company name variations
5. Avoid complex Boolean operators (AND/OR) - keep queries simple
6. Each query should be slightly different to capture various study types

Query types to generate:
- Exact product name (quoted if multi-word)
- Product name + company
- Product name + clinical area/condition
- Product name + "clinical trial" or "study"
- Brand name variations (if applicable)

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
            List of search query strings.
        """
        dtx_name = dtx_data.get("dtx_name", "")
        dtx_name_de = dtx_data.get("dtx_name_de", "")
        company = dtx_data.get("company_provider", "")
        icd_codes = dtx_data.get("clinical_area_icd10", [])
        description = dtx_data.get("description", "")[:500]
        
        # Build the prompt
        prompt = f"""Generate search queries for this Digital Therapeutic:

Product Name: {dtx_name}
German Name: {dtx_name_de if dtx_name_de != dtx_name else 'Same as above'}
Company: {company}
Clinical Areas (ICD-10): {', '.join(icd_codes[:5]) if icd_codes else 'Not specified'}
Description: {description}

Generate 4-5 short, effective search queries as a JSON array.
Example output: ["deprexis depression", "deprexis GAIA", "deprexis clinical trial", "deprexis CBT app"]"""

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
                return queries[:6]
            
        except Exception as e:
            print(f"    LLM query generation error: {e}")
        
        # Fallback to rule-based generation
        return self._generate_fallback_queries(dtx_data)
    
    def _generate_fallback_queries(self, dtx_data: Dict) -> List[str]:
        """Generate fallback queries without LLM.
        
        Args:
            dtx_data: Dictionary containing DTx metadata.
            
        Returns:
            List of search query strings.
        """
        queries = []
        
        dtx_name = dtx_data.get("dtx_name", "")
        company = dtx_data.get("company_provider", "")
        
        if dtx_name:
            # Clean the name
            clean_name = re.sub(r'[®™]', '', dtx_name).strip()
            core_name = clean_name.split(" - ")[0].split(":")[0].strip()
            
            # Remove German suffixes
            core_name = re.sub(
                r'\s+(App|Therapie|für|bei|zur|die|der|das)\s*$', 
                '', core_name, flags=re.IGNORECASE
            ).strip()
            
            if len(core_name) >= 3:
                queries.append(core_name)
                queries.append(f"{core_name} clinical trial")
                queries.append(f"{core_name} digital therapeutic")
        
        if company:
            # Extract company name
            clean_company = re.sub(
                r'\s+(GmbH|AG|Inc|Ltd|LLC|s\.r\.o\.).*$', 
                '', company, flags=re.IGNORECASE
            ).strip()
            clean_company = clean_company.split(",")[0].strip()
            clean_company = clean_company.split("\n")[-1].strip()
            
            if len(clean_company) > 4 and clean_company.lower() not in ["deutschland", "germany", "usa"]:
                queries.append(f"{clean_company} digital health")
        
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
