"""Phase 3: LLM-based evidence analysis.

Builds the benchmarking table (one row per verified RCT/RWE study, grouped per
DTx) defined in ``data-format/phase3_analysis.json``, whose columns mirror the
ground-truth datasets in ``Test_Datasets/test_dataset_benchmarking_numbers.csv``
and ``test_dataset_benchmarking_analysis.csv``.

For every verified study under
``evidence/{Country}/{slug}/verified/{RCT|RWE}/{source}/studies.json`` it:
  1. fills DTx-level columns from ``data/dtx_data*.json``,
  2. takes study-level columns from the already-scraped study object when present,
  3. asks the configured LLM to fill any remaining gaps strictly from the
     study's raw evidence file(s) (XML / JSON / HTML), returning "" when absent.

Every field carries provenance ("dtx" | "study" | "llm" | "empty") so results
are auditable. The Phase 3 LLM is configurable via PHASE3_PROVIDER/PHASE3_MODEL
(defaults to LLM_PROVIDER) and per-call cost/tokens/latency are logged through
``utils.llm_metrics``.

Output is JSON only (per-DTx files + a combined file + run metrics), namespaced
per model so different models can be benchmarked side by side without
overwriting each other. (xlsx/csv export is deferred for now.)
"""

import csv
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from slugify import slugify

from utils.data_manager import DataManager
from utils.llm_metrics import aggregate, invoke_with_metrics
from utils.llm_provider import LLMProvider

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path("data-format/phase3_analysis.json")
DEFAULT_OUTPUT_DIR = Path("Phase_3_Evidence_Analysis")
MAX_RAW_CONTENT_CHARS = 60_000
CHUNK_OVERLAP_CHARS = 2_000


def _csv_cell(value: Any) -> str:
    """Render a row value for the flat CSV export.

    Lists/tuples are joined with "; "; ``None`` becomes an empty string; all
    other values are stringified as-is.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v) for v in value)
    return str(value)

# Sources whose raw files are trial *registries* (protocol-only; usually no
# published results). Used to flag "registry-only" rows when no publication
# source contributed outcomes.
REGISTRY_SOURCES = {"drks", "clinicaltrials", "isrctn"}

# Narrative fields that benefit from a dedicated, focused extraction pass when
# the consolidated pass leaves them empty.
PER_FIELD_KEYS = ("key_outcomes_findings", "collected_data")

# JSON keys that carry administrative noise (addresses, contacts, ethics votes)
# rather than clinical content. Pruned before sending registry JSON to the LLM
# so the budget is spent on relevant fields.
NOISE_JSON_KEYS = {
    "trialcontacts",
    "trialcontact",
    "contacts",
    "contact",
    "materialsupports",
    "ethicscommittee",
    "centralcontacts",
    "overallofficials",
    "locations",
    "responsibleparty",
}


def _prune_json(obj: Any) -> Any:
    """Recursively drop administrative/noise keys from a parsed JSON object."""
    if isinstance(obj, dict):
        return {
            k: _prune_json(v)
            for k, v in obj.items()
            if k.lower() not in NOISE_JSON_KEYS
        }
    if isinstance(obj, list):
        return [_prune_json(v) for v in obj]
    return obj


def _pubmed_xml_to_text(xml_text: str) -> str:
    """Convert raw PubMed efetch XML into a compact, relevant text block.

    Mirrors the field selection of ``PubMedScraper._parse_pubmed_xml`` (title,
    labeled abstract, publication date, journal, publication types, MeSH terms,
    keywords) so large XML files (>100KB of markup) collapse to a few KB of the
    text that actually matters for extraction. Returns "" if nothing parseable.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""

    blocks: List[str] = []
    for article in root.findall(".//PubmedArticle"):
        medline = article.find(".//MedlineCitation")
        if medline is None:
            continue
        article_elem = medline.find(".//Article")
        if article_elem is None:
            continue

        pmid = medline.findtext("PMID", "")
        title = article_elem.findtext(".//ArticleTitle", "")

        abstract_parts: List[str] = []
        abstract_elem = article_elem.find(".//Abstract")
        if abstract_elem is not None:
            for text_elem in abstract_elem.findall(".//AbstractText"):
                label = text_elem.get("Label", "")
                text = "".join(text_elem.itertext()) or ""
                abstract_parts.append(f"{label}: {text}" if label else text)
        abstract = "\n".join(p for p in abstract_parts if p.strip())

        pub_date = article_elem.find(".//PubDate")
        if pub_date is not None:
            date = " ".join(
                v for v in (
                    pub_date.findtext("Year", ""),
                    pub_date.findtext("Month", ""),
                    pub_date.findtext("Day", ""),
                ) if v
            )
        else:
            date = ""

        journal = article_elem.findtext(".//Journal/Title", "")
        pub_types = [pt.text for pt in medline.findall(".//PublicationType") if pt.text]
        mesh = [m.text for m in medline.findall(".//MeshHeading/DescriptorName") if m.text]
        keywords = [k.text for k in medline.findall(".//Keyword") if k.text]

        lines = [f"PMID: {pmid}"]
        if title:
            lines.append(f"Title: {title}")
        if journal:
            lines.append(f"Journal: {journal}")
        if date:
            lines.append(f"Publication date: {date}")
        if pub_types:
            lines.append(f"Publication types: {', '.join(pub_types)}")
        if abstract:
            lines.append(f"Abstract:\n{abstract}")
        if mesh:
            lines.append(f"MeSH terms: {', '.join(mesh)}")
        if keywords:
            lines.append(f"Keywords: {', '.join(keywords)}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)

SYSTEM_PROMPT = """\
You are a clinical evidence data extraction and summarization specialist. You \
are given the raw content of a SINGLE clinical study (from ClinicalTrials.gov, \
DRKS, ISRCTN, PubMed, or a product website) and a list of fields to fill.

RULES:
1. Base every answer ONLY on the provided raw content. Do NOT use external or \
world knowledge, and do NOT perform web searches.
2. When a field is stated explicitly, extract it directly. When it is not \
labeled but can be derived or summarized from the content (e.g. key outcomes / \
findings, collected data), read the relevant text and write a concise, faithful \
summary grounded in that content.
3. Do NOT invent facts, numbers, statistics, or results that are not supported \
by the provided content.
4. If the content contains no relevant information for a field, return an empty \
string "" for it.
5. Return ONLY a valid JSON object whose keys are exactly the requested field \
keys (no markdown, no code fences, no commentary).
"""


def _load_schema() -> List[Dict[str, Any]]:
    """Load the ordered column definitions from the Phase 3 schema file."""
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"Schema file not found: {SCHEMA_PATH}. "
            "Create it at data-format/phase3_analysis.json."
        )
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    columns = data.get("columns")
    if not columns:
        raise ValueError(f"{SCHEMA_PATH} has no 'columns' list.")
    return columns


def _model_slug(model: str) -> str:
    """Filesystem-safe slug for a model name (used to namespace outputs)."""
    slug = (model or "unknown").strip().lower()
    slug = re.sub(r"[^a-z0-9.+_-]+", "-", slug)
    return slug.strip("-") or "unknown"


_EMPTY_TOKENS = {"", "n/a", "na", "none", "null", "not available", "not found", "-"}


def _is_empty(value: Any) -> bool:
    """True if a value should be treated as missing."""
    if value is None:
        return True
    return str(value).strip().lower() in _EMPTY_TOKENS


_MONTHS = {
    "jan": "January", "feb": "February", "mar": "March", "apr": "April",
    "may": "May", "jun": "June", "jul": "July", "aug": "August",
    "sep": "September", "oct": "October", "nov": "November", "dec": "December",
}
_MONTH_NUM = {name.lower(): i for i, name in enumerate(
    ["", "January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"]) if name}


def _month_name(token: str) -> str:
    """Resolve a month token (name/abbrev/number) to its full English name."""
    t = token.strip().lower()
    if t[:3] in _MONTHS:
        return _MONTHS[t[:3]]
    if t.isdigit():
        n = int(t)
        if 1 <= n <= 12:
            return list(_MONTH_NUM.keys())[n - 1].capitalize()
    return ""


def _format_date(raw: Any) -> str:
    """Normalize a date to 'DD Month YYYY' / 'Month YYYY' / 'YYYY'.

    Handles common scraped formats (e.g. '2026-Mar-11', '2026-03', '2026',
    'March 2018', '04 March 2018', ISO timestamps, 'January 1, 2024',
    '2024-01'). Returns the original string unchanged when no year is found so
    no information is lost.
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s or s.lower() in _EMPTY_TOKENS:
        return ""

    # Drop any time component, then tokenize on common separators.
    date_part = s.split("T")[0]
    tokens = [t for t in re.split(r"[\s,\-/.]+", date_part) if t]

    year = month = None
    day = None
    numeric: List[int] = []
    starts_with_year = bool(re.match(r"^\d{4}\b", date_part))

    for tok in tokens:
        if re.fullmatch(r"\d{4}", tok) and year is None:
            year = tok
        elif re.fullmatch(r"[A-Za-z]{3,}", tok):
            if month is None:
                month = _month_name(tok) or month
        elif re.fullmatch(r"\d{1,2}", tok):
            numeric.append(int(tok))

    if month is None and numeric:
        # No month name: numeric layout. ISO (year first) -> month, day;
        # otherwise treat a single value as the day, two as day, month.
        if starts_with_year:
            month = _month_name(str(numeric[0])) or None
            if len(numeric) > 1 and 1 <= numeric[1] <= 31:
                day = numeric[1]
        else:
            if 1 <= numeric[0] <= 31:
                day = numeric[0]
            if len(numeric) > 1:
                month = _month_name(str(numeric[1])) or None
    elif month is not None and numeric:
        # Month name present; the first 1-31 numeric token is the day.
        for n in numeric:
            if 1 <= n <= 31:
                day = n
                break

    if not year:
        return s  # unparseable -> keep original
    if month and day:
        return f"{day:02d} {month} {year}"
    if month:
        return f"{month} {year}"
    return year


class Phase3Analyzer:
    """Analyzes verified evidence into the Phase 3 benchmarking table."""

    def __init__(
        self,
        data_manager: DataManager,
        limit: Optional[int] = None,
        model_override: Optional[str] = None,
        llm_fill: bool = True,
        output_dir: Optional[str] = None,
    ):
        self.data_manager = data_manager
        self.limit = limit
        self.model_override = model_override
        self.llm_fill = llm_fill
        self.output_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
        self.evidence_dir = Path("evidence")

        self.columns = _load_schema()
        self.column_keys = [c["key"] for c in self.columns]
        self.column_labels = {c["key"]: c["label"] for c in self.columns}
        self.dtx_keys = [c["key"] for c in self.columns if c.get("fill") == "dtx"]

        self.provider = LLMProvider.get_phase3_provider()
        self.model = model_override or LLMProvider.get_phase3_model()
        self.source_name = LLMProvider.get_phase3_source_name(model_override)
        self._llm = None  # lazily built (only when a gap needs filling)

        self.metrics: List[Dict[str, Any]] = []
        self.audits: List[Dict[str, Any]] = []
        # One DTx slug->entry map per country (Germany / USA use different data
        # files), so a combined run never mixes the two.
        self._dtx_maps: Dict[str, Dict[str, Dict]] = {}
        self._category_cache: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # DTx metadata
    # ------------------------------------------------------------------

    def _load_dtx_map(self, country: str) -> Dict[str, Dict]:
        key = "usa" if country.strip().lower().startswith("us") else "germany"
        if key in self._dtx_maps:
            return self._dtx_maps[key]
        if key == "usa":
            data = self.data_manager.load_usa_dtx_data()
        else:
            data = self.data_manager.load_dtx_data()
        dtx_map: Dict[str, Dict] = {}
        for entry in data.get("dtx_list", []):
            slug = self._name_to_slug(entry.get("dtx_name", ""))
            if slug:
                dtx_map[slug] = entry
        self._dtx_maps[key] = dtx_map
        return dtx_map

    @staticmethod
    def _name_to_slug(name: str) -> str:
        # Mirror exactly how evidence folders are named
        # (base_evidence_scraper._sanitize_dtx_name) so slugs always match.
        return slugify(name or "", max_length=50, lowercase=True)

    def _lookup_entry(self, slug: str, country: str) -> Dict[str, Any]:
        dtx_map = self._load_dtx_map(country)
        entry = dtx_map.get(slug)
        if entry:
            return entry
        for stored_slug, stored_entry in dtx_map.items():
            if slug.startswith(stored_slug) or stored_slug.startswith(slug):
                return stored_entry
        return {}

    @staticmethod
    def _company(entry: dict) -> str:
        raw = entry.get("company_provider", "")
        if raw:
            lines = [ln.strip() for ln in str(raw).strip().split("\n") if ln.strip()]
            return lines[-1] if lines else str(raw)
        return ""

    @staticmethod
    def _icd10(entry: dict) -> str:
        v = entry.get("clinical_area_icd10", "")
        return ", ".join(v) if isinstance(v, list) else str(v or "")

    @staticmethod
    def _store(entry: dict, store_key: str, field: str) -> str:
        store = entry.get(store_key) or {}
        if isinstance(store, dict):
            val = store.get(field)
            return str(val) if val not in (None, "") else ""
        return ""

    @staticmethod
    def _diga_listing_status(entry: dict) -> str:
        """DiGA listing status for the analysis table.

        - non-DiGA / not in the scraped German list -> "not a DiGA"
        - removed DiGA -> "Removed (DD.MM.YYYY)" (date parsed from the delisting
          text), or "Removed" when no date is present
        - otherwise the listing status (e.g. "Permanently listed").
        """
        status = str(entry.get("diga_listing") or "").strip()
        if not status:
            return "not a DiGA"
        if status.lower() == "removed":
            text = " ".join(
                str(entry.get(k) or "")
                for k in ("removed_from_diga_listing", "reason_for_delisting")
            )
            match = re.search(r"\d{2}\.\d{2}\.\d{4}", text)
            return f"Removed ({match.group(0)})" if match else "Removed"
        return status

    @staticmethod
    def _is_diga(entry: dict, country: str) -> str:
        explicit = entry.get("is_it_a_diga")
        if explicit:
            return str(explicit)
        dl = str(entry.get("diga_listing") or "").strip()
        low = dl.lower()
        if dl and not any(tok in low for tok in ("no diga", "not in", "n/a", "na", "none")):
            return "YES"
        return "YES" if country.strip().lower().startswith("ger") else "NO"

    def _dtx_fields(self, entry: dict, country: str) -> Dict[str, str]:
        """Extract the 14 DTx-level columns from a dtx_data entry."""
        if not entry:
            return {}
        getters = {
            "dtx_name": lambda: entry.get("dtx_name", ""),
            "is_it_a_diga": lambda: self._is_diga(entry, country),
            "company_provider": lambda: self._company(entry),
            "company_founding_year": lambda: entry.get("company_founding_year", ""),
            "diga_listing_status": lambda: self._diga_listing_status(entry),
            "diga_listing_date": lambda: entry.get("date_of_first_listing") or "",
            "category": lambda: entry.get("category") or entry.get("dtx_category") or "",
            "risk_class": lambda: entry.get("risk_class", ""),
            "clinical_area_icd10": lambda: self._icd10(entry),
            "rating_on_playstore": lambda: self._store(entry, "reviews_playstore", "rating"),
            "reviews_on_playstore": lambda: self._store(entry, "reviews_playstore", "review_count"),
            "rating_on_appstore": lambda: self._store(entry, "reviews_appstore", "rating"),
            "reviews_on_appstore": lambda: self._store(entry, "reviews_appstore", "review_count"),
        }
        out: Dict[str, str] = {}
        for key in self.dtx_keys:
            getter = getters.get(key)
            if getter:
                val = getter()
                out[key] = str(val) if val not in (None, "") else ""
        return out

    # ------------------------------------------------------------------
    # Evidence discovery
    # ------------------------------------------------------------------

    # Directory names under evidence/ that are not countries.
    NON_COUNTRY_DIRS = {"summary"}

    def _available_countries(self) -> List[str]:
        """Country sub-directories present under evidence/ (e.g. Germany, USA)."""
        if not self.evidence_dir.is_dir():
            return []
        return [
            d.name for d in sorted(self.evidence_dir.iterdir())
            if d.is_dir() and d.name.lower() not in self.NON_COUNTRY_DIRS
        ]

    def discover_studies(
        self, countries: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Walk verified evidence across the requested countries.

        ``countries`` is a list of evidence/ sub-directory names; when omitted,
        every country directory present is processed. Each returned record is
        tagged with its ``country`` so DTx-level columns use the right data file.
        """
        if not countries:
            countries = self._available_countries()

        records: List[Dict[str, Any]] = []
        seen_slugs: List[str] = []  # (country, slug) order preserved for --limit

        for country in countries:
            base = self.evidence_dir / country
            if not base.exists():
                logger.warning("Evidence directory not found: %s", base)
                continue

            for dtx_dir in sorted(base.iterdir()):
                if not dtx_dir.is_dir():
                    continue
                verified = dtx_dir / "verified"
                if not verified.is_dir():
                    continue
                slug = dtx_dir.name
                slug_has_study = False

                for type_dir in sorted(verified.iterdir()):
                    if not type_dir.is_dir():
                        continue
                    evidence_type = type_dir.name  # RCT or RWE
                    for source_dir in sorted(type_dir.iterdir()):
                        if not source_dir.is_dir():
                            continue
                        source = source_dir.name
                        studies_file = source_dir / "studies.json"
                        if not studies_file.exists():
                            continue
                        try:
                            payload = json.loads(studies_file.read_text(encoding="utf-8"))
                        except Exception as exc:
                            logger.warning("Could not read %s: %s", studies_file, exc)
                            continue
                        for study in payload.get("studies", []):
                            records.append({
                                "country": country,
                                "dtx_slug": slug,
                                "evidence_type": evidence_type,
                                "source": source,
                                "source_dir": source_dir,
                                "study": study,
                            })
                            slug_has_study = True
                if slug_has_study:
                    seen_slugs.append((country, slug))

        if self.limit:
            allowed = set(seen_slugs[: self.limit])
            records = [r for r in records if (r["country"], r["dtx_slug"]) in allowed]

        logger.info(
            "Discovered %d verified studies across %d DTx apps in %s",
            len(records),
            len({(r["country"], r["dtx_slug"]) for r in records}),
            ", ".join(countries) or "(none)",
        )
        return records

    # ------------------------------------------------------------------
    # Raw file handling
    # ------------------------------------------------------------------

    @staticmethod
    def _study_id(study: Dict[str, Any]) -> str:
        for key in ("study_id", "pmid", "drks_id", "nct_id", "isrctn_id", "id"):
            val = study.get(key)
            if val:
                return str(val)
        return ""

    def _find_raw_files(self, record: Dict[str, Any]) -> List[Path]:
        """Locate the raw file(s) for a study (verified dir first, then candidates)."""
        source_dir: Path = record["source_dir"]
        source = record["source"]
        study_id = self._study_id(record["study"])

        dtx_dir = source_dir.parent.parent.parent  # verified/{type}/{source} -> dtx dir
        search_dirs = [
            source_dir / "raw",
            dtx_dir / "candidates" / source / "raw",
        ]
        allowed_suffixes = (".xml", ".json", ".html", ".txt")

        for raw_dir in search_dirs:
            if not raw_dir.is_dir():
                continue
            files = [
                f for f in sorted(raw_dir.iterdir())
                if f.is_file() and f.suffix.lower() in allowed_suffixes
            ]
            if study_id:
                matched = [f for f in files if f.stem.startswith(study_id)]
                if matched:
                    return matched
            # No id (or no id match) but exactly one file -> use it.
            if not study_id and len(files) == 1:
                return files
        return []

    @staticmethod
    def _normalize_raw(path: Path) -> str:
        """Read a raw file and reduce it to relevant text for the LLM.

        - PubMed ``.xml``  -> compact text (title/abstract/dates/journal/MeSH).
        - registry ``.json`` -> pretty-printed JSON with noise keys pruned.
        - website ``.txt``/``.html`` -> as-is.
        Falls back to the original text whenever parsing fails.
        """
        content = path.read_text(encoding="utf-8", errors="replace")
        suffix = path.suffix.lower()
        if suffix == ".xml":
            parsed = _pubmed_xml_to_text(content)
            return parsed or content
        if suffix == ".json":
            try:
                data = _prune_json(json.loads(content))
                return json.dumps(data, indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                return content
        return content

    def _own_context(self, record: Dict[str, Any]) -> str:
        """Build the normalized context from the record's OWN raw file(s) only.

        Each verified evidence is analyzed strictly from its own source raw file
        (DRKS / PubMed / ISRCTN / ClinicalTrials / website); no sibling sources.
        """
        files = self._find_raw_files(record)
        if not files:
            return ""
        parts = [
            f"--- FILE: {f.name} ---\n{self._normalize_raw(f)}" for f in files
        ]
        return "\n\n".join(parts).strip()

    @staticmethod
    def _chunk_text(text: str) -> List[str]:
        """Split text into <=budget windows with small overlap (map-reduce)."""
        if len(text) <= MAX_RAW_CONTENT_CHARS:
            return [text]
        step = MAX_RAW_CONTENT_CHARS - CHUNK_OVERLAP_CHARS
        return [
            text[i: i + MAX_RAW_CONTENT_CHARS]
            for i in range(0, len(text), step)
        ]

    # ------------------------------------------------------------------
    # Row building
    # ------------------------------------------------------------------

    @staticmethod
    def _study_value(study: Dict[str, Any], col: Dict[str, Any]) -> str:
        for key in col.get("study_keys", []):
            if key in study and not _is_empty(study.get(key)):
                return str(study.get(key)).strip()
        return ""

    def _evidence_type_value(self, record: Dict[str, Any]) -> str:
        study = record["study"]
        for key in ("evidence_type",):
            if not _is_empty(study.get(key)):
                return str(study.get(key)).strip()
        classification = study.get("_classification") or {}
        # Keep the short folder code (RCT / RWE) to match the test dataset style.
        return record["evidence_type"]

    def _ensure_llm(self):
        if self._llm is None:
            llm, provider, model = LLMProvider.get_phase3_llm(model_override=self.model_override)
            self._llm = llm
            self.provider = provider
            self.model = model
        return self._llm

    async def _llm_extract(
        self,
        record: Dict[str, Any],
        source_label: str,
        context_text: str,
        cols: List[Dict[str, Any]],
        focus: Optional[str] = None,
    ) -> Dict[str, str]:
        """Single LLM call: extract ``cols`` from ``context_text``.

        ``focus`` marks a dedicated single-field pass (used for the hard
        narrative fields) so the prompt can stress reading the whole text.
        """
        study = record["study"]
        fields_block = "\n".join(
            f'- "{c["key"]}": {c.get("description", c["label"])}' for c in cols
        )
        known = {
            "title": study.get("title", ""),
            "source": record["source"],
            "evidence_type": record["evidence_type"],
        }
        if focus == "outcomes_data":
            instruction = (
                'Carefully read the ENTIRE raw data below, then fill the requested '
                'field(s) while strictly distinguishing two different things:\n'
                '- "collected_data": the instruments, measures, scales, questionnaires '
                'and variables that are COLLECTED/tracked (what is measured), e.g. '
                'PHQ-9, IBS-SSS, NPRS pain, adherence logs. Primary Outcome and '
                'Secondary Outcome MEASURE definitions (what will be measured and when) '
                'belong HERE.\n'
                '- "key_outcomes_findings": ONLY actual REPORTED results/findings - '
                'effect sizes, percentage changes, p-values, statistical significance, '
                'responder rates, conclusions. If the source only DEFINES what will be '
                'measured and reports NO results, return "" for this field.\n'
                'First evaluate, for each piece of outcome text, whether it is a '
                'reported result/finding or merely a measure/what-was-collected, then '
                'route it to the correct field. Do not invent results.'
            )
        elif focus:
            instruction = (
                f'Carefully read the ENTIRE raw data below, then fill ONLY the '
                f'field "{focus}". If it is stated explicitly, extract it; if not, '
                f'write a concise, faithful summary derived from the content. Look '
                f'across every section before deciding it is absent (return "").'
            )
        else:
            instruction = "Fill the requested fields from the raw data below."
        prompt = (
            f"STUDY CONTEXT (for identification only):\n{json.dumps(known, ensure_ascii=False)}\n\n"
            f"SOURCE: {source_label}\n\n"
            f"{instruction}\n\n"
            f"FIELDS TO FILL (return a JSON object with EXACTLY these keys):\n{fields_block}\n\n"
            f"--- RAW DATA START ---\n{context_text}\n--- RAW DATA END ---\n\n"
            f"Return ONLY the JSON object. Use \"\" for any field not present in the raw data."
        )

        llm = self._ensure_llm()
        messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
        try:
            response, metric = await invoke_with_metrics(
                llm,
                messages,
                provider=self.provider,
                model=self.model,
                call_label="phase3_perfield" if focus else "phase3_analysis",
                web_search=False,
                extra={
                    "dtx_slug": record["dtx_slug"],
                    "source": source_label,
                    "evidence_type": record["evidence_type"],
                    "study_id": self._study_id(study),
                    "focus": focus or "",
                },
            )
            self.metrics.append(metric)
        except Exception as exc:
            logger.warning(
                "LLM extract failed for %s (%s): %s",
                record["dtx_slug"], source_label, exc,
            )
            return {}

        return self._parse_json(response)

    async def _llm_category(
        self, slug: str, entry: Dict[str, Any], country: str
    ) -> str:
        """Derive a short (1-3 word) DTx category from its scraped metadata.

        Cached per (country, slug) so it costs one LLM call per app, reused
        across all of that app's study rows. Grounded only in the DTx name,
        description and clinical area (ICD-10); returns "" on failure.
        """
        cache_key = f"{country}:{slug}"
        if cache_key in self._category_cache:
            return self._category_cache[cache_key]

        name = entry.get("dtx_name", "")
        description = entry.get("description", "") or ""
        icd10 = self._icd10(entry)
        context = json.dumps(
            {"dtx_name": name, "description": description, "clinical_area_icd10": icd10},
            ensure_ascii=False,
        )
        prompt = (
            "From the digital therapeutic (DTx) metadata below, give the medical / "
            "therapeutic CATEGORY of the app as a short label of 1-3 words "
            "(e.g. \"Psyche\", \"Digestion\", \"Nervous system\", "
            "\"Muscles, bones and joints\", \"Mental Health\"). Base it on the "
            "indication, description and clinical area only. Do NOT invent details.\n\n"
            f"DTX METADATA:\n{context}\n\n"
            'Return ONLY a JSON object: {"category": "<short label>"}.'
        )
        llm = self._ensure_llm()
        messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
        category = ""
        try:
            response, metric = await invoke_with_metrics(
                llm,
                messages,
                provider=self.provider,
                model=self.model,
                call_label="phase3_category",
                web_search=False,
                extra={"dtx_slug": slug, "country": country},
            )
            self.metrics.append(metric)
            category = str(self._parse_json(response).get("category", "")).strip()
        except Exception as exc:
            logger.warning("LLM category failed for %s: %s", slug, exc)
        self._category_cache[cache_key] = category
        return category

    async def _extract_over_chunks(
        self,
        record: Dict[str, Any],
        source_label: str,
        text: str,
        cols: List[Dict[str, Any]],
        focus: Optional[str] = None,
    ) -> Dict[str, str]:
        """Map-reduce extraction: run over chunks, merge first non-empty per field."""
        merged: Dict[str, str] = {}
        for chunk in self._chunk_text(text):
            remaining = [c for c in cols if _is_empty(merged.get(c["key"]))]
            if not remaining:
                break
            parsed = await self._llm_extract(
                record, source_label, chunk, remaining, focus=focus
            )
            for col in remaining:
                val = parsed.get(col["key"], "")
                if not _is_empty(val):
                    merged[col["key"]] = str(val).strip()
        return merged

    @staticmethod
    def _parse_json(response: Any) -> Dict[str, str]:
        text = getattr(response, "content", response)
        if isinstance(text, list):
            text = text[0].get("text", "") if text else ""
        text = str(text).strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            logger.warning("Could not parse LLM JSON response.")
            return {}

    async def analyze_record(
        self, record: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Build one table row for a single verified study, from its OWN raw file.

        Returns ``(row, audit)`` where ``row`` is the pure 28-key benchmarking
        row and ``audit`` holds provenance/notes (kept out of the row so the
        output structure matches the ground-truth JSON).
        """
        study = record["study"]
        country = record["country"]
        entry = self._lookup_entry(record["dtx_slug"], country)
        dtx_fields = self._dtx_fields(entry, country)

        row: Dict[str, str] = {k: "" for k in self.column_keys}
        provenance: Dict[str, str] = {k: "empty" for k in self.column_keys}

        # No DTx entry in the data file for this evidence folder slug: the DTx
        # columns stay empty. Surface it so name drift is visible.
        no_dtx_match = not entry
        if no_dtx_match:
            logger.warning(
                "No DTx entry for evidence slug '%s' (%s); DTx columns left empty.",
                record["dtx_slug"], country,
            )

        # DTx-level columns
        for key in self.dtx_keys:
            val = dtx_fields.get(key, "")
            if not _is_empty(val):
                row[key] = str(val)
                provenance[key] = "dtx"

        # Category: LLM-derive from the DTx metadata when the dataset field is empty.
        if self.llm_fill and entry and _is_empty(row.get("category")):
            category = await self._llm_category(record["dtx_slug"], entry, country)
            if not _is_empty(category):
                row["category"] = category
                provenance["category"] = "llm"

        # Evidence type
        row["evidence_type"] = self._evidence_type_value(record)
        provenance["evidence_type"] = "study"

        # Study-level columns from the scraped study object
        gap_cols: List[Dict[str, Any]] = []
        for col in self.columns:
            key = col["key"]
            fill = col.get("fill")
            if fill == "dtx" or key == "evidence_type":
                continue
            val = self._study_value(study, col)
            if not _is_empty(val):
                row[key] = val
                provenance[key] = "study"
            elif fill == "study_or_llm":
                gap_cols.append(col)

        # LLM gap fill from THIS source's own raw file only (normalized, chunked)
        source = record["source"]
        note = ""
        if self.llm_fill and gap_cols:
            context = self._own_context(record)
            if not context:
                note = "no_raw_file"
            else:
                chunked = len(context) > MAX_RAW_CONTENT_CHARS
                # Consolidated pass over the own file for all gap columns.
                filled = await self._extract_over_chunks(
                    record, source, context, gap_cols
                )
                for col in gap_cols:
                    key = col["key"]
                    val = filled.get(key, "")
                    if not _is_empty(val):
                        row[key] = str(val).strip()
                        provenance[key] = "llm"

                # Combined focused pass for the two hard narrative fields: one call
                # that evaluates each piece of outcome text and routes measures to
                # collected_data and only reported results to key_outcomes_findings.
                gap_by_key = {c["key"]: c for c in gap_cols}
                narrative_cols = [
                    gap_by_key[k] for k in PER_FIELD_KEYS
                    if k in gap_by_key and _is_empty(row.get(k))
                ]
                if narrative_cols:
                    refilled = await self._extract_over_chunks(
                        record, source, context, narrative_cols, focus="outcomes_data"
                    )
                    for col in narrative_cols:
                        key = col["key"]
                        val = refilled.get(key, "")
                        if not _is_empty(val):
                            row[key] = str(val).strip()
                            provenance[key] = "llm"

                # Empty key findings for a registry protocol is the expected case:
                # the outcome measures are captured under collected_data instead.
                if (
                    _is_empty(row.get("key_outcomes_findings"))
                    and source in REGISTRY_SOURCES
                ):
                    note = (
                        "registry-only: outcome measures captured under collected_data; "
                        "no reported results"
                    )

                if chunked:
                    note = (note + "; " if note else "") + "chunked"

        # Normalize date columns to 'DD Month YYYY' / 'Month YYYY' / 'YYYY'
        # regardless of whether they came from the study object or the LLM.
        for date_key in ("trial_start_date", "trial_end_date", "publication_date"):
            if not _is_empty(row.get(date_key)):
                row[date_key] = _format_date(row[date_key])

        if no_dtx_match:
            note = (note + "; " if note else "") + "no_dtx_match"

        audit = {
            "country": country,
            "dtx_slug": record["dtx_slug"],
            "dtx_name": row.get("dtx_name", ""),
            "source": source,
            "evidence_type_folder": record["evidence_type"],
            "study_id": self._study_id(study),
            "study_title": study.get("title", ""),
            "provenance": provenance,
            "note": note,
        }
        return row, audit

    async def analyze_all(
        self, countries: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        records = self.discover_studies(countries)
        if not records:
            return []
        total = len(records)
        rows: List[Dict[str, Any]] = []
        self.audits = []
        for idx, record in enumerate(records, 1):
            sid = self._study_id(record["study"]) or "?"
            print(
                f"  [{idx}/{total}] {record['country']} / {record['dtx_slug']} / "
                f"{record['evidence_type']} / {record['source']} / {sid}"
            )
            try:
                row, audit = await self.analyze_record(record)
                rows.append(row)
                self.audits.append(audit)
            except Exception as exc:
                logger.error("Failed to analyze %s: %s", record["dtx_slug"], exc)
                print(f"    ERROR: {exc}")
        return rows

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _run_dir(self) -> Path:
        # One folder per model; all countries are combined into this run.
        return self.output_dir / _model_slug(self.model)

    def save_outputs(self, rows: List[Dict[str, Any]]) -> Path:
        """Write all Phase 3 JSON artifacts for a run and return the run directory.

        All countries are written into a single per-model folder.
        ``phase3_combined.json`` mirrors the ground-truth CSV-to-JSON shape
        (``metadata`` + pure 28-key ``rows`` + ``by_dtx``) so the two can be
        benchmarked directly; provenance/notes live in a separate ``audit`` list
        aligned with ``rows`` by index.
        """
        run_dir = self._run_dir()
        by_dtx_dir = run_dir / "by_dtx"
        by_dtx_dir.mkdir(parents=True, exist_ok=True)

        audits = self.audits if len(self.audits) == len(rows) else [{} for _ in rows]

        # Group rows by DTx name (matching the ground-truth converter grouping),
        # while keeping the slug (from the aligned audit) for per-DTx filenames.
        # Per-DTx files are keyed by country+slug to avoid cross-country clashes.
        by_dtx: List[Dict[str, Any]] = []
        slug_groups: Dict[str, Dict[str, Any]] = {}
        countries_seen: List[str] = []
        for row, audit in zip(rows, audits):
            name = row.get("dtx_name", "") or audit.get("dtx_slug", "")
            slug = audit.get("dtx_slug", name)
            country = audit.get("country", "")
            if country and country not in countries_seen:
                countries_seen.append(country)
            if by_dtx and by_dtx[-1]["dtx_name"] == name:
                by_dtx[-1]["studies"].append(row)
            else:
                by_dtx.append({"dtx_name": name, "studies": [row]})
            group_key = f"{country}/{slug}" if country else slug
            grp = slug_groups.setdefault(
                group_key,
                {"dtx_slug": slug, "dtx_name": name, "country": country,
                 "studies": [], "audit": []},
            )
            grp["studies"].append(row)
            grp["audit"].append(audit)

        totals = aggregate(self.metrics) if self.metrics else aggregate([])
        run_meta = {
            "country": sorted(countries_seen) if countries_seen else "All",
            "analysis_date": datetime.utcnow().isoformat() + "Z",
            "provider": self.provider,
            "model": self.model,
            "llm_source": self.source_name,
            "llm_fill_enabled": self.llm_fill,
            "total_rows": len(rows),
            "dtx_apps": len(slug_groups),
            "llm_calls": len(self.metrics),
            "metrics_totals": totals,
            "columns": list(self.column_keys),
        }

        for group_key, grp in slug_groups.items():
            fname = group_key.replace("/", "_") + ".json"
            with open(by_dtx_dir / fname, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "dtx_slug": grp["dtx_slug"],
                        "dtx_name": grp["dtx_name"],
                        "country": grp["country"],
                        "model": self.model,
                        "studies": grp["studies"],
                        "audit": grp["audit"],
                    },
                    fh,
                    indent=2,
                    ensure_ascii=False,
                )

        with open(run_dir / "phase3_combined.json", "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "metadata": run_meta,
                    "rows": rows,
                    "by_dtx": by_dtx,
                    "audit": audits,
                },
                fh,
                indent=2,
                ensure_ascii=False,
            )

        with open(run_dir / "run_metrics.json", "w", encoding="utf-8") as fh:
            json.dump(run_meta, fh, indent=2, ensure_ascii=False)

        # Flat CSV mirror of phase3_combined.json's rows, stored next to it so
        # every output instance has both formats. List-valued cells are joined
        # with "; " and missing keys are written as empty strings.
        columns = list(self.column_keys)
        with open(run_dir / "phase3_combined.csv", "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
            writer.writerow(columns)
            for row in rows:
                writer.writerow([_csv_cell(row.get(col, "")) for col in columns])

        logger.info("Phase 3 outputs written to %s", run_dir)
        return run_dir
