#!/usr/bin/env python3
"""Build rhizomatic concept→concept edges from grounded KG signals only.

Three signals, zero LLM:

1. SHARED THEORIST — both concepts attributed_to the same agent.
   Strongest signal. Two concepts from the same thinker are intellectually
   connected by definition.

2. CO-OCCURRENCE — both concepts appear in the same source_text via
   term→mapping→instantiated_in. Requires ≥2 independent co-occurrences
   to filter combinatorial noise from single prolific works.

3. SAME SCHOOL — both concepts attributed to theorists in the same
   school (lineage_schools.json roster). Weakest signal; only used when
   no stronger signal exists for the pair.

All edges are `related_to` — directional relations (extends, critiques,
redefines) require domain knowledge we don't have in the data.

All writes through KG factory methods (O-1 compliant).
Idempotent: skips pairs that already have a rhizomatic edge.

Usage:
  python scripts/build_rhizomatic_edges.py [--dry-run]
"""
from __future__ import annotations

import json
import logging
import pathlib
import re
import sys
import unicodedata
from collections import defaultdict
from itertools import combinations

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from translate_core.knowledge_graph import KnowledgeGraph

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

DRY_RUN = "--dry-run" in sys.argv
DATA_DIR = pathlib.Path("data")

RHIZO_RELS = frozenset({"extends", "critiques", "redefines", "reappropriates", "related_to"})

# Co-occurrence threshold: concept pair must appear together in ≥N works.
MIN_COOCCUR = 2


def _norm(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c)
    )


# ---------------------------------------------------------------------------
# Load KG
# ---------------------------------------------------------------------------
kg = KnowledgeGraph()
G = kg.G

log.info(f"Nodes: {G.number_of_nodes()}, Edges: {G.number_of_edges()}")

# ---------------------------------------------------------------------------
# Build indexes
# ---------------------------------------------------------------------------

# term → concept
term_concept: dict[str, str] = {}
# mapping → src term, tgt term
map_src: dict[str, str] = {}
map_tgt: dict[str, str] = {}
# mapping → source_text (via instantiated_in)
map_work: dict[str, str] = {}

for u, v, d in G.edges(data=True):
    rel = d.get("relation")
    if rel == "instantiates_concept":
        term_concept[u] = v
    elif rel == "has_mapping":
        map_src[v] = u
    elif rel == "maps_to":
        map_tgt[u] = v
    elif rel == "instantiated_in":
        map_work[u] = v

# concept → agents (attributed_to)
concept_agents: dict[str, set[str]] = defaultdict(set)
for u, v, d in G.edges(data=True):
    if d.get("relation") == "attributed_to" and u.startswith("concept:"):
        concept_agents[u].add(v)

# Existing rhizomatic edges
existing_rhizo: set[tuple[str, str]] = set()
for u, v, d in G.edges(data=True):
    if d.get("relation") in RHIZO_RELS:
        existing_rhizo.add((u, v))
        existing_rhizo.add((v, u))

log.info(f"Existing rhizomatic edges: {len(existing_rhizo) // 2}")

# Load roster
roster_path = DATA_DIR / "lineage_schools.json"
agent_school: dict[str, str] = {}
if roster_path.exists():
    roster = json.loads(roster_path.read_text("utf-8"))
    for name, school in roster.items():
        slug = re.sub(r"[^a-z0-9]+", "-", _norm(name)).strip("-")
        if slug:
            agent_school[f"agent:{slug}"] = school

# ---------------------------------------------------------------------------
# Signal 1: SHARED THEORIST
# ---------------------------------------------------------------------------
log.info("Signal 1: shared theorist...")
agent_concepts: dict[str, set[str]] = defaultdict(set)
for cid, agents in concept_agents.items():
    for aid in agents:
        agent_concepts[aid].add(cid)

shared_theorist_pairs: set[tuple[str, str]] = set()
for aid, concepts in agent_concepts.items():
    if len(concepts) < 2:
        continue
    for a, b in combinations(sorted(concepts), 2):
        if (a, b) not in existing_rhizo:
            shared_theorist_pairs.add((a, b))

log.info(f"  Shared theorist pairs: {len(shared_theorist_pairs)}")

# ---------------------------------------------------------------------------
# Signal 2: CO-OCCURRENCE in works
# ---------------------------------------------------------------------------
log.info("Signal 2: co-occurrence in works...")
work_concepts: dict[str, set[str]] = defaultdict(set)
for mid, wid in map_work.items():
    for tid in (map_src.get(mid), map_tgt.get(mid)):
        if tid:
            cid = term_concept.get(tid)
            if cid:
                work_concepts[wid].add(cid)

cooccur_count: dict[tuple[str, str], int] = defaultdict(int)
for wid, concepts in work_concepts.items():
    if len(concepts) < 2:
        continue
    for a, b in combinations(sorted(concepts), 2):
        cooccur_count[(a, b)] += 1

cooccur_pairs = {
    pair for pair, count in cooccur_count.items()
    if count >= MIN_COOCCUR and pair not in existing_rhizo
}
log.info(f"  Co-occurrence pairs (≥{MIN_COOCCUR} works): {len(cooccur_pairs)}")

# ---------------------------------------------------------------------------
# Signal 3: SAME SCHOOL
# ---------------------------------------------------------------------------
log.info("Signal 3: same school (roster)...")
concept_schools: dict[str, set[str]] = defaultdict(set)
for cid, agents in concept_agents.items():
    for aid in agents:
        school = agent_school.get(aid)
        if school:
            concept_schools[cid].add(school)

school_concepts: dict[str, set[str]] = defaultdict(set)
for cid, schools in concept_schools.items():
    for s in schools:
        school_concepts[s].add(cid)

same_school_pairs: set[tuple[str, str]] = set()
for school, concepts in school_concepts.items():
    if len(concepts) < 2:
        continue
    for a, b in combinations(sorted(concepts), 2):
        if (a, b) not in existing_rhizo:
            same_school_pairs.add((a, b))

log.info(f"  Same school pairs: {len(same_school_pairs)}")

# ---------------------------------------------------------------------------
# Merge and deduplicate
# ---------------------------------------------------------------------------
# Priority: shared_theorist > cooccur > same_school
# All become related_to — the signal source is recorded as edge data.
all_pairs: dict[tuple[str, str], str] = {}

for pair in same_school_pairs:
    all_pairs[pair] = "same_school"
for pair in cooccur_pairs:
    all_pairs[pair] = "co_occurrence"
for pair in shared_theorist_pairs:
    all_pairs[pair] = "shared_theorist"

log.info(f"\nTotal unique pairs to create: {len(all_pairs)}")
by_signal = defaultdict(int)
for signal in all_pairs.values():
    by_signal[signal] += 1
for signal, count in sorted(by_signal.items()):
    log.info(f"  {signal}: {count}")

if DRY_RUN:
    log.info("\n--dry-run: no changes written.")
    # Show samples per signal
    for signal in ("shared_theorist", "co_occurrence", "same_school"):
        samples = [(a, b) for (a, b), s in all_pairs.items() if s == signal][:5]
        log.info(f"\n  Sample {signal}:")
        for a, b in samples:
            la = G.nodes.get(a, {}).get("label", a)
            lb = G.nodes.get(b, {}).get("label", b)
            log.info(f"    {la} ↔ {lb}")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Write edges
# ---------------------------------------------------------------------------
created = 0
for (a, b), signal in all_pairs.items():
    kg.link_concepts_rhizomatic(a, b, "related_to")
    created += 1

    if created % 1000 == 0:
        log.info(f"  Created {created} edges...")

kg.save()
log.info(f"\nDone: {created} rhizomatic edges created.")
