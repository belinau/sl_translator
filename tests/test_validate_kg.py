"""Tests for the KG invariant gate (scripts/validate_kg.py).

Covers: detection of each hard-invariant class on a crafted dirty payload,
a clean payload producing zero violations, and a real-data gate asserting the
live KG has no hard-invariant violations (ontology invariant #8)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("validate_kg", ROOT / "scripts" / "validate_kg.py")
assert _spec and _spec.loader
validate_kg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_kg)
validate = validate_kg.validate
HARD = validate_kg.HARD


def _agent(aid, **kw):
    base = {"id": aid, "type": "agent", "name": "X", "role": "author",
            "dedup_group": "x", "alt_spellings": ["X"], "all_roles": ["author"],
            "mention_count": 1}
    base.update(kw)
    return base


def _source(sid, **kw):
    base = {"id": sid, "type": "source_text", "title": "A Real Title",
            "year": 1990, "project_type": "book"}
    base.update(kw)
    return base


def test_clean_payload_has_no_violations():
    nodes = [
        _source("source:foo-bar-1990"),
        _agent("agent:jane-doe"),
        {"id": "institution:maska", "type": "institution", "name": "Maska", "kind": "publisher"},
        _source("source:my-book", project_type="book_translation"),
    ]
    edges = [
        {"source": "source:foo-bar-1990", "target": "agent:jane-doe", "relation": "written_by"},
        {"source": "source:my-book", "target": "agent:jane-doe", "relation": "translated_by"},
    ]
    assert validate(nodes, edges) == {}


def test_detects_each_hard_invariant():
    nodes = [
        {"id": "ghost", "type": None},                                   # ghost_node
        {"id": "seg1", "type": "tm_segment"},                            # forbidden_node_type
        {"id": "weird:1", "type": "mystery"},                            # unknown_node_type
        _agent("agent:bad", role="overlord"),                            # bad_role
        {"id": "agent:bare", "type": "agent", "name": "B", "role": "author"},  # agent_missing_required
        {"id": "institution:x", "type": "institution", "name": "X", "kind": "wizardry"},  # bad_kind
        _source("source:badtype", project_type="nonsense"),             # bad_project_type
        _source("source:notitle", title=""),                            # source_no_title
        _source("source:lower-frag", title="lowercase fragment"),       # fragment_title
        _source("source:cont", project_type="festival_programme"),      # container_missing_translated_by
        _source("source:dup-2000"),                                     # duplicate_source_stem (twin below)
        _source("source:book-dup-2000", project_type="book"),
    ]
    edges = [
        {"source": "source:cont", "target": "source:cont", "relation": "cited_in"},  # self-loop
        {"source": "source:badtype", "target": "agent:missing", "relation": "written_by"},  # dangling
        {"source": "source:foo-bar-1990", "target": "institution:maska", "relation": "alt_published_by"},  # unknown relation
    ]
    v = validate(nodes, edges)
    for inv in ("ghost_node", "forbidden_node_type", "unknown_node_type", "bad_role",
                "agent_missing_required", "bad_kind", "bad_project_type", "source_no_title",
                "container_missing_translated_by", "duplicate_source_stem",
                "cited_in_self_loop", "dangling_edge", "unknown_edge_relation"):
        assert inv in v, f"missing detection: {inv}"
        assert inv in HARD
    # fragment_title is still detected but advisory (soft), not gate-failing
    assert "fragment_title" in v
    assert "fragment_title" not in HARD


def test_container_with_translated_by_is_clean():
    nodes = [_source("source:c", project_type="book_translation"), _agent("agent:t")]
    edges = [{"source": "source:c", "target": "agent:t", "relation": "translated_by"}]
    assert "container_missing_translated_by" not in validate(nodes, edges)


def test_live_kg_has_no_hard_violations():
    kg_path = ROOT / "data" / "knowledge.db"
    if not kg_path.exists():
        pytest.skip("live KG not present")
    data = json.loads(kg_path.read_text(encoding="utf-8"))
    v = validate(data.get("nodes", []), data.get("edges", []))
    hard = {k: x for k, x in v.items() if k in HARD}
    assert not hard, f"live KG has hard violations: { {k: len(x) for k, x in hard.items()} }"
