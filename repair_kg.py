#!/usr/bin/env python3
"""
repair_kg.py — Repair corrupted knowledge.db

The bug: In KnowledgeGraph.save(), the edge serialization does:
    {"source": u, "target": v, **self.G.edges[u, v]}
The edge data dict (from link_translations) also contains a "source" key
(provenance metadata, default "auto"). The ** unpacking overwrites the
networkx source-node ID, so every translates_to edge has its "source" field
set to "auto" instead of the actual source term node ID (e.g. "term:en:xxx").

This script provides two modes:
  --analyze   Show stats about the corrupted graph without modifying anything.
  (default)   Create a timestamped backup, delete the corrupted file,
              and print instructions to re-seed.

Usage:
  python repair_kg.py             # backup + delete + instructions
  python repair_kg.py --analyze   # stats only, no changes
"""

import argparse
import json
import pathlib
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR = pathlib.Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
KG_DB_PATH = DATA_DIR / "knowledge.db"

# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------


def load_graph(path: pathlib.Path) -> dict | None:
    """Load the JSON graph file. Returns None if missing or broken."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[ERROR] Could not parse {path}: {exc}")
        return None


def analyze(raw: dict) -> None:
    """Print detailed stats about the corrupted graph."""
    nodes = raw.get("nodes", [])
    edges = raw.get("edges", [])

    # --- Node stats ---
    node_types = Counter(n.get("type", "unknown") for n in nodes)
    print("\n=== Node Stats ===")
    print(f"  Total nodes: {len(nodes)}")
    for ntype, count in node_types.most_common():
        print(f"    {ntype}: {count}")

    # --- Edge stats ---
    relations = Counter(e.get("relation", "(none)") for e in edges)
    print("\n=== Edge Stats ===")
    print(f"  Total edges: {len(edges)}")
    for rel, count in relations.most_common():
        print(f"    {rel}: {count}")

    # --- translates_to corruption analysis ---
    translates_edges = [e for e in edges if e.get("relation") == "translates_to"]
    corrupted = [e for e in translates_edges if e.get("source") == "auto"]
    intact = [e for e in translates_edges if e.get("source") != "auto"]

    print("\n=== translates_to Corruption ===")
    print(f"  Total translates_to edges: {len(translates_edges)}")
    print(f"  Corrupted (source='auto'): {len(corrupted)}")
    print(f"  Intact (source != 'auto'): {len(intact)}")

    if corrupted:
        # Show how many unique targets are affected
        targets = set(e.get("target") for e in corrupted)
        sources_still_present = set(e.get("source") for e in corrupted)
        print(f"  Unique SL targets affected: {len(targets)}")
        print(f"  Unique 'source' values (should be term IDs): {sources_still_present}")

        # Sample a few corrupted edges
        print("\n  Sample corrupted edges:")
        for edge in corrupted[:5]:
            print(
                f"    source={edge.get('source')!r}  ->  "
                f"target={edge.get('target')!r}  "
                f"confidence={edge.get('confidence', '?')}"
            )
        if len(corrupted) > 5:
            print(f"    ... and {len(corrupted) - 5} more")

    # --- Other edge types check ---
    other_edges = [e for e in edges if e.get("relation") != "translates_to"]
    other_source_auto = [e for e in other_edges if e.get("source") == "auto"]
    if other_source_auto:
        print(
            f"  WARNING: {len(other_source_auto)} non-translates_to edges also "
            f"have source='auto' (may be corrupted too)"
        )

    # --- File size ---
    file_size = KG_DB_PATH.stat().st_size
    print("\n=== File ===")
    print(f"  Path: {KG_DB_PATH}")
    print(f"  Size: {file_size:,} bytes ({file_size / 1024 / 1024:.1f} MB)")

    # --- Verdict ---
    print("\n=== Verdict ===")
    if len(corrupted) > 0:
        pct = len(corrupted) / max(1, len(translates_edges)) * 100
        print(
            f"  CORRUPTED: {len(corrupted)}/{len(translates_edges)} "
            f"({pct:.1f}%) translates_to edges have lost their source node."
        )
        print(
            "  CAUSE: KnowledgeGraph.save() serialises edges as "
            '{"source": u, "target": v, **edge_data}'
        )
        print(
            "         but link_translations() stores provenance in edge_data['source'],"
        )
        print("         so the node ID gets overwritten by the string 'auto'.")
        print()
        print("  RECOMMENDATION: Delete and re-seed from TM + glossary.")
        print(f"    python {BASE_DIR / 'seed_kg.py'}")
    elif len(translates_edges) == 0:
        print("  No translates_to edges found. Graph may be empty or un-seeded.")
    else:
        print("  Graph appears HEALTHY — no corrupted translates_to edges detected.")


# ---------------------------------------------------------------------------
# Repair action
# ---------------------------------------------------------------------------


def repair(raw: dict) -> None:
    """Backup the corrupted file, delete it, and print re-seed instructions."""
    if not KG_DB_PATH.exists():
        print(f"[OK] {KG_DB_PATH} does not exist — nothing to repair.")
        return

    # First run analysis so the user sees what's happening
    analyze(raw)

    # Check if actually corrupted
    edges = raw.get("edges", [])
    translates_edges = [e for e in edges if e.get("relation") == "translates_to"]
    corrupted = [e for e in translates_edges if e.get("source") == "auto"]

    if not corrupted:
        print("\n[OK] Graph is not corrupted — no action needed.")
        return

    # Create timestamped backup
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = DATA_DIR / f"knowledge.db.backup.{timestamp}"
    print("\n=== Repairing ===")
    print(f"  Creating backup: {backup_path}")
    shutil.copy2(KG_DB_PATH, backup_path)
    print(f"  Backup size: {backup_path.stat().st_size:,} bytes")

    # Delete corrupted file
    print(f"  Deleting corrupted file: {KG_DB_PATH}")
    KG_DB_PATH.unlink()

    print("\n=== Done ===")
    print(f"  Corrupted file backed up to: {backup_path}")
    print(f"  {KG_DB_PATH} has been removed.")
    print()
    print("  To rebuild the knowledge graph from your TM and glossary data, run:")
    print(f"    cd {BASE_DIR}")
    print("    python seed_kg.py")
    print()
    print("  Note: Re-seeding requires Spacy (en_core_web_sm) and Classla/Stanza")
    print("  for Slovenian. It will process all TM entries to rebuild nodes and edges.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Repair or analyze corrupted knowledge.db",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python repair_kg.py             # backup + delete + instructions\n"
            "  python repair_kg.py --analyze   # stats only, no changes\n"
        ),
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Show stats about the corrupted graph without modifying anything.",
    )
    args = parser.parse_args()

    if not KG_DB_PATH.exists():
        print(f"[INFO] {KG_DB_PATH} does not exist — nothing to do.")
        print("  Run seed_kg.py to create a fresh knowledge graph.")
        sys.exit(0)

    file_size = KG_DB_PATH.stat().st_size
    print(f"Loading {KG_DB_PATH} ({file_size / 1024 / 1024:.1f} MB)...")

    raw = load_graph(KG_DB_PATH)
    if raw is None:
        print("[ERROR] Could not load graph. Check file format.")
        sys.exit(1)

    print(
        f"Loaded {len(raw.get('nodes', []))} nodes, {len(raw.get('edges', []))} edges."
    )

    if args.analyze:
        analyze(raw)
    else:
        repair(raw)


if __name__ == "__main__":
    main()
