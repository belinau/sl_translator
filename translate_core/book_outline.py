"""Editor-side book outline + paragraph segmentation primitives.

These symbols (``TOCEntry``, ``BookOutline`` and ``split_paragraphs``) are
imported by ``import_book.py`` (editor-side document preparation, OUT OF SCOPE
for the parsing-simplification work in
``docs/superpowers/plans/2026-06-06-parsing-simplification.md``).

They were extracted verbatim from ``translate_core/vl_parser.py``, which Phase
7 of the parsing-simplification plan will delete. From Phase 2 forward this
module is the source-of-truth for these names; the still-extant
``vl_parser.py`` definitions are dead-code-walking pending that deletion.

This module is intentionally self-contained: it imports nothing from
``translate_core.vl_parser`` so that it survives ``vl_parser.py``'s removal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ----------------------------------------------------------------------
# Outline dataclasses (copied verbatim from vl_parser.py:108-142)
# ----------------------------------------------------------------------


@dataclass
class TOCEntry:
    """One entry from the table of contents."""

    level: int
    kind: str  # part|chapter|section|subsection|front_matter|back_matter|untitled
    number: str  # printed numbering ("3", "III", "3.2", "a)", "")
    title: str  # exact title, leader dots stripped
    page_number: str  # printed page number, or "" if absent
    source_page: int = -1  # PDF page index where this entry appeared


@dataclass
class BookOutline:
    """Resolved table of contents."""

    entries: list[TOCEntry] = field(default_factory=list)
    page_to_chapter: dict[str, int] = field(default_factory=dict)
    reconciliation_warnings: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# Paragraph segmentation (copied verbatim from vl_parser.py:512-594)
# ----------------------------------------------------------------------

# Goal: translator-editable segments that never contain half-sentences.
#
# Rules:
#   1. Blank lines = paragraph break.
#   2. Markdown structural lines (#, >, -, *, N., [^N]:) = their own segment.
#   3. Otherwise: ALL consecutive non-blank lines join into one paragraph —
#      PDF line wraps are never segment boundaries.
#   4. Hyphenated word-splits across lines ("iden-\nities") repair to "identities".
#   5. Paragraphs longer than max_chars split ONLY at sentence boundaries
#      (period/!/? followed by space + capital). Never mid-sentence.

_MD_STRUCTURAL_LINE_RE = re.compile(
    r"^\s*(?:#+\s|>\s|[-*+]\s|\d+[.)]\s|\[\^\w+\]:)"
)
_MULTI_SPACE_RE = re.compile(r"  +")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“‘«¿¡])")


def _flush_buffer(buf: list[str], out: list[str]) -> None:
    """Join a buffer of non-structural lines into one paragraph, repairing
    hyphenated word-splits at line boundaries. Push onto out, clear buf."""
    if not buf:
        return
    joined = ""
    for raw in buf:
        s = raw.strip()
        if not s:
            continue
        if joined.endswith("-") and joined[-2:-1].isalpha() and s[:1].isalpha():
            # "exaggerat-" + "ing" → "exaggerating"
            joined = joined[:-1] + s
        elif joined:
            joined += " " + s
        else:
            joined = s
    joined = _MULTI_SPACE_RE.sub(" ", joined).strip()
    if joined:
        out.append(joined)
    buf.clear()


def split_paragraphs(text: str, max_chars: int = 1500) -> list[str]:
    """Split markdown into translator-sized segments without ever breaking
    a sentence. See module-level rules comment for the segmentation policy."""
    if not text or not text.strip():
        return []

    paragraphs: list[str] = []
    buffer: list[str] = []

    for line in text.split("\n"):
        if not line.strip():
            _flush_buffer(buffer, paragraphs)
            continue
        if _MD_STRUCTURAL_LINE_RE.match(line):
            # New structural element. Close out the previous one, then
            # start this one in the buffer — continuation lines (no
            # marker) will fold into it.
            _flush_buffer(buffer, paragraphs)
            buffer.append(line)
            continue
        buffer.append(line)
    _flush_buffer(buffer, paragraphs)

    # Split overlong paragraphs at sentence boundaries only.
    result: list[str] = []
    for p in paragraphs:
        if len(p) <= max_chars:
            result.append(p)
            continue
        sentences = _SENTENCE_BOUNDARY_RE.split(p)
        chunk = ""
        for s in sentences:
            candidate = f"{chunk} {s}".strip() if chunk else s
            if len(candidate) > max_chars and chunk:
                result.append(chunk)
                chunk = s
            else:
                chunk = candidate
        if chunk:
            result.append(chunk)
    return result


__all__ = ["TOCEntry", "BookOutline", "split_paragraphs"]
