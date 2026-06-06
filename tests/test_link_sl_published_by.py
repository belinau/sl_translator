"""Tests for KnowledgeGraph.link_sl_published_by (ontology §3.2).

Mirrors link_published_by: same return semantics, same edge attribute
shape, same self-loop behaviour. Uses in-memory KnowledgeGraph() only
(empty db_path so the on-disk graph is never read).
"""

import pathlib

from translate_core.knowledge_graph import KnowledgeGraph


def _fresh_kg(tmp_path: pathlib.Path) -> KnowledgeGraph:
    # Nonexistent db path → _load() returns early, graph stays empty.
    return KnowledgeGraph(db_path=tmp_path / "empty.db")


def _seed_work_and_publisher(kg: KnowledgeGraph) -> tuple[str, str]:
    src = kg.add_source_text_node("foo-work", title="Foo")
    inst = kg.add_institution_node("foo-publisher", name="Foo Publisher", kind="publisher")
    return src, inst


def test_creates_exactly_one_sl_published_by_edge(tmp_path):
    kg = _fresh_kg(tmp_path)
    src, inst = _seed_work_and_publisher(kg)

    ok = kg.link_sl_published_by("foo-work", "foo-publisher")

    assert ok is True
    sl_edges = [
        (u, v, d)
        for u, v, d in kg.G.edges(data=True)
        if d.get("relation") == "sl_published_by"
    ]
    assert len(sl_edges) == 1
    u, v, _ = sl_edges[0]
    assert u == src and v == inst, "direction must be source_text -> institution"


def test_idempotent_no_duplicate_edge(tmp_path):
    kg = _fresh_kg(tmp_path)
    _seed_work_and_publisher(kg)

    assert kg.link_sl_published_by("foo-work", "foo-publisher") is True
    assert kg.link_sl_published_by("foo-work", "foo-publisher") is True

    sl_edges = [d for *_e, d in kg.G.edges(data=True) if d.get("relation") == "sl_published_by"]
    assert len(sl_edges) == 1


def test_returns_false_on_dangling_node(tmp_path):
    kg = _fresh_kg(tmp_path)
    kg.add_source_text_node("foo-work", title="Foo")
    # institution node intentionally absent

    ok = kg.link_sl_published_by("foo-work", "missing-publisher")

    assert ok is False
    assert not any(
        d.get("relation") == "sl_published_by" for *_e, d in kg.G.edges(data=True)
    )


def test_self_loop_behaviour_matches_link_published_by(tmp_path):
    """Whatever link_published_by does on a self-loop input, the new
    factory must do the same — parity is the acceptance criterion."""
    kg_pub = _fresh_kg(tmp_path / "pub")
    kg_sl = _fresh_kg(tmp_path / "sl")
    # Pre-prefixed identical ids: makes src_node == inst_node, triggering
    # whatever self-loop policy the original implements.
    same_id = "source:loop"
    kg_pub.add_source_text_node("loop", title="Loop")
    kg_sl.add_source_text_node("loop", title="Loop")

    pub_result = kg_pub.link_published_by(same_id, same_id)
    sl_result = kg_sl.link_sl_published_by(same_id, same_id)

    assert pub_result == sl_result, "self-loop return parity"

    pub_self_loops = [
        d for u, v, d in kg_pub.G.edges(data=True)
        if u == v == same_id and d.get("relation") == "published_by"
    ]
    sl_self_loops = [
        d for u, v, d in kg_sl.G.edges(data=True)
        if u == v == same_id and d.get("relation") == "sl_published_by"
    ]
    assert len(pub_self_loops) == len(sl_self_loops), "self-loop creation parity"
