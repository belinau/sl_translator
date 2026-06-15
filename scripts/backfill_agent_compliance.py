#!/usr/bin/env python3
"""Backfill the O-12 agent quartet on existing bare-agent nodes.

Ontology O-12 requires every agent node to carry:
  dedup_group, alt_spellings, all_roles, mention_count

This script fills missing values conservatively without merging agents:
  dedup_group   -> dedup_group_key(name)
  alt_spellings -> [name] unioned with any existing alt_spellings
  all_roles     -> [role] unioned with any existing all_roles
  mention_count -> existing value or 1

Idempotent. Dry-run by default; use --apply to persist.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from translate_core.entity_extraction.name_dedup import dedup_group_key
from translate_core.knowledge_graph import KnowledgeGraph

DEFAULT_KG = ROOT / "data" / "knowledge.db"
AGENT_REQUIRED = ("dedup_group", "alt_spellings", "all_roles", "mention_count")


def backfill(kg: KnowledgeGraph, *, apply: bool) -> dict:
    fixed = {k: 0 for k in AGENT_REQUIRED}
    fixed["nodes_touched"] = 0

    for nid in list(kg.G.nodes):
        data = kg.G.nodes[nid]
        if data.get("type") != "agent":
            continue

        name = data.get("name", "")
        role = data.get("role", "agent")
        changed = False

        if "dedup_group" not in data:
            data["dedup_group"] = dedup_group_key(name) if name else ""
            fixed["dedup_group"] += 1
            changed = True

        if "alt_spellings" not in data:
            alts = [name] if name else []
            data["alt_spellings"] = alts
            fixed["alt_spellings"] += 1
            changed = True

        if "all_roles" not in data:
            roles = [role] if role else ["agent"]
            data["all_roles"] = roles
            fixed["all_roles"] += 1
            changed = True
        elif role and role not in data["all_roles"]:
            data["all_roles"].append(role)
            changed = True

        if "mention_count" not in data:
            data["mention_count"] = data.get("mention_count", 1) or 1
            fixed["mention_count"] += 1
            changed = True

        if changed:
            fixed["nodes_touched"] += 1

    if apply and fixed["nodes_touched"]:
        bak_path = kg.db_path.with_suffix(kg.db_path.suffix + ".bak")
        if not bak_path.exists() and kg.db_path.exists():
            bak_path.write_text(kg.db_path.read_text(encoding="utf-8"), encoding="utf-8")
        kg.save()

    return fixed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_KG,
        help=f"Path to knowledge.db (default: {DEFAULT_KG})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist changes; without this, prints what would change (dry run).",
    )
    args = parser.parse_args()

    if not args.path.exists():
        print(f"KG not found at {args.path}")
        return 1

    kg = KnowledgeGraph(args.path)
    fixed = backfill(kg, apply=args.apply)

    mode = "applied" if args.apply else "dry-run"
    print(
        f"[{mode}] Touched {fixed['nodes_touched']} agent node(s): "
        f"dedup_group={fixed['dedup_group']}, "
        f"alt_spellings={fixed['alt_spellings']}, "
        f"all_roles={fixed['all_roles']}, "
        f"mention_count={fixed['mention_count']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
