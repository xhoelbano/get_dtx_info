"""LLM-based evidence relevance verifier for Layer 2 classification.

This module provides LLM-based verification of whether candidate studies
are actually relevant to a specific Digital Therapeutic (DTx) product.

This is Layer 2 of the two-layer classification system:
- Layer 1: Collect all search results as candidates (no filtering)
- Layer 2: LLM verifies each candidate against DTx metadata
"""
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage

from .llm_provider import LLMProvider


class EvidenceVerifier:
    """LLM-based verification of evidence relevance to specific DTx products.
    
    Reads raw candidate files, compares with DTx metadata, and determines
    if the study is actually about the specific DTx product.
    """
    
    SYSTEM_PROMPT = """You are a clinical evidence analyst specializing in Digital Therapeutics (DTx).

Your task is to determine if a clinical study or publication is specifically about 
a particular digital therapeutic product.

IMPORTANT RULES:
1. The study must EXPLICITLY mention the specific DTx product by name or clearly describe it
2. Studies about the same CONDITION (e.g., depression, back pain) but different treatments are NOT relevant
3. Studies about the same COMPANY but different products are NOT relevant
4. Generic studies about "digital therapeutics" or "mobile health apps" without specific product mention are NOT relevant
5. The product name, company name, or a very specific description must appear in the study

Be STRICT - it's better to reject a borderline case than include irrelevant evidence.

Respond with a JSON object containing:
{
  "is_relevant": true/false,
  "confidence": 0-100,
  "reason": "brief explanation",
  "matched_elements": ["list", "of", "what", "matched"]
}"""

    VERIFICATION_PROMPT_TEMPLATE = """Determine if this study is specifically about the Digital Therapeutic product described below.

=== DTx PRODUCT INFORMATION ===
Product Name: {dtx_name}
Company: {company}
Clinical Area (ICD-10): {icd_codes}
Description: {description}

=== STUDY INFORMATION ===
Title: {study_title}
Abstract/Summary: {study_abstract}
Sponsor/Funder: {study_sponsor}
Intervention: {study_intervention}
Source: {study_source}
{raw_page_section}
=== QUESTION ===
Is this study specifically about the digital therapeutic "{dtx_name}" from "{company}"?

Consider:
1. Does the study title, abstract, OR the raw page content mention "{dtx_name}" or a clear variation?
2. Is the sponsor/funder related to "{company}"?
3. Does the intervention description match this DTx?
4. Could this be about a different product or a generic study?

Respond with JSON only:"""

    def __init__(self):
        """Initialize the evidence verifier."""
        self.llm = LLMProvider.get_llm(temperature=0.0, max_tokens=500)
    
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
            raw_content: Optional raw file content (XML or JSON string)
            
        Returns:
            Verification result dictionary:
            {
                "is_relevant": bool,
                "confidence": int (0-100),
                "reason": str,
                "matched_elements": list
            }
        """
        # Extract DTx info
        dtx_name = dtx_data.get("dtx_name", "Unknown")
        company = dtx_data.get("company_provider", "Unknown")
        icd_codes = dtx_data.get("clinical_area_icd10", [])
        description = dtx_data.get("description", "")[:500]
        
        # Extract study info
        study_title = study_data.get("title", "")
        study_abstract = self._extract_abstract(study_data, raw_content)
        study_sponsor = self._extract_sponsor(study_data)
        study_intervention = self._extract_intervention(study_data)
        study_source = study_data.get("source", "Unknown")
        
        raw_page_section = ""
        if raw_content:
            extracted_text = self._extract_text_from_html(raw_content) if (
                "<html" in raw_content[:2000].lower() or "<body" in raw_content[:2000].lower()
            ) else raw_content[:4000]
            if extracted_text and extracted_text.strip():
                raw_page_section = (
                    "\n=== RAW PAGE CONTENT (from publication/source page) ===\n"
                    + extracted_text[:4000]
                    + "\n"
                )

        prompt = self.VERIFICATION_PROMPT_TEMPLATE.format(
            dtx_name=dtx_name,
            company=company,
            icd_codes=", ".join(icd_codes[:5]) if icd_codes else "Not specified",
            description=description,
            study_title=study_title,
            study_abstract=study_abstract[:2000] if study_abstract else "Not available",
            study_sponsor=study_sponsor,
            study_intervention=study_intervention[:500] if study_intervention else "Not specified",
            study_source=study_source,
            raw_page_section=raw_page_section,
        )
        
        try:
            messages = [
                SystemMessage(content=self.SYSTEM_PROMPT),
                HumanMessage(content=prompt)
            ]
            
            response = await self.llm.ainvoke(messages)
            content = response.content.strip()
            
            # Parse JSON from response
            result = self._parse_json_response(content)
            
            if result:
                return result
            
        except Exception as e:
            print(f"    LLM verification error: {e}")
        
        # Fallback: use basic keyword matching
        return self._fallback_verification(study_data, dtx_data, raw_content)
    
    def _extract_abstract(self, study_data: Dict, raw_content: Optional[str]) -> str:
        """Extract abstract/summary from study data or raw content."""
        # Try parsed fields first
        abstract = study_data.get("abstract", "")
        if abstract:
            return abstract
        
        abstract = study_data.get("brief_summary", "")
        if abstract:
            return abstract
        
        abstract = study_data.get("detailed_description", "")
        if abstract:
            return abstract
        
        # Try raw content if available
        if raw_content:
            # Try to extract from XML
            if "<Abstract>" in raw_content:
                match = re.search(r'<Abstract[^>]*>(.*?)</Abstract>', raw_content, re.DOTALL)
                if match:
                    # Clean XML tags
                    text = re.sub(r'<[^>]+>', ' ', match.group(1))
                    return text.strip()
            
            # Try to extract from JSON
            if raw_content.strip().startswith("{"):
                try:
                    data = json.loads(raw_content)
                    # Navigate common JSON structures
                    if "protocolSection" in data:
                        desc = data.get("protocolSection", {}).get("descriptionModule", {})
                        return desc.get("briefSummary", "") or desc.get("detailedDescription", "")
                except:
                    pass
            
            # Try HTML: extract visible text
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
        return s[:5000]
    
    def _extract_sponsor(self, study_data: Dict) -> str:
        """Extract sponsor information from study data."""
        sponsor = study_data.get("sponsor", "")
        if sponsor:
            return sponsor
        
        sponsor = study_data.get("lead_sponsor", "")
        if sponsor:
            return sponsor
        
        # Check nested sponsor structures
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
        
        # Check interventions list
        interventions = study_data.get("interventions", [])
        if interventions:
            if isinstance(interventions, list):
                parts = []
                for interv in interventions[:3]:  # Limit to 3
                    if isinstance(interv, dict):
                        name = interv.get("name", "")
                        desc = interv.get("description", "")
                        parts.append(f"{name}: {desc}" if desc else name)
                    elif isinstance(interv, str):
                        parts.append(interv)
                return "; ".join(parts)
        
        return ""
    
    def _parse_json_response(self, content: str) -> Optional[Dict]:
        """Parse JSON from LLM response."""
        # Try direct parse
        try:
            # Handle reasoning models that may prepend thinking blocks
            if content.startswith("{"):
                # Find the last complete JSON object
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
                            # Skip reasoning blocks
                            if '"type": "reasoning"' not in json_str and "'type': 'reasoning'" not in json_str:
                                try:
                                    return json.loads(json_str)
                                except:
                                    pass
            
            # Try extracting from code block
            if "```" in content:
                match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
                if match:
                    return json.loads(match.group(1))
            
            # Last resort: find JSON object
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
        """Fallback verification using keyword matching.
        
        Used when LLM verification fails.
        """
        dtx_name = dtx_data.get("dtx_name", "")
        company = dtx_data.get("company_provider", "")
        
        # Extract core name (without condition suffixes)
        core_name = self._clean_dtx_name(dtx_name)
        company_name = self._clean_company_name(company)
        
        # Collect text to search
        text_parts = [
            study_data.get("title", ""),
            study_data.get("abstract", ""),
            study_data.get("brief_summary", ""),
            study_data.get("sponsor", ""),
            study_data.get("intervention", ""),
        ]
        
        if raw_content:
            text_parts.append(raw_content[:5000])
        
        text_to_search = " ".join(str(p) for p in text_parts).lower()
        
        matched = []
        
        # Check for product name (cleaned full name)
        if core_name and core_name.lower() in text_to_search:
            matched.append("product_name")
        
        # Also try just the first word (brand name) if multi-word and at least 4 chars
        if "product_name" not in matched and core_name:
            first_word = core_name.split()[0] if core_name.split() else ""
            if len(first_word) >= 4 and first_word.lower() in text_to_search:
                matched.append("product_name_partial")
        
        # Check for company name
        if company_name and company_name.lower() in text_to_search:
            matched.append("company_name")
        
        # Determine relevance
        is_relevant = "product_name" in matched or "product_name_partial" in matched
        confidence = len(matched) * 40  # 40 per match, max 80
        
        return {
            "is_relevant": is_relevant,
            "confidence": min(confidence, 80),  # Cap at 80 for fallback
            "reason": f"Fallback keyword check: matched {matched}" if matched else "No product name match found",
            "matched_elements": matched
        }
    
    def _clean_dtx_name(self, dtx_name: str) -> str:
        """Extract clean product name."""
        if not dtx_name:
            return ""
        
        clean = re.sub(r'[®™]', '', dtx_name).strip()
        clean = clean.split(" - ")[0].split(":")[0].strip()
        
        # Remove German suffixes
        clean = re.sub(
            r'\s+(für|bei|zur|gegen|im)\s+\w+(\s+\w+)*$',
            '', clean, flags=re.IGNORECASE
        ).strip()
        
        # Remove trailing compound-word suffixes (e.g. "Diabetestherapie", "Therapie", "App")
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
        """Extract clean company name."""
        if not company:
            return ""
        
        clean = re.sub(
            r'\s*(GmbH|AG|Inc\.?|Ltd\.?|LLC|Corp\.?|Co\.?).*$',
            '', company, flags=re.IGNORECASE
        ).strip()
        
        return clean.split(",")[0].strip()
    
    async def verify_candidates_batch(
        self,
        candidates: List[Dict],
        dtx_data: Dict,
        raw_files_dir: Optional[Path] = None
    ) -> Tuple[List[Dict], List[Dict]]:
        """Verify a batch of candidates and split into relevant/irrelevant.
        
        Args:
            candidates: List of candidate study dictionaries
            dtx_data: DTx metadata
            raw_files_dir: Optional path to raw files directory
            
        Returns:
            Tuple of (relevant_studies, rejected_studies)
        """
        relevant = []
        rejected = []
        
        for study in candidates:
            # Try to load raw content if available
            raw_content = None
            if raw_files_dir:
                study_id = study.get("study_id") or study.get("pmid") or study.get("nct_id") or study.get("drks_id")
                if study_id:
                    # Try JSON
                    raw_file = raw_files_dir / f"{study_id}.json"
                    if raw_file.exists():
                        try:
                            raw_content = raw_file.read_text(encoding="utf-8")
                        except:
                            pass
                    else:
                        # Try XML
                        raw_file = raw_files_dir / f"{study_id}.xml"
                        if raw_file.exists():
                            try:
                                raw_content = raw_file.read_text(encoding="utf-8")
                            except:
                                pass
            
            # Verify the study
            verification = await self.verify_study(study, dtx_data, raw_content)
            study["_verification"] = verification
            
            if verification.get("is_relevant", False):
                relevant.append(study)
            else:
                rejected.append(study)
        
        return relevant, rejected


class EvidenceClassifierV2:
    """RCT vs RWE classification for verified evidence (Layer 2).
    
    This classifier only runs on LLM-verified relevant studies.
    Uses both keyword-based and LLM-based classification.
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
    
    def __init__(self):
        """Initialize the classifier."""
        self.llm = LLMProvider.get_llm(temperature=0.0, max_tokens=300)
    
    async def classify(self, study_data: Dict, raw_content: Optional[str] = None) -> Dict:
        """Classify a study as RCT or RWE.
        
        Args:
            study_data: Study metadata dictionary
            raw_content: Optional raw page text (HTML text or XML) for extra context
            
        Returns:
            Classification result:
            {
                "classification": "RCT" or "RWE",
                "confidence": int (0-100),
                "reason": str
            }
        """
        # Try keyword classification first (include raw_content text)
        keyword_result = self._keyword_classify(study_data, raw_content=raw_content)
        
        if keyword_result["confidence"] >= 80:
            return keyword_result
        
        # Use LLM for ambiguous cases
        try:
            llm_result = await self._llm_classify(study_data, raw_content=raw_content)
            
            # Combine results if both available
            if keyword_result["confidence"] > 0:
                # Weight towards keyword if it's confident
                if keyword_result["classification"] == llm_result["classification"]:
                    return {
                        "classification": llm_result["classification"],
                        "confidence": max(keyword_result["confidence"], llm_result["confidence"]),
                        "reason": llm_result["reason"]
                    }
            
            return llm_result
            
        except Exception as e:
            # Fall back to keyword result
            return keyword_result
    
    def _keyword_classify(self, study_data: Dict, raw_content: Optional[str] = None) -> Dict:
        """Classify using keyword matching."""
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
        
        # Include publication types
        pub_types = study_data.get("publication_types", [])
        if pub_types:
            text_parts.extend(str(pt) for pt in pub_types)
        
        text = " ".join(str(p) for p in text_parts).lower()
        
        # Count matches
        rct_score = sum(1 for kw in self.RCT_KEYWORDS if kw in text)
        rwe_score = sum(1 for kw in self.RWE_KEYWORDS if kw in text)
        
        # Strong indicators in publication types
        if pub_types:
            for pt in pub_types:
                pt_lower = str(pt).lower()
                if "randomized controlled trial" in pt_lower:
                    rct_score += 3
                elif "observational" in pt_lower or "cohort" in pt_lower:
                    rwe_score += 3
        
        # Determine classification
        if rct_score > rwe_score:
            confidence = min(50 + rct_score * 10, 95)
            return {
                "classification": "RCT",
                "confidence": confidence,
                "reason": f"Keyword match: {rct_score} RCT indicators vs {rwe_score} RWE indicators"
            }
        elif rwe_score > rct_score:
            confidence = min(50 + rwe_score * 10, 95)
            return {
                "classification": "RWE",
                "confidence": confidence,
                "reason": f"Keyword match: {rwe_score} RWE indicators vs {rct_score} RCT indicators"
            }
        else:
            return {
                "classification": "RWE",  # Default to RWE
                "confidence": 30,
                "reason": "No clear indicators found, defaulting to RWE"
            }
    
    async def _llm_classify(self, study_data: Dict, raw_content: Optional[str] = None) -> Dict:
        """Classify using LLM."""
        raw_section = ""
        if raw_content:
            if "<html" in raw_content[:2000].lower() or "<body" in raw_content[:2000].lower():
                raw_text = EvidenceVerifier._extract_text_from_html(raw_content)[:3000]
            else:
                raw_text = raw_content[:3000]
            if raw_text.strip():
                raw_section = f"\nRaw page content:\n{raw_text}\n"

        prompt = f"""Classify this clinical study as either RCT (Randomized Controlled Trial) or RWE (Real-World Evidence).

Title: {study_data.get('title', 'N/A')}
Abstract: {study_data.get('abstract', study_data.get('brief_summary', 'N/A'))[:1500]}
Study Type: {study_data.get('study_type', 'N/A')}
Publication Types: {study_data.get('publication_types', 'N/A')}
{raw_section}
RCT indicators: randomization, blinding, placebo control, interventional design
RWE indicators: observational, retrospective, registry-based, cohort study

Respond with JSON only:
{{"classification": "RCT" or "RWE", "confidence": 0-100, "reason": "brief explanation"}}"""

        messages = [
            SystemMessage(content="You are a clinical research classifier. Classify studies as RCT or RWE based on study design."),
            HumanMessage(content=prompt)
        ]
        
        response = await self.llm.ainvoke(messages)
        content = response.content.strip()
        
        # Parse response
        try:
            # Handle various JSON formats
            if "{" in content:
                match = re.search(r'\{[^{}]*"classification"[^{}]*\}', content, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
        except:
            pass
        
        # Default
        return {
            "classification": "RWE",
            "confidence": 50,
            "reason": "LLM classification failed, defaulting to RWE"
        }
