#!/usr/bin/env python3
"""scripts/prune_noise_mappings.py

One-off pruning of co-occurrence-noise translation mappings from the KG.

Keep a mapping iff:
  * ``verified is True``, OR
  * ``confidence >= 0.5`` AND src-term fan-out ≤ 2 AND both endpoint terms exist.

Delete everything else.  Then sweep orphan terms (zero has_mapping out-edges,
zero maps_to in-edges, zero instantiates_concept out-edges).

Constants ``MIN_CONF = 0.5`` and ``MAX_FANOUT = 2`` are grounded in the
measured fan-out distribution of the live KG (see plan).

Usage
-----
    python scripts/prune_noise_mappings.py             # dry-run
    python scripts/prune_noise_mappings.py --apply       # mutate knowledge.db
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIN_CONF = 0.5
MAX_FANOUT = 2  # Fan-out ≤2: src-term→1 has 3022 mappings, →2 has 1037; tail is noise.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prune noise translation mappings from the KG",
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

    nodes = kg_data["nodes"]
    edges = kg_data["edges"]

    node_lookup = {n["id"]: n for n in nodes}
    total_mappings = sum(1 for n in nodes if n.get("type") == "translation_mapping")
    print(f"  Total mapping nodes: {total_mappings}")

    # ── Build per-src-term fan-out among conf≥0.5 unverified mappings ──
    src_term_map: dict[str, list[str]] = {}  # src_term_id -> [mapping_id, ...]
    mapping_src: dict[str, str] = {}  # mapping_id -> src_term_id
    mapping_tgt: dict[str, str] = {}  # mapping_id -> tgt_term_id

    for e in edges:
        if e.get("relation") == "has_mapping":
            # src_term -[has_mapping]-> mapping
            mapping_src[e["target"]] = e["source"]
            src_term_map.setdefault(e["source"], []).append(e["target"])
        elif e.get("relation") == "maps_to":
            # mapping -[maps_to]-> tgt_term
            mapping_tgt[e["source"]] = e["target"]

    # Fan-out: distinct tgt_terms among conf≥0.5 unverified
    fanout: Counter = Counter()
    for mid, mnode in node_lookup.items():
        if mnode.get("type") != "translation_mapping":
            continue
        if mnode.get("verified") is True:
            continue
        conf = mnode.get("confidence") or 0
        if conf < MIN_CONF:
            continue
        src = mapping_src.get(mid)
        if src:
            fanout[src] += 1

    # ── Classify each mapping ──
    keep_ids: set[str] = set()
    delete_ids: set[str] = set()

    verified_count = 0
    structural_count = 0
    low_conf_count = 0
    high_fanout_count = 0
    missing_endpoint_count = 0

    for n in nodes:
        if n.get("type") != "translation_mapping":
            continue
        mid = n["id"]

        if n.get("verified") is True:
            keep_ids.add(mid)
            verified_count += 1
            continue

        conf = n.get("confidence") or 0

        # Low confidence
        if conf < MIN_CONF:
            delete_ids.add(mid)
            low_conf_count += 1
            continue

        src = mapping_src.get(mid)
        tgt = mapping_tgt.get(mid)

        # Check fan-out
        src_fanout = fanout.get(src, 0)
        if src_fanout > MAX_FANOUT:
            delete_ids.add(mid)
            high_fanout_count += 1
            continue

        # Check both endpoints exist
        if src not in node_lookup or tgt not in node_lookup:
            delete_ids.add(mid)
            missing_endpoint_count += 1
            continue

        # Structural survivor
        keep_ids.add(mid)
        structural_count += 1

    print("\n  Classification:")
    print(f"    Verified keep:        {verified_count}")
    print(f"    Structural keep:      {structural_count}")
    print(f"    Low confidence (<{MIN_CONF}):   {low_conf_count}")
    print(f"    High fan-out (>{MAX_FANOUT}):     {high_fanout_count}")
    print(f"    Missing endpoint:     {missing_endpoint_count}")
    print(f"    Total keep:           {len(keep_ids)}")
    print(f"    Total delete:         {len(delete_ids)}")

    # ── Orphan term detection ──
    # After deleting mappings, find terms with no has_mapping out, no maps_to in, no instantiates_concept out
    term_ids = {n["id"] for n in nodes if n.get("type") == "term"}

    # Build edge indexes for terms
    term_has_mapping_out: set[str] = set()  # terms with has_mapping out-edge
    term_maps_to_in: set[str] = set()  # terms with maps_to in-edge
    term_inst_concept_out: set[str] = set()  # terms with instantiates_concept out-edge

    for e in edges:
        rel = e.get("relation", "")
        if rel == "has_mapping":
            term_has_mapping_out.add(e["source"])
        elif rel == "maps_to":
            term_maps_to_in.add(e["target"])
        elif rel == "instantiates_concept":
            term_inst_concept_out.add(e["source"])

    # Orphan terms: no mapping edges AND no concept edges, AND not in keep set
    orphan_term_ids: set[str] = set()
    for tid in term_ids:
        if tid in term_has_mapping_out or tid in term_maps_to_in or tid in term_inst_concept_out:
            continue
        orphan_term_ids.add(tid)

    print(f"    Orphan terms (no edges): {len(orphan_term_ids)}")

    # Show fan-out distribution for kept terms
    print("\n  Fan-out distribution of structural survivors:")
    for fo in sorted(set(fanout[src] for src in fanout if fanout[src] <= MAX_FANOUT + 2)):
        count = sum(1 for s, c in fanout.items() if c == fo)
        print(f"    fan-out={fo}: {count} src-terms")

    if not args.apply:
        print(f"\nDRY RUN — {len(delete_ids)} mappings and {len(orphan_term_ids)} orphan terms would be deleted. Re-run with --apply.")
        return 0

    # ── Apply deletions ──
    from translate_core.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph(db_path)

    # Delete mappings
    deleted_mappings = 0
    for mid in delete_ids:
        if kg.remove_node(mid):
            deleted_mappings += 1

    # Delete orphan terms
    deleted_terms = 0
    for tid in orphan_term_ids:
        if kg.remove_node(tid):
            deleted_terms += 1

    print(f"\n  Deleted {deleted_mappings} mapping nodes, {deleted_terms} orphan term nodes.")
    kg.save()
    print(f"  Saved {db_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())