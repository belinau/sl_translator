# Phase 6 Call Graph Survey

## 1. `_segment_title_attribution.json` (data/quarantine/)

### File structure (first 30 lines)
```json
{
  "big-EN-SL.tmx": {
    "176": ["source:zaloznik-jasmina-zavzemanje-prostora-2024"],
    "188": [
      "source:zaloznik-jasmina-zavzemanje-prostora-2024",
      "source:pregl-arjan-na-platnu",
      "source:terzan-kaja-gledalisce-kot-prostor",
      "source:suvakovic-misko-nsk"
    ],
    ...
  }
}
```
Shape: `{origin_tmx: {seg_idx (int as string): [container_slugs]}}`

### Readers & Writers

| Site | Type | Purpose |
|------|------|---------|
| `run_entity_extraction.py:529` | reader | Load curator-validated segment→container attribution |
| `run_entity_extraction.py:532-548` | reader | Parse attribution map and key-map for disambiguation |
| `scripts/build_segment_attribution.py:32` | writer | Build (auto-generated from source DOCX n-gram matching) |

### Load signature
`run_entity_extraction.py:530` — `attribution_by_origin: dict[str, dict[int, str]]` (tmx → seg_idx → container_id after "source:" prefix removed)

---

## 2. seeded_book_finder callers (Phase 6 retirement targets)

### Function imports & definitions
- **`find_book_anchors`** defined at `seeded_book_finder.py:107`
- **`book_claims_to_records`** defined at `seeded_book_finder.py:197`
- **`BookClaim.contains`** method at `seeded_book_finder.py:60`
- **`claim_for_segment`** defined at `seeded_book_finder.py:187`

### Callers

#### `find_book_anchors` (seeded manifest → BookClaim objects)
- **`run_entity_extraction.py:689`** — main call in `main()`:
  ```python
  book_claims = find_book_anchors(entries, manifest)
  ```
  Produces list of BookClaim for all seeded translated works.

#### `book_claims_to_records` (BookClaim → ingest-ready translated_work records)
- **`run_entity_extraction.py:149`** — ingest phase, authoritative seeded records:
  ```python
  seeded_records = book_claims_to_records(book_claims)
  all_records.extend(seeded_records)
  ```
  Writes payload with neutral `title_orig`/`title_translation`; legacy `title_sl`/`title_en` also in output (Phase 11 cleanup).

#### `claim_for_segment` (global_idx + origin → first matching BookClaim)
- **`run_entity_extraction.py:96`** — export phase, container routing for attribution:
  ```python
  claim = claim_for_segment(book_claims, global_idx, ctx.origin)
  if claim:
    container_work_id = claim.work_id
  ```
- **`run_entity_extraction.py:193–195`** — ingest phase, per-segment container resolution:
  ```python
  claim = claim_for_segment(book_claims, origin_start + local_idx, ctx.origin)
  if claim:
    container = claim.work_id
  ```
- **`run_entity_extraction.py:278`** — ingest post-process retargeting (cited_works):
  ```python
  claim = claim_for_segment(book_claims, global_idx, origin)
  ```

#### `BookClaim.contains` (implicit via `claim_for_segment` → `contains` check)
- **`seeded_book_finder.py:192`** — within `claim_for_segment` loop:
  ```python
  if c.contains(global_idx):
    return c
  ```

### Proximity-propagation block (run_entity_extraction.py:659–685)

Conservative gap-filling between attributed segments in same TMX:
```python
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
```
**Purpose**: Fill unattributed gaps where segment window is ≤100 and boundary containers agree.

### seeded-records branch (run_entity_extraction.py:148–156)

```python
# Seeded book records — authoritative translated_works
seeded_records = book_claims_to_records(book_claims)
all_records.extend(seeded_records)

# Author/translator/publisher per seeded book are created automatically by
# write_to_kg pass 2 (translated_work ingest) using pending_* fields
# populated by book_claims_to_records. No need to inject standalone
# agent_person/institution records here — they were duplicating into the
# review queue (book_extractor signals don't carry smol_verified_classification
# so they scored 0.65 → REVIEW). Cleaner to let pass 2 do it.
```
**Purpose**: Seed translated_work records into the ingest pipeline; downstream pass-2 routing handles agent creation.

---

## 3. `seg_idx` / `global_idx` consumers

### Consumer map for `export_segments` output

Phase 6 introduces `t_index` (chronological order, Phase 1 blueprint §1 done) as canonical. Current consumers:

| Consumer Site | Input source | Usage | Needs `t_index`? |
|---|---|---|---|
| `run_entity_extraction.py:162` | smol_by_key lookup | `(origin, seg_idx)` tuple key | No — local to origin |
| `run_entity_extraction.py:179` | per-label index | local `lbl.idx` cast to `seg_idx` | No — local |
| `run_entity_extraction.py:204–206` | `build_record` call | passed as third arg (`seg_idx: int`) | **YES** — segment sequence matters for tgt_lang context |
| `run_entity_extraction.py:281` | citation retargeting | `r["source"]["global_idx"] = global_idx` written for cited_works | **YES** — needed for TM cross-reference |
| `smol_extractor.py:944–950` | smol extraction replay | `item.get("seg_idx")` → `build_record` arg | **YES** — same as #3 |
| `ingest_book_footnotes.py:201–203` | footnote dedup | `r.get("global_idx")` in set membership | **YES** — must match exported segment seq |

### Consumer map for `smol_extractor.parse_smol_response` output

From `smol_extractor.py:302–339` (`build_record` signature):
```python
def build_record(
    ent: dict,
    origin: str,
    seg_idx: int,          # ← Phase 1B requires callers pass explicit (src_lang, tgt_lang)
    container_work_id: str = "",
    *,
    src_lang: str | None,
    tgt_lang: str | None,
) -> dict | None:
```

**Key consumers of smol payload fields**:
- **`seg_idx`** at `smol_extractor.py:949`: `seg_idx` passed to `build_record` — controls segment-context sourcing
- **`origin`** at `smol_extractor.py:947`: source-language detection via `_detect_source_lang(origin)`
- **`container_work_id`** at `smol_extractor.py:945–950`: passed to record builder for container routing

### Critical impact: `seg_idx` vs `t_index`

1. **Current role**: `seg_idx` is **natural list order** (position in origin's segment list).
2. **Phase 6 introduces**: `t_index` = **chronological order** (by entry creation/modification date).
3. **Consumers needing update**:
   - `run_entity_extraction.py:281` writes `global_idx` for cited_works TM refs — may need to switch to `t_index` if TM segment ordering changes post-Phase-1.
   - `ingest_book_footnotes.py:201–203` dedup check uses `global_idx` — must match the canonical indexing used in segment export.
   - `smol_extractor.py:949` passes `seg_idx` to `build_record` — verify context is **natural order** (not chronological).

---

## Summary

- **_segment_title_attribution.json**: 2 readers (run_entity_extraction, build_segment_attribution), shape {origin_tmx: {seg_idx: [sources]}}
- **seeded_book_finder**: 4 functions (find_book_anchors, book_claims_to_records, claim_for_segment, BookClaim.contains) called 5 times in run_entity_extraction; proximity-propagation fills gaps ≤100 segs when bookends agree; seeded records are injected before attribution post-processing.
- **seg_idx/global_idx**: 6 consumer sites; Phase 6 `t_index` may conflict with natural-order assumptions in cited-work TM refs (run_entity_extraction.py:281) and footnote dedup (ingest_book_footnotes.py:201–203).
