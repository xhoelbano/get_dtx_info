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
            if "pdf" in content_type.lower() or url.endswith(".pdf"):
                with open(filepath, "wb") as f:
                    f.write(response.content)
                return filepath
            else:
                print(f"    Warning: {url} is not a PDF (content-type: {content_type})")
                return None
                
        except Exception as e:
            print(f"    Error downloading PDF from {url}: {e}")
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
        
        Args:
            queries: List of search query strings.
            country: "Germany" or "USA"
            dtx_name: Name of the DTx
            classifier: LLM classifier for RCT/RWE determination
            max_results_per_query: Max results per query
            
        Returns:
            Dictionary with counts: {"rct": N, "rwe": M, "total": N+M}
        """
        all_results = []
        seen_ids = set()
        
        # Search with each query
        for query in queries:
            try:
                results = await self.search(query, max_results_per_query)
                
                # Deduplicate by study ID
                for result in results:
                    study_id = result.get("study_id") or result.get("pmid") or result.get("nct_id")
                    if study_id and study_id not in seen_ids:
                        seen_ids.add(study_id)
                        all_results.append(result)
                
                await asyncio.sleep(0.5)  # Rate limiting
                
            except Exception as e:
                print(f"    Error searching '{query}': {e}")
        
        if not all_results:
            return {"rct": 0, "rwe": 0, "total": 0}
        
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
            "total": len(all_results)
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
