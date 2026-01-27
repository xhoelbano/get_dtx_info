"""Evidence orchestrator to coordinate all evidence scrapers.

This module manages the search across all evidence sources (PubMed, 
ClinicalTrials.gov, DRKS, ISRCTN) for DTx products and generates summaries.
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from .pubmed_scraper import PubMedScraper
from .clinicaltrials_scraper import ClinicalTrialsScraper
from .isrctn_scraper import ISRCTNScraper
from .drks_scraper import DRKSScraper


class EvidenceOrchestrator:
    """Orchestrate evidence collection across all sources.
    
    Coordinates searches across PubMed, ClinicalTrials.gov, DRKS, and ISRCTN
    for each DTx product and generates summary reports.
    """
    
    # Available sources
    SOURCES = ["pubmed", "clinicaltrials", "isrctn", "drks"]
    
    def __init__(self, evidence_dir: str = "evidence"):
        """Initialize the orchestrator.
        
        Args:
            evidence_dir: Root directory for storing evidence files.
        """
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize scrapers
        self.scrapers = {
            "pubmed": PubMedScraper(evidence_dir),
            "clinicaltrials": ClinicalTrialsScraper(evidence_dir),
            "isrctn": ISRCTNScraper(evidence_dir),
            "drks": DRKSScraper(evidence_dir)
        }
        
        # Will be set during search
        self.query_generator = None
        self.classifier = None
    
    def set_utilities(self, query_generator, classifier):
        """Set the LLM utilities for query generation and classification.
        
        Args:
            query_generator: SearchQueryGenerator instance.
            classifier: EvidenceClassifier instance.
        """
        self.query_generator = query_generator
        self.classifier = classifier
    
    async def close(self):
        """Clean up all scraper resources."""
        for scraper in self.scrapers.values():
            try:
                await scraper.close()
            except:
                pass
    
    async def search_dtx(
        self,
        dtx_data: Dict,
        country: str,
        sources: List[str] = None,
        download_pdfs: bool = True,
        max_results_per_query: int = 30
    ) -> Dict:
        """Search all sources for evidence about a DTx.
        
        Args:
            dtx_data: Dictionary containing DTx metadata.
            country: "Germany" or "USA" (origin country of the DTx)
            sources: List of sources to search (defaults to all)
            download_pdfs: Whether to download PDFs from PubMed
            max_results_per_query: Max results per query per source
            
        Returns:
            Dictionary with results from all sources.
        """
        if not self.query_generator or not self.classifier:
            raise ValueError("Must call set_utilities() before searching")
        
        dtx_name = dtx_data.get("dtx_name", "Unknown")
        sources = sources or self.SOURCES
        
        print(f"\n  Searching evidence for: {dtx_name}")
        
        # Generate search queries
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
        
        # Search each source
        for source in sources:
            if source not in self.scrapers:
                print(f"    Unknown source: {source}")
                continue
            
            scraper = self.scrapers[source]
            print(f"    Searching {source.upper()}...")
            
            try:
                if source == "pubmed":
                    # PubMed has special PDF handling
                    source_results = await scraper.search_and_save_with_pdfs(
                        queries=queries,
                        country=country,
                        dtx_name=dtx_name,
                        classifier=self.classifier,
                        max_results_per_query=max_results_per_query,
                        download_pdfs=download_pdfs
                    )
                else:
                    # Other sources
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
            
            # Rate limiting between sources
            await asyncio.sleep(1)
        
        results["total_rct"] = total_rct
        results["total_rwe"] = total_rwe
        
        print(f"    Total: {total_rct} RCT, {total_rwe} RWE across all sources")
        
        return results
    
    async def search_all_dtx(
        self,
        dtx_list: List[Dict],
        country: str,
        sources: List[str] = None,
        download_pdfs: bool = True,
        max_results_per_query: int = 30
    ) -> Dict:
        """Search evidence for all DTx in a list.
        
        Args:
            dtx_list: List of DTx metadata dictionaries.
            country: "Germany" or "USA"
            sources: List of sources to search
            download_pdfs: Whether to download PDFs
            max_results_per_query: Max results per query per source
            
        Returns:
            Dictionary with overall statistics.
        """
        print(f"\nSearching evidence for {len(dtx_list)} {country} DTx products...")
        
        total_stats = {
            "country": country,
            "search_date": datetime.utcnow().isoformat() + "Z",
            "dtx_searched": 0,
            "dtx_with_evidence": 0,
            "total_rct": 0,
            "total_rwe": 0,
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
                    download_pdfs=download_pdfs,
                    max_results_per_query=max_results_per_query
                )
                
                total_stats["dtx_searched"] += 1
                
                if results["total_rct"] > 0 or results["total_rwe"] > 0:
                    total_stats["dtx_with_evidence"] += 1
                
                total_stats["total_rct"] += results["total_rct"]
                total_stats["total_rwe"] += results["total_rwe"]
                total_stats["by_dtx"][dtx_name] = {
                    "rct": results["total_rct"],
                    "rwe": results["total_rwe"],
                    "sources": results["sources"]
                }
                
            except Exception as e:
                print(f"    Error processing {dtx_name}: {e}")
                total_stats["by_dtx"][dtx_name] = {"error": str(e)}
            
            # Rate limiting between DTx
            await asyncio.sleep(2)
        
        # Save summary
        self._save_country_summary(country, total_stats)
        
        return total_stats
    
    def _save_country_summary(self, country: str, stats: Dict):
        """Save country-level evidence summary.
        
        Args:
            country: "Germany" or "USA"
            stats: Statistics dictionary.
        """
        summary_dir = self.evidence_dir / "summary"
        summary_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"{country.lower()}_evidence_summary.json"
        filepath = summary_dir / filename
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        
        print(f"\nSummary saved to: {filepath}")
    
    def load_country_summary(self, country: str) -> Optional[Dict]:
        """Load existing country summary if available.
        
        Args:
            country: "Germany" or "USA"
            
        Returns:
            Summary dictionary or None.
        """
        filepath = self.evidence_dir / "summary" / f"{country.lower()}_evidence_summary.json"
        
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return None
    
    def get_overall_statistics(self) -> Dict:
        """Get overall statistics across all countries and sources.
        
        Returns:
            Dictionary with comprehensive statistics.
        """
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
                "rct_count": 0,
                "rwe_count": 0,
                "by_source": {}
            }
            
            # Count DTx folders
            for dtx_folder in country_dir.iterdir():
                if dtx_folder.is_dir():
                    country_stats["dtx_count"] += 1
                    
                    # Count by source
                    for evidence_type in ["RCT", "RWE"]:
                        type_folder = dtx_folder / evidence_type
                        if not type_folder.exists():
                            continue
                        
                        for source_folder in type_folder.iterdir():
                            if source_folder.is_dir():
                                source = source_folder.name
                                
                                if source not in country_stats["by_source"]:
                                    country_stats["by_source"][source] = {"rct": 0, "rwe": 0}
                                
                                # Count studies
                                studies_file = source_folder / "studies.json"
                                if studies_file.exists():
                                    try:
                                        with open(studies_file, "r") as f:
                                            data = json.load(f)
                                            count = data.get("count", 0)
                                            
                                            if evidence_type == "RCT":
                                                country_stats["rct_count"] += count
                                                country_stats["by_source"][source]["rct"] += count
                                            else:
                                                country_stats["rwe_count"] += count
                                                country_stats["by_source"][source]["rwe"] += count
                                    except:
                                        pass
            
            stats["countries"][country] = country_stats
        
        # Calculate totals
        stats["total_dtx"] = sum(c.get("dtx_count", 0) for c in stats["countries"].values())
        stats["total_rct"] = sum(c.get("rct_count", 0) for c in stats["countries"].values())
        stats["total_rwe"] = sum(c.get("rwe_count", 0) for c in stats["countries"].values())
        
        return stats
    
    def generate_report(self) -> str:
        """Generate a text report of evidence collection status.
        
        Returns:
            Formatted report string.
        """
        stats = self.get_overall_statistics()
        
        lines = [
            "=" * 60,
            "CLINICAL EVIDENCE COLLECTION REPORT",
            f"Generated: {stats['generated_at']}",
            "=" * 60,
            "",
            f"Total DTx with evidence searches: {stats['total_dtx']}",
            f"Total RCT studies found: {stats['total_rct']}",
            f"Total RWE studies found: {stats['total_rwe']}",
            "",
        ]
        
        for country, country_stats in stats.get("countries", {}).items():
            lines.append(f"--- {country} ---")
            lines.append(f"  DTx products: {country_stats.get('dtx_count', 0)}")
            lines.append(f"  RCT studies: {country_stats.get('rct_count', 0)}")
            lines.append(f"  RWE studies: {country_stats.get('rwe_count', 0)}")
            
            by_source = country_stats.get("by_source", {})
            if by_source:
                lines.append("  By source:")
                for source, counts in by_source.items():
                    lines.append(f"    {source}: {counts.get('rct', 0)} RCT, {counts.get('rwe', 0)} RWE")
            lines.append("")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)
