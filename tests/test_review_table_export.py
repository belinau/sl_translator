# tests/test_review_table_export.py
#
# Tests for DocumentParser.compile_review_table_docx — bilingual review
# table export with inline diff rendering and comments column.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

docx = pytest.importorskip("docx")

from translate_core.doc_parser import DocumentParser


def _make_clone(n: int = 3, with_suggestion: int = 1, with_comment: int = 2) -> dict:
    """Build a minimal review clone dict for testing."""
    segs = []
    for i in range(n):
        seg = {
            "id": i,
            "original_id": i,
            "source": f"Source text {i} here.",
            "target": f"Target text {i} here.",
            "reviewer_target": "",
            "reviewer_status": "pending",
            "comments": [],
        }
        if i == with_suggestion:
            seg["reviewer_target"] = f"Revised target {i} here."
        if i == with_comment:
            from translate_core import comments as cm
            seg["comments"] = [
                cm.new_comment("translator", 0, f"Translator note {i}"),
                cm.new_comment("reviewer", 1, f"Reviewer note {i}", review_id="rev-test1234"),
            ]
        segs.append(seg)
    return {
        "review_id": "rev-test1234",
        "original_project_id": "testproj",
        "original_filename": "test.pdf",
        "lang_pair": "en->sl",
        "status": "reviewing",
        "reviewer_name": "Test Reviewer",
        "segments": segs,
    }


class TestReviewTableExport:
    """compile_review_table_docx produces a 4-column bilingual table."""

    def test_table_structure(self, tmp_path):
        """Output has one table with header + one row per segment."""
        clone = _make_clone(n=3)
        out = tmp_path / "table.docx"
        DocumentParser().compile_review_table_docx(clone, out)

        d = docx.Document(str(out))
        assert len(d.tables) == 1
        t = d.tables[0]
        assert len(t.columns) == 4
        # Header + 3 data rows
        assert len(t.rows) == 4
        headers = [c.text for c in t.rows[0].cells]
        assert headers == ["#", "Source", "Target", "Comments"]

    def test_segment_numbering(self, tmp_path):
        """First column shows 1-based segment numbering."""
        clone = _make_clone(n=3)
        out = tmp_path / "table.docx"
        DocumentParser().compile_review_table_docx(clone, out)

        d = docx.Document(str(out))
        t = d.tables[0]
        for i in range(3):
            assert t.rows[i + 1].cells[0].text == str(i + 1)

    def test_source_and_target_columns(self, tmp_path):
        """Source and target text appear in their respective columns."""
        clone = _make_clone(n=3)
        out = tmp_path / "table.docx"
        DocumentParser().compile_review_table_docx(clone, out)

        d = docx.Document(str(out))
        t = d.tables[0]
        # Row 1 = seg 0
        assert "Source text 0" in t.rows[1].cells[1].text
        assert "Target text 0" in t.rows[1].cells[2].text

    def test_diff_rendered_when_suggestion_exists(self, tmp_path):
        """When reviewer_target differs from target, the target cell
        contains diff runs: strikethrough (deleted) and bold (inserted)."""
        clone = _make_clone(n=3, with_suggestion=1)
        out = tmp_path / "table.docx"
        DocumentParser().compile_review_table_docx(clone, out)

        d = docx.Document(str(out))
        t = d.tables[0]
        # Row 2 = seg 1 (with_suggestion=1)
        target_cell = t.rows[2].cells[2]
        runs = target_cell.paragraphs[0].runs
        # Expect at least one strikethrough run and one bold run
        strike_runs = [r for r in runs if r.font.strike]
        bold_runs = [r for r in runs if r.bold]
        assert len(strike_runs) >= 1, "Expected strikethrough run for deleted text"
        assert len(bold_runs) >= 1, "Expected bold run for inserted text"
        # No literal asterisks
        for r in runs:
            assert "*" not in (r.text or "")

    def test_no_diff_when_no_suggestion(self, tmp_path):
        """When no reviewer_target, target cell renders plain text."""
        clone = _make_clone(n=3, with_suggestion=99)  # no suggestion
        out = tmp_path / "table.docx"
        DocumentParser().compile_review_table_docx(clone, out)

        d = docx.Document(str(out))
        t = d.tables[0]
        target_cell = t.rows[1].cells[2]
        runs = target_cell.paragraphs[0].runs
        # No strikethrough or bold runs
        for r in runs:
            assert not r.font.strike
            assert not r.bold

    def test_comments_column_populated(self, tmp_path):
        """Comments column shows formatted comment text."""
        clone = _make_clone(n=3, with_comment=0)
        out = tmp_path / "table.docx"
        DocumentParser().compile_review_table_docx(clone, out)

        d = docx.Document(str(out))
        t = d.tables[0]
        comments_text = t.rows[1].cells[3].text
        assert "Translator note 0" in comments_text
        assert "Reviewer note 0" in comments_text

    def test_empty_comments_column(self, tmp_path):
        """Segments without comments have empty comments cell."""
        clone = _make_clone(n=3, with_comment=99)  # no comments
        out = tmp_path / "table.docx"
        DocumentParser().compile_review_table_docx(clone, out)

        d = docx.Document(str(out))
        t = d.tables[0]
        assert t.rows[1].cells[3].text == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])