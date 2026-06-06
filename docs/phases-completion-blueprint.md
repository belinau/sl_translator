# Phases Completion Blueprint — Minimum Correct Path

**Produced:** 2026-06-06
**References:** plan `docs/superpowers/plans/2026-06-06-parsing-simplification.md`,
phase log `docs/superpowers/plans/2026-06-06-phase-log.md`,
blueprint `docs/phase4_blueprint.md`

---

## 1. Plan vs. Reality — Phases 4-8

Phases 4-8 are committed. Forward-risk deviations:

**Phase 4.** Migration script (`migrate_to_neutral_ontology.py`) built then
reverted (commit `dee8bec`) as premature — correct per no-mid-plan-migration
constraint. Four open follow-ups:

- OF-1: `tests/test_link_sl_published_by.py` references the old method name.
  Mechanical test rename.
- OF-2: `tests/test_kg_ingest_compliance.py::test_sl_published_by_edge_present`
  still failing. One assert rename.
- OF-3: `scripts/ingest_extra_containers.py` + `data/extra_containers.json` —
  plan line 578 deliverable, never built.
- OF-5: 75 reader migration sites per `docs/phase4_reader_inventory.md` —
  not done. Pre-Phase-4 nodes still carry legacy field names; readers get
  blanks until end-of-plan migration.

**Not addressed in any phase:** `document_pair_pipeline.py:599-601` still
emits `title_en`/`title_sl` into `kg.add_source_text_node(**extra)`. Every
`process_pair` run writes invariant-4-violating field names.

**Phases 5-10 on target.** Chokepoint live at `kg_ingest_entities.py:68-69`.
Phase 6 attribution: 37 144 attributed / 0 conflicts / 3 107 unanchored on
the live 40 251-entry TM.

---

## 2. Broken-Shit Risk Catalog

**R1 — Active invariant-4 writer (HIGH).** `document_pair_pipeline.py:597-602`
builds `extra = {"title_en": ..., "title_sl": ...}` and passes to
`kg.add_source_text_node(**extra)`. `knowledge_graph.py:395-460` forwards
`**kwargs` straight to `G.add_node`. Every pipeline run writes forbidden
field names. NOT in Phase 11 delete list.

**R2 — Slug mismatch kills attribution coverage (HIGH).**
`data/segment_title_attribution.json` uses year-less curator slugs
(e.g. `source:bester-vid-za-slavo`). COBISS `_make_source_id`
(`ingest_personal_bibliography.py:74-83`) generates
`<author_slug>-<title[:60]>-<year>`. Of 19 distinct anchor_cids, only 2
exist as KG nodes. `verify_container_spans.py:133-154` COBISS coverage
reports near-zero because the ID sets don't intersect.

**R3 — `build_segment_attribution.py:32` STA_PATH stale (MEDIUM).** Line 32:
`Path("data/quarantine/_segment_title_attribution.json")`. Phase 6
(commit `5f03f73`) promoted this to `data/segment_title_attribution.json`.
Any re-run reads the old path.

**R4 — `ingest_extra_containers.py` missing (MEDIUM).** OF-3.
`CONTAINER_PROVENANCE` at `kg_ingest_entities.py:68` declares
`"curator_extra"` but no script writes it. Containers outside COBISS have
no ingest path.

**R5 — 3 515 untagged source_text nodes (LOW, defer justified).**
`kind=None, provenance=None`. Originate from pre-Phase-5 writers. No
active writer produces them after Phase 5. Readers using neutral fields
get blank results, not failures. Correct cleanup gate: re-enable
`validate_kg.py` enforcement (reverted at `dee8bec`) after Phase 11
deletes the legacy writers. Mid-plan migration banned by AGENT_CONTEXT §3.

**R6 — Phase 11 SUNSET items pending (MEDIUM).** Three Phase-4-deferred
items: smol prompt-level `slovenian_edition` key rename, `cobiss_parser.py:43`
`title_en` field rename, SUNSET tag sweep.

---

## 3. Minimum Correct Completion Path

Prerequisites (mechanical): land OF-1 (test method rename) and OF-2 (one
assert rename) before any test-suite gate.

### Step A — Fix R1: neutral fields in `document_pair_pipeline.py`

Add `orig_lang: str` and `translation_lang: str` keyword-only parameters
to `_ingest_match` at line 561. Replace lines 597-602:

```python
extra: dict = {
    "project_type": en_payload.get("project_type") or "cited_work",
    "title_orig": match.title_orig,
    "orig_lang": orig_lang,
    "title_translation": match.title_translation,
    "translation_lang": translation_lang,
    "container_work_id": container_work_id,
}
```

Rename `CitationMatch` dataclass fields (lines 82-83) from
`title_en`/`title_sl` to `title_orig`/`title_translation`. Update both
`CitationMatch(...)` constructors at lines 413-419 and 423-429.

Call site at line 814 passes `orig_lang=en_side.lang,
translation_lang=sl_side.lang`. Both available — `en_side`/`sl_side` are
`SideParsed` instances set at lines 778-779; `.lang` is populated and
validated by `parse_side` at line 443. No new dependency; no language
detection.

### Step B — Fix R3: `build_segment_attribution.py:32`

```python
STA_PATH = Path("data/segment_title_attribution.json")
```

One-line. Does not block Step E (proof harness uses curator anchors,
not ngram output), but must land before any ngram re-run.

### Step C — Resolve R2: slug mismatch

Run `scripts/forensic_audit.py`. The classification table at lines 162-175
buckets each of the 19 anchor_cids into: `cobiss-matched`,
`cobiss-not-ingested`, `kg-orphan-non-cobiss`, `curator-extra`.

For `cobiss-matched` / `cobiss-not-ingested`: update
`data/segment_title_attribution.json` to replace the year-less slug with
the COBISS-generated KG node ID. The audit prints the COBISS-generated ID
at line 157 via `cobiss_slug_to_entry`; reconstruct it as
`source:<_make_source_id(entry.title, entry.year, author_slug)>`.

For `curator-extra` (not in COBISS): populate `data/extra_containers.json`
with the required fields, then run `scripts/ingest_extra_containers.py`
(Step D).

### Step D — Build R4: `scripts/ingest_extra_containers.py` + `data/extra_containers.json`

Per plan line 578 and `docs/phase4_blueprint.md §3.2`. Spec:

- Load `data/extra_containers.json`. Empty list → exit 0 (idempotent).
- Per record: skip if `kg.G.has_node(record["container_id"])`.
- Field ALLOWLIST for node write: `title_orig`, `title_translation`,
  `orig_lang`, `translation_lang`, `year`, `publisher_city`. Unrecognised
  fields ignored.
- `kg.add_source_text_node(text_id=container_id, title=...,
  project_type=..., provenance="curator_extra", **kwargs)`.
- Wire `translated_by` → `translator_agent_id`. Warn if agent absent; do
  NOT auto-create.
- Wire `published_by` if `publisher` present.
- Exit codes: 0 success, 1 missing required fields, 2 KG load failure.

`provenance="curator_extra"` accepted by Phase 5 chokepoint
(`CONTAINER_PROVENANCE`, `kg_ingest_entities.py:68`).

Initial `data/extra_containers.json`: `[]`. Curator populates with
records per plan line 677 schema for the `curator-extra` anchor_cids
identified in Step C.

### Step E — End-to-End Attribution Proof

`scripts/verify_container_spans.py` is the harness. After Steps B, C, D:

```
.venv/bin/python3 scripts/verify_container_spans.py
```

Expected correctness indicators:

- `cobiss_personal translated_work nodes in KG: N` ≥ 110 (Phase 4 result)
- `of those anchored in TM via curator anchors: K` — at minimum 15 of 19
  (up from 2)
- Per attributed container: `span: t_index S .. E (N segments)` with head
  and tail segment text printed

The span end is correct by design: `container_attribution.py` propagates
each anchor through all subsequent t_indexes of the same origin until the
next anchor fires — that firing point IS the container boundary. The
script prints `after[t=E+1]` text and its container (lines 97-103),
proving boundary detection. This answers the core verification ask:
start is known via the title-match anchor; end is known via the next
anchor's t_index minus one; chronological correctness is guaranteed by
Phase 1's `t_index`.

### Step F — Phase 11 SUNSET (three items, file deletions excluded)

**F1 — Smol prompt rename.** Locate `# SUNSET: Phase 11` tag on the smol
prompt-level `slovenian_edition` JSON-schema key in `smol_extractor.py`.
Rename to `translation_edition`. Update the builder to read the new key.
Run a smol regression sample against representative TM segments; confirm
output content unchanged. Per plan Step 11.2.

**F2 — `CobissEntry.title_en` rename.** `cobiss_parser.py:43` field
`title_en` → `title_second_side`. Update `cobiss_classifier.py:88-94,
101-110` (`title + " " + title_en` keyword-scan sites). Update
`ingest_personal_bibliography.py` at the Phase-4 kwarg-building site
reading `entry.title_en`. Update tests. Per plan Step 11.3.

**F3 — SUNSET tag sweep.** `grep -rn '# SUNSET: Phase 11' --include='*.py' .`
— triage each hit. Remove tag and guarded code where the prerequisite
(book-ingest scripts gone) is satisfied.

---

## 4. Self-Correction Notes

**SC1 — Steps C and D are coupled, not parallel.** `forensic_audit.py`
must run first. The classification table is input to both the Step C
slug updates and the Step D curator data entry. Sequence: audit →
classify → fix slugs AND populate `extra_containers.json` → ingest → proof.

**SC2 — Step B does not block Step E.** `verify_container_spans.py` loads
curator anchors from `data/segment_title_attribution.json` directly, not
from the ngram output. Fix STA_PATH for hygiene; do not treat it as a
proof-harness blocker.

**SC3 — OF-1 / OF-2 are clean-test-gate preconditions, not architecture.**
One-line renames. Land them before any Step A-D implementation to avoid
baseline failures clouding test output.

**SC4 — `kind="translated_work"` on COBISS nodes (this session's addition)
is not required by the plan.** Phase 5's `_route_record` reads `kind` on
INCOMING records (smol, doc_pair, COBISS ingest payloads), not on KG
nodes. The session-added `kind="translated_work" if ptype in
CONTAINER_TYPES else "cited_work"` (line 187-189 of the ingester) is
harmless but beyond plan scope. Leave it in place — removing it now would
require a second ingest pass and the KG already carries 110 such tags.

**SC5 — No deviations from the user's explicit constraints.** No new
dependencies. No backward-compat fallbacks. No mid-plan migration. No
blind language direction. Personal name absent from this prose.

---

## Source file references

- `translate_core/document_pair_pipeline.py` — lines 561-614, 778-779, 814
- `scripts/build_segment_attribution.py` — line 32
- `scripts/forensic_audit.py` — lines 146-175
- `scripts/verify_container_spans.py` — lines 133-154
- `translate_core/container_attribution.py` — lines 77-84, 97-158
- `translate_core/kg_ingest_entities.py` — lines 68-69
- `docs/phase4_blueprint.md` — §3.2 (ingest_extra_containers spec)
- `docs/superpowers/plans/2026-06-06-parsing-simplification.md` —
  lines 578, 677, 1444-1448
- `translate_core/cobiss_parser.py` — line 43
- `data/segment_title_attribution.json` — 19 year-less anchor_cids
- `scripts/ingest_personal_bibliography.py` — lines 74-83 (slug formula)
