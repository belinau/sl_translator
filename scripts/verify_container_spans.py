#!/usr/bin/env python3
"""Phase 10.5 verification: prove container span detection works on real data.

Loads the TM with chronological t_index, loads curator anchors, runs
attribute_segments_to_containers, then reports per-(origin, container):
  - start t_index (first attributed segment)
  - end   t_index (last  attributed segment)
  - segment count
  - source text of the segment at the start (to show the title-match anchor)
  - source text of the segment at the end   (to show what the container "owns" last)
  - source text of the segment just AFTER the end (the next-container boundary)

Also reports:
  - total origins in TM
  - total chronological entries
  - unanchored entries (before any anchor for their origin)
  - conflicts (same (origin, t_index) with disagreeing container_ids)
  - sample COBISS containers that received NO anchor (i.e. cannot be located in TM)
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translate_core.tm import TranslationMemory
from translate_core.container_attribution import (
    load_curator_anchors,
    attribute_segments_to_containers,
)
from translate_core.knowledge_graph import KnowledgeGraph


def main() -> int:
    print("loading TM...")
    tm = TranslationMemory()
    print(f"  origins: {sorted({e['origin'] for e in tm.entries})}")
    print(f"  total compat entries: {len(tm.entries)}")
    print(f"  t_index range across compat view: "
          f"{min(e['t_index'] for e in tm.entries)}..{max(e['t_index'] for e in tm.entries)}")

    print("\nloading curator anchors...")
    anchors = load_curator_anchors(tm_entries=tm.entries)
    print(f"  anchors loaded: {len(anchors)}")
    by_origin = defaultdict(list)
    for a in anchors:
        by_origin[a.origin].append(a)
    for origin, lst in by_origin.items():
        print(f"  {origin}: {len(lst)} anchors, "
              f"{len({a.container_id for a in lst})} distinct container_ids")

    print("\nrunning attribute_segments_to_containers...")
    result = attribute_segments_to_containers(anchors, tm.entries)
    print(f"  attributed (origin, t_index) pairs: {len(result.attributed)}")
    print(f"  unanchored:                          {len(result.unanchored)}")
    print(f"  conflicts:                           {len(result.conflicts)}")
    if result.conflicts:
        print("  first 3 conflicts:")
        for c in result.conflicts[:3]:
            print(f"    {c}")

    # ── per-(origin, container) span computation ───────────────────────────
    span: dict[tuple[str, str], list[int]] = defaultdict(list)
    for (origin, t_index), container_id in result.attributed.items():
        span[(origin, container_id)].append(t_index)

    spans_sorted: dict[tuple[str, str], tuple[int, int, int]] = {}
    for key, t_list in span.items():
        spans_sorted[key] = (min(t_list), max(t_list), len(t_list))

    by_t = {(e["origin"], e["t_index"]): e for e in tm.entries}

    # Print top-10 largest spans per origin
    for origin in sorted({k[0] for k in spans_sorted}):
        origin_spans = [(cid, s, e, n) for (o, cid), (s, e, n)
                         in spans_sorted.items() if o == origin]
        origin_spans.sort(key=lambda x: -x[3])
        print(f"\n── {origin}: {len(origin_spans)} containers attributed; "
              f"top 5 by segment count ──")
        for cid, s, e_, n in origin_spans[:5]:
            print(f"  source:{cid}")
            print(f"    span: t_index {s} .. {e_}  ({n} segments)")
            head = by_t.get((origin, s))
            tail = by_t.get((origin, e_))
            if head:
                src_text = (head.get("source") or "")[:80]
                tgt_text = (head.get("target") or "")[:80]
                print(f"    head[t={s}] src: {src_text!r}")
                print(f"    head[t={s}] tgt: {tgt_text!r}")
            if tail and tail is not head:
                src_text = (tail.get("source") or "")[:80]
                print(f"    tail[t={e_}] src: {src_text!r}")
            # Show what's RIGHT AFTER the end (proves boundary detection)
            after = by_t.get((origin, e_ + 1))
            if after:
                src_text = (after.get("source") or "")[:80]
                print(f"    after[t={e_+1}] src: {src_text!r}  "
                      f"(this segment is attributed to: "
                      f"{result.attributed.get((origin, e_+1), '(none)')})")
            print()

    # ── span overlap check (within an origin, two spans should not interleave) ──
    print("── overlap check ──")
    overlap_count = 0
    for origin in sorted({k[0] for k in spans_sorted}):
        origin_spans = sorted(
            [(s, e_, cid) for (o, cid), (s, e_, _n) in spans_sorted.items()
             if o == origin],
            key=lambda x: x[0],
        )
        # A span "interleaves" another if its t_index range is not contiguous
        # within the origin AND its anchor was claimed back by a later one.
        # With the walker's design, attribution is monotonic by t_index: once
        # an anchor fires it owns segments until the next anchor. So two
        # containers in the SAME origin should occupy DISJOINT ranges with
        # the second starting at t_index == first.end + 1 (if anchors are
        # adjacent on consecutive t_indexes) OR later. They CAN'T overlap.
        for i in range(len(origin_spans) - 1):
            s1, e1, c1 = origin_spans[i]
            s2, e2, c2 = origin_spans[i + 1]
            if s2 <= e1:
                overlap_count += 1
                if overlap_count <= 3:
                    print(f"  OVERLAP in {origin}: "
                          f"{c1}[{s1}..{e1}] vs {c2}[{s2}..{e2}]")
    print(f"  total overlaps: {overlap_count}")

    # ── coverage: how many COBISS containers actually got anchored? ───────
    print("\n── COBISS container coverage ──")
    kg = KnowledgeGraph()
    cobiss_containers = [
        n for n, d in kg.G.nodes(data=True)
        if d.get("type") == "source_text"
        and d.get("kind") == "translated_work"
        and d.get("provenance") == "cobiss_personal"
    ]
    print(f"  cobiss_personal translated_work nodes in KG: {len(cobiss_containers)}")
    anchored_ids = {cid for _o, cid in spans_sorted.keys()}
    cobiss_ids = {n.removeprefix("source:") for n in cobiss_containers}
    coverage = cobiss_ids & anchored_ids
    print(f"  of those anchored in TM via curator anchors: {len(coverage)}")
    missing = sorted(cobiss_ids - anchored_ids)
    print(f"  NOT anchored (cannot be located in TM): {len(missing)}")
    if missing:
        print("  first 5 missing:")
        for m in missing[:5]:
            node = kg.G.nodes.get(f"source:{m}", {})
            title = node.get("title_orig") or node.get("title_translation") or "(no title)"
            year = node.get("year")
            print(f"    source:{m}  year={year}  title={title!r}")

    # ── unanchored breakdown ──
    print("\n── unanchored by origin ──")
    un_by_origin = defaultdict(int)
    for o, _t in result.unanchored:
        un_by_origin[o] += 1
    for o in sorted(un_by_origin):
        total_for_origin = sum(
            1 for e in tm.entries if e["origin"] == o
        )
        un = un_by_origin[o]
        print(f"  {o}: {un} unanchored / {total_for_origin} total "
              f"({100*un/total_for_origin:.1f}% pre-first-anchor)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
