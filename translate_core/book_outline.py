"""Editor-side book outline + paragraph segmentation primitives.

``TOCEntry``, ``BookOutline`` and ``split_paragraphs`` are imported by
``import_book.py`` for editor-side document preparation. This module is
the source-of-truth for these names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ----------------------------------------------------------------------
# Outline dataclasses
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
# Paragraph segmentation
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
        result.extend(split_long_paragraph(p, max_chars))
    return result


def split_long_paragraph(p: str, max_chars: int) -> list[str]:
    """Split a single paragraph at sentence boundaries.

    Returns ``[p]`` when the paragraph fits within *max_chars*; otherwise
    chunks at sentence boundaries.  A single sentence longer than
    *max_chars* is kept whole (never mid-sentence).
    """
    if len(p) <= max_chars:
        return [p]
    sentences = _SENTENCE_BOUNDARY_RE.split(p)
    chunks: list[str] = []
    chunk = ""
    for s in sentences:
        candidate = f"{chunk} {s}".strip() if chunk else s
        if len(candidate) > max_chars and chunk:
            chunks.append(chunk)
            chunk = s
        else:
            chunk = candidate
    if chunk:
        chunks.append(chunk)
    return chunks


def resegment_pending(ws: dict, max_chars: int) -> dict:
    """Split oversized untouched segments in a project dict, in place.

    A segment qualifies for splitting only when:
      - ``status != "done"`` AND
      - ``target`` is empty or whitespace AND
      - ``len(source) > max_chars``

    Children copy all parent keys; ``source`` is the chunk from
    :func:`split_long_paragraph`, ``target=""``, ``status="pending"``.
    ``docx_para_idx`` (when present) is copied to every child.
    Segment ids are renumbered sequentially (ids ARE array indexes).
    ``active_index`` is remapped to the first child of the formerly active segment.
    ``segments_meta`` is expanded in lockstep (duplicated per child) when
    present and aligned; dropped on mismatch.
    ``total`` is updated; ``done`` count is unchanged by construction.

    Returns ``{"before": n, "after": n, "split": 0}`` when nothing qualifies.
    """
    old_segments: list[dict] = ws.get("segments", [])
    if not old_segments:
        return {"before": 0, "after": 0, "split": 0}

    new_segments: list[dict] = []
    old_to_new_first: dict[int, int] = {}  # old index → new first index
    split_count = 0

    for idx, seg in enumerate(old_segments):
        source = seg.get("source", "")
        is_done = seg.get("status") == "done"
        has_target = bool(seg.get("target", "").strip())
        can_split = (not is_done) and (not has_target) and len(source) > max_chars

        if can_split:
            chunks = split_long_paragraph(source, max_chars)
            if len(chunks) <= 1:
                # Single huge sentence — keep as-is
                old_to_new_first[idx] = len(new_segments)
                new_segments.append(seg)
            else:
                split_count += 1
                for chunk in chunks:
                    child = dict(seg)  # copy all keys
                    child["source"] = chunk
                    child["target"] = ""
                    child["status"] = "pending"
                    # docx_para_idx is already copied via dict(seg)
                    old_to_new_first[idx] = old_to_new_first.get(idx, len(new_segments))
                    new_segments.append(child)
        else:
            old_to_new_first[idx] = len(new_segments)
            new_segments.append(seg)

    if split_count == 0:
        # Even if nothing was split, clean up mismatched meta.
        if "segments_meta" in ws and ws["segments_meta"] is not None:
            if len(ws["segments_meta"]) != len(old_segments):
                del ws["segments_meta"]
        return {"before": len(old_segments), "after": len(old_segments), "split": 0}

    # Renumber ids (ids ARE array indexes)
    for new_idx, seg in enumerate(new_segments):
        seg["id"] = new_idx

    # Remap active_index
    old_active = ws.get("active_index", 0)
    ws["active_index"] = old_to_new_first.get(old_active, 0)

    # Handle segments_meta parallel array
    meta = ws.get("segments_meta")
    if meta is not None:
        if len(meta) == len(old_segments):
            new_meta: list[dict] = []
            for idx, seg in enumerate(old_segments):
                source = seg.get("source", "")
                is_done = seg.get("status") == "done"
                has_target = bool(seg.get("target", "").strip())
                can_split = (not is_done) and (not has_target) and len(source) > max_chars
                if can_split:
                    chunks = split_long_paragraph(source, max_chars)
                    for _ in chunks:
                        new_meta.append(dict(meta[idx]))
                else:
                    new_meta.append(dict(meta[idx]))
            ws["segments_meta"] = new_meta
        else:
            del ws["segments_meta"]

    ws["segments"] = new_segments
    ws["total"] = len(new_segments)
    # done count unchanged — we only split pending segments with no target

    return {"before": len(old_segments), "after": len(new_segments), "split": split_count}


__all__ = ["TOCEntry", "BookOutline", "split_paragraphs", "split_long_paragraph", "resegment_pending"]
