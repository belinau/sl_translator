"""Stable slug helper.

Used to produce diacritic-stripped, lowercased ASCII slugs for stable
entity IDs (works, agents, institutions). Extracted here so the helper
survives Phase 7's deletion of the VL-era typed extractor that owned
the original definition.

This is the SAME implementation that ``translate_core/kg_ingest_entities.py``
uses; both call sites can rely on identical behaviour.
"""

from __future__ import annotations

import re
import unicodedata


def _slugify(text: str) -> str:
    """Slug a string for use as a stable entity ID.

    - Normalises to NFKD and drops combining marks (diacritics)
    - Replaces non-alphanumerics with dashes
    - Strips leading/trailing dashes, lowercases
    - Truncates to 80 chars; returns ``"unknown"`` when input collapses to empty.
    """
    nfkd = unicodedata.normalize("NFKD", text)
    s = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:80] if s else "unknown"
