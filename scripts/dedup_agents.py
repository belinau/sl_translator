#!/usr/bin/env python3
"""scripts/dedup_agents.py

One-off agent deduplication: merge safe (identical name-token-set) duplicate
agents within each ``dedup_group``, re-pointing all edges onto the canonical
agent and removing the duplicate node.

Safe merge predicate
--------------------
Only agents whose normalised name-token *set* is identical (pure reordering,
e.g. ``zaloznik-jasmina`` ↔ ``jasmina-zaloznik``) are merged.  Ambiguous
groups where members have *different* token sets (e.g. Srećko Horvat vs
Sebastjan Horvat sharing ``s.horvat``) are skipped for curator review.

Canonical pick (deterministic)
-------------------------------
1. Agent whose id matches a curated roster entry (concept_theorists.json or
   lineage_schools.json).
2. Highest ``mention_count``.
3. Most incident edges.
4. Lexicographically smallest id.

Usage
-----
    python scripts/dedup_agents.py --dry-run   # report only
    python scripts/dedup_agents.py --apply      # mutate knowledge.db
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translate_core.entity_extraction._slug import _slugify


# ---------------------------------------------------------------------------
# Name-token normalisation (same logic as name_dedup but using frozenset)
# ---------------------------------------------------------------------------

def _norm_tokens(name: str) -> frozenset[str]:
    """NFKD-strip-lower → split on non-alphanum → frozenset of tokens len≥2."""
    nfkd = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    lowered = stripped.lower()
    tokens = re.sub(r"[^a-z0-9]+", " ", lowered).split()
    return frozenset(t for t in tokens if len(t) >= 2)


# ---------------------------------------------------------------------------
# Roster loading (for canonical-pick tie-breaking)
# ---------------------------------------------------------------------------

def _load_roster_ids() -> set[str]:
    """Return the set of agent ids that appear in the curated rosters."""
    ids: set[str] = set()
    for path in (
        ROOT / "data" / "concept_theorists.json",
        ROOT / "data" / "lineage_schools.json",
    ):
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if "concept_theorists" in str(path):
            # concept_theorists.json is a flat list of theorist dicts with "value"
            if isinstance(data, list):
                for entry in data:
                    slug = _slugify(entry.get("value", ""))
                    if slug:
                        ids.add(f"agent:{slug}")
        elif "lineage_schools" in str(path):
            # lineage_schools.json: dict of theorist_slug → school
            if isinstance(data, dict):
                for slug in data:
                    ids.add(f"agent:{slug}")
    return ids


# ---------------------------------------------------------------------------
# Cluster identification
# ---------------------------------------------------------------------------

def find_safe_clusters(
    agents: list[dict],
) -> tuple[list[list[str]], list[list[str]]]:
    """Return (safe_clusters, unsafe_groups).

    safe_clusters: lists of agent_ids where ≥2 agents share an identical
        normalised name-token set with ≥2 tokens — these are genuine
        same-person duplicates (reorder-variants like
        ``zaloznik-jasmina`` ↔ ``jasmina-zaloznik``).
    unsafe_groups: lists of agent_ids from dedup_groups where NO two
        members share a safe token-set — left for the curator.

    We cluster globally by token-set, not within dedup_group boundaries,
    because the same person can appear in different dedup_groups
    (e.g. ``b.kunst`` vs ``b-kunst``).
    """
    from collections import defaultdict

    # Cluster ALL agents by their name token-set, regardless of dedup_group
    by_ts: dict[frozenset[str], list[dict]] = defaultdict(list)
    for a in agents:
        ts = _norm_tokens(a.get("name", ""))
        if len(ts) >= 2:
            by_ts[ts].append(a)

    safe: list[list[str]] = []
    unsafe_dg_agents: set[str] = set()  # track agents in unsafe dedup_groups

    for ts, cluster in by_ts.items():
        if len(cluster) >= 2:
            safe.append([a["id"] for a in cluster])

    # Identify agents in dedup_groups where no safe cluster exists
    # (these are "ambiguous" groups — different people sharing initials)
    by_dg: dict[str, list[dict]] = defaultdict(list)
    for a in agents:
        dg = a.get("dedup_group", "")
        if dg:
            by_dg[dg].append(a)

    unsafe: list[list[str]] = []
    for dg_key, members in by_dg.items():
        if len(members) < 2:
            continue
        # Check if ANY member in this group was captured by a safe cluster
        member_ids = {m["id"] for m in members}
        in_safe = any(
            member_ids.intersection(sc)
            for sc in safe
        )
        if not in_safe:
            unsafe.append([m["id"] for m in members])

    return safe, unsafe


# ---------------------------------------------------------------------------
# Canonical pick (deterministic)
# ---------------------------------------------------------------------------

def pick_canonical(
    candidate_ids: list[str],
    kg_data: dict[str, Any],
    roster_ids: set[str],
) -> str:
    """Pick the canonical agent id from *candidate_ids* using the priority rules."""
    if len(candidate_ids) == 1:
        return candidate_ids[0]

    node_lookup = {n["id"]: n for n in kg_data["nodes"] if n.get("type") == "agent"}
    candidates = [node_lookup[cid] for cid in candidate_ids if cid in node_lookup]
    if not candidates:
        return sorted(candidate_ids)[0]

    # Rule (a): roster match
    roster_matches = [c for c in candidates if c["id"] in roster_ids]
    if len(roster_matches) == 1:
        return roster_matches[0]["id"]
    if roster_matches:
        candidates = roster_matches

    # Rule (b): highest mention_count
    max_mc = max(c.get("mention_count") or 0 for c in candidates)
    candidates = [c for c in candidates if (c.get("mention_count") or 0) == max_mc]
    if len(candidates) == 1:
        return candidates[0]["id"]

    # Rule (c): most incident edges
    edge_counts: dict[str, int] = {}
    # Build edge count from the raw KG (will be recalculated per-node)
    for e in kg_data.get("edges", []):
        src, tgt = e.get("source", ""), e.get("target", "")
        edge_counts[src] = edge_counts.get(src, 0) + 1
        edge_counts[tgt] = edge_counts.get(tgt, 0) + 1

    max_edges = max(edge_counts.get(c["id"], 0) for c in candidates)
    candidates = [c for c in candidates if edge_counts.get(c["id"], 0) == max_edges]
    if len(candidates) == 1:
        return candidates[0]["id"]

    # Rule (d): lexicographically smallest id
    return sorted(c["id"] for c in candidates)[0]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deduplicate safe agent duplicates in the knowledge graph",
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
    agents = [n for n in kg_data["nodes"] if n.get("type") == "agent"]
    print(f"  Total agents: {len(agents)}")

    roster_ids = _load_roster_ids()
    print(f"  Roster agent ids: {len(roster_ids)}")

    safe_clusters, unsafe_groups = find_safe_clusters(agents)
    total_mergeable = sum(len(sc) for sc in safe_clusters)
    print(f"  Safe merge clusters: {len(safe_clusters)}")
    print(f"  Agents in safe clusters: {total_mergeable}")
    print(f"  Unsafe groups (skipped): {len(unsafe_groups)}")

    if unsafe_groups:
        node_lookup = {n["id"]: n for n in agents}
        print("\n  Skipped (ambiguous) groups:")
        for ids in unsafe_groups[:10]:
            names = []
            for cid in ids:
                n = node_lookup.get(cid)
                names.append(n.get("name", cid) if n else cid)
            print(f"    {names}")
        if len(unsafe_groups) > 10:
            print(f"    … and {len(unsafe_groups) - 10} more")

    # Compute merges
    merges: list[tuple[str, str]] = []  # (canonical, duplicate)
    for cluster_ids in safe_clusters:
        canonical = pick_canonical(cluster_ids, kg_data, roster_ids)
        for cid in cluster_ids:
            if cid != canonical:
                merges.append((canonical, cid))
    print(f"\n  Merges to perform: {len(merges)}")
    if not merges:
        print("Nothing to do.")
        return 0

    # Show a sample
    print("  Sample merges (first 10):")
    node_lookup = {n["id"]: n for n in agents}
    for can, dup in merges[:10]:
        can_name = node_lookup.get(can, {}).get("name", can)
        dup_name = node_lookup.get(dup, {}).get("name", dup)
        print(f"    {dup_name} ({dup}) → {can_name} ({can})")

    if not args.apply:
        print("\nDRY RUN — no changes made. Re-run with --apply to merge.")
        return 0

    # Apply merges using KnowledgeGraph
    from translate_core.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph(db_path)
    merged = 0
    for canonical, duplicate in merges:
        if kg.merge_agent_nodes(canonical, duplicate):
            merged += 1
        else:
            print(f"  WARNING: merge failed for {duplicate} → {canonical}")

    print(f"\n  Merged {merged} duplicate agents into their canonicals.")
    kg.save()
    print(f"  Saved {db_path}")

    # Verify
    agents_after = [n for n in kg.G.nodes(data=True) if n[1].get("type") == "agent"]
    print(f"  Agents remaining: {len(agents_after)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())