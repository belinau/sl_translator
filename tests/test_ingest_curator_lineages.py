"""Tests for Phase 10 curator-lineage ingestion.

Covers `scripts/ingest_curator_lineages.py` (does not exist yet — this
file is the TDD-red specification).

The script reads two curator-authored JSON files:

  * `data/concept_theorists.json` — `dict[concept_id, theorist_name]`
    (e.g. `{"concept:interactivity": "Roy Ascott"}`).
  * `data/lineage_schools.json`   — `dict[agent_name, school_name]`
    (e.g. `{"Aby Warburg": "iconology"}`).

For each entry the ingester either:

  (a) creates a `concept` node and an `(concept) -[attributed_to]-> (agent)`
      edge, when the agent already exists in the KG; OR
  (b) appends a `{id, type, reason: "missing_agent", source, concept_id}`
      record to a `kg_review.json` review queue, when the agent does not
      exist. For the schools file the concept node is still created in
      case (b) so that future curator-added agents can be linked cheaply.

NO live KG access. NO real-data side-effects: review-queue paths come
from `tmp_path`; the KG is a synthetic stand-in for the pure unit tests
(matches the structural-protocol style in
`tests/test_drain_noise_concepts.py`).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict

import networkx as nx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Load scripts/ingest_curator_lineages.py via importlib so the
# `scripts/` directory's missing __init__.py doesn't break imports.
#
# At RED time the module does NOT exist; the loader call is wrapped in
# `pytest.importorskip` so a meaningful failure surfaces in collection
# without polluting every individual test with the same stack trace.
# ---------------------------------------------------------------------------
_INGEST_PATH = ROOT / "scripts" / "ingest_curator_lineages.py"


def _load_ingester():
    """Load the ingester module fresh. Raises ModuleNotFoundError when
    the script does not exist yet (the RED state)."""
    if not _INGEST_PATH.exists():
        raise ModuleNotFoundError(
            f"scripts/ingest_curator_lineages.py not present yet: {_INGEST_PATH}"
        )
    spec = importlib.util.spec_from_file_location(
        "ingest_curator_lineages", _INGEST_PATH
    )
    assert spec and spec.loader, "could not load ingest_curator_lineages"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ingester():
    """The loaded module. Skip-marker not used: we WANT these tests to
    fail loudly during RED so the implementer sees ModuleNotFoundError."""
    return _load_ingester()


# ---------------------------------------------------------------------------
# Synthetic KG stand-in — exposes only `.G` (a networkx DiGraph).
# Matches `_FakeKG` style in `tests/test_drain_noise_concepts.py`.
# ---------------------------------------------------------------------------
class _FakeKG:
    """Minimal KG shim. The Phase 10 ingester reads `.G` and writes via
    public factory methods. We provide both surfaces — node-reads through
    `.G`, and the factory methods that the planner calls. Each factory
    delegates to plain networkx so the test can assert on node + edge
    state without touching real `KnowledgeGraph`."""

    def __init__(self) -> None:
        self.G: nx.DiGraph = nx.DiGraph()

    # --- pre-seeding helpers (test fixtures) ---
    def seed_agent(self, slug: str, label: str = "") -> str:
        nid = slug if slug.startswith("agent:") else f"agent:{slug}"
        self.G.add_node(
            nid, id=nid, type="agent", label=label or nid.split(":", 1)[1]
        )
        return nid

    def seed_concept(self, cid: str, **attrs: Any) -> str:
        data: Dict[str, Any] = {
            "id": cid, "type": "concept", "label": cid, "definition": "",
        }
        data.update(attrs)
        self.G.add_node(cid, **data)
        return cid

    # --- factory methods the ingester is expected to call ---
    def add_concept_node(
        self,
        concept_id: str,
        label: str,
        domain: str = "",
        definition: str = "",
        **kwargs: Any,
    ) -> str:
        """Mirror `KnowledgeGraph.add_concept_node`: idempotent — does NOT
        overwrite an existing node's label/definition."""
        if not self.G.has_node(concept_id):
            data: Dict[str, Any] = {
                "id": concept_id,
                "type": "concept",
                "label": label,
                "domain": domain,
                "definition": definition,
            }
            data.update(kwargs)
            self.G.add_node(concept_id, **data)
        return concept_id

    def link_attributed_to(self, src_id: str, agent_id: str) -> bool:
        """Mirror the production semantics enough for the ingester:
        edge `(src) -[attributed_to]-> (agent)`. Idempotent. The
        production method targets `translation_mapping` nodes; the
        Phase 10 ingester points it at `concept` nodes. We deliberately
        do NOT enforce the production type-guard in this fake — the
        ingester is allowed to call this factory with concept sources;
        if the implementer decides to use a different factory name
        (e.g. `link_concept_attributed_to`), the test will fail with
        AttributeError and the implementer will rename here."""
        ag = agent_id if agent_id.startswith("agent:") else f"agent:{agent_id}"
        if not (self.G.has_node(src_id) and self.G.has_node(ag)):
            return False
        if not self.G.has_edge(src_id, ag):
            self.G.add_edge(src_id, ag, relation="attributed_to")
        return True


@pytest.fixture
def fkg() -> _FakeKG:
    return _FakeKG()


# ---------------------------------------------------------------------------
# plan_ingest — the pure function the implementer is expected to expose.
# Signature (proposed):
#
#     plan_ingest(
#         kg,
#         theorists: dict[str, str],
#         schools:   dict[str, str],
#     ) -> IngestPlan
#
# where `IngestPlan` (NamedTuple or dataclass) exposes:
#   - new_concepts: list[dict]  — concept-node specs (id, label, definition)
#   - new_edges:    list[tuple[str, str]]  — (src concept id, agent id) pairs
#   - review:       list[dict]  — review-queue records to append
#
# Order across all three lists MUST be deterministic for the same input.
# ---------------------------------------------------------------------------


def _read_review_queue(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


# ===========================================================================
# 1. Concept-theorists ingest
# ===========================================================================
def test_theorists_creates_concept_and_attributed_to_edge_when_agent_exists(
    ingester, fkg: _FakeKG, tmp_path: Path,
) -> None:
    fkg.seed_agent("agent:roy-ascott", label="Roy Ascott")
    theorists = {"concept:interactivity": "Roy Ascott"}
    schools: dict[str, str] = {}

    plan = ingester.plan_ingest(fkg, theorists=theorists, schools=schools)
    ingester.apply_plan(fkg, plan, review_path=tmp_path / "kg_review.json")

    # concept node exists with curator-supplied id verbatim
    assert fkg.G.has_node("concept:interactivity")
    node = fkg.G.nodes["concept:interactivity"]
    assert node["type"] == "concept"
    # label = id stripped of `concept:` prefix, underscores → spaces
    assert node["label"] == "interactivity"
    assert node["definition"] == ""
    # label_lang NOT set (or explicitly None) — curator file has no lang
    assert node.get("label_lang") in (None, "")

    # edge to the agent
    assert fkg.G.has_edge("concept:interactivity", "agent:roy-ascott")
    edge = fkg.G.get_edge_data("concept:interactivity", "agent:roy-ascott")
    assert edge["relation"] == "attributed_to"


def test_theorists_label_strips_concept_prefix_and_replaces_underscores(
    ingester, fkg: _FakeKG, tmp_path: Path,
) -> None:
    fkg.seed_agent("agent:dick-higgins", label="Dick Higgins")
    theorists = {"concept:intermedia_art": "Dick Higgins"}

    plan = ingester.plan_ingest(fkg, theorists=theorists, schools={})
    ingester.apply_plan(fkg, plan, review_path=tmp_path / "kg_review.json")

    node = fkg.G.nodes["concept:intermedia_art"]
    assert node["label"] == "intermedia art"


def test_theorists_preserves_existing_concept_node_label_and_definition(
    ingester, fkg: _FakeKG, tmp_path: Path,
) -> None:
    """If the concept node already exists, the ingester must NOT
    overwrite `label` or `definition`; it only adds the attributed_to
    edge."""
    fkg.seed_agent("agent:roy-ascott", label="Roy Ascott")
    fkg.seed_concept(
        "concept:interactivity",
        label="Interactivity (curated)",
        definition="A curated definition.",
    )
    theorists = {"concept:interactivity": "Roy Ascott"}

    plan = ingester.plan_ingest(fkg, theorists=theorists, schools={})
    ingester.apply_plan(fkg, plan, review_path=tmp_path / "kg_review.json")

    node = fkg.G.nodes["concept:interactivity"]
    assert node["label"] == "Interactivity (curated)"
    assert node["definition"] == "A curated definition."
    # but the edge IS added
    assert fkg.G.has_edge("concept:interactivity", "agent:roy-ascott")


# ===========================================================================
# 2. Lineage-schools ingest
# ===========================================================================
def test_schools_creates_concept_with_slugged_id_and_original_label(
    ingester, fkg: _FakeKG, tmp_path: Path,
) -> None:
    fkg.seed_agent("agent:aby-warburg", label="Aby Warburg")
    schools = {"Aby Warburg": "iconology"}

    plan = ingester.plan_ingest(fkg, theorists={}, schools=schools)
    ingester.apply_plan(fkg, plan, review_path=tmp_path / "kg_review.json")

    # School string → `concept:<slug>`. Plain lowercase one-word case.
    assert fkg.G.has_node("concept:iconology")
    node = fkg.G.nodes["concept:iconology"]
    assert node["type"] == "concept"
    # `label` is the ORIGINAL school string (preserves casing)
    assert node["label"] == "iconology"
    assert node["definition"] == ""
    # No language metadata → label_lang is None / absent
    assert node.get("label_lang") in (None, "")

    # attributed_to edge points at the agent
    assert fkg.G.has_edge("concept:iconology", "agent:aby-warburg")


def test_schools_slugifies_spaces_and_strips_punctuation(
    ingester, fkg: _FakeKG, tmp_path: Path,
) -> None:
    fkg.seed_agent("agent:achille-mbembe", label="Achille Mbembe")
    # "decolonial theory" → lowercased, spaces → hyphens
    schools = {"Achille Mbembe": "decolonial theory"}

    plan = ingester.plan_ingest(fkg, theorists={}, schools=schools)
    ingester.apply_plan(fkg, plan, review_path=tmp_path / "kg_review.json")

    assert fkg.G.has_node("concept:decolonial-theory")
    assert fkg.G.nodes["concept:decolonial-theory"]["label"] == "decolonial theory"
    assert fkg.G.has_edge("concept:decolonial-theory", "agent:achille-mbembe")


def test_schools_multiple_agents_share_one_concept_node(
    ingester, fkg: _FakeKG, tmp_path: Path,
) -> None:
    """Several agents pointing at the same school must collapse to ONE
    concept node with multiple attributed_to edges."""
    fkg.seed_agent("agent:achille-mbembe", label="Achille Mbembe")
    fkg.seed_agent("agent:walter-mignolo", label="Walter Mignolo")
    schools = {
        "Achille Mbembe": "decolonial theory",
        "Walter Mignolo": "decolonial theory",
    }

    plan = ingester.plan_ingest(fkg, theorists={}, schools=schools)
    ingester.apply_plan(fkg, plan, review_path=tmp_path / "kg_review.json")

    # exactly ONE concept node for the school
    concept_nodes = [
        n for n, d in fkg.G.nodes(data=True)
        if d.get("type") == "concept" and d.get("label") == "decolonial theory"
    ]
    assert len(concept_nodes) == 1
    assert concept_nodes[0] == "concept:decolonial-theory"

    # both attributed_to edges present
    assert fkg.G.has_edge("concept:decolonial-theory", "agent:achille-mbembe")
    assert fkg.G.has_edge("concept:decolonial-theory", "agent:walter-mignolo")


# ===========================================================================
# 3. Missing-agent routing
# ===========================================================================
def test_theorists_missing_agent_routed_to_review_queue(
    ingester, fkg: _FakeKG, tmp_path: Path,
) -> None:
    """No agent node for the theorist → append a record to
    `data/kg_review.json` with `reason="missing_agent"`. NO stub agent
    is created."""
    review_path = tmp_path / "kg_review.json"
    theorists = {"concept:phantom": "Nobody Real"}

    plan = ingester.plan_ingest(fkg, theorists=theorists, schools={})
    ingester.apply_plan(fkg, plan, review_path=review_path)

    # no agent node was created
    agent_nodes = [n for n, d in fkg.G.nodes(data=True) if d.get("type") == "agent"]
    assert agent_nodes == []

    # review-queue record landed
    queue = _read_review_queue(review_path)
    assert len(queue) == 1
    rec = queue[0]
    assert rec["type"] == "agent"
    assert rec["reason"] == "missing_agent"
    assert rec["source"] == "concept_theorists"
    assert rec["concept_id"] == "concept:phantom"
    # the attempted agent slug — implementer chooses exact convention but
    # it must be an `agent:`-prefixed slug.
    assert rec["id"].startswith("agent:")


def test_schools_missing_agent_routed_to_review_but_concept_still_created(
    ingester, fkg: _FakeKG, tmp_path: Path,
) -> None:
    """Lineage-schools branch: the concept node IS created even when
    the agent is missing, so that future curator agent-additions resolve
    cleanly. Only the edge is deferred to the review queue."""
    review_path = tmp_path / "kg_review.json"
    schools = {"Unknown Theorist": "speculative realism"}

    plan = ingester.plan_ingest(fkg, theorists={}, schools=schools)
    ingester.apply_plan(fkg, plan, review_path=review_path)

    # concept node exists
    assert fkg.G.has_node("concept:speculative-realism")
    # NO agent node was stubbed
    agent_nodes = [n for n, d in fkg.G.nodes(data=True) if d.get("type") == "agent"]
    assert agent_nodes == []
    # NO attributed_to edge (the agent doesn't exist to point at)
    out = list(fkg.G.out_edges("concept:speculative-realism", data=True))
    assert out == []

    # review-queue record landed with the right `source`
    queue = _read_review_queue(review_path)
    assert len(queue) == 1
    rec = queue[0]
    assert rec["type"] == "agent"
    assert rec["reason"] == "missing_agent"
    assert rec["source"] == "lineage_schools"
    assert rec["id"].startswith("agent:")


def test_review_queue_appends_to_existing_records(
    ingester, fkg: _FakeKG, tmp_path: Path,
) -> None:
    """If `kg_review.json` already exists with prior records, the
    ingester APPENDS — it must not clobber the file."""
    review_path = tmp_path / "kg_review.json"
    existing = [
        {"id": "source:prior", "type": "source_text", "reason": "placeholder_year"}
    ]
    review_path.write_text(json.dumps(existing), encoding="utf-8")

    theorists = {"concept:phantom": "Nobody Real"}
    plan = ingester.plan_ingest(fkg, theorists=theorists, schools={})
    ingester.apply_plan(fkg, plan, review_path=review_path)

    queue = _read_review_queue(review_path)
    assert len(queue) == 2
    assert queue[0] == existing[0]
    assert queue[1]["reason"] == "missing_agent"


# ===========================================================================
# 4. Idempotency
# ===========================================================================
def test_idempotent_no_duplicate_edges_or_review_records(
    ingester, fkg: _FakeKG, tmp_path: Path,
) -> None:
    """Running the ingester twice with the same inputs must NOT
    duplicate edges OR review-queue entries."""
    review_path = tmp_path / "kg_review.json"
    fkg.seed_agent("agent:roy-ascott", label="Roy Ascott")
    theorists = {
        "concept:interactivity": "Roy Ascott",
        "concept:phantom": "Nobody Real",
    }
    schools = {"Aby Warburg": "iconology"}  # Aby Warburg agent absent
    # First run
    plan = ingester.plan_ingest(fkg, theorists=theorists, schools=schools)
    ingester.apply_plan(fkg, plan, review_path=review_path)
    # Second run on the SAME KG state
    plan2 = ingester.plan_ingest(fkg, theorists=theorists, schools=schools)
    ingester.apply_plan(fkg, plan2, review_path=review_path)

    # exactly one attributed_to edge to roy-ascott
    edges = [
        (u, v) for u, v, d in fkg.G.edges(data=True)
        if d.get("relation") == "attributed_to"
        and u == "concept:interactivity" and v == "agent:roy-ascott"
    ]
    assert len(edges) == 1

    # exactly one concept node per id
    concept_nodes = [n for n, d in fkg.G.nodes(data=True) if d.get("type") == "concept"]
    assert sorted(concept_nodes) == sorted(set(concept_nodes))

    # review queue: one entry per missing agent, not two
    queue = _read_review_queue(review_path)
    by_concept = [r for r in queue if r.get("concept_id") == "concept:phantom"]
    assert len(by_concept) == 1
    by_aby = [
        r for r in queue
        if r.get("source") == "lineage_schools"
        and r.get("id", "").endswith("aby-warburg")
    ]
    assert len(by_aby) == 1


# ===========================================================================
# 5. Pure-function determinism
# ===========================================================================
def test_plan_ingest_is_deterministic_for_same_inputs(
    ingester, fkg: _FakeKG,
) -> None:
    """`plan_ingest` must produce the SAME plan when given identical KG
    state and inputs — no set-iteration order leakage, no time-stamps in
    the plan output. Compare both serialisations and the structural
    contents."""
    fkg.seed_agent("agent:roy-ascott")
    fkg.seed_agent("agent:dick-higgins")
    fkg.seed_agent("agent:aby-warburg")
    theorists = {
        "concept:interactivity": "Roy Ascott",
        "concept:intermedia_art": "Dick Higgins",
        "concept:ghost_theorist": "Nobody Real",
    }
    schools = {
        "Aby Warburg": "iconology",
        "Unknown Theorist": "speculative realism",
    }
    plan_a = ingester.plan_ingest(fkg, theorists=theorists, schools=schools)
    plan_b = ingester.plan_ingest(fkg, theorists=theorists, schools=schools)

    # The plan exposes three iterables (new_concepts, new_edges, review).
    # Compare structurally — the implementer may choose NamedTuple,
    # dataclass, or plain dict; in all cases the three fields are present.
    assert list(plan_a.new_concepts) == list(plan_b.new_concepts)
    assert list(plan_a.new_edges) == list(plan_b.new_edges)
    assert list(plan_a.review) == list(plan_b.review)


# ===========================================================================
# 6. CLI entry point — main() with --dry-run / --apply
# ===========================================================================
def test_main_dry_run_does_not_mutate_or_write(
    ingester, fkg: _FakeKG, tmp_path: Path,
) -> None:
    """`--dry-run` must not write to the review queue OR mutate the KG.
    The implementer is expected to expose a `main(argv, kg=..., ...)` or
    accept dependency-injection via env-style indirection so this test
    can drive it without touching the real `data/knowledge.db`. The
    minimum contract: there's a callable `main` on the module."""
    review_path = tmp_path / "kg_review.json"
    fkg.seed_agent("agent:roy-ascott", label="Roy Ascott")
    theorists_path = tmp_path / "concept_theorists.json"
    schools_path = tmp_path / "lineage_schools.json"
    theorists_path.write_text(
        json.dumps({"concept:interactivity": "Roy Ascott",
                    "concept:phantom": "Nobody Real"}),
        encoding="utf-8",
    )
    schools_path.write_text(json.dumps({}), encoding="utf-8")

    rc = ingester.main(
        argv=["--dry-run"],
        kg=fkg,
        theorists_path=theorists_path,
        schools_path=schools_path,
        review_path=review_path,
    )
    assert rc == 0
    # KG untouched
    assert not fkg.G.has_node("concept:interactivity")
    # review queue not written
    assert not review_path.exists()


def test_main_apply_writes_to_kg_and_review_queue(
    ingester, fkg: _FakeKG, tmp_path: Path,
) -> None:
    review_path = tmp_path / "kg_review.json"
    fkg.seed_agent("agent:roy-ascott", label="Roy Ascott")
    theorists_path = tmp_path / "concept_theorists.json"
    schools_path = tmp_path / "lineage_schools.json"
    theorists_path.write_text(
        json.dumps({"concept:interactivity": "Roy Ascott",
                    "concept:phantom": "Nobody Real"}),
        encoding="utf-8",
    )
    schools_path.write_text(json.dumps({}), encoding="utf-8")

    rc = ingester.main(
        argv=["--apply"],
        kg=fkg,
        theorists_path=theorists_path,
        schools_path=schools_path,
        review_path=review_path,
    )
    assert rc == 0
    # KG was mutated for the resolvable theorist
    assert fkg.G.has_node("concept:interactivity")
    assert fkg.G.has_edge("concept:interactivity", "agent:roy-ascott")
    # review queue contains the missing agent
    queue = _read_review_queue(review_path)
    assert any(
        r.get("reason") == "missing_agent" and r.get("concept_id") == "concept:phantom"
        for r in queue
    )
