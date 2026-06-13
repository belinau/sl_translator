#!/usr/bin/env python3
"""Build rhizomatic concept→concept edges from grounded KG signals only.

Single signal, zero LLM:

  SHARED THEORIST — both concepts attributed_to the same agent.
  If the same thinker developed both concepts, they are intellectually
  connected by definition. This is the only signal that guarantees
  correctness without domain-specific heuristics.

  Co-occurrence (same work) and same-school (roster) were tested and
  rejected: co-occurrence in omnibus containers (festival catalogues,
  anthologies) creates combinatorial noise; same-school is too coarse
  (e.g. "post-structuralism" lumps Foucault, Derrida, Bataille into
  one blob of 61 concepts).

Only concepts with at least one attributed_to edge (vetted concepts)
participate. All edges are `related_to`. All writes through KG factory
methods (O-1 compliant). Idempotent.

Usage:
  python scripts/build_rhizomatic_edges.py [--dry-run]
"""
from __future__ import annotations

import logging
import pathlib
import sys
from collections import defaultdict
from itertools import combinations

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from translate_core.knowledge_graph import KnowledgeGraph

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

DRY_RUN = "--dry-run" in sys.argv
RHIZO_RELS = frozenset({"extends", "critiques", "redefines", "reappropriates", "related_to"})

# ---------------------------------------------------------------------------
# Load KG
# ---------------------------------------------------------------------------
kg = KnowledgeGraph()
G = kg.G
log.info(f"Nodes: {G.number_of_nodes()}, Edges: {G.number_of_edges()}")

# concept → agents (attributed_to)
concept_agents: dict[str, set[str]] = defaultdict(set)
for u, v, d in G.edges(data=True):
    if d.get("relation") == "attributed_to" and u.startswith("concept:"):
        concept_agents[u].add(v)

vetted = frozenset(concept_agents.keys())
log.info(f"Vetted concepts (have attributed_to): {len(vetted)}")

# Existing rhizomatic edges
existing_rhizo: set[tuple[str, str]] = set()
for u, v, d in G.edges(data=True):
    if d.get("relation") in RHIZO_RELS:
        existing_rhizo.add((u, v))
        existing_rhizo.add((v, u))
log.info(f"Existing rhizomatic edges: {len(existing_rhizo) // 2}")

# ---------------------------------------------------------------------------
# SHARED THEORIST: both concepts attributed_to the same agent
# ---------------------------------------------------------------------------
log.info("Building shared-theorist pairs...")
agent_concepts: dict[str, set[str]] = defaultdict(set)
for cid, agents in concept_agents.items():
    for aid in agents:
        agent_concepts[aid].add(cid)

pairs: set[tuple[str, str]] = set()
for aid, concepts in agent_concepts.items():
    if len(concepts) < 2:
        continue
    for a, b in combinations(sorted(concepts), 2):
        if (a, b) not in existing_rhizo:
            pairs.add((a, b))

log.info(f"New pairs to create: {len(pairs)}")

if DRY_RUN:
    log.info("--dry-run: no changes written.")
    for a, b in sorted(pairs)[:15]:
        la = G.nodes.get(a, {}).get("label", a)
        lb = G.nodes.get(b, {}).get("label", b)
        log.info(f"  {la} ↔ {lb}")
    sys.exit(0)

created = 0
for a, b in pairs:
    kg.link_concepts_rhizomatic(a, b, "related_to")
    created += 1

kg.save()
log.info(f"Done: {created} rhizomatic edges created.")
