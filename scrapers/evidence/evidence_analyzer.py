"""LLM-based evidence analyzer for verified raw evidence files.

.. deprecated::
    Superseded by ``scrapers/evidence/phase3_analyzer.py`` (the Phase 3 pipeline
    used by the ``analyze-evidence`` command). That module is candidate-centric,
    groups rows per DTx to match the benchmarking xlsx, prefers already-scraped
    study fields before calling the LLM, tracks per-field provenance, and is
    configurable per model. This file is kept for reference only.

Walks through every verified raw evidence file (JSON, XML, HTML),
sends the content to the configured LLM to extract structured study
information, merges with pre-filled DiGA metadata from dtx_data.json,
and outputs a combined JSON (optionally CSV).

The output schema is driven entirely by data-format/evidence_analysis.json.
To add, remove, or rename fields, edit that file — no code changes needed.
"""

import csv
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from utils.data_manager import DataManager
from utils.llm_provider import LLMProvider

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path("data-format/evidence_analysis.json")

SYSTEM_PROMPT_TEMPLATE = """\
You are a clinical evidence data extraction specialist. You will be given the raw content \
of a clinical study file (from ClinicalTrials.gov, DRKS, ISRCTN, PubMed, or a website). \
Your task is to extract structured information and return it as a JSON object.

CRITICAL RULES:
1. Extract ONLY information that is explicitly present in the provided data.
2. If a field is NOT found in the data, return an empty string "" for that field.
3. Do NOT hallucinate, guess, infer, or add any information not present in the raw data.
4. Do NOT search the web or use any external knowledge.
5. Return ONLY a valid JSON object — no markdown, no explanation, no code fences.

SCHEMA — fill each field from the raw data (return every key exactly as shown):

{schema_json}

Remember: return ONLY the JSON object with ALL the keys above, nothing else.\
"""

MAX_RAW_CONTENT_CHARS = 80_000


def _load_schema() -> Dict[str, str]:
    """Load the analysis schema from the JSON file."""
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"Schema file not found: {SCHEMA_PATH}. "
            "Create it at data-format/evidence_analysis.json."
        )
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


class EvidenceAnalyzer:
    """Analyzes verified raw evidence files using an LLM."""

    def __init__(
        self,
        data_manager: DataManager,
        limit: Optional[int] = None,
        model_override: Optional[str] = None,
    ):
        self.data_manager = data_manager
        self.limit = limit
        self.model_override = model_override
        self.evidence_dir = Path("evidence")

        self.schema = _load_schema()
        self.schema_fields = list(self.schema.keys())

        schema_json = json.dumps(self.schema, indent=2, ensure_ascii=False)
        self.system_prompt = SYSTEM_PROMPT_TEMPLATE.replace("{schema_json}", schema_json)

        self.llm = LLMProvider.get_llm(
            temperature=0.0,
            max_tokens=4000,
            model_override=model_override,
        )
        self.llm_source_name = LLMProvider.get_source_name()

        self._dtx_data: Optional[Dict] = None
        self._slug_map: Optional[Dict[str, Dict]] = None

    # ------------------------------------------------------------------
    # DiGA metadata helpers
    # ------------------------------------------------------------------

    def _load_dtx_metadata(self) -> None:
        """Load dtx_data.json and build a slug -> entry mapping."""
        if self._dtx_data is not None:
            return
        self._dtx_data = self.data_manager.load_dtx_data()
        self._slug_map = {}
        for entry in self._dtx_data.get("dtx_list", []):
            slug = self._name_to_slug(entry.get("dtx_name", ""))
            if slug:
                self._slug_map[slug] = entry

    @staticmethod
    def _name_to_slug(name: str) -> str:
        """Convert a DTx name to its filesystem slug (mirrors evidence folder names)."""
        slug = name.lower().strip()
        slug = re.sub(r"[^a-z0-9äöüß]+", "-", slug)
        slug = slug.strip("-")
        if len(slug) > 50:
            slug = slug[:50].rstrip("-")
        return slug

    # Mapping from schema field names to dtx_data.json extraction logic.
    # Each value is a callable(entry) -> str.  Only fields that appear in
    # the current schema (loaded from evidence_analysis.json) are used.
    _DTX_FIELD_MAP: Dict[str, Any] = {}

    @classmethod
    def _init_field_map(cls) -> None:
        """Build the mapping once (lazy)."""
        if cls._DTX_FIELD_MAP:
            return

        def _company(e: dict) -> str:
            raw = e.get("company_provider", "")
            if raw:
                lines = raw.strip().split("\n")
                return lines[-1].strip() if lines else raw
            return ""

        def _listing(e: dict) -> str:
            return e.get("listing_status", "")

        def _removed(e: dict) -> str:
            ls = e.get("listing_status", "")
            if ls == "Delisted":
                return "Yes"
            return "No" if ls else ""

        def _icd10(e: dict) -> str:
            v = e.get("clinical_area_icd10", "")
            return ", ".join(v) if isinstance(v, list) else str(v)

        def _store_field(store_key: str, field: str):
            def _fn(e: dict) -> str:
                store = e.get(store_key) or {}
                val = store.get(field)
                return str(val) if val else ""
            return _fn

        def _web_based(e: dict) -> str:
            if e.get("web_app_url"):
                return "Yes"
            return "No" if e.get("dtx_name") else ""

        cls._DTX_FIELD_MAP = {
            "diga_app_name":          lambda e: e.get("dtx_name", ""),
            "dtx_name":               lambda e: e.get("dtx_name", ""),
            "company_provider":       _company,
            "listing":                _listing,
            "removed_from_diga_listing": _removed,
            "diga_listing":           lambda e: e.get("date_of_first_listing", ""),
            "clinical_area_icd10":    _icd10,
            "rating_on_playstore":    _store_field("reviews_playstore", "rating"),
            "reviews_on_playstore":   _store_field("reviews_playstore", "review_count"),
            "rating_on_appstore":     _store_field("reviews_appstore", "rating"),
            "reviews_on_appstore":    _store_field("reviews_appstore", "review_count"),
            "web_based":              _web_based,
            "reasons_of_delisting":   lambda e: e.get("reason_for_delisting") or "",
        }

    def _slug_to_dtx_metadata(self, slug: str) -> Dict[str, Any]:
        """Return pre-filled DiGA-level fields for *slug*.

        Only fields that exist in both the schema and the field map are
        returned, so renaming/adding/removing schema keys just works.
        """
        self._init_field_map()
        self._load_dtx_metadata()

        entry = self._slug_map.get(slug, {})
        if not entry:
            for stored_slug, stored_entry in (self._slug_map or {}).items():
                if slug.startswith(stored_slug) or stored_slug.startswith(slug):
                    entry = stored_entry
                    break

        prefilled: Dict[str, str] = {}
        for field in self.schema_fields:
            extractor = self._DTX_FIELD_MAP.get(field)
            if extractor and entry:
                prefilled[field] = str(extractor(entry))
        return prefilled

    # ------------------------------------------------------------------
    # Evidence file discovery
    # ------------------------------------------------------------------

    def discover_evidence_files(self, country: str = "Germany") -> List[Dict[str, str]]:
        """Walk verified evidence folders and return metadata for every raw file."""
        base = self.evidence_dir / country
        if not base.exists():
            logger.warning("Evidence directory not found: %s", base)
            return []

        files: List[Dict[str, str]] = []
        seen_slugs: set = set()

        for dtx_dir in sorted(base.iterdir()):
            if not dtx_dir.is_dir():
                continue
            verified = dtx_dir / "verified"
            if not verified.exists():
                continue

            dtx_slug = dtx_dir.name

            for evidence_type_dir in sorted(verified.iterdir()):
                if not evidence_type_dir.is_dir():
                    continue
                evidence_type = evidence_type_dir.name  # RCT or RWE

                for source_dir in sorted(evidence_type_dir.iterdir()):
                    if not source_dir.is_dir():
                        continue
                    source = source_dir.name  # pubmed, clinicaltrials, drks, isrctn, website

                    raw_dir = source_dir / "raw"
                    if not raw_dir.exists():
                        continue

                    for raw_file in sorted(raw_dir.iterdir()):
                        if not raw_file.is_file():
                            continue
                        if raw_file.suffix.lower() not in (".json", ".xml", ".html"):
                            continue

                        files.append({
                            "path": str(raw_file),
                            "dtx_slug": dtx_slug,
                            "evidence_type": evidence_type,
                            "source": source,
                            "file_type": raw_file.suffix.lower(),
                        })
                        seen_slugs.add(dtx_slug)

        if self.limit:
            limited_slugs = sorted(seen_slugs)[: self.limit]
            files = [f for f in files if f["dtx_slug"] in limited_slugs]

        logger.info(
            "Discovered %d raw evidence files across %d DTx apps",
            len(files),
            len({f["dtx_slug"] for f in files}),
        )
        return files

    # ------------------------------------------------------------------
    # Raw file reading
    # ------------------------------------------------------------------

    @staticmethod
    def _read_raw_file(path: str) -> str:
        """Read a raw evidence file and return its content as a string."""
        file_path = Path(path)
        suffix = file_path.suffix.lower()

        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()

        if suffix == ".json":
            try:
                data = json.loads(content)
                content = json.dumps(data, indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                pass

        if len(content) > MAX_RAW_CONTENT_CHARS:
            content = content[:MAX_RAW_CONTENT_CHARS] + "\n\n[... TRUNCATED — file too large ...]"

        return content

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(
        raw_content: str,
        source_type: str,
        evidence_type: str,
        file_type: str,
    ) -> str:
        """Build the human-message prompt that wraps the raw content."""
        source_labels = {
            "clinicaltrials": "ClinicalTrials.gov (JSON)",
            "drks": "German Clinical Trials Register / DRKS (JSON)",
            "isrctn": "ISRCTN Registry (JSON)",
            "pubmed": "PubMed article (XML)",
            "website": "Company / product website (HTML)",
        }
        source_label = source_labels.get(source_type, source_type)

        return (
            f"SOURCE: {source_label}\n"
            f"FILE TYPE: {file_type}\n"
            f"EVIDENCE TYPE (from folder): {evidence_type}\n\n"
            f"--- RAW DATA START ---\n"
            f"{raw_content}\n"
            f"--- RAW DATA END ---\n\n"
            f"Extract all available information from the raw data above into the JSON schema. "
            f"Return ONLY the JSON object."
        )

    # ------------------------------------------------------------------
    # LLM call + response parsing
    # ------------------------------------------------------------------

    async def _call_llm(self, prompt: str) -> Dict[str, str]:
        """Send prompt to LLM and parse the JSON response."""
        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=prompt),
        ]

        for attempt in range(2):
            try:
                response = await self.llm.ainvoke(messages)
                text = response.content
                if isinstance(text, list):
                    text = text[0].get("text", "") if text else ""
                text = text.strip()

                # Strip markdown code fences if present
                if text.startswith("```"):
                    text = re.sub(r"^```(?:json)?\s*", "", text)
                    text = re.sub(r"\s*```$", "", text)

                return json.loads(text)
            except (json.JSONDecodeError, Exception) as exc:
                if attempt == 0:
                    logger.warning("LLM returned invalid JSON, retrying: %s", exc)
                else:
                    logger.error("LLM failed after retry: %s", exc)
                    return {}

        return {}

    # ------------------------------------------------------------------
    # Single-file analysis
    # ------------------------------------------------------------------

    async def analyze_single(self, file_info: Dict[str, str]) -> Dict[str, str]:
        """Analyze one raw evidence file and return a merged result dict."""
        dtx_slug = file_info["dtx_slug"]
        evidence_type = file_info["evidence_type"]
        source = file_info["source"]
        file_type = file_info["file_type"]
        path = file_info["path"]

        prefilled = self._slug_to_dtx_metadata(dtx_slug)

        raw_content = self._read_raw_file(path)
        prompt = self._build_prompt(raw_content, source, evidence_type, file_type)

        llm_result = await self._call_llm(prompt)

        template = {field: "" for field in self.schema_fields}

        for key in template:
            if key in prefilled and prefilled[key]:
                template[key] = str(prefilled[key])

        for key, value in llm_result.items():
            if key in template and value:
                val = str(value).strip()
                if val and val.lower() not in ("n/a", "none", "null", "not available", "not found"):
                    template[key] = val

        if "evidence_type" in template and not template["evidence_type"]:
            template["evidence_type"] = evidence_type

        template["_source_file"] = path
        template["_source_type"] = source
        template["_analysis_timestamp"] = datetime.utcnow().isoformat() + "Z"

        return template

    # ------------------------------------------------------------------
    # Batch analysis
    # ------------------------------------------------------------------

    async def analyze_all(self, country: str = "Germany") -> List[Dict[str, str]]:
        """Discover and analyze all verified evidence files."""
        files = self.discover_evidence_files(country=country)
        if not files:
            logger.warning("No verified evidence files found for %s", country)
            return []

        total = len(files)
        dtx_count = len({f["dtx_slug"] for f in files})
        logger.info("Starting analysis of %d files across %d DTx apps", total, dtx_count)

        results: List[Dict[str, str]] = []

        for idx, file_info in enumerate(files, 1):
            slug = file_info["dtx_slug"]
            fname = Path(file_info["path"]).name
            print(
                f"  [{idx}/{total}] {slug} / {file_info['evidence_type']} / "
                f"{file_info['source']} / {fname}"
            )

            try:
                result = await self.analyze_single(file_info)
                results.append(result)
            except Exception as exc:
                logger.error("Failed to analyze %s: %s", file_info["path"], exc)
                print(f"    ERROR: {exc}")

        return results

    # ------------------------------------------------------------------
    # Saving results
    # ------------------------------------------------------------------

    def save_results(
        self,
        results: List[Dict[str, str]],
        output_path: str,
        country: str = "Germany",
        llm_source_name: str = "",
    ) -> None:
        """Write analysis results to a JSON file."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        name_field = self.schema_fields[0] if self.schema_fields else "_source_file"

        payload = {
            "metadata": {
                "country": country,
                "analysis_date": datetime.utcnow().isoformat() + "Z",
                "llm_provider": llm_source_name,
                "total_studies_analyzed": len(results),
                "dtx_apps_covered": len({r.get(name_field) or r.get("_source_file", "") for r in results}),
            },
            "results": results,
        }

        with open(out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

        logger.info("Results saved to %s", out)

    def export_csv(self, results: List[Dict[str, str]], csv_path: str) -> None:
        """Export results to a flat CSV table using schema fields as columns."""
        if not results:
            return

        out = Path(csv_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        with open(out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.schema_fields, extrasaction="ignore")
            writer.writeheader()
            for row in results:
                writer.writerow({k: row.get(k, "") for k in self.schema_fields})

        logger.info("CSV exported to %s", out)
