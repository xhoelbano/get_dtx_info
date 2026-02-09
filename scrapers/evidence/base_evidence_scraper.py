"""Base class for evidence scrapers with two-layer classification support.

This module provides the abstract base class for all evidence source scrapers
(PubMed, ClinicalTrials.gov, DRKS, ISRCTN).

Folder Structure (Two-Layer Classification):
  evidence/
    {Country}/
      {DTx-slug}/
        candidates/           # Layer 1: All raw results from queries
          {source}/
            raw/              # Raw XML/JSON files
            studies.json      # Parsed study metadata
        verified/             # Layer 2: LLM-confirmed relevant studies
          RCT/
            {source}/
              raw/
              studies.json
          RWE/
            {source}/
              raw/
              studies.json
"""
import asyncio
import json
import os
import re
import shutil
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple

import httpx
from dotenv import load_dotenv
from slugify import slugify


class BaseEvidenceScraper(ABC):
    """Abstract base class for evidence scrapers.
    
    All source-specific scrapers (PubMed, ClinicalTrials.gov, etc.) 
    should inherit from this class.
    
    Supports two-layer classification:
    - Layer 1: Collect candidates (all search results) to candidates/ folder
    - Layer 2: LLM verifies relevance and moves to verified/{RCT|RWE}/ folder
    """
    
    # Source identifier (override in subclass)
    SOURCE_NAME = "base"
    
    def __init__(self, evidence_dir: str = "evidence"):
        """Initialize the evidence scraper.
        
        Args:
            evidence_dir: Root directory for storing evidence files.
        """
        load_dotenv()
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        
        # HTTP client for API calls
        self._http_client: Optional[httpx.AsyncClient] = None
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client for API calls."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=60.0,
                follow_redirects=True,
                http2=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "application/json, text/html, */*",
                    "Accept-Language": "en-US,en;q=0.9",
                }
            )
        return self._http_client
    
    async def close(self):
        """Clean up resources."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
    
    def _sanitize_dtx_name(self, dtx_name: str) -> str:
        """Convert DTx name to filesystem-safe folder name.
        
        Args:
            dtx_name: Original DTx name.
            
        Returns:
            Sanitized folder name.
        """
        return slugify(dtx_name, max_length=50, lowercase=True)
    
    def _extract_core_product_name(self, dtx_name: str) -> str:
        """Extract the core product name from full DTx name.
        
        Args:
            dtx_name: Full DTx name (e.g., "Cara Care für Reizdarm")
            
        Returns:
            Core product name (e.g., "Cara Care")
        """
        if not dtx_name:
            return ""
        
        # Remove trademark symbols
        clean = re.sub(r'[®™]', '', dtx_name).strip()
        
        # Take first part before separators
        clean = clean.split(" - ")[0].split(":")[0].strip()
        
        # Remove common German suffixes
        clean = re.sub(
            r'\s+(App|Therapie|für|bei|zur|die|der|das|Meine|aktive|im|Erwachsenenalter)\s*$',
            '', clean, flags=re.IGNORECASE
        ).strip()
        
        # Remove trailing condition descriptions (e.g., "für Reizdarm")
        clean = re.sub(
            r'\s+für\s+\w+$',
            '', clean, flags=re.IGNORECASE
        ).strip()
        
        return clean
    
    # =====================================================================
    # LAYER 1: Candidates folder structure (before LLM verification)
    # =====================================================================
    
    def _get_candidates_folder(self, country: str, dtx_name: str) -> Path:
        """Get or create the candidates folder path for a DTx.
        
        Layer 1 storage: All search results before LLM verification.
        
        Args:
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            
        Returns:
            Path to the candidates folder: evidence/{Country}/{DTx}/candidates/{source}/
        """
        safe_name = self._sanitize_dtx_name(dtx_name)
        folder = self.evidence_dir / country / safe_name / "candidates" / self.SOURCE_NAME
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    
    def _get_candidates_raw_folder(self, country: str, dtx_name: str) -> Path:
        """Get or create the raw files folder within candidates.
        
        Args:
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            
        Returns:
            Path to the raw folder: evidence/{Country}/{DTx}/candidates/{source}/raw/
        """
        folder = self._get_candidates_folder(country, dtx_name) / "raw"
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    
    # =====================================================================
    # LAYER 2: Verified folder structure (after LLM verification)
    # =====================================================================
    
    def _get_verified_folder(self, country: str, dtx_name: str, evidence_type: str) -> Path:
        """Get or create the verified folder path for a DTx.
        
        Layer 2 storage: LLM-verified relevant studies only.
        
        Args:
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            evidence_type: "RCT" or "RWE"
            
        Returns:
            Path: evidence/{Country}/{DTx}/verified/{RCT|RWE}/{source}/
        """
        safe_name = self._sanitize_dtx_name(dtx_name)
        folder = self.evidence_dir / country / safe_name / "verified" / evidence_type / self.SOURCE_NAME
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    
    def _get_verified_raw_folder(self, country: str, dtx_name: str, evidence_type: str) -> Path:
        """Get or create the raw files folder within verified.
        
        Args:
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            evidence_type: "RCT" or "RWE"
            
        Returns:
            Path: evidence/{Country}/{DTx}/verified/{RCT|RWE}/{source}/raw/
        """
        folder = self._get_verified_folder(country, dtx_name, evidence_type) / "raw"
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    
    # =====================================================================
    # LEGACY: Old folder structure (for backwards compatibility)
    # =====================================================================
    
    def _get_dtx_folder(self, country: str, dtx_name: str, evidence_type: str) -> Path:
        """Get or create the folder path for a DTx's evidence.
        
        DEPRECATED: Use _get_candidates_folder or _get_verified_folder instead.
        Kept for backwards compatibility with existing code.
        
        Args:
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            evidence_type: "RCT" or "RWE"
            
        Returns:
            Path to the evidence folder.
        """
        safe_name = self._sanitize_dtx_name(dtx_name)
        folder = self.evidence_dir / country / safe_name / evidence_type / self.SOURCE_NAME
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    
    def _get_pdfs_folder(self, country: str, dtx_name: str, evidence_type: str) -> Path:
        """Get or create the PDFs folder for a DTx's evidence.
        
        Args:
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            evidence_type: "RCT" or "RWE"
            
        Returns:
            Path to the PDFs folder.
        """
        folder = self._get_verified_folder(country, dtx_name, evidence_type) / "pdfs"
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    
    # =====================================================================
    # Save/Load methods
    # =====================================================================
    
    def save_candidates_metadata(
        self, 
        country: str, 
        dtx_name: str, 
        metadata: Dict[str, Any],
        filename: str = "studies.json"
    ):
        """Save candidate studies metadata to JSON file (Layer 1).
        
        Args:
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            metadata: Dictionary of metadata to save
            filename: Name of the JSON file
        """
        folder = self._get_candidates_folder(country, dtx_name)
        filepath = folder / filename
        
        # Add timestamp
        metadata["_saved_at"] = datetime.utcnow().isoformat() + "Z"
        metadata["_source"] = self.SOURCE_NAME
        metadata["_layer"] = "candidates"
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    def save_verified_metadata(
        self, 
        country: str, 
        dtx_name: str, 
        evidence_type: str,
        metadata: Dict[str, Any],
        filename: str = "studies.json"
    ):
        """Save verified studies metadata to JSON file (Layer 2).
        
        Args:
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            evidence_type: "RCT" or "RWE"
            metadata: Dictionary of metadata to save
            filename: Name of the JSON file
        """
        folder = self._get_verified_folder(country, dtx_name, evidence_type)
        filepath = folder / filename
        
        # Add timestamp
        metadata["_saved_at"] = datetime.utcnow().isoformat() + "Z"
        metadata["_source"] = self.SOURCE_NAME
        metadata["_layer"] = "verified"
        metadata["_evidence_type"] = evidence_type
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    def load_candidates_metadata(
        self, 
        country: str, 
        dtx_name: str,
        filename: str = "studies.json"
    ) -> Optional[Dict]:
        """Load existing candidates metadata if available.
        
        Args:
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            filename: Name of the JSON file
            
        Returns:
            Existing metadata dict or None.
        """
        folder = self._get_candidates_folder(country, dtx_name)
        filepath = folder / filename
        
        if filepath.exists():
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return None
        return None
    
    def save_metadata(
        self, 
        country: str, 
        dtx_name: str, 
        evidence_type: str, 
        metadata: Dict[str, Any],
        filename: str = "metadata.json"
    ):
        """Save evidence metadata to JSON file.
        
        DEPRECATED: Use save_candidates_metadata or save_verified_metadata.
        Kept for backwards compatibility.
        
        Args:
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            evidence_type: "RCT" or "RWE"
            metadata: Dictionary of metadata to save
            filename: Name of the JSON file
        """
        folder = self._get_dtx_folder(country, dtx_name, evidence_type)
        filepath = folder / filename
        
        # Add timestamp
        metadata["_saved_at"] = datetime.utcnow().isoformat() + "Z"
        metadata["_source"] = self.SOURCE_NAME
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    def load_existing_metadata(
        self, 
        country: str, 
        dtx_name: str, 
        evidence_type: str,
        filename: str = "metadata.json"
    ) -> Optional[Dict]:
        """Load existing metadata if available.
        
        Args:
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            evidence_type: "RCT" or "RWE"
            filename: Name of the JSON file
            
        Returns:
            Existing metadata dict or None.
        """
        folder = self._get_dtx_folder(country, dtx_name, evidence_type)
        filepath = folder / filename
        
        if filepath.exists():
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return None
        return None
    
    # =====================================================================
    # Raw file operations
    # =====================================================================
    
    def copy_raw_file_to_verified(
        self,
        country: str,
        dtx_name: str,
        evidence_type: str,
        study_id: str,
        file_extension: str = "json"
    ) -> Optional[Path]:
        """Copy a raw file from candidates to verified folder.
        
        Args:
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            evidence_type: "RCT" or "RWE"
            study_id: Study identifier (PMID, NCT, DRKS, ISRCTN)
            file_extension: File extension (json or xml)
            
        Returns:
            Path to the copied file in verified folder, or None if source doesn't exist.
        """
        source_folder = self._get_candidates_raw_folder(country, dtx_name)
        source_file = source_folder / f"{study_id}.{file_extension}"
        
        if not source_file.exists():
            return None
        
        dest_folder = self._get_verified_raw_folder(country, dtx_name, evidence_type)
        dest_file = dest_folder / f"{study_id}.{file_extension}"
        
        shutil.copy2(source_file, dest_file)
        return dest_file
    
    # =====================================================================
    # PDF downloads
    # =====================================================================
    
    async def download_pdf(
        self, 
        url: str, 
        country: str, 
        dtx_name: str, 
        evidence_type: str,
        filename: str
    ) -> Optional[Path]:
        """Download a PDF file and save it to verified folder.
        
        Args:
            url: URL of the PDF to download.
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            evidence_type: "RCT" or "RWE"
            filename: Name to save the PDF as
            
        Returns:
            Path to the saved PDF or None if download failed.
        """
        pdfs_folder = self._get_pdfs_folder(country, dtx_name, evidence_type)
        filepath = pdfs_folder / filename
        
        # Skip if already downloaded
        if filepath.exists():
            return filepath
        
        try:
            client = await self._get_http_client()
            response = await client.get(url)
            response.raise_for_status()
            
            # Check if it's actually a PDF
            content_type = response.headers.get("content-type", "")
            if "pdf" in content_type.lower():
                with open(filepath, "wb") as f:
                    f.write(response.content)
                return filepath
            else:
                return None
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 401, 429):
                return None
            print(f"    Error downloading PDF: HTTP {e.response.status_code}")
            return None
        except Exception as e:
            error_str = str(e)
            if "403" not in error_str and "Forbidden" not in error_str:
                print(f"    Error downloading PDF: {e}")
            return None
    
    # =====================================================================
    # Abstract methods (implement in subclasses)
    # =====================================================================
    
    @abstractmethod
    async def search(self, query: str, max_results: int = 50) -> List[Dict]:
        """Search the source for evidence.
        
        Args:
            query: Search query string.
            max_results: Maximum number of results to return.
            
        Returns:
            List of evidence dictionaries.
        """
        pass
    
    @abstractmethod
    async def get_study_details(self, study_id: str) -> Optional[Dict]:
        """Get detailed information for a specific study.
        
        Args:
            study_id: Unique identifier for the study (PMID, NCT, DRKS, ISRCTN).
            
        Returns:
            Dictionary with study details or None.
        """
        pass
    
    # =====================================================================
    # Layer 1: Search and collect candidates
    # =====================================================================
    
    async def search_and_save_candidates(
        self,
        queries: List[str],
        country: str,
        dtx_name: str,
        max_results_per_query: int = 50
    ) -> Dict[str, int]:
        """Search with queries and save ALL results as candidates (Layer 1).
        
        No classification or filtering at this stage - just collect raw data.
        
        Args:
            queries: List of search query strings.
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            max_results_per_query: Max results per query
            
        Returns:
            Dictionary with counts: {"total": N, "queries": [...]}
        """
        all_results = []
        seen_ids = set()
        
        # Search with each query
        for query in queries:
            try:
                results = await self.search(query, max_results_per_query)
                
                # Deduplicate by study ID only (no filtering)
                for result in results:
                    study_id = result.get("study_id") or result.get("pmid") or result.get("nct_id")
                    if study_id and study_id not in seen_ids:
                        seen_ids.add(study_id)
                        result["_matched_query"] = query
                        all_results.append(result)
                
                await asyncio.sleep(0.5)  # Rate limiting
                
            except Exception as e:
                print(f"    Error searching '{query}': {e}")
        
        # Save all candidates (no classification)
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
    
    # =====================================================================
    # Legacy: Combined search and classify (old behavior)
    # =====================================================================
    
    async def search_and_save(
        self,
        queries: List[str],
        country: str,
        dtx_name: str,
        classifier,  # Will be EvidenceClassifier instance
        max_results_per_query: int = 50
    ) -> Dict[str, int]:
        """Search with multiple queries and save results organized by RCT/RWE.
        
        DEPRECATED: Use search_and_save_candidates instead for Layer 1.
        This method is kept for backwards compatibility.
        
        Args:
            queries: List of search query strings.
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            classifier: LLM classifier for RCT/RWE determination
            max_results_per_query: Max results per query
            
        Returns:
            Dictionary with counts: {"rct": N, "rwe": M, "total": N+M, "filtered": F}
        """
        all_results = []
        seen_ids = set()
        filtered_count = 0
        
        # Search with each query
        for query in queries:
            try:
                results = await self.search(query, max_results_per_query)
                
                # Deduplicate by study ID and filter for relevance
                for result in results:
                    study_id = result.get("study_id") or result.get("pmid") or result.get("nct_id")
                    if study_id and study_id not in seen_ids:
                        seen_ids.add(study_id)
                        
                        # Check relevance before adding (old behavior)
                        if self._is_result_relevant_basic(result, dtx_name):
                            all_results.append(result)
                        else:
                            filtered_count += 1
                
                await asyncio.sleep(0.5)  # Rate limiting
                
            except Exception as e:
                print(f"    Error searching '{query}': {e}")
        
        if filtered_count > 0:
            print(f"    Filtered {filtered_count} irrelevant results")
        
        if not all_results:
            return {"rct": 0, "rwe": 0, "total": 0, "filtered": filtered_count}
        
        # Classify and organize results
        rct_results = []
        rwe_results = []
        
        for result in all_results:
            try:
                classification = await classifier.classify(result)
                result["classification"] = classification
                
                if classification.get("classification") == "RCT":
                    rct_results.append(result)
                else:
                    rwe_results.append(result)
                    
            except Exception as e:
                result["classification"] = {"classification": "RWE", "confidence": 0, "reason": "Classification failed"}
                rwe_results.append(result)
        
        # Save results
        if rct_results:
            self.save_metadata(country, dtx_name, "RCT", {
                "studies": rct_results,
                "count": len(rct_results),
                "queries_used": queries
            }, "studies.json")
        
        if rwe_results:
            self.save_metadata(country, dtx_name, "RWE", {
                "studies": rwe_results,
                "count": len(rwe_results),
                "queries_used": queries
            }, "studies.json")
        
        return {
            "rct": len(rct_results),
            "rwe": len(rwe_results),
            "total": len(all_results),
            "filtered": filtered_count
        }
    
    def _is_result_relevant_basic(self, result: Dict, dtx_name: str) -> bool:
        """Basic relevance check (used by legacy search_and_save).
        
        This is a simpler check than the full LLM verification in Layer 2.
        
        Args:
            result: Search result dictionary.
            dtx_name: Full DTx name.
            
        Returns:
            True if the result appears relevant.
        """
        core_name = self._extract_core_product_name(dtx_name)
        if not core_name or len(core_name) < 3:
            return True
        
        # Check basic text fields
        text_parts = []
        text_parts.append(result.get("title", "") or "")
        text_parts.append(result.get("abstract", "") or "")
        text_parts.append(result.get("brief_summary", "") or "")
        text_parts.append(result.get("intervention", "") or "")
        text_parts.append(result.get("sponsor", "") or "")
        
        text_to_search = " ".join(text_parts).lower()
        core_name_lower = core_name.lower()
        
        if core_name_lower in text_to_search:
            return True
        
        # Check without spaces
        core_name_nospace = core_name_lower.replace(" ", "")
        if len(core_name_nospace) > 3 and core_name_nospace in text_to_search.replace(" ", ""):
            return True
        
        return False
    
    # =====================================================================
    # Statistics
    # =====================================================================
    
    def get_summary_stats(self, country: str) -> Dict:
        """Get summary statistics for a country's evidence.
        
        Args:
            country: "Germany" or "USA"
            
        Returns:
            Dictionary with summary stats.
        """
        country_dir = self.evidence_dir / country
        if not country_dir.exists():
            return {"dtx_count": 0, "candidates_count": 0, "verified_rct_count": 0, "verified_rwe_count": 0}
        
        dtx_count = 0
        candidates_count = 0
        verified_rct_count = 0
        verified_rwe_count = 0
        
        for dtx_folder in country_dir.iterdir():
            if dtx_folder.is_dir():
                dtx_count += 1
                
                # Count candidates
                candidates_folder = dtx_folder / "candidates" / self.SOURCE_NAME
                if candidates_folder.exists():
                    studies_file = candidates_folder / "studies.json"
                    if studies_file.exists():
                        try:
                            with open(studies_file, "r") as f:
                                data = json.load(f)
                                candidates_count += data.get("count", 0)
                        except:
                            pass
                
                # Count verified RCT
                rct_folder = dtx_folder / "verified" / "RCT" / self.SOURCE_NAME
                if rct_folder.exists():
                    studies_file = rct_folder / "studies.json"
                    if studies_file.exists():
                        try:
                            with open(studies_file, "r") as f:
                                data = json.load(f)
                                verified_rct_count += data.get("count", 0)
                        except:
                            pass
                
                # Count verified RWE
                rwe_folder = dtx_folder / "verified" / "RWE" / self.SOURCE_NAME
                if rwe_folder.exists():
                    studies_file = rwe_folder / "studies.json"
                    if studies_file.exists():
                        try:
                            with open(studies_file, "r") as f:
                                data = json.load(f)
                                verified_rwe_count += data.get("count", 0)
                        except:
                            pass
        
        return {
            "dtx_count": dtx_count,
            "candidates_count": candidates_count,
            "verified_rct_count": verified_rct_count,
            "verified_rwe_count": verified_rwe_count
        }
