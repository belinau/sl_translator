#!/usr/bin/env python3
"""Phase 4 migration — strip SL/EN-named fields from the KG.

One-off transform. Renames `sl_published_by` edges to
`translation_published_by`. Migrates `title_en`/`title_sl`/`slovenian_edition`
node attributes into the canonical neutral encoding when edge evidence
(`translated_by` to `agent:urban-belina`, or `written_by`) supplies
direction; otherwise routes the node to `data/migration_review.json` for
curator decision (no blind language assignment).

Idempotent: re-running on already-migrated nodes is a no-op.

Usage:
    python scripts/migrate_to_neutral_ontology.py            # dry-run
    python scripts/migrate_to_neutral_ontology.py --apply    # write
    python scripts/migrate_to_neutral_ontology.py --kg-path X.db --apply
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KG = ROOT / "data" / "knowledge.db"
DEFAULT_REVIEW = ROOT / "data" / "migration_review.json"
BELINA_AGENT = "agent:urban-belina"

LEGACY_NODE_FIELDS = ("title_en", "title_sl", "slovenian_edition")
NEUTRAL_TITLE_FIELDS = ("title_orig", "title_translation")


def migrate(kg_path: Path, apply: bool, review_path: Path) -> dict:
    raw = kg_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    # Edge index: per-source-node set of outgoing relations + target index
    out_rel = defaultdict(set)
    out_to = defaultdict(lambda: defaultdict(set))  # source -> relation -> {targets}
    for edge in edges:
        rel = edge.get("relation")
        src = edge.get("source")
        tgt = edge.get("target")
        if rel and src:
            out_rel[src].add(rel)
            out_to[src][rel].add(tgt)

    # PASS 1: edge rename
    edges_renamed = 0
    for edge in edges:
        if edge.get("relation") == "sl_published_by":
            if apply:
                edge["relation"] = "translation_published_by"
            edges_renamed += 1

    # PASS 2: node field migration
    counts = {
        "nodes_migrated_translator": 0,
        "nodes_migrated_author": 0,
        "nodes_already_neutral_legacy_stripped": 0,
        "nodes_skipped_no_legacy_fields": 0,
    }
    review_records: list[dict] = []

    for node in nodes:
        has_legacy = any(node.get(k) for k in LEGACY_NODE_FIELDS)
        has_neutral_title = any(node.get(k) for k in NEUTRAL_TITLE_FIELDS)

        if not has_legacy:
            counts["nodes_skipped_no_legacy_fields"] += 1
            continue

        nid = node.get("id")

        # Partial prior migration: neutral set + legacy lingering -> strip legacy
        if has_neutral_title:
            if apply:
                for k in LEGACY_NODE_FIELDS:
                    node.pop(k, None)
            counts["nodes_already_neutral_legacy_stripped"] += 1
            continue

        # Full migration: use edge evidence
        rels = out_rel.get(nid, set())
        title_en_val = node.get("title_en")
        title_sl_val = node.get("title_sl")
        sl_edition = node.get("slovenian_edition")

        translated_by_belina = (
            "translated_by" in rels
            and BELINA_AGENT in out_to.get(nid, {}).get("translated_by", set())
        )
        written_by_anyone = (
            "written_by" in rels and "translated_by" not in rels
        )

        if translated_by_belina:
            # Belina translates into SL: SL is the translation, EN is the original
            if title_sl_val:
                node["title_translation"] = title_sl_val
                node["translation_lang"] = "sl"
            if title_en_val:
                node["title_orig"] = title_en_val
                node["orig_lang"] = "en"
            if isinstance(sl_edition, dict):
                node["translation_edition"] = _build_translation_edition(sl_edition, "sl")
            counts["nodes_migrated_translator"] += 1
            if apply:
                _strip_legacy(node)

        elif written_by_anyone:
            # Author wrote it in their language; per corpus convention SL is the
            # original side and EN (if present) is the translation alias.
            if title_sl_val:
                node["title_orig"] = title_sl_val
                node["orig_lang"] = "sl"
                if title_en_val:
                    node["title_translation"] = title_en_val
                    node["translation_lang"] = "en"
            elif title_en_val:
                node["title_orig"] = title_en_val
                node["orig_lang"] = "en"
            if isinstance(sl_edition, dict):
                node["translation_edition"] = _build_translation_edition(sl_edition, "sl")
            counts["nodes_migrated_author"] += 1
            if apply:
                _strip_legacy(node)

        else:
            # No edge evidence (smol/doc_pair extractions, non-Belina translators,
            # or nodes whose direction cannot be inferred). NO blind direction
            # assignment. Route to curator review.
            review_records.append({
                "node_id": nid,
                "node_type": node.get("type"),
                "project_type": node.get("project_type"),
                "title": node.get("title"),
                "title_sl_value": title_sl_val,
                "title_en_value": title_en_val,
                "slovenian_edition": sl_edition,
                "outgoing_relations": sorted(rels),
                "reason": "direction_undetermined",
            })
            # Legacy fields are still stripped from the node so the KG is in
            # neutral shape after --apply. The curator will fill in
            # title_orig/title_translation/orig_lang/translation_lang per record
            # from the review queue, via the editor.
            if apply:
                _strip_legacy(node)

    print(f"edges_renamed={edges_renamed}")
    for k, v in counts.items():
        print(f"{k}={v}")
    print(f"nodes_routed_to_review={len(review_records)}")
    print(f"total_legacy_nodes_processed={sum(counts.values()) - counts['nodes_skipped_no_legacy_fields']}")

    if apply:
        kg_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        review_path.write_text(
            json.dumps(review_records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nWrote: {kg_path}")
        print(f"Wrote: {review_path}")
    else:
        print(f"\n--dry-run: nothing written.")
        print(f"           (run with --apply to commit changes)")

    return {
        "edges_renamed": edges_renamed,
        **counts,
        "nodes_routed_to_review": len(review_records),
    }


def _build_translation_edition(sl_edition: dict, language: str) -> dict:
    """Map slovenian_edition sub-dict -> translation_edition with language tag."""
    return {
        **{k: v for k, v in sl_edition.items()
           if k in ("publisher", "city", "year", "translator")},
        "language": language,
    }


def _strip_legacy(node: dict) -> None:
    for k in LEGACY_NODE_FIELDS:
        node.pop(k, None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kg-path", default=str(DEFAULT_KG),
                    help="KG JSON file path (default: data/knowledge.db)")
    ap.add_argument("--apply", action="store_true",
                    help="Write changes (default is dry-run)")
    ap.add_argument("--review-path", default=str(DEFAULT_REVIEW),
                    help="Output path for the curator review queue (default: data/migration_review.json)")
    args = ap.parse_args()

    kg_path = Path(args.kg_path)
    review_path = Path(args.review_path)

    if not kg_path.exists():
        print(f"ERROR: KG file not found: {kg_path}", flush=True)
        return 2

    migrate(kg_path=kg_path, apply=args.apply, review_path=review_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
