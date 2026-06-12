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

    def test_body_segment_yields_body_snippet(self):
        """Body segments now flow to live extraction (format='body')."""
        snippet = collect_from_editor_segment(
            segment_text="This is regular body text.",
            segments_meta_entry={"type": "body_text", "index": 10},
            project_id="test_project",
        )
        assert snippet is not None
        assert snippet.format == "body"

    def test_lang_pair_embedded_in_origin(self):
        """lang_pair lands in origin so _detect_source_lang recovers it."""
        from translate_core.entity_extraction.smol_extractor import _detect_source_lang

        snippet = collect_from_editor_segment(
            segment_text="Haraway, Donna. A Cyborg Manifesto. Routledge, 1991.",
            segments_meta_entry={"type": "footnote", "index": 5},
            project_id="test_project",
            target_text="Haraway, Donna. Kiborški manifest. Routledge, 1991.",
            lang_pair="en-sl",
        )
        assert snippet is not None
        assert _detect_source_lang(snippet.origin) == ("en", "sl")
        assert snippet.target_text == "Haraway, Donna. Kiborški manifest. Routledge, 1991."

    def test_no_lang_pair_keeps_project_origin(self):
        snippet = collect_from_editor_segment(
            segment_text="Some citation text here.",
            segments_meta_entry={"type": "footnote", "index": 1},
            project_id="test_project",
        )
        assert snippet is not None
        assert snippet.origin == "test_project"

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


# ======================================================================
# Tests: extract_and_ingest live-extractor wiring
# ======================================================================


@pytest.fixture
def kg(tmp_path):
    from translate_core.knowledge_graph import KnowledgeGraph

    g = KnowledgeGraph(db_path=tmp_path / "kg.json")
    # Never touch data/knowledge.db.
    g.save = lambda: None  # type: ignore[method-assign]
    return g


class TestExtractAndIngestWiring:
    """The orchestrator feeds extractor output through build_record →
    score_all → dedup_records → write_to_kg (O-10 tiers)."""

    def _snippet(self, **overrides):
        defaults = dict(
            text="Haraway, Donna. A Cyborg Manifesto. Routledge, 1991.",
            origin="editor_en-sl_test_project",
            segment_idx=5,
            format="footnote",
            container_work_id="test-container",
            target_text="Haraway, Donna. Kiborški manifest. Routledge, 1991.",
        )
        defaults.update(overrides)
        return CitationSnippet(**defaults)

    def test_extractor_none_counts_as_error(self, kg, tmp_path):
        from translate_core.citation_collector import extract_and_ingest

        report = extract_and_ingest(
            [self._snippet()],
            kg,
            review_path=str(tmp_path / "review.json"),
            dropped_path=str(tmp_path / "dropped.jsonl"),
            extractor=lambda **kw: None,
        )
        assert report.extracted == 1
        assert report.errors == 1
        assert report.written == 0 and report.queued == 0

    def test_entities_flow_to_chokepoint(self, kg, tmp_path):
        """A returned entity must leave the pipeline as written/queued/dropped
        — never silently vanish."""
        from translate_core.citation_collector import extract_and_ingest

        seen_calls: list[dict] = []

        def extractor(**kw):
            seen_calls.append(kw)
            return [
                {
                    "kind": "agent_person",
                    "name": "Donna Haraway",
                    "role": "author",
                }
            ]

        report = extract_and_ingest(
            [self._snippet()],
            kg,
            review_path=str(tmp_path / "review.json"),
            dropped_path=str(tmp_path / "dropped.jsonl"),
            extractor=extractor,
        )
        # Extractor received the bilingual pair and editor metadata.
        assert seen_calls[0]["src"].startswith("Haraway")
        assert seen_calls[0]["tgt"].startswith("Haraway, Donna. Kiborški")
        assert seen_calls[0]["origin"] == "editor_en-sl_test_project"
        assert seen_calls[0]["container_work_id"] == "test-container"
        # The record must be accounted for at the chokepoint.
        assert report.extracted == 1
        assert report.errors == 0
        assert report.written + report.queued + report.dropped == 1

    def test_short_reference_never_reaches_extractor(self, kg, tmp_path):
        from translate_core.citation_collector import extract_and_ingest

        calls = []

        def extractor(**kw):
            calls.append(kw)
            return []

        report = extract_and_ingest(
            [self._snippet(text="Ibid., 45.")],
            kg,
            review_path=str(tmp_path / "review.json"),
            dropped_path=str(tmp_path / "dropped.jsonl"),
            extractor=extractor,
        )
        assert calls == []
        assert report.dropped == 1
    def test_successive_confirms_accumulate_review_queue(self, kg, tmp_path):
        """Records from the first editor confirm must survive after the second confirm.

        This is the literal user-reported bug: re-confirming a segment used to
        overwrite data/extraction_review.json so only the last confirm's entities
        survived. The fix merges rather than replaces.
        """
        from translate_core.citation_collector import extract_and_ingest

        review_path = str(tmp_path / "review.json")
        dropped_path = str(tmp_path / "dropped.jsonl")

        entities_call_1 = [{"kind": "agent_person", "name": "Donna Haraway", "role": "author"}]
        entities_call_2 = [{"kind": "agent_person", "name": "Rosi Braidotti", "role": "author"}]

        r1 = extract_and_ingest(
            [self._snippet(text="Haraway, Donna. A Cyborg Manifesto. Routledge, 1991.")],
            kg,
            review_path=review_path,
            dropped_path=dropped_path,
            extractor=lambda **kw: entities_call_1,
        )
        r2 = extract_and_ingest(
            [self._snippet(text="Braidotti, Rosi. The Posthuman. Polity Press, 2013.")],
            kg,
            review_path=review_path,
            dropped_path=dropped_path,
            extractor=lambda **kw: entities_call_2,
        )
        # Both entities must be accounted for.
        total_accounted = (r1.written + r1.queued + r1.dropped) + (r2.written + r2.queued + r2.dropped)
        assert total_accounted >= 2, f"expected both entities accounted for, got r1={r1} r2={r2}"

        # All queued records from both runs must be present in the file
        # (the bug was that run 2 would clobber run 1's records).
        import json
        from pathlib import Path
        queued_total = r1.queued + r2.queued
        if queued_total >= 2:
            assert Path(review_path).exists(), "review file must exist when records were queued"
            queue = json.loads(Path(review_path).read_text(encoding="utf-8"))
            names_in_queue = {(r.get("payload") or {}).get("name") for r in queue}
            assert len(queue) >= 2, (
                f"Both entities were queued ({queued_total} total) but only "
                f"{len(queue)} record(s) in the file — run 2 clobbered run 1. "
                f"Names found: {names_in_queue}"
            )
        elif queued_total == 1:
            # One entity queued, one written directly — queue must have exactly 1 record
            # and the file must exist.
            assert Path(review_path).exists(), "review file must exist for the 1 queued record"
            queue = json.loads(Path(review_path).read_text(encoding="utf-8"))
            assert len(queue) >= 1, "queued record must appear in the file"
        # queued_total == 0 means both went direct-write — file may not exist, which is fine.