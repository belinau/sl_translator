#!/usr/bin/env python3
"""scripts/drain_noise_concepts.py

Phase 9 one-off cleanup. Walks every `concept` node in the live KG and
drains the noise ones — concepts that have no definition, no lineage
edges, no `originating_author` provenance, and no meaningful term
attestation. With Phase 8 the writers that produced these concepts are
gone; this script removes the data they left behind.

Usage:
    # inspect what would happen
    .venv/bin/python3 scripts/drain_noise_concepts.py --dry-run

    # apply the deletion (asks no further confirmation — caller decides)
    .venv/bin/python3 scripts/drain_noise_concepts.py --apply

Performance: the live KG has ~8 455 concept nodes among ~tens of
thousands of nodes total. The walk is strictly single-pass:

    for node_id, data in kg.G.nodes(data=True):
        if data.get("type") != "concept":
            continue
        if is_noise_concept(node_id, kg):
            ...

The predicate `is_noise_concept` reads only LOCAL edges of the candidate
(`in_edges` / `out_edges`), so the total work is O(N + E) where N is the
node count and E the edge count, not O(N^2).

Edge cleanup is implicit: `KnowledgeGraph.remove_concept_node` delegates
to `nx.DiGraph.remove_node`, which removes the concept together with all
incident edges. Term nodes whose `instantiates_concept` edge pointed at a
deleted concept are preserved (only the edge goes); the KG never grows a
dangling edge as a result of this script.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Predicate constants
# ---------------------------------------------------------------------------

# Relations that count as "lineage" — a concept participating in any of
# these (in either direction) is curated content and must be preserved.
# `attributed_to` is listed for completeness even though in practice it
# originates from `translation_mapping` nodes; if it ever lands incident
# to a concept that's still a keep-signal.
LINEAGE_RELATIONS: frozenset[str] = frozenset({
    "extends",
    "critiques",
    "redefines",
    "reappropriates",
    "related_to",
    "attributed_to",
})

# Minimum term-attestation count for the "highly-attested concept"
# rescue. The threshold is strictly GREATER THAN this value — see
# `is_noise_concept` and the boundary test in
# `tests/test_drain_noise_concepts.py`.
TERM_ATTESTATION_THRESHOLD: int = 5


class _KGLike(Protocol):
    """Structural protocol — only `.G` is read by the predicate. Tests
    pass a tiny stand-in; production passes a real `KnowledgeGraph`."""
    G: Any  # networkx.DiGraph


# ---------------------------------------------------------------------------
# Predicate
# ---------------------------------------------------------------------------
def is_noise_concept(node_id: str, kg: _KGLike) -> bool:
    """Return True when the concept identified by `node_id` is noise and
    safe to drain.

    A concept is noise iff ALL of the following hold:

      (a) it exists and has `type == "concept"`;
      (b) its `definition` is empty (missing, empty string, or
          whitespace-only);
      (c) it does not participate in any LINEAGE_RELATIONS edge in
          either direction;
      (d) it does not have an `originating_author` field (smol
          provenance) with a non-empty STRING value. NOTE: if the live
          KG ever stores `originating_author` as a list/dict, this check
          treats it as missing and may mis-classify the concept as
          noise. The Phase 9 dry-run is the gate to catch that; the
          coordinator should spot-check the shape distribution before
          `--apply`.
      (e) the number of incoming `instantiates_concept` edges whose
          source is a `term` node is `<= TERM_ATTESTATION_THRESHOLD`
          (strict `<= 5` means the rescue kicks in at 6+ attestations).

    A missing node, or a node of any other type, is reported as
    non-noise (returns False) — this script must never propose deleting
    nodes it doesn't own.

    The implementation uses bounded local edge inspection
    (`in_edges`/`out_edges` of the candidate only), keeping the per-node
    cost independent of the graph size."""
    g = kg.G
    if not g.has_node(node_id):
        return False
    data = g.nodes[node_id]
    if data.get("type") != "concept":
        return False

    # (b) definition signals curated content
    definition = data.get("definition")
    if isinstance(definition, str) and definition.strip():
        return False

    # (d) originating_author signals smol provenance
    author = data.get("originating_author")
    if isinstance(author, str) and author.strip():
        return False

    # (c) lineage edges — both directions
    for _src, _tgt, edge in g.out_edges(node_id, data=True):
        if edge.get("relation") in LINEAGE_RELATIONS:
            return False
    for _src, _tgt, edge in g.in_edges(node_id, data=True):
        if edge.get("relation") in LINEAGE_RELATIONS:
            return False

    # (e) term-attestation rescue — count incoming instantiates_concept
    # edges WHOSE SOURCE IS A TERM NODE. Edges from non-term predecessors
    # don't count.
    attestations = 0
    for src, _tgt, edge in g.in_edges(node_id, data=True):
        if edge.get("relation") != "instantiates_concept":
            continue
        if g.nodes[src].get("type") != "term":
            continue
        attestations += 1
        if attestations > TERM_ATTESTATION_THRESHOLD:
            return False

    return True


# ---------------------------------------------------------------------------
# Single-pass classification
# ---------------------------------------------------------------------------
def _category(node_id: str, data: Mapping[str, Any], kg: _KGLike) -> str:
    """Bucket each concept for the dry-run breakdown.

    Single source of truth: `is_noise_concept` decides delete vs keep.
    Only when the concept is NOT noise do we compute the keep-bucket
    label (`"definition"`, `"originating_author"`, `"lineage"`, or
    `"term_attested"`), in the same evaluation order as the predicate.
    """
    if is_noise_concept(node_id, kg):
        return "noise"

    # Concept is kept — determine which signal saved it.
    definition = data.get("definition")
    if isinstance(definition, str) and definition.strip():
        return "definition"
    author = data.get("originating_author")
    if isinstance(author, str) and author.strip():
        return "originating_author"

    g = kg.G
    for _src, _tgt, edge in g.out_edges(node_id, data=True):
        if edge.get("relation") in LINEAGE_RELATIONS:
            return "lineage"
    for _src, _tgt, edge in g.in_edges(node_id, data=True):
        if edge.get("relation") in LINEAGE_RELATIONS:
            return "lineage"

    # If none of the above applied, the only remaining keep-reason
    # is term-attestation rescue.
    return "term_attested"


def _walk_concepts(kg: _KGLike) -> Iterable[tuple[str, Mapping[str, Any]]]:
    """Single pass over the node table, yielding only concept entries.
    We materialise the iterator into a list ONCE so callers in `--apply`
    mode can mutate the graph during iteration without confusing the
    underlying dict iterator."""
    return [
        (nid, data)
        for nid, data in kg.G.nodes(data=True)
        if data.get("type") == "concept"
    ]


def classify(kg: _KGLike) -> tuple[list[str], Counter]:
    """Single-pass classification of every concept in `kg`.

    Returns `(noise_ids, breakdown)` where `noise_ids` is the list of
    concept ids that `is_noise_concept` flags for deletion, and
    `breakdown` is a Counter over category buckets including both noise
    and the various keep reasons."""
    noise_ids: list[str] = []
    breakdown: Counter = Counter()
    for nid, data in _walk_concepts(kg):
        cat = _category(nid, data, kg)
        breakdown[cat] += 1
        if cat == "noise":
            noise_ids.append(nid)
    return noise_ids, breakdown


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_report(noise_ids: list[str], breakdown: Counter, *, mode: str) -> None:
    keep = sum(c for cat, c in breakdown.items() if cat != "noise")
    will_or_did = "would_delete" if mode == "dry-run" else "deleted"
    print(f"mode={mode} {will_or_did}={len(noise_ids)} keep={keep}")
    print("breakdown:")
    for cat in ("noise", "definition", "lineage",
                "originating_author", "term_attested"):
        print(f"  {cat}: {breakdown.get(cat, 0)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Drain noise concept nodes from the live KG (Phase 9). "
            "Defaults to --dry-run; pass --apply to mutate the graph."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be deleted without mutating the KG (default).",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="Delete noise concepts and save the KG.",
    )
    args = parser.parse_args(argv)

    # Lazy import — the KG load is expensive (spacy/classla pipelines)
    # and we don't want to pay it just for `--help`.
    from translate_core.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph()
    print(
        f"loaded kg: {kg.G.number_of_nodes()} nodes, "
        f"{kg.G.number_of_edges()} edges"
    )

    noise_ids, breakdown = classify(kg)

    if args.apply:
        failed: list[str] = []
        for cid in noise_ids:
            if not kg.remove_concept_node(cid):
                failed.append(cid)
        if failed:
            print(f"WARNING: {len(failed)} concept(s) could not be removed "
                  f"(already absent or wrong type). First few: {failed[:5]}")
        kg.save()
        _print_report(noise_ids, breakdown, mode="apply")
    else:
        _print_report(noise_ids, breakdown, mode="dry-run")

    return 0


if __name__ == "__main__":
    sys.exit(main())
