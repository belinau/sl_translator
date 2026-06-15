#!/usr/bin/env python3
"""One-off migration: rename legacy ``sl_published_by`` edges to ``translation_published_by``.

``sl_published_by`` was the old name for the Slovenian/translation-edition
publisher relation. The ontology now only defines ``translation_published_by``,
and ``scripts/validate_kg.py`` treats any ``sl_published_by`` edge as an
unknown relation (hard invariant violation).

This script renames the relation in-place. Because ``KnowledgeGraph`` uses a
non-multigraph DiGraph, each directed pair can carry only one edge, so there
is no duplicate-risk.

Idempotent. Dry-run by default; use --apply to persist.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from translate_core.knowledge_graph import KnowledgeGraph

DEFAULT_KG = ROOT / "data" / "knowledge.db"
OLD_REL = "sl_published_by"
NEW_REL = "translation_published_by"


def migrate(kg: KnowledgeGraph, *, apply: bool) -> int:
    renamed = 0
    for u, v, d in list(kg.G.edges(data=True)):
        if d.get("relation") == OLD_REL:
            d["relation"] = NEW_REL
            renamed += 1

    if apply and renamed:
        bak_path = kg.db_path.with_suffix(kg.db_path.suffix + ".bak")
        if not bak_path.exists() and kg.db_path.exists():
            bak_path.write_text(kg.db_path.read_text(encoding="utf-8"), encoding="utf-8")
        kg.save()

    return renamed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path", type=Path, default=DEFAULT_KG,
        help=f"Path to knowledge.db (default: {DEFAULT_KG})",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Persist changes; without this, prints what would change (dry run).",
    )
    args = parser.parse_args()

    if not args.path.exists():
        print(f"KG not found at {args.path}")
        return 1

    kg = KnowledgeGraph(args.path)
    renamed = migrate(kg, apply=args.apply)

    mode = "applied" if args.apply else "dry-run"
    print(f"[{mode}] Renamed {renamed} {OLD_REL!r} edge(s) to {NEW_REL!r}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
