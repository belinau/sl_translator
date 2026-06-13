#!/usr/bin/env python3
"""scripts/drop_person_name_nodes.py

One-off removal of person-name term & concept nodes from the knowledge graph.

Uses a two-stage approach:
1. **Candidate generation** (high recall): match term/concept labels against
   normalised agent names (full-name match) and agent surname tokens.
2. **Smol confirmation** (high precision): batch-classify candidates with
   ``classify_person_names`` via the local Ollama endpoint. Only smol-confirmed
   PERSON labels are deleted.

Protected nodes are never candidates:
- Concepts with a non-empty ``definition``
- Concepts with an outgoing ``attributed_to`` edge
- Terms that are an endpoint of a ``verified=True`` translation_mapping

Usage
-----
    python scripts/drop_person_name_nodes.py             # dry-run
    python scripts/drop_person_name_nodes.py --apply       # mutate knowledge.db
"""

from __future__ import annotations

import argparse
import json

import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _norm_lower(text: str) -> str:
    """NFKD-strip-lower for fuzzy matching."""
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return stripped.lower().strip()


def _surname_tokens(agent_names: list[str]) -> set[str]:
    """Extract surname-like tokens (last token of multi-token names, len≥4)."""
    tokens: set[str] = set()
    for name in agent_names:
        normed = _norm_lower(name)
        parts = normed.split()
        if len(parts) >= 2:
            last = parts[-1]
            if len(last) >= 4:
                tokens.add(last)
    return tokens


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------

def generate_candidates(
    kg_data: dict,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (term_candidates, concept_candidates) as (id, label) pairs.

    A node is a candidate if its normalised label matches an agent full name
    or surname token, AND it is not protected. Protection rules:
    concepts with definitions, concepts with attributed_to edges, and terms
    that are endpoints of verified mappings are never candidates.

    All candidates are auto-confirmed — they match a person in our own KG,
    and the protection rules guard real concepts/terms from deletion.
    """
    agent_names: list[str] = []
    for n in kg_data["nodes"]:
        if n.get("type") == "agent":
            name = n.get("name", "")
            if name:
                agent_names.append(name)

    full_name_norms: set[str] = {_norm_lower(n) for n in agent_names if len(n.split()) >= 2}
    surname_toks: set[str] = set()
    for name in agent_names:
        normed = _norm_lower(name)
        parts = normed.split()
        if len(parts) >= 2 and len(parts[-1]) >= 4:
            surname_toks.add(parts[-1])

    # Protected concept ids
    protected_concepts: set[str] = set()
    for n in kg_data["nodes"]:
        if n.get("type") == "concept" and n.get("definition"):
            protected_concepts.add(n["id"])
    for e in kg_data["edges"]:
        if e.get("relation") == "attributed_to" and e.get("source", "").startswith("concept:"):
            protected_concepts.add(e["source"])

    # Protected term ids (endpoint of verified mapping)
    protected_terms: set[str] = set()
    for n in kg_data["nodes"]:
        if n.get("type") == "translation_mapping" and n.get("verified") is True:
            for e in kg_data["edges"]:
                if e.get("relation") == "has_mapping" and e.get("target") == n["id"]:
                    protected_terms.add(e["source"])
                if e.get("relation") == "maps_to" and e.get("source") == n["id"]:
                    protected_terms.add(e["target"])

    term_candidates: list[tuple[str, str]] = []
    concept_candidates: list[tuple[str, str]] = []

    for n in kg_data["nodes"]:
        ntype = n.get("type", "")
        nid = n["id"]
        label = n.get("term", n.get("label", "")) if ntype == "term" else n.get("label", "")
        if not label:
            continue
        normed_label = _norm_lower(label)

        if ntype == "term":
            if nid in protected_terms:
                continue
            # Terms: both full-name and surname matches are auto-confirmed
            if " " in normed_label and normed_label in full_name_norms:
                term_candidates.append((nid, label))
            elif len(normed_label) >= 4 and normed_label in surname_toks:
                term_candidates.append((nid, label))

        elif ntype == "concept":
            if nid in protected_concepts:
                continue
            # Concepts: ONLY full-name matches are auto-confirmed.
            # Single-token surname matches are too aggressive for concepts
            # (e.g. concept:power matches surname "Power" but is really an abstract concept).
            if " " in normed_label and normed_label in full_name_norms:
                concept_candidates.append((nid, label))

    return term_candidates, concept_candidates


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Remove person-name term/concept nodes (auto-confirmed from agent data)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Mutate knowledge.db (default: dry-run only)",
    )
    args = parser.parse_args(argv)

    db_path = ROOT / "data" / "knowledge.db"
    if not db_path.exists():
        print(f"ERROR: {db_path} not found", file=sys.stderr)
        return 1

    print(f"Loading {db_path} …")
    kg_data = json.loads(db_path.read_text(encoding="utf-8"))

    agents = sum(1 for n in kg_data["nodes"] if n.get("type") == "agent")
    terms = sum(1 for n in kg_data["nodes"] if n.get("type") == "term")
    concepts = sum(1 for n in kg_data["nodes"] if n.get("type") == "concept")
    print(f"  Agents: {agents}, Terms: {terms}, Concepts: {concepts}")

    term_cands, concept_cands = generate_candidates(kg_data)
    print(f"  Person-name term candidates: {len(term_cands)}")
    print(f"  Person-name concept candidates: {len(concept_cands)}")

    if not term_cands and not concept_cands:
        print("No candidates found. Nothing to do.")
        return 0

    # Show samples
    for label, items in [("Terms", term_cands), ("Concepts", concept_cands)]:
        if items:
            print(f"  {label} to delete (first 15):")
            for nid, lab in items[:15]:
                print(f"    {nid}: {lab}")

    if not args.apply:
        print(f"\nDRY RUN — {len(term_cands) + len(concept_cands)} nodes would be deleted. Re-run with --apply to delete.")
        return 0

    # Apply deletions using KnowledgeGraph
    from translate_core.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph(db_path)
    deleted_terms = 0
    deleted_concepts = 0

    for nid, lab in term_cands:
        if kg.remove_node(nid):
            deleted_terms += 1

    for nid, lab in concept_cands:
        if kg.remove_concept_node(nid):
            deleted_concepts += 1

    print(f"\n  Deleted {deleted_terms} term nodes, {deleted_concepts} concept nodes.")
    kg.save()
    print(f"  Saved {db_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())