"""Keyword generator for evidence searches."""
from typing import List, Dict


class KeywordGenerator:
    """Generate search keywords for finding DTx evidence."""
    
    # Templates for generating search queries
    TEMPLATES = [
        '"{dtx_name}" randomized controlled trial',
        '"{dtx_name}" RCT',
        '"{dtx_name}" clinical trial',
        '"{dtx_name}" real world evidence',
        '"{dtx_name}" digital therapeutic',
        '"{dtx_name}" {clinical_area}',
        '"{company_name}" digital therapeutic {clinical_area}',
        '"{dtx_name}" efficacy study',
        '"{dtx_name}" effectiveness',
    ]
    
    # Clinical area mappings from ICD-10 codes to readable terms
    ICD10_TO_CLINICAL_AREA = {
        "F": "mental health psychiatry",
        "F3": "mood disorders depression",
        "F4": "anxiety disorders",
        "F9": "ADHD attention deficit",
        "G": "neurological",
        "G43": "migraine headache",
        "I": "cardiovascular",
        "I10": "hypertension blood pressure",
        "J": "respiratory",
        "J44": "COPD",
        "K": "digestive gastrointestinal",
        "K58": "irritable bowel syndrome IBS",
        "M": "musculoskeletal",
        "M54": "back pain",
        "R": "symptoms signs",
    }
    
    def __init__(self):
        """Initialize the keyword generator."""
        pass
    
    def generate_queries(
        self, 
        dtx_data: Dict,
        max_queries: int = 5
    ) -> List[str]:
        """Generate search queries for a DTx.
        
        Args:
            dtx_data: Dictionary containing DTx information.
            max_queries: Maximum number of queries to generate.
            
        Returns:
            List of search query strings.
        """
        dtx_name = dtx_data.get("dtx_name", "")
        dtx_name_de = dtx_data.get("dtx_name_de", "")
        company_name = self._extract_company_name(dtx_data.get("company_provider", ""))
        icd_codes = dtx_data.get("clinical_area_icd10", [])
        
        # Get clinical area from ICD codes
        clinical_area = self._get_clinical_area(icd_codes)
        
        queries = []
        
        # Generate from templates
        for template in self.TEMPLATES:
            try:
                query = template.format(
                    dtx_name=dtx_name,
                    company_name=company_name,
                    clinical_area=clinical_area
                )
                # Only add if the query has meaningful content
                if dtx_name and query not in queries:
                    queries.append(query)
            except KeyError:
                continue
        
        # Add German name query if different
        if dtx_name_de and dtx_name_de != dtx_name:
            queries.append(f'"{dtx_name_de}" klinische studie')
        
        # Add trial registration number queries
        trial_ids = dtx_data.get("trial_registration_ids", [])
        for trial_id in trial_ids:
            if trial_id:
                queries.append(trial_id)
        
        return queries[:max_queries]
    
    def _extract_company_name(self, company_provider: str) -> str:
        """Extract company name from full provider string.
        
        Args:
            company_provider: Full provider string (e.g., "GAIA AG, Deutschland").
            
        Returns:
            Company name only.
        """
        if not company_provider:
            return ""
        return company_provider.split(",")[0].strip()
    
    def _get_clinical_area(self, icd_codes: List[str]) -> str:
        """Get clinical area description from ICD-10 codes.
        
        Args:
            icd_codes: List of ICD-10 codes.
            
        Returns:
            Clinical area description string.
        """
        if not icd_codes:
            return ""
        
        # Get the first code and look up clinical area
        primary_code = icd_codes[0] if icd_codes else ""
        
        # Try to match by prefix
        for prefix, area in self.ICD10_TO_CLINICAL_AREA.items():
            if primary_code.startswith(prefix):
                return area
        
        return ""
    
    def generate_pubmed_query(self, dtx_data: Dict) -> str:
        """Generate an optimized PubMed search query.
        
        Args:
            dtx_data: Dictionary containing DTx information.
            
        Returns:
            PubMed-optimized search query.
        """
        dtx_name = dtx_data.get("dtx_name", "")
        
        # PubMed-specific query with MeSH terms
        return f'("{dtx_name}"[Title/Abstract]) AND (randomized controlled trial[Publication Type] OR clinical trial[Publication Type])'
    
    def generate_scholar_query(self, dtx_data: Dict) -> str:
        """Generate an optimized Google Scholar search query.
        
        Args:
            dtx_data: Dictionary containing DTx information.
            
        Returns:
            Google Scholar-optimized search query.
        """
        dtx_name = dtx_data.get("dtx_name", "")
        clinical_area = self._get_clinical_area(
            dtx_data.get("clinical_area_icd10", [])
        )
        
        return f'"{dtx_name}" {clinical_area} clinical trial OR RCT'
