# tests/test_relink_para_idx.py
#
# Tests for DocumentParser.relink_docx_para_idx — re-assigns docx_para_idx
# to segments by matching normalized source text against template paragraphs.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

docx = pytest.importorskip("docx")


from translate_core.doc_parser import DocumentParser


def _create_fixture_docx(tmp_path, paragraphs):
    """Create a simple DOCX with the given paragraph texts."""
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(11)
    for text in paragraphs:
        doc.add_paragraph(text)
    path = tmp_path / "fixture.docx"
    doc.save(str(path))
    return path


class TestRelinkDocxParaIdx:
    def test_basic_assignment(self, tmp_path):
        """Three-paragraph template, three segments → all assigned."""
        paras = ["First paragraph.", "Second paragraph.", "Third paragraph."]
        template = _create_fixture_docx(tmp_path, paras)
        segments = [
            {"source": "First paragraph."},
            {"source": "Second paragraph."},
            {"source": "Third paragraph."},
        ]
        parser = DocumentParser()
        assigned = parser.relink_docx_para_idx(template, segments)
        assert assigned == 3
        assert segments[0]["docx_para_idx"] == 0
        assert segments[1]["docx_para_idx"] == 1
        assert segments[2]["docx_para_idx"] == 2

    def test_split_paragraph_shares_idx(self, tmp_path):
        """One paragraph split into two segments: both get the same idx."""
        paras = ["First paragraph.", "A longer paragraph with more content.", "Third paragraph."]
        template = _create_fixture_docx(tmp_path, paras)
        segments = [
            {"source": "First paragraph."},
            {"source": "A longer paragraph"},  # first chunk
            {"source": "with more content."},   # second chunk of same para
            {"source": "Third paragraph."},
        ]
        parser = DocumentParser()
        assigned = parser.relink_docx_para_idx(template, segments)
        # "A longer paragraph" is contained in "A longer paragraph with more content."
        # and "with more content." is contained in it too
        # Both chunks should map to paragraph index 1
        assert assigned == 4
        assert segments[0]["docx_para_idx"] == 0
        assert segments[1]["docx_para_idx"] == 1
        assert segments[2]["docx_para_idx"] == 1  # same paragraph as chunk above
        assert segments[3]["docx_para_idx"] == 2

    def test_nbsp_normalized(self, tmp_path):
        """Segments with \\xa0 (non-breaking space) still match."""
        paras = ["Hello world text."]
        template = _create_fixture_docx(tmp_path, paras)
        segments = [
            {"source": "Hello\xa0world text."},  # \xa0 instead of space
        ]
        parser = DocumentParser()
        assigned = parser.relink_docx_para_idx(template, segments)
        assert assigned == 1
        assert segments[0]["docx_para_idx"] == 0

    def test_absent_segment_stays_unassigned(self, tmp_path):
        """A segment whose source doesn't appear in the template stays without idx."""
        paras = ["First paragraph.", "Second paragraph."]
        template = _create_fixture_docx(tmp_path, paras)
        segments = [
            {"source": "First paragraph."},
            {"source": "This text is not in the template."},
        ]
        parser = DocumentParser()
        assigned = parser.relink_docx_para_idx(template, segments)
        # Only the first segment matches
        assert assigned == 1
        assert segments[0]["docx_para_idx"] == 0
        assert "docx_para_idx" not in segments[1]

    def test_existing_idx_preserved(self, tmp_path):
        """Segments that already have docx_para_idx are skipped."""
        paras = ["First paragraph.", "Second paragraph."]
        template = _create_fixture_docx(tmp_path, paras)
        segments = [
            {"source": "First paragraph.", "docx_para_idx": 0},
            {"source": "Second paragraph."},
        ]
        parser = DocumentParser()
        assigned = parser.relink_docx_para_idx(template, segments)
        assert assigned == 1
        assert segments[0]["docx_para_idx"] == 0
        assert segments[1]["docx_para_idx"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])