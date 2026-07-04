# tests/test_pdf_format_capture.py
#
# Tests for translate_core/pdf_format_capture.py — manifest capture + alignment.
# Verifies the manifest records per-paragraph metadata (heading levels,
# alignment, indent, blockquote) and aligns to segments additively without
# altering source/target/status/id.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

fitz = pytest.importorskip("fitz", reason="PyMuPDF required for pdf_format_capture tests")

from translate_core.pdf_format_capture import (
    ParagraphRecord,
    FormatManifest,
    capture_paragraph_manifest,
    attach_manifest_to_segments,
    _normalize_strong,
)


@pytest.fixture
def fixture_pdf(tmp_path):
    """Build a small PDF with a title, two body paragraphs, and a heading."""
    pdf_path = tmp_path / "test.pdf"
    doc = fitz.open()
    page = doc.new_page()
    # Title (large font)
    page.insert_text((72, 72), "Chapter Title", fontsize=18, fontname="helv")
    # Body paragraph 1 (normal size)
    page.insert_text((72, 110), "This is the first body paragraph with several words.", fontsize=10, fontname="helv")
    # Body paragraph 2 (normal size)
    page.insert_text((72, 130), "This is the second body paragraph with more words.", fontsize=10, fontname="helv")
    # Subheading (medium font)
    page.insert_text((72, 160), "Section Two", fontsize=14, fontname="helv")
    # Body paragraph 3
    page.insert_text((72, 185), "Content under the subheading continues here.", fontsize=10, fontname="helv")
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


class TestCaptureManifest:
    def test_capture_returns_manifest_with_paragraphs(self, fixture_pdf):
        m = capture_paragraph_manifest(fixture_pdf)
        assert isinstance(m, FormatManifest)
        assert len(m.paragraphs) > 0
        assert m.pdf_page_count == 1

    def test_capture_detects_heading_by_font_size(self, fixture_pdf):
        m = capture_paragraph_manifest(fixture_pdf)
        h1 = [p for p in m.paragraphs if p.heading_level == 1]
        # The 18pt title should be detected as H1 (ratio >= 1.4 × body 10pt)
        assert len(h1) >= 1
        assert "Chapter Title" in h1[0].text or "Title" in h1[0].text

    def test_capture_body_font_size_is_modal(self, fixture_pdf):
        m = capture_paragraph_manifest(fixture_pdf)
        # The dominant font size should be 10pt (body)
        assert m.body_font_size == 10.0

    def test_capture_assigns_sequential_para_idx(self, fixture_pdf):
        m = capture_paragraph_manifest(fixture_pdf)
        idxs = [p.pdf_para_idx for p in m.paragraphs]
        assert idxs == list(range(len(m.paragraphs)))


class TestAttachManifest:
    def test_attach_adds_keys_without_altering_segment(self):
        manifest = FormatManifest(
            paragraphs=[
                ParagraphRecord(pdf_para_idx=0, text="Hello world this is a test paragraph"),
                ParagraphRecord(pdf_para_idx=1, text="Second paragraph here with words", heading_level=1),
            ],
            body_font_size=10.0,
        )
        segs = [
            {"id": 0, "source": "Hello world this is a test paragraph", "target": "t0", "status": "done"},
            {"id": 1, "source": "Second paragraph here with words", "target": "t1", "status": "pending"},
        ]
        n = attach_manifest_to_segments(segs, manifest)
        assert n == 2
        assert segs[0]["pdf_para_idx"] == 0
        assert segs[0]["heading_level"] == 0
        # source/target/status/id untouched
        assert segs[0]["source"] == "Hello world this is a test paragraph"
        assert segs[0]["target"] == "t0"
        assert segs[0]["status"] == "done"
        assert segs[0]["id"] == 0
        # heading segment
        assert segs[1]["heading_level"] == 1

    def test_attach_is_idempotent(self):
        manifest = FormatManifest(
            paragraphs=[ParagraphRecord(pdf_para_idx=5, text="Unique text segment here")],
        )
        segs = [{"id": 0, "source": "Unique text segment here", "target": "", "status": "pending"}]
        n1 = attach_manifest_to_segments(segs, manifest)
        n2 = attach_manifest_to_segments(segs, manifest)
        assert n1 == 1
        assert n2 == 1  # already attached, counted again

    def test_attach_handles_unmatched_gracefully(self):
        manifest = FormatManifest(
            paragraphs=[ParagraphRecord(pdf_para_idx=0, text="Completely different text")],
        )
        segs = [
            {"id": 0, "source": "Unrelated segment", "target": "", "status": "pending"},
        ]
        n = attach_manifest_to_segments(segs, manifest)
        assert n == 0
        assert "pdf_para_idx" not in segs[0]

    def test_normalize_strong_repairs_hyphenated_linebreaks(self):
        assert _normalize_strong("fore-\ncloses") == "forecloses"
        assert _normalize_strong("hello   world") == "hello world"

    def test_normalize_strong_strips_footnote_markers(self):
        assert _normalize_strong('text[^25] more') == "text more"
        assert _normalize_strong('end."25 Next') == 'end." Next'


class TestGroupSegmentsByPara:
    def test_grouping_consecutive_same_para(self):
        from translate_core.book_outline import group_segments_by_para
        segs = [
            {"id": 0, "source": "a", "pdf_para_idx": 0},
            {"id": 1, "source": "b", "pdf_para_idx": 0},
            {"id": 2, "source": "c", "pdf_para_idx": 1},
            {"id": 3, "source": "d"},  # None → own group
        ]
        groups = group_segments_by_para(segs)
        assert len(groups) == 3
        assert [s["id"] for s in groups[0]] == [0, 1]
        assert [s["id"] for s in groups[1]] == [2]
        assert [s["id"] for s in groups[2]] == [3]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])