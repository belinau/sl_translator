#!/usr/bin/env python3
"""Fix translation_mapping lineage fields that carry source-format labels
(glossary, curatorial-exhibition, visual-art) instead of intellectual traditions.

Uses the concept→theorist→school path (lineage_schools.json roster) to find
the correct school. Only patches mappings where a clear signal exists.

Respects O-1: all writes go through KnowledgeGraph factory methods.
Respects O-3: verified is monotonic — preserved across re-creation.
"""
from __future__ import annotations

import json
import logging
import pathlib
import re
import sys
import unicodedata
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from translate_core.knowledge_graph import KnowledgeGraph

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SOURCE_FORMAT_LINEAGES = {"glossary", "curatorial-exhibition", "visual-art"}
DEFAULT_LINEAGES = {"performance", "general", "manual", ""}
DATA_DIR = pathlib.Path("data")
DRY_RUN = "--dry-run" in sys.argv


def _norm(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c)
    )


# ---------------------------------------------------------------------------
# Load roster: theorist name → school
# ---------------------------------------------------------------------------
roster_path = DATA_DIR / "lineage_schools.json"
if not roster_path.exists():
    log.error(f"Roster not found: {roster_path}")
    sys.exit(1)

roster: dict[str, str] = json.loads(roster_path.read_text("utf-8"))
agent_school: dict[str, str] = {}
for name, school in roster.items():
    slug = re.sub(r"[^a-z0-9]+", "-", _norm(name)).strip("-")
    if slug:
        agent_school[f"agent:{slug}"] = school

log.info(f"Loaded roster: {len(agent_school)} agent→school mappings")

# ---------------------------------------------------------------------------
# Load KG and build indexes
# ---------------------------------------------------------------------------
kg = KnowledgeGraph()
G = kg.G

# term → concept
term_to_concept: dict[str, str] = {}
# mapping → src_term, tgt_term
mapping_src: dict[str, str] = {}
mapping_tgt: dict[str, str] = {}
# mapping → bridge edges
mapping_instantiated_in: dict[str, list[str]] = defaultdict(list)
mapping_attributed_to: dict[str, list[str]] = defaultdict(list)

for u, v, d in G.edges(data=True):
    rel = d.get("relation")
    if rel == "instantiates_concept":
        term_to_concept[u] = v
    elif rel == "has_mapping":
        mapping_src[v] = u  # term → mapping, so mapping's src is u
    elif rel == "maps_to":
        mapping_tgt[u] = v  # mapping → term
    elif rel == "instantiated_in":
        mapping_instantiated_in[u].append(v)
    elif rel == "attributed_to" and u.startswith("map:"):
        mapping_attributed_to[u].append(v)

# concept → attributed_to → agent → school
concept_school: dict[str, str] = {}
for u, v, d in G.edges(data=True):
    if d.get("relation") != "attributed_to":
        continue
    if not u.startswith("concept:") or not v.startswith("agent:"):
        continue
    school = agent_school.get(v)
    if school and school not in DEFAULT_LINEAGES and school not in SOURCE_FORMAT_LINEAGES:
        # First school wins per concept (could aggregate, but dominant is fine)
        if u not in concept_school:
            concept_school[u] = school

log.info(f"Concepts with known school: {len(concept_school)}")

# ---------------------------------------------------------------------------
# Find mappings to fix
# ---------------------------------------------------------------------------
to_fix: list[dict] = []

for node_id, data in list(G.nodes(data=True)):
    if data.get("type") != "translation_mapping":
        continue
    lineage = (data.get("lineage") or "").strip()
    if lineage not in SOURCE_FORMAT_LINEAGES:
        continue

    src_term = mapping_src.get(node_id)
    tgt_term = mapping_tgt.get(node_id)
    if not src_term or not tgt_term:
        continue

    # Find concepts linked to either term
    best_school = None
    for tid in (src_term, tgt_term):
        cid = term_to_concept.get(tid)
        if cid and cid in concept_school:
            best_school = concept_school[cid]
            break

    if not best_school:
        continue

    to_fix.append({
        "old_id": node_id,
        "src_term": src_term,
        "tgt_term": tgt_term,
        "old_lineage": lineage,
        "new_lineage": best_school,
        "confidence": data.get("confidence", 0.8),
        "register": data.get("register", "academic"),
        "gloss": data.get("gloss"),
        "year": data.get("year"),
        "verified": data.get("verified", False),
        "inst_in": mapping_instantiated_in.get(node_id, []),
        "attr_to": mapping_attributed_to.get(node_id, []),
    })

# Group by lineage transition for reporting
transitions: Counter = Counter()
for item in to_fix:
    transitions[(item["old_lineage"], item["new_lineage"])] += 1

log.info(f"\nMappings to fix: {len(to_fix)}")
log.info("Transitions:")
for (old, new), count in transitions.most_common():
    log.info(f"  {old} → {new}: {count}")

if DRY_RUN:
    log.info("\n--dry-run: no changes written.")
    # Show sample
    for item in to_fix[:10]:
        log.info(f"  {item['old_id']}")
        log.info(f"    {item['old_lineage']} → {item['new_lineage']}")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Apply fixes via factory method
# ---------------------------------------------------------------------------
fixed = 0
skipped = 0

for item in to_fix:
    old_id = item["old_id"]

    # 1. Remove old node (and all its edges)
    if not G.has_node(old_id):
        skipped += 1
        continue
    G.remove_node(old_id)

    # 2. Re-create via factory with corrected lineage
    new_id = kg.link_translations_with_context(
        src_term_id=item["src_term"],
        tgt_term_id=item["tgt_term"],
        confidence=item["confidence"],
        lineage=item["new_lineage"],
        register=item["register"],
        gloss=item["gloss"],
        year=item["year"],
        verified=item["verified"],
    )

    if not new_id:
        log.warning(f"  Factory returned empty for {item['src_term']} → {item['tgt_term']}")
        skipped += 1
        continue

    # 3. Re-wire bridge edges
    for st_id in item["inst_in"]:
        if G.has_node(st_id) and not G.has_edge(new_id, st_id):
            G.add_edge(new_id, st_id, relation="instantiated_in")

    for ag_id in item["attr_to"]:
        if G.has_node(ag_id) and not G.has_edge(new_id, ag_id):
            G.add_edge(new_id, ag_id, relation="attributed_to")

    fixed += 1

log.info(f"\nFixed: {fixed}, Skipped: {skipped}")
kg.save()
log.info("Saved.")
