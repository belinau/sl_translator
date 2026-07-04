# translate_core/pdf_format_capture.py
#
# Capture a per-paragraph formatting manifest from a PDF so the DOCX export
# can reconstruct the original paragraph grouping, heading levels, and
# paragraph alignment — without reproducing page breaks or exact fonts.
#
# The manifest is purely additive metadata. It NEVER re-splits or re-merges
# the translator's segments; it only records, per PDF paragraph unit, the
# formatting signals that were lost when fitz get_text() collapsed to plain
# text. The alignment step (attach_manifest_to_segments) matches manifest
# records to existing segments by normalized-text containment — the same
# pattern relink_docx_para_idx uses for DOCX.

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    fitz = None  # type: ignore[assignment]
    HAS_FITZ = False


# --------------------------------------------------------------------------
# Manifest dataclass
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ParagraphRecord:
    """One PDF paragraph unit with the formatting signals export needs.

    pdf_para_idx is a stable running counter across the whole document so
    segments that belong to the same PDF paragraph share the same value
    (and thus group together at export).
    """

    pdf_para_idx: int
    text: str
    font_size: float = 0.0
    heading_level: int = 0        # 0 = body, 1 = H1, 2 = H2, ...
    para_align: str = "left"     # left | center | right | justify
    para_indent_in: float = 0.0  # left indent relative to page margin
    is_blockquote: bool = False


@dataclass
class FormatManifest:
    """Document-level manifest: per-paragraph records + body-style baseline."""

    paragraphs: list[ParagraphRecord] = field(default_factory=list)
    body_font_size: float = 0.0     # dominant (modal) font size → body baseline
    body_font_name: str = ""        # dominant font name (informational only)
    body_indent_in: float = 0.0      # modal left indent of body paragraphs
    pdf_page_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "paragraphs": [
                {
                    "pdf_para_idx": p.pdf_para_idx,
                    "text": p.text,
                    "font_size": p.font_size,
                    "heading_level": p.heading_level,
                    "para_align": p.para_align,
                    "para_indent_in": p.para_indent_in,
                    "is_blockquote": p.is_blockquote,
                }
                for p in self.paragraphs
            ],
            "body_font_size": self.body_font_size,
            "body_font_name": self.body_font_name,
            "body_indent_in": self.body_indent_in,
            "pdf_page_count": self.pdf_page_count,
        }


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------


def capture_paragraph_manifest(pdf_path: str | Any) -> FormatManifest:
    """Read a PDF and return a per-paragraph formatting manifest.

    Uses fitz page.get_text("dict") so we get font name/size per span and
    block bounding boxes for alignment/indent. Text is joined per block in
    reading order; blocks are the closest fitz analogue to a "paragraph".

    No page breaks are recorded (per project decision: the target DOCX
    reflows naturally; only paragraph boundaries matter).
    """
    if not HAS_FITZ:
        log.warning("pdf_format_capture: PyMuPDF (fitz) not installed — empty manifest")
        return FormatManifest()

    assert fitz is not None  # guarded by HAS_FITZ
    doc = fitz.open(str(pdf_path))
    page_count = len(doc)
    paragraphs: list[ParagraphRecord] = []
    all_font_sizes: list[float] = []
    all_font_names: list[str] = []
    all_indents: list[float] = []
    para_idx = 0

    for page in doc:
        page_dict: dict = page.get_text("dict")  # type: ignore[assignment]
        # page rect for indent calculation
        page_rect = page.rect
        page_left = page_rect.x0

        for block in page_dict.get("blocks", []):
            if not isinstance(block, dict):
                continue
            if block.get("type", 0) != 0:
                # skip image blocks
                continue
            block_lines = block.get("lines", [])
            if not block_lines:
                continue

            # Join all line spans in this block into one text unit.
            block_text_parts: list[str] = []
            block_sizes: list[float] = []
            block_fonts: list[str] = []
            for line in block_lines:
                if not isinstance(line, dict):
                    continue
                for span in line.get("spans", []):
                    if not isinstance(span, dict):
                        continue
                    txt = span.get("text", "")
                    if not txt:
                        continue
                    block_text_parts.append(txt)
                    size = float(span.get("size", 0.0))
                    if size > 0:
                        block_sizes.append(size)
                    font = span.get("font", "")
                    if font:
                        all_font_names.append(font)
                    block_fonts.append(font)
                # fitz does not insert a line break inside a block; the
                # reflow stage joins wrapped lines, so we join with space
                # and let the segment aligner match on normalized text.
                block_text_parts.append(" ")
            block_text = "".join(block_text_parts).strip()
            if not block_text:
                continue

            # Dominant font size for this block (modal)
            if block_sizes:
                block_size = _modal(block_sizes)
                all_font_sizes.append(block_size)
            else:
                block_size = 0.0

            # Block bbox for alignment + indent
            bbox = block.get("bbox", (0, 0, 0, 0))
            block_left = bbox[0]
            block_right = bbox[2]


            # Indent relative to page left edge (inches; fitz uses points)
            para_indent_in = max(0.0, (block_left - page_left) / 72.0)
            all_indents.append(para_indent_in)

            # Alignment heuristic from bbox position vs page width
            para_align = _infer_align(block_left, block_right, page_left, page_rect.x1)

            paragraphs.append(ParagraphRecord(
                pdf_para_idx=para_idx,
                text=block_text,
                font_size=block_size,
                heading_level=0,  # set after body baseline is known
                para_align=para_align,
                para_indent_in=para_indent_in,
                is_blockquote=False,
            ))
            para_idx += 1

    doc.close()

    # Body baseline: modal font size + modal indent across all blocks.
    # The modal indent is the body paragraph's typical left margin; a
    # blockquote is a paragraph indented *beyond* that baseline.
    body_size = _modal(all_font_sizes) if all_font_sizes else 0.0
    body_font = _modal_str(all_font_names) if all_font_names else ""
    body_indent = _modal(all_indents) if all_indents else 0.0

    # Heading detection: font size relative to body
    for p in paragraphs:
        if body_size <= 0 or p.font_size <= 0:
            continue
        ratio = p.font_size / body_size
        if ratio >= 1.4:
            p_new = ParagraphRecord(
                pdf_para_idx=p.pdf_para_idx,
                text=p.text,
                font_size=p.font_size,
                heading_level=1,
                para_align=p.para_align,
                para_indent_in=p.para_indent_in,
                is_blockquote=False,
            )
            paragraphs[p.pdf_para_idx] = p_new
        elif ratio >= 1.2:
            p_new = ParagraphRecord(
                pdf_para_idx=p.pdf_para_idx,
                text=p.text,
                font_size=p.font_size,
                heading_level=2,
                para_align=p.para_align,
                para_indent_in=p.para_indent_in,
                is_blockquote=False,
            )
            paragraphs[p.pdf_para_idx] = p_new
        elif p.para_indent_in >= (body_indent + 0.5) and p.para_align == "left":
            # Block quote heuristic: indented *beyond* the body's typical
            # left margin (not just indented from the page edge — the body
            # itself sits at body_indent). 0.5″ extra is a meaningful inset.
            p_new = ParagraphRecord(
                pdf_para_idx=p.pdf_para_idx,
                text=p.text,
                font_size=p.font_size,
                heading_level=0,
                para_align=p.para_align,
                para_indent_in=p.para_indent_in,
                is_blockquote=True,
            )
            paragraphs[p.pdf_para_idx] = p_new

    return FormatManifest(
        paragraphs=paragraphs,
        body_font_size=body_size,
        body_font_name=body_font,
        body_indent_in=body_indent,
        pdf_page_count=page_count,
    )


# --------------------------------------------------------------------------
# Alignment to segments (additive — never re-splits)
# --------------------------------------------------------------------------


_WS_RE = re.compile(r"\s+")
# Hyphenated line-break: "fore-\ncloses" → "forecloses". Matches the repair
# _reflow_pdf_text performs on segments, so the stream and segment texts use
# the same convention and substring search succeeds.
_HYPHEN_JOIN_RE = re.compile(r"([a-zA-Z])-\s+([a-zA-Z])")
# Footnote markers: [^N] (renumber output) and bare trailing digits after
# terminal punctuation (PyMuPDF superscript artifact). _renumber_footnotes
# rewrites bare digits to [^N], so the segment text diverges from the PDF
# block text; stripping both lets alignment compare prose only.
_FN_MARKER_RE = re.compile(r"\[\^\d{1,3}\]|(?<=[.!?:;\"\u201d\u2019\u00bb])\d{1,3}(?=\s|$)")


def _normalize(text: str) -> str:
    """Normalize text for containment matching (same rule as relink_docx_para_idx)."""
    return _WS_RE.sub(" ", text.replace("\xa0", " ")).strip()


def _normalize_strong(text: str) -> str:
    """Stronger normalization for stream alignment: repairs hyphenated
    line-breaks and strips footnote markers so the manifest stream matches
    reflow+renumber-repaired segment text."""
    t = text.replace("\xa0", " ")
    t = _HYPHEN_JOIN_RE.sub(r"\1\2", t)   # "fore-\ncloses" → "forecloses"
    t = _FN_MARKER_RE.sub(" ", t)          # drop [^N] / bare superscript digits
    return _WS_RE.sub(" ", t).strip()

def attach_manifest_to_segments(
    segments: list[dict],
    manifest: FormatManifest,
) -> int:
    """Attach manifest fields to existing segments by stream alignment.

    Builds a single normalized text stream from the manifest blocks (in
    order), records the byte offset where each block starts, then for each
    segment finds where its normalized source text falls in the stream. The
    segment is attributed to the block where it *starts* (the block whose
    offset range contains the start of the segment text).

    This handles: (a) segments that are sub-strings of one block, (b) segments
    that span several blocks (joined by split_paragraphs), (c) sentence-chunks
    that start mid-block (split_long_paragraph). It does NOT depend on 1:1
    block↔segment correspondence.

    NEVER modifies source/target/status/id. Returns the number of segments
    matched.
    """
    if not manifest.paragraphs:
        return 0

    # Build the normalized stream + per-block start offsets.
    para_texts = [_normalize_strong(p.text) for p in manifest.paragraphs]
    # (start_char_offset, block_idx) for each non-empty block.
    block_offsets: list[tuple[int, int]] = []
    stream_parts: list[str] = []
    pos = 0
    for bi, pt in enumerate(para_texts):
        if not pt:
            continue
        block_offsets.append((pos, bi))
        stream_parts.append(pt)
        pos += len(pt) + 1  # +1 for the join space
    stream = " ".join(stream_parts)

    matched = 0
    search_cursor = 0  # char offset in stream to start searching from

    for seg in segments:
        if "pdf_para_idx" in seg and seg["pdf_para_idx"] is not None:
            matched += 1  # idempotent
            continue
        src = _normalize_strong(seg.get("source", ""))
        if not src:
            continue

        # Find the segment's source text in the stream, searching forward
        # from the current cursor. Use a prefix probe first (the first ~60
        # chars are usually distinctive enough), then the full text, then a
        # shorter probe — handles reflow-induced spacing/hyphen differences.
        found_at = -1
        probe = src[:60]
        if probe:
            found_at = stream.find(probe, search_cursor)
        if found_at < 0 and len(src) > 10:
            found_at = stream.find(src, search_cursor)
        if found_at < 0 and len(src) >= 30:
            found_at = stream.find(src[:30])

        if found_at < 0:
            continue

        # Map found_at → block index (the block whose [start, next_start)
        # range contains found_at).
        bi = _block_at(block_offsets, found_at)
        if bi < 0:
            continue
        _attach(seg, manifest.paragraphs[bi])
        # Advance the search cursor past the end of this segment's match so
        # the next segment is found after it (preserves document order).
        search_cursor = found_at + len(src)
        matched += 1
    return matched


def _block_at(
    block_offsets: list[tuple[int, int]],
    char_pos: int,
) -> int:
    """Return the block_idx whose [start, next_start) range contains char_pos."""
    import bisect
    starts = [off for off, _ in block_offsets]
    idx = bisect.bisect_right(starts, char_pos) - 1
    if 0 <= idx < len(block_offsets):
        return block_offsets[idx][1]
    return -1


def _attach(seg: dict, rec: ParagraphRecord) -> None:
    """Attach manifest fields to a segment (additive, never overwrites id/source/target/status)."""
    seg["pdf_para_idx"] = rec.pdf_para_idx
    seg["heading_level"] = rec.heading_level
    seg["para_align"] = rec.para_align
    seg["para_indent_in"] = rec.para_indent_in
    seg["is_blockquote"] = rec.is_blockquote




# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _modal(values: list[float]) -> float:
    """Return the modal value (most frequent), rounded to 1 decimal."""
    if not values:
        return 0.0
    from collections import Counter
    rounded = [round(v, 1) for v in values]
    return Counter(rounded).most_common(1)[0][0]


def _modal_str(values: list[str]) -> str:
    if not values:
        return ""
    from collections import Counter
    return Counter(values).most_common(1)[0][0]


def _infer_align(
    block_left: float, block_right: float,
    page_left: float, page_right: float,
) -> str:
    """Infer paragraph alignment from block bbox vs page rect."""
    page_width = page_right - page_left
    if page_width <= 0:
        return "left"
    left_gap = block_left - page_left
    right_gap = page_right - page_right
    # Centered: roughly equal left/right gaps, narrow block
    if abs(left_gap - right_gap) < page_width * 0.05 and left_gap > page_width * 0.1:
        return "center"
    # Right-aligned: large left gap, small/negative right gap
    if left_gap > page_width * 0.2 and right_gap < page_width * 0.05:
        return "right"
    # Justified: block nearly spans page width
    if (page_width - (block_right - block_left)) < page_width * 0.1:
        return "justify"
    return "left"


__all__ = [
    "ParagraphRecord",
    "FormatManifest",
    "capture_paragraph_manifest",
    "attach_manifest_to_segments",
]