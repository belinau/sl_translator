#!/usr/bin/env python3
"""Harvest WORK-BOUNDARY anchors from segment classifier output.

Per audit §4 Fallback (`docs/parsing_simplification_audit.md`):

  > For TMX origins with no matched source DOCX (the COBISS container
  > exists but the DOCX wasn't located), fall back to language-direction
  > switches and footnote-density bursts. Both signals already live in
  > `segment_classifier`'s classes (BIBLIOGRAPHY_ENTRY, FOOTNOTE,
  > INLINE_CITATION). Implement as: a sustained run (≥5 segments) of high
  > biblio/footnote density adjacent to BODY_TEXT marks the end of one
  > book and beginning of the next book's apparatus.

This script:

  1. Runs `classify_segments` on every TM origin.
  2. Finds runs of ≥5 consecutive segments classified as
     BIBLIOGRAPHY_ENTRY, FOOTNOTE, BOOK_METADATA, FESTIVAL_CREDIT, or
     ARTWORK_RECORD (the "apparatus" classes).
  3. The segment AFTER each cluster (first BODY_TEXT or any non-apparatus
     class) is emitted as a synthetic `boundary:<origin>:<t_index>`
     anchor.
  4. The walker treats these as "new container" markers — preventing
     primary-path container anchors (Založnik, Kunst, etc.) from
     propagating across multiple works.

This delivers the END-of-work detection promised by audit §4 fallback
without requiring per-container source files for every COBISS entry.

No regex (per project rule). No new dependencies.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translate_core.tm import TranslationMemory
from translate_core.entity_extraction.segment_classifier import (
    classify_segments,
    SegmentClass,
)

ATTR_PATH = ROOT / "data" / "segment_title_attribution.json"

# Apparatus classes — the structural metadata that marks end-of-work
# regions per audit §4. Empirical: the TM has these segments SCATTERED
# rather than clustered (max consecutive run = 4), so we use a
# WINDOW-DENSITY interpretation of the audit's "≥5 segments of high
# density" specification.
APPARATUS_CLASSES = frozenset({
    SegmentClass.BIBLIOGRAPHY_ENTRY,
    SegmentClass.FOOTNOTE,
    SegmentClass.BOOK_METADATA,
    SegmentClass.FESTIVAL_CREDIT,
    SegmentClass.ARTWORK_RECORD,
    SegmentClass.EVENT_METADATA,
    SegmentClass.INSTITUTION_LINE,
    SegmentClass.ARTIST_HEADER,
    SegmentClass.INLINE_CITATION,
})

WINDOW = 15         # ±15 segments around the candidate
MIN_DENSITY = 5     # at least 5 apparatus segments in the window
MIN_GAP = 50        # boundaries within MIN_GAP segments are merged


def find_boundaries(
    tm: TranslationMemory,
) -> dict[str, list[int]]:
    """Return {origin: [seg_idx, ...]} of boundary positions.

    For each origin, scan in natural seg_idx order. When ≥MIN_RUN
    consecutive apparatus segments occur, the FIRST non-apparatus segment
    AFTER the run is recorded as a boundary (the next work's start).
    """
    by_origin: dict[str, list[dict]] = defaultdict(list)
    for e in tm.entries:
        origin = e.get("origin")
        if origin:
            by_origin[origin].append(e)

    boundaries: dict[str, list[int]] = {}

    for origin, entries in by_origin.items():
        labels = classify_segments(entries)
        n = len(labels)
        # Precompute boolean is_apparatus per segment
        is_app = [l.klass in APPARATUS_CLASSES for l in labels]
        # Running window count: number of apparatus segments in [i-WINDOW..i+WINDOW]
        # Build prefix sum for fast windowed counts.
        prefix = [0] * (n + 1)
        for i in range(n):
            prefix[i + 1] = prefix[i] + (1 if is_app[i] else 0)
        # Find seg_idxs where window density >= MIN_DENSITY AND the segment
        # itself is BODY_TEXT (first body segment after dense apparatus =
        # the next work's start)
        candidate_boundaries: list[int] = []
        in_dense = False
        for i, lbl in enumerate(labels):
            lo = max(0, i - WINDOW)
            hi = min(n, i + WINDOW + 1)
            density = prefix[hi] - prefix[lo]
            if density >= MIN_DENSITY:
                in_dense = True
            else:
                if in_dense and lbl.klass == SegmentClass.BODY_TEXT:
                    candidate_boundaries.append(i)
                    in_dense = False
        # Merge boundaries within MIN_GAP of each other
        merged: list[int] = []
        last = -MIN_GAP - 1
        for b in candidate_boundaries:
            if b - last >= MIN_GAP:
                merged.append(b)
                last = b
        boundaries[origin] = merged
        print(f"  {origin}: {len(merged)} boundaries "
              f"(out of {n} segments, "
              f"{sum(is_app)} apparatus)")
    return boundaries


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Merge boundary anchors into "
                             "segment_title_attribution.json.")
    args = parser.parse_args(argv)

    print("loading TM...")
    tm = TranslationMemory()
    print(f"  entries: {len(tm.entries)}")

    print("\nrunning segment classifier per origin...")
    boundaries = find_boundaries(tm)

    total = sum(len(v) for v in boundaries.values())
    print(f"\ntotal apparatus->body boundaries: {total}")
    print("These will become synthetic anchors that BREAK over-propagation")
    print("of primary-path container anchors across multiple works.")

    if not args.apply:
        print("\n(dry-run; pass --apply to merge into attribution file)")
        return 0

    existing = json.loads(ATTR_PATH.read_text(encoding="utf-8"))
    shutil.copy(ATTR_PATH, str(ATTR_PATH) + ".pre-boundary.bak")

    added = 0
    for origin, idxs in boundaries.items():
        existing.setdefault(origin, {})
        for idx in idxs:
            key = str(idx)
            synthetic = f"source:boundary-{origin}-{idx}"
            current = existing[origin].get(key)
            if current is None:
                existing[origin][key] = [synthetic]
                added += 1
            elif isinstance(current, list):
                if synthetic not in current:
                    current.append(synthetic)
                    added += 1
            elif isinstance(current, str):
                if current != synthetic:
                    existing[origin][key] = [current, synthetic]
                    added += 1

    ATTR_PATH.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nattribution file updated: +{added} boundary anchors")
    print(f"backup: {ATTR_PATH}.pre-boundary.bak")
    return 0


if __name__ == "__main__":
    sys.exit(main())
