# tests/test_pdf_dict_to_markdown_text.py
#
# Tests for DocumentParser._pdf_dict_to_markdown_text — the PDF parse path
# that walks fitz get_text("dict") spans and emits *italic* / **bold**
# markdown markers, preserving emphasis that get_text() plain text loses.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

fitz = pytest.importorskip("fitz", reason="PyMuPDF required")

# Use the real Kafer PDF for integration tests (it has verified italic spans)
KAFER_PDF = Path(__file__).resolve().parent.parent / "data" / "books" / "feminist-queer-crip-alison-kafer.pdf"


@pytest.fixture
def kafer_pdf():
    """The Kafer PDF — has 8.5pt footnotes with MinionPro-It italic spans."""
    if not KAFER_PDF.exists():
        pytest.skip("Kafer PDF not available")
    return KAFER_PDF


@pytest.fixture
def plain_pdf(tmp_path):
    """A simple PDF with only regular text (no italic)."""
    pdf_path = tmp_path / "plain.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Regular text line one.", fontsize=10, fontname="helv")
    page.insert_text((72, 90), "Regular text line two.", fontsize=10, fontname="helv")
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


class TestPdfDictToMarkdown:
    """_pdf_dict_to_markdown_text emits *...* around italic spans."""

    def test_italic_emitted_on_kafer(self, kafer_pdf):
        """Kafer PDF has italic spans (MinionPro-It) — they become *...*."""
        from translate_core.doc_parser import DocumentParser
        parser = DocumentParser()
        md = parser._pdf_dict_to_markdown_text(kafer_pdf)
        # Kafer p195 has "Washington Post," in italic → *Washington Post,*
        assert "*Washington Post*" in md or "*Washington Post,*" in md, (
            "Expected italic *Washington Post* in Kafer PDF output"
        )

    def test_plain_pdf_no_italic_markers(self, plain_pdf):
        """A PDF with no italic text produces no *...* markers."""
        from translate_core.doc_parser import DocumentParser
        parser = DocumentParser()
        md = parser._pdf_dict_to_markdown_text(plain_pdf)
        assert "*Regular*" not in md, f"Unexpected italic in plain PDF: {md!r}"

    def test_line_structure_preserved(self, plain_pdf):
        """Output has one line per fitz line (newlines preserved)."""
        from translate_core.doc_parser import DocumentParser
        parser = DocumentParser()
        md = parser._pdf_dict_to_markdown_text(plain_pdf)
        lines = [l for l in md.split("\n") if l.strip()]
        assert len(lines) >= 2, f"Expected >=2 lines, got {len(lines)}"

    def test_kafer_footnote_italic_preserved(self, kafer_pdf):
        """Kafer footnote italic (The Disability Studies Reader) is preserved."""
        from translate_core.doc_parser import DocumentParser
        parser = DocumentParser()
        md = parser._pdf_dict_to_markdown_text(kafer_pdf)
        assert "*The Disability Studies Reader*" in md, (
            "Expected *The Disability Studies Reader* in Kafer output"
        )

    def test_empty_pdf_returns_empty(self, tmp_path):
        """Empty PDF returns empty string."""
        from translate_core.doc_parser import DocumentParser
        pdf_path = tmp_path / "empty.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()
        parser = DocumentParser()
        md = parser._pdf_dict_to_markdown_text(pdf_path)
        assert md.strip() == ""

    def test_to_markdown_with_meta_kafer_preserves_italic(self, kafer_pdf):
        """Full to_markdown_with_meta path preserves italic through reflow/renumber."""
        from translate_core.doc_parser import DocumentParser
        parser = DocumentParser()
        md, _ = parser.to_markdown_with_meta(kafer_pdf, preprocess=True)
        # Italic should survive reflow + renumber
        assert "*Washington Post*" in md or "*Washington Post,*" in md, (
            "Italic lost in full pipeline"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])