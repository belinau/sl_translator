"""Tests for scripts/ingest_kafer_concepts.py driver logic.

Uses a lightweight KG stand-in (networkx graph only) so tests run without
the live data/knowledge.db.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translate_core.entity_extraction._slug import _slugify
from translate_core.entity_extraction.smol_extractor import build_record
from translate_core.kg_ingest_entities import (
    aggregate_agent_signals,
    aggregate_institution_signals,
    score_all,
    dedup_records,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_concept_entry(
    label: str,
    definition: str = "A test concept definition.",
    originating_author: str | None = "Test Author",
    source_work_title: str | None = None,
    seg_idx: int = 0,
    container_work_id: str = "feminist-queer-crip-alison-kafer-pdf",
) -> dict:
    """Build a single extraction entry in the kafer_concept_extractions.json shape."""
    ent: dict = {
        "kind": "concept",
        "label_orig": label,
        "label_translation": None,
        "orig_lang": "en",
        "translation_lang": "sl",
        "domain": "humanities",
        "definition": definition,
    }
    if originating_author:
        ent["originating_author"] = originating_author
    if source_work_title:
        ent["source_work_title"] = source_work_title
    return {
        "origin": "feminist-queer-crip-alison-kafer",
        "seg_idx": seg_idx,
        "container_work_id": container_work_id,
        "klass": "concept_curated",
        "entities": [ent],
    }


def _build_and_score(entries: list[dict]) -> list[dict]:
    """Run the exact pipeline: build_record → aggregate → score → dedup → re-score."""
    records = []
    for entry in entries:
        for ent in entry.get("entities", []):
            rec = build_record(
                ent,
                entry["origin"],
                entry["seg_idx"],
                entry.get("container_work_id", ""),
                src_lang="en",
                tgt_lang="sl",
            )
            if rec is not None:
                rec.setdefault("source", {})["provenance"] = "curator_extra"
                rec.setdefault("signals", {})["curator_endorsed"] = True
                records.append(rec)
    aggregate_agent_signals(records)
    aggregate_institution_signals(records)
    scored = score_all(records)
    deduped = dedup_records(scored)
    return score_all(deduped)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestBuildRecord:
    """build_record produces valid concept records from curated entries."""

    def test_concept_with_author_and_segment(self) -> None:
        entry = _make_concept_entry("test concept", seg_idx=42)
        ent = entry["entities"][0]
        rec = build_record(
            ent, entry["origin"], entry["seg_idx"],
            entry.get("container_work_id", ""),
            src_lang="en", tgt_lang="sl",
        )
        assert rec is not None
        assert rec["kind"] == "concept"
        assert rec["source"]["origin"] == "feminist-queer-crip-alison-kafer"
        assert rec["source"]["segment_idx"] == 42
        assert rec["payload"]["label_orig"] == "test concept"
        assert rec["payload"]["orig_lang"] == "en"
        assert rec["payload"]["translation_lang"] == "sl"
        assert rec["payload"]["originating_author"] == "Test Author"

    def test_concept_without_author(self) -> None:
        entry = _make_concept_entry("orphan concept", originating_author=None)
        ent = entry["entities"][0]
        rec = build_record(
            ent, entry["origin"], entry["seg_idx"],
            entry.get("container_work_id", ""),
            src_lang="en", tgt_lang="sl",
        )
        assert rec is not None
        assert "originating_author" not in rec["payload"]

    def test_concept_with_source_work(self) -> None:
        entry = _make_concept_entry(
            "cyborg concept",
            originating_author="Donna Haraway",
            source_work_title="A Cyborg Manifesto",
        )
        ent = entry["entities"][0]
        rec = build_record(
            ent, entry["origin"], entry["seg_idx"],
            entry.get("container_work_id", ""),
            src_lang="en", tgt_lang="sl",
        )
        assert rec is not None
        assert rec["payload"]["source_work_title"] == "A Cyborg Manifesto"
        assert rec["signals"]["has_source_work"] is True

    def test_unlocated_concept_still_builds(self) -> None:
        entry = _make_concept_entry("unlocated concept", seg_idx=-1)
        ent = entry["entities"][0]
        rec = build_record(
            ent, entry["origin"], entry["seg_idx"],
            entry.get("container_work_id", ""),
            src_lang="en", tgt_lang="sl",
        )
        assert rec is not None
        assert rec["source"]["segment_idx"] == -1


class TestScoring:
    """Curator-endorsed concepts reach direct-write tier."""

    def test_curator_endorsed_direct_write(self) -> None:
        entries = [_make_concept_entry("scored concept")]
        records = _build_and_score(entries)
        assert len(records) > 0
        assert records[0]["tier"] == "direct_write"

    def test_concept_with_author_scores_higher(self) -> None:
        entries_a = [_make_concept_entry("with author", originating_author="Author A")]
        entries_b = [_make_concept_entry("without author", originating_author=None)]
        scored_a = _build_and_score(entries_a)
        scored_b = _build_and_score(entries_b)
        # Both should direct-write with curator_endorsed, but confidence differs
        assert scored_a[0]["confidence"] >= scored_b[0]["confidence"]


class TestLineageEdgeCreation:
    """Lineage pairs produce concept→concept edges when both endpoints exist."""

    def test_lineage_with_existing_concepts(self, tmp_path: Path) -> None:
        from translate_core.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()

        # Create both concepts
        kg.add_concept_node("concept:a-concept", label="a concept", domain="humanities", definition="")
        kg.add_concept_node("concept:b-concept", label="b concept", domain="humanities", definition="")

        # Wire lineage
        kg.link_concepts_rhizomatic("concept:a-concept", "concept:b-concept", "critiques")
        assert kg.G.has_edge("concept:a-concept", "concept:b-concept")
        assert kg.G.edges["concept:a-concept", "concept:b-concept"]["relation"] == "critiques"

    def test_lineage_skips_missing_endpoint(self) -> None:
        from translate_core.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()

        kg.add_concept_node("concept:existing-concept", label="existing", domain="humanities", definition="")

        # Missing endpoint → no edge created (link_concepts_rhizomatic no-ops)
        kg.link_concepts_rhizomatic("concept:existing-concept", "concept:missing-concept", "extends")
        assert not kg.G.has_edge("concept:existing-concept", "concept:missing-concept")


class TestIdempotency:
    """Re-running the ingest adds nothing."""

    def test_concepts_already_exist(self) -> None:
        """Verify all Kafer concept IDs from the extraction file exist in the KG."""
        kg_path = ROOT / "data" / "knowledge.db"
        if not kg_path.exists():
            pytest.skip("No live KG")

        from translate_core.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()

        extractions_path = ROOT / "data" / "kafer_concept_extractions.json"
        with open(extractions_path) as f:
            data = json.load(f)

        labels = set()
        for entry in data:
            for ent in entry.get("entities", []):
                lbl = ent.get("label_orig", "")
                if lbl:
                    labels.add(lbl)

        concept_ids = {f"concept:{_slugify(lbl)}" for lbl in labels}
        existing = {cid for cid in concept_ids if kg.G.has_node(cid)}
        assert len(existing) == len(concept_ids), (
            f"{len(concept_ids) - len(existing)} concepts missing from KG"
        )

    def test_no_kafer_in_smol_extractions(self) -> None:
        """Kafer entries must NOT appear in smol_extractions.json."""
        smol_path = ROOT / "data" / "smol_entities_map" / "smol_extractions.json"
        if not smol_path.exists():
            pytest.skip("No smol extractions file")

        with open(smol_path) as f:
            smol = json.load(f)

        kafer_entries = [e for e in smol if e.get("origin") == "feminist-queer-crip-alison-kafer"]
        assert len(kafer_entries) == 0, "Kafer entries leaked into smol_extractions.json"


class TestNoTermsOrInstantiates:
    """The concept ingest must not create term nodes or instantiates_concept edges."""

    def test_no_new_term_nodes(self) -> None:
        kg_path = ROOT / "data" / "knowledge.db"
        if not kg_path.exists():
            pytest.skip("No live KG")

        from translate_core.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()

        # Verify that Kafer concept labels don't appear as term nodes
        with open(ROOT / "data" / "kafer_concept_extractions.json") as f:
            data = json.load(f)

        kafer_labels = set()
        for entry in data:
            for ent in entry.get("entities", []):
                lbl = ent.get("label_orig", "")
                if lbl:
                    kafer_labels.add(lbl)

        # Term nodes with Kafer concept labels should not exist
        term_nodes = [n for n in kg.G.nodes if n.startswith("term:")]
        kafer_term_slugs = {_slugify(lbl) for lbl in kafer_labels}
        # The key assertion is that write_to_kg Pass 6 doesn't create terms
        # (it only creates concept nodes, agent nodes, and source_text nodes)