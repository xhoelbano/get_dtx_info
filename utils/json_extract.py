"""Provider-agnostic JSON extraction and schema normalization for LLM output.

Different models violate the "return only JSON" instruction in different ways:
- Anthropic Claude often prepends prose ("I'll research...", "Let me analyze...").
- Reasoning models emit a reasoning object *before* the real JSON.
- Some models wrap the JSON in ```json ... ``` fences.

This module pulls the JSON payload out regardless of those variations and then
coerces it into a stable structure (via Pydantic) so that *every* provider
yields the same shape downstream. The shape mirrors data-format/dtx_research.json.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DtxProduct(BaseModel):
    """A single DTx product. Extra/unknown keys are preserved (extra='allow')
    so adding fields to data-format/dtx_research.json needs no change here."""

    model_config = ConfigDict(extra="allow")

    dtx_name: str = "Unknown"
    description: Optional[str] = None
    clinical_area_icd10: List[str] = Field(default_factory=list)
    app_store_url: Optional[str] = None
    play_store_url: Optional[str] = None
    listing_status: Optional[str] = "Unknown"
    price_usd: Optional[str] = None
    source_url: Optional[str] = None

    @field_validator("dtx_name", mode="before")
    @classmethod
    def _coerce_name(cls, v: Any) -> str:
        if v is None:
            return "Unknown"
        return v if isinstance(v, str) else str(v)

    @field_validator("clinical_area_icd10", mode="before")
    @classmethod
    def _coerce_icd10(cls, v: Any) -> List[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        if isinstance(v, (list, tuple)):
            return [str(x) for x in v if x is not None and str(x).strip()]
        return [str(v)]

    @field_validator(
        "description",
        "app_store_url",
        "play_store_url",
        "listing_status",
        "price_usd",
        "source_url",
        mode="before",
    )
    @classmethod
    def _coerce_optional_str(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        return v if isinstance(v, str) else str(v)


class CompanyInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    company_website: Optional[str] = None
    company_founding_year: Optional[int] = None
    headquarters: Optional[str] = None

    @field_validator("company_founding_year", mode="before")
    @classmethod
    def _coerce_year(cls, v: Any) -> Optional[int]:
        if v is None or v == "":
            return None
        try:
            return int(str(v).strip())
        except (ValueError, TypeError):
            return None


class ResearchResult(BaseModel):
    """Top-level research contract. Always exposes dtx_products / company_info /
    research_notes so downstream code can rely on a single stable shape."""

    model_config = ConfigDict(extra="allow")

    dtx_products: List[DtxProduct] = Field(default_factory=list)
    company_info: CompanyInfo = Field(default_factory=CompanyInfo)
    research_notes: Optional[str] = None

    @field_validator("dtx_products", mode="before")
    @classmethod
    def _drop_non_dicts(cls, v: Any) -> List[Any]:
        if v is None:
            return []
        if isinstance(v, dict):
            return [v]
        if isinstance(v, (list, tuple)):
            return [item for item in v if isinstance(item, dict)]
        return []


def _all_top_level_objects(text: str) -> List[str]:
    """Return every balanced top-level ``{...}`` substring in ``text``.

    String contents are tracked so braces inside JSON string values don't throw
    off the depth count.
    """
    objects: List[str] = []
    depth = 0
    start: Optional[int] = None
    in_string = False
    escape = False

    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    objects.append(text[start : i + 1])
                    start = None

    return objects


def _looks_like_reasoning(obj: str) -> bool:
    return (
        '"type": "reasoning"' in obj
        or "'type': 'reasoning'" in obj
        or '"type":"reasoning"' in obj
        or "'type':'reasoning'" in obj
    )


def extract_json_block(text: str) -> Optional[str]:
    """Extract the most relevant JSON object from a raw model response.

    Handles leading prose, ``` fences, and a reasoning object emitted before the
    real payload. Returns the JSON substring, or None if nothing usable is found.
    """
    if not text:
        return None

    cleaned = text.replace("```json", "").replace("```", "")
    objects = _all_top_level_objects(cleaned)
    if not objects:
        return None

    # Prefer the object that actually carries our schema.
    for obj in objects:
        if "dtx_products" in obj:
            return obj

    # Otherwise return the first non-reasoning object.
    for obj in objects:
        if not _looks_like_reasoning(obj):
            return obj

    return objects[0]


def parse_research_response(text: str) -> Optional[Dict[str, Any]]:
    """Parse + normalize a raw model response into the stable research shape.

    Returns a plain dict (dtx_products / company_info / research_notes plus any
    extra keys the model supplied) or None if the text holds no parseable JSON.
    """
    block = extract_json_block(text)
    if block is None:
        return None

    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    try:
        return ResearchResult.model_validate(data).model_dump()
    except Exception:
        return None
