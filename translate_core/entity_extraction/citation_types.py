"""Citation type taxonomy and style detection.

CitationType and CitationStyle enums, style detection from diagnostic
markers, short-reference detection, and author-form heuristics.
"""

from __future__ import annotations

import re
from enum import Enum


class CitationType(str, Enum):
    BOOK = "book"
    BOOK_CHAPTER = "book_chapter"
    JOURNAL_ARTICLE = "journal_article"
    MAGAZINE_ARTICLE = "magazine_article"
    NEWSPAPER_ARTICLE = "newspaper_article"
    WEB_SOURCE = "web_source"
    EXHIBITION_CATALOG = "exhibition_catalog"
    INTERVIEW = "interview"
    THESIS_DISSERTATION = "thesis_dissertation"
    SHORT_REFERENCE = "short_reference"
    OTHER = "cited_work"


class CitationStyle(str, Enum):
    CHICAGO_EN = "chicago_en"
    CHICAGO_SL = "chicago_sl"      # Maska / PNZ / Studia humanitatis
    UNKNOWN = "unknown"




# ── Style detection ──────────────────────────────────────────────────────────
# Diagnostic markers from primary sources (PNZ guide for SL Chicago, CMOS for
# EN Chicago, MLA Style Center for MLA, ISO 690 for SIST).

_SL_CHICAGO_MARKERS = re.compile(
    r"\b(?:Glej|Prim\.?|prev\.|ur\.|str\.|letn\.|št\.|v:\s|V:\s)",
    re.IGNORECASE,
)


def detect_style(segment_text: str) -> CitationStyle:
    """Style detection limited to the two styles actually in this corpus:
    Slovenian Chicago humanities (Maska / Studia humanitatis / PNZ markers)
    and English Chicago. Everything else returns UNKNOWN — VL still handles
    it via the generic footnote-form prompt.
    """
    if not segment_text or len(segment_text) < 4:
        return CitationStyle.UNKNOWN
    if _SL_CHICAGO_MARKERS.search(segment_text):
        return CitationStyle.CHICAGO_SL
    return CitationStyle.CHICAGO_EN


# ── Short-reference detection (subset of SHORT_REFERENCE type) ───────────────

_IBID_RE = re.compile(
    r"^\s*(?:Ibid\.?|Ibidem|prav tam)\b", re.IGNORECASE,
)
_OP_CIT_RE = re.compile(
    r"^\s*(?:op\.\s*cit\.|loc\.\s*cit\.|nav\.\s*delo|cit\.\s*po)\b",
    re.IGNORECASE,
)
_PAGE_ONLY_RE = re.compile(
    r"^\s*(?:pp?\.|str\.)\s*\d+\s*[-–]?\s*\d*\s*\.?\s*$",
    re.IGNORECASE,
)


def is_short_reference(segment_text: str) -> bool:
    """True if the segment looks like a back-reference, not a full citation."""
    s = segment_text.strip()
    if not s:
        return False
    if _IBID_RE.match(s):
        return True
    if _OP_CIT_RE.match(s):
        return True
    if _PAGE_ONLY_RE.match(s) and len(s) < 30:
        return True
    return False


# The user's TM corpus is all footnote-form (Firstname Lastname,) — never
# bibliography-form (Lastname, Firstname.). detect_author_form now uses
# style hints when available; falls back to regex heuristics otherwise.

_LASTNAME_FIRST_RE = re.compile(
    r"^[^\s,]+,\s*[A-Z\u0100-\u024F]",
)


def detect_author_form(segment_text: str, style: CitationStyle | None = None) -> str:
    """Detect whether the segment uses footnote-form (Firstname Lastname)
    or bibliography-form (Lastname, Firstname) author names.

    Chicago SL and Chicago EN footnotes use footnote form.
    Only bibliography sections (alphabetic lists) use bibliography form.
    When style hints are available, use them; otherwise fall back to
    regex heuristics.

    Returns 'footnote' or 'bibliography'.
    """
    # Per O-7: CitationStyle enum is {CHICAGO_EN, CHICAGO_SL, UNKNOWN}.
    # No publisher sub-variants in the enum.
    if style in (CitationStyle.CHICAGO_SL, CitationStyle.CHICAGO_EN):
        # Chicago footnotes always use firstname-last form.
        # Only alphabetic bibliography sections use lastname-first.
        # If the segment starts with a lastname-first pattern, it's bibliography form.
        if _LASTNAME_FIRST_RE.match(segment_text.strip()):
            return "bibliography"
        return "footnote"

    # For UNKNOWN style or Vpogledi-style segments: default to footnote.
    return "footnote"

