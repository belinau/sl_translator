# tests/test_document_aligner.py
#
# End-to-end tests for the modernized document-pair pipeline + the new
# Document Aligner entry point. Mirrors tests/test_document_pair_pipeline.py
# and tests/test_citation_collector.py (fake extractor → no Ollama needed).
#
# Proves:
#   1. .docx now dispatches to docx_to_markdown (Step 1) — previously raised.
#   2. process_pair routes KG submit through extract_and_ingest (Step 2):
#      cited_work written, cited_in edge to container, no doc_pair nodes.
#   3. The TMX viewer reads the produced TMX via the existing parser
#      (Step 3e viewer path — no custom parser).

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ======================================================================
# Step 1 — .docx dispatch in to_markdown_with_meta
# ======================================================================


class TestDocxDispatch:
    def test_docx_dispatch_parses(self):
        """to_markdown_with_meta routes .docx to docx_to_markdown.

        Previously MarkItDown's DocxConverter raised FileConversionException
        (the uninstalled optional `mammoth`). Now the .docx branch returns
        the zipfile-based parser output without raising.
        """
        from translate_core.doc_parser import DocumentParser

        docx_path = Path("data/articles/Lumerai, the Mother Snake.docx")
        if not docx_path.exists():
            pytest.skip(f"fixture missing: {docx_path}")
        md, meta = DocumentParser().to_markdown_with_meta(docx_path)
        assert isinstance(md, str)
        assert isinstance(meta, list)
        # docx_to_markdown emits [^N] footnote refs/defs for academic DOCX;
        # an empty body would also be a regression signal, so assert non-empty.
        assert md.strip() != ""


# ======================================================================
# Step 2 — process_pair routes KG submit through extract_and_ingest
# ======================================================================


@pytest.fixture
def kg(tmp_path, monkeypatch):
    from translate_core.knowledge_graph import KnowledgeGraph
    import config

    # Redirect the TM store into the test tmp dir so process_pair's TMX
    # write lands in tmp_path, not the real data/tm/.
    monkeypatch.setattr(config, "TM_DIR", tmp_path / "tm")
    (tmp_path / "tm").mkdir(parents=True, exist_ok=True)
    g = KnowledgeGraph(db_path=tmp_path / "kg.json")
    # Never touch data/knowledge.db.
    g.save = lambda: None  # type: ignore[method-assign]
    return g


def _write_footnote_md(path: Path, footnote_text: str) -> None:
    """Write a one-footnote markdown file the matcher can pair."""
    path.write_text(
        f"Body paragraph one.\n\n[^1]: {footnote_text}\n",
        encoding="utf-8",
    )


class TestProcessPairRoutesThroughExtractAndIngest:
    def test_cited_work_written_and_cited_in_wired(self, kg, tmp_path):
        """A matched bilingual pair routes through extract_and_ingest and
        produces a direct-write cited_work + cited_in edge to the container.

        Uses a fake extractor returning one cited_work entity (mirrors the
        test_citation_collector.TestExtractAndIngestWiring idiom — no Ollama).
        """
        from translate_core.document_pair_pipeline import process_pair

        en_path = tmp_path / "en.md"
        sl_path = tmp_path / "sl.md"
        _write_footnote_md(en_path, "Deleuze, Gilles. *Anti-Oedipus*. Continuum, 1972.")
        _write_footnote_md(sl_path, "Deleuze, Gilles. *Anti-Oedipus*. Continuum, 1972.")

        # COBISS-style container (the aligner picker's filter set).
        cid_full = kg.add_source_text_node(
            "test-cont", "Container", provenance="cobiss_personal", kind="translated_work",
        )
        container_node = "source:test-cont"  # add_source_text_node lowercases the slug

        def fake_extractor(**kw):
            return [
                {
                    "kind": "cited_work",
                    "author": "Gilles Deleuze",
                    "title_orig": "Anti-Oedipus",
                    "title_translation": "Anti-Oedip",
                    "orig_lang": "en",
                    "translation_lang": "sl",
                    "year": 1972,
                }
            ]

        result = process_pair(
            en_path, sl_path, cid_full, kg, extractor=fake_extractor, dry_run=False,
        )

        # Matching: one bilingual pair (identical title → score 1.0 ≥ 0.6).
        assert result.n_bilingual >= 1, (
            f"expected ≥1 bilingual match, got n_bilingual={result.n_bilingual}"
        )

        # KG submit routed through extract_and_ingest → IngestReport present.
        assert result.ingest_report is not None
        assert result.ingest_report.written >= 1, (
            f"expected ≥1 direct-write, got report={result.ingest_report}"
        )

        # TMX produced next to the EN doc.
        assert result.tmx_path is not None and result.tmx_path.exists(), (
            f"tmx_path missing: {result.tmx_path}"
        )

        # cited_in edge from the written cited_work → container exists.
        cited_in_edges = [
            (u, v, d) for u, v, d in kg.G.edges(data=True)
            if d.get("relation") == "cited_in"
        ]
        assert any(v == container_node for _, v, _ in cited_in_edges), (
            f"no cited_in edge into container {container_node}; "
            f"edges={cited_in_edges}"
        )

        # The dead direct-write path is gone: no source_text carries the
        # legacy doc_pair provenance stamp.
        doc_pair_nodes = [
            nid for nid, nd in kg.G.nodes(data=True)
            if nd.get("type") == "source_text" and nd.get("provenance") == "doc_pair"
        ]
        assert doc_pair_nodes == [], (
            f"found doc_pair-provenance source_text nodes (dead path not removed): "
            f"{doc_pair_nodes}"
        )

    def test_dry_run_writes_nothing(self, kg, tmp_path):
        from translate_core.document_pair_pipeline import process_pair

        en_path = tmp_path / "en.md"
        sl_path = tmp_path / "sl.md"
        _write_footnote_md(en_path, "Deleuze, Gilles. *Anti-Oedipus*. Continuum, 1972.")
        _write_footnote_md(sl_path, "Deleuze, Gilles. *Anti-Oedipus*. Continuum, 1972.")
        kg.add_source_text_node(
            "test-cont", "Container", provenance="cobiss_personal", kind="translated_work",
        )

        result = process_pair(
            en_path, sl_path, "source:test-cont", kg,
            extractor=lambda **kw: [{"kind": "cited_work", "title_orig": "x"}],
            dry_run=True,
        )
        assert result.ingest_report is None
        assert result.tmx_path is None
        assert result.n_bilingual >= 1  # matching still runs

    def test_empty_pair_snippets_safe(self, kg, tmp_path):
        """A pair with no footnotes → extract_and_ingest([]) returns all-zero
        IngestReport (no smol calls, no crash)."""
        from translate_core.document_pair_pipeline import process_pair

        en_path = tmp_path / "en.md"
        sl_path = tmp_path / "sl.md"
        en_path.write_text("Just body text, no footnotes.", encoding="utf-8")
        sl_path.write_text("Samo telo, brez opomb.", encoding="utf-8")
        kg.add_source_text_node(
            "test-cont", "Container", provenance="cobiss_personal", kind="translated_work",
        )

        calls: list = []

        def fake_extractor(**kw):
            calls.append(kw)
            return []

        result = process_pair(
            en_path, sl_path, "test-cont", kg, extractor=fake_extractor, dry_run=False,
        )
        assert result.n_bilingual == 0
        assert result.ingest_report is not None
        assert result.ingest_report.written == 0
        assert result.ingest_report.errors == 0
        # No snippets → extractor never called.
        assert calls == []




# ======================================================================
# Add new container — curator_extra path (radio/podcast/non-COBISS works)
# ======================================================================


class TestAddNewContainer:
    """A curator-added container (provenance=curator_extra, kind=translated_work,
    with a translated_by edge per O-20) is a valid cited_in target for the
    modernized pipeline and is surfaced by the aligner's broadened picker."""

    def _make_curator_container(self, kg, title, translator="Urban Belina"):
        from translate_core.entity_extraction._slug import _slugify
        from translate_core.entity_extraction.ingest_helpers import ensure_agent

        slug = _slugify(title)
        kg.add_source_text_node(
            slug, title,
            provenance="curator_extra",
            kind="translated_work",
            project_type="book_translation",
        )
        t_slug = ensure_agent(kg, translator, role="translator")
        kg.link_translated_by(slug, t_slug)
        return slug

    def test_curator_container_has_translated_by(self, kg):
        """O-20: every container node must have a translated_by edge."""
        slug = self._make_curator_container(kg, "Lumerai the Mother Snake")
        node_id = f"source:{slug}"
        assert kg.G.has_node(node_id)
        # The translated_by edge points source → agent.
        has_edge = any(
            u == node_id and d.get("relation") == "translated_by"
            for u, v, d in kg.G.edges(data=True)
        )
        assert has_edge, f"container {node_id} missing translated_by edge"
        # Node carries the curator_extra provenance + translated_work kind.
        nd = kg.G.nodes[node_id]
        assert nd.get("provenance") == "curator_extra"
        assert nd.get("kind") == "translated_work"

    def test_curator_container_surfaced_by_picker_filter(self, kg):
        """The aligner's broadened picker surfaces curator_extra containers."""
        self._make_curator_container(kg, "Lumerai the Mother Snake")
        all_src = kg.get_all_by_type("source_text") or []
        surfaced = [
            s for s in all_src
            if s.get("kind") == "translated_work"
            and s.get("provenance") in {"cobiss_personal", "curator_extra"}
        ]
        assert any(s.get("provenance") == "curator_extra" for s in surfaced)

    def test_picker_surfaces_new_container_among_many(self, kg):
        """Regression: with 500+ translated_work containers (the live KG has
        553), a just-created container must still appear in the picker's
        capped result and stay selectable. The original bug capped at 50
        in insertion order, so a node added last was cut off and the select
        raised ValueError: Invalid value — crashing the whole page render.

        This test mirrors ``ui/aligner._candidates`` logic (recent-first
        sort + pin selected) against a KG with >50 containers.
        """
        # Seed 60 older cobiss_personal containers, then 1 new curator_extra.
        for i in range(60):
            kg.add_source_text_node(
                f"old-cont-{i}", f"Old Container {i}",
                provenance="cobiss_personal", kind="translated_work",
                project_type="book_translation",
            )
        new_slug = self._make_curator_container(kg, "Brand New Radio Piece")
        new_node_id = f"source:{new_slug}"

        all_src = kg.get_all_by_type("source_text") or []
        filtered = [
            s for s in all_src
            if s.get("kind") == "translated_work"
            and s.get("provenance") in {"cobiss_personal", "curator_extra"}
        ]
        # Recent-first sort (mirrors _candidates).
        filtered.sort(key=lambda s: s.get("created_at") or "", reverse=True)
        top = filtered[:50]
        # Pin the selected container.
        selected = new_node_id
        if selected and selected not in {s["id"] for s in top}:
            sel_node = next((s for s in filtered if s["id"] == selected), None)
            if sel_node is not None:
                top.append(sel_node)

        top_ids = {s["id"] for s in top}
        # The new container is the most recent → sorted to the front, so it
        # is within the cap even without pinning.
        assert new_node_id in top_ids, (
            f"new container {new_node_id} lost past the 50-cap; "
            f"top has {len(top)} entries, first={top[0]['id'] if top else None}"
        )
        # And the select value would be valid (the original crash).
        assert selected in top_ids

    def test_curator_container_is_valid_cited_in_target(self, kg, tmp_path):
        """A cited_work written via extract_and_ingest wires cited_in into a
        curator_extra container — the non-COBISS use case end-to-end."""
        from translate_core.document_pair_pipeline import process_pair

        en_path = tmp_path / "en.md"
        sl_path = tmp_path / "sl.md"
        _write_footnote_md(en_path, "Deleuze, Gilles. *Anti-Oedipus*. Continuum, 1972.")
        _write_footnote_md(sl_path, "Deleuze, Gilles. *Anti-Oedipus*. Continuum, 1972.")
        slug = self._make_curator_container(kg, "Lumerai the Mother Snake")
        container_node = f"source:{slug}"

        def fake_extractor(**kw):
            return [
                {
                    "kind": "cited_work",
                    "author": "Gilles Deleuze",
                    "title_orig": "Anti-Oedipus",
                    "title_translation": "Anti-Oedip",
                    "orig_lang": "en",
                    "translation_lang": "sl",
                    "year": 1972,
                }
            ]

        result = process_pair(
            en_path, sl_path, slug, kg, extractor=fake_extractor, dry_run=False,
        )
        assert result.n_bilingual >= 1
        assert result.ingest_report is not None
        assert result.ingest_report.written >= 1, f"report={result.ingest_report}"
        cited_in = [
            (u, v, d) for u, v, d in kg.G.edges(data=True)
            if d.get("relation") == "cited_in"
        ]
        assert any(v == container_node for _, v, _ in cited_in), (
            f"no cited_in edge into curator_extra container {container_node}; "
            f"edges={cited_in}"
        )

# ======================================================================
# Step 3e — TMX viewer reads pairs (existing parser, no custom code)
# ======================================================================


class TestTmxViewerReadsPairs:
    def test_tmx_viewer_reads_pairs(self, kg, tmp_path):
        """The TMX produced by process_pair is readable by the existing
        ``read_tmx_with_timecodes`` parser (the parser
        TranslationMemory._load_tmx delegates to) — no new parser."""
        from translate_core.document_pair_pipeline import process_pair
        from translate_core.tm_timecodes import read_tmx_with_timecodes

        en_path = tmp_path / "en.md"
        sl_path = tmp_path / "sl.md"
        _write_footnote_md(en_path, "Deleuze, Gilles. *Anti-Oedipus*. Continuum, 1972.")
        _write_footnote_md(sl_path, "Deleuze, Gilles. *Anti-Oedipus*. Continuum, 1972.")
        kg.add_source_text_node(
            "test-cont", "Container", provenance="cobiss_personal", kind="translated_work",
        )

        result = process_pair(
            en_path, sl_path, "test-cont", kg,
            extractor=lambda **kw: [], dry_run=False,
        )
        assert result.tmx_path is not None

        entries = read_tmx_with_timecodes(result.tmx_path)
        assert len(entries) >= 1, f"expected ≥1 TU, got {len(entries)}"
        for e in entries:
            assert (e.get("source") or "").strip() != ""
            assert (e.get("target") or "").strip() != ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])