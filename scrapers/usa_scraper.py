"""USA Digital Therapeutics scraper using LLM-based research.

This module uses Azure OpenAI GPT to research and extract DTx information
for US companies from CSV input files.
"""
import asyncio
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from .base_scraper import BaseScraper


class USAScraper(BaseScraper):
    """Scraper for US Digital Therapeutics using LLM-based research.
    
    This scraper takes a CSV file with company information and uses
    Azure OpenAI GPT to research and extract DTx product information.
    """
    
    # System prompt for the LLM to research DTx information
    RESEARCH_SYSTEM_PROMPT = """You are a research assistant specializing in Digital Therapeutics (DTx) and healthcare technology.

Your task is to research a company and find ALL of their Digital Therapeutics products.

IMPORTANT: A Digital Therapeutic (DTx) is software that delivers evidence-based therapeutic interventions to treat, manage, or prevent medical conditions. This includes:
- Apps for treating mental health conditions (depression, anxiety, PTSD, etc.)
- Apps for neurological conditions (Parkinson's, dyspraxia, ADHD, etc.)
- Apps for chronic disease management (diabetes, chronic pain, insomnia, etc.)
- Apps for substance use disorders
- Apps for rehabilitation and physical therapy
- Any software/app that provides therapeutic treatment (not just tracking/monitoring)

For each company, you MUST find:
1. **ALL DTx Product Names**: List every digital therapeutic product/app they offer
2. **Clinical Indications**: What conditions each DTx treats (with ICD-10 codes)
3. **Regulatory Status**: FDA clearance (US), CE marking (EU), or other regulatory approvals
4. **App Store Presence**: URLs for Apple App Store and Google Play Store
5. **Product Description**: What the DTx does therapeutically
6. **Active Status**: Whether currently available

Research guidelines:
- Search thoroughly - companies often have MULTIPLE DTx products
- Look at the company's website, especially "Products", "Solutions", "DTx", or "Therapeutics" sections
- Include products even if they don't have FDA clearance (many DTx have CE marking or no regulatory clearance yet)
- For EU companies, look for CE marking instead of FDA approval
- Include pilot programs and products in development if they are therapeutic
- ICD-10 codes examples: G20 (Parkinson's), F82 (Dyspraxia/DCD), F32 (Depression), F41 (Anxiety), G47.0 (Insomnia)

Respond ONLY with a valid JSON object (no markdown, no explanations). The JSON must have this exact structure:
{
    "dtx_products": [
        {
            "dtx_name": "Product Name",
            "description": "Brief description of the therapeutic intervention",
            "clinical_area_icd10": ["G20", "F82"],
            "clinical_indications": "Parkinson's Disease, Dyspraxia",
            "fda_clearance": "510(k)" | "De Novo" | "PMA" | "Breakthrough Device" | "CE Mark" | "None" | "Unknown",
            "fda_clearance_number": "K123456" | null,
            "app_store_url": "https://apps.apple.com/..." | null,
            "play_store_url": "https://play.google.com/..." | null,
            "listing_status": "Active" | "Inactive" | "Pilot" | "Unknown",
            "price_usd": "199.00" | null
        }
    ],
    "company_info": {
        "company_website": "https://..." | null,
        "company_founding_year": 2015 | null,
        "headquarters": "City, Country" | null
    },
    "research_notes": "Any relevant notes about the research findings"
}

CRITICAL: If the company website mentions DTx products, you MUST include them. Do not return empty dtx_products if the company clearly has therapeutic apps."""

    def __init__(self, config_path: str = "config/usa.json"):
        """Initialize the USA DTx scraper.
        
        Args:
            config_path: Path to USA configuration file.
        """
        super().__init__(config_path)
        load_dotenv()
        
        self.csv_input_path = self.config.get("csv_input_path", "data-format/us_company.csv")
        self.output_file = self.config.get("output_file", "data/dtx_data_usa.json")
        self.llm_settings = self.config.get("llm_settings", {})
        self.csv_mappings = self.config.get("csv_column_mappings", {})
        
        # Initialize the LLM
        self.llm = self._setup_llm()
    
    def _setup_llm(self) -> AzureChatOpenAI:
        """Setup the Azure OpenAI LLM for research."""
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
        return AzureChatOpenAI(
            model=deployment,
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
            temperature=self.llm_settings.get("temperature", 0.1),
            max_tokens=self.llm_settings.get("max_tokens", 4000),
        )
    
    def _get_column_value(self, row: Dict, column_names: List[str]) -> Optional[str]:
        """Get value from row using multiple possible column names.
        
        Args:
            row: CSV row dictionary.
            column_names: List of possible column names to try.
            
        Returns:
            Value from the first matching column, or None.
        """
        for col in column_names:
            if col in row and row[col]:
                return row[col].strip()
        return None
    
    def read_csv(self, csv_path: str = None) -> List[Dict]:
        """Read company data from CSV file.
        
        Args:
            csv_path: Path to CSV file (overrides config).
            
        Returns:
            List of company dictionaries.
        """
        path = Path(csv_path or self.csv_input_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")
        
        companies = []
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Extract company info using column mappings
                company = {
                    "company_name": self._get_column_value(
                        row, 
                        self.csv_mappings.get("company_name", ["CompanyName"])
                    ),
                    "company_id": self._get_column_value(
                        row, 
                        self.csv_mappings.get("company_id", ["CompanyID"])
                    ),
                    "website": self._get_column_value(
                        row, 
                        self.csv_mappings.get("website", ["Website"])
                    ),
                    "year_founded": self._get_column_value(
                        row, 
                        self.csv_mappings.get("year_founded", ["YearFounded"])
                    ),
                    "description": self._get_column_value(
                        row, 
                        self.csv_mappings.get("description", ["Description"])
                    ),
                    "hq_country": self._get_column_value(
                        row, 
                        self.csv_mappings.get("hq_country", ["HQCountry"])
                    ),
                    # Store full row for additional data
                    "_raw": row
                }
                
                # Only include if we have a company name
                if company["company_name"]:
                    companies.append(company)
        
        return companies
    
    async def research_company(self, company: Dict) -> Dict:
        """Use LLM to research DTx information for a company.
        
        Args:
            company: Company dictionary with basic info.
            
        Returns:
            Dictionary with researched DTx information.
        """
        company_name = company.get("company_name", "Unknown")
        website = company.get("website", "")
        description = company.get("description", "")
        
        # Build the research prompt
        research_prompt = f"""Research the following company for ALL Digital Therapeutics (DTx) products:

**Company Name**: {company_name}
**Website**: {website if website else 'Not provided'}
**Company Description**: {description if description else 'Not provided'}

IMPORTANT RESEARCH STEPS:
1. Visit the company website and look for ALL therapeutic products/apps
2. Check "Products", "Solutions", "DTx", "Therapeutics", "Applications" sections
3. Look for mobile apps on App Store and Google Play
4. Search for regulatory approvals (FDA in US, CE Mark in EU)
5. Find clinical indications and ICD-10 codes for each product

Include ALL products that provide therapeutic treatment for medical conditions:
- Mental health apps (depression, anxiety, PTSD, stress)
- Neurological condition apps (Parkinson's, dyspraxia, ADHD, dementia)
- Chronic disease apps (diabetes, pain management, insomnia)
- Rehabilitation apps (physical therapy, speech therapy)
- Substance use disorder apps
- Children's therapeutic apps (developmental disorders, wellbeing)

DO NOT skip products just because they lack FDA approval - many DTx have CE marking or are in pilot phase.
DO NOT skip products that are for children or specific populations.
List EVERY therapeutic app/product the company offers.

Return the research results as a JSON object with ALL found DTx products."""

        try:
            messages = [
                SystemMessage(content=self.RESEARCH_SYSTEM_PROMPT),
                HumanMessage(content=research_prompt)
            ]
            
            response = await self.llm.ainvoke(messages)
            response_text = response.content.strip()
            
            # Try to parse JSON from the response
            # Handle potential markdown code blocks
            if response_text.startswith("```"):
                # Extract JSON from code block
                lines = response_text.split("\n")
                json_lines = []
                in_json = False
                for line in lines:
                    if line.startswith("```json"):
                        in_json = True
                        continue
                    elif line.startswith("```"):
                        in_json = False
                        continue
                    if in_json:
                        json_lines.append(line)
                response_text = "\n".join(json_lines)
            
            research_result = json.loads(response_text)
            return research_result
            
        except json.JSONDecodeError as e:
            print(f"      JSON parse error: {e}")
            print(f"      Response was: {response_text[:500]}...")
            return {
                "dtx_products": [],
                "company_info": {},
                "research_notes": f"Failed to parse LLM response: {str(e)}"
            }
        except Exception as e:
            print(f"      LLM research error: {e}")
            return {
                "dtx_products": [],
                "company_info": {},
                "research_notes": f"Research failed: {str(e)}"
            }
    
    def _format_dtx_entry(self, company: Dict, product: Dict, company_info: Dict) -> Dict:
        """Format a single DTx product into the standard schema.
        
        Args:
            company: Original company data from CSV.
            product: DTx product data from LLM research.
            company_info: Company info from LLM research.
            
        Returns:
            Formatted DTx entry matching the schema.
        """
        # Parse founding year
        founding_year = None
        if company.get("year_founded"):
            try:
                founding_year = int(company["year_founded"])
            except (ValueError, TypeError):
                pass
        if not founding_year and company_info.get("company_founding_year"):
            founding_year = company_info.get("company_founding_year")
        
        return {
            "dtx_name": product.get("dtx_name", "Unknown"),
            "company_provider": company.get("company_name", "Unknown"),
            "company_website": company_info.get("company_website") or company.get("website"),
            "company_founding_year": founding_year,
            "listing_status": product.get("listing_status", "Unknown"),
            "date_of_first_listing": None,  # Not applicable for US
            "clinical_area_icd10": product.get("clinical_area_icd10", []),
            "clinical_indications": product.get("clinical_indications"),
            "dtx_category": None,  # Could be mapped from clinical area
            "description": product.get("description"),
            "app_store_url": product.get("app_store_url"),
            "play_store_url": product.get("play_store_url"),
            "web_app_url": None,
            "price_usd": product.get("price_usd"),
            "fda_clearance": product.get("fda_clearance"),
            "fda_clearance_number": product.get("fda_clearance_number"),
            "languages": ["English"],
            "trial_registration_ids": [],
            "reviews_playstore": None,
            "reviews_appstore": None,
            "source_url": company.get("website"),
            "last_scraped": datetime.utcnow().isoformat() + "Z",
            "reason_for_delisting": None
        }
    
    async def scrape(
        self,
        csv_path: str = None,
        company_filter: str = None,
        **kwargs
    ) -> Dict:
        """Main scraping method - research DTx info for all companies in CSV.
        
        Args:
            csv_path: Path to input CSV file (overrides config).
            company_filter: Optional company name to filter to.
            **kwargs: Additional arguments.
            
        Returns:
            Dictionary containing all researched DTx data.
        """
        print(f"Starting USA DTx research...")
        
        # Read companies from CSV
        companies = self.read_csv(csv_path)
        print(f"Loaded {len(companies)} companies from CSV")
        
        # Filter if specified
        if company_filter:
            companies = [
                c for c in companies 
                if company_filter.lower() in c.get("company_name", "").lower()
            ]
            print(f"Filtered to {len(companies)} companies matching '{company_filter}'")
        
        dtx_list = []
        companies_with_dtx = 0
        
        for i, company in enumerate(companies, 1):
            company_name = company.get("company_name", "Unknown")
            print(f"[{i}/{len(companies)}] Researching: {company_name}")
            
            try:
                # Research the company using LLM
                research_result = await self.research_company(company)
                
                products = research_result.get("dtx_products", [])
                company_info = research_result.get("company_info", {})
                
                if products:
                    companies_with_dtx += 1
                    print(f"    Found {len(products)} DTx product(s)")
                    
                    for product in products:
                        dtx_entry = self._format_dtx_entry(company, product, company_info)
                        dtx_list.append(dtx_entry)
                        print(f"      - {product.get('dtx_name', 'Unknown')}")
                else:
                    notes = research_result.get("research_notes", "")
                    print(f"    No DTx products found. {notes[:100]}")
                
            except Exception as e:
                print(f"    Error researching company: {e}")
            
            # Rate limiting - be respectful to the API
            await asyncio.sleep(2)
        
        # Build the result
        result = {
            "metadata": {
                "country": "USA",
                "last_updated": datetime.utcnow().isoformat() + "Z",
                "total_count": len(dtx_list),
                "companies_researched": len(companies),
                "companies_with_dtx": companies_with_dtx,
                "source": "LLM Research (Azure OpenAI)"
            },
            "dtx_list": dtx_list
        }
        
        print(f"\nResearch complete!")
        print(f"  Companies researched: {len(companies)}")
        print(f"  Companies with DTx: {companies_with_dtx}")
        print(f"  Total DTx products: {len(dtx_list)}")
        
        return result
    
    async def scrape_single_company(self, company_name: str, website: str = None) -> Dict:
        """Research a single company without CSV input.
        
        Args:
            company_name: Name of the company to research.
            website: Optional company website URL.
            
        Returns:
            Dictionary containing researched DTx data.
        """
        company = {
            "company_name": company_name,
            "website": website,
            "description": None,
            "year_founded": None
        }
        
        print(f"Researching single company: {company_name}")
        research_result = await self.research_company(company)
        
        products = research_result.get("dtx_products", [])
        company_info = research_result.get("company_info", {})
        
        dtx_list = []
        for product in products:
            dtx_entry = self._format_dtx_entry(company, product, company_info)
            dtx_list.append(dtx_entry)
        
        return {
            "metadata": {
                "country": "USA",
                "last_updated": datetime.utcnow().isoformat() + "Z",
                "total_count": len(dtx_list),
                "source": "LLM Research (Azure OpenAI)"
            },
            "dtx_list": dtx_list
        }
    
    def save_results(self, data: Dict, output_path: str = None):
        """Save research results to JSON file.
        
        Args:
            data: DTx data dictionary to save.
            output_path: Output file path (overrides config).
        """
        path = Path(output_path or self.output_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        print(f"Results saved to: {path}")
    
    def load_existing_data(self, output_path: str = None) -> Dict:
        """Load existing USA DTx data if available.
        
        Args:
            output_path: Path to existing data file.
            
        Returns:
            Existing data dictionary or empty structure.
        """
        path = Path(output_path or self.output_file)
        
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        
        return {
            "metadata": {
                "country": "USA",
                "last_updated": None,
                "total_count": 0
            },
            "dtx_list": []
        }
    
    def merge_results(self, existing_data: Dict, new_data: Dict) -> Dict:
        """Merge new research results with existing data.
        
        Updates existing DTx entries and adds new ones.
        
        Args:
            existing_data: Existing DTx data.
            new_data: New research results.
            
        Returns:
            Merged data dictionary.
        """
        # Create lookup by DTx name + company
        existing_lookup = {
            (d.get("dtx_name", ""), d.get("company_provider", "")): d
            for d in existing_data.get("dtx_list", [])
        }
        
        # Update or add new entries
        for dtx in new_data.get("dtx_list", []):
            key = (dtx.get("dtx_name", ""), dtx.get("company_provider", ""))
            existing_lookup[key] = dtx
        
        merged_list = list(existing_lookup.values())
        
        return {
            "metadata": {
                "country": "USA",
                "last_updated": datetime.utcnow().isoformat() + "Z",
                "total_count": len(merged_list),
                "source": "LLM Research (Azure OpenAI)"
            },
            "dtx_list": merged_list
        }
