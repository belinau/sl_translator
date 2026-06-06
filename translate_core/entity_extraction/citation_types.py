"""Citation type taxonomy, style detection, and VL prompt templates.

Specification: docs/citation-extraction-spec.md

Eleven citation types with distinct structural schemas. Four citation styles
(Chicago notes-bibliography EN, MLA 9th, Slovenian Chicago humanities/Maska,
SIST ISO 690). Style detection from diagnostic markers; type detection by VL.
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


# Type-specific field schemas. `required` fields MUST be present and verified
# (substring-matched in segment text) for a record to be kept. Subsidiary
# fields that fail substring check are nulled; the record still survives.
TYPE_SCHEMAS: dict[CitationType, dict] = {
    CitationType.BOOK: {
        "required": ["authors", "title", "year"],
        "fields": [
            "authors", "title", "subtitle", "translator", "editor", "edition",
            "publisher", "city", "year", "pages", "url", "doi",
        ],
    },
    CitationType.BOOK_CHAPTER: {
        "required": ["authors", "chapter_title", "book_title", "year"],
        "fields": [
            "authors", "chapter_title", "book_title", "editors", "translator",
            "publisher", "city", "year", "pages",
        ],
    },
    CitationType.JOURNAL_ARTICLE: {
        "required": ["authors", "article_title", "journal", "year"],
        "fields": [
            "authors", "article_title", "journal", "volume", "issue",
            "year", "pages", "doi", "url",
        ],
        # If publisher or city is set on a journal article, the whole record
        # is rejected — they don't belong here.
        "forbidden": ["publisher", "city"],
    },
    CitationType.MAGAZINE_ARTICLE: {
        "required": ["article_title", "magazine", "date"],
        "fields": [
            "authors", "article_title", "magazine", "date", "pages", "url",
        ],
    },
    CitationType.NEWSPAPER_ARTICLE: {
        "required": ["article_title", "newspaper", "date"],
        "fields": [
            "authors", "article_title", "newspaper", "date", "section",
            "page", "url",
        ],
    },
    CitationType.WEB_SOURCE: {
        "required": ["title", "site_name", "url"],
        "fields": [
            "authors", "title", "site_name", "publisher_or_org",
            "date_published", "accessed_date", "url",
        ],
    },
    CitationType.EXHIBITION_CATALOG: {
        "required": ["title", "venue", "year"],
        "fields": [
            "title", "editors", "curators", "venue", "city", "publisher",
            "year", "exhibition_dates",
        ],
    },
    CitationType.INTERVIEW: {
        "required": ["interviewee", "interviewer", "date"],
        "fields": [
            "interviewee", "interviewer", "title_or_program",
            "publication_or_network", "date", "medium", "url",
        ],
    },
    CitationType.THESIS_DISSERTATION: {
        "required": ["authors", "title", "degree_type", "institution", "year"],
        "fields": [
            "authors", "title", "degree_type", "institution", "year", "url",
        ],
    },
    CitationType.SHORT_REFERENCE: {
        "required": ["marker"],
        "fields": [
            "marker", "author_surname", "short_title", "pages",
            "references_full_citation_id",
        ],
    },
    CitationType.OTHER: {
        "required": [],
        "fields": ["authors", "title", "raw_text", "notes"],
    },
}


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




# ── VL prompts: classification (Call 1) ──────────────────────────────────────
# Short, schema-as-example, in the style of the original generic prompt.

SYSTEM_CLASSIFY = "Classify a citation. Output only JSON."

CLASSIFY_TEMPLATE = """\
Citation text:
{segment}

What type is this?

Type (choose one): book, book_chapter, journal_article, magazine_article, newspaper_article, web_source, exhibition_catalog, interview, thesis_dissertation, other

Reply: {{"type":"..."}}"""

CLASSIFY_PREFILL = '{"type":"'
CLASSIFY_MAX_TOKENS = 30


# ── VL prompts: typed extraction (Call 2) ────────────────────────────────────
# Each typed prompt is filled with form-aware author instructions:
#
#   FOOTNOTE form     — author block opens as "Firstname Lastname,"
#   BIBLIOGRAPHY form — author block opens as "Lastname, Firstname."
#
# These are structurally distinct conventions. NEVER share an author
# instruction between them — the 1.6B model will conflate orders.

_SYSTEM_EXTRACT_TYPED_TEMPLATE = (
    "Extract fields from a {type} citation. Output only JSON. "
    "Copy fields verbatim from the text. Use null for absent fields. "
    "Never paraphrase."
)


def system_for_type(citation_type: CitationType) -> str:
    """Spec §6 Call 2 system message — type-specific."""
    return _SYSTEM_EXTRACT_TYPED_TEMPLATE.format(type=citation_type.value)


# Minimal per-type templates. Each is just: {segment} + Reply line showing
# the field shape. No bullets, no per-field instructions, no "this is a X
# citation" assertions, no author-order rules. The 1.6B model returns what
# it sees in the order it sees; downstream parses the name strings.
#
# Conventions in Reply line:
#   "..."     → fill with the literal text from the segment if present
#   [...]     → fill with a list of strings (each name as it appears)
#   NNNN      → fill with a 4-digit year
#   null      → field is usually absent; leave null unless clearly present
#
# Authors / editors / curators / interviewee / interviewer come back as
# strings (or lists of strings) — the verifier handles surname extraction.


_BOOK_TEMPLATE = """\
{segment}

Reply: {{"authors":[...],"title":"...","subtitle":null,"translator":null,"editor":null,"edition":null,"publisher":"...","city":null,"year":NNNN,"pages":null,"url":null,"doi":null}}"""


_BOOK_CHAPTER_TEMPLATE = """\
{segment}

Reply: {{"authors":[...],"chapter_title":"...","book_title":"...","editors":[...],"translator":null,"publisher":"...","city":null,"year":NNNN,"pages":"..."}}"""


_JOURNAL_ARTICLE_TEMPLATE = """\
{segment}

Reply: {{"authors":[...],"article_title":"...","journal":"...","volume":"...","issue":"...","year":NNNN,"pages":"...","doi":null,"url":null}}"""


_MAGAZINE_ARTICLE_TEMPLATE = """\
{segment}

Reply: {{"authors":[...],"article_title":"...","magazine":"...","date":"...","pages":null,"url":null}}"""


_NEWSPAPER_ARTICLE_TEMPLATE = """\
{segment}

Reply: {{"authors":[...],"article_title":"...","newspaper":"...","date":"...","section":null,"page":null,"url":null}}"""


_WEB_SOURCE_TEMPLATE = """\
{segment}

Reply: {{"authors":[...],"title":"...","site_name":"...","publisher_or_org":null,"date_published":null,"accessed_date":null,"url":"..."}}"""


_EXHIBITION_CATALOG_TEMPLATE = """\
{segment}

Reply: {{"title":"...","editors":[...],"curators":[...],"venue":"...","city":"...","publisher":null,"year":NNNN,"exhibition_dates":null}}"""


_INTERVIEW_TEMPLATE = """\
{segment}

Reply: {{"interviewee":"...","interviewer":"...","title_or_program":null,"publication_or_network":null,"date":"...","medium":null,"url":null}}"""


_THESIS_DISSERTATION_TEMPLATE = """\
{segment}

Reply: {{"authors":[...],"title":"...","degree_type":"...","institution":"...","year":NNNN,"url":null}}"""


_OTHER_TEMPLATE = """\
{segment}

Reply: {{"authors":[...],"title":null,"raw_text":"...","notes":"..."}}"""


# Per-type prefill — opens the JSON at the first field of each Reply
# template. VL continues from the prefill onward.
_PREFILL_SUFFIX: dict[CitationType, str] = {
    CitationType.BOOK: '"authors":',
    CitationType.BOOK_CHAPTER: '"authors":',
    CitationType.JOURNAL_ARTICLE: '"authors":',
    CitationType.MAGAZINE_ARTICLE: '"authors":',
    CitationType.NEWSPAPER_ARTICLE: '"authors":',
    CitationType.WEB_SOURCE: '"authors":',
    CitationType.EXHIBITION_CATALOG: '"title":',
    CitationType.INTERVIEW: '"interviewee":',
    CitationType.THESIS_DISSERTATION: '"authors":',
    CitationType.OTHER: '"authors":',
}


_TEMPLATES: dict[CitationType, str] = {
    CitationType.BOOK: _BOOK_TEMPLATE,
    CitationType.BOOK_CHAPTER: _BOOK_CHAPTER_TEMPLATE,
    CitationType.JOURNAL_ARTICLE: _JOURNAL_ARTICLE_TEMPLATE,
    CitationType.MAGAZINE_ARTICLE: _MAGAZINE_ARTICLE_TEMPLATE,
    CitationType.NEWSPAPER_ARTICLE: _NEWSPAPER_ARTICLE_TEMPLATE,
    CitationType.WEB_SOURCE: _WEB_SOURCE_TEMPLATE,
    CitationType.EXHIBITION_CATALOG: _EXHIBITION_CATALOG_TEMPLATE,
    CitationType.INTERVIEW: _INTERVIEW_TEMPLATE,
    CitationType.THESIS_DISSERTATION: _THESIS_DISSERTATION_TEMPLATE,
    CitationType.OTHER: _OTHER_TEMPLATE,
}


def prompt_for_type(
    citation_type: CitationType,
    segment_text: str,
    style: CitationStyle = CitationStyle.UNKNOWN,
    form: str = "footnote",
) -> tuple[str, str, int]:
    """Return (user_prompt, prefill, max_tokens).

    Minimal: the user prompt is just the segment + the Reply schema line.
    No instructions about name order, no per-field bullets, no style
    assertions. VL emits what it sees; downstream parses.
    """
    # Per O-18: `form` is NOT passed to the VL prompt. The 1.6B model
    # returns names as it sees them; downstream parsing handles name order.
    # `style` is also not used in the prompt — it's for downstream scoring.
    _ = (style, form)
    template = _TEMPLATES.get(citation_type)
    if template is None:
        template = _OTHER_TEMPLATE
        citation_type = CitationType.OTHER
    prefill = '{' + _PREFILL_SUFFIX.get(citation_type, '"authors":')
    user = template.format(segment=segment_text[:800])
    return user, prefill, 600
