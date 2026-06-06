#!/usr/bin/env python3
# scripts/enrich_bilingual.py
#
# Phase 5: Batch bilingual enrichment for existing KG source_text nodes.
#
# Usage:
#   python scripts/enrich_bilingual.py [--dry-run] [--report REPORT_PATH]
#
# --dry-run: compute enrichment matches but don't write to KG
# --report:  write a JSON report of what would be / was enriched

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.knowledge_graph import KnowledgeGraph
from translate_core.tm import TranslationMemory
from translate_core.entity_extraction.bilingual_enrichment_batch import (
    run_bilingual_enrichment,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("enrich_bilingual")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 5: Batch bilingual enrichment for KG source_text nodes"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute matches but don't write to KG",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        help="Path to write JSON report of enrichment results",
    )
    parser.add_argument(
        "--kg-path",
        type=str,
        default="data/knowledge.db",
        help="Path to knowledge.db (default: data/knowledge.db)",
    )
    parser.add_argument(
        "--tm-dir",
        type=str,
        default="data/tm",
        help="Path to TM directory (default: data/tm)",
    )
    args = parser.parse_args()

    kg_path = Path(args.kg_path)
    tm_dir = Path(args.tm_dir)

    log.info("Loading KG from %s", kg_path)
    kg = KnowledgeGraph(db_path=kg_path)

    log.info("Loading TM from %s", tm_dir)
    tm = TranslationMemory(tm_dir=tm_dir)
    tm_entries = tm.entries

    log.info("Running bilingual enrichment (dry_run=%s)", args.dry_run)
    results = run_bilingual_enrichment(kg, tm_entries, dry_run=args.dry_run)

    # Print summary
    print("\n=== Bilingual Enrichment Report ===\n")
    total_enriched = 0
    for phase, counter in results.items():
        print(f"  {phase}:")
        for key, value in sorted(counter.items()):
            print(f"    {key}: {value}")
            if "enriched" in key or "matched" in key:
                total_enriched += value
    print(f"\n  Total enriched/matched: {total_enriched}")

    if args.report:
        report_path = Path(args.report)
        report_data = {
            phase: dict(counter) for phase, counter in results.items()
        }
        report_path.write_text(
            json.dumps(report_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info("Report written to %s", report_path)

    # Post-enrichment verification
    if not args.dry_run:
        log.info("Running post-enrichment verification...")
        nodes = [dict(n) for n in kg.G.nodes.values() if n.get("type") == "source_text"]
        total = len(nodes)
        has_both = sum(1 for n in nodes if n.get("title_en") and n.get("title_sl"))
        has_en_only = sum(1 for n in nodes if n.get("title_en") and not n.get("title_sl"))
        has_sl_only = sum(1 for n in nodes if n.get("title_sl") and not n.get("title_en"))
        has_neither = sum(1 for n in nodes if not n.get("title_en") and not n.get("title_sl"))

        containers_with_orig = sum(
            1 for n in nodes
            if n.get("project_type") in ("book_translation", "article_translation", "festival_programme", "exhibition_catalogue")
            and n.get("title_orig") and n.get("title_translation")
        )
        containers_total = sum(
            1 for n in nodes
            if n.get("project_type") in ("book_translation", "article_translation", "festival_programme", "exhibition_catalogue")
        )

        print("\n=== Post-enrichment KG State ===")
        print(f"  Total source_text nodes: {total}")
        print(f"  Has both title_en + title_sl: {has_both}")
        print(f"  EN only (missing SL): {has_en_only}")
        print(f"  SL only (missing EN): {has_sl_only}")
        print(f"  Neither: {has_neither}")
        print(f"  Containers with title_orig + title_translation: {containers_with_orig}/{containers_total}")


if __name__ == "__main__":
    main()