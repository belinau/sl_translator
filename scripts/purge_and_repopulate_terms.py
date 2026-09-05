#!/usr/bin/env python3
"""Surgically purge the *terms* part of the Knowledge Graph and rebuild it
cleanly from the glossary files.

What gets deleted (and ONLY this):
  * every ``term`` node          (~14.5k — noise from old NLP analysis)
  * every ``translation_mapping`` node  (the mapping layer coupled to terms)
  * every edge incident to either:
        translates_to        (term -> term)
        instantiates_concept (term -> concept)   <-- concept NODES stay
        has_mapping          (term -> translation_mapping)
        maps_to              (translation_mapping -> term)
        instantiated_in      (translation_mapping -> source_text)
        attributed_to        (translation_mapping -> agent)
  * ``term_keywords`` rows for the purged term nodes

What is NOT touched: agent, source_text, concept, institution nodes — and
every edge that does not touch a term or translation_mapping node
(cited_in, written_by, published_by, edited_by, translated_by, hosted_by,
performed_by, critiques, extends, related_to between non-term nodes, …).

Repopulation ingests **all** entries from every file under ``data/glossary/``
through the sanctioned ``KnowledgeGraph`` factory methods
(``add_term_node`` + ``link_translations_with_context``), so the resulting
term subgraph is sourced exclusively from glossary files.

Safety:
  * Dry-run by default. Re-run with ``--apply`` to persist.
  * On ``--apply`` the SQLite DB is backed up to
    ``data/knowledge.sqlite.terms-prepurge-<UTC>.bak`` first.
  * The verified translation_mappings that are NOT backed by any glossary
    file (the curator-blessed ones) are exported to
    ``data/term_purge_recovery_<UTC>.json`` so they are recoverable.

Usage:
    python scripts/purge_and_repopulate_terms.py            # dry-run
    python scripts/purge_and_repopulate_terms.py --apply     # persist
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translate_core.glossary import Glossary
from translate_core.knowledge_graph import KnowledgeGraph

GLOSSARY_DIR = ROOT / "data" / "glossary"
SQLITE_PATH = ROOT / "data" / "knowledge.sqlite"

# Relations carried by translation_mapping nodes (beyond the term<->tm links).
# Used only for the recovery export.
TM_PROVENANCE_RELATIONS = frozenset({"instantiated_in", "attributed_to"})


def _collect_purge_ids(kg: KnowledgeGraph) -> tuple[list[str], list[str]]:
    """Return (term_ids, mapping_ids) currently in the in-memory graph."""
    term_ids: list[str] = []
    mapping_ids: list[str] = []
    for node_id, data in kg.G.nodes(data=True):
        t = data.get("type")
        if t == "term":
            term_ids.append(node_id)
        elif t == "translation_mapping":
            mapping_ids.append(node_id)
    return term_ids, mapping_ids


def _export_recovery(
    kg: KnowledgeGraph, term_ids: list[str], mapping_ids: list[str], dest: Path
) -> int:
    """Write the verified, non-glossary-backed mappings to `dest`.

    Returns the number of mappings exported. These are the curator-blessed
    mappings that have no glossary-file counterpart and are therefore not
    recreated during repopulation.
    """
    term_lookup = {tid: dict(kg.G.nodes[tid]) for tid in term_ids}
    records: list[dict] = []
    for mid in mapping_ids:
        data = dict(kg.G.nodes[mid])
        lineage = str(data.get("lineage", ""))
        if not data.get("verified"):
            continue
        if lineage == "glossary" or lineage.startswith("glossary:"):
            continue  # backed by a glossary file — will be recreated
        endpoints: list[dict] = []
        provenance: list[dict] = []
        # outgoing: maps_to (-> term), instantiated_in (-> source_text),
        # attributed_to (-> agent)
        for _, tgt, edata in kg.G.out_edges(mid, data=True):
            rel = edata.get("relation")
            if rel == "maps_to":
                td = term_lookup.get(tgt)
                endpoints.append(
                    {"role": "target", "id": tgt,
                     "term": td.get("term") if td else None,
                     "lang": td.get("lang") if td else None}
                )
            elif rel in TM_PROVENANCE_RELATIONS:
                provenance.append({"relation": rel, "target": tgt})
        # incoming: has_mapping (term -> mapping)
        for src, _, edata in kg.G.in_edges(mid, data=True):
            if edata.get("relation") == "has_mapping":
                td = term_lookup.get(src)
                endpoints.append(
                    {"role": "source", "id": src,
                     "term": td.get("term") if td else None,
                     "lang": td.get("lang") if td else None}
                )
        records.append(
            {
                "mapping_id": mid,
                "attrs": {k: v for k, v in data.items() if k not in ("id", "type")},
                "endpoints": endpoints,
                "provenance": provenance,
            }
        )
    dest.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(records)


def _purge_bulk(kg: KnowledgeGraph, term_ids: list[str], mapping_ids: list[str]) -> None:
    """Delete all term + translation_mapping nodes and their incident edges in
    a single SQLite transaction, then refresh the in-memory graph.

    Uses ``KGStore.delete_node`` semantics (cascade edges + term_keywords) but
    batched for performance — calling ``KnowledgeGraph.remove_node`` per node
    would rebuild the flashtext indices ~19k times (O(N²)). The final
    ``_load_from_sqlite`` + ``rebuild_term_keywords_from_nodes`` restores
    index consistency exactly as ``remove_node`` would.
    """
    conn = kg._store._conn
    all_ids = term_ids + mapping_ids
    # Parameter-batch the IN-list to stay within SQLite's 999-host-var limit.
    BATCH = 900
    conn.execute("BEGIN")
    try:
        # 1. Edges whose source or target is being purged.
        for i in range(0, len(all_ids), BATCH):
            chunk = all_ids[i : i + BATCH]
            placeholders = ",".join("?" for _ in chunk)
            conn.execute(
                f"DELETE FROM edges WHERE source IN ({placeholders}) "
                f"OR target IN ({placeholders})",
                (*chunk, *chunk),
            )
        # 2. term_keywords rows for purged term nodes.
        for i in range(0, len(term_ids), BATCH):
            chunk = term_ids[i : i + BATCH]
            placeholders = ",".join("?" for _ in chunk)
            conn.execute(
                f"DELETE FROM term_keywords WHERE term_id IN ({placeholders})",
                (*chunk,),
            )
        # 3. The nodes themselves.
        for i in range(0, len(all_ids), BATCH):
            chunk = all_ids[i : i + BATCH]
            placeholders = ",".join("?" for _ in chunk)
            conn.execute(
                f"DELETE FROM nodes WHERE id IN ({placeholders})", (*chunk,)
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    # Refresh in-memory graph + indices from the now-clean SQLite store.
    kg.G.clear()
    kg._load_from_sqlite()
    kg._rebuild_indices()


def _repopulate(kg: KnowledgeGraph) -> tuple[int, int, int]:
    """Ingest every glossary entry through the factory methods.

    Returns (term_pairs, mappings, skipped).
    """
    glossary = Glossary(glossary_dir=GLOSSARY_DIR)
    entries = glossary.entries

    proj_slug = "glossary-import"
    proj_title = "glossary import"
    if not kg.G.has_node(f"source:{proj_slug}"):
        kg.add_source_text_node(proj_slug, title=proj_title,
                                project_type="glossary_import")

    mappings = 0
    skipped = 0
    for e in entries:
        src = e["source_term"]
        tgt = e["target_term"]
        src_lang = e.get("source_lang", "en")
        tgt_lang = e.get("target_lang", "sl")
        note = e.get("note", "") or ""
        lineage = f"glossary:{note}" if note else "glossary"

        src_id = kg.add_term_node(src, src_lang, is_phrase=(" " in src))
        tgt_id = kg.add_term_node(tgt, tgt_lang, is_phrase=(" " in tgt))

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
            mappings += 1
        else:
            skipped += 1

    # Safety net: term_keywords table consistent with the new term nodes.
    kg._store.rebuild_term_keywords_from_nodes()
    return len(entries), mappings, skipped


def _backup(dest: Path) -> None:
    """Checkpoint the WAL into the main DB, then copy it to `dest`."""
    import sqlite3
    conn = sqlite3.connect(str(SQLITE_PATH))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    shutil.copy2(SQLITE_PATH, dest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Purge all term + translation_mapping nodes and rebuild "
                    "the terms part of the KG from glossary files only."
    )
    parser.add_argument(
        "--apply", action="store_true", default=False,
        help="Persist changes (default: dry-run).",
    )
    args = parser.parse_args(argv)

    print(f"Loading KG from {SQLITE_PATH} …")
    kg = KnowledgeGraph()
    before_nodes = kg.G.number_of_nodes()
    before_edges = kg.G.number_of_edges()
    print(f"  nodes: {before_nodes}, edges: {before_edges}")

    term_ids, mapping_ids = _collect_purge_ids(kg)
    print(f"\nTerm nodes to purge:               {len(term_ids)}")
    print(f"Translation_mapping nodes to purge: {len(mapping_ids)}")

    # Count edges that will be removed (incident to a purge id).
    purge_set = set(term_ids) | set(mapping_ids)
    edges_removed = 0
    for u, v in kg.G.edges():
        if u in purge_set or v in purge_set:
            edges_removed += 1
    print(f"Edges incident to purge set:       {edges_removed}")

    # Recovery export of curator-blessed, non-glossary mappings.
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    recovery_path = ROOT / "data" / f"term_purge_recovery_{ts}.json"
    n_rec = _export_recovery(kg, term_ids, mapping_ids, recovery_path)
    print(f"Verified non-glossary mappings exported for recovery: {n_rec}")
    print(f"  -> {recovery_path}")

    # Preview of glossary repopulation source.
    glossary = Glossary(glossary_dir=GLOSSARY_DIR)
    from collections import Counter
    origins = Counter(e.get("origin") for e in glossary.entries)
    print(f"\nGlossary entries to ingest: {len(glossary.entries)}")
    for origin, count in origins.most_common():
        print(f"  {origin}: {count}")

    if not args.apply:
        print("\nDRY RUN — no changes written. Re-run with --apply to persist.")
        print("After apply: term nodes will be rebuilt purely from the glossary "
              "files above; all other node/edge types are untouched.")
        return 0

    # --- Backup -------------------------------------------------------------
    backup_path = SQLITE_PATH.with_name(f"knowledge.sqlite.terms-prepurge-{ts}.bak")
    print(f"\nBacking up SQLite DB -> {backup_path}")
    _backup(backup_path)

    # --- Purge --------------------------------------------------------------
    print("Purging term + translation_mapping nodes …")
    _purge_bulk(kg, term_ids, mapping_ids)
    mid_nodes = kg.G.number_of_nodes()
    mid_edges = kg.G.number_of_edges()
    print(f"  after purge: nodes {mid_nodes}, edges {mid_edges}")

    # Sanity: no term or translation_mapping nodes remain.
    leftover = sum(
        1 for _, d in kg.G.nodes(data=True)
        if d.get("type") in ("term", "translation_mapping")
    )
    if leftover:
        print(f"  WARNING: {leftover} term/mapping nodes remain after purge!",
              file=sys.stderr)

    # --- Repopulate ---------------------------------------------------------
    print("Repopulating terms from glossary files …")
    pairs, mappings, skipped = _repopulate(kg)
    print(f"  processed {pairs} term pairs")
    print(f"  new/updated mappings: {mappings}")
    print(f"  skipped: {skipped}")

    after_nodes = kg.G.number_of_nodes()
    after_edges = kg.G.number_of_edges()
    print("Done.")
    print(f"  nodes : {before_nodes} -> {after_nodes}")
    print(f"  edges : {before_edges} -> {after_edges}")
    # Type breakdown of the new term subgraph.
    from collections import Counter as _C
    types = _C(d.get("type") for _, d in kg.G.nodes(data=True))
    print("  node types:", dict(types))
    print(f"  backup: {backup_path}")
    print(f"  recovery export: {recovery_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())