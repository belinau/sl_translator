#!/usr/bin/env python3
"""Drop anchor cids in data/segment_title_attribution.json that don't
resolve to an existing source_text node in the KG.

These are the pre-Phase-6 year-less stems left over from when
build_segment_attribution.py emitted bare ngram-slug fallbacks (the
KG had no COBISS containers at the time, so normalization no-op'd).
With the harvester now writing canonical year-suffixed anchors, the
year-less entries are stale doubles that confuse the walker.

Default: dry-run. --apply writes the cleaned file (backup is made).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translate_core.knowledge_graph import KnowledgeGraph

ATTR_PATH = ROOT / "data" / "segment_title_attribution.json"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    print("loading KG...")
    kg = KnowledgeGraph()

    data = json.loads(ATTR_PATH.read_text(encoding="utf-8"))

    kept_count = 0
    dropped_count = 0
    dropped_cids: dict[str, int] = {}
    new_data: dict = {}
    for origin, seg_map in data.items():
        new_data[origin] = {}
        for seg_idx_str, val in seg_map.items():
            cids = val if isinstance(val, list) else [val]
            kept_cids: list[str] = []
            for c in cids:
                if not isinstance(c, str) or not c.startswith("source:"):
                    continue
                if kg.G.has_node(c):
                    kept_cids.append(c)
                    kept_count += 1
                else:
                    dropped_count += 1
                    dropped_cids[c] = dropped_cids.get(c, 0) + 1
            if kept_cids:
                new_data[origin][seg_idx_str] = (
                    kept_cids if len(kept_cids) > 1 else (
                        kept_cids if isinstance(val, list) else kept_cids[0]
                    )
                )

    # Normalise: prefer list form where the original was list, scalar where it was scalar
    for origin in new_data:
        for k, v in list(new_data[origin].items()):
            if isinstance(v, list) and len(v) == 1:
                # keep as list for consistency with the harvester output
                pass

    print(f"kept:    {kept_count} cid references")
    print(f"dropped: {dropped_count} cid references "
          f"({len(dropped_cids)} distinct unresolved cids)")
    for c, n in sorted(dropped_cids.items(), key=lambda x: -x[1])[:10]:
        print(f"  {c}: {n}")

    if not args.apply:
        print("\n(dry-run; pass --apply to write)")
        return 0

    shutil.copy(ATTR_PATH, str(ATTR_PATH) + ".pre-drop.bak")
    ATTR_PATH.write_text(
        json.dumps(new_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nattribution file cleaned: {ATTR_PATH}")
    print(f"  backup: {ATTR_PATH}.pre-drop.bak")
    return 0


if __name__ == "__main__":
    sys.exit(main())
