"""ClinicalTrials.gov evidence scraper using their API v2.

This module searches ClinicalTrials.gov for clinical trials and extracts
study details including design, status, conditions, and interventions.
Also saves raw JSON responses for future analysis.
"""
import asyncio
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote_plus, urlencode

from .base_evidence_scraper import BaseEvidenceScraper


class ClinicalTrialsScraper(BaseEvidenceScraper):
    """Scraper for ClinicalTrials.gov using their official API v2.
    
    API documentation: https://clinicaltrials.gov/data-api/api
    No authentication required.
    """
    
    SOURCE_NAME = "clinicaltrials"
    
    # API endpoint
    API_URL = "https://clinicaltrials.gov/api/v2/studies"
    
    def _get_raw_folder(self, country: str, dtx_name: str, evidence_type: str) -> Path:
        """Get or create the raw JSON folder for ClinicalTrials.gov downloads.
        
        Args:
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            evidence_type: "RCT" or "RWE"
            
        Returns:
            Path to the raw folder.
        """
        folder = self._get_dtx_folder(country, dtx_name, evidence_type) / "raw"
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    
    def _save_raw_json(
        self, 
        study: Dict, 
        country: str, 
        dtx_name: str, 
        evidence_type: str
    ) -> Optional[str]:
        """Save raw JSON for a single study.
        
        Args:
            study: Raw study data from API.
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            evidence_type: "RCT" or "RWE"
            
        Returns:
            Path to the saved JSON file, or None if failed.
        """
        try:
            # Extract NCT ID from the raw data
            protocol = study.get("protocolSection", {})
            id_module = protocol.get("identificationModule", {})
            nct_id = id_module.get("nctId", "")
            
            if not nct_id:
                return None
            
            raw_folder = self._get_raw_folder(country, dtx_name, evidence_type)
            save_path = raw_folder / f"{nct_id}.json"
            
            # Skip if already downloaded
            if save_path.exists():
                return str(save_path)
            
            # Save raw JSON
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(study, f, indent=2, ensure_ascii=False)
            
            return str(save_path)
            
        except Exception as e:
            return None
    
    def _format_query_with_expansion(self, query: str) -> str:
        """Format query with EXPANSION[Term] operator to enforce exact phrase matching.
        
        ClinicalTrials.gov API v2 defaults to EXPANSION[Relaxation] which:
        - Includes synonyms via UMLS
        - Relaxes adjacency requirements (words don't need to be together!)
        
        Using EXPANSION[Term] ensures:
        - Words in phrases must stay together
        - Only includes lexical variants (plurals, case-insensitive)
        - No synonym expansion
        
        Args:
            query: Original search query string.
            
        Returns:
            Query formatted with EXPANSION[Term] operator.
        """
        # If query already has EXPANSION operator, return as-is
        if "EXPANSION[" in query:
            return query
        
        # Wrap the entire query with EXPANSION[Term] to enforce exact matching
        # This is especially important for quoted phrases like "Cara Care"
        return f"EXPANSION[Term]{query}"
    
    async def search(self, query: str, max_results: int = 50) -> List[Dict]:
        """Search ClinicalTrials.gov for studies matching the query.
        
        Uses curl as a fallback since httpx is blocked by their anti-bot protection.
        Applies EXPANSION[Term] operator to enforce exact phrase matching.
        
        Args:
            query: Search query string.
            max_results: Maximum number of results to return.
            
        Returns:
            List of study dictionaries with metadata.
        """
        # Format query with EXPANSION[Term] to prevent relaxed adjacency matching
        formatted_query = self._format_query_with_expansion(query)
        
        # Try intervention search first, then condition search
        for param_type in ["query.intr", "query.cond"]:
            try:
                params = {
                    param_type: formatted_query,
                    "pageSize": min(max_results, 100),
                    "format": "json"
                }
                
                # Build URL
                url = f"{self.API_URL}?{urlencode(params)}"
                
                # Use curl to avoid httpx fingerprint blocking
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: subprocess.run(
                        ["curl", "-s", url],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                )
                
                if result.returncode == 0 and result.stdout:
                    data = json.loads(result.stdout)
                    studies = data.get("studies", [])
                    if studies:
                        parsed_studies = []
                        for study in studies:
                            parsed = self._parse_study(study)
                            # Store raw data temporarily for later saving
                            parsed["_raw_study_data"] = study
                            parsed_studies.append(parsed)
                        return parsed_studies
                    
            except Exception as e:
                continue
        
        print(f"    ClinicalTrials.gov search error: No results found")
        return []
    
    def _parse_study(self, study: Dict) -> Dict:
        """Parse a study from the API response into our format.
        
        Args:
            study: Raw study data from API.
            
        Returns:
            Formatted study dictionary.
        """
        # The API returns nested structure
        protocol = study.get("protocolSection", {})
        
        # Identification
        id_module = protocol.get("identificationModule", {})
        nct_id = id_module.get("nctId", "")
        brief_title = id_module.get("briefTitle", "")
        official_title = id_module.get("officialTitle", "")
        
        # Status
        status_module = protocol.get("statusModule", {})
        overall_status = status_module.get("overallStatus", "")
        start_date = status_module.get("startDateStruct", {}).get("date", "")
        completion_date = status_module.get("completionDateStruct", {}).get("date", "")
        
        # Design
        design_module = protocol.get("designModule", {})
        study_type = design_module.get("studyType", "")
        phases = design_module.get("phases", [])
        
        design_info = design_module.get("designInfo", {})
        allocation = design_info.get("allocation", "")
        intervention_model = design_info.get("interventionModel", "")
        primary_purpose = design_info.get("primaryPurpose", "")
        masking = design_info.get("maskingInfo", {}).get("masking", "")
        
        enrollment = design_module.get("enrollmentInfo", {}).get("count", "")
        
        # Conditions
        conditions_module = protocol.get("conditionsModule", {})
        conditions = conditions_module.get("conditions", [])
        keywords = conditions_module.get("keywords", [])
        
        # Interventions
        arms_module = protocol.get("armsInterventionsModule", {})
        interventions = []
        for intervention in arms_module.get("interventions", []):
            interventions.append({
                "name": intervention.get("name", ""),
                "type": intervention.get("type", ""),
                "description": intervention.get("description", "")[:500] if intervention.get("description") else ""
            })
        
        # Sponsors
        sponsor_module = protocol.get("sponsorCollaboratorsModule", {})
        lead_sponsor = sponsor_module.get("leadSponsor", {}).get("name", "")
        collaborators = [c.get("name", "") for c in sponsor_module.get("collaborators", [])]
        
        # Locations
        locations_module = protocol.get("contactsLocationsModule", {})
        locations = locations_module.get("locations", [])
        countries = list(set(loc.get("country", "") for loc in locations if loc.get("country")))
        
        # Description
        desc_module = protocol.get("descriptionModule", {})
        brief_summary = desc_module.get("briefSummary", "")
        detailed_description = desc_module.get("detailedDescription", "")
        
        return {
            "study_id": nct_id,
            "nct_id": nct_id,
            "title": brief_title or official_title,
            "official_title": official_title,
            "status": overall_status,
            "phase": ", ".join(phases) if phases else "N/A",
            "study_type": study_type,
            "design": {
                "allocation": allocation,
                "intervention_model": intervention_model,
                "primary_purpose": primary_purpose,
                "masking": masking
            },
            "enrollment": enrollment,
            "start_date": start_date,
            "completion_date": completion_date,
            "conditions": conditions,
            "keywords": keywords[:10] if keywords else [],
            "interventions": interventions,
            "lead_sponsor": lead_sponsor,
            "collaborators": collaborators[:5] if collaborators else [],
            "countries": countries,
            "brief_summary": brief_summary[:1500] if brief_summary else "",
            "detailed_description": detailed_description[:2000] if detailed_description else "",
            "source": "ClinicalTrials.gov",
            "url": f"https://clinicaltrials.gov/study/{nct_id}"
        }
    
    async def get_study_details(self, study_id: str) -> Optional[Dict]:
        """Get detailed information for a specific study by NCT ID.
        
        Args:
            study_id: ClinicalTrials.gov NCT ID (e.g., "NCT01234567").
            
        Returns:
            Dictionary with study details or None.
        """
        client = await self._get_http_client()
        
        try:
            # Direct study lookup
            url = f"{self.API_URL}/{study_id}"
            params = {"format": "json"}
            
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            return self._parse_study(data)
            
        except Exception as e:
            print(f"    Error fetching study {study_id}: {e}")
            return None
    
    def is_likely_rct(self, study: Dict) -> bool:
        """Quick check if a study is likely an RCT based on design info.
        
        This is a preliminary check - the LLM classifier makes the final call.
        
        Args:
            study: Study dictionary.
            
        Returns:
            True if study appears to be an RCT.
        """
        # Check study type
        study_type = study.get("study_type", "").lower()
        if "observational" in study_type:
            return False
        
        # Check allocation
        allocation = study.get("design", {}).get("allocation", "").lower()
        if "randomized" in allocation:
            return True
        
        # Check phase
        phase = study.get("phase", "").lower()
        if any(p in phase for p in ["phase 2", "phase 3", "phase ii", "phase iii"]):
            return True
        
        # Check masking
        masking = study.get("design", {}).get("masking", "").lower()
        if "double" in masking or "triple" in masking:
            return True
        
        return False
    
    async def search_and_save_candidates(
        self,
        queries: List[str],
        country: str,
        dtx_name: str,
        max_results_per_query: int = 50
    ) -> Dict[str, int]:
        """Search ClinicalTrials.gov and save ALL results as candidates (Layer 1).
        
        No classification or relevance filtering - just collect raw data.
        Saves raw JSON for each study.
        
        Args:
            queries: List of search query strings.
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            max_results_per_query: Max results per query
            
        Returns:
            Dictionary with counts: {"total": N, "queries": [...]}
        """
        all_results = []
        seen_ncts = set()
        
        # Get raw folder for candidates
        raw_folder = self._get_candidates_raw_folder(country, dtx_name)
        
        # Search with each query
        for query in queries:
            try:
                results = await self.search(query, max_results_per_query)
                
                # Deduplicate by NCT ID only (no filtering)
                for result in results:
                    nct_id = result.get("nct_id")
                    if nct_id and nct_id not in seen_ncts:
                        seen_ncts.add(nct_id)
                        result["_matched_query"] = query
                        
                        # Save raw JSON for this study
                        raw_study_data = result.pop("_raw_study_data", None)
                        if raw_study_data:
                            raw_path = self._save_raw_json_to_candidates(
                                raw_study_data, nct_id, raw_folder
                            )
                            if raw_path:
                                result["_raw_json_path"] = str(raw_path)
                        
                        all_results.append(result)
                
                await asyncio.sleep(0.5)  # Rate limiting
                
            except Exception as e:
                print(f"    Error searching '{query[:50]}...': {e}")
        
        # Save all candidates
        if all_results:
            self.save_candidates_metadata(country, dtx_name, {
                "studies": all_results,
                "count": len(all_results),
                "queries_used": queries,
                "dtx_name": dtx_name,
                "country": country
            }, "studies.json")
        
        return {
            "total": len(all_results),
            "queries": queries
        }
    
    def _save_raw_json_to_candidates(
        self, 
        study: Dict, 
        nct_id: str,
        raw_folder: Path
    ) -> Optional[Path]:
        """Save raw JSON for a single study to candidates folder.
        
        Args:
            study: Raw study data from API.
            nct_id: Study NCT ID.
            raw_folder: Path to candidates raw folder.
            
        Returns:
            Path to the saved JSON file, or None if failed.
        """
        try:
            save_path = raw_folder / f"{nct_id}.json"
            
            # Skip if already downloaded
            if save_path.exists():
                return save_path
            
            # Save raw JSON
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(study, f, indent=2, ensure_ascii=False)
            
            return save_path
            
        except Exception as e:
            print(f"        Warning: Failed to save raw JSON for {nct_id}: {e}")
            return None
    
    async def search_and_save(
        self,
        queries: List[str],
        country: str,
        dtx_name: str,
        classifier,
        max_results_per_query: int = 50
    ) -> Dict[str, int]:
        """Search ClinicalTrials.gov, classify results, and save.
        
        DEPRECATED: Use search_and_save_candidates for Layer 1.
        
        Includes relevance filtering to remove false positives.
        
        Args:
            queries: List of search query strings.
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            classifier: LLM classifier for RCT/RWE determination
            max_results_per_query: Max results per query
            
        Returns:
            Dictionary with counts.
        """
        all_results = []
        seen_ncts = set()
        filtered_count = 0
        
        # Search with each query
        for query in queries:
            try:
                results = await self.search(query, max_results_per_query)
                
                # Deduplicate by NCT ID and filter for relevance
                for result in results:
                    nct_id = result.get("nct_id")
                    if nct_id and nct_id not in seen_ncts:
                        seen_ncts.add(nct_id)
                        
                        # Check relevance before adding
                        if self.is_result_relevant(result, dtx_name):
                            # Track which query found this result
                            result["matched_query"] = query
                            all_results.append(result)
                        else:
                            filtered_count += 1
                
                await asyncio.sleep(0.5)  # Rate limiting
                
            except Exception as e:
                print(f"    Error searching '{query[:50]}...': {e}")
        
        if filtered_count > 0:
            print(f"    Filtered {filtered_count} irrelevant trials")
        
        if not all_results:
            return {"rct": 0, "rwe": 0, "total": 0, "filtered": filtered_count}
        
        print(f"    Found {len(all_results)} relevant trials, classifying...")
        
        # Classify and organize results
        rct_results = []
        rwe_results = []
        
        for result in all_results:
            try:
                # Use preliminary check to guide LLM
                likely_rct = self.is_likely_rct(result)
                
                classification = await classifier.classify(result, hint_rct=likely_rct)
                result["classification"] = classification
                
                evidence_type = "RCT" if classification.get("classification") == "RCT" else "RWE"
                
                # Save raw JSON to disk
                raw_study_data = result.pop("_raw_study_data", None)
                if raw_study_data:
                    raw_path = self._save_raw_json(raw_study_data, country, dtx_name, evidence_type)
                    if raw_path:
                        result["_raw_json_path"] = raw_path
                
                if evidence_type == "RCT":
                    rct_results.append(result)
                else:
                    rwe_results.append(result)
                    
            except Exception as e:
                # Remove raw data before fallback handling
                raw_study_data = result.pop("_raw_study_data", None)
                
                # Use preliminary check as fallback
                if self.is_likely_rct(result):
                    result["classification"] = {
                        "classification": "RCT",
                        "confidence": 50,
                        "reason": f"Fallback: design suggests RCT. Error: {e}"
                    }
                    # Save raw JSON
                    if raw_study_data:
                        raw_path = self._save_raw_json(raw_study_data, country, dtx_name, "RCT")
                        if raw_path:
                            result["_raw_json_path"] = raw_path
                    rct_results.append(result)
                else:
                    result["classification"] = {
                        "classification": "RWE",
                        "confidence": 50,
                        "reason": f"Fallback: design suggests RWE. Error: {e}"
                    }
                    # Save raw JSON
                    if raw_study_data:
                        raw_path = self._save_raw_json(raw_study_data, country, dtx_name, "RWE")
                        if raw_path:
                            result["_raw_json_path"] = raw_path
                    rwe_results.append(result)
        
        # Save results
        if rct_results:
            self.save_metadata(country, dtx_name, "RCT", {
                "studies": rct_results,
                "count": len(rct_results),
                "queries_used": queries
            }, "studies.json")
            print(f"    Saved {len(rct_results)} RCT trials")
        
        if rwe_results:
            self.save_metadata(country, dtx_name, "RWE", {
                "studies": rwe_results,
                "count": len(rwe_results),
                "queries_used": queries
            }, "studies.json")
            print(f"    Saved {len(rwe_results)} RWE trials")
        
        return {
            "rct": len(rct_results),
            "rwe": len(rwe_results),
            "total": len(all_results),
            "filtered": filtered_count
        }
