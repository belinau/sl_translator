#!/usr/bin/env python3
"""Orchestrator for the project-type-aware TM entity extractor.

Two-phase pipeline:
  Phase A (export):  --export-segments → writes segments to JSON for OMP agent dispatch
  Phase B (ingest):  --ingest-extractions → reads smol agent results, scores, writes to KG

The smol model (GLM-5.1) is NEVER called from this codebase. It is dispatched
as an agent via the OMP harness, which processes the exported segments and writes
results to data/smol_extractions.json.

No VL calls. No regex heuristics for entity extraction. The book_extractor regex
is kept ONLY as a fallback for citation-style segments where no smol extraction
is available.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent))

from translate_core.entity_extraction.smol_extractor import (
    format_extract_prompt,
    build_record,
)
from translate_core.entity_extraction.book_extractor import (
    extract_from_book_origin,
    _agent_person_from_name as _book_agent_person,
    _institution_from_publisher as _book_institution,
)
from translate_core.tm import TranslationMemory
from translate_core.entity_extraction.origin_walker import (
    build_origin_contexts,
    OriginContext,
)
from translate_core.entity_extraction.seeded_book_finder import (
    SeedManifest,
    find_book_anchors,
    book_claims_to_records,
    claim_for_segment,
)
from translate_core.entity_extraction.bilingual_enrichment import enrich_titles_sl
from translate_core.kg_ingest_entities import (
    aggregate_agent_signals,
    aggregate_institution_signals,
    score_all,
    dedup_records,
    write_to_kg,
)

# ── Paths ─────────────────────────────────────────────────────────────────────

SEGMENTS_EXPORT_PATH = Path("data/smold_segments_for_extraction.json")
SMOL_EXTRACTIONS_PATH = Path("data/smol_extractions.json")


# ── Phase A: Export segments for OMP agent dispatch ───────────────────────────

def export_segments(
    contexts: List[OriginContext],
    attribution_by_origin: dict[str, dict[int, str]],
    book_claims: list,
    origin_offsets: dict[str, int],
) -> None:
    """Write non-noise segments to JSON for OMP smol agent extraction.

    Each segment gets a prompt pre-formatted for the smol model, plus
    metadata (origin, seg_idx, container_work_id from attribution/seed).
    """
    export: list[dict] = []

    for ctx in contexts:
        origin_start = origin_offsets.get(ctx.origin, 0)
        attr_map = attribution_by_origin.get(ctx.origin, {})

        for lbl in ctx.labels:
            if lbl.klass.value == "noise":
                continue

            seg_idx = lbl.idx
            global_idx = origin_start + seg_idx
            container_work_id = ""

            # Try attribution first, then seed manifest
            attr_cid = attr_map.get(int(seg_idx))
            if attr_cid:
                container_work_id = attr_cid.removeprefix("source:")
            else:
                claim = claim_for_segment(book_claims, global_idx, ctx.origin)
                if claim:
                    container_work_id = claim.work_id

            prompt = format_extract_prompt(
                src=lbl.src,
                tgt=lbl.tgt,
                origin=ctx.origin,
            )

            export.append({
                "origin": ctx.origin,
                "seg_idx": int(seg_idx),
                "global_idx": global_idx,
                "container_work_id": container_work_id,
                "klass": lbl.klass.value,
                "src": lbl.src[:800],
                "tgt": lbl.tgt[:800],
                "prompt": prompt,
            })

    SEGMENTS_EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEGMENTS_EXPORT_PATH.write_text(
        json.dumps(export, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[export] Wrote {len(export)} segments to {SEGMENTS_EXPORT_PATH}")
    print(f"         Now dispatch OMP smol agents to extract entities.")
    print(f"         Write results to {SMOL_EXTRACTIONS_PATH}")


# ── Phase B: Ingest smol agent extraction results ──────────────────────────────

def ingest_extractions(
    args,
    contexts: List[OriginContext],
    attribution_by_origin: dict[str, dict[int, str]],
    book_claims: list,
    origin_offsets: dict[str, int],
    entries_by_origin: dict[str, list[dict]],
) -> None:
    """Read smol agent results, merge with book_extractor fallback, score, ingest."""
    if not SMOL_EXTRACTIONS_PATH.exists():
        print(f"[ingest] ERROR: No smol extraction results at {SMOL_EXTRACTIONS_PATH}")
        print(f"         Run --export-segments first, then dispatch OMP agents.")
        sys.exit(1)

    raw_extractions = json.loads(SMOL_EXTRACTIONS_PATH.read_text(encoding="utf-8"))
    print(f"[ingest] Loaded {len(raw_extractions)} extraction results from {SMOL_EXTRACTIONS_PATH}")

    all_records: List[dict] = []

    # Seeded book records — authoritative translated_works
    seeded_records = book_claims_to_records(book_claims)
    all_records.extend(seeded_records)

    # Author/translator/publisher per seeded book are created automatically by
    # write_to_kg pass 2 (translated_work ingest) using pending_* fields
    # populated by book_claims_to_records. No need to inject standalone
    # agent_person/institution records here — they were duplicating into the
    # review queue (book_extractor signals don't carry smol_verified_classification
    # so they scored 0.65 → REVIEW). Cleaner to let pass 2 do it.

    # Build index: (origin, seg_idx) → extraction result
    smol_by_key: dict[tuple[str, int], dict] = {}
    for item in raw_extractions:
        key = (item.get("origin", ""), item.get("seg_idx", -1))
        smol_by_key[key] = item

    # Process each origin context
    for ctx in contexts:
        origin_start = origin_offsets.get(ctx.origin, 0)
        attr_map = attribution_by_origin.get(ctx.origin, {})

        # Try smol extractions first, fall back to book_extractor per segment
        ctx_records: List[dict] = []
        smol_hit_count = 0
        fallback_count = 0

        for lbl in ctx.labels:
            if lbl.klass.value == "noise":
                continue

            key = (ctx.origin, int(lbl.idx))
            smol_result = smol_by_key.get(key)

            if smol_result and smol_result.get("entities"):
                # Use smol agent extraction
                entities = smol_result["entities"]
                # Determine container_work_id
                container = smol_result.get("container_work_id", "")
                if not container:
                    local_idx = int(lbl.idx)
                    attr_cid = attr_map.get(local_idx)
                    if attr_cid:
                        container = attr_cid.removeprefix("source:")
                    else:
                        claim = claim_for_segment(
                            book_claims, origin_start + local_idx, ctx.origin,
                        )
                        if claim:
                            container = claim.work_id

                for ent in entities:
                    rec = build_record(ent, ctx.origin, int(lbl.idx), container)
                    if rec:
                        ctx_records.append(rec)
                smol_hit_count += 1
            else:
                fallback_count += 1
        # If smol returned nothing for this entire context, fall back to
        # book_extractor (regex) ONLY when the user did not pass --no-fallback.
        # The user has explicitly stated they want NO regex extraction when
        # smol coverage is complete.
        if not ctx_records:
            if args.no_fallback:
                print(f"      {ctx.origin}: smol returned 0, "
                      f"book_extractor fallback DISABLED (--no-fallback)")
            else:
                ctx_records = extract_from_book_origin(ctx.labels)
                print(f"      {ctx.origin}: smol returned 0, using book_extractor fallback "
                      f"({len(ctx_records)} records)")
        else:
            print(f"      {ctx.origin}: {smol_hit_count} smol hits, "
                  f"{fallback_count} segments without smol "
                  f"({'no fallback' if args.no_fallback else 'book_extractor fallback'})")

        # Per-segment supplement: only when fallback is enabled
        if ctx_records and fallback_count > 0 and not args.no_fallback:
            book_recs = extract_from_book_origin(ctx.labels)
            existing_keys = {
                (r["kind"], r["payload"].get("name") or r["payload"].get("title_en") or r["payload"].get("title_orig"))
                for r in ctx_records
                if isinstance(r.get("payload"), dict)
            }
            added = 0
            for br in book_recs:
                key = (
                    br["kind"],
                    br["payload"].get("name") or br["payload"].get("title_en") or br["payload"].get("title_orig"),
                )
                if key not in existing_keys:
                    ctx_records.append(br)
                    added += 1
            if added:
                print(f"      {ctx.origin}: supplemented with {added} book_extractor records")
        # Retarget cited_in. Three valid sources of a container_work_id, in
        # priority order:
        #   (1) attribution map  — curator-verified segment→container map
        #   (2) seed_manifest    — anchor patterns matched in the TM
        #   (3) smol payload     — the smol model itself surfaced a container
        # Each yields a `retargeted_via` provenance tag; (3) covers the
        # case where the per-segment cascade at line ~195 already pointed at
        # a valid container before build_record ran.
        retargeted_attr = 0
        retargeted_claim = 0
        retargeted_smol = 0
        # Retarget container_work_id for ALL types that carry one:
        # cited_work, artwork, performance, concept. Each kind uses
        # `cited_in → container` per ontology §3.2 when ingested.
        for r in ctx_records:
            if r["kind"] not in ("cited_work", "artwork", "performance", "concept"):
                continue
            local_idx = r["source"].get("segment_idx", -1)
            if local_idx < 0:
                continue
            origin = r["source"].get("origin", "")
            attr_cid = attr_map.get(int(local_idx))
            if attr_cid:
                r["payload"]["container_work_id"] = attr_cid.removeprefix("source:")
                r["source"]["retargeted_to"] = attr_cid
                r["source"]["retargeted_via"] = "attribution"
                retargeted_attr += 1
                continue
            global_idx = origin_start + local_idx
            claim = claim_for_segment(book_claims, global_idx, origin)
            if claim:
                r["payload"]["container_work_id"] = claim.work_id
                r["source"]["global_idx"] = global_idx
                r["source"]["retargeted_to"] = claim.work_id
                r["source"]["retargeted_via"] = "seed_manifest"
                retargeted_claim += 1
                continue
            # The smol payload already carried a container_work_id from the
            # per-segment cascade. Acknowledge it as retargeted so the orphan
            # gate below doesn't drop it.
            smol_cwid = r["payload"].get("container_work_id")
            if smol_cwid:
                r["source"]["retargeted_to"] = (
                    smol_cwid if smol_cwid.startswith("source:")
                    else f"source:{smol_cwid}"
                )
                r["source"]["retargeted_via"] = "smol_payload"
                retargeted_smol += 1

        # cited_works without ANY container reference stay as floating
        # bibliography nodes — ontology §2.4 does not require cited_in.
        # We only drop records that lack both container_work_id and any
        # identifying title (i.e. shapeless records).
        kept_recs: List[dict] = []
        dropped_shapeless = 0
        for r in ctx_records:
            if r["kind"] == "cited_work":
                p = r.get("payload", {})
                has_title = bool(
                    p.get("title_orig") or p.get("title_translation")
                    or p.get("title_en") or p.get("title_sl")
                )
                if not has_title:
                    dropped_shapeless += 1
                    continue
            kept_recs.append(r)

        print(f"      {ctx.origin}: {len(ctx_records)} raw records "
              f"(retargeted: {retargeted_attr} attribution, "
              f"{retargeted_claim} seeds, {retargeted_smol} smol_payload; "
              f"dropped {dropped_shapeless} shapeless cited_work; "
              f"dominant={ctx.dominant})")
        all_records.extend(kept_recs)

    print(f"      {len(all_records)} records after orphan-citation drop")

    # Bilingual enrichment (TM matching only, no LLM)
    if not args.no_bilingual:
        print(f"[3c/5] Bilingual second pass — filling title_sl on work records …")
        enrich_titles_sl(all_records, entries_by_origin, extractor=None)

    # Score and deduplicate
    print(f"[4/5] Aggregating signals, scoring, deduplicating …")
    aggregate_agent_signals(all_records)
    aggregate_institution_signals(all_records)
    scored = score_all(all_records)
    deduped = dedup_records(scored)
    re_scored = score_all(deduped)
    print(f"      after dedup: {len(re_scored)} records")

    tier_counts = Counter(r.get("tier") for r in re_scored)
    print(f"      tiers: direct_write={tier_counts.get('direct_write', 0)} "
          f"review={tier_counts.get('review', 0)} drop={tier_counts.get('drop', 0)}")

    # Preview
    if args.preview_patterns:
        preview_path = args.output_dir / "extraction_pattern_preview.md"
        print(f"[5/5] Writing pattern preview to {preview_path} …")
        preview_path.write_text(_build_preview(contexts, re_scored), encoding="utf-8")

    # Write review + dropped files
    review_path = args.output_dir / "extraction_review.json"
    dropped_path = args.output_dir / "extraction_dropped.jsonl"

    if args.dry_run:
        review = [r for r in re_scored if r.get("tier") == "review"]
        dropped = [r for r in re_scored if r.get("tier") == "drop"]
        if review:
            review_path.write_text(
                json.dumps(review, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        if dropped:
            with open(dropped_path, "w", encoding="utf-8") as f:
                for r in dropped:
                    f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        print(f"\n[dry run] Wrote:")
        if args.preview_patterns:
            print(f"  {preview_path}")
        if review:
            print(f"  {review_path} ({len(review)} records)")
        if dropped:
            print(f"  {dropped_path} ({len(dropped)} records)")
        print(f"\nNo KG writes performed (dry run).")
        return

    # Real write mode
    print(f"[5/5] Writing direct-tier records to KG …")
    from translate_core.knowledge_graph import KnowledgeGraph
    if args.kg_path:
        # Ensure parent dir exists; KnowledgeGraph reads from this path on
        # construction (or starts empty if the file is absent).
        args.kg_path.parent.mkdir(parents=True, exist_ok=True)
        kg = KnowledgeGraph(db_path=args.kg_path)
        print(f"      writing to KG at: {args.kg_path}")
    else:
        kg = KnowledgeGraph()
        print(f"      writing to default KG (data/knowledge.db)")
    stats = write_to_kg(
        kg, re_scored,
        review_path=review_path,
        dropped_path=dropped_path,
        dry_run=False,
    )
    print(f"      direct-write: {stats.direct_write}")
    print(f"      review queue: {stats.review_queued}")
    print(f"      dropped:      {stats.dropped}")
    print(f"\nBy kind:")
    for kind, counts in stats.by_kind.items():
        print(f"  {kind:18s} direct={counts['direct_write']:5d}  review={counts['review']:5d}  drop={counts['dropped']:5d}")


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _summarise_contexts(contexts: List[OriginContext]) -> str:
    out = []
    for c in contexts:
        out.append(f"### {c.origin}")
        out.append(f"  segments: {len(c.labels)}")
        out.append(f"  dominant: {c.dominant}")
        out.append(f"  profile:  {c.profile}")
    return "\n".join(out) + "\n"


def _format_example_record(r: dict, idx: int) -> str:
    lines = [f"### Example {idx}: `{r['kind']}` (confidence {r.get('confidence', 0):.2f}, tier {r.get('tier', '?')})"]
    p = r.get("payload", {})
    if r["kind"] == "agent_person":
        lines.append(f"  name:         {p.get('name')}")
        lines.append(f"  role:         {p.get('role')}")
        lines.append(f"  dedup_group:  {p.get('dedup_group')}")
        lines.append(f"  alt_spellings:{p.get('alt_spellings')}")
    elif r["kind"] == "institution":
        lines.append(f"  name:         {p.get('name')}")
        lines.append(f"  kind:         {p.get('kind')}")
        lines.append(f"  city:         {p.get('city')}")
    elif r["kind"] == "cited_work":
        lines.append(f"  author:       {p.get('author')}")
        lines.append(f"  title_en:     {p.get('title_en')}")
        lines.append(f"  title_sl:     {p.get('title_sl')}")
        lines.append(f"  year:         {p.get('year')}")
        lines.append(f"  project_type: {p.get('project_type')}")
        lines.append(f"  container:    {p.get('container_work_id')}")
    src = r.get("source", {})
    lines.append(f"  origin:       {src.get('origin')}")
    lines.append(f"  seg_idx:      {src.get('segment_idx')}")
    lines.append(f"  retargeted:   {src.get('retargeted_to', '(none)')}")
    return "\n".join(lines)


def _build_preview(
    contexts: List[OriginContext],
    records: List[dict],
) -> str:
    out = ["# Extraction Pattern Preview\n"]
    out.append("## Context Summary\n")
    out.append(_summarise_contexts(contexts))
    out.append("\n## Extracted Records\n")
    for kind in ("agent_person", "institution", "cited_work"):
        kind_records = [r for r in records if r.get("kind") == kind]
        out.append(f"\n### {kind} ({len(kind_records)})\n")
        for i, r in enumerate(kind_records[:20]):
            out.append(_format_example_record(r, i + 1))
            out.append("")
    return "\n".join(out)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip KG writes; only compute and report.")
    parser.add_argument("--preview-patterns", action="store_true",
                        help="Emit data/extraction_pattern_preview.md for review.")
    parser.add_argument("--origin", type=str, default=None,
                        help="Limit to a single TM origin (filename).")
    parser.add_argument("--output-dir", type=Path, default=Path("data"),
                        help="Where to write review / drop / preview files.")
    parser.add_argument("--no-bilingual", action="store_true",
                        help="Skip the bilingual title_sl fill pass.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Fire-test: process only the first N entries of the (filtered) TM.")

    # Two-phase pipeline
    parser.add_argument("--export-segments", action="store_true",
                        help="Phase A: Export segments to JSON for OMP smol agent dispatch.")
    parser.add_argument("--ingest-extractions", action="store_true",
                        help="Phase B: Read smol agent results from data/smol_extractions.json, score, ingest.")
    parser.add_argument("--write", action="store_true",
                        help="Actually write to KG (otherwise dry-run unless --ingest-extractions alone).")
    parser.add_argument("--no-fallback", action="store_true",
                        help="Disable book_extractor regex fallback. Use ONLY smol extractions. "
                             "Recommended when smol coverage is complete.")
    parser.add_argument("--kg-path", type=Path, default=None,
                        help="Write to this KG path instead of data/knowledge.db. Use a fresh "
                             "path (e.g. data/knowledge_smol.db) to build a clean rebuilt graph "
                             "without contaminating the live KG.")
    args = parser.parse_args()

    # Gate: must pick exactly one of export/ingest
    if not args.export_segments and not args.ingest_extractions:
        # Default: export segments (first phase)
        args.export_segments = True
        print("[hint] No --export-segments or --ingest-extractions given; defaulting to export.")

    # Explicit gating for ingest phase
    if args.ingest_extractions:
        if args.write:
            args.dry_run = False
        elif not args.dry_run and not args.preview_patterns:
            print("[hint] --ingest-extractions without --write — defaulting to --dry-run.")
            args.dry_run = True
    else:
        # Export phase is always dry
        args.dry_run = True

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # ── Load TM and build contexts (shared between both phases) ──
    print(f"[1/5] Loading TMs from data/tm/ …")
    tm = TranslationMemory()
    entries = tm.entries
    print(f"      {len(entries)} TM entries total")

    if args.origin:
        entries = [e for e in entries if e.get("origin") == args.origin]
        print(f"      filtered to origin={args.origin}: {len(entries)} entries")

    if args.limit:
        entries = entries[: args.limit]
        print(f"      [fire-test] limited to first {len(entries)} entries")

    print(f"[2/5] Building per-origin contexts (segment classification, profile derivation) …")
    contexts = build_origin_contexts(entries)
    for c in contexts:
        print(f"      origin={c.origin:24s} segments={len(c.labels):6d} dominant={c.dominant}")

    # ── Load attribution ──
    print(f"[3a/5] Loading curator-validated segment→container attribution …")
    ATTRIBUTION_PATH = Path("data/quarantine/_segment_title_attribution.json")
    attribution_by_origin: dict[str, dict[int, str]] = {}
    if ATTRIBUTION_PATH.exists():
        raw_attr = json.loads(ATTRIBUTION_PATH.read_text(encoding="utf-8"))
        for tmx_name, segs in raw_attr.items():
            local_map: dict[int, str] = {}
            for sidx_str, val in segs.items():
                try:
                    sidx = int(sidx_str)
                except (TypeError, ValueError):
                    continue
                if isinstance(val, list):
                    cid = next((v for v in val if isinstance(v, str) and v.startswith("source:")), None)
                elif isinstance(val, str) and val.startswith("source:"):
                    cid = val
                else:
                    cid = None
                if cid:
                    local_map[sidx] = cid
            attribution_by_origin[tmx_name] = local_map
        total_attr = sum(len(v) for v in attribution_by_origin.values())
        print(f"       {total_attr} attributed segments across {len(attribution_by_origin)} TMX files")
        for tmx_name, m in attribution_by_origin.items():
            print(f"       - {tmx_name}: {len(m)} segments")
    else:
        print(f"       (no attribution file at {ATTRIBUTION_PATH}; skipping retargeting)")

    # ── Load the segment_to_book map (book-key shorthand → container) ──
    # `_segment_to_book.json` carries the per-segment book attribution using
    # short keys ('skrb_kunst', 'zavzemanje') that map to the canonical
    # COBISS container slugs. This adds another ~3,440 segments to the
    # attribution. Curator file wins on conflicts; smol payload runs last.
    SEGMENT_TO_BOOK_PATH = Path("data/quarantine/_segment_to_book.json")
    BOOK_KEY_TO_CONTAINER = {
        "skrb_kunst": "source:kunst-zivljenje-umetnosti",
        "zavzemanje": "source:zaloznik-jasmina-zavzemanje-prostora-2024",
    }
    if SEGMENT_TO_BOOK_PATH.exists():
        s2b = json.loads(SEGMENT_TO_BOOK_PATH.read_text(encoding="utf-8"))
        added_count = 0
        for tmx_name, segs in s2b.items():
            local_map = attribution_by_origin.setdefault(tmx_name, {})
            for sidx_str, val in segs.items():
                try:
                    sidx = int(sidx_str)
                except (TypeError, ValueError):
                    continue
                if sidx in local_map:
                    continue  # curator wins
                # val is typically a list of book-keys; take first known mapping
                book_keys = val if isinstance(val, list) else [val]
                for bk in book_keys:
                    cid = BOOK_KEY_TO_CONTAINER.get(bk)
                    if cid:
                        local_map[sidx] = cid
                        added_count += 1
                        break
        new_total = sum(len(v) for v in attribution_by_origin.values())
        print(f"[3a.1/5] _segment_to_book.json: +{added_count} new attributions → "
              f"{new_total} total")

    # ── Load ngram attribution (n-gram match results from build_segment_attribution) ──
    # `_segment_attribution_ngram.json` carries bare slugs without year suffix
    # (e.g. 'pregl-arjan-na-platnu'). Resolve them to canonical COBISS
    # containers via prefix match against existing KG container IDs.
    NGRAM_PATH = Path("data/quarantine/_segment_attribution_ngram.json")
    NGRAM_MIN_CONFIDENCE = 0.5
    if NGRAM_PATH.exists():
        ng = json.loads(NGRAM_PATH.read_text(encoding="utf-8"))
        # Build prefix lookup from existing KG container IDs
        from translate_core.knowledge_graph import KnowledgeGraph
        _kg = KnowledgeGraph()
        prefix_to_container: dict[str, str] = {}
        for nid, ndata in _kg.G.nodes(data=True):
            if not nid.startswith("source:"):
                continue
            if ndata.get("project_type") not in {
                "book_translation", "article_translation",
                "festival_programme", "exhibition_catalogue",
            }:
                continue
            slug = nid[len("source:"):]
            # Try slug stripped of trailing -year
            for stem_len in range(len(slug), 8, -1):
                stem = slug[:stem_len].rstrip("-0123456789")
                if stem and stem not in prefix_to_container:
                    prefix_to_container[stem] = nid
                    break

        ngram_added = 0
        ngram_skipped_low_conf = 0
        ngram_unresolved = 0
        for tmx_name, segs in ng.items():
            local_map = attribution_by_origin.setdefault(tmx_name, {})
            for sidx_str, val in segs.items():
                try:
                    sidx = int(sidx_str)
                except (TypeError, ValueError):
                    continue
                if sidx in local_map:
                    continue  # curator and segment_to_book wins
                if not isinstance(val, list) or not val:
                    continue
                top = val[0]
                if not isinstance(top, list) or len(top) < 2:
                    continue
                slug, conf = top[0], top[1]
                if conf < NGRAM_MIN_CONFIDENCE:
                    ngram_skipped_low_conf += 1
                    continue
                # Resolve to KG container by prefix
                resolved = None
                if f"source:{slug}" in {nid for nid in _kg.G.nodes()}:
                    resolved = f"source:{slug}"
                else:
                    # Try as prefix of any container slug
                    for stem, cid in prefix_to_container.items():
                        if slug.startswith(stem) or stem.startswith(slug):
                            resolved = cid
                            break
                if resolved:
                    local_map[sidx] = resolved
                    ngram_added += 1
                else:
                    ngram_unresolved += 1
        new_total = sum(len(v) for v in attribution_by_origin.values())
        print(f"[3a.2/5] _segment_attribution_ngram.json (conf≥{NGRAM_MIN_CONFIDENCE}): "
              f"+{ngram_added} new attributions ({ngram_skipped_low_conf} low-conf skipped, "
              f"{ngram_unresolved} unresolved) → {new_total} total")

    # ── Proximity propagation: fill gaps between attributed segments ──
    # If segments S₁ and S₂ in the same TMX are both attributed to container
    # X (where S₁ < S₂ and the gap |S₂-S₁| ≤ MAX_GAP), interpolate the
    # intervening segments as also belonging to X. Conservative: only fills
    # gaps where BOTH bookends agree on the container.
    MAX_GAP = 100  # propagate across small gaps; bigger gaps are book boundaries
    propagated_count = 0
    for tmx_name, local_map in list(attribution_by_origin.items()):
        if not local_map:
            continue
        sorted_keys = sorted(local_map.keys())
        for i in range(len(sorted_keys) - 1):
            s1, s2 = sorted_keys[i], sorted_keys[i + 1]
            if s2 - s1 <= 1:
                continue  # adjacent — no gap
            if s2 - s1 > MAX_GAP:
                continue  # too far — likely a book boundary
            c1, c2 = local_map[s1], local_map[s2]
            if c1 != c2:
                continue  # bookends disagree — keep gap unattributed
            for sidx in range(s1 + 1, s2):
                if sidx not in local_map:
                    local_map[sidx] = c1
                    propagated_count += 1
    new_total = sum(len(v) for v in attribution_by_origin.values())
    print(f"[3a.3/5] Proximity propagation (gap≤{MAX_GAP}, agreeing bookends): "
          f"+{propagated_count} new attributions → {new_total} total")
    print(f"[3a.2/5] Loading seeded book manifest (legacy fallback) …")
    manifest = SeedManifest.load()
    print(f"       {len(manifest.works)} seeded works in manifest")
    book_claims = find_book_anchors(entries, manifest)
    print(f"       {len(book_claims)} book anchors found in TMs:")
    for claim in book_claims:
        print(f"       - {claim.author} / {claim.title_orig}")
        print(f"           anchored at {claim.origin}:{claim.anchor_idx}, pattern={claim.matched_pattern!r}")
        print(f"           segment_ranges: {len(claim.segment_ranges)} range(s), {claim.total_segments_in_range} segments total")

    # ── Compute origin offsets ──
    origin_offsets: dict[str, int] = {}
    for i, e in enumerate(entries):
        o = e.get("origin", "")
        if o not in origin_offsets:
            origin_offsets[o] = i

    # ── Phase A or B ──
    if args.export_segments:
        export_segments(contexts, attribution_by_origin, book_claims, origin_offsets)
        return

    if args.ingest_extractions:
        entries_by_origin: dict[str, list[dict]] = {
            c.origin: [
                {"source": lbl.src, "target": lbl.tgt, "origin": lbl.origin}
                for lbl in c.labels
            ]
            for c in contexts
        }
        ingest_extractions(
            args, contexts, attribution_by_origin, book_claims,
            origin_offsets, entries_by_origin,
        )
        return


if __name__ == "__main__":
    main()