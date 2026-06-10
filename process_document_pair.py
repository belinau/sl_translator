#!/usr/bin/env python
# process_document_pair.py
#
# CLI entry point for the document-pair bilingual pipeline (Phase 4).
#
# Usage:
#   python process_document_pair.py \
#       --en  data/books/Kafer-EN.pdf \
#       --sl  data/books/Kafer-SL.docx \
#       --container source:feminist-queer-crip-kafer-2013 \
#       [--dry-run]
#
# For pre-converted markdown inputs:
#   python process_document_pair.py \
#       --en  data/books/Okri-EN.docx.md \
#       --sl  data/books/Okri-SL.doc.md \
#       --container source:cesta-sestradanih-okri-2010 \
#       [--dry-run]

import logging
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from translate_core.knowledge_graph import KnowledgeGraph
from translate_core.document_pair_pipeline import (
    parse_side,
    process_pair,
    PairResult,
)


log = logging.getLogger(__name__)

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Process a bilingual EN/SL document pair into KG entities and a TMX.",
    )
    ap.add_argument("--en", required=True, metavar="PATH",
                    help="Path to the EN document or markdown (.pdf, .docx, .doc, .md)")
    ap.add_argument("--sl", required=True, metavar="PATH",
                    help="Path to the SL document or markdown (.pdf, .docx, .doc, .md)")
    ap.add_argument("--container", required=True, metavar="ID",
                    help="KG source_text node ID of the container work "
                         "(e.g. source:cesta-sestradanih-okri-2010)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Parse and match, but do not write to KG or create TMX")
    ap.add_argument("--report", metavar="PATH",
                    help="Write JSON result report to this path")
    args = ap.parse_args()

    en_path = Path(args.en)
    sl_path = Path(args.sl)
    container_id = args.container

    if not en_path.exists():
        log.error(f"ERROR: EN file not found: {en_path}")
        sys.exit(1)
    if not sl_path.exists():
        log.error(f"ERROR: SL file not found: {sl_path}")
        sys.exit(1)

    log.info(f"EN: {en_path}")
    log.info(f"SL: {sl_path}")
    log.info(f"Container: {container_id}")
    log.info(f"Dry-run: {args.dry_run}")

    # Load KG
    kg = KnowledgeGraph(db_path=config.KG_DB_PATH)

    # Verify container exists
    container_node = f"source:{container_id.removeprefix('source:').lower()}"
    if not kg.G.has_node(container_node):
        log.warning(f"container node {container_node!r} not found in KG. It will be referenced but not verified.")

    # Run the pipeline
    result: PairResult = process_pair(
        en_doc_path=en_path,
        sl_doc_path=sl_path,
        container_work_id=container_id,
        kg=kg,
        dry_run=args.dry_run,
    )

    # Print summary
    log.info("Document-Pair Pipeline Result ===")
    log.info("=== Document-Pair Pipeline Result ===")
    log.info(f"  Bilingual citations (title_en + title_sl):  {result.n_bilingual}")
    log.info(f"  EN-only citations (no SL match):            {result.n_en_only}")
    log.info(f"  SL-only citations (no EN match):            {result.n_sl_only}")
    log.info(f"  Unmatched EN:  {len(result.unmatched_en)}")
    log.info(f"  Unmatched SL:  {len(result.unmatched_sl)}")
    if result.tmx_path:
        log.info(f"  Generated TMX: {result.tmx_path}")

    if result.unmatched_en:
        log.info("\nUnmatched EN citations (no SL equivalent found):")
        for rec in result.unmatched_en[:5]:
            p = rec.get("payload", {})
            log.info(f"  - {p.get('title_en', '(no title)')[:80]}")
        if len(result.unmatched_en) > 5:
            log.info(f"  ... and {len(result.unmatched_en) - 5} more")

    # Write report
    if args.report:
        report = {
            "container_work_id": result.container_work_id,
            "n_bilingual": result.n_bilingual,
            "n_en_only": result.n_en_only,
            "n_sl_only": result.n_sl_only,
            "tmx_path": str(result.tmx_path) if result.tmx_path else None,
            "unmatched_en_titles": [
                (rec.get("payload") or {}).get("title_en", "")
                for rec in result.unmatched_en
            ],
            "unmatched_sl_titles": [
                (rec.get("payload") or {}).get("title_sl", "")
                or (rec.get("payload") or {}).get("title_en", "")
                for rec in result.unmatched_sl
            ],
        }
        Path(args.report).write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info(f"\nReport written to {args.report}")

    if not args.dry_run:
        log.info('KG updated successfully.' if result.n_bilingual > 0 else 'No bilingual citations found to write.')


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    main()
