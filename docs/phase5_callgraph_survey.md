# Phase 5 callgraph survey

## 1. Existing review-queue write paths

All writes funnel through `translate_core/kg_ingest_entities.py`:

| Line | Pattern | Trigger |
|---|---|---|
| 500 | `review.append(r)` | `ConfidenceTier.REVIEW` (scored low) |
| 513 | `review.append(r)` | `translated_work` missing translator (O-20 violation) |
| 601 | `review.append(r)` | Unknown record kind (safety fallback) |
| 1015 | `review_path.write_text(json.dumps(review, ...))` | Final write to `data/extraction_review.json` |

`review` is a local list accumulated during `write_to_kg()`; written once at the end. No ad-hoc writers exist anywhere else in the ingest path.

UI side (out of scope for Phase 5):
- `kg_editor_ui.py:463` `_drop_kg_review()`
- `kg_editor_ui.py:1106` `page_kg_review()` writes scan results to `data/kg_review.json`

## 2. Provenance field consumers

Zero readers of `record["source"]["provenance"]` today.

Today `provenance` is only WRITTEN — `scripts/ingest_personal_bibliography.py:183` sets `kwargs["provenance"] = "cobiss_personal"` and passes it to `kg.add_source_text_node()` as a node attribute. Phase 5 introduces the first routing decision keyed on this field on the SOURCE record (not the node).

## 3. Current `translated_work` routing (lines 509–544)

```python
if kind == "translated_work":
    p = r["payload"]
    if not p.get("translator"):
        # O-20: container source_text MUST NOT be written without a translator.
        review.append(r)
        continue
    wid = _slugify(p["work_id"])
    # ... creates kg.add_source_text_node(...) with title_orig/title_translation
```

Accept gate today: `DIRECT_WRITE` tier AND has translator.
Reject gate: low-confidence or missing translator → `data/extraction_review.json`.

## 4. Anchor points for Phase 5

- Routing chokepoint: insert at lines 509–514, between translator check and node creation.
- Provenance guard: accept `kind="translated_work"` ONLY when `record["source"]["provenance"] ∈ {"cobiss_personal", "curator_extra"}`. Else → review with reason `provenance_mismatch_for_translated_work`.
- Routing decision happens before any KG writes or mutations — no side effects on early reject.
