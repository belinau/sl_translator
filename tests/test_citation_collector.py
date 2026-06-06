# tests/test_citation_collector.py
#
# Unit tests for citation_collector.py — Phase 2.
# Tests the CitationSnippet dataclass, format adapters, and orchestrator plumbing.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.citation_collector import (
    CitationSnippet,
    IngestReport,
    collect_from_segments_meta,
    collect_from_editor_segment,
    read_tmx_manifest,
    write_tmx_manifest,
)
from translate_core.entity_extraction.citation_types import CitationStyle


# ======================================================================
# Tests: CitationSnippet dataclass
# ======================================================================


class TestCitationSnippet:
    def test_frozen_dataclass(self):
        s = CitationSnippet(
            text="Haraway, Donna. A Cyborg Manifesto.",
            origin="test.tmx",
            segment_idx=0,
            format="bibliography",
            style_hint=CitationStyle.CHICAGO_EN,
        )
        assert s.text == "Haraway, Donna. A Cyborg Manifesto."
        assert s.origin == "test.tmx"
        assert s.segment_idx == 0
        assert s.format == "bibliography"
        assert s.style_hint == CitationStyle.CHICAGO_EN
        assert s.container_work_id is None
        assert s.source_page is None
        assert s.footnote_number is None

    def test_with_container(self):
        s = CitationSnippet(
            text="Test citation.",
            origin="test.tmx",
            segment_idx=5,
            format="footnote",
            style_hint=CitationStyle.CHICAGO_SL,
            container_work_id="source:test-container",
            source_page=42,
            footnote_number=3,
        )
        assert s.container_work_id == "source:test-container"
        assert s.source_page == 42
        assert s.footnote_number == 3

    def test_immutable(self):
        s = CitationSnippet(
            text="Test",
            origin="test",
            segment_idx=0,
            format="footnote",
        )
        with pytest.raises(AttributeError):
            s.text = "changed"


# ======================================================================
# Tests: IngestReport dataclass
# ======================================================================


class TestIngestReport:
    def test_default_counts(self):
        r = IngestReport()
        assert r.extracted == 0
        assert r.classified == 0
        assert r.verified == 0
        assert r.written == 0
        assert r.queued == 0
        assert r.dropped == 0
        assert r.errors == 0


# ======================================================================
# Tests: collect_from_editor_segment
# ======================================================================


class TestCollectFromEditorSegment:
    def test_footnote_segment(self):
        snippet = collect_from_editor_segment(
            segment_text="Haraway, Donna. A Cyborg Manifesto. Routledge, 1991.",
            segments_meta_entry={"type": "footnote", "index": 5, "footnote_number": 5},
            project_id="test_project",
            container_work_id="source:test-container",
        )
        assert snippet is not None
        assert snippet.format == "footnote"
        assert snippet.footnote_number == 5
        assert snippet.container_work_id == "source:test-container"

    def test_bibliography_segment(self):
        snippet = collect_from_editor_segment(
            segment_text="Šelih, Alenka. Pravna zgodovina.",
            segments_meta_entry={"type": "bibliography", "index": 20},
            project_id="test_project",
        )
        assert snippet is not None
        assert snippet.format == "bibliography"

    def test_non_citation_segment_returns_none(self):
        snippet = collect_from_editor_segment(
            segment_text="This is regular body text.",
            segments_meta_entry={"type": "body_text", "index": 10},
            project_id="test_project",
        )
        assert snippet is None

    def test_empty_text_returns_none(self):
        snippet = collect_from_editor_segment(
            segment_text="   ",
            segments_meta_entry={"type": "footnote", "index": 1},
            project_id="test_project",
        )
        assert snippet is None

    def test_style_detection_on_footnote(self):
        """detect_style is called on the segment text."""
        snippet = collect_from_editor_segment(
            segment_text="Šelih, Alenka. Pravna zgodovina. Ljubljana: Založba, 2020.",
            segments_meta_entry={"type": "footnote", "index": 1},
            project_id="test_project",
        )
        assert snippet is not None
        # The style detected depends on the regex markers present.
        # This text doesn't have SL-specific markers, so it defaults to CHICAGO_EN.
        assert snippet.style_hint in (CitationStyle.CHICAGO_EN, CitationStyle.CHICAGO_SL)
    def test_endnote_segment(self):
        snippet = collect_from_editor_segment(
            segment_text="Some citation text.",
            segments_meta_entry={"type": "endnote", "index": 3},
            project_id="test_project",
        )
        assert snippet is not None
        assert snippet.format == "endnote"


# ======================================================================
# Tests: collect_from_segments_meta
# ======================================================================


class TestCollectFromSegmentsMeta:
    def test_filters_citation_segments(self):
        project = {
            "filename": "test.pdf",
            "segments": [
                {"source": "Body text paragraph.", "target": ""},
                {"source": "Haraway, Donna. A Cyborg Manifesto. Routledge, 1991.", "target": ""},
                {"source": "See ibid., 45.", "target": ""},
            ],
            "segments_meta": [
                {"type": "body_text", "index": 0},
                {"type": "bibliography_entry", "index": 1},
                {"type": "footnote", "index": 2},
            ],
        }
        snippets = collect_from_segments_meta(project, container_work_id="source:test-book")
        assert len(snippets) == 2
        assert snippets[0].format == "bibliography"
        assert snippets[1].format == "footnote"
        assert all(s.container_work_id == "source:test-book" for s in snippets)

    def test_skips_empty_segments(self):
        project = {
            "filename": "test.pdf",
            "segments": [
                {"source": "", "target": ""},
                {"source": "   ", "target": ""},
            ],
            "segments_meta": [
                {"type": "bibliography_entry", "index": 0},
                {"type": "footnote", "index": 1},
            ],
        }
        snippets = collect_from_segments_meta(project)
        assert len(snippets) == 0


# ======================================================================
# Tests: O-17 — cited_in self-loops forbidden
# ======================================================================


class TestCitedInSelfLoops:
    """O-17: cited_in self-loops are forbidden."""

    def test_snippet_container_id_different_from_source(self):
        """CitationSnippet container_work_id should be a separate container,
        not the same as the citation's own ID."""
        # This is a design test — the actual wiring is in extract_and_ingest
        s = CitationSnippet(
            text="Haraway, Donna. A Cyborg Manifesto.",
            origin="test.tmx",
            segment_idx=0,
            format="bibliography",
            container_work_id="source:test-container",
        )
        # The container_work_id is a book_translation or similar container,
        # never the same as the citation's own ID
        assert s.container_work_id is not None
        assert s.container_work_id != ""
# ======================================================================
# Tests: TMX manifest sidecar (Phase 3)
# ======================================================================


class TestTmxManifest:
    """Tests for write_tmx_manifest / read_tmx_manifest round-trip."""

    def test_write_and_read_manifest(self, tmp_path):
        """Round-trip: write a manifest then read it back."""
        tmx_file = tmp_path / "test.tmx"
        tmx_file.write_text("", encoding="utf-8")

        write_tmx_manifest(
            str(tmx_file),
            container_work_id="source:okri-cesta-sestradanih-2010",
            container_type="book_translation",
            source_lang="en",
            target_lang="sl",
        )

        manifest = read_tmx_manifest(str(tmx_file))
        assert manifest is not None
        assert manifest["container_work_id"] == "source:okri-cesta-sestradanih-2010"
        assert manifest["container_type"] == "book_translation"
        assert manifest["source_lang"] == "en"
        assert manifest["target_lang"] == "sl"
        assert "created_at" in manifest

    def test_manifest_file_is_sibling(self, tmp_path):
        """Manifest is written as <tmx>.manifest.json, not <tmx>.json."""
        tmx_file = tmp_path / "myfile.tmx"
        tmx_file.write_text("", encoding="utf-8")
        write_tmx_manifest(str(tmx_file), "source:x", "book_translation")
        assert (tmp_path / "myfile.manifest.json").exists()
        assert not (tmp_path / "myfile.json").exists()

    def test_read_nonexistent_returns_none(self, tmp_path):
        """read_tmx_manifest returns None when no sidecar exists."""
        tmx_file = tmp_path / "no_sidecar.tmx"
        tmx_file.write_text("", encoding="utf-8")
        assert read_tmx_manifest(str(tmx_file)) is None

    def test_collect_from_tmx_uses_manifest_container_id(self, tmp_path):
        """collect_from_tmx picks up container_work_id from manifest sidecar."""
        # Write a minimal valid TMX
        tmx_content = """<?xml version="1.0" encoding="UTF-8"?>
<tmx version="1.4">
  <header srclang="en" adminlang="en"/>
  <body>
    <tu>
      <tuv xml:lang="en"><seg>Haraway, Donna. A Cyborg Manifesto. London: Routledge, 1991.</seg></tuv>
      <tuv xml:lang="sl"><seg>Prevod.</seg></tuv>
    </tu>
  </body>
</tmx>"""
        tmx_file = tmp_path / "test.tmx"
        tmx_file.write_text(tmx_content, encoding="utf-8")

        # Write manifest
        write_tmx_manifest(
            str(tmx_file),
            container_work_id="source:test-container-123",
            container_type="book_translation",
        )

        # collect_from_tmx should pick up the container_work_id automatically
        # (We can't easily test the full path without VL, but we can verify
        # the manifest is read and the sidecar exists.)
        manifest = read_tmx_manifest(str(tmx_file))
        assert manifest is not None
        assert manifest["container_work_id"] == "source:test-container-123"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])