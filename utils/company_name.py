"""Shared company-name normalization.

The German DiGA detail pages embed the manufacturer name inside a block that
also contains DiGA status notes ("... wurde am DD.MM.YYYY aus dem Verzeichnis
gestrichen."), boilerplate ("Weitere Hinweise finden Sie hier.",
"übermittelten Angaben"), and fragments of the product name. Across every
observed entry the real company name is the LAST non-empty line of that block,
optionally followed by a country suffix (", Deutschland").

This module is the single source of truth for turning a raw ``company_provider``
value into a clean company name. It is used both at scrape-time (so newly
scraped data is stored clean) and at read-time (so older/polluted data still
yields correct search queries).
"""
from __future__ import annotations

import re

# Legal-entity suffixes that reliably mark the line carrying the company name.
_LEGAL = re.compile(
    r"(GmbH|mbH|AG|UG|SE|KG|e\.V\.|B\.V\.|s\.r\.o\.|Ltd\.?|Inc\.?|LLC|Corp\.?)",
    re.IGNORECASE,
)


def normalize_company_name(raw: str) -> str:
    """Return the clean company name from a raw ``company_provider`` value.

    Rules (verified against the German DiGA dataset):
    - "Unknown"/empty -> "".
    - Prefer the LAST non-empty line that contains a legal-entity suffix;
      otherwise fall back to the last non-empty line.
    - Drop any trailing country suffix after the first comma.
    - Collapse internal whitespace.

    Args:
        raw: Raw company/provider string (may be multi-line and noisy).

    Returns:
        Clean company name, or "" when none can be determined.
    """
    if not raw or raw.strip().lower() == "unknown":
        return ""

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return ""

    legal_lines = [line for line in lines if _LEGAL.search(line)]
    name = (legal_lines[-1] if legal_lines else lines[-1]).split(",")[0].strip()
    return re.sub(r"\s+", " ", name)
