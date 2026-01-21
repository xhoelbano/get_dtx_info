"""Data manager for JSON read/write with incremental support."""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional


class DataManager:
    """Manages DTx data storage with incremental update support."""
    
    def __init__(self, data_dir: str = "data"):
        """Initialize the data manager.
        
        Args:
            data_dir: Directory to store data files.
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.dtx_file = self.data_dir / "dtx_data.json"
        self.evidence_file = self.data_dir / "evidence_metadata.json"
    
    def _get_empty_dtx_data(self, country: str = "Germany") -> dict:
        """Return empty DTx data structure."""
        return {
            "metadata": {
                "country": country,
                "last_updated": None,
                "total_count": 0,
                "active_count": 0,
                "provisional_count": 0,
                "delisted_count": 0
            },
            "dtx_list": []
        }
    
    def load_dtx_data(self) -> dict:
        """Load existing DTx data from file.
        
        Returns:
            Dictionary containing DTx data or empty structure if file doesn't exist.
        """
        if not self.dtx_file.exists():
            return self._get_empty_dtx_data()
        
        with open(self.dtx_file, "r", encoding="utf-8") as f:
            return json.load(f)
    
    def save_dtx_data(self, data: dict):
        """Save DTx data to file.
        
        Args:
            data: Dictionary containing DTx data.
        """
        # Update metadata timestamp
        data["metadata"]["last_updated"] = datetime.utcnow().isoformat() + "Z"
        
        # Update counts
        dtx_list = data.get("dtx_list", [])
        data["metadata"]["total_count"] = len(dtx_list)
        data["metadata"]["active_count"] = sum(
            1 for d in dtx_list if d.get("listing_status") == "Permanently listed"
        )
        data["metadata"]["provisional_count"] = sum(
            1 for d in dtx_list if d.get("listing_status") == "Provisionally listed"
        )
        data["metadata"]["delisted_count"] = sum(
            1 for d in dtx_list if d.get("listing_status") == "Delisted"
        )
        
        with open(self.dtx_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def get_existing_dtx_urls(self) -> dict:
        """Get mapping of source URLs to their last scraped timestamps.
        
        Returns:
            Dictionary mapping source_url to last_scraped timestamp.
        """
        data = self.load_dtx_data()
        return {
            dtx["source_url"]: dtx.get("last_scraped")
            for dtx in data.get("dtx_list", [])
            if dtx.get("source_url")
        }
    
    def update_dtx(self, dtx_data: dict, mode: str = "incremental") -> dict:
        """Update DTx data with new entries.
        
        Args:
            dtx_data: New DTx data to add/update.
            mode: "incremental" to merge, "full" to replace.
            
        Returns:
            Updated full data dictionary.
        """
        if mode == "full":
            # Full refresh - create new data structure
            data = self._get_empty_dtx_data(
                dtx_data.get("metadata", {}).get("country", "Germany")
            )
            data["dtx_list"] = dtx_data.get("dtx_list", [])
        else:
            # Incremental update
            data = self.load_dtx_data()
            existing_urls = {dtx["source_url"]: i for i, dtx in enumerate(data["dtx_list"])}
            
            for new_dtx in dtx_data.get("dtx_list", []):
                url = new_dtx.get("source_url")
                if url in existing_urls:
                    # Update existing entry
                    data["dtx_list"][existing_urls[url]] = new_dtx
                else:
                    # Add new entry
                    data["dtx_list"].append(new_dtx)
        
        self.save_dtx_data(data)
        return data
    
    def load_evidence_data(self) -> dict:
        """Load existing evidence data from file.
        
        Returns:
            Dictionary containing evidence data with RCT/RWE separation.
        """
        default_data = {
            "metadata": {
                "last_updated": None,
                "total_rct": 0,
                "total_rwe": 0
            },
            "evidence_by_dtx": {}
        }
        
        if not self.evidence_file.exists():
            return default_data
        
        try:
            with open(self.evidence_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return default_data
                return json.loads(content)
        except json.JSONDecodeError:
            # File is corrupted, return default
            return default_data
    
    def save_evidence_data(self, data: dict):
        """Save evidence data to file.
        
        Args:
            data: Dictionary containing evidence data.
        """
        data["metadata"] = data.get("metadata", {})
        data["metadata"]["last_updated"] = datetime.utcnow().isoformat() + "Z"
        
        # Count totals
        total_rct = 0
        total_rwe = 0
        for dtx_data in data.get("evidence_by_dtx", {}).values():
            total_rct += len(dtx_data.get("RCT", []))
            total_rwe += len(dtx_data.get("RWE", []))
        
        data["metadata"]["total_rct"] = total_rct
        data["metadata"]["total_rwe"] = total_rwe
        
        with open(self.evidence_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def add_evidence_for_dtx(self, dtx_name: str, evidence_result: dict):
        """Add evidence results for a DTx (with RCT/RWE separation).
        
        Args:
            dtx_name: Name of the DTx.
            evidence_result: Dictionary with 'RCT' and 'RWE' lists.
        """
        data = self.load_evidence_data()
        
        if dtx_name not in data["evidence_by_dtx"]:
            data["evidence_by_dtx"][dtx_name] = {
                "search_date": evidence_result.get("search_date"),
                "queries_used": evidence_result.get("queries_used", []),
                "RCT": [],
                "RWE": []
            }
        
        # Add RCT papers (avoid duplicates)
        existing_rct_titles = {e.get("title", "").lower() for e in data["evidence_by_dtx"][dtx_name].get("RCT", [])}
        for paper in evidence_result.get("RCT", []):
            title = paper.get("title", "").lower()
            if title and title not in existing_rct_titles:
                data["evidence_by_dtx"][dtx_name]["RCT"].append(paper)
                existing_rct_titles.add(title)
        
        # Add RWE papers (avoid duplicates)
        existing_rwe_titles = {e.get("title", "").lower() for e in data["evidence_by_dtx"][dtx_name].get("RWE", [])}
        for paper in evidence_result.get("RWE", []):
            title = paper.get("title", "").lower()
            if title and title not in existing_rwe_titles:
                data["evidence_by_dtx"][dtx_name]["RWE"].append(paper)
                existing_rwe_titles.add(title)
        
        self.save_evidence_data(data)
    
    def add_evidence(self, dtx_name: str, evidence: dict):
        """Add a single evidence entry for a DTx (legacy support).
        
        Args:
            dtx_name: Name of the DTx.
            evidence: Evidence data dictionary.
        """
        data = self.load_evidence_data()
        
        if dtx_name not in data["evidence_by_dtx"]:
            data["evidence_by_dtx"][dtx_name] = {"RCT": [], "RWE": []}
        
        # Determine type
        evidence_type = evidence.get("evidence_type", "RCT")
        target_list = "RCT" if evidence_type == "RCT" else "RWE"
        
        # Check for duplicates by title
        existing = data["evidence_by_dtx"][dtx_name].get(target_list, [])
        title = evidence.get("title", "").lower()
        
        is_duplicate = any(e.get("title", "").lower() == title for e in existing)
        
        if not is_duplicate and title:
            if target_list not in data["evidence_by_dtx"][dtx_name]:
                data["evidence_by_dtx"][dtx_name][target_list] = []
            data["evidence_by_dtx"][dtx_name][target_list].append(evidence)
            self.save_evidence_data(data)
