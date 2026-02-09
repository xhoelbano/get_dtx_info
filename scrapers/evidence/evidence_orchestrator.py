"""Evidence orchestrator with two-layer classification support.

This module manages the search across all evidence sources (PubMed, 
ClinicalTrials.gov, DRKS, ISRCTN) for DTx products.

Two-Layer Classification System:
- Layer 1 (Candidates): Collect all search results without filtering
- Layer 2 (Verification): LLM verifies relevance before final classification
"""
import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from slugify import slugify

from .pubmed_scraper import PubMedScraper
from .clinicaltrials_scraper import ClinicalTrialsScraper
from .isrctn_scraper import ISRCTNScraper
from .drks_scraper import DRKSScraper


class EvidenceOrchestrator:
    """Orchestrate evidence collection with two-layer classification.
    
    Coordinates searches across PubMed, ClinicalTrials.gov, DRKS, and ISRCTN
    with support for:
    - Layer 1: Collect candidates (all search results)
    - Layer 2: LLM verification + RCT/RWE classification
    """
    
    # Available sources (ordered by reliability/speed)
    SOURCES = ["pubmed", "clinicaltrials", "drks", "isrctn"]
    
    # Sources that use Playwright (slower, may need Playwright installed)
    PLAYWRIGHT_SOURCES = ["drks", "isrctn"]
    
    def __init__(self, evidence_dir: str = "evidence"):
        """Initialize the orchestrator.
        
        Args:
            evidence_dir: Root directory for storing evidence files.
        """
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize scrapers (lazy - will fail gracefully if Playwright not installed)
        self._scrapers = {}
        self._scrapers_initialized = False
        
        # Utilities
        self.query_generator = None
        self.verifier = None
        self.classifier = None
    
    def _init_scrapers(self, sources: List[str] = None):
        """Initialize scrapers on demand."""
        if self._scrapers_initialized:
            return
        
        sources = sources or self.SOURCES
        
        # Always initialize API-based scrapers
        if "pubmed" in sources:
            self._scrapers["pubmed"] = PubMedScraper(str(self.evidence_dir))
        if "clinicaltrials" in sources:
            self._scrapers["clinicaltrials"] = ClinicalTrialsScraper(str(self.evidence_dir))
        
        # Try to initialize Playwright scrapers
        for source in self.PLAYWRIGHT_SOURCES:
            if source not in sources:
                continue
            try:
                if source == "drks":
                    self._scrapers["drks"] = DRKSScraper(str(self.evidence_dir))
                elif source == "isrctn":
                    self._scrapers["isrctn"] = ISRCTNScraper(str(self.evidence_dir))
            except ImportError:
                print(f"    Warning: Playwright not installed, skipping {source}")
        
        self._scrapers_initialized = True
    
    @property
    def scrapers(self):
        """Get initialized scrapers."""
        self._init_scrapers()
        return self._scrapers
    
    def set_utilities(self, query_generator, classifier=None, verifier=None):
        """Set the LLM utilities.
        
        Args:
            query_generator: SearchQueryGenerator instance.
            classifier: EvidenceClassifier or EvidenceClassifierV2 instance (optional for Layer 1).
            verifier: EvidenceVerifier instance (for Layer 2).
        """
        self.query_generator = query_generator
        self.classifier = classifier
        self.verifier = verifier
    
    async def close(self):
        """Clean up all scraper resources."""
        for scraper in self._scrapers.values():
            try:
                await scraper.close()
            except:
                pass
    
    # =====================================================================
    # LAYER 1: Collect Candidates
    # =====================================================================
    
    async def collect_candidates(
        self,
        dtx_data: Dict,
        country: str,
        sources: List[str] = None,
        max_results_per_query: int = 50
    ) -> Dict:
        """Layer 1: Collect all search results as candidates.
        
        No classification or filtering - just gather raw data from all sources.
        
        Args:
            dtx_data: Dictionary containing DTx metadata.
            country: "Germany" or "USA"
            sources: List of sources to search (defaults to all)
            max_results_per_query: Max results per query per source
            
        Returns:
            Dictionary with results from all sources.
        """
        if not self.query_generator:
            raise ValueError("Must call set_utilities() with query_generator before searching")
        
        dtx_name = dtx_data.get("dtx_name", "Unknown")
        sources = sources or self.SOURCES
        
        # Initialize only the scrapers we need
        self._init_scrapers(sources)
        
        print(f"\n  [Layer 1] Collecting candidates for: {dtx_name}")
        
        # Generate search queries (now deterministic: 2 queries max)
        queries = await self.query_generator.generate_queries(dtx_data)
        print(f"    Queries: {queries}")
        
        results = {
            "dtx_name": dtx_name,
            "country": country,
            "layer": "candidates",
            "search_date": datetime.utcnow().isoformat() + "Z",
            "queries": queries,
            "sources": {}
        }
        
        total_candidates = 0
        
        # Search each source
        for source in sources:
            if source not in self._scrapers:
                print(f"    Skipping {source} (not available)")
                continue
            
            scraper = self._scrapers[source]
            print(f"    Searching {source.upper()}...")
            
            try:
                source_results = await scraper.search_and_save_candidates(
                    queries=queries,
                    country=country,
                    dtx_name=dtx_name,
                    max_results_per_query=max_results_per_query
                )
                
                results["sources"][source] = source_results
                total_candidates += source_results.get("total", 0)
                
                print(f"      Found {source_results.get('total', 0)} candidates")
                
            except Exception as e:
                print(f"      Error: {e}")
                results["sources"][source] = {"error": str(e), "total": 0}
            
            # Rate limiting between sources
            await asyncio.sleep(1)
        
        results["total_candidates"] = total_candidates
        print(f"    Total candidates: {total_candidates}")
        
        return results
    
    # =====================================================================
    # LAYER 2: Verify and Classify
    # =====================================================================
    
    async def verify_and_classify(
        self,
        dtx_data: Dict,
        country: str,
        sources: List[str] = None
    ) -> Dict:
        """Layer 2: Verify candidate relevance and classify as RCT/RWE.
        
        Reads candidates from Layer 1 folder, verifies with LLM, and moves
        verified studies to the verified/ folder with RCT/RWE classification.
        
        Args:
            dtx_data: Dictionary containing DTx metadata.
            country: "Germany" or "USA"
            sources: List of sources to process (defaults to all)
            
        Returns:
            Dictionary with verification results.
        """
        if not self.verifier or not self.classifier:
            raise ValueError("Must call set_utilities() with verifier and classifier")
        
        dtx_name = dtx_data.get("dtx_name", "Unknown")
        sources = sources or self.SOURCES
        safe_name = slugify(dtx_name, max_length=50, lowercase=True)
        
        print(f"\n  [Layer 2] Verifying candidates for: {dtx_name}")
        
        results = {
            "dtx_name": dtx_name,
            "country": country,
            "layer": "verification",
            "verification_date": datetime.utcnow().isoformat() + "Z",
            "sources": {}
        }
        
        total_verified_rct = 0
        total_verified_rwe = 0
        total_rejected = 0
        
        # Process each source
        for source in sources:
            candidates_folder = self.evidence_dir / country / safe_name / "candidates" / source
            
            if not candidates_folder.exists():
                print(f"    Skipping {source} (no candidates)")
                continue
            
            studies_file = candidates_folder / "studies.json"
            if not studies_file.exists():
                continue
            
            print(f"    Processing {source.upper()} candidates...")
            
            try:
                # Load candidates
                with open(studies_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                candidates = data.get("studies", [])
                raw_folder = candidates_folder / "raw"
                
                # Verify batch
                relevant, rejected = await self.verifier.verify_candidates_batch(
                    candidates=candidates,
                    dtx_data=dtx_data,
                    raw_files_dir=raw_folder if raw_folder.exists() else None
                )
                
                print(f"      {len(relevant)} relevant, {len(rejected)} rejected")
                
                # Classify relevant studies
                rct_results = []
                rwe_results = []
                
                for study in relevant:
                    try:
                        classification = await self.classifier.classify(study)
                        study["_classification"] = classification
                        
                        if classification.get("classification") == "RCT":
                            rct_results.append(study)
                        else:
                            rwe_results.append(study)
                    except Exception as e:
                        # Default to RWE on error
                        study["_classification"] = {
                            "classification": "RWE",
                            "confidence": 50,
                            "reason": f"Classification error: {e}"
                        }
                        rwe_results.append(study)
                
                # Save verified results
                if rct_results:
                    self._save_verified_results(
                        country, dtx_name, source, "RCT",
                        rct_results, raw_folder
                    )
                    print(f"      Saved {len(rct_results)} RCT studies")
                
                if rwe_results:
                    self._save_verified_results(
                        country, dtx_name, source, "RWE",
                        rwe_results, raw_folder
                    )
                    print(f"      Saved {len(rwe_results)} RWE studies")
                
                # Save rejected studies (for debugging/review)
                if rejected:
                    self._save_rejected_studies(
                        country, dtx_name, source, rejected
                    )
                
                results["sources"][source] = {
                    "candidates": len(candidates),
                    "verified_rct": len(rct_results),
                    "verified_rwe": len(rwe_results),
                    "rejected": len(rejected)
                }
                
                total_verified_rct += len(rct_results)
                total_verified_rwe += len(rwe_results)
                total_rejected += len(rejected)
                
            except Exception as e:
                print(f"      Error: {e}")
                results["sources"][source] = {"error": str(e)}
        
        results["total_verified_rct"] = total_verified_rct
        results["total_verified_rwe"] = total_verified_rwe
        results["total_rejected"] = total_rejected
        
        print(f"    Verified: {total_verified_rct} RCT, {total_verified_rwe} RWE")
        print(f"    Rejected: {total_rejected}")
        
        return results
    
    def _save_verified_results(
        self,
        country: str,
        dtx_name: str,
        source: str,
        evidence_type: str,
        studies: List[Dict],
        candidates_raw_folder: Path
    ):
        """Save verified studies to the verified folder.
        
        Also copies raw files from candidates to verified.
        """
        safe_name = slugify(dtx_name, max_length=50, lowercase=True)
        verified_folder = self.evidence_dir / country / safe_name / "verified" / evidence_type / source
        verified_folder.mkdir(parents=True, exist_ok=True)
        
        # Save studies.json
        studies_file = verified_folder / "studies.json"
        with open(studies_file, "w", encoding="utf-8") as f:
            json.dump({
                "studies": studies,
                "count": len(studies),
                "_saved_at": datetime.utcnow().isoformat() + "Z",
                "_source": source,
                "_layer": "verified",
                "_evidence_type": evidence_type
            }, f, indent=2, ensure_ascii=False)
        
        # Copy raw files
        if candidates_raw_folder and candidates_raw_folder.exists():
            raw_folder = verified_folder / "raw"
            raw_folder.mkdir(parents=True, exist_ok=True)
            
            for study in studies:
                # Find the study ID
                study_id = (
                    study.get("pmid") or 
                    study.get("nct_id") or 
                    study.get("drks_id") or 
                    study.get("isrctn_id") or
                    study.get("study_id")
                )
                
                if not study_id:
                    continue
                
                # Try to copy raw file
                for ext in ["xml", "json"]:
                    src_file = candidates_raw_folder / f"{study_id}.{ext}"
                    if src_file.exists():
                        dst_file = raw_folder / f"{study_id}.{ext}"
                        shutil.copy2(src_file, dst_file)
                        break
    
    def _save_rejected_studies(
        self,
        country: str,
        dtx_name: str,
        source: str,
        rejected: List[Dict]
    ):
        """Save rejected studies for debugging/review."""
        safe_name = slugify(dtx_name, max_length=50, lowercase=True)
        rejected_folder = self.evidence_dir / country / safe_name / "rejected" / source
        rejected_folder.mkdir(parents=True, exist_ok=True)
        
        rejected_file = rejected_folder / "rejected.json"
        with open(rejected_file, "w", encoding="utf-8") as f:
            json.dump({
                "studies": rejected,
                "count": len(rejected),
                "_saved_at": datetime.utcnow().isoformat() + "Z",
                "_source": source
            }, f, indent=2, ensure_ascii=False)
    
    # =====================================================================
    # Combined workflow
    # =====================================================================
    
    async def search_dtx(
        self,
        dtx_data: Dict,
        country: str,
        sources: List[str] = None,
        candidates_only: bool = False,
        verify_only: bool = False,
        max_results_per_query: int = 50
    ) -> Dict:
        """Full two-layer search for a single DTx.
        
        Args:
            dtx_data: Dictionary containing DTx metadata.
            country: "Germany" or "USA"
            sources: List of sources to search (defaults to all)
            candidates_only: If True, only collect candidates (Layer 1)
            verify_only: If True, only verify existing candidates (Layer 2)
            max_results_per_query: Max results per query per source
            
        Returns:
            Dictionary with results from both layers.
        """
        results = {
            "dtx_name": dtx_data.get("dtx_name", "Unknown"),
            "country": country,
            "search_date": datetime.utcnow().isoformat() + "Z"
        }
        
        # Layer 1: Collect candidates
        if not verify_only:
            candidates_result = await self.collect_candidates(
                dtx_data=dtx_data,
                country=country,
                sources=sources,
                max_results_per_query=max_results_per_query
            )
            results["candidates"] = candidates_result
        
        # Layer 2: Verify and classify
        if not candidates_only:
            if self.verifier and self.classifier:
                verification_result = await self.verify_and_classify(
                    dtx_data=dtx_data,
                    country=country,
                    sources=sources
                )
                results["verification"] = verification_result
            else:
                print("    Skipping verification (verifier/classifier not set)")
        
        return results
    
    async def search_all_dtx(
        self,
        dtx_list: List[Dict],
        country: str,
        sources: List[str] = None,
        candidates_only: bool = False,
        verify_only: bool = False,
        max_results_per_query: int = 50
    ) -> Dict:
        """Search evidence for all DTx in a list.
        
        Args:
            dtx_list: List of DTx metadata dictionaries.
            country: "Germany" or "USA"
            sources: List of sources to search
            candidates_only: If True, only collect candidates (Layer 1)
            verify_only: If True, only verify existing candidates (Layer 2)
            max_results_per_query: Max results per query per source
            
        Returns:
            Dictionary with overall statistics.
        """
        mode = "candidates only" if candidates_only else "verify only" if verify_only else "full"
        print(f"\nSearching evidence for {len(dtx_list)} {country} DTx products (mode: {mode})...")
        
        total_stats = {
            "country": country,
            "search_date": datetime.utcnow().isoformat() + "Z",
            "mode": mode,
            "dtx_searched": 0,
            "total_candidates": 0,
            "total_verified_rct": 0,
            "total_verified_rwe": 0,
            "total_rejected": 0,
            "by_dtx": {}
        }
        
        for i, dtx_data in enumerate(dtx_list, 1):
            dtx_name = dtx_data.get("dtx_name", "Unknown")
            print(f"\n[{i}/{len(dtx_list)}] {dtx_name}")
            
            try:
                results = await self.search_dtx(
                    dtx_data=dtx_data,
                    country=country,
                    sources=sources,
                    candidates_only=candidates_only,
                    verify_only=verify_only,
                    max_results_per_query=max_results_per_query
                )
                
                total_stats["dtx_searched"] += 1
                
                # Aggregate candidates
                if "candidates" in results:
                    total_stats["total_candidates"] += results["candidates"].get("total_candidates", 0)
                
                # Aggregate verification
                if "verification" in results:
                    v = results["verification"]
                    total_stats["total_verified_rct"] += v.get("total_verified_rct", 0)
                    total_stats["total_verified_rwe"] += v.get("total_verified_rwe", 0)
                    total_stats["total_rejected"] += v.get("total_rejected", 0)
                
                total_stats["by_dtx"][dtx_name] = results
                
            except Exception as e:
                print(f"    Error processing {dtx_name}: {e}")
                total_stats["by_dtx"][dtx_name] = {"error": str(e)}
            
            # Rate limiting between DTx
            await asyncio.sleep(2)
        
        # Save summary
        self._save_country_summary(country, total_stats)
        
        return total_stats
    
    # =====================================================================
    # Legacy support
    # =====================================================================
    
    async def search_dtx_legacy(
        self,
        dtx_data: Dict,
        country: str,
        sources: List[str] = None,
        download_pdfs: bool = True,
        max_results_per_query: int = 30
    ) -> Dict:
        """Legacy single-pass search (deprecated).
        
        Use search_dtx() with the two-layer system instead.
        """
        if not self.query_generator or not self.classifier:
            raise ValueError("Must call set_utilities() before searching")
        
        dtx_name = dtx_data.get("dtx_name", "Unknown")
        sources = sources or self.SOURCES
        
        self._init_scrapers(sources)
        
        print(f"\n  [Legacy] Searching evidence for: {dtx_name}")
        
        queries = await self.query_generator.generate_queries(dtx_data)
        print(f"    Generated {len(queries)} search queries")
        
        results = {
            "dtx_name": dtx_name,
            "country": country,
            "search_date": datetime.utcnow().isoformat() + "Z",
            "queries": queries,
            "sources": {}
        }
        
        total_rct = 0
        total_rwe = 0
        
        for source in sources:
            if source not in self._scrapers:
                continue
            
            scraper = self._scrapers[source]
            print(f"    Searching {source.upper()}...")
            
            try:
                if source == "pubmed":
                    source_results = await scraper.search_and_save_with_pdfs(
                        queries=queries,
                        country=country,
                        dtx_name=dtx_name,
                        classifier=self.classifier,
                        max_results_per_query=max_results_per_query,
                        download_pdfs=download_pdfs
                    )
                else:
                    source_results = await scraper.search_and_save(
                        queries=queries,
                        country=country,
                        dtx_name=dtx_name,
                        classifier=self.classifier,
                        max_results_per_query=max_results_per_query
                    )
                
                results["sources"][source] = source_results
                total_rct += source_results.get("rct", 0)
                total_rwe += source_results.get("rwe", 0)
                
                print(f"      Found: {source_results.get('rct', 0)} RCT, {source_results.get('rwe', 0)} RWE")
                
            except Exception as e:
                print(f"      Error: {e}")
                results["sources"][source] = {"error": str(e), "rct": 0, "rwe": 0}
            
            await asyncio.sleep(1)
        
        results["total_rct"] = total_rct
        results["total_rwe"] = total_rwe
        
        return results
    
    # =====================================================================
    # Reporting
    # =====================================================================
    
    def _save_country_summary(self, country: str, stats: Dict):
        """Save country-level evidence summary."""
        summary_dir = self.evidence_dir / "summary"
        summary_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"{country.lower()}_evidence_summary.json"
        filepath = summary_dir / filename
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        
        print(f"\nSummary saved to: {filepath}")
    
    def load_country_summary(self, country: str) -> Optional[Dict]:
        """Load existing country summary if available."""
        filepath = self.evidence_dir / "summary" / f"{country.lower()}_evidence_summary.json"
        
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return None
    
    def get_overall_statistics(self) -> Dict:
        """Get overall statistics with two-layer structure."""
        stats = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "countries": {}
        }
        
        for country in ["Germany", "USA"]:
            country_dir = self.evidence_dir / country
            if not country_dir.exists():
                continue
            
            country_stats = {
                "dtx_count": 0,
                "candidates_count": 0,
                "verified_rct_count": 0,
                "verified_rwe_count": 0,
                "rejected_count": 0,
                "by_source": {}
            }
            
            for dtx_folder in country_dir.iterdir():
                if not dtx_folder.is_dir():
                    continue
                
                country_stats["dtx_count"] += 1
                
                # Count candidates
                candidates_dir = dtx_folder / "candidates"
                if candidates_dir.exists():
                    for source_folder in candidates_dir.iterdir():
                        if source_folder.is_dir():
                            source = source_folder.name
                            studies_file = source_folder / "studies.json"
                            if studies_file.exists():
                                try:
                                    with open(studies_file, "r") as f:
                                        data = json.load(f)
                                        count = data.get("count", 0)
                                        country_stats["candidates_count"] += count
                                except:
                                    pass
                
                # Count verified
                verified_dir = dtx_folder / "verified"
                if verified_dir.exists():
                    for evidence_type in ["RCT", "RWE"]:
                        type_folder = verified_dir / evidence_type
                        if not type_folder.exists():
                            continue
                        
                        for source_folder in type_folder.iterdir():
                            if source_folder.is_dir():
                                source = source_folder.name
                                
                                if source not in country_stats["by_source"]:
                                    country_stats["by_source"][source] = {
                                        "candidates": 0, "rct": 0, "rwe": 0, "rejected": 0
                                    }
                                
                                studies_file = source_folder / "studies.json"
                                if studies_file.exists():
                                    try:
                                        with open(studies_file, "r") as f:
                                            data = json.load(f)
                                            count = data.get("count", 0)
                                            
                                            if evidence_type == "RCT":
                                                country_stats["verified_rct_count"] += count
                                                country_stats["by_source"][source]["rct"] += count
                                            else:
                                                country_stats["verified_rwe_count"] += count
                                                country_stats["by_source"][source]["rwe"] += count
                                    except:
                                        pass
                
                # Count rejected
                rejected_dir = dtx_folder / "rejected"
                if rejected_dir.exists():
                    for source_folder in rejected_dir.iterdir():
                        if source_folder.is_dir():
                            rejected_file = source_folder / "rejected.json"
                            if rejected_file.exists():
                                try:
                                    with open(rejected_file, "r") as f:
                                        data = json.load(f)
                                        count = data.get("count", 0)
                                        country_stats["rejected_count"] += count
                                except:
                                    pass
            
            stats["countries"][country] = country_stats
        
        # Calculate totals
        stats["total_dtx"] = sum(c.get("dtx_count", 0) for c in stats["countries"].values())
        stats["total_candidates"] = sum(c.get("candidates_count", 0) for c in stats["countries"].values())
        stats["total_verified_rct"] = sum(c.get("verified_rct_count", 0) for c in stats["countries"].values())
        stats["total_verified_rwe"] = sum(c.get("verified_rwe_count", 0) for c in stats["countries"].values())
        stats["total_rejected"] = sum(c.get("rejected_count", 0) for c in stats["countries"].values())
        
        return stats
    
    def generate_report(self) -> str:
        """Generate a text report of evidence collection status."""
        stats = self.get_overall_statistics()
        
        lines = [
            "=" * 60,
            "CLINICAL EVIDENCE COLLECTION REPORT (Two-Layer System)",
            f"Generated: {stats['generated_at']}",
            "=" * 60,
            "",
            f"Total DTx with evidence searches: {stats['total_dtx']}",
            f"Total candidates collected: {stats['total_candidates']}",
            f"Total verified RCT studies: {stats['total_verified_rct']}",
            f"Total verified RWE studies: {stats['total_verified_rwe']}",
            f"Total rejected (false positives): {stats['total_rejected']}",
            "",
        ]
        
        for country, country_stats in stats.get("countries", {}).items():
            lines.append(f"--- {country} ---")
            lines.append(f"  DTx products: {country_stats.get('dtx_count', 0)}")
            lines.append(f"  Candidates: {country_stats.get('candidates_count', 0)}")
            lines.append(f"  Verified RCT: {country_stats.get('verified_rct_count', 0)}")
            lines.append(f"  Verified RWE: {country_stats.get('verified_rwe_count', 0)}")
            lines.append(f"  Rejected: {country_stats.get('rejected_count', 0)}")
            
            by_source = country_stats.get("by_source", {})
            if by_source:
                lines.append("  By source:")
                for source, counts in by_source.items():
                    lines.append(f"    {source}: {counts.get('rct', 0)} RCT, {counts.get('rwe', 0)} RWE")
            lines.append("")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)
