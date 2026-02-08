"""LLM-based evidence classifier for RCT vs RWE determination.

This module uses the configured LLM provider to classify clinical studies
as either Randomized Controlled Trials (RCT) or Real-World Evidence (RWE).
"""
import json
import re
from typing import Dict, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from .llm_provider import LLMProvider


class EvidenceClassifier:
    """Classify clinical evidence as RCT or RWE using LLM.
    
    Uses Azure OpenAI to analyze study characteristics and determine
    whether a study is a Randomized Controlled Trial (RCT) or
    Real-World Evidence (RWE).
    """
    
    SYSTEM_PROMPT = """You are a clinical research expert specializing in study design classification.
Your task is to classify clinical studies as either RCT (Randomized Controlled Trial) or RWE (Real-World Evidence).

CLASSIFICATION CRITERIA:

**RCT (Randomized Controlled Trial):**
- Randomized assignment of participants to intervention/control groups
- Controlled comparison with placebo or standard care
- Prospective design (planned before data collection)
- Interventional study manipulating treatment
- Typically Phase I-IV clinical trials
- Keywords: randomized, randomised, double-blind, placebo-controlled, clinical trial, phase II/III

**RWE (Real-World Evidence):**
- Observational study without randomization
- Retrospective analysis of existing data
- Registry studies or claims data analysis
- Cohort studies or case-control studies
- Cross-sectional studies
- Pragmatic trials in routine care settings
- Post-market surveillance studies
- Keywords: observational, retrospective, registry, cohort, real-world, routine care, claims data

IMPORTANT:
- Analyze the study design, methodology, and type carefully
- Publication type metadata (if available) is highly reliable
- If the study clearly states "randomized" in title/design, it's likely RCT
- Registry-based or retrospective studies are typically RWE
- When uncertain, lean towards RWE (more conservative)

Respond with ONLY a JSON object in this exact format:
{"classification": "RCT" or "RWE", "confidence": 0-100, "reason": "brief explanation"}"""

    # Keyword-based classification hints
    RCT_KEYWORDS = [
        "randomized", "randomised", "rct", "controlled trial",
        "double-blind", "double blind", "placebo-controlled",
        "randomization", "randomisation", "phase ii", "phase iii",
        "phase 2", "phase 3", "clinical trial phase"
    ]
    
    RWE_KEYWORDS = [
        "real-world", "real world", "observational",
        "retrospective", "registry", "claims data",
        "electronic health record", "ehr", "routine care",
        "cohort study", "cross-sectional", "case-control",
        "pragmatic trial", "naturalistic", "post-market",
        "surveillance", "longitudinal cohort"
    ]
    
    def __init__(self):
        """Initialize the classifier."""
        self.llm = LLMProvider.get_llm(temperature=0.0, max_tokens=200)
    
    async def classify(self, study: Dict, hint_rct: bool = None) -> Dict:
        """Classify a study as RCT or RWE.
        
        Args:
            study: Dictionary containing study metadata.
            hint_rct: Optional hint from preliminary analysis.
            
        Returns:
            Classification result with confidence and reason.
        """
        # First try keyword-based classification for clear cases
        keyword_result = self._keyword_classify(study)
        if keyword_result and keyword_result.get("confidence", 0) >= 80:
            return keyword_result
        
        # Use LLM for ambiguous cases
        return await self._llm_classify(study, hint_rct)
    
    def _keyword_classify(self, study: Dict) -> Optional[Dict]:
        """Quick keyword-based classification for clear cases.
        
        Args:
            study: Study dictionary.
            
        Returns:
            Classification dict or None if ambiguous.
        """
        # Collect all text for analysis
        text_parts = [
            study.get("title", ""),
            study.get("abstract", ""),
            study.get("brief_summary", ""),
            study.get("study_type", ""),
            study.get("study_design", ""),
            str(study.get("design", {})),
            study.get("allocation", ""),
        ]
        
        # Add publication types if available (very reliable)
        pub_types = study.get("publication_types", [])
        if pub_types:
            text_parts.extend(pub_types)
        
        text = " ".join(str(p).lower() for p in text_parts if p)
        
        # Check publication types first (most reliable)
        for pt in pub_types:
            pt_lower = pt.lower()
            if "randomized controlled trial" in pt_lower:
                return {
                    "classification": "RCT",
                    "confidence": 95,
                    "reason": f"Publication type: {pt}"
                }
            if "observational" in pt_lower or "cohort" in pt_lower:
                return {
                    "classification": "RWE",
                    "confidence": 90,
                    "reason": f"Publication type: {pt}"
                }
        
        # Score keywords
        rct_score = sum(1 for kw in self.RCT_KEYWORDS if kw in text)
        rwe_score = sum(1 for kw in self.RWE_KEYWORDS if kw in text)
        
        # Strong RCT signals
        if "randomized" in text or "randomised" in text:
            if rwe_score == 0:
                return {
                    "classification": "RCT",
                    "confidence": 85,
                    "reason": "Explicit randomization mentioned"
                }
        
        # Strong RWE signals
        if "observational" in text or "retrospective" in text or "registry" in text:
            if rct_score == 0:
                return {
                    "classification": "RWE",
                    "confidence": 85,
                    "reason": "Observational/retrospective design"
                }
        
        # Clear score difference
        if rct_score > rwe_score + 2:
            return {
                "classification": "RCT",
                "confidence": 70,
                "reason": f"Keyword analysis (RCT:{rct_score}, RWE:{rwe_score})"
            }
        
        if rwe_score > rct_score + 2:
            return {
                "classification": "RWE",
                "confidence": 70,
                "reason": f"Keyword analysis (RCT:{rct_score}, RWE:{rwe_score})"
            }
        
        # Ambiguous - return None to trigger LLM
        return None
    
    async def _llm_classify(self, study: Dict, hint_rct: bool = None) -> Dict:
        """Use LLM to classify ambiguous cases.
        
        Args:
            study: Study dictionary.
            hint_rct: Optional hint from preliminary analysis.
            
        Returns:
            Classification result.
        """
        # Build prompt with study information
        title = study.get("title", "Unknown")
        abstract = study.get("abstract", "") or study.get("brief_summary", "")
        study_type = study.get("study_type", "")
        design = study.get("design", {})
        allocation = design.get("allocation", "") if isinstance(design, dict) else ""
        pub_types = study.get("publication_types", [])
        
        prompt = f"""Classify this clinical study:

Title: {title}

Abstract/Summary: {abstract[:800] if abstract else 'Not available'}

Study Type: {study_type if study_type else 'Not specified'}
Allocation: {allocation if allocation else 'Not specified'}
Publication Types: {', '.join(pub_types) if pub_types else 'Not specified'}

{f'Note: Preliminary analysis suggests this might be {"RCT" if hint_rct else "RWE"}.' if hint_rct is not None else ''}

Classify as RCT or RWE with confidence (0-100) and brief reason."""

        try:
            messages = [
                SystemMessage(content=self.SYSTEM_PROMPT),
                HumanMessage(content=prompt)
            ]
            
            response = await self.llm.ainvoke(messages)
            content = response.content.strip()
            
            # Parse JSON response
            if "```" in content:
                match = re.search(r'```(?:json)?\s*({.*?})\s*```', content, re.DOTALL)
                if match:
                    content = match.group(1)
            
            # Try to extract JSON
            json_match = re.search(r'\{[^{}]*\}', content)
            if json_match:
                result = json.loads(json_match.group())
                
                # Validate and normalize
                classification = result.get("classification", "").upper()
                if classification not in ["RCT", "RWE"]:
                    classification = "RWE"  # Default to RWE if unclear
                
                return {
                    "classification": classification,
                    "confidence": min(100, max(0, int(result.get("confidence", 50)))),
                    "reason": result.get("reason", "LLM classification")
                }
            
        except Exception as e:
            print(f"    LLM classification error: {e}")
        
        # Fallback based on hint or default to RWE
        return {
            "classification": "RCT" if hint_rct else "RWE",
            "confidence": 40,
            "reason": "Fallback classification (LLM failed)"
        }
    
    def batch_keyword_classify(self, studies: list) -> list:
        """Quickly classify multiple studies using keywords only.
        
        Useful for preliminary sorting before detailed LLM analysis.
        
        Args:
            studies: List of study dictionaries.
            
        Returns:
            List of (study, classification) tuples.
        """
        results = []
        for study in studies:
            classification = self._keyword_classify(study)
            if classification:
                results.append((study, classification))
            else:
                # Ambiguous - default to RWE with low confidence
                results.append((study, {
                    "classification": "RWE",
                    "confidence": 30,
                    "reason": "Ambiguous - requires LLM review"
                }))
        return results
