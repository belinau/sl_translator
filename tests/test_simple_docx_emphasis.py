# tests/test_simple_docx_emphasis.py
#
# Tests for the simple-pipeline DOCX emphasis path:
#   1. _docx_paragraph_to_markdown  — DOCX runs → *italic* / **bold** markers
#   2. _parse_docx                  — segments carry emphasis markers
#   3. compile_from_template        — markdown emphasis in target renders
#      as real Word italic/bold runs; plain text falls back to proportional
#      distribution.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

docx = pytest.importorskip("docx")


# ======================================================================
# _docx_paragraph_to_markdown — DOCX runs → markdown emphasis
# ======================================================================


def _make_paragraph(doc, runs: list[tuple[str, bool, bool]]) -> "docx.paragraph.Paragraph":
    """Helper: build a paragraph with runs as (text, italic, bold)."""
    p = doc.add_paragraph()
    for txt, it, bd in runs:
        run = p.add_run(txt)
        run.italic = it
        run.bold = bd
    return p


class TestDocxParagraphToMarkdown:
    """_docx_paragraph_to_markdown converts runs to markdown emphasis."""

    def test_plain_text_no_markers(self, tmp_path):
        from main import _docx_paragraph_to_markdown
        d = docx.Document()
        p = _make_paragraph(d, [("Plain text.", False, False)])
        assert _docx_paragraph_to_markdown(p) == "Plain text."

    def test_italic_wrapped(self, tmp_path):
        from main import _docx_paragraph_to_markdown
        d = docx.Document()
        p = _make_paragraph(d, [("Regular and ", False, False),
                                 ("italic part", True, False),
                                 (" text.", False, False)])
        result = _docx_paragraph_to_markdown(p)
        assert "*italic part*" in result
        assert "Regular and " in result

    def test_bold_wrapped(self, tmp_path):
        from main import _docx_paragraph_to_markdown
        d = docx.Document()
        p = _make_paragraph(d, [("A ", False, False),
                                 ("bold word", False, True),
                                 (".", False, False)])
        result = _docx_paragraph_to_markdown(p)
        assert "**bold word**" in result

    def test_bold_italic_wrapped(self, tmp_path):
        from main import _docx_paragraph_to_markdown
        d = docx.Document()
        p = _make_paragraph(d, [("both", True, True)])
        result = _docx_paragraph_to_markdown(p)
        assert "***both***" in result

    def test_adjacent_same_flags_coalesced(self, tmp_path):
        """Two adjacent italic runs coalesce into one *span*, not *a**b*."""
        from main import _docx_paragraph_to_markdown
        d = docx.Document()
        p = _make_paragraph(d, [("one", True, False), ("two", True, False)])
        result = _docx_paragraph_to_markdown(p)
        assert result == "*onetwo*"
        assert "**" not in result


# ======================================================================
# _parse_docx — segments carry emphasis markers
# ======================================================================


class TestParseDocxEmphasis:
    """_parse_docx emits segments with markdown emphasis markers."""

    def test_parse_docx_emits_italic_markers(self, tmp_path):
        from main import _parse_docx
        d = docx.Document()
        d.add_paragraph("This has *italic* in it.")  # plain text with literal *
        # Actually we need a real italic run:
        d2 = docx.Document()
        p = d2.add_paragraph()
        p.add_run("This has ")
        r = p.add_run("italic")
        r.italic = True
        p.add_run(" in it.")
        path = tmp_path / "test.docx"
        d2.save(str(path))

        segments = _parse_docx(path)
        assert len(segments) >= 1
        source = segments[0]["source"]
        assert "*italic*" in source
        assert "This has " in source
        assert "docx_para_idx" in segments[0]

    def test_parse_docx_emits_bold_markers(self, tmp_path):
        from main import _parse_docx
        d = docx.Document()
        p = d.add_paragraph()
        p.add_run("A ")
        r = p.add_run("bold")
        r.bold = True
        p.add_run(" word.")
        path = tmp_path / "bold.docx"
        d.save(str(path))

        segments = _parse_docx(path)
        assert len(segments) >= 1
        assert "**bold**" in segments[0]["source"]


# ======================================================================
# compile_from_template — markdown emphasis renders in template export
# ======================================================================


class TestCompileFromTemplateEmphasis:
    """compile_from_template renders markdown emphasis as real Word runs."""

    def test_emphasis_target_renders_as_runs(self, tmp_path):
        """Target text with *italic* / **bold** markers → real Word runs,
        no literal asterisks."""
        from translate_core.doc_parser import DocumentParser

        # Build a template DOCX with one paragraph carrying plain runs.
        d = docx.Document()
        p = d.add_paragraph()
        p.add_run("Original text here.")
        template = tmp_path / "template.docx"
        d.save(str(template))

        segments = [{
            "id": 0,
            "source": "Original text here.",
            "target": "Prevedeno *kurzivo* in **krepko**.",
            "status": "done",
            "docx_para_idx": 0,
        }]

        out = tmp_path / "out.docx"
        DocumentParser().compile_from_template(template, out, segments)

        result = docx.Document(str(out))
        paras = [par for par in result.paragraphs if par.text.strip()]
        assert len(paras) >= 1
        runs = paras[0].runs
        italic_runs = [r for r in runs if r.italic and not r.bold]
        bold_runs = [r for r in runs if r.bold and not r.italic]
        assert len(italic_runs) >= 1, "Expected italic run from *kurzivo*"
        assert len(bold_runs) >= 1, "Expected bold run from **krepko**"
        # No literal asterisks
        for r in runs:
            assert "*" not in (r.text or ""), f"Literal asterisk in run: {r.text!r}"
        # Content preserved
        italic_text = " ".join(r.text for r in italic_runs)
        assert "kurzivo" in italic_text
        bold_text = " ".join(r.text for r in bold_runs)
        assert "krepko" in bold_text

    def test_plain_text_falls_back_to_proportional(self, tmp_path):
        """Target without emphasis markers uses proportional distribution
        (backward compat)."""
        from translate_core.doc_parser import DocumentParser

        d = docx.Document()
        p = d.add_paragraph()
        r1 = p.add_run("Original ")
        r1.bold = True
        r2 = p.add_run("text.")
        template = tmp_path / "template.docx"
        d.save(str(template))

        segments = [{
            "id": 0,
            "source": "Original text.",
            "target": "Prevedeno besedilo tukaj.",
            "status": "done",
            "docx_para_idx": 0,
        }]

        out = tmp_path / "out.docx"
        DocumentParser().compile_from_template(template, out, segments)

        result = docx.Document(str(out))
        paras = [par for par in result.paragraphs if par.text.strip()]
        # The text should be replaced (no literal asterisks, text present)
        import re as _re
        full_text = _re.sub(r"\s+", " ", " ".join(r.text for r in paras[0].runs))
        assert "Prevedeno besedilo tukaj." in full_text
        # Proportional path keeps the original run count (2 runs: bold + plain)
        assert len(paras[0].runs) == 2

    def test_no_markers_no_emphasis_runs(self, tmp_path):
        """Plain target text produces no italic/bold runs in output."""
        from translate_core.doc_parser import DocumentParser

        d = docx.Document()
        d.add_paragraph("Plain original.")
        template = tmp_path / "template.docx"
        d.save(str(template))

        segments = [{
            "id": 0,
            "source": "Plain original.",
            "target": "Plain translation.",
            "status": "done",
            "docx_para_idx": 0,
        }]

        out = tmp_path / "out.docx"
        DocumentParser().compile_from_template(template, out, segments)

        result = docx.Document(str(out))
        paras = [par for par in result.paragraphs if par.text.strip()]
        for r in paras[0].runs:
            assert not r.italic or r.italic is None
            assert not r.bold or r.bold is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])