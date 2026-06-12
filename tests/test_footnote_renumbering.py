# tests/test_footnote_renumbering.py
#
# Tests for DocumentParser._renumber_footnotes — sequence-expectation
# validation, false-positive rejection, class-(c) no-op, and
# footnote_alignment_report.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.doc_parser import DocumentParser, footnote_alignment_report


class TestRenumbersTwoChapterNotes:
    """Two-chapter text: notes rows restart at 1 per chapter,
    body [^N] refs → global sequential renumbering.

    Short ladders (< 4 rows) are referred to smol; tests pin the verdict
    so they stay deterministic and network-free."""

    def test_global_sequential_defs(self, monkeypatch):
        from translate_core.entity_extraction import smol_client
        monkeypatch.setattr(
            smol_client, "classify_numbered_block", lambda rows, **kw: "footnotes"
        )
        text = """Chapter one

Some text [^1] and more [^2].

Notes
1. First note of ch1
2. Second note of ch1

Chapter two

More text [^1] and also [^2].

Notes
1. First note of ch2
2. Second note of ch2
"""
        parser = DocumentParser()
        result, report = parser._renumber_footnotes(text)
        # 4 defs total (2 per chapter), globally renumbered [^1]..[^4]
        assert report["defs"] == 4
        assert report["blocks"] == [2, 2]
        assert report["refs"] >= 2

    def test_single_bare_row_never_promoted(self, monkeypatch):
        """An isolated '1.' row is the bibliography false-positive shape
        (wrapped journal volume numbers) — never auto-promoted, smol is
        not even consulted."""
        from translate_core.entity_extraction import smol_client
        monkeypatch.setattr(
            smol_client, "classify_numbered_block",
            lambda rows, **kw: (_ for _ in ()).throw(AssertionError("smol must not be called")),
        )
        text = """Intro text.

Notes
1. Only note

Body text here.
"""
        parser = DocumentParser()
        result, report = parser._renumber_footnotes(text)
        assert report["defs"] == 0
        assert "1. Only note" in result

    def test_ambiguous_ladder_left_unconverted_when_smol_down(self, monkeypatch):
        """Ollama unavailable → ambiguous short ladders stay unconverted
        and are surfaced in the report; nothing is silently guessed."""
        from translate_core.entity_extraction import smol_client
        monkeypatch.setattr(
            smol_client, "classify_numbered_block", lambda rows, **kw: None
        )
        text = """Notes
1. First short note
2. Second short note
"""
        parser = DocumentParser()
        result, report = parser._renumber_footnotes(text)
        assert report["defs"] == 0
        assert report["ambiguous_blocks"] == 1
        assert "1. First short note" in result


class TestFalsePositiveRejection:
    """A notes-section row with unexpected number stays as plain text."""

    def test_false_positive_def_row_rejected(self, monkeypatch):
        """A '210.' row mid-block (when expected is e.g. 3) is rejected."""
        from translate_core.entity_extraction import smol_client
        monkeypatch.setattr(
            smol_client, "classify_numbered_block", lambda rows, **kw: "footnotes"
        )
        text = """Notes
1. First note
2. Second note
210. Not a footnote
3. Third note
"""
        parser = DocumentParser()
        result, report = parser._renumber_footnotes(text)
        # 210. should be rejected (expected was 3)
        assert report["rejected_def_rows"] >= 1
        # Should have accepted 1, 2, 3 (3 after the rejected row)
        assert report["defs"] == 3
        # The line "210. Not a footnote" should remain as plain text
        assert "210. Not a footnote" in result or "210" in result


class TestNoNotesSectionNoConversion:
    """Document without a notes section → class (c): convert nothing."""

    def test_plain_text_unchanged(self):
        text = "Just a plain paragraph with no notes section.\nAnother line."
        parser = DocumentParser()
        result, report = parser._renumber_footnotes(text)
        assert report["defs"] == 0
        assert report["refs"] == 0
        assert report["aligned"] is True
        assert result == text

    def test_existing_markers_preserved_if_no_defs(self):
        """If there are [^N] refs but no defs and no notes section,
        class (c) applies: nothing is converted."""
        text = "Some text with [^1] but no definitions."
        parser = DocumentParser()
        result, report = parser._renumber_footnotes(text)
        assert report["defs"] == 0


class TestFootnoteAlignmentReport:
    """footnote_alignment_report detects misalignment between defs and refs."""

    def test_missing_def_flagged(self):
        """Inline ref with no matching def → missing_def_numbers."""
        segments = [
            {"source": "See [^5] for details."},
            {"source": "Plain text."},
        ]
        report = footnote_alignment_report(segments)
        assert report["defs"] == 0
        assert report["refs"] == 1
        assert report["aligned"] is False
        assert "5" in report["missing_def_numbers"]

    def test_balanced_defs_and_refs(self):
        segments = [
            {"source": "See [^1] and [^2]."},
            {"source": "[^1]: First note."},
            {"source": "[^2]: Second note."},
        ]
        report = footnote_alignment_report(segments)
        assert report["defs"] == 2
        assert report["refs"] == 2
        assert report["aligned"] is True

    def test_unreferenced_def_flagged(self):
        segments = [
            {"source": "Plain text."},
            {"source": "[^99]: Orphan definition."},
        ]
        report = footnote_alignment_report(segments)
        assert "99" in report["unreferenced_def_numbers"]
        assert report["aligned"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])