#!/usr/bin/env python3
"""Ingest Kafer "Feminist, Queer, Crip" curated concepts into the KG.

Reads the curator-authored concept extraction records from
``data/kafer_concept_extractions.json`` (same shape as smol extraction
entries, but kept separate — never merged into smol_extractions.json)
and feeds them through the exact entity-extraction pipeline chokepoint:

  build_record → aggregate_agent_signals → aggregate_institution_signals
  → score_all → dedup_records → score_all → write_to_kg

Also supports a ``--lineages`` pass that wires curated concept→concept
edges from ``data/kafer_concept_lineages.json`` via
``kg.link_concepts_rhizomatic``.

Usage:
    .venv/bin/python scripts/ingest_kafer_concepts.py --dry-run
    .venv/bin/python scripts/ingest_kafer_concepts.py --apply
    .venv/bin/python scripts/ingest_kafer_concepts.py --lineages --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translate_core.entity_extraction.smol_extractor import build_record
from translate_core.entity_extraction._slug import _slugify
from translate_core.kg_ingest_entities import (
    aggregate_agent_signals,
    aggregate_institution_signals,
    score_all,
    dedup_records,
    write_to_kg,
)
from translate_core.knowledge_graph import KnowledgeGraph

EXTRACTIONS_PATH = ROOT / "data" / "kafer_concept_extractions.json"
LINEAGES_PATH = ROOT / "data" / "kafer_concept_lineages.json"
REVIEW_PATH = ROOT / "data" / "extraction_review.json"
DROPPED_PATH = ROOT / "data" / "extraction_dropped.jsonl"


def _load_extractions() -> list[dict]:
    with open(EXTRACTIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _build_records(entries: list[dict]) -> list[dict]:
    """Convert extraction entries to pipeline records via build_record."""
    records = []
    for entry in entries:
        origin = entry["origin"]
        seg_idx = entry["seg_idx"]
        container_work_id = entry.get("container_work_id", "")
        for ent in entry.get("entities", []):
            rec = build_record(
                ent,
                origin,
                seg_idx,
                container_work_id,
                src_lang="en",
                tgt_lang="sl",
            )
            if rec is not None:
                # Use curator_extra provenance so _route_record routes to direct (not review)
                rec.setdefault("source", {})["provenance"] = "curator_extra"
                # Curator endorsement: set curator_endorsed so these
                # high-trust curated records direct-write rather than
                # routing to the review queue.
                rec.setdefault("signals", {})["curator_endorsed"] = True
                records.append(rec)
    return records


def _ingest_concepts(dry_run: bool) -> None:
    """Main concept ingest pass."""
    entries = _load_extractions()
    print(f"Loaded {len(entries)} extraction entries from {EXTRACTIONS_PATH.name}")

    records = _build_records(entries)
    print(f"Built {len(records)} pipeline records")

    # Exact pipeline: aggregate → score → dedup → re-score
    aggregate_agent_signals(records)
    aggregate_institution_signals(records)
    scored = score_all(records)
    deduped = dedup_records(scored)
    re_scored = score_all(deduped)

    # Tier counts
    tiers = {"direct_write": 0, "review": 0, "drop": 0}
    unlocated = []
    for r in re_scored:
        tiers[r["tier"]] = tiers.get(r["tier"], 0) + 1
        if r.get("source", {}).get("segment_idx") == -1:
            label = r.get("payload", {}).get("label", "?")
            if label not in unlocated:
                unlocated.append(label)

    print(f"Tier counts: {tiers}")
    if unlocated:
        print(f"Unlocated concepts ({len(unlocated)}):")
        for u in unlocated:
            print(f"  - {u}")

    if dry_run:
        print("\n[dry-run] No changes written.")
        return

    kg = KnowledgeGraph()
    stats = write_to_kg(
        kg,
        re_scored,
        review_path=REVIEW_PATH,
        dropped_path=DROPPED_PATH,
        dry_run=False,
    )
    kg.save()
    print(f"\nIngest complete: {stats}")
    print(f"  direct_write={stats.direct_write}  review_queued={stats.review_queued}  dropped={stats.dropped}")


def _ingest_lineages(dry_run: bool) -> None:
    """Wire curated concept→concept lineage edges."""
    if not LINEAGES_PATH.exists():
        print(f"No lineages file at {LINEAGES_PATH}")
        return

    with open(LINEAGES_PATH, encoding="utf-8") as f:
        lineages = json.load(f)
    print(f"Loaded {len(lineages)} lineage pairs from {LINEAGES_PATH.name}")

    if dry_run:
        for pair in lineages:
            a_id = f"concept:{_slugify(pair['a'])}"
            b_id = f"concept:{_slugify(pair['b'])}"
            rel = pair.get("relation", "related_to")
            print(f"  {a_id} -[{rel}]-> {b_id}")
        print("\n[dry-run] No edges written.")
        return

    kg = KnowledgeGraph()
    created = 0
    skipped = 0
    for pair in lineages:
        a_id = f"concept:{_slugify(pair['a'])}"
        b_id = f"concept:{_slugify(pair['b'])}"
        rel = pair.get("relation", "related_to")
        if kg.G.has_node(a_id) and kg.G.has_node(b_id):
            kg.link_concepts_rhizomatic(a_id, b_id, rel)
            created += 1
        else:
            skipped += 1
            missing = []
            if not kg.G.has_node(a_id):
                missing.append(a_id)
            if not kg.G.has_node(b_id):
                missing.append(b_id)
            print(f"  Skipped {a_id} -[{rel}]-> {b_id} (missing: {', '.join(missing)})")

    kg.save()
    print(f"\nLineages complete: {created} edges created, {skipped} skipped")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Kafer curated concepts into the KG"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is dry-run)",
    )
    parser.add_argument(
        "--lineages",
        action="store_true",
        help="Ingest curated concept→concept lineage edges",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    if args.lineages:
        _ingest_lineages(dry_run)
    else:
        _ingest_concepts(dry_run)


if __name__ == "__main__":
    main()