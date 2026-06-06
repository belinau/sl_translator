# Phase 5 Blueprint: `_route_record` dispatcher

## 1. Routing rules — decision table

Priority order: first matching rule fires. Phase 5 uses only `"direct"` and `"review"`.

### `kind = "translated_work"`

| provenance | result |
|---|---|
| `"cobiss_personal"` | `("direct", record)` |
| `"curator_extra"` | `("direct", record)` |
| `"tm_smol"` | `("review", {"reason": "provenance_mismatch_for_translated_work"})` |
| `"doc_pair"` | `("review", {"reason": "provenance_mismatch_for_translated_work"})` |
| `None` / absent / other | `("review", {"reason": "provenance_missing_or_unknown"})` |

### `kind = "cited_work"` — ordered checks

| condition | result |
|---|---|
| provenance absent / `None` / not in `VALID_PROVENANCE` | `("review", {"reason": "provenance_missing_or_unknown"})` |
| `orig_lang is None AND translation_lang is None` | `("review", {"reason": "language_pair_undetermined"})` |
| `container_index` provided AND `container_work_id` not in it | `("review", {"reason": "container_not_found"})` |
| otherwise (provenance ∈ `VALID_PROVENANCE`) | `("direct", record)` |

Valid provenances for `cited_work`: `{"cobiss_personal", "tm_smol", "doc_pair"}`.

### Other kinds: `agent_person`, `institution`, `concept`, `artwork`, `performance`

| provenance | result |
|---|---|
| ∈ `VALID_PROVENANCE` | `("direct", record)` |
| absent / `None` / other | `("review", {"reason": "provenance_missing_or_unknown"})` |

These kinds do NOT check language pair or container index.

## 2. Function placement and signature

**Placement:** after the constants block (after `STYLE_ALLOWLIST` at line 65), before `_segment_pointer`.

**Signature:**

```python
def _route_record(
    record: dict,
    *,
    container_index: set[str] | None = None,
) -> tuple[str, dict]:
```

`container_index` is the set of slugified container work IDs already written in pass 1. Pass 1 (during which containers are being built) passes `None` → skips container resolution. Pass 3 (deferred_cited loop) passes `set(work_id_by_payload.keys())`.

**Return semantics:**
- `("direct", record)` — caller falls through to existing per-kind handlers.
- `("review", {"reason": "<code>"})` — caller appends `record` (with `_route_reason` attached) to local `review` list and `continue`s.

**Caller changes — replace lines 509–514:**

Before:
```python
if kind == "translated_work":
    p = r["payload"]
    if not p.get("translator"):
        review.append(r)
        continue
```

After:
```python
route, route_meta = _route_record(r, container_index=None)
if route == "review":
    r["_route_reason"] = route_meta.get("reason")
    review.append(r)
    stats.bump(kind, ConfidenceTier.REVIEW)
    continue

if kind == "translated_work":
    p = r["payload"]
    if not p.get("translator"):   # O-20 check stays
        review.append(r)
        continue
```

Pass 3 (`deferred_cited` loop near line 656) gains the same routing call with `container_index=set(work_id_by_payload.keys())` before writing.

## 3. Constants — single source of truth

Add after `STYLE_ALLOWLIST` at line 65:

```python
# §5 provenance allowlist — single source of truth.
CONTAINER_PROVENANCE: frozenset[str] = frozenset({"cobiss_personal", "curator_extra"})
VALID_PROVENANCE: frozenset[str] = CONTAINER_PROVENANCE | frozenset({"tm_smol", "doc_pair"})
```

## 4. Producer provenance stamping

- **`scripts/ingest_personal_bibliography.py`** — writes directly to KG via `kg.add_source_text_node(provenance="cobiss_personal", ...)`. Already done in Phase 4. No change.
- **`scripts/ingest_extra_containers.py`** — writes directly to KG. No change for the routing chokepoint.
- **`translate_core/entity_extraction/smol_extractor.py`** — single insertion point at the end of `_dispatch` (lines ~300–335): after each `_build_*` call returns a non-None record, set `record["source"]["provenance"] = "tm_smol"` before returning. One patch covers all six builders.
- **`translate_core/document_pair_pipeline.py`** — `_heuristic_record` (lines 278–318) returns the record. Add `"provenance": "doc_pair"` to the `"source"` dict literal at lines 311–317.

## 5. Test plan — `tests/test_kg_ingest_routing.py` (new)

Pytest function-style. `tmp_path`-scoped `KnowledgeGraph`; `kg.save = lambda: None`. No live KG mutation.

Non-SL/EN pairs in fixtures where direction isn't the discriminator (e.g. `orig_lang="de"`, `translation_lang="fr"`).

| ID | Setup | Expected |
|---|---|---|
| (a) | translated_work, cobiss_personal, de/fr | direct; container node + translated_by |
| (a') | translated_work, curator_extra, de/en | same |
| (b) | translated_work, tm_smol | review; reason `provenance_mismatch_for_translated_work` |
| (c) | translated_work, doc_pair | review; same reason |
| (d) | cited_work, tm_smol, container resolves | direct; cited_in edge |
| (e) | cited_work, doc_pair | direct |
| (f) | cited_work, cobiss_personal | direct; written_by edge; NO cited_in |
| (g) | cited_work, valid provenance, container unresolved | review; reason `container_not_found` |
| (h) | cited_work, provenance absent | review; reason `provenance_missing_or_unknown` |
| (i) | agent_person, tm_smol | direct. agent_person, unknown_source → review |
| (j) | cited_work, valid provenance, `orig_lang=None AND translation_lang=None` | review; reason `language_pair_undetermined` |

All tests MUST fail against current code (TDD red).

## 6. Verification gate

After green, run:

```bash
.venv/bin/python3 run_entity_extraction.py --inspect 2>&1 | grep -E "router|direct|review|reject" | head -20
```

`--inspect` may not exist yet on `run_entity_extraction.py`. If absent, the green agent adds it OR exposes `simulate_routing(records) -> dict` as an importable function returning counts without writing.

Acceptance: at least one `translated_work` record from a non-COBISS provenance routed to review.

## 7. Risks — existing tests that break

**`tests/test_kg_ingest_compliance.py`** — its `_rec()` helper (lines 31–40) omits `provenance`. Under the new router these records route to review with `provenance_missing_or_unknown`, breaking ~7 tests:

- `test_container_requires_translated_by_edge`
- `test_off_allowlist_container_project_type_coerced`
- `test_source_text_ids_are_slugified`
- `test_no_direct_add_edge_from_ingest_module` (may pass for the wrong reason)
- `test_off_allowlist_cited_project_type_coerced`
- `test_off_allowlist_citation_style_dropped`
- `test_sl_published_by_edge_present`

**Green-phase fix:** extend `_rec()` with a `provenance` kwarg, default to a valid value. Update each fixture call:
- `translated_work` records → `"cobiss_personal"`
- `cited_work`, `artwork`, `agent_person`, `institution` records → `"tm_smol"`

One-line change per fixture call; not a redesign.

No other existing test file exercises `write_to_kg` with the old routing behavior.
