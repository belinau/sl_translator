# translate_core/docx_format_capture.py
#
# Capture a per-paragraph formatting manifest from a DOCX so the academic
# DOCX pipeline can reconstruct original paragraph grouping on export — the
# DOCX analogue of pdf_format_capture.capture_paragraph_manifest.
#
# The manifest is purely additive metadata. It NEVER re-splits or re-merges
# the translator's segments; it only records, per DOCX body paragraph, the
# formatting signals (heading level, alignment, indent, block-quote) and a
# stable ``pdf_para_idx`` that segments sharing one source paragraph carry
# so the export groups them back into a single DOCX paragraph.
#
# ``docx_to_markdown`` emits one markdown line per non-empty body paragraph
# (blank-line separated), so segments are 1:1 with source paragraphs except
# for ``split_long_paragraph`` sentence splits — which the shared
# ``pdf_para_idx`` rejoins at export.

from __future__ import annotations

import logging
import re
from typing import Any

from translate_core.pdf_format_capture import FormatManifest, ParagraphRecord

log = logging.getLogger(__name__)

try:
    import docx as _docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    HAS_DOCX = True
except ImportError:
    _docx = None  # type: ignore[assignment]
    WD_ALIGN_PARAGRAPH = None  # type: ignore[assignment]
    HAS_DOCX = False


_ALIGN_MAP = {
    "LEFT": "left",
    "CENTER": "center",
    "RIGHT": "right",
    "JUSTIFY": "justify",
}

_HEADING_STYLE_RE = re.compile(r"^heading\s*([1-6])$", re.IGNORECASE)


def _heading_level_from_style(style_name: str | None) -> int:
    if not style_name:
        return 0
    m = _HEADING_STYLE_RE.match(style_name.strip())
    if m:
        return int(m.group(1))
    return 0


def capture_docx_paragraph_manifest(docx_path: str | Any) -> FormatManifest:
    """Read a DOCX and return a per-paragraph formatting manifest.

    Walks the body-level paragraphs in document order (the same order
    ``docx_to_markdown`` emits), one :class:`ParagraphRecord` per non-empty
    paragraph. Footnote/endnote text lives in separate parts and is not a
    body paragraph, so it is intentionally absent — matching
    ``docx_to_markdown`` which appends ``[^N]:`` defs after the body.

    ``pdf_para_idx`` is a running counter over the non-empty body paragraphs
    so segments derived from one source paragraph share the same value.
    """
    if not HAS_DOCX:
        log.warning("docx_format_capture: python-docx not installed — empty manifest")
        return FormatManifest()
    assert _docx is not None  # guarded by HAS_DOCX

    doc = _docx.Document(str(docx_path))
    paragraphs: list[ParagraphRecord] = []
    para_idx = 0
    all_indents: list[float] = []

    for para in doc.paragraphs:
        text = (para.text or "").strip()
        if not text:
            continue

        style_name = ""
        try:
            style_name = para.style.name if para.style is not None else ""
        except Exception:
            style_name = ""

        heading_level = _heading_level_from_style(style_name)

        align = "left"
        alignment = para.alignment
        if alignment is not None and WD_ALIGN_PARAGRAPH is not None:
            key = getattr(alignment, "name", str(alignment)).upper()
            align = _ALIGN_MAP.get(key, "left")

        para_indent_in = 0.0
        try:
            li = para.paragraph_format.left_indent
            if li is not None:
                para_indent_in = float(li.inches)
        except Exception:
            para_indent_in = 0.0
        all_indents.append(para_indent_in)

        is_blockquote = False
        if heading_level == 0:
            sn = style_name.lower() if style_name else ""
            if "quote" in sn or "block text" in sn or "citation" in sn:
                is_blockquote = True

        paragraphs.append(ParagraphRecord(
            pdf_para_idx=para_idx,
            text=text,
            font_size=0.0,          # DOCX uses heading styles, not font-ratio detection
            heading_level=heading_level,
            para_align=align,
            para_indent_in=para_indent_in,
            is_blockquote=is_blockquote,
        ))
        para_idx += 1

    body_indent = _modal_float(all_indents) if all_indents else 0.0
    # Promote paragraphs indented well beyond the body baseline to block quotes
    # (only where no explicit style signalled it).
    if body_indent >= 0:
        for i, p in enumerate(paragraphs):
            if (
                p.heading_level == 0
                and not p.is_blockquote
                and p.para_indent_in >= (body_indent + 0.5)
                and p.para_align == "left"
            ):
                paragraphs[i] = ParagraphRecord(
                    pdf_para_idx=p.pdf_para_idx,
                    text=p.text,
                    font_size=p.font_size,
                    heading_level=0,
                    para_align=p.para_align,
                    para_indent_in=p.para_indent_in,
                    is_blockquote=True,
                )

    return FormatManifest(
        paragraphs=paragraphs,
        body_font_size=0.0,
        body_font_name="",
        body_indent_in=body_indent,
        pdf_page_count=0,
    )


def _modal_float(values: list[float]) -> float:
    """Modal value rounded to 2 decimals (body indent baseline)."""
    if not values:
        return 0.0
    buckets: dict[float, int] = {}
    for v in values:
        key = round(v, 2)
        buckets[key] = buckets.get(key, 0) + 1
    return max(buckets, key=lambda k: buckets[k])