"""Base class for evidence scrapers.

This module provides the abstract base class for all evidence source scrapers
(PubMed, ClinicalTrials.gov, DRKS, ISRCTN).
"""
import asyncio
import json
import os
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

import httpx
from dotenv import load_dotenv
from slugify import slugify


class BaseEvidenceScraper(ABC):
    """Abstract base class for evidence scrapers.
    
    All source-specific scrapers (PubMed, ClinicalTrials.gov, etc.) 
    should inherit from this class.
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
        # Use slugify to create safe folder names
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
    
    def is_result_relevant(self, result: Dict, dtx_name: str) -> bool:
        """Check if a search result is relevant to the DTx.
        
        Verifies that the core product name appears in the title or abstract.
        This filters out false positives from generic searches.
        
        Uses a unified filtering mechanism for all evidence sources (PubMed, 
        ClinicalTrials.gov, DRKS, ISRCTN).
        
        Args:
            result: Search result dictionary with title and/or abstract.
            dtx_name: Full DTx name.
            
        Returns:
            True if the result appears relevant to the DTx.
        """
        core_name = self._extract_core_product_name(dtx_name)
        if not core_name or len(core_name) < 3:
            # If we can't extract a core name, accept all results
            return True
        
        # Get text to search in
        title = result.get("title", "") or ""
        abstract = result.get("abstract", "") or result.get("brief_summary", "") or ""
        
        # Combine and lowercase for case-insensitive search
        text_to_search = f"{title} {abstract}".lower()
        core_name_lower = core_name.lower()
        
        # Check if core product name appears in the text (primary check)
        if core_name_lower in text_to_search:
            return True
        
        # Also check for variations (without spaces for compound names)
        # e.g., "CaraCare" for "Cara Care"
        core_name_nospace = core_name_lower.replace(" ", "")
        if len(core_name_nospace) > 3 and core_name_nospace in text_to_search.replace(" ", ""):
            return True
        
        # Fallback for multi-word names: check if first word appears as complete word
        # Only for distinctive words (6+ characters) to filter out common short words
        # like "Cara" (4), "Beats" (5), "Feel" (4) that cause false positives
        words = core_name_lower.split()
        if len(words) >= 2:
            first_word = words[0]
            # Require 6+ characters for single-word fallback matching
            if len(first_word) >= 6:
                # Use word boundary to ensure it's a complete word, not substring
                # e.g., "deprexis" should match, but "pre" in "expression" should not
                pattern = r'\b' + re.escape(first_word) + r'\b'
                if re.search(pattern, text_to_search):
                    return True
        
        return False
    
    def _get_dtx_folder(self, country: str, dtx_name: str, evidence_type: str) -> Path:
        """Get or create the folder path for a DTx's evidence.
        
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
        folder = self._get_dtx_folder(country, dtx_name, evidence_type) / "pdfs"
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    
    def save_metadata(
        self, 
        country: str, 
        dtx_name: str, 
        evidence_type: str, 
        metadata: Dict[str, Any],
        filename: str = "metadata.json"
    ):
        """Save evidence metadata to JSON file.
        
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
    
    async def download_pdf(
        self, 
        url: str, 
        country: str, 
        dtx_name: str, 
        evidence_type: str,
        filename: str
    ) -> Optional[Path]:
        """Download a PDF file and save it.
        
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
                # PMC now uses JS-based bot protection, returns HTML instead of PDF
                # This is expected for many articles - silently skip
                return None
                
        except httpx.HTTPStatusError as e:
            # 403 Forbidden is common for PMC PDFs (bot protection)
            # Silently skip these - the metadata is still useful
            if e.response.status_code in (403, 401, 429):
                return None
            print(f"    Error downloading PDF: HTTP {e.response.status_code}")
            return None
        except Exception as e:
            # Only print unexpected errors
            error_str = str(e)
            if "403" not in error_str and "Forbidden" not in error_str:
                print(f"    Error downloading PDF: {e}")
            return None
    
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
    
    async def search_and_save(
        self,
        queries: List[str],
        country: str,
        dtx_name: str,
        classifier,  # Will be EvidenceClassifier instance
        max_results_per_query: int = 50
    ) -> Dict[str, int]:
        """Search with multiple queries and save results organized by RCT/RWE.
        
        Includes relevance filtering to remove false positives.
        
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
                        
                        # Check relevance before adding
                        if self.is_result_relevant(result, dtx_name):
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
                # Default to RWE if classification fails
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
    
    def get_summary_stats(self, country: str) -> Dict:
        """Get summary statistics for a country's evidence.
        
        Args:
            country: "Germany" or "USA"
            
        Returns:
            Dictionary with summary stats.
        """
        country_dir = self.evidence_dir / country
        if not country_dir.exists():
            return {"dtx_count": 0, "rct_count": 0, "rwe_count": 0}
        
        dtx_count = 0
        rct_count = 0
        rwe_count = 0
        
        for dtx_folder in country_dir.iterdir():
            if dtx_folder.is_dir():
                dtx_count += 1
                
                # Count RCT
                rct_folder = dtx_folder / "RCT" / self.SOURCE_NAME
                if rct_folder.exists():
                    studies_file = rct_folder / "studies.json"
                    if studies_file.exists():
                        try:
                            with open(studies_file, "r") as f:
                                data = json.load(f)
                                rct_count += data.get("count", 0)
                        except:
                            pass
                
                # Count RWE
                rwe_folder = dtx_folder / "RWE" / self.SOURCE_NAME
                if rwe_folder.exists():
                    studies_file = rwe_folder / "studies.json"
                    if studies_file.exists():
                        try:
                            with open(studies_file, "r") as f:
                                data = json.load(f)
                                rwe_count += data.get("count", 0)
                        except:
                            pass
        
        return {
            "dtx_count": dtx_count,
            "rct_count": rct_count,
            "rwe_count": rwe_count
        }
