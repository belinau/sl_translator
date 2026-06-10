#!/usr/bin/env python3
"""Push glossary entries from custom.tsv into the Knowledge Graph.

Creates term nodes and verified translation_mapping edges for each
EN↔SL term pair, mirroring exactly what the UI "Add term" button does
(but for the entire file at once).

Idempotent: existing term nodes are updated (frequency incremented),
existing translation mappings have their confidence bumped — no
duplicates are created.

Usage:
    # Dry run (default) — shows what would be added
    python scripts/ingest_glossary_to_kg.py

    # Apply — writes to data/knowledge.db
    python scripts/ingest_glossary_to_kg.py --apply

    # Limit to a specific file (default: custom.tsv)
    python scripts/ingest_glossary_to_kg.py --apply --file glossary-EN-SL.txt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translate_core.glossary import Glossary
from translate_core.knowledge_graph import KnowledgeGraph

GLOSSARY_DIR = ROOT / "data" / "glossary"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Push glossary entries from a TSV/CSV/TBX file into the KG"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write changes to data/knowledge.db (default: dry-run)",
    )
    parser.add_argument(
        "--file", default="custom.tsv",
        help="Glossary file name under data/glossary/ (default: custom.tsv)",
    )
    parser.add_argument(
        "--origin", default=None,
        help="Only import entries whose 'origin' field matches this value "
             "(default: match the --file name)",
    )
    parser.add_argument(
        "--project", default="glossary-import",
        help="Project slug for the source_text provenance node (default: glossary-import)",
    )
    args = parser.parse_args(argv)

    # --- Load glossary -------------------------------------------------------
    print("Loading glossary...")
    glossary = Glossary(glossary_dir=GLOSSARY_DIR)

    origin_filter = args.origin or args.file
    entries = [
        e for e in glossary.entries
        if e.get("origin", "") == origin_filter
    ]
    print(f"  Total glossary entries: {len(glossary.entries)}")
    print(f"  Entries from '{origin_filter}': {len(entries)}")

    if not entries:
        print("No matching entries found. Nothing to do.")
        return 0

    # --- Load KG -------------------------------------------------------------
    print("Loading KG...")
    kg = KnowledgeGraph()
    print(f"  nodes: {kg.G.number_of_nodes()}, edges: {kg.G.number_of_edges()}")

    # Ensure source text node for provenance
    proj_slug = args.project
    proj_title = args.project.replace("-", " ").replace("_", " ")
    if not kg.G.has_node(f"source:{proj_slug}"):
        kg.add_source_text_node(
            proj_slug, title=proj_title, project_type="glossary_import",
        )

    # --- Ingest -------------------------------------------------------------
    added_terms = 0
    added_mappings = 0
    skipped = 0

    for e in entries:
        src = e["source_term"]
        tgt = e["target_term"]
        src_lang = e.get("source_lang", "en")
        tgt_lang = e.get("target_lang", "sl")
        note = e.get("note", "")
        lineage = "glossary"

        # Derive lineage / context from note field
        # Notes like "McRuer" or "Haraway" are author attributions → use as lineage
        if note:
            lineage = f"glossary:{note}"

        # Add term nodes (idempotent — increments frequency if exists)
        src_id = kg.add_term_node(
            src, src_lang,
            is_phrase=(" " in src),
        )
        tgt_id = kg.add_term_node(
            tgt, tgt_lang,
            is_phrase=(" " in tgt),
        )

        # Link with verified translation mapping
        map_id = kg.link_translations_with_context(
            src_term_id=src_id,
            tgt_term_id=tgt_id,
            confidence=1.0,
            lineage=lineage,
            gloss=note or None,
            verified=True,
            source_text_id=proj_slug,
        )

        if map_id:
            added_mappings += 1
        else:
            skipped += 1

        added_terms += 1

    print(f"\n  Processed: {added_terms} term pairs")
    print(f"  New/updated mappings: {added_mappings}")
    print(f"  Skipped (missing nodes): {skipped}")

    # --- Save or dry-run -----------------------------------------------------
    if args.apply:
        kg.save()
        print(f"\n✓ Saved KG to {kg.db_path} "
              f"({kg.G.number_of_nodes()} nodes, {kg.G.number_of_edges()} edges)")
    else:
        print("\n  (dry-run — no changes written. Re-run with --apply to persist.)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())