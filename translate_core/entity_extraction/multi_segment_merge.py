"""Forward-spill detection: merge a BIBLIOGRAPHY_ENTRY segment with the next
segment when the citation visibly continues across the boundary.

The user's TMs sometimes split a citation across two consecutive segments
depending on how the TMX was prepared. Example from the Kunst corpus:

  seg N:   "Audre Lorde, The Master's Tools Will Never Dismantle the Master's House, London:"
  seg N+1: "Crossing Press, 1984."

Decision rules (forward-spill only for v1):

  - seg ends with ":"  → almost certainly cut before publisher/place; merge if
                         next seg is short and looks like publisher info
                         (contains a year, OR is short + capitalised opener).
  - seg ends with ","  → cut mid-clause; merge if next seg starts with a year,
                         a page/URL marker, or lowercase continuation.
  - otherwise          → don't merge in v1 (too noisy).
"""

from __future__ import annotations

import re


_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_PAGE_OR_URL_RE = re.compile(r"\b(?:pp?\.|str\.)\s*\d+|https?://")
_LOWERCASE_OPENER_RE = re.compile(r"^[a-zšžčćđáéíóúýň]")
_LEADING_YEAR_RE = re.compile(r"^\s*\d{4}[,.)\s]")
_CAPITALISED_OPENER_RE = re.compile(r"^\s*[A-ZŠŽČĆĐ]")


def should_merge_forward(
    seg_text: str,
    seg_class: str,
    next_seg_text: str,
) -> bool:
    """True if `seg_text` looks like a citation cut mid-clause and
    `next_seg_text` looks like its continuation."""
    if not seg_text or not next_seg_text:
        return False
    if seg_class != "bibliography_entry":
        return False
    s = seg_text.rstrip()
    if not s:
        return False
    nxt = next_seg_text.strip()
    if not nxt:
        return False

    # Case 1: cut before publisher (most common — "..., London:")
    if s.endswith(":"):
        if len(nxt) > 200:
            return False
        if _YEAR_RE.search(nxt):
            return True
        if _CAPITALISED_OPENER_RE.match(nxt):
            return True
        return False

    # Case 2: cut mid-clause (",")
    if s.endswith(","):
        if _LEADING_YEAR_RE.match(nxt):
            return True
        if _PAGE_OR_URL_RE.search(nxt):
            return True
        if _LOWERCASE_OPENER_RE.match(nxt):
            return True
        return False

    # No terminal period/!/? at all is suspicious but ambiguous; skip in v1.
    return False


def merge_forward(seg_text: str, next_seg_text: str) -> str:
    """Glue two segments together with a single space."""
    return f"{seg_text.rstrip()} {next_seg_text.lstrip()}"


# ── Multi-citation within one segment ────────────────────────────────────────
# Footnotes often pack multiple citations into one segment separated by `; `
# (semicolon + space), a universal academic convention. Plain split on that
# separator — no lookahead heuristics.

def split_multi_citation(text: str) -> list[str]:
    """Split a segment on `; ` (semicolon + space). Fragments shorter than
    25 chars are dropped (subtitle internal `;` produces tiny pieces that
    aren't citations). Returns [text] if no usable split."""
    if "; " not in text:
        return [text]
    parts = [p.strip().rstrip(";").strip() for p in text.split("; ")]
    parts = [p for p in parts if len(p) >= 25]
    return parts if len(parts) > 1 else [text]
