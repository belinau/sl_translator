# tests/test_export_emphasis.py
#
# Tests for compile_to_designed_docx emphasis rendering and
# emphasis_integrity checks.

import sys
from pathlib import Path
import re

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

docx = pytest.importorskip("docx")

from translate_core.doc_parser import DocumentParser, _MD_EMPHASIS_RE
from translate_core.style_rules import emphasis_integrity


# ======================================================================
# compile_to_designed_docx — emphasis rendering
# ======================================================================


class TestExportEmphasis:
    """Body text *italic* / **bold** exports as real Word italic/bold runs
    with no literal asterisks. Footnotes also render emphasis."""

    def test_body_emphasis_as_runs(self, tmp_path):
        """Markdown emphasis in body text becomes italic/bold Word runs."""
        md = "Telo z *kurzivo* in **krepko**.\n\n[^1]: Glej *Naslov dela*, str. 5."
        # Add a body line carrying the footnote ref
        md_body = "Telo z *kurzivo* in **krepko**. [^1]\n\n[^1]: Glej *Naslov dela*, str. 5."

        output = tmp_path / "test_emphasis.docx"
        parser = DocumentParser()
        parser.compile_to_designed_docx(md_body, output)

        d = docx.Document(str(output))
        # Collect all run text and formatting
        all_runs = []
        for para in d.paragraphs:
            for run in para.runs:
                all_runs.append(run)

        # At least one italic run and one bold run
        italic_runs = [r for r in all_runs if r.italic and not r.bold]
        bold_runs = [r for r in all_runs if r.bold and not r.italic]
        assert len(italic_runs) >= 1, "Expected at least one italic run"
        assert len(bold_runs) >= 1, "Expected at least one bold run"

        # No literal asterisks in any run text (emphasis markers are consumed)
        asterisk_runs = [r for r in all_runs if "*" in (r.text or "")]
        assert len(asterisk_runs) == 0, (
            f"Found literal asterisks in runs: {[r.text for r in asterisk_runs]}"
        )

        # The italic run should contain "kurzivo" (without *)
        italic_texts = [r.text for r in italic_runs]
        assert any("kurzivo" in t for t in italic_texts), (
            f"Expected 'kurzivo' in italic runs, got: {italic_texts}"
        )

        # The bold run should contain "krepko" (without **)
        bold_texts = [r.text for r in bold_runs]
        assert any("krepko" in t for t in bold_texts), (
            f"Expected 'krepko' in bold runs, got: {bold_texts}"
        )

    def test_footnote_emphasis_rendered(self, tmp_path):
        """Emphasis in footnote text is rendered as real italic/bold in
        the injected footnotes.xml."""
        md_body = "Body text [^1].\n\n[^1]: Glej *Naslov dela*, str. 5."
        output = tmp_path / "test_fn_emphasis.docx"
        parser = DocumentParser()
        parser.compile_to_designed_docx(md_body, output)

        # Read the raw footnotes.xml from the ZIP
        import zipfile

        with zipfile.ZipFile(str(output), "r") as zf:
            fn_xml = zf.read("word/footnotes.xml").decode("utf-8")

        # Should contain <w:i/> for italic formatting
        assert "<w:i/>" in fn_xml, "Expected <w:i/> in footnotes XML for italic"

        # Should contain the footnote text without literal asterisks
        # "Naslov dela" should appear (the italic text inside *...*)
        assert "Naslov dela" in fn_xml, (
            f"Expected 'Naslov dela' in footnotes XML"
        )

        # The literal * markers should NOT appear
        assert "*Naslov" not in fn_xml, "Literal asterisks should not appear in footnotes XML"

    def test_footnote_superscript_style_defined(self, tmp_path):
        """Exported DOCX defines FootnoteReference style with superscript."""
        import zipfile
        md_body = "Body text [^1].\n\n[^1]: Footnote text."
        output = tmp_path / "test_fn_style.docx"
        DocumentParser().compile_to_designed_docx(md_body, output)
        with zipfile.ZipFile(str(output), "r") as zf:
            sx = zf.read("word/styles.xml").decode("utf-8")
        assert "FootnoteReference" in sx, "FootnoteReference style missing from styles.xml"
        assert "superscript" in sx, "FootnoteReference style lacks superscript vertAlign"
        assert "FootnoteText" in sx, "FootnoteText paragraph style missing"

    def test_footnote_text_size_from_typography(self, tmp_path):
        """FootnoteText style uses footnote_size_pt from the typography profile."""
        import zipfile
        md_body = "Body [^1].\n\n[^1]: Text."
        output = tmp_path / "test_fn_size.docx"
        typo = {"body_font": "Times New Roman", "body_size_pt": 12,
                "line_spacing": 1.5, "margins_in": 1.0, "space_after_pt": 0,
                "para_first_line_indent_in": 0.0, "footnote_size_pt": 10,
                "footnote_line_spacing": 1.0, "blockquote_size_pt": 11,
                "blockquote_line_spacing": 1.0, "blockquote_indent_in": 0.5,
                "h1_size_pt": 18, "h2_size_pt": 13}
        DocumentParser().compile_to_designed_docx(md_body, output, house_typography=typo)
        with zipfile.ZipFile(str(output), "r") as zf:
            sx = zf.read("word/styles.xml").decode("utf-8")
        # 10pt → w:sz="20" (half-points)
        assert 'w:val="20"' in sx, f"FootnoteText size not 10pt (20 half-pts) in: {sx[:500]!r}"


# ======================================================================
# emphasis_integrity from style_rules
# ======================================================================


class TestEmphasisIntegrityExport:
    """emphasis_integrity catches missing/lost emphasis markup."""

    def test_missing_italic_span_warning(self):
        """Source with one *italic* span, target with none → warning."""
        source = "This is *important* text."
        target = "To je pomembno besedilo."
        results = emphasis_integrity(source, target)
        assert len(results) >= 1
        assert any("count differs" in r["message"] for r in results)

    def test_balanced_emphasis_no_warning(self):
        """Source and target both have matching emphasis → no count warning."""
        source = "This is *important*."
        target = "To je *pomembno*."
        results = emphasis_integrity(source, target)
        count_warnings = [r for r in results if "count differs" in r["message"]]
        assert count_warnings == []

    def test_odd_asterisk_warning(self):
        """Target with odd number of * characters → unbalanced warning."""
        source = "This has *italic* text."
        target = "This has * one asterisk."
        results = emphasis_integrity(source, target)
        assert any("Unbalanced" in r["message"] for r in results)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])