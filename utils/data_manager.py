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
            Dictionary containing evidence data.
        """
        if not self.evidence_file.exists():
            return {"evidence_by_dtx": {}}
        
        with open(self.evidence_file, "r", encoding="utf-8") as f:
            return json.load(f)
    
    def save_evidence_data(self, data: dict):
        """Save evidence data to file.
        
        Args:
            data: Dictionary containing evidence data.
        """
        data["last_updated"] = datetime.utcnow().isoformat() + "Z"
        
        with open(self.evidence_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def add_evidence(self, dtx_name: str, evidence: dict):
        """Add evidence entry for a DTx.
        
        Args:
            dtx_name: Name of the DTx.
            evidence: Evidence data dictionary.
        """
        data = self.load_evidence_data()
        
        if dtx_name not in data["evidence_by_dtx"]:
            data["evidence_by_dtx"][dtx_name] = []
        
        # Check for duplicates by DOI or title
        existing = data["evidence_by_dtx"][dtx_name]
        doi = evidence.get("doi")
        title = evidence.get("title", "").lower()
        
        is_duplicate = any(
            e.get("doi") == doi or e.get("title", "").lower() == title
            for e in existing
        )
        
        if not is_duplicate:
            data["evidence_by_dtx"][dtx_name].append(evidence)
            self.save_evidence_data(data)
