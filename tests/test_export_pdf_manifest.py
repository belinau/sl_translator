# tests/test_export_pdf_manifest.py
#
# Tests for compile_to_designed_docx with the paragraph manifest path.
# Verifies Maska typography (TNR 12pt, 1.5 line spacing, A4, 1" margins,
# no space between paragraphs), heading reconstruction, and paragraph
# grouping from pdf_para_idx.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

docx = pytest.importorskip("docx", reason="python-docx required for export tests")

from translate_core.doc_parser import DocumentParser


class TestExportMaskaTypography:
    """The manifest-path export applies Maska house typography."""

    def test_maska_font_size_and_spacing(self, tmp_path):
        segments = [
            {"id": 0, "source": "First paragraph of body text.", "target": "Prvi odstavek.", "status": "done", "pdf_para_idx": 0},
            {"id": 1, "source": "Second paragraph.", "target": "Drugi odstavek.", "status": "done", "pdf_para_idx": 1},
        ]
        md = "\n\n".join(s["target"] for s in segments)
        out = tmp_path / "out.docx"
        DocumentParser().compile_to_designed_docx(md, out, segments=segments)

        doc = docx.Document(str(out))
        sn = doc.styles["Normal"]
        assert sn.font.name == "Times New Roman"
        assert sn.font.size.pt == 12
        assert sn.paragraph_format.line_spacing == 1.5
        # Maska SLOG ODSTAVKA: no space between paragraphs
        assert sn.paragraph_format.space_after.pt == 0

    def test_maska_a4_page_and_one_inch_margins(self, tmp_path):
        segments = [
            {"id": 0, "source": "Body.", "target": "Telo.", "status": "done", "pdf_para_idx": 0},
        ]
        out = tmp_path / "out.docx"
        DocumentParser().compile_to_designed_docx("\n\nBody.", out, segments=segments)
        doc = docx.Document(str(out))
        sec = doc.sections[0]
        assert sec.top_margin.inches == 1.0
        assert sec.bottom_margin.inches == 1.0
        assert sec.left_margin.inches == 1.0
        assert sec.right_margin.inches == 1.0
        # A4: 8.27 x 11.69 inches
        assert abs(sec.page_width.inches - 8.27) < 0.05
        assert abs(sec.page_height.inches - 11.69) < 0.05

    def test_heading_from_manifest_renders_bold(self, tmp_path):
        segments = [
            {"id": 0, "source": "Chapter Title", "target": "Naslov poglavja", "status": "done",
             "pdf_para_idx": 0, "heading_level": 1},
            {"id": 1, "source": "Body text here.", "target": "Besedilo.", "status": "done",
             "pdf_para_idx": 1},
        ]
        md = "\n\n".join(s["target"] for s in segments)
        out = tmp_path / "out.docx"
        DocumentParser().compile_to_designed_docx(md, out, segments=segments)
        doc = docx.Document(str(out))
        # The first non-empty paragraph should be the heading (bold, centered)
        heading_para = None
        for p in doc.paragraphs:
            if p.text.strip() == "Naslov poglavja":
                heading_para = p
                break
        assert heading_para is not None, "Heading paragraph not found"
        assert heading_para.runs[0].bold is True
        assert heading_para.alignment is not None  # CENTER

    def test_segments_sharing_para_idx_join_into_one_paragraph(self, tmp_path):
        """Two segments with the same pdf_para_idx become ONE DOCX paragraph."""
        segments = [
            {"id": 0, "source": "First sentence. ", "target": "Prvi stavek. ", "status": "done", "pdf_para_idx": 0},
            {"id": 1, "source": "Second sentence.", "target": "Drugi stavek.", "status": "done", "pdf_para_idx": 0},
            {"id": 2, "source": "Different paragraph.", "target": "Drugi odstavek.", "status": "done", "pdf_para_idx": 1},
        ]
        md = "\n\n".join(s["target"] for s in segments)
        out = tmp_path / "out.docx"
        DocumentParser().compile_to_designed_docx(md, out, segments=segments)
        doc = docx.Document(str(out))
        # Should have 2 content paragraphs (not 3): the first two joined
        content_paras = [p for p in doc.paragraphs if p.text.strip()]
        assert len(content_paras) == 2
        assert "Prvi stavek." in content_paras[0].text
        assert "Drugi stavek." in content_paras[0].text
        assert content_paras[1].text.strip() == "Drugi odstavek."

    def test_legacy_md_text_path_without_manifest_unchanged(self, tmp_path):
        """When no segments/manifest given, the legacy Georgia/11pt path runs."""
        out = tmp_path / "legacy.docx"
        DocumentParser().compile_to_designed_docx("# Title\n\nBody text.", out)
        doc = docx.Document(str(out))
        sn = doc.styles["Normal"]
        # Legacy: Georgia 11pt, 1.25 line spacing, 1.2" margins
        assert sn.font.name == "Georgia"
        assert sn.font.size.pt == 11
        assert sn.paragraph_format.line_spacing == 1.25

    def test_custom_house_typography_is_consumed(self, tmp_path):
        """A non-Maska profile passed via house_typography is applied —
        proving the export consumes the profile, not a hardcoded style."""
        custom_typo = {
            "label": "Test Publisher",
            "body_font": "Arial",
            "body_size_pt": 11,
            "line_spacing": 1.0,
            "page_size": "Letter",
            "margins_in": 1.5,
            "space_after_pt": 6,
            "footnote_size_pt": 9,
            "footnote_line_spacing": 1.0,
            "blockquote_size_pt": 10,
            "blockquote_line_spacing": 1.0,
            "blockquote_indent_in": 0.4,
            "h1_size_pt": 16,
            "h2_size_pt": 14,
        }
        segments = [
            {"id": 0, "source": "Body.", "target": "Telo.", "status": "done", "pdf_para_idx": 0},
        ]
        out = tmp_path / "custom.docx"
        DocumentParser().compile_to_designed_docx(
            "\n\nTelo.", out, segments=segments, house_typography=custom_typo,
        )
        doc = docx.Document(str(out))
        sn = doc.styles["Normal"]
        assert sn.font.name == "Arial"
        assert sn.font.size.pt == 11
        assert sn.paragraph_format.line_spacing == 1.0
        # Letter size: 8.5 x 11 in
        sec = doc.sections[0]
        assert abs(sec.page_width.inches - 8.5) < 0.05
        assert sec.left_margin.inches == 1.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])