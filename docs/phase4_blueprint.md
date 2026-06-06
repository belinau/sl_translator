# Phase 4 Blueprint — Language-Neutral KG, End-to-End

Produced by Phase 4 Step 4.1 (`feature-dev:code-architect`). The TDD-red
implementer reads §9 (test plan); the TDD-green implementer follows §10
(implementation order) and reads §1–§8 for the specifics.

**Two coordinator anchors:**

1. **Phase 4 COBISS work is EXACTLY TWO surgical edits** to
   `scripts/ingest_personal_bibliography.py`: (a) kwarg rename in the
   `kg.add_source_text_node(...)` call per `belina_role` branch; (b)
   finish the bilingual publisher TODO at lines 269-271.
2. **Phase 5 accepts BOTH container provenances** —
   `provenance="cobiss_personal"` AND `provenance="curator_extra"` —
   for `kind="translated_work"`.

**Hard constraints (BANNED throughout):**
- `d.get("title_orig") or d.get("title_en")` fallback chains.
- `lingua-py` / `langdetect` / `fasttext` — no new dependencies.
- `"sl"` / `"en"` literals in flow-control conditionals. Language codes
  appear ONLY as data values being written.
- Legacy field names (`title_en`, `title_sl`, `slovenian_edition`,
  `sl_published_by`) in any new write.

---

## §1 — Ontology revision diff

File: `/Users/bel/CascadeProjects/sl_translator/ontology.md`

### 1.1 §2.4.2 — delete legacy second paragraph; document `translation_edition`

**Replace lines 155–172** with:

```
#### 2.4.2 Bilingual title fields (canonical, from `docs/citation-extraction-spec.md`)

When a `source_text` exists in two languages (the user's translation
case), the bilingual form is encoded as:

- `title_orig` — the original-language title
- `title_translation` — the title in the translation language
- `orig_lang`, `translation_lang` — two-letter ISO 639-1 language codes
- `translation_edition: {publisher, city, year, translator, language}` —
  populated when the translation edition has distinct publication
  metadata (publisher, year, or translator differ from the original).
  The `language` field MUST be an ISO 639-1 code matching
  `translation_lang`.

DO NOT invent flat ad-hoc fields like `publisher_en` / `publisher_sl`
for new writes. The bilingual encoding above is canonical. The legacy
names `title_en`, `title_sl`, and `slovenian_edition` are FORBIDDEN in
new writes.
```

### 1.2 §3.2 — rename edge

**Line 246 before:**
```
| `(source_text) -[sl_published_by]-> (institution)` | Slovenian-edition publisher when SL edition differs from original |
```
**Line 246 after:**
```
| `(source_text) -[translation_published_by]-> (institution)` | Publisher of the translation edition when it differs from the original-edition publisher |
```
Edge count stays at 13.

### 1.3 §4 invariant 4 — rewrite

**Lines 295–300 before:**
```
4. **Bilingual data extraction is not optional.** A `source_text`
   record representing a citation that exists in both languages of the
   TM MUST carry both `title_en` and `title_sl` (or
   `title_orig` + `title_translation`). Unilingual citation records
   produced when the TM holds the other-language form are a
   correctness defect, not an acceptable interim state.
```
**Lines 295–300 after:**
```
4. **Bilingual data extraction is not optional.** A `source_text`
   record representing a citation that exists in both languages of the
   TM MUST carry `title_orig`, `title_translation`, `orig_lang`, and
   `translation_lang`. Unilingual citation records produced when the
   TM holds the other-language form are a correctness defect, not an
   acceptable interim state. The legacy names `title_en` and `title_sl`
   are FORBIDDEN; a node carrying them is a hard invariant violation
   caught by `validate_kg.py`.
```

---

## §2 — COBISS ingest kwarg-rename + publisher TODO

File: `/Users/bel/CascadeProjects/sl_translator/scripts/ingest_personal_bibliography.py`

**Two edits. Nothing else in this script changes.**

### Edit A — replace `title_sl`/`title_en` kwargs (lines 171–215)

Delete lines 171–184 and replace with:

```python
# Bilingual title handling. Fold subtitle into the primary title.
primary_title = entry.title
if entry.subtitle and entry.subtitle.lower() not in (entry.title or "").lower():
    primary_title = f"{entry.title}: {entry.subtitle}"
secondary_title = entry.title_en  # EN side per COBISS parser convention

kwargs = {}
# Assign canonical neutral fields based on belina_role.
# Language codes are DATA VALUES from the COBISS parser convention:
# entry.title = SL side, entry.title_en = EN side after "=".
# No text-level language detection.
if belina_role == "author":
    if primary_title:
        kwargs["title_orig"] = primary_title
        kwargs["orig_lang"] = "sl"
    if secondary_title:
        kwargs["title_translation"] = secondary_title
        kwargs["translation_lang"] = "en"
elif belina_role == "translator":
    if primary_title:
        kwargs["title_translation"] = primary_title
        kwargs["translation_lang"] = "sl"
    if secondary_title:
        kwargs["title_orig"] = secondary_title
        kwargs["orig_lang"] = "en"
elif belina_role == "editor":
    if primary_title:
        kwargs["title_orig"] = primary_title
        kwargs["orig_lang"] = "sl"
    if secondary_title:
        kwargs["title_translation"] = secondary_title
        kwargs["translation_lang"] = "en"
# belina_role is None only for unclassified entries, which are
# already skipped at lines 140-147. No else branch.
```

Update the `add_source_text_node` call (around line 210):

```python
node_id = kg.add_source_text_node(
    text_id=source_id,
    title=primary_title or secondary_title or f"Entry #{entry.entry_number}",
    project_type=ptype,
    provenance="cobiss_personal",
    **kwargs,
)
```

Lines 185–209 (year, extent, series, edition, journal_*, issn, isbn,
cobiss_id, urls kwargs) are unchanged.

### Edit B — finish bilingual publisher TODO (lines 269–271)

Replace lines 269–271 (TODO comments) with nothing. Guard the existing
publisher block (lines 254–267) and add the bilingual branch:

```python
if entry.publisher and ": =" not in entry.publisher:
    pub_name = entry.publisher.strip()
    if pub_name:
        inst_kind = classify_institution_kind(pub_name)
        inst_id = _make_institution_id(pub_name)
        inst_node = kg.add_institution_node(
            inst_id=inst_id, name=pub_name, kind=inst_kind,
        )
        report["institutions_created"] += 1
        if kg.link_published_by(node_id, inst_node):
            report["edges_created"] += 1
elif entry.publisher and ": =" in entry.publisher:
    # Bilingual publisher: "SL Inst Name: = EN Inst Name" COBISS convention.
    parts = entry.publisher.split(": =", maxsplit=1)
    primary_pub = parts[0].strip()
    translation_pub = parts[1].strip()

    if primary_pub:
        p_kind = classify_institution_kind(primary_pub)
        p_id = _make_institution_id(primary_pub)
        p_node = kg.add_institution_node(inst_id=p_id, name=primary_pub, kind=p_kind)
        report["institutions_created"] += 1
        if kg.link_published_by(node_id, p_node):
            report["edges_created"] += 1

    if translation_pub:
        t_kind = classify_institution_kind(translation_pub)
        t_id = _make_institution_id(translation_pub)
        t_node = kg.add_institution_node(inst_id=t_id, name=translation_pub, kind=t_kind)
        report["institutions_created"] += 1
        if kg.link_translation_published_by(node_id, t_node):
            report["edges_created"] += 1
```

---

## §3 — Extra-container ingest

### 3.1 `data/extra_containers.json` (NEW)

Initial content: `[]`. Record schema:

```json
{
  "container_id": "source:title-slug",
  "project_type": "book_translation",
  "title_orig": "Naslov v izvirniku",
  "orig_lang": "sl",
  "title_translation": "Title in translation",
  "translation_lang": "en",
  "year": 2024,
  "publisher": "Publisher name",
  "publisher_city": "Ljubljana",
  "translator_agent_id": "agent:urban-belina",
  "provenance": "curator_extra"
}
```

Required: `container_id`, `project_type`, `provenance`, `translator_agent_id`. Others optional.

### 3.2 `scripts/ingest_extra_containers.py` (NEW)

Flow:

1. Load `data/extra_containers.json` (or `--path`). Empty → exit 0.
2. Load KG.
3. Per record:
   a. If `kg.G.has_node(record["container_id"])` → skip (idempotent).
   b. Build kwargs from a recognised-field ALLOWLIST: `title_orig`, `title_translation`, `orig_lang`, `translation_lang`, `year`, `publisher_city`. Unrecognised fields IGNORED (prevents KG pollution).
   c. `kg.add_source_text_node(text_id=container_id, title=<title_orig or title_translation or container_id>, project_type=..., provenance="curator_extra", **kwargs)`.
   d. Wire `translated_by` → `translator_agent_id` (warn if agent absent; do NOT auto-create).
   e. Wire `published_by` → institution if `publisher` present.
4. Save KG. Print summary (`containers_created`, `containers_skipped`, `agents_missing`).

Exit code: 0 on success, 1 on missing required fields, 2 on KG load failure.

`provenance="curator_extra"` is accepted by Phase 5's routing chokepoint
alongside `"cobiss_personal"` for `kind="translated_work"`.

---

## §4 — Smol_extractor cleanup

File: `/Users/bel/CascadeProjects/sl_translator/translate_core/entity_extraction/smol_extractor.py`

### 4.1 Delete three `# SUNSET: Phase 11` alias-write blocks

- Lines 548–555 in `_build_cited_work` — entire `# SUNSET` block + the `if LANG_EN in {orig_lang, translation_lang}…` conditional.
- Lines 759–763 in `_build_artwork`.
- Lines 884–888 in `_build_performance`.

### 4.2 Builder remaps `slovenian_edition` → `translation_edition`

In `_build_cited_work`, around line 545 in the payload dict:

**Before:**
```python
"slovenian_edition": slovenian_edition,
```
**After:**
```python
"translation_edition": (
    {**slovenian_edition, "language": translation_lang}
    if slovenian_edition and translation_lang
    else None
),
```

`translation_lang` is already in scope (line 540). When None, the
sub-dict is dropped — consistent with the O-5 guard at lines 560–563.

The model's prompt-level JSON-schema key `"slovenian_edition"` stays
unchanged in Phase 4. The INPUT-side read (`sl = ent.get("slovenian_edition") or {}`)
is the bridge from model output → canonical payload. The prompt key
rename is Phase 11 SUNSET work.

### 4.3 Place new `# SUNSET: Phase 11` tag at the prompt template

In the prompt-template construction (the location where the model's
JSON-schema documentation declares the `slovenian_edition` key), add:

```python
# SUNSET: Phase 11 — rename prompt key "slovenian_edition" →
# "translation_edition" after model-regression testing confirms no
# recall drop. The builder already maps this key to translation_edition
# in the payload (§4.2). The prompt rename requires a separate model
# evaluation pass. Do not rename before Phase 11 regression suite runs.
```

---

## §5 — Migration script `scripts/migrate_to_neutral_ontology.py` (NEW)

CLI: `python scripts/migrate_to_neutral_ontology.py [--dry-run] [--apply] [--kg-path PATH]`

Default: `--dry-run`. `--apply` required to write.

### Algorithm (pseudocode)

```python
LEGACY_NODE_FIELDS = ("title_en", "title_sl", "slovenian_edition")
NEUTRAL_TITLE_FIELDS = ("title_orig", "title_translation")

def migrate(kg_path, apply):
    data = json.loads(Path(kg_path).read_text())
    nodes, edges = data["nodes"], data["edges"]

    # PASS 1 — build outgoing-relation index
    out_rels = defaultdict(set)
    for edge in edges:
        out_rels[edge["source"]].add(edge.get("relation"))

    # PASS 2 — edge rename
    edges_renamed = 0
    for edge in edges:
        if edge.get("relation") == "sl_published_by":
            if apply:
                edge["relation"] = "translation_published_by"
            edges_renamed += 1

    # PASS 3 — node field migration
    counts = {
        "nodes_migrated": 0,
        "nodes_already_neutral_legacy_stripped": 0,
        "nodes_skipped_no_legacy_fields": 0,
    }

    for node in nodes:
        has_legacy = any(k in node for k in LEGACY_NODE_FIELDS)
        has_neutral = any(k in node for k in NEUTRAL_TITLE_FIELDS)

        if not has_legacy:
            counts["nodes_skipped_no_legacy_fields"] += 1
            continue

        if has_neutral and has_legacy:
            if apply:
                for k in LEGACY_NODE_FIELDS:
                    node.pop(k, None)
            counts["nodes_already_neutral_legacy_stripped"] += 1
            continue

        # Full migration: use edge evidence for direction
        nid = node["id"]
        rels = out_rels.get(nid, set())
        title_en_val = node.get("title_en")
        title_sl_val = node.get("title_sl")
        sl_edition = node.get("slovenian_edition")

        if "translated_by" in rels:
            # SL is the translation target (corpus convention)
            if title_sl_val:
                node["title_translation"] = title_sl_val
                node["translation_lang"] = "sl"
            if title_en_val:
                node["title_orig"] = title_en_val
                node["orig_lang"] = "en"

        elif "written_by" in rels and "translated_by" not in rels:
            # Author wrote in SL
            if title_sl_val:
                node["title_orig"] = title_sl_val
                node["orig_lang"] = "sl"
                if title_en_val:
                    node["title_translation"] = title_en_val
                    node["translation_lang"] = "en"
            elif title_en_val:
                node["title_orig"] = title_en_val
                node["orig_lang"] = "en"

        else:
            # Neither edge (smol/doc_pair): SL-first corpus default
            if title_sl_val and title_en_val:
                node["title_orig"] = title_sl_val
                node["orig_lang"] = "sl"
                node["title_translation"] = title_en_val
                node["translation_lang"] = "en"
            elif title_sl_val:
                node["title_orig"] = title_sl_val
                node["orig_lang"] = "sl"
            elif title_en_val:
                node["title_orig"] = title_en_val
                node["orig_lang"] = "en"

        if isinstance(sl_edition, dict):
            node["translation_edition"] = {
                **{k: v for k, v in sl_edition.items()
                   if k in ("publisher", "city", "year", "translator")},
                "language": "sl",
            }

        if apply:
            for k in LEGACY_NODE_FIELDS:
                node.pop(k, None)

        counts["nodes_migrated"] += 1

    # PASS 4 — reshape JSON review queues
    queues_reshaped = 0
    for qpath in (Path("data/extraction_review.json"),
                  Path("data/kg_review.json")):
        queues_reshaped += reshape_review_queue(qpath, apply=apply)

    print(f"edges_renamed={edges_renamed}")
    for k, v in counts.items():
        print(f"{k}={v}")
    print(f"queue_records_reshaped={queues_reshaped}")

    if apply:
        Path(kg_path).write_text(json.dumps(data, ensure_ascii=False, indent=2))
```

`reshape_review_queue` applies the SL-first corpus default to queue payloads (queue records lack edge evidence) and writes the reshaped queue when `apply=True`.

**Idempotency:** second run finds `has_legacy=False` for previously migrated nodes → all counted under `nodes_skipped_no_legacy_fields`; zero writes.

**Dry-run baseline:** `edges_renamed ≈ 20` per audit. Deviation → inspect before applying.

---

## §6 — `knowledge_graph.py` factory changes

File: `/Users/bel/CascadeProjects/sl_translator/translate_core/knowledge_graph.py`

### 6.1 `add_source_text_node` — verified, no changes

Lines 402–425: method accepts `**kwargs` and passes through. New neutral kwargs flow unchanged.

### 6.2 Rename `link_sl_published_by` → `link_translation_published_by`

Line 657 method signature and line 663 relation string both rename. All callers updated in the same commit:
```bash
grep -rn "link_sl_published_by" . --include="*.py"
```

### 6.3 No other factory changes

---

## §7 — Reader migration (per `docs/phase4_reader_inventory.md`)

**Critical rule:** the inventory doc recommends backward-compat fallback chains. Those recommendations are BANNED. Every substitution below is a naked rename. No `or` chains. No fallback to legacy names.

### 7.1 `kg_editor_ui.py` — 16 sites

| Line | Old | New |
|---|---|---|
| 153 | `p.get("title_en"), p.get("title_sl")` | `p.get("title_orig"), p.get("title_translation")` |
| 161 | `p.get('title_en') or p.get('title_sl')` | `p.get('title_orig') or p.get('title_translation')` |
| 163 | `p.get('title_en')` | `p.get('title_translation')` |
| 176–177 | `title_en`, `title_sl` reads | `title_translation`, `title_orig` (update labels) |
| 184 | `p.get("slovenian_edition") or {}` | `p.get("translation_edition") or {}` |
| 254 | `p.get("title_en") or p.get("title_sl")` | `p.get("title_translation") or p.get("title_orig")` |
| 256–257 | `title_en=`, `title_sl=` kwargs | `title_orig=`, `title_translation=` |
| 284–285 | same as 256–257 | same |
| 288 | `slovenian_edition=` | `translation_edition=` |
| 315 | `p.get("title_en") or p.get("title_sl")` | `p.get("title_orig") or p.get("title_translation")` |
| 320–321 | `"title_en":`, `"title_sl":` | `"title_orig":`, `"title_translation":` |
| 1136 | `it.get("title_en") or it.get("title_sl")` | `it.get("title_orig") or it.get("title_translation")` |
| 1143–1144 | `it.get('title_en')`, `it.get('title_sl')` | `it.get('title_orig')`, `it.get('title_translation')` |
| 1158–1159 | `it.get("title_en")`, `it.get("title_sl")` | `it.get("title_orig")`, `it.get("title_translation")` |
| 1172 | `title_en=, title_sl=` | `title_orig=, title_translation=` |
| 1199 | `"title_en":`, `"title_sl":` | `"title_orig":`, `"title_translation":` |

### 7.2 `translate_core/kg_ingest_entities.py` — 13 sites

- Line 347: remove `or r['payload'].get('title_en') or r['payload'].get('title_sl')` from ID fallback.
- Line 385: `bool(ep.get("title_en") and ep.get("title_sl"))` → `bool(ep.get("title_orig") and ep.get("title_translation"))`.
- Lines 519–520: kwargs rename.
- Lines 681–682: `extras.setdefault("title_en"/"title_sl", ...)` → neutral.
- Lines 694–695: `p.get("slovenian_edition")` + `extras["slovenian_edition"]` → `translation_edition`.
- Line 702: remove `or p.get("title_en") or p.get("title_sl")` from title fallback.
- Line 740: `sl_pub = p.get("slovenian_edition") or {}` → `trans_pub = p.get("translation_edition") or {}`; `kg.link_sl_published_by(...)` → `kg.link_translation_published_by(...)`.
- Lines 798–799, 808, 865, 874: replace `title_en` / `title_sl` with `title_orig` / `title_translation`.

### 7.3 `translate_core/entity_extraction/bilingual_enrichment_batch.py`

All `node.get("title_en")` → `node.get("title_orig")`; all `node.get("title_sl")` → `node.get("title_translation")`. All payload writes of legacy fields → neutral.

### 7.4 `translate_core/entity_extraction/book_extractor.py`

`slovenian_edition` → `translation_edition` (with `language` field). `title_en` / `title_sl` → `title_orig` / `title_translation`.

### 7.5 `run_entity_extraction.py` (lines 234, 242, 309, 427)

Remove `or p.get("title_en")` / `or p.get("title_sl")` from display fallback chains.

### 7.6 `scripts/enrich_bilingual.py` (lines 105–108)

`title_en` / `title_sl` → `title_orig` / `title_translation`.

### 7.7 `process_document_pair.py` (lines 104, 117, 122)

Field-name substitutions in display fallback chains.

### 7.8 `translate_core/document_pair_pipeline.py`

15 reads of `title_en` / `title_sl` → `title_orig` / `title_translation`.

### 7.9 Test files

- `tests/test_link_sl_published_by.py`: rename `"sl_published_by"` assertions → `"translation_published_by"`. Consider renaming the file.
- `tests/test_kg_ingest_compliance.py`: edge-name assertions.
- `tests/test_document_pair_pipeline.py`: assertion-side field names; fixture input unchanged.
- `tests/test_bilingual_enrichment.py`: 21 assertions on expected values.
- `tests/test_bilingual_compliance.py:136`: merged title assertion.
- `tests/test_smol_extractor_lang_neutral.py`: INVERT existing assertions — `title_en` / `title_sl` must NOT be written for any pair (including EN/SL).

---

## §8 — Validator enforcement (`scripts/validate_kg.py`)

### 8.1 New constants

```python
LEGACY_NODE_FIELDS = {"title_en", "title_sl", "slovenian_edition"}
LEGACY_EDGE_RELATIONS = {"sl_published_by"}
```

### 8.2 New checks inside `source_text` branch (around line 109)

```python
elif t == "source_text":
    # ... existing checks ...
    for bad_field in LEGACY_NODE_FIELDS:
        if n.get(bad_field) is not None:
            v[f"legacy_{bad_field}"].append(nid)
```

### 8.3 New check in the edge loop

```python
    if e.get("relation") in LEGACY_EDGE_RELATIONS:
        v["legacy_sl_published_by_edge"].append(
            f"{e['source']} -[{e.get('relation')}]-> {e['target']}"
        )
```

### 8.4 Extend `HARD` set

```python
HARD = {
    # ... existing keys ...
    "legacy_title_en",
    "legacy_title_sl",
    "legacy_slovenian_edition",
    "legacy_sl_published_by_edge",
}
```

---

## §9 — Test plan

### 9.1 `tests/test_neutral_ontology_migration.py` (NEW)

| Test | Asserts |
|---|---|
| `test_edge_rename` | `sl_published_by` → `translation_published_by` under `--apply` |
| `test_node_translator_edge` | `translated_by` node: SL → translation, EN → orig |
| `test_node_author_edge` | `written_by` only: SL → orig, EN → translation |
| `test_node_no_edge_both_fields` | SL-first default: SL → orig, EN → translation |
| `test_node_no_edge_sl_only` | Only SL: `title_orig`, `orig_lang="sl"` |
| `test_node_no_edge_en_only` | Only EN: `title_orig`, `orig_lang="en"` |
| `test_slovenian_edition_rename` | sub-dict → `translation_edition` with `language="sl"` |
| `test_legacy_fields_stripped` | After `--apply`, no legacy fields anywhere |
| `test_idempotency` | Second run is no-op |
| `test_partial_migration_idempotency` | Both neutral + legacy → legacy stripped only |
| `test_dry_run_no_writes` | Counts reported; KG file unchanged |
| `test_review_queue_reshape` | `extraction_review.json` payloads reshaped under `--apply` |

### 9.2 `tests/test_ingest_personal_bibliography_neutral.py` (NEW)

| Test | Asserts |
|---|---|
| `test_author_entry_kwargs` | `belina_role="author"` → `title_orig`+`orig_lang="sl"`; +`title_translation`+`translation_lang="en"` when EN side present |
| `test_translator_entry_kwargs` | `belina_role="translator"` → `title_translation`+`translation_lang="sl"`; +`title_orig`+`orig_lang="en"` when EN side present |
| `test_editor_entry_kwargs` | Same as author |
| `test_no_legacy_field_in_kwargs` | No `add_source_text_node` call passes `title_en` or `title_sl` |
| `test_provenance_cobiss_personal` | Every call passes `provenance="cobiss_personal"` |
| `test_bilingual_publisher_split` | `"SL: = EN"` → `published_by` + `translation_published_by` |
| `test_single_publisher_no_split` | No `: =` → single `published_by`, no `translation_published_by` |

### 9.3 `tests/test_ingest_extra_containers.py` (NEW)

| Test | Asserts |
|---|---|
| `test_basic_ingest` | Record → node with neutral fields + `provenance="curator_extra"` |
| `test_translated_by_edge` | `translator_agent_id` → `translated_by` edge |
| `test_idempotency` | Second run skips existing |
| `test_empty_file` | Empty list → exit 0, KG unchanged |
| `test_published_by_edge` | `publisher` field → `published_by` |

### 9.4 Extend `tests/test_smol_extractor_lang_neutral.py`

| Test | Asserts |
|---|---|
| `test_cited_work_no_legacy_keys_en_sl` | `_build_cited_work` output: no `title_en`, no `title_sl` for EN/SL pair |
| `test_artwork_no_legacy_keys_en_sl` | Same for `_build_artwork` |
| `test_performance_no_legacy_keys_en_sl` | Same for `_build_performance` |
| `test_cited_work_no_slovenian_edition_key` | No `slovenian_edition` key in payload; `translation_edition` when sub-dict data present |
| `test_translation_edition_has_language_field` | `translation_edition.language == translation_lang` when present |

Existing assertions that legacy fields ARE written must be INVERTED.

### 9.5 `tests/test_validate_kg_neutral.py` (NEW)

| Test | Asserts |
|---|---|
| `test_title_en_is_hard_violation` | Node with `title_en` → `legacy_title_en` in HARD violations |
| `test_title_sl_is_hard_violation` | `legacy_title_sl` |
| `test_slovenian_edition_is_hard_violation` | `legacy_slovenian_edition` |
| `test_sl_published_by_edge_is_hard_violation` | `legacy_sl_published_by_edge` |
| `test_neutral_node_passes` | `title_orig` + `title_translation` only → zero violations |
| `test_translation_published_by_edge_passes` | `relation="translation_published_by"` → zero violations |

---

## §10 — Implementation order for TDD green

**Invariant:** readers are updated AFTER migration runs. Updating readers before migration → they read legacy nodes and return blank. Updating writers before migration → they re-pollute on next ingest.

1. Edit `ontology.md` per §1. Commit alone — migration references the new spec.
2. Implement `scripts/migrate_to_neutral_ontology.py` per §5. Run `tests/test_neutral_ontology_migration.py`.
3. Coordinator runs `--dry-run` against KG copy. Verify `edges_renamed ≈ 20`, `nodes_migrated` is meaningful.
4. Coordinator confirms with user. Run `--apply` against the LIVE KG (Step 4.7); back up first.
5. Update `scripts/ingest_personal_bibliography.py` per §2 (kwarg rename + publisher TODO). Run `tests/test_ingest_personal_bibliography_neutral.py`.
6. Implement `scripts/ingest_extra_containers.py` + `data/extra_containers.json` per §3. Run `tests/test_ingest_extra_containers.py`.
7. Update `translate_core/entity_extraction/smol_extractor.py` per §4 (delete 3 SUNSET blocks; builder `slovenian_edition`→`translation_edition`; place new SUNSET tag). Run extended smol tests.
8. Rename `link_sl_published_by` → `link_translation_published_by` in `translate_core/knowledge_graph.py` per §6. Update all callers in same commit.
9. Update all readers per §7. Order: `kg_ingest_entities.py` first (it is also a writer), then `bilingual_enrichment_batch.py`, `book_extractor.py`, `kg_editor_ui.py`, then remaining scripts + test files. NO backward-compat fallbacks.
10. Update `scripts/validate_kg.py` per §8. Run `tests/test_validate_kg_neutral.py`.
11. Run `pytest tests/`. Green.
12. Run `python scripts/validate_kg.py` against migrated KG. Zero hard violations. Confirm `translation_published_by` edges present; zero `sl_published_by`; zero legacy node fields.

---

## §11 — Risks and open questions

**R1: Non-Belina `translated_by` direction.** The migration assigns `title_sl`→`title_translation` for all `translated_by` nodes (SL is the translation target). For Belina's corpus this is correct. If non-Belina translators working into non-SL languages exist in the KG, the rule misclassifies. Mitigation: inspect `--dry-run` output; the SL-first structural rule remains correct for the corpus as a whole (Slovenian-anchored). No text detection.

**R2: `translation_lang=None` when `slovenian_edition` is present.** The builder must guard: only produce `translation_edition` when `translation_lang is not None`.

**R3: JSON review queues.** If `data/extraction_review.json` and `data/kg_review.json` are not reshaped by the migration's Pass 4, queue records render blank titles after readers update. Mitigation: dry-run reports `queue_records_reshaped`; zero on a non-empty queue prints a warning.

**R4: `extra_containers.json` schema extensibility.** Use an explicit allowlist of recognised optional fields; unrecognised fields are ignored.

**R5: Provenance not backfilled.** The migration renames fields but does not stamp `provenance="cobiss_personal"` on existing nodes. Phase 5 routing reads `record["source"]["provenance"]` from the extraction record, not the KG node — acceptable for new ingest. Existing nodes remain provenance-less; documented as known limitation.

**R6: `tests/test_smol_extractor_lang_neutral.py` assertion inversion.** Phase 1B's `_build_cited_work` test for EN/SL pair asserts legacy keys ARE present. Phase 4 deletes the alias blocks; the assertion fails. TDD-red marks expected failure; TDD-green inverts to assert NOT written. Update the test file's docstring.

---

**Files for implementation:**

- `/Users/bel/CascadeProjects/sl_translator/ontology.md` — §1
- `/Users/bel/CascadeProjects/sl_translator/scripts/ingest_personal_bibliography.py` — §2 (lines 171–215, 254–271)
- `/Users/bel/CascadeProjects/sl_translator/scripts/migrate_to_neutral_ontology.py` — NEW (§5)
- `/Users/bel/CascadeProjects/sl_translator/scripts/ingest_extra_containers.py` — NEW (§3)
- `/Users/bel/CascadeProjects/sl_translator/data/extra_containers.json` — NEW (§3)
- `/Users/bel/CascadeProjects/sl_translator/translate_core/entity_extraction/smol_extractor.py` — §4
- `/Users/bel/CascadeProjects/sl_translator/translate_core/knowledge_graph.py` — §6
- `/Users/bel/CascadeProjects/sl_translator/scripts/validate_kg.py` — §8
- `/Users/bel/CascadeProjects/sl_translator/kg_editor_ui.py` — §7.1
- `/Users/bel/CascadeProjects/sl_translator/translate_core/kg_ingest_entities.py` — §7.2
- `/Users/bel/CascadeProjects/sl_translator/translate_core/entity_extraction/bilingual_enrichment_batch.py` — §7.3
- `/Users/bel/CascadeProjects/sl_translator/translate_core/entity_extraction/book_extractor.py` — §7.4
- `/Users/bel/CascadeProjects/sl_translator/tests/test_neutral_ontology_migration.py` — NEW (§9.1)
- `/Users/bel/CascadeProjects/sl_translator/tests/test_ingest_personal_bibliography_neutral.py` — NEW (§9.2)
- `/Users/bel/CascadeProjects/sl_translator/tests/test_ingest_extra_containers.py` — NEW (§9.3)
- `/Users/bel/CascadeProjects/sl_translator/tests/test_smol_extractor_lang_neutral.py` — §9.4
- `/Users/bel/CascadeProjects/sl_translator/tests/test_validate_kg_neutral.py` — NEW (§9.5)
