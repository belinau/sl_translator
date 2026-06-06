#!/usr/bin/env python3
"""scripts/ingest_curator_lineages.py

Phase 10 ingester for curator-authored lineage data. Reads two JSON files
and writes `concept` nodes plus `(concept) -[attributed_to]-> (agent)`
edges to the live KG.

Input files (default paths):

  * `data/concept_theorists.json` — `dict[concept_id, theorist_name]`
    with PRE-slugged `concept:<id>` keys, e.g.
    `{"concept:interactivity": "Roy Ascott"}`.
  * `data/lineage_schools.json` — `dict[agent_name, school_name]`,
    e.g. `{"Aby Warburg": "iconology"}`.

For each entry:

  (a) when the agent already exists in the KG as `agent:<slug>`, create
      the concept node (if missing) and the attributed_to edge;
  (b) when the agent is missing, append a review record to
      `data/kg_review.json`. For the schools file the concept node is
      still created so future curator agent-additions resolve cheaply.

NO stub agents are ever created. Idempotent — re-running over the same
KG state and review queue produces the same result.

Usage:
    .venv/bin/python3 scripts/ingest_curator_lineages.py --dry-run
    .venv/bin/python3 scripts/ingest_curator_lineages.py --apply
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, NamedTuple, Protocol

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Structural protocol — only the surface the ingester touches.
# ---------------------------------------------------------------------------
class _KGLike(Protocol):
    """The ingester reads `.G` for membership checks and writes via two
    factory methods. Tests pass a synthetic stand-in; production passes a
    real `KnowledgeGraph`."""

    G: Any  # networkx.DiGraph

    def add_concept_node(
        self,
        concept_id: str,
        label: str,
        domain: str = ...,
        definition: str = ...,
        **kwargs: Any,
    ) -> str: ...

    def link_attributed_to(self, source_id: str, agent_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# Slug rule (Phase 10 spec):
#   lowercase, spaces → hyphens, non-alphanumeric stripped EXCEPT hyphens.
# Applied identically to school strings and to agent display names so
# `theorist_name → agent:<slug>` lookups round-trip with the real KG's
# `agent:<lowercased-slug>` convention.
# ---------------------------------------------------------------------------
_SLUG_STRIP = re.compile(r"[^a-z0-9-]")


def _slug(text: str) -> str:
    s = text.lower().replace(" ", "-")
    return _SLUG_STRIP.sub("", s)


def _agent_id_for(name: str) -> str:
    return f"agent:{_slug(name)}"


def _concept_id_for_school(school: str) -> str:
    return f"concept:{_slug(school)}"


def _label_for_pre_slugged_concept(concept_id: str) -> str:
    """`concept:intermedia_art` → `intermedia art`."""
    body = concept_id[len("concept:") :] if concept_id.startswith("concept:") else concept_id
    return body.replace("_", " ")


# ---------------------------------------------------------------------------
# Plan structures — immutable so re-running plan_ingest is safe.
# ---------------------------------------------------------------------------
class Plan(NamedTuple):
    """Result of planning. Three iterables in deterministic order.

    `new_concepts` — list of `{id, label, definition}` dicts for concept
                     nodes to create (already filtered against the KG).
    `new_edges`    — list of `(concept_id, agent_id)` tuples to link
                     via `link_attributed_to`.
    `review`       — list of review-queue records to append. Each has
                     fields `{id, type, reason, source, concept_id}`.

    NamedTuple (not dataclass) on purpose: the script is loaded via
    `importlib.util.spec_from_file_location` from the tests, which
    confuses the dataclass-annotation resolver on Python 3.14 (it tries
    to look the script up in `sys.modules` and finds None). NamedTuple
    sidesteps that and gives us the immutability we want anyway.
    """

    new_concepts: list[dict[str, str]]
    new_edges: list[tuple[str, str]]
    review: list[dict[str, str]]


# ---------------------------------------------------------------------------
# Pure planner
# ---------------------------------------------------------------------------
def plan_ingest(
    kg: _KGLike,
    *,
    theorists: Mapping[str, str],
    schools: Mapping[str, str],
) -> Plan:
    """Build the plan for a single ingest pass.

    Pure (no side-effects on `kg`). Deterministic for identical inputs:
    iterates `sorted(...)` so dict-insertion-order leakage cannot affect
    the output. Re-emitting on a partially-applied KG is fine — duplicate
    concept/edge entries are filtered against `kg.G` here; duplicate
    review records are filtered against the on-disk queue by `apply_plan`.
    """
    g = kg.G

    new_concepts: list[dict[str, str]] = []
    new_edges: list[tuple[str, str]] = []
    review: list[dict[str, str]] = []

    # Track planned concept ids so a second curator file can't re-plan
    # the same node twice in one pass.
    planned_concept_ids: set[str] = set()

    def _plan_concept(cid: str, label: str) -> None:
        if cid in planned_concept_ids:
            return
        planned_concept_ids.add(cid)
        if g.has_node(cid):
            return
        new_concepts.append({"id": cid, "label": label, "definition": ""})

    # --- theorists: `concept:<id>` → theorist_name ----------------------
    for concept_id, theorist_name in sorted(theorists.items()):
        label = _label_for_pre_slugged_concept(concept_id)
        _plan_concept(concept_id, label)

        agent_id = _agent_id_for(theorist_name)
        if g.has_node(agent_id):
            # Avoid re-emitting an existing edge.
            if not g.has_edge(concept_id, agent_id):
                new_edges.append((concept_id, agent_id))
        else:
            review.append(
                {
                    "id": agent_id,
                    "type": "agent",
                    "reason": "missing_agent",
                    "source": "concept_theorists",
                    "concept_id": concept_id,
                }
            )

    # --- schools: agent_name → school_name -------------------------------
    # Sort by (agent_name, school_name) for determinism and to keep
    # multiple-agents-share-one-concept ordering stable.
    for agent_name, school in sorted(schools.items()):
        concept_id = _concept_id_for_school(school)
        # Concept label is the ORIGINAL school string (preserves casing).
        _plan_concept(concept_id, school)

        agent_id = _agent_id_for(agent_name)
        if g.has_node(agent_id):
            if not g.has_edge(concept_id, agent_id):
                new_edges.append((concept_id, agent_id))
        else:
            review.append(
                {
                    "id": agent_id,
                    "type": "agent",
                    "reason": "missing_agent",
                    "source": "lineage_schools",
                    "concept_id": concept_id,
                }
            )

    return Plan(
        new_concepts=new_concepts,
        new_edges=new_edges,
        review=review,
    )


# ---------------------------------------------------------------------------
# Plan application
# ---------------------------------------------------------------------------
def _dedup_key(rec: Mapping[str, Any]) -> tuple[Any, ...]:
    """Stable identity for a review record. Re-runs of `plan_ingest`
    re-emit the same missing-agent records (the planner can't see the
    on-disk queue); we dedupe at apply time against this 5-tuple."""
    return (
        rec.get("id"),
        rec.get("type"),
        rec.get("reason"),
        rec.get("source"),
        rec.get("concept_id"),
    )


def _load_review_queue(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(
            f"Review queue at {path} is not a JSON array: got {type(data).__name__}"
        )
    return data


def _write_review_queue(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def apply_plan(kg: _KGLike, plan: Plan, *, review_path: Path) -> None:
    """Apply `plan` to `kg` and append missing-agent records to
    `review_path`.

    Concept-node and edge writes go through the idempotent factories
    (`add_concept_node`, `link_attributed_to`), so re-applying the same
    plan never duplicates KG state. Review-queue records are deduped
    against the existing on-disk queue by the 5-tuple key above, so
    repeated runs don't grow the queue either.
    """
    # 1. Create concepts.
    for spec in plan.new_concepts:
        kg.add_concept_node(
            spec["id"],
            label=spec["label"],
            definition=spec.get("definition", ""),
        )

    # 2. Link concept → agent (factory is a no-op if either node is
    #    missing or the edge already exists).
    for concept_id, agent_id in plan.new_edges:
        kg.link_attributed_to(concept_id, agent_id)

    # 3. Append review records, deduping against the existing queue.
    if plan.review:
        existing = _load_review_queue(review_path)
        seen = {_dedup_key(r) for r in existing}
        appended: list[dict[str, Any]] = list(existing)
        for rec in plan.review:
            key = _dedup_key(rec)
            if key in seen:
                continue
            seen.add(key)
            appended.append(dict(rec))
        if len(appended) != len(existing):
            _write_review_queue(review_path, appended)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_curator_file(path: Path) -> dict[str, str]:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(
            f"Curator file {path} must be a JSON object, got {type(data).__name__}"
        )
    return {str(k): str(v) for k, v in data.items()}


def _print_report(plan: Plan, *, mode: str) -> None:
    verb = "would_create" if mode == "dry-run" else "created"
    edge_verb = "would_link" if mode == "dry-run" else "linked"
    rev_verb = "would_queue" if mode == "dry-run" else "queued"
    print(
        f"mode={mode} "
        f"{verb}_concepts={len(plan.new_concepts)} "
        f"{edge_verb}_edges={len(plan.new_edges)} "
        f"{rev_verb}_review_records={len(plan.review)}"
    )


def main(
    argv: list[str] | None = None,
    *,
    kg: _KGLike | None = None,
    theorists_path: Path = Path("data/concept_theorists.json"),
    schools_path: Path = Path("data/lineage_schools.json"),
    review_path: Path = Path("data/kg_review.json"),
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Wire curator-authored lineage data into the KG (Phase 10). "
            "Defaults to --dry-run; pass --apply to mutate the graph."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Report planned writes without mutating the KG (default).",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="Apply the plan to the KG and append review records.",
    )
    args = parser.parse_args(argv)

    theorists = _load_curator_file(theorists_path)
    schools = _load_curator_file(schools_path)

    # Lazy-load the real KG only when no kg was injected — keeps `--help`
    # and dependency-injected tests cheap (spacy/classla pipelines are
    # expensive to spin up).
    if kg is None:
        from translate_core.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph()
        print(
            f"loaded kg: {kg.G.number_of_nodes()} nodes, "
            f"{kg.G.number_of_edges()} edges"
        )

    plan = plan_ingest(kg, theorists=theorists, schools=schools)

    if args.apply:
        apply_plan(kg, plan, review_path=review_path)
        save = getattr(kg, "save", None)
        if callable(save):
            save()
        _print_report(plan, mode="apply")
    else:
        _print_report(plan, mode="dry-run")

    return 0


if __name__ == "__main__":
    sys.exit(main())
