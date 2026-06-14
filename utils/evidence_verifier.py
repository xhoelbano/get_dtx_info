"""LLM-based evidence relevance verifier + RCT/RWE classifier (Layer 2).

This module provides:
- EvidenceVerifier: decides whether a candidate study is specifically about a
  given Digital Therapeutic (DTx) product.
- EvidenceClassifierV2: classifies a verified-relevant study as RCT, RWE, or
  INELIGIBLE (anything that is not a primary RCT/RWE study).

Two-layer classification system:
- Layer 1: Collect all search results as candidates (no filtering)
- Layer 2: LLM verifies each candidate against DTx metadata, then classifies it

Prompt output structures are schema-driven (loaded from data-format/*.json), so
the JSON shape lives in one place and is not hardcoded inside the prompt text.
All LLM calls are wrapped with utils.llm_metrics.invoke_with_metrics so token,
cost, and latency are recorded like the Phase 1 pipeline.
"""
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from langchain_core.messages import HumanMessage, SystemMessage

from .company_name import normalize_company_name
from .llm_provider import LLMProvider
from .llm_metrics import invoke_with_metrics

VERIFICATION_SCHEMA_PATH = Path("data-format/evidence_verification.json")
CLASSIFICATION_SCHEMA_PATH = Path("data-format/evidence_classification.json")

# How much raw full-text/page content to feed the LLM. Product names often
# appear in methods/registration records rather than the abstract, so we pass a
# generous slice to reduce false "not mentioned" rejections.
RAW_CONTENT_CHARS = 9000


def _load_schema(path: Path) -> Dict:
    """Load a schema JSON file (single source of truth for prompt output shape)."""
    if not path.exists():
        raise FileNotFoundError(
            f"Schema file not found: {path}. Create it under data-format/."
        )
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _response_text(response: Any) -> str:
    """Flatten a LangChain response to text (handles str or content-block list)."""
    content = getattr(response, "content", response)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text_val = block.get("text")
                if isinstance(text_val, str) and text_val:
                    parts.append(text_val)
        return "".join(parts).strip()
    return (content or "").strip()


class EvidenceVerifier:
    """LLM-based verification of evidence relevance to a specific DTx product."""

    SYSTEM_PROMPT_TEMPLATE = """\
You are a clinical evidence analyst specializing in Digital Therapeutics (DTx).

You decide whether a single clinical study or publication is specifically about ONE \
given digital therapeutic product. A digital therapeutic is a specific software \
(sometimes paired with a companion device) that delivers an evidence-based therapeutic \
intervention.

Mark the study as RELEVANT only when the evidence shows it concerns THIS specific product:
- The product name, an abbreviation, a former or rebranded name, or a clear spelling/locale \
variation appears in the title, abstract, full text, or trial registration record.
- The intervention is unambiguously this product (for example, the manufacturer is named as \
the developer of the studied intervention, or the software/device is described in a way that \
uniquely identifies it).

Mark the study as NOT RELEVANT when:
- It addresses the same medical condition but a different treatment or product.
- It concerns the same company but a different product.
- It is generic research about "digital therapeutics", "mobile health", or "apps" with no \
specific identification of this product.

Important guidance:
- Read the FULL TEXT / raw content and any trial-registration details provided, not only the \
abstract, before concluding the product is not mentioned. Product names often appear in the \
methods, intervention description, acknowledgements, or registration record.
- Judge specificity rather than strictness for its own sake: include genuine matches even when \
phrased differently, and exclude only when there is no specific link to this product.

Return ONLY a valid JSON object (no markdown, no code fences, no commentary) with exactly \
these keys:

{schema_json}\
"""

    VERIFICATION_PROMPT_TEMPLATE = """\
Assess whether the study below is specifically about this digital therapeutic product.

=== DIGITAL THERAPEUTIC ===
Product name: {dtx_name}
Known name variations: {name_variants}
Manufacturer / provider: {company}
Clinical area (ICD-10): {icd_codes}
Description: {description}

=== STUDY ===
Title: {study_title}
Abstract / summary: {study_abstract}
Sponsor / funder: {study_sponsor}
Intervention: {study_intervention}
Source: {study_source}
{raw_page_section}
Decide using all of the information above, including the full text / raw content when present. \
Return the JSON object only."""

    def __init__(self, metrics_sink: Optional[List[Dict]] = None):
        """Initialize the verifier.

        Args:
            metrics_sink: Optional shared list to collect per-call metric rows.
        """
        # Web search must be OFF here: verification judges the provided text only.
        self.llm = LLMProvider.get_llm(
            temperature=0.0, max_tokens=600, enable_web_search=False
        )
        self.provider = LLMProvider.get_active_provider()
        self.model = LLMProvider.get_active_model()

        self.schema = _load_schema(VERIFICATION_SCHEMA_PATH)
        schema_json = json.dumps(self.schema, indent=2, ensure_ascii=False)
        self.system_prompt = self.SYSTEM_PROMPT_TEMPLATE.replace(
            "{schema_json}", schema_json
        )

        self.metrics: List[Dict] = metrics_sink if metrics_sink is not None else []

    async def verify_study(
        self,
        study_data: Dict,
        dtx_data: Dict,
        raw_content: Optional[str] = None
    ) -> Dict:
        """Verify if a study is relevant to a specific DTx.

        Args:
            study_data: Parsed study metadata from candidates/studies.json
            dtx_data: DTx metadata from dtx_data.json
            raw_content: Optional raw file content (XML, JSON, or HTML string)

        Returns:
            Verification result dict: is_relevant, confidence, reason, matched_elements.
        """
        dtx_name = dtx_data.get("dtx_name", "Unknown")
        company = normalize_company_name(
            dtx_data.get("company_provider", "")
        ) or "Unknown"
        icd_codes = dtx_data.get("clinical_area_icd10", [])
        description = (dtx_data.get("description") or "")[:500]
        name_variants = self._build_name_variants(dtx_name, company)

        study_title = study_data.get("title", "")
        study_abstract = self._extract_abstract(study_data, raw_content)
        study_sponsor = self._extract_sponsor(study_data)
        study_intervention = self._extract_intervention(study_data)
        study_source = study_data.get("source", "Unknown")

        raw_page_section = ""
        if raw_content:
            extracted_text = self._extract_text_from_html(raw_content) if (
                "<html" in raw_content[:2000].lower() or "<body" in raw_content[:2000].lower()
            ) else raw_content[:RAW_CONTENT_CHARS]
            if extracted_text and extracted_text.strip():
                raw_page_section = (
                    "\n=== FULL TEXT / RAW CONTENT (from publication or source page) ===\n"
                    + extracted_text[:RAW_CONTENT_CHARS]
                    + "\n"
                )

        prompt = self.VERIFICATION_PROMPT_TEMPLATE.format(
            dtx_name=dtx_name,
            name_variants=", ".join(name_variants) if name_variants else "None",
            company=company,
            icd_codes=", ".join(icd_codes[:5]) if icd_codes else "Not specified",
            description=description,
            study_title=study_title,
            study_abstract=study_abstract[:2500] if study_abstract else "Not available",
            study_sponsor=study_sponsor,
            study_intervention=study_intervention[:500] if study_intervention else "Not specified",
            study_source=study_source,
            raw_page_section=raw_page_section,
        )

        try:
            messages = [
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=prompt)
            ]

            response, metrics = await invoke_with_metrics(
                self.llm,
                messages,
                provider=self.provider,
                model=self.model,
                call_label="phase2_verify",
                web_search=False,
                extra={
                    "dtx_name": dtx_name,
                    "study_id": self._study_id(study_data),
                    "source": study_source,
                },
            )
            self.metrics.append(metrics)

            result = self._parse_json_response(_response_text(response))
            if result:
                return result

        except Exception as e:
            print(f"    LLM verification error: {e}")

        # Fallback: deterministic keyword matching when the LLM call/parse fails.
        return self._fallback_verification(study_data, dtx_data, raw_content)

    @staticmethod
    def _study_id(study_data: Dict) -> str:
        """Best-effort study identifier for logging/raw-file lookup."""
        return str(
            study_data.get("study_id")
            or study_data.get("pmid")
            or study_data.get("nct_id")
            or study_data.get("drks_id")
            or study_data.get("isrctn_id")
            or ""
        )

    def _build_name_variants(self, dtx_name: str, company: str) -> List[str]:
        """Produce plausible name variations to help the LLM match the product.

        Reduces false rejections caused by trademark symbols, German condition
        suffixes, hyphen/space differences, or brand-only mentions.
        """
        if not dtx_name:
            return []

        variants = set()
        raw = dtx_name.strip()
        variants.add(raw)
        variants.add(re.sub(r"[®™]", "", raw).strip())

        core = self._clean_dtx_name(dtx_name)
        if core:
            variants.add(core)
            tokens = core.split()
            if tokens and len(tokens[0]) >= 4:
                variants.add(tokens[0])

        for v in list(variants):
            if "-" in v:
                variants.add(v.replace("-", " "))
            if " " in v:
                variants.add(v.replace(" ", ""))

        cleaned = [v.strip() for v in variants if v and len(v.strip()) >= 2]
        seen, result = set(), []
        for v in sorted(cleaned, key=len, reverse=True):
            key = v.lower()
            if key not in seen and key != (dtx_name or "").strip().lower():
                seen.add(key)
                result.append(v)
        # Keep the canonical name first, then variations.
        return [dtx_name.strip()] + result

    def _extract_abstract(self, study_data: Dict, raw_content: Optional[str]) -> str:
        """Extract abstract/summary from study data or raw content."""
        for key in ("abstract", "brief_summary", "detailed_description"):
            value = study_data.get(key, "")
            if value:
                return value

        if raw_content:
            if "<Abstract>" in raw_content:
                match = re.search(r'<Abstract[^>]*>(.*?)</Abstract>', raw_content, re.DOTALL)
                if match:
                    text = re.sub(r'<[^>]+>', ' ', match.group(1))
                    return text.strip()

            if raw_content.strip().startswith("{"):
                try:
                    data = json.loads(raw_content)
                    if "protocolSection" in data:
                        desc = data.get("protocolSection", {}).get("descriptionModule", {})
                        return desc.get("briefSummary", "") or desc.get("detailedDescription", "")
                except Exception:
                    pass

            if "<html" in raw_content[:2000].lower() or "<body" in raw_content[:2000].lower():
                text = self._extract_text_from_html(raw_content)
                if text:
                    return text

        return ""

    @staticmethod
    def _extract_text_from_html(html: str) -> str:
        """Extract visible text from HTML, stripping tags/scripts/styles."""
        s = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
        s = re.sub(r'<[^>]+>', ' ', s)
        s = re.sub(r'&[a-zA-Z]+;', ' ', s)
        s = re.sub(r'&#?\w+;', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s[:RAW_CONTENT_CHARS]

    def _extract_sponsor(self, study_data: Dict) -> str:
        """Extract sponsor information from study data."""
        for key in ("sponsor", "lead_sponsor"):
            value = study_data.get(key, "")
            if value:
                return value

        sponsors = study_data.get("sponsors", {})
        if isinstance(sponsors, dict):
            lead = sponsors.get("leadSponsor", {})
            if isinstance(lead, dict):
                return lead.get("name", "")

        return "Not specified"

    def _extract_intervention(self, study_data: Dict) -> str:
        """Extract intervention information from study data."""
        intervention = study_data.get("intervention", "")
        if intervention:
            return intervention

        interventions = study_data.get("interventions", [])
        if interventions and isinstance(interventions, list):
            parts = []
            for interv in interventions[:3]:
                if isinstance(interv, dict):
                    name = interv.get("name", "")
                    desc = interv.get("description", "")
                    parts.append(f"{name}: {desc}" if desc else name)
                elif isinstance(interv, str):
                    parts.append(interv)
            return "; ".join(parts)

        return ""

    def _parse_json_response(self, content: str) -> Optional[Dict]:
        """Parse the verification JSON from an LLM response (tolerant of prose/fences)."""
        if not content:
            return None
        try:
            if content.startswith("{"):
                brace_count = 0
                last_json_start = None
                for i, char in enumerate(content):
                    if char == '{':
                        if brace_count == 0:
                            last_json_start = i
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0 and last_json_start is not None:
                            json_str = content[last_json_start:i+1]
                            if '"type": "reasoning"' not in json_str and "'type': 'reasoning'" not in json_str:
                                try:
                                    return json.loads(json_str)
                                except Exception:
                                    pass

            if "```" in content:
                match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
                if match:
                    return json.loads(match.group(1))

            match = re.search(r'\{[^{}]*"is_relevant"[^{}]*\}', content, re.DOTALL)
            if match:
                return json.loads(match.group(0))

        except json.JSONDecodeError:
            pass

        return None

    def _fallback_verification(
        self,
        study_data: Dict,
        dtx_data: Dict,
        raw_content: Optional[str]
    ) -> Dict:
        """Fallback verification using keyword matching (LLM unavailable)."""
        dtx_name = dtx_data.get("dtx_name", "")
        company = dtx_data.get("company_provider", "")

        core_name = self._clean_dtx_name(dtx_name)
        company_name = self._clean_company_name(company)

        text_parts = [
            study_data.get("title", ""),
            study_data.get("abstract", ""),
            study_data.get("brief_summary", ""),
            study_data.get("sponsor", ""),
            study_data.get("intervention", ""),
        ]
        if raw_content:
            text_parts.append(raw_content[:RAW_CONTENT_CHARS])

        text_to_search = " ".join(str(p) for p in text_parts).lower()

        matched = []
        if core_name and core_name.lower() in text_to_search:
            matched.append("product_name")

        if "product_name" not in matched and core_name:
            first_word = core_name.split()[0] if core_name.split() else ""
            if len(first_word) >= 4 and first_word.lower() in text_to_search:
                matched.append("product_name_partial")

        if company_name and company_name.lower() in text_to_search:
            matched.append("company_name")

        is_relevant = "product_name" in matched or "product_name_partial" in matched
        confidence = len(matched) * 40

        return {
            "is_relevant": is_relevant,
            "confidence": min(confidence, 80),
            "reason": f"Fallback keyword check: matched {matched}" if matched else "No product name match found",
            "matched_elements": matched
        }

    def _clean_dtx_name(self, dtx_name: str) -> str:
        """Extract clean product name (drop trademark symbols, condition suffixes)."""
        if not dtx_name:
            return ""

        clean = re.sub(r'[®™]', '', dtx_name).strip()
        clean = clean.split(" - ")[0].split(":")[0].strip()
        clean = re.sub(
            r'\s+(für|bei|zur|gegen|im)\s+\w+(\s+\w+)*$',
            '', clean, flags=re.IGNORECASE
        ).strip()
        clean = re.sub(
            r'\s+\S*(?:therapie|app)\s*$',
            '', clean, flags=re.IGNORECASE
        ).strip()
        clean = re.sub(
            r'\s+(die|der|das)\s*$',
            '', clean, flags=re.IGNORECASE
        ).strip()

        return clean

    def _clean_company_name(self, company: str) -> str:
        """Extract clean company name via the shared normalizer, then drop the
        trailing legal-entity suffix so matching uses the distinctive part."""
        clean = normalize_company_name(company)
        if not clean:
            return ""

        return re.sub(
            r'\s*(GmbH|mbH|AG|UG|SE|KG|e\.V\.|B\.V\.|s\.r\.o\.|Ltd\.?|Inc\.?|LLC|Corp\.?|Co\.?)\s*$',
            '', clean, flags=re.IGNORECASE
        ).strip()

    async def verify_candidates_batch(
        self,
        candidates: List[Dict],
        dtx_data: Dict,
        raw_files_dir: Optional[Path] = None
    ) -> Tuple[List[Dict], List[Dict]]:
        """Verify a batch of candidates and split into relevant/rejected.

        Loads each candidate's raw file (when available) and passes it to the
        verifier so the LLM sees full text, not just the abstract. The loaded
        raw content is attached to each study under the transient "__raw_content"
        key so the classifier can reuse it; callers must strip it before saving.

        Returns:
            Tuple of (relevant_studies, rejected_studies)
        """
        relevant = []
        rejected = []

        for study in candidates:
            raw_content = self._load_raw_for_study(study, raw_files_dir)

            verification = await self.verify_study(study, dtx_data, raw_content)
            study["_verification"] = verification
            study["__raw_content"] = raw_content

            if verification.get("is_relevant", False):
                relevant.append(study)
            else:
                rejected.append(study)

        return relevant, rejected

    @staticmethod
    def _load_raw_for_study(study: Dict, raw_files_dir: Optional[Path]) -> Optional[str]:
        """Load the raw candidate file (json/xml) for a study, if present."""
        if not raw_files_dir:
            return None
        study_id = (
            study.get("study_id") or study.get("pmid")
            or study.get("nct_id") or study.get("drks_id")
            or study.get("isrctn_id")
        )
        if not study_id:
            return None
        for ext in ("json", "xml"):
            raw_file = raw_files_dir / f"{study_id}.{ext}"
            if raw_file.exists():
                try:
                    return raw_file.read_text(encoding="utf-8")
                except Exception:
                    return None
        return None


class EvidenceClassifierV2:
    """Classify a verified-relevant study as RCT, RWE, or INELIGIBLE (Layer 2).

    Uses a fast keyword pass for clear cases and an LLM for ambiguous ones.
    Studies that are not a primary RCT or RWE design are labeled INELIGIBLE so
    the orchestrator can exclude them from verified evidence.
    """

    RCT_KEYWORDS = [
        "randomized", "randomised", "rct", "controlled trial",
        "double-blind", "double blind", "placebo-controlled",
        "phase ii", "phase iii", "phase 2", "phase 3",
        "interventional", "clinical trial"
    ]

    RWE_KEYWORDS = [
        "observational", "retrospective", "registry", "cohort",
        "real-world", "real world", "cross-sectional", "case-control",
        "pragmatic", "naturalistic", "survey", "chart review"
    ]

    # Designs that are neither primary RCT nor primary RWE.
    INELIGIBLE_PUB_TYPES = [
        "review", "systematic review", "meta-analysis", "meta analysis",
        "editorial", "comment", "letter", "case reports", "case report",
        "guideline", "practice guideline", "news", "published erratum",
        "retracted publication",
    ]
    INELIGIBLE_TITLE_MARKERS = [
        "systematic review", "meta-analysis", "meta analysis", "scoping review",
        "narrative review", "study protocol", "protocol for", "a protocol",
        "rationale and design", "case report",
    ]

    SYSTEM_PROMPT_TEMPLATE = """\
You are a clinical research methodologist. You classify a single study by its design \
into exactly one category for a Digital Therapeutics evidence base.

Definitions:
- RCT (Randomized Controlled Trial): an interventional, experimental study that randomly \
allocates participants to an intervention group and a control/comparator group under \
controlled trial conditions. Randomization to isolate the treatment effect is the defining \
feature.
- RWE (Real-World Evidence): clinical evidence about a product's use, benefits, or risks \
derived from observational designs or real-world data collected in routine clinical practice - \
for example prospective or retrospective cohort, case-control, registry-based, pragmatic \
non-randomized studies, claims data, electronic health records, or app/device usage analyses.
- INELIGIBLE: anything that is not a primary RCT or primary RWE study - for example narrative \
or systematic reviews, meta-analyses, study protocols without results, editorials, \
commentaries, letters, case reports, or usability/qualitative-only studies.

Decision axis: experimental-and-randomized (RCT) versus observational/real-world primary \
study (RWE) versus not a primary effectiveness study (INELIGIBLE). A protocol with no results \
yet is INELIGIBLE.

Return ONLY a valid JSON object (no markdown, no code fences, no commentary) with exactly \
these keys:

{schema_json}\
"""

    CLASSIFICATION_PROMPT_TEMPLATE = """\
Classify the study below by its design.

Title: {title}
Abstract / summary: {abstract}
Study type: {study_type}
Publication types: {publication_types}
{raw_section}
Return the JSON object only."""

    VALID_LABELS = {"RCT", "RWE", "INELIGIBLE"}

    def __init__(self, metrics_sink: Optional[List[Dict]] = None):
        """Initialize the classifier."""
        self.llm = LLMProvider.get_llm(
            temperature=0.0, max_tokens=400, enable_web_search=False
        )
        self.provider = LLMProvider.get_active_provider()
        self.model = LLMProvider.get_active_model()

        self.schema = _load_schema(CLASSIFICATION_SCHEMA_PATH)
        schema_json = json.dumps(self.schema, indent=2, ensure_ascii=False)
        self.system_prompt = self.SYSTEM_PROMPT_TEMPLATE.replace(
            "{schema_json}", schema_json
        )

        self.metrics: List[Dict] = metrics_sink if metrics_sink is not None else []

    async def classify(self, study_data: Dict, raw_content: Optional[str] = None) -> Dict:
        """Classify a study as RCT, RWE, or INELIGIBLE.

        Returns:
            Classification dict: classification, study_design, confidence, reason.
        """
        keyword_result = self._keyword_classify(study_data, raw_content=raw_content)

        # High-confidence keyword decision (including a clear INELIGIBLE design).
        if keyword_result["confidence"] >= 80:
            return keyword_result

        try:
            llm_result = await self._llm_classify(study_data, raw_content=raw_content)

            # Agreement between keyword and LLM boosts confidence.
            if (
                keyword_result["confidence"] > 0
                and keyword_result["classification"] == llm_result["classification"]
            ):
                llm_result["confidence"] = max(
                    keyword_result["confidence"], llm_result["confidence"]
                )
            return llm_result

        except Exception:
            # On LLM failure, use a definite keyword result; otherwise mark
            # INELIGIBLE rather than silently defaulting to RWE.
            if keyword_result["classification"] in ("RCT", "RWE") and keyword_result["confidence"] > 0:
                return keyword_result
            return {
                "classification": "INELIGIBLE",
                "study_design": "unknown",
                "confidence": 0,
                "reason": "Study design could not be determined (keyword + LLM classification unavailable).",
            }

    def _keyword_classify(self, study_data: Dict, raw_content: Optional[str] = None) -> Dict:
        """Classify using keyword matching. Returns UNKNOWN when no clear signal."""
        pub_types = study_data.get("publication_types", []) or []
        pub_types_lower = [str(pt).lower() for pt in pub_types]
        title_lower = str(study_data.get("title", "")).lower()

        # Strong INELIGIBLE signal from publication types or title markers.
        for pt in pub_types_lower:
            if any(marker == pt or marker in pt for marker in self.INELIGIBLE_PUB_TYPES):
                # "clinical trial" pub types may co-exist; only block pure non-trial types.
                if not any(t in pt for t in ("trial", "randomized", "randomised")):
                    return {
                        "classification": "INELIGIBLE",
                        "study_design": pt,
                        "confidence": 85,
                        "reason": f"Publication type indicates a non-RCT/RWE design ({pt}).",
                    }
        for marker in self.INELIGIBLE_TITLE_MARKERS:
            if marker in title_lower:
                return {
                    "classification": "INELIGIBLE",
                    "study_design": marker,
                    "confidence": 80,
                    "reason": f"Title indicates a non-RCT/RWE design ('{marker}').",
                }

        text_parts = [
            study_data.get("title", ""),
            study_data.get("abstract", ""),
            study_data.get("study_type", ""),
            study_data.get("study_design", ""),
        ]
        if raw_content:
            if "<html" in raw_content[:2000].lower() or "<body" in raw_content[:2000].lower():
                text_parts.append(EvidenceVerifier._extract_text_from_html(raw_content)[:3000])
            else:
                text_parts.append(raw_content[:3000])
        text_parts.extend(pub_types_lower)
        text = " ".join(str(p) for p in text_parts).lower()

        rct_score = sum(1 for kw in self.RCT_KEYWORDS if kw in text)
        rwe_score = sum(1 for kw in self.RWE_KEYWORDS if kw in text)

        for pt in pub_types_lower:
            if "randomized controlled trial" in pt:
                rct_score += 3
            elif "observational" in pt or "cohort" in pt:
                rwe_score += 3

        if rct_score > rwe_score:
            return {
                "classification": "RCT",
                "study_design": "randomized controlled trial",
                "confidence": min(50 + rct_score * 10, 95),
                "reason": f"Keyword match: {rct_score} RCT indicators vs {rwe_score} RWE indicators.",
            }
        if rwe_score > rct_score:
            return {
                "classification": "RWE",
                "study_design": "observational / real-world study",
                "confidence": min(50 + rwe_score * 10, 95),
                "reason": f"Keyword match: {rwe_score} RWE indicators vs {rct_score} RCT indicators.",
            }

        # No clear signal -> defer to the LLM.
        return {
            "classification": "UNKNOWN",
            "study_design": "unknown",
            "confidence": 0,
            "reason": "No clear keyword indicators; deferring to LLM.",
        }

    async def _llm_classify(self, study_data: Dict, raw_content: Optional[str] = None) -> Dict:
        """Classify using the LLM (schema-driven prompt)."""
        raw_section = ""
        if raw_content:
            if "<html" in raw_content[:2000].lower() or "<body" in raw_content[:2000].lower():
                raw_text = EvidenceVerifier._extract_text_from_html(raw_content)[:3000]
            else:
                raw_text = raw_content[:3000]
            if raw_text.strip():
                raw_section = f"Full text / raw content:\n{raw_text}\n"

        abstract = study_data.get("abstract") or study_data.get("brief_summary") or "N/A"
        prompt = self.CLASSIFICATION_PROMPT_TEMPLATE.format(
            title=study_data.get("title", "N/A"),
            abstract=str(abstract)[:1800],
            study_type=study_data.get("study_type", "N/A"),
            publication_types=study_data.get("publication_types", "N/A"),
            raw_section=raw_section,
        )

        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=prompt)
        ]

        response, metrics = await invoke_with_metrics(
            self.llm,
            messages,
            provider=self.provider,
            model=self.model,
            call_label="phase2_classify",
            web_search=False,
            extra={
                "study_id": EvidenceVerifier._study_id(study_data),
                "source": study_data.get("source", "Unknown"),
            },
        )
        self.metrics.append(metrics)

        parsed = self._parse_classification(_response_text(response))
        if parsed:
            return parsed

        raise ValueError("Could not parse classification response")

    def _parse_classification(self, content: str) -> Optional[Dict]:
        """Parse + normalize the classification JSON; enforce a valid label."""
        if not content:
            return None
        obj = None
        try:
            if content.startswith("{"):
                match = re.search(r'\{.*\}', content, re.DOTALL)
                if match:
                    obj = json.loads(match.group(0))
            if obj is None and "```" in content:
                m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
                if m:
                    obj = json.loads(m.group(1))
            if obj is None:
                m = re.search(r'\{[^{}]*"classification"[^{}]*\}', content, re.DOTALL)
                if m:
                    obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None

        if not isinstance(obj, dict):
            return None

        label = str(obj.get("classification", "")).strip().upper()
        if label not in self.VALID_LABELS:
            label = "INELIGIBLE"
        return {
            "classification": label,
            "study_design": obj.get("study_design", ""),
            "confidence": obj.get("confidence", 0),
            "reason": obj.get("reason", ""),
        }
