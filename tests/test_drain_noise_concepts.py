"""Tests for the Phase 9 noise-concept drain.

Covers:
  * `is_noise_concept(node_id, kg) -> bool` predicate in
    `scripts/drain_noise_concepts.py`.
  * `KnowledgeGraph.remove_concept_node(concept_id) -> bool` factory.

NO live KG access. All graphs are synthesised in-memory under tmp_path.

Predicate test strategy: `is_noise_concept` only reads `kg.G`, so we use a
tiny synthetic stand-in (`_FakeKG`) wrapping a bare `networkx.DiGraph`.
This avoids the ~5 s spacy/classla load that real `KnowledgeGraph()`
incurs in `__init__`.

Factory test strategy: `remove_concept_node` is a real method on
`KnowledgeGraph`, so those tests instantiate `KnowledgeGraph` against a
non-existent db path (load is a no-op). One session-scoped instance is
shared across the factory tests; each test mutates and then cleans the
graph itself."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict

import networkx as nx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from translate_core.knowledge_graph import KnowledgeGraph  # noqa: E402


# ---------------------------------------------------------------------------
# Load scripts/drain_noise_concepts.py as a module via importlib (the
# `scripts/` directory has no __init__.py, so a plain `import scripts.x`
# won't work portably).
# ---------------------------------------------------------------------------
_DRAIN_PATH = ROOT / "scripts" / "drain_noise_concepts.py"
_spec = importlib.util.spec_from_file_location("drain_noise_concepts", _DRAIN_PATH)
assert _spec and _spec.loader, "could not load drain_noise_concepts"
drain_noise_concepts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drain_noise_concepts)
is_noise_concept = drain_noise_concepts.is_noise_concept


# ---------------------------------------------------------------------------
# Synthetic stand-in for predicate tests.
# ---------------------------------------------------------------------------
class _FakeKG:
    """Minimal KG shim exposing `.G` — enough for `is_noise_concept`."""

    def __init__(self) -> None:
        self.G: nx.DiGraph = nx.DiGraph()

    def add_concept(self, cid: str, **attrs: Any) -> str:
        data: Dict[str, Any] = {"id": cid, "type": "concept", "label": cid,
                                "definition": ""}
        data.update(attrs)
        self.G.add_node(cid, **data)
        return cid

    def add_term(self, tid: str, lang: str = "en", **attrs: Any) -> str:
        data: Dict[str, Any] = {"id": tid, "type": "term", "term": tid,
                                "lang": lang}
        data.update(attrs)
        self.G.add_node(tid, **data)
        return tid

    def link(self, src: str, tgt: str, relation: str) -> None:
        self.G.add_edge(src, tgt, relation=relation)


@pytest.fixture
def fkg() -> _FakeKG:
    return _FakeKG()


# ---------------------------------------------------------------------------
# is_noise_concept — case (a): noise when empty def + no lineage + no author.
# ---------------------------------------------------------------------------
def test_is_noise_concept_true_when_empty_def_no_lineage_no_author(fkg: _FakeKG) -> None:
    cid = fkg.add_concept("concept:lonely", definition="")
    # no edges, no originating_author
    assert is_noise_concept(cid, fkg) is True


def test_is_noise_concept_true_when_only_unrelated_edges(fkg: _FakeKG) -> None:
    """`instantiates_concept` from a single term is NOT a lineage edge and
    doesn't save a concept that's otherwise empty (the term-attestation
    rescue requires count > 5 — see case (e))."""
    cid = fkg.add_concept("concept:meh", definition="")
    tid = fkg.add_term("term:en:meh")
    fkg.link(tid, cid, "instantiates_concept")
    assert is_noise_concept(cid, fkg) is True


# ---------------------------------------------------------------------------
# is_noise_concept — case (b): non-empty definition → keep.
# ---------------------------------------------------------------------------
def test_is_noise_concept_false_when_definition_present(fkg: _FakeKG) -> None:
    cid = fkg.add_concept("concept:rich", definition="A real definition.")
    assert is_noise_concept(cid, fkg) is False


def test_is_noise_concept_false_when_definition_whitespace_only_treated_as_empty(
    fkg: _FakeKG,
) -> None:
    """A definition that's nothing but whitespace should NOT count as a
    real definition — same as missing."""
    cid = fkg.add_concept("concept:blank", definition="   \n  ")
    assert is_noise_concept(cid, fkg) is True


# ---------------------------------------------------------------------------
# is_noise_concept — case (c): participates in ANY lineage edge → keep.
# Lineage relations: {extends, critiques, redefines, reappropriates,
# related_to, attributed_to}. Both directions count.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "relation",
    ["extends", "critiques", "redefines", "reappropriates",
     "related_to", "attributed_to"],
)
def test_is_noise_concept_false_when_outgoing_lineage_edge(
    fkg: _FakeKG, relation: str,
) -> None:
    a = fkg.add_concept("concept:a", definition="")
    b = fkg.add_concept("concept:b", definition="")
    fkg.link(a, b, relation)
    # `a` has the outgoing lineage edge → keep `a`.
    assert is_noise_concept(a, fkg) is False


@pytest.mark.parametrize(
    "relation",
    ["extends", "critiques", "redefines", "reappropriates",
     "related_to", "attributed_to"],
)
def test_is_noise_concept_false_when_incoming_lineage_edge(
    fkg: _FakeKG, relation: str,
) -> None:
    a = fkg.add_concept("concept:a", definition="")
    b = fkg.add_concept("concept:b", definition="")
    fkg.link(a, b, relation)
    # `b` has the incoming lineage edge → keep `b`.
    assert is_noise_concept(b, fkg) is False


# ---------------------------------------------------------------------------
# is_noise_concept — case (d): `originating_author` field → keep.
# ---------------------------------------------------------------------------
def test_is_noise_concept_false_when_originating_author_present(fkg: _FakeKG) -> None:
    cid = fkg.add_concept(
        "concept:authored",
        definition="",
        originating_author="agent:foucault",
    )
    assert is_noise_concept(cid, fkg) is False


def test_is_noise_concept_true_when_originating_author_empty_string(fkg: _FakeKG) -> None:
    """An empty `originating_author` field is no provenance signal."""
    cid = fkg.add_concept("concept:phantom", definition="", originating_author="")
    assert is_noise_concept(cid, fkg) is True


# ---------------------------------------------------------------------------
# is_noise_concept — case (e): term-attestation rescue.
# Threshold is strictly > 5 (i.e. 6 incoming `instantiates_concept` edges
# from term nodes keeps the concept; 5 still counts as noise).
# ---------------------------------------------------------------------------
def _wire_term_attestations(fkg: _FakeKG, cid: str, n: int) -> None:
    for i in range(n):
        tid = fkg.add_term(f"term:en:t{i}")
        fkg.link(tid, cid, "instantiates_concept")


def test_is_noise_concept_true_at_threshold_boundary_5_attestations(fkg: _FakeKG) -> None:
    cid = fkg.add_concept("concept:five", definition="")
    _wire_term_attestations(fkg, cid, 5)
    # threshold is `> 5` so exactly five attestations is still noise
    assert is_noise_concept(cid, fkg) is True


def test_is_noise_concept_false_when_six_term_attestations(fkg: _FakeKG) -> None:
    cid = fkg.add_concept("concept:six", definition="")
    _wire_term_attestations(fkg, cid, 6)
    assert is_noise_concept(cid, fkg) is False


def test_is_noise_concept_false_when_many_term_attestations(fkg: _FakeKG) -> None:
    cid = fkg.add_concept("concept:popular", definition="")
    _wire_term_attestations(fkg, cid, 25)
    assert is_noise_concept(cid, fkg) is False


def test_is_noise_concept_ignores_attestations_from_non_term_predecessors(
    fkg: _FakeKG,
) -> None:
    """The rescue only counts `instantiates_concept` edges *from term
    nodes*. Edges from other node types must not inflate the count."""
    cid = fkg.add_concept("concept:weird", definition="")
    # 6 incoming instantiates_concept edges, but the sources are NOT terms
    for i in range(6):
        nid = f"concept:other{i}"
        fkg.G.add_node(nid, id=nid, type="concept", definition="")
        fkg.link(nid, cid, "instantiates_concept")
    assert is_noise_concept(cid, fkg) is True


# ---------------------------------------------------------------------------
# is_noise_concept — robustness: missing node / wrong type.
# ---------------------------------------------------------------------------
def test_is_noise_concept_false_when_node_missing(fkg: _FakeKG) -> None:
    """A missing node can't be noise to remove (there's nothing there)."""
    assert is_noise_concept("concept:ghost", fkg) is False


def test_is_noise_concept_false_when_node_is_not_a_concept(fkg: _FakeKG) -> None:
    tid = fkg.add_term("term:en:foo")
    assert is_noise_concept(tid, fkg) is False


# ---------------------------------------------------------------------------
# remove_concept_node factory tests.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def kg(tmp_path_factory: pytest.TempPathFactory) -> KnowledgeGraph:
    """One real KG instance reused across the factory tests. The db path
    points at a non-existent file, so `_load` is a no-op. NLP pipelines
    load once (~5 s) per pytest session for this module."""
    tmp = tmp_path_factory.mktemp("kg")
    return KnowledgeGraph(db_path=tmp / "drain.db")


@pytest.fixture(autouse=False)
def clean_kg(kg: KnowledgeGraph) -> KnowledgeGraph:
    """Yield the shared KG with an empty graph, then clear after use."""
    kg.G.clear()
    return kg


def test_remove_concept_node_removes_node_and_returns_true(
    clean_kg: KnowledgeGraph,
) -> None:
    clean_kg.G.add_node("concept:x", id="concept:x", type="concept",
                        label="X", definition="")
    assert clean_kg.remove_concept_node("concept:x") is True
    assert not clean_kg.G.has_node("concept:x")


def test_remove_concept_node_cleans_incident_instantiates_concept_edges(
    clean_kg: KnowledgeGraph,
) -> None:
    """All edges touching the deleted concept go with it — incoming
    `instantiates_concept` edges from term nodes must not survive as
    dangling edges (their target node is gone)."""
    clean_kg.G.add_node("concept:doomed", id="concept:doomed", type="concept",
                        label="Doomed", definition="")
    for i in range(3):
        tid = f"term:en:word{i}"
        clean_kg.G.add_node(tid, id=tid, type="term", term=f"word{i}", lang="en")
        clean_kg.G.add_edge(tid, "concept:doomed", relation="instantiates_concept")

    assert clean_kg.remove_concept_node("concept:doomed") is True

    # Concept gone
    assert not clean_kg.G.has_node("concept:doomed")
    # Term nodes preserved
    for i in range(3):
        assert clean_kg.G.has_node(f"term:en:word{i}")
    # No edges referencing the deleted concept (incoming or outgoing) survive.
    for u, v, _d in clean_kg.G.edges(data=True):
        assert u != "concept:doomed" and v != "concept:doomed"
    # No dangling edge: every edge endpoint is still in the graph
    for u, v, _d in clean_kg.G.edges(data=True):
        assert clean_kg.G.has_node(u)
        assert clean_kg.G.has_node(v)


def test_remove_concept_node_cleans_outgoing_lineage_edges(
    clean_kg: KnowledgeGraph,
) -> None:
    """If a concept has outgoing lineage edges to OTHER concepts, removing
    it must drop those edges too (no dangling sources)."""
    clean_kg.G.add_node("concept:a", id="concept:a", type="concept",
                        label="A", definition="")
    clean_kg.G.add_node("concept:b", id="concept:b", type="concept",
                        label="B", definition="")
    clean_kg.G.add_edge("concept:a", "concept:b", relation="extends")

    assert clean_kg.remove_concept_node("concept:a") is True
    assert not clean_kg.G.has_node("concept:a")
    assert clean_kg.G.has_node("concept:b")
    assert not clean_kg.G.has_edge("concept:a", "concept:b")


def test_remove_concept_node_returns_false_for_missing_node(
    clean_kg: KnowledgeGraph,
) -> None:
    assert clean_kg.remove_concept_node("concept:not-here") is False


def test_remove_concept_node_returns_false_for_non_concept_node(
    clean_kg: KnowledgeGraph,
) -> None:
    """Calling on a non-concept node is a no-op and returns False (or
    similar falsy signal). The node MUST survive — we don't want a
    misdirected call to clobber a term."""
    clean_kg.G.add_node("term:en:keep", id="term:en:keep", type="term",
                        term="keep", lang="en")
    result = clean_kg.remove_concept_node("term:en:keep")
    assert result is False
    assert clean_kg.G.has_node("term:en:keep")


def test_remove_concept_node_is_idempotent(clean_kg: KnowledgeGraph) -> None:
    """Calling on an already-removed concept must return False, not raise.
    This protects the `--apply` re-run case."""
    clean_kg.G.add_node("concept:once", id="concept:once", type="concept",
                        label="Once", definition="")
    assert clean_kg.remove_concept_node("concept:once") is True
    assert clean_kg.remove_concept_node("concept:once") is False
