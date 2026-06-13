"""USA Digital Therapeutics scraper using LLM-based research.

This module uses the configured LLM provider to research and extract DTx
information for US companies from CSV input files.

The extraction schema is driven entirely by data-format/dtx_research.json — to
add, remove, or rename fields, edit that file (no code changes needed). The
model, provider, and web-search behavior are all controlled via .env.
"""
import asyncio
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .base_scraper import BaseScraper
from utils.llm_provider import LLMProvider
from utils.llm_metrics import aggregate, invoke_with_metrics
from utils.json_extract import parse_research_response


# Single source of truth for the DTx research extraction schema.
RESEARCH_SCHEMA_PATH = Path("data-format/dtx_research.json")

# System prompt template. The schema JSON is injected (not hardcoded) so the
# prompt always matches data-format/dtx_research.json.
RESEARCH_SYSTEM_PROMPT_TEMPLATE = """\
You are a research assistant specializing in Digital Therapeutics (DTx) and healthcare technology.

Your task is to research a single company and identify its Digital Therapeutic (DTx) products.

WHAT COUNTS AS A DTx:
A Digital Therapeutic is software that delivers an evidence-based therapeutic intervention to \
treat, manage, or prevent a medical condition (e.g. mental health, neurological, chronic disease, \
substance use, rehabilitation). Pure trackers, wellness/lifestyle apps, booking tools, and \
generic remote-monitoring dashboards are NOT DTx unless they deliver an actual therapeutic intervention.

WHAT COUNTS AS ONE PRODUCT:
- Treat each distinct, separately-branded product or app as ONE entry.
- If the company sells a single platform, return ONE entry for the platform. Only split a platform \
into multiple entries when each disease-specific module is independently branded and marketed as a \
distinct product. Do NOT inflate the count by listing every indication of one product separately.

GROUNDING RULES:
- If you have a web-search tool available, use it to verify product names, store URLs, and status. \
Prefer information you can confirm from the company website or app stores.
- Provide a "source_url" for every product: the page the information came from. If you cannot find \
a real source, do not invent the product.
- Do NOT fabricate App Store / Play Store URLs. Use null if you cannot confirm a real listing.
- For "clinical_area_icd10", give the single primary ICD-10 code unless the product is clearly \
multi-indication. Examples: G20 (Parkinson's), F82 (Dyspraxia/DCD), F32 (Depression), \
F41 (Anxiety), G47.0 (Insomnia).
- It is acceptable and correct to return an empty "dtx_products" list if the company has no genuine \
DTx product. Do not pad the result.

OUTPUT FORMAT:
Respond with ONLY a valid JSON object (no markdown, no code fences, no explanations) matching \
exactly this schema. The placeholder values describe what each field should contain:

{schema_json}
"""


def _load_research_schema() -> Dict:
    """Load the DTx research schema from the JSON file."""
    if not RESEARCH_SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"Schema file not found: {RESEARCH_SCHEMA_PATH}. "
            "Create it at data-format/dtx_research.json."
        )
    with open(RESEARCH_SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _env_float(name: str, default: float) -> float:
    """Read a float from env, falling back to default on missing/invalid."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    """Read an int from env, falling back to default on missing/invalid."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class USAScraper(BaseScraper):
    """Scraper for US Digital Therapeutics using LLM-based research.
    
    This scraper takes a CSV file with company information and uses
    the configured LLM provider to research and extract DTx product information.
    """

    def __init__(self, config_path: str = "config/usa.json"):
        """Initialize the USA DTx scraper.
        
        Args:
            config_path: Path to USA configuration file.
        """
        super().__init__(config_path)
        
        self.csv_input_path = self.config.get("csv_input_path", "data-format/us_company.csv")
        self.output_file = self.config.get("output_file", "data/dtx_data_usa.json")
        self.csv_mappings = self.config.get("csv_column_mappings", {})

        # Schema-driven system prompt (no hardcoded JSON structure).
        self.research_schema = _load_research_schema()
        schema_json = json.dumps(self.research_schema, indent=2, ensure_ascii=False)
        self.research_system_prompt = RESEARCH_SYSTEM_PROMPT_TEMPLATE.replace(
            "{schema_json}", schema_json
        )

        # Model + provider come purely from .env (LLM_PROVIDER + model var).
        # Temperature/max_tokens default in code, optionally overridden via env.
        temperature = _env_float("LLM_TEMPERATURE", 0.0)
        max_tokens = _env_int("LLM_MAX_TOKENS", 4000)

        self.llm = LLMProvider.get_llm(
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.provider = LLMProvider.get_active_provider()
        self.model_name = LLMProvider.get_active_model()
        self.web_search_active = LLMProvider.web_search_active()

        # Per-call benchmark rows collected during a run.
        self._call_metrics: List[Dict] = []
    
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
    
    @staticmethod
    def _response_to_text(response) -> str:
        """Flatten a LangChain response into plain text.

        When a native web-search tool is bound, OpenAI/Anthropic return a list
        of content blocks where the leading blocks are tool-use records
        (web_search_call / server_tool_use) with no usable text. Keep only
        genuine text blocks; never stringify tool blocks (that corrupts the JSON
        with Python dict reprs).
        """
        content = response.content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    text_val = block.get("text")
                    if isinstance(text_val, str) and text_val:
                        parts.append(text_val)
                # skip web_search_call / server_tool_use / tool_result blocks
            return "".join(parts).strip()
        return (content or "").strip()
    
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

        website_line = (
            website
            if website
            else "Not provided - use web search to find the official website for each product."
        )
        research_prompt = f"""### RESEARCH TARGET
**Company Name**: {company_name}
**Website**: {website_line}
**Context/Description**: {description if description else 'Not provided'}

Identify this company's genuine Digital Therapeutic products following the rules in the system prompt.
For each product, confirm details against the company website or app stores and include a source_url.
Return an empty dtx_products list if the company has no real DTx product."""

        response_text = ""
        try:
            messages = [
                SystemMessage(content=self.research_system_prompt),
                HumanMessage(content=research_prompt)
            ]

            response, _metrics = await invoke_with_metrics(
                self.llm,
                messages,
                provider=self.provider,
                model=self.model_name,
                call_label="usa_research",
                web_search=self.web_search_active,
                extra={"company_name": company_name},
            )
            self._call_metrics.append(_metrics)

            response_text = self._response_to_text(response)

            if not response_text:
                print("      Empty text response after filtering tool-use blocks.")
                return {
                    "dtx_products": [],
                    "company_info": {},
                    "research_notes": (
                        "Model returned no final text (only tool-use blocks). "
                        "Web search may not have produced a final answer."
                    ),
                }

            # Provider-agnostic parse: tolerates leading prose, code fences, and
            # a reasoning object emitted before the real JSON, then normalizes to
            # the schema so every model yields the same shape.
            research_result = parse_research_response(response_text)
            if research_result is not None:
                return research_result

            # Repair retry: chatty models (notably Claude) sometimes ignore the
            # "JSON only" instruction. Ask once more for strict JSON, reusing the
            # same model/tools, then re-parse.
            repaired = await self._repair_to_json(
                messages, response_text, company_name
            )
            if repaired is not None:
                return repaired

            print("      Could not parse JSON (even after repair retry).")
            print(f"      Response was: {response_text[:500]}...")
            return {
                "dtx_products": [],
                "company_info": {},
                "research_notes": "Failed to parse LLM response as JSON after repair retry.",
            }

        except Exception as e:
            print(f"      LLM research error: {e}")
            return {
                "dtx_products": [],
                "company_info": {},
                "research_notes": f"Research failed: {str(e)}"
            }

    async def _repair_to_json(
        self,
        original_messages: List,
        prior_text: str,
        company_name: str,
    ) -> Optional[Dict]:
        """Re-prompt once for strict JSON when the first response didn't parse.

        Reuses the same model (and any bound web-search tool) so the behavior
        stays identical across providers. Returns the parsed/normalized dict, or
        None if the repair attempt also fails.
        """
        try:
            repair_messages = list(original_messages) + [
                AIMessage(content=prior_text),
                HumanMessage(
                    content=(
                        "Your previous response could not be parsed as JSON. "
                        "Reply with ONLY the JSON object matching the required "
                        "schema - no explanations, no markdown, no code fences."
                    )
                ),
            ]
            response, _metrics = await invoke_with_metrics(
                self.llm,
                repair_messages,
                provider=self.provider,
                model=self.model_name,
                call_label="usa_research_repair",
                web_search=self.web_search_active,
                extra={"company_name": company_name},
            )
            self._call_metrics.append(_metrics)
            return parse_research_response(self._response_to_text(response))
        except Exception as e:
            print(f"      Repair retry failed: {e}")
            return None

    def _format_dtx_entry(self, company: Dict, product: Dict, company_info: Dict) -> Dict:
        """Format a single DTx product into the unified schema.
        
        Schema matches German DTx structure with only price_usd instead of price_eur.
        
        Args:
            company: Original company data from CSV.
            product: DTx product data from LLM research.
            company_info: Company info from LLM research.
            
        Returns:
            Formatted DTx entry matching the unified schema.
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
            # Core identification
            "dtx_name": product.get("dtx_name", "Unknown"),
            "company_provider": company.get("company_name", "Unknown"),
            "company_website": company_info.get("company_website") or company.get("website"),
            "company_founding_year": founding_year,
            # Regulatory status
            "listing_status": product.get("listing_status", "Unknown"),
            "date_of_first_listing": None,
            # Clinical information
            "clinical_area_icd10": product.get("clinical_area_icd10", []),
            "dtx_category": None,
            "description": product.get("description"),
            # Platform availability
            "app_store_url": product.get("app_store_url"),
            "play_store_url": product.get("play_store_url"),
            "web_app_url": None,
            # Pricing (USD for USA, EUR for Germany)
            "price_usd": product.get("price_usd"),
            # Languages & trials
            "languages": ["English"],
            "trial_registration_ids": [],
            # App store metrics
            "reviews_playstore": None,
            "reviews_appstore": None,
            # Metadata
            "source_url": product.get("source_url") or company_info.get("company_website") or company.get("website"),
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
        print(f"  Provider: {self.provider} | Model: {self.model_name or '(from env)'} | "
              f"Web search: {'on' if self.web_search_active else 'off'}")

        # Reset per-run metrics.
        self._call_metrics = []

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
        benchmark = aggregate(self._call_metrics)
        result = {
            "metadata": {
                "country": "USA",
                "last_updated": datetime.utcnow().isoformat() + "Z",
                "total_count": len(dtx_list),
                "companies_researched": len(companies),
                "companies_with_dtx": companies_with_dtx,
                "source": LLMProvider.get_source_name(),
                "benchmark": {
                    "provider": self.provider,
                    "model": self.model_name,
                    "web_search": self.web_search_active,
                    **benchmark,
                },
            },
            "dtx_list": dtx_list
        }
        
        print(f"\nResearch complete!")
        print(f"  Companies researched: {len(companies)}")
        print(f"  Companies with DTx: {companies_with_dtx}")
        print(f"  Total DTx products: {len(dtx_list)}")
        print(f"  LLM calls: {benchmark['total_calls']} | "
              f"Tokens: {benchmark['total_tokens']} | "
              f"Cost: ${benchmark['total_estimated_cost_usd']} | "
              f"Total time: {benchmark['total_latency_ms']} ms")
        
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

        # Reset per-run metrics.
        self._call_metrics = []

        print(f"Researching single company: {company_name}")
        print(f"  Provider: {self.provider} | Model: {self.model_name or '(from env)'} | "
              f"Web search: {'on' if self.web_search_active else 'off'}")
        research_result = await self.research_company(company)
        
        products = research_result.get("dtx_products", [])
        company_info = research_result.get("company_info", {})
        
        dtx_list = []
        for product in products:
            dtx_entry = self._format_dtx_entry(company, product, company_info)
            dtx_list.append(dtx_entry)
        
        benchmark = aggregate(self._call_metrics)
        return {
            "metadata": {
                "country": "USA",
                "last_updated": datetime.utcnow().isoformat() + "Z",
                "total_count": len(dtx_list),
                "source": LLMProvider.get_source_name(),
                "benchmark": {
                    "provider": self.provider,
                    "model": self.model_name,
                    "web_search": self.web_search_active,
                    **benchmark,
                },
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
                "source": LLMProvider.get_source_name()
            },
            "dtx_list": merged_list
        }
