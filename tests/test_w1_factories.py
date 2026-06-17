"""New-behavior tests for the W0/W1 cleanup plan.

- merge_source_text_metadata (W1.1)
- reclassify_node (W1.2)
- commit_as target branches (W1.3)
- flag_dubious (W1.4)
- build_segments_meta + _CONCEPT_SKIP_TYPES routing (W1.5/W1.6)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from translate_core.book_outline import build_segments_meta
from translate_core.knowledge_graph import KnowledgeGraph
from translate_core.kg_review_ops import commit_as
from ui.workspace import _CONCEPT_SKIP_TYPES


def _kg(tmp_path: Path) -> KnowledgeGraph:
    g = KnowledgeGraph(db_path=tmp_path / "kg.json")
    g.save = lambda: None  # type: ignore[method-assign]
    return g


def test_merge_source_text_metadata_accumulates_provenance(tmp_path: Path):
    kg = _kg(tmp_path)
    kg.add_source_text_node(
        "w1",
        title="T",
        footnote_numbers=[3],
        tm_segment_refs=[{"global_idx": 10, "note": "a"}],
    )
    kg.merge_source_text_metadata(
        "w1",
        footnote_numbers=[1, 3],
        tm_segment_refs=[{"global_idx": 10, "note": "a"}, {"global_idx": 11, "note": "b"}],
    )
    node = kg.G.nodes["source:w1"]
    assert node["footnote_numbers"] == [1, 3]
    refs = node["tm_segment_refs"]
    assert {r["global_idx"] for r in refs} == {10, 11}


def test_merge_source_text_metadata_no_op_when_missing(tmp_path: Path):
    kg = _kg(tmp_path)
    # Does not raise; simply returns early.
    kg.merge_source_text_metadata("source:missing", footnote_numbers=[1])
    assert not kg.G.has_node("source:missing")


def test_reclassify_node_migrates_edges_and_removes_old(tmp_path: Path):
    kg = _kg(tmp_path)
    kg.add_term_node("x", "en")
    kg.add_term_node("a", "en")
    kg.add_term_node("b", "en")
    kg.G.add_edge("term:x", "term:a", relation="test")
    kg.G.add_edge("term:a", "term:b", relation="test")

    assert kg.reclassify_node("term:a", "term:b") is True
    assert not kg.G.has_node("term:a")
    assert kg.G.has_edge("term:x", "term:b")
def test_reclassify_node_same_id_is_no_op(tmp_path: Path):
    kg = _kg(tmp_path)
    kg.add_term_node("a", "en")
    assert kg.reclassify_node("term:a", "term:a") is False
    # add_term_node may have linked the term to a concept that was merged away;
    # the term node itself must survive the no-op.
    assert kg.G.has_node("term:en:a")

def test_build_segments_meta_classifies_prose_and_apparatus():
    segments = [
        {"source": "Urban Belina is a Slovenian theatre director and dramaturg.", "target": ""},
        {"source": "Foucault, Michel. Discipline and Punish. Pantheon, 1977.", "target": ""},
    ]
    meta = build_segments_meta(segments)
    assert len(meta) == len(segments)
    assert meta[0]["index"] == 0
    assert meta[1]["index"] == 1

    prose_type = meta[0]["type"]
    apparatus_type = meta[1]["type"]
    assert prose_type not in _CONCEPT_SKIP_TYPES, f"prose classified as {prose_type}"
    assert apparatus_type in _CONCEPT_SKIP_TYPES, f"apparatus classified as {apparatus_type}"

def test_commit_as_cited_work_does_not_create_concept(tmp_path: Path):
    kg = _kg(tmp_path)
    err, nid = commit_as(kg, "cited_work", {
        "title_orig": "Foo",
        "year": "2000",
        "author": "Original Author",
    })
    assert err is None
    assert nid == "source:foo"
    assert not any(n.startswith("concept:") for n in kg.G.nodes)


def test_commit_as_concept_branch(tmp_path: Path):
    kg = _kg(tmp_path)
    err, nid = commit_as(kg, "concept", {"label": "Bar", "domain": "test"})
    assert err is None
    assert nid == "concept:bar"
    assert kg.G.nodes["concept:bar"].get("label") == "Bar"


def test_commit_as_concept_requires_label(tmp_path: Path):
    kg = _kg(tmp_path)
    err, nid = commit_as(kg, "concept", {"domain": "test"})
    assert err == "Label is required."
    assert nid is None


def test_flag_dubious_returns_node_items_and_respects_dismissed(tmp_path: Path):
    from scripts.flag_kg_review import flag_dubious

    nodes = [
        {"id": "agent:bad", "type": "agent"},  # missing required fields
        {"id": "source:empty", "type": "source_text"},  # no title
        {"id": "agent:good", "type": "agent", "name": "N", "role": "author",
         "dedup_group": "dg", "alt_spellings": ["N"], "all_roles": ["author"], "mention_count": 1},
    ]
    edges = []
    items = flag_dubious(nodes, edges, dismissed=set())
    reasons = {it["reason"] for it in items}
    assert {"agent_missing_required", "source_no_title"}.issubset(reasons)
    assert all("id" in it and "type" in it for it in items)

    items = flag_dubious(nodes, edges, dismissed={"agent:bad"})
    assert not any(it["id"] == "agent:bad" for it in items)


def test_build_segments_meta_classifies_prose_and_apparatus():
    segments = [
        {"source": "Urban Belina is a Slovenian theatre director and dramaturg.", "target": ""},
        {"source": "Foucault, Michel. Discipline and Punish. Pantheon, 1977.", "target": ""},
    ]
    meta = build_segments_meta(segments)
    assert len(meta) == len(segments)
    assert meta[0]["index"] == 0
    assert meta[1]["index"] == 1

    prose_type = meta[0]["type"]
    apparatus_type = meta[1]["type"]
    assert prose_type not in _CONCEPT_SKIP_TYPES, f"prose classified as {prose_type}"
    assert apparatus_type in _CONCEPT_SKIP_TYPES, f"apparatus classified as {apparatus_type}"


def test_build_segments_meta_handles_flat_docx():
    segments = [{"source": f"Paragraph {i}.", "target": ""} for i in range(3)]
    meta = build_segments_meta(segments)
    assert len(meta) == 3
    assert all("type" in m for m in meta)
