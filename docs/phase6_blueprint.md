# Phase 6 Blueprint: `translate_core/container_attribution.py`

## Patterns and conventions

- `tm.py` Phase 1B exposes `t_index` (chronological global rank) on every entry via `_reindex_t_index`. The compat view `self.entries` preserves per-origin natural order because `_build_compat_entries` sorts by `raw_index` before re-numbering. That means `[e for e in tm.entries if e["origin"] == X][seg_idx]` gives the entry whose `t_index` is the correct lookup key.
- All pair buckets live in `_entries_by_pair`. `iter_chronological()` reads from those buckets without the compat swap, so it returns ACTUAL language codes.
- Curator file `data/segment_title_attribution.json` is `{origin_tmx: {seg_idx_str: [container_source_id, ...]}}`.
- `seeded_book_finder` is imported only by `run_entity_extraction.py:43-48`. No other callers.
- N-gram script writes `data/quarantine/_segment_attribution_final.json`; the run_entity_extraction loader reads `data/quarantine/_segment_attribution_ngram.json` — a pre-existing path mismatch. Phase 6 fixes both to write/read `data/segment_attribution_ngram.json` outside quarantine.
- `ingest_smol_extractions` at `smol_extractor.py:942-954` reads `item.get("seg_idx", -1)` — this is where the t_index fallback lives.

## Public surface — `translate_core/container_attribution.py` (NEW)

```python
from __future__ import annotations
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple, Literal

logger = logging.getLogger(__name__)


class Anchor(NamedTuple):
    origin: str
    t_index: int
    container_id: str           # WITHOUT "source:" prefix
    source: Literal["curator", "ngram"]


class AttributionResult(NamedTuple):
    attributed: dict[tuple[str, int], str]
    # key=(origin, t_index), value=container_id
    conflicts: list[dict]
    # {"origin", "t_index", "container_ids": [...], "sources": [...]}
    unanchored: list[tuple[str, int]]
    origins_by_pair: dict[str, tuple[str | None, str | None]]
    # origin -> (source_lang, target_lang) from actual TM entry (no swap)
```

### `load_curator_anchors(path, tm_entries) -> list[Anchor]`

Converts seg_idx-keyed JSON to t_index-keyed Anchors. The seg_idx in the curator file is per-origin natural position; `tm.entries` filtered by origin gives that position. Missing file returns `[]`.

```python
def load_curator_anchors(
    path="data/segment_title_attribution.json",
    tm_entries=None,
) -> list[Anchor]:
    return _load_anchors_from_json(path, source="curator", tm_entries=tm_entries)
```

### `load_ngram_anchors(path, tm_entries) -> list[Anchor]`

Same logic with `source="ngram"`. Missing file returns `[]`.

### Private helper `_load_anchors_from_json`

Build `by_origin: dict[str, list[dict]]` from `tm_entries` preserving insertion order. For each `(origin, seg_idx, value)` in the file:
- Pick `container_id` from value (list → first `"source:"`-prefixed; string → if `"source:"`-prefixed).
- `seg_idx >= len(by_origin[origin])` → log warning and skip (out-of-range).
- Append `Anchor(origin, t_index=by_origin[origin][seg_idx]["t_index"], container_id=cid_without_prefix, source=source)`.

### `attribute_segments_to_containers(anchors, tm_entries) -> AttributionResult`

Algorithm:

```
1. Group anchors by origin. Sort each group by t_index ascending.
2. Conflict detection: anchors with same (origin, t_index) but different
   container_id values → emit one conflict dict per (origin, t_index)
   collision. Remove those t_indexes from the usable anchor list. Anchors
   that agree (same t_index, same container_id) are deduplicated silently.
3. For each origin, walk its entries in t_index order:
     active = None
     anchor_iter = iter(sorted_anchors_for_origin)
     next_anchor = next(anchor_iter, None)
     for entry in sorted(entries_for_origin, key=lambda e: e["t_index"]):
         while next_anchor and next_anchor.t_index <= entry["t_index"]:
             active = next_anchor.container_id
             next_anchor = next(anchor_iter, None)
         if active is None:
             unanchored.append((origin, entry["t_index"]))
         else:
             attributed[(origin, entry["t_index"])] = active
4. origins_by_pair[origin] = (entries_for_origin[0]["source_lang"],
                              entries_for_origin[0]["target_lang"])
```

No `MAX_GAP`. No proximity propagation. Once an anchor fires, all subsequent entries for that origin inherit until the next anchor.

## Other changes in the same diff

### `scripts/build_segment_attribution.py`

- OUTPUT_PATH `data/quarantine/_segment_attribution_final.json` → `data/segment_attribution_ngram.json`.
- Output shape: `{origin: {str(seg_idx): "source:<slug>"}}` — single string per seg, not list. The script's existing `output[origin][str(idx)] = cobiss_id` already emits this shape; just point it at the new path.

### `run_entity_extraction.py`

- Delete `from translate_core.entity_extraction.seeded_book_finder import ...` (lines 43-48).
- Add `from translate_core.container_attribution import load_curator_anchors, load_ngram_anchors, attribute_segments_to_containers`.
- Fix `SMOL_EXTRACTIONS_PATH` (line 61): `Path("data/smol_entities_map/smol_extractions.json")`.
- Delete the seeded-records branch (lines 148-157).
- Replace the attribution-loading block (lines 529-685: segment_to_book + ngram + proximity-propagation + seeded manifest) with:

```python
print("[3a/5] Loading container attribution (curator + ngram) …")
anchors = load_curator_anchors(tm_entries=entries)
anchors += load_ngram_anchors(tm_entries=entries)
attrib_result = attribute_segments_to_containers(anchors, entries)
print(f"      attributed={len(attrib_result.attributed)} "
      f"conflicts={len(attrib_result.conflicts)} "
      f"unanchored={len(attrib_result.unanchored)}")
if attrib_result.conflicts:
    print(f"      WARNING: {len(attrib_result.conflicts)} attribution conflicts queued for review")
```

- Build `entries_by_t_index: dict[(origin, seg_idx), t_index]` once in `main()` from `entries`; pass to `export_segments` and `ingest_extractions`.
- Replace `claim_for_segment` calls with `attrib_result.attributed.get((ctx.origin, t_idx), "")`.
- `export_segments` adds `t_index` to each exported record.
- Remove `book_claims` and seeded-manifest loading.

### `translate_core/entity_extraction/smol_extractor.py` — `ingest_smol_extractions`

```python
# Old (line 944):
seg_idx = item.get("seg_idx", -1)

# New:
t_index = item.get("t_index")
if t_index is None:
    seg_idx = item.get("seg_idx", -1)
    _warn_once_t_index_fallback(item.get("origin", ""))
    effective_idx = seg_idx
else:
    effective_idx = t_index
```

Module-level:
```python
_t_index_fallback_warned: set[str] = set()

def _warn_once_t_index_fallback(origin: str) -> None:
    if origin not in _t_index_fallback_warned:
        logger.warning(
            "ingest_smol_extractions: origin=%r has no t_index; "
            "falling back to seg_idx. Re-run smol export after Phase 6.",
            origin,
        )
        _t_index_fallback_warned.add(origin)
```

## Test plan — `tests/test_container_attribution.py`

(a) **Basic chronological propagation:** entries `[t=5, 15, 40, 55]`, anchors at `t=10 (book-a)` and `t=50 (book-b)`. Expect `unanchored=[(origin, 5)]`; `attributed[t=15]=book-a, t=40=book-a, t=55=book-b`.

(b) **Conflict detection:** two anchors at same `(origin, t_index=100)` with different container_ids. Expect attributed has no key for `t_index=100`; conflicts has one dict with `container_ids=[X, Y]`, `sources=["curator", "ngram"]`.

(c) **Unanchored list:** one anchor at `t=30`; entries `[t=5, 15, 35]`. Expect `unanchored=[(origin,5), (origin,15)]`; `attributed={(origin,35): book-a}`.

(d) **t_index only:** entries carrying both `t_index` and `raw_index`. Anchor at `t_index=0`. Verify entry at `raw_index=100, t_index=0` is attributed; entry at `raw_index=0, t_index=100` (without anchor) is unanchored.

(e) **Multi-pair corpus:** entries from `en-sl.tmx` (`source_lang="en"`) and `hr-sl.tmx` (`source_lang="hr"`). Anchor per-origin. Expect attributed has both origins; `origins_by_pair["en-sl.tmx"]=("en","sl")` and `origins_by_pair["hr-sl.tmx"]=("hr","sl")`. No cross-origin attribution.

(f) **origins_by_pair surface:** key set equals distinct origins in `tm_entries`; values are `(source_lang, target_lang)` from the first entry seen per origin.

(g) **Missing ngram file:** `load_ngram_anchors(path="data/nonexistent.json", tm_entries=[])` returns `[]` without raising.

(h) **Agree-deduplication:** two anchors at same `(origin, t_index)` with same `container_id` from `curator` and `ngram` → no conflict, one attribution.

## Risks

1. **Indefinite propagation:** an origin with one anchor at `t=10` attributes ALL subsequent entries to that container. This is audit §4 design; documented in the walker docstring.

2. **Compat-view vs actual codes for `origins_by_pair`:** callers should pass `list(tm.iter_chronological())` (actual codes) or `tm.entries` (compat-swapped). Document that `iter_chronological` is the right input for downstream language-pair reasoning. For the seg_idx → t_index lookup, `tm.entries` natural-order is the contract (curator file was built that way).

3. **Per-origin ordering contract:** `_build_compat_entries` sort by `raw_index` is load-bearing for `_load_anchors_from_json`. Cross-reference comment in both files.

4. **Removing seeded-records branch (lines 148-157):** no test checks for the `seeded=True` signal or for translated_work records from this branch. Safe.

5. **`ingest_book_footnotes.py:201-203` uses `global_idx`** — that file is on Phase 11's deletion list. Phase 6 does not touch it.

## Build sequence (TDD green)

1. Create `translate_core/container_attribution.py` per public surface above.
2. Modify `scripts/build_segment_attribution.py` OUTPUT_PATH + output shape.
3. Modify `run_entity_extraction.py`: imports, `SMOL_EXTRACTIONS_PATH`, attribution block replacement, `claim_for_segment` removal, seeded-records branch removal.
4. Modify `smol_extractor.ingest_smol_extractions`: t_index with fallback warning.
5. Verify `.venv/bin/python3 -c "from run_entity_extraction import SMOL_EXTRACTIONS_PATH; print(SMOL_EXTRACTIONS_PATH.exists())"` returns `True`.
6. Real-data dry-run: `attributed > 1000`, `conflicts > 0` (proves conflict detection), `unanchored` surfaced.
