# Parsing & KG Ingestion Simplification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The coordinating Claude (referred to below as "the coordinator") MUST verify each phase's gate before dispatching the next phase.

**Goal:** Simplify the entity-recognition / KG-ingestion pipeline by deleting VL-era debris, fixing the structurally-wrong TMX ordering at the root, replacing naive-index container detection with chronological-anchor logic, draining the 8,455 noise-concept nodes, and wiring curator lineage data — all while leaving the NiceGUI translation editor and its doc-prep untouched.

**Architecture:** Trunk the smol/DeepSeek-flash extraction path (`run_entity_extraction.py` → `data/smol_entities_map/smol_extractions.json` → `translate_core/kg_ingest_entities.write_to_kg`). Fix TMX chronological ordering in `translate_core/tm.py` so every downstream index agrees. Promote curator attribution files from `data/quarantine/` to canonical input locations. Make container vs. cited-work routing a single chokepoint inside `kg_ingest_entities.py` keyed on `record["source"]["provenance"]`. Delete every parser that bypasses `KnowledgeGraph` factory methods.

**Tech Stack:** Python 3.x, NetworkX (KG storage at `data/knowledge.db`, JSON-serialized), `translate-toolkit` (`tmxfile`), `lxml` (timecode reader), `pytest`, `rapidfuzz`.

**Authoritative constraints — every phase MUST respect these:**

1. The normative ontology is `/Users/bel/CascadeProjects/sl_translator/ontology.md`. Six node types, thirteen edge relations, no exceptions. Any code that contradicts it is wrong.
2. The only authorised KG writers are `KnowledgeGraph` factory methods in `translate_core/knowledge_graph.py`. No agent may use `kg.G.add_node` / `kg.G.add_edge` directly.
3. **OUT OF SCOPE — DO NOT TOUCH:** the NiceGUI editor surfaces (`main.py`, `ui/*.py`, `kg_editor_ui.py`, `app_state.py`, `config.py`, `translate_core/llm.py`, `translate_core/glossary.py`, `translate_core/qa.py`, `visualise_kg.py`, `import_book.py`) and the TM search APIs (`TranslationMemory.lookup_fuzzy`, `search_concordance`, `search_prefix`). Only the TMX **loader** (`_load_tmx` / `_load_all`) is in scope.
4. **Verification is real-data, not stub assertions.** Every phase ends with loading `data/knowledge.db` (or a backup snapshot) and counting node/edge totals against expected ranges. Unit-test pass alone is not acceptance.
5. **Backup before every destructive change.** Each phase that mutates the KG snapshots `data/knowledge.db` to `data/knowledge.db.phase{N}.bak`.
6. **No fast smoke tests.** No "imports resolved → done" or "tests pass → done". Every phase has a behavioural verification.
7. **KG nodes and edges use language-neutral field and relation names. Language codes are VALUES, never part of field names.** The KG ontology MUST NOT carry SL/EN-specific or any other language-pair-specific fields or edge names. Phase 4 cleans this up across the ontology AND all writers AND all existing data. After Phase 4:

   - **Source_text bilingual fields:** `title_orig` + `title_translation` + `orig_lang` + `translation_lang`. ISO 639-1 codes are values in `orig_lang` / `translation_lang`. No `title_en`, no `title_sl`, no `slovenian_edition` field anywhere on the KG side.
   - **Translation-edition info** (the data that used to live in `slovenian_edition`) lives in a neutral `translation_edition: {publisher, city, year, translator, language}` sub-dict. `language` is an ISO 639-1 code value.
   - **Translation publisher edge:** `translation_published_by` (renamed from `sl_published_by`). The relation describes the role (publisher of the translation), not the language.
   - **Source-side code** that READS data with known language (e.g. `scripts/ingest_personal_bibliography.py` reading the COBISS export, which is structurally Slovenian-anchored) MAY use language-code constants like `"sl"` as VALUES being written into the neutral fields. This is not a constraint-9 violation because the value enters as data, not as flow control on the KG layer.

   No flat `publisher_en` / `publisher_sl` ad-hoc fields. No new SL/EN-named edges. Constraint applies retroactively: Phase 4 migrates existing nodes/edges in the live KG to the neutral shape without data loss.
8. Coordinator: each subagent dispatch MUST include (a) the section of the audit relevant to its task (`docs/parsing_simplification_audit.md`), (b) the ontology, (c) the explicit OUT-OF-SCOPE list, (d) the verification gate it must satisfy before reporting done, (e) the bibliography-bright-line and language-neutrality rules below.

9. **Language neutrality — no hardcoded language pair.** The translator works across multiple language pairs (SL↔EN, HR↔SL, and more pairs may be added). TMs from all directions live in `data/tm/*.tmx` interleaved. Code MUST NOT:
   - Branch on hardcoded `"sl"` / `"en"` string literals as if they were the only languages.
   - Default `orig_lang="sl"` or any other specific language as a silent fallback.
   - Name classes / methods / modules in ways that bake in a language pair (no `SlToEnTitle`, no `def normalize_sl_title`, no `class EnglishMatcher`).
   - The ontology fields `orig_lang` / `translation_lang` are language-CODE data — they hold whatever languages are actually in the source. Detection must come from EVIDENCE (explicit translation markers in COBISS text, `xml:lang` on TMX `<tuv>` elements, classifier output that reads such evidence). When evidence is absent, route the record to the review queue with reason `direction_undetermined` — never guess.

10. **Bibliography bright line — personal/COBISS vs. book bibliography.** Per ontology §2.4.1 + audit §5, two completely different bibliographies feed the KG and **must not be conflated** in any subagent prompt or any code:

    | Source | What it lists | KG records produced | Edge wired |
    |---|---|---|---|
    | **Personal/COBISS bibliography** (`data/personal bibliography/bibliography_belina.txt`, ingested by `scripts/ingest_personal_bibliography.py`) | Every work the translator authored OR translated | (a) Container `source_text` nodes with `project_type ∈ {book_translation, article_translation, festival_programme, exhibition_catalogue}` for works he translated; (b) Self-authored `source_text` nodes with `project_type ∈ {book, magazine_article, ...}` for works he authored (NOT translated). | `translated_by` for (a); `written_by` for (b) |
    | **Book bibliography** (the end-bibliography or footnotes INSIDE a specific translated work, ingested historically by `ingest_book_bibliography.py` / `ingest_book_footnotes.py` and going forward by the smol pipeline + `document_pair_pipeline.py`) | Every work CITED IN a specific translated work | Cited `source_text` nodes with typed `project_type ∈ {book, journal_article, book_chapter, magazine_article, ...}` | `cited_in` pointing to the container that cited them |

    Subagent prompts must reference the correct bibliography by full name. NEVER write "the bibliography" without qualification. The legacy `ingest_book_*` scripts (book bibliography) are deleted in Phase 11; the COBISS ingester (personal) stays as the authoritative container source.

**Reference documents the coordinator and every subagent must consult:**

- `/Users/bel/CascadeProjects/sl_translator/ontology.md` (normative ontology)
- `/Users/bel/CascadeProjects/sl_translator/docs/parsing_simplification_audit.md` (findings)
- `/Users/bel/CascadeProjects/sl_translator/sl_translator_constraints.md` (user constraints — referenced from audit §10.2)

**Execution mode:** subagent-driven. One subagent per task (or small group of related steps within one phase). The coordinator (this Claude) reviews each subagent's output, runs the verification gate personally, and only then dispatches the next.

**Phase order with dependencies:**

```
Phase 0 (pre-flight, isolation, snapshots)
   ↓
Phase 1 (TMX chronological ordering) ─── foundational
   ↓
Phase 2 (extract editor-safe symbols from vl_parser.py)  ── prereq for Phase 7
   ↓
Phase 3 (confidence-bump fix)                            ── prereq for Phase 5
   ↓
Phase 1B (language-neutrality remediation)               ── corrective; prereq for Phase 4+
   ↓
Phase 4 (COBISS bilingual title encoding)
   ↓
Phase 5 (container/cited-work routing chokepoint)
   ↓
Phase 6 (chronological-anchor container attribution)     ── depends on Phase 1 + 1B
   ↓
Phase 7 (DELETE VL-era files)                            ── depends on Phase 2
   ↓
Phase 8 (DELETE seed_kg.py + seed_from_tm + ontology §6 factories)
   ↓
Phase 9 (drain noise concepts from live KG)              ── depends on Phase 8
   ↓
Phase 10 (wire curator lineage data)                     ── depends on Phase 9
   ↓
Phase 11 (DELETE ingest_book_* + bilingual_tm_matcher + book_extractor)  ── depends on Phase 5/6
   ↓
Phase 12 (end-to-end run + KG diff verification)
```

---

## Phase 0 — Pre-flight, isolation, snapshots

**Purpose:** establish the workspace, snapshot the live KG before any change, and confirm the audit's claimed counts.

**Files:**
- Create: `data/knowledge.db.preflight.bak` (binary copy of live KG)
- Read-only inspection of: `data/knowledge.db`, `data/smol_entities_map/smol_extractions.json`

### Tasks

- [ ] **Step 0.1: Coordinator decides isolation strategy.**

The repo currently has 50+ uncommitted modifications on `main`. The coordinator asks the user: "Commit current uncommitted work first (recommended), stash it, or proceed on `main`?" If isolation via `git worktree` is chosen, create one rooted at `main` HEAD; the user's uncommitted work stays in the original worktree.

- [ ] **Step 0.2: Snapshot the live KG.**

```bash
cp /Users/bel/CascadeProjects/sl_translator/data/knowledge.db \
   /Users/bel/CascadeProjects/sl_translator/data/knowledge.db.preflight.bak
ls -la /Users/bel/CascadeProjects/sl_translator/data/knowledge.db*
```
Expected: both files present and the same size.

- [ ] **Step 0.3: Verify audit counts on the live KG.**

```bash
.venv/bin/python3 -c "
from translate_core.knowledge_graph import KnowledgeGraph
kg = KnowledgeGraph()
from collections import Counter
node_types = Counter(d.get('type') for _, d in kg.G.nodes(data=True))
edge_rels  = Counter(d.get('relation') for _, _, d in kg.G.edges(data=True))
print('NODES:', dict(node_types))
print('EDGES:', dict(edge_rels))
concepts_with_def = sum(1 for _, d in kg.G.nodes(data=True) if d.get('type')=='concept' and (d.get('definition') or '').strip())
lineage_rels = {'extends','critiques','redefines','reappropriates','related_to'}
lineage_edges = sum(1 for _,_,d in kg.G.edges(data=True) if d.get('relation') in lineage_rels)
print('concepts_with_definition:', concepts_with_def)
print('lineage_edges:', lineage_edges)
"
```
Expected (from audit §1): `concept≈8455`, `agent≈5415`, `source_text≈3625`, `institution≈3931`, `term≈16798`, `translation_mapping≈54152`, `concepts_with_definition=0`, `lineage_edges=0`. Coordinator records actual numbers in `docs/superpowers/plans/2026-06-06-phase-log.md` for diffing later.

- [ ] **Step 0.4: Inventory the modules to be modified vs. preserved.**

```bash
grep -rn "from translate_core.vl_parser\|from translate_core import vl_parser\|split_paragraphs\|BookOutline" \
  /Users/bel/CascadeProjects/sl_translator --include="*.py" | grep -v __pycache__
```
Expected: confirm that `import_book.py:112` imports `split_paragraphs` and `:147` imports `BookOutline`. These are editor-doc-prep callers and dictate Phase 2.

- [ ] **Step 0.5: Commit the audit and plan documents.**

```bash
# docs/ is in .gitignore — force-add the audit trail so it persists in git history.
git add -f docs/parsing_simplification_audit.md docs/superpowers/plans/2026-06-06-parsing-simplification.md
git commit -m "docs: audit + simplification plan"
```

### Phase 0 verification gate

- [ ] `data/knowledge.db.preflight.bak` exists and matches `data/knowledge.db` byte-for-byte.
- [ ] Live KG counts recorded in phase-log file.
- [ ] `import_book.py` confirmed as the only editor-doc-prep consumer of `vl_parser` symbols.

---

## Phase 1 — TMX chronological ordering (foundational)

**Purpose:** make `translate_core/tm.py` expose TMX `creationdate` and a chronological view of segments alongside the existing natural-load-order entries. This is the root fix; everything downstream depends on it. Naive `enumerate()` indexing is the documented cause of container-boundary mis-assignment (audit §4, §10.1).

**Design constraint — preserve `self.entries` natural order.** The editor surface (`main.py:246-251`) appends to `tm.entries` after curator edits; an `inline_test` fixture also reads `tm.entries[0]` positionally. To avoid any risk to the editor, **do NOT mutate `self.entries` order**. Instead:
  - Attach `t_index` (int, position in chronological order), `raw_index` (int, position in load order), and `creationdate` (str or `None`) to each entry dict.
  - Expose a `TranslationMemory.iter_chronological(origin: str | None = None)` method that yields entries sorted ascending by `creationdate` (None-dated entries appended after dated ones, preserving relative natural order).
  - Downstream consumers (Phase 6 attribution) use `iter_chronological()`; legacy iterators over `self.entries` continue to work unchanged.

**Files:**
- Create: `translate_core/tm_timecodes.py`
- Modify: `translate_core/tm.py` (lines 23–62: `TranslationMemory.__init__` and `_load_tmx`)
- Test: `tests/test_tm_timecodes.py`
- DO NOT MODIFY: `lookup_fuzzy`, `search_concordance`, `search_prefix`, or any other read-side method of `TranslationMemory`.

### Tasks

- [ ] **Step 1.1: Coordinator dispatches subagent for TDD red.**

Agent dispatch prompt skeleton (the coordinator fills `<...>` slots):

> Subagent type: `python-development:python-pro`.
> Working directory: `/Users/bel/CascadeProjects/sl_translator`.
> Read first: `ontology.md`, `docs/parsing_simplification_audit.md` §4 and §10.1, the current `translate_core/tm.py:1-100`, and the TMX header of `data/tm/2022-SL-EN.tmx` lines 1–30.
> Task: write `tests/test_tm_timecodes.py` that asserts:
> (a) `read_tmx_with_timecodes(path)` returns a list of segment dicts in **natural load order**, each carrying keys `source, target, origin, raw_index, creationdate, t_index`. `raw_index` matches file order; `t_index` is the position the entry would have if sorted by `creationdate` ascending.
> (b) Given a TMX with three `<tu>` blocks whose `creationdate` values are out of natural order (e.g. natural=[A,B,C] but dates=[2024, 2022, 2023]), `t_index` correctly assigns B=0, C=1, A=2 while `raw_index` stays at A=0, B=1, C=2 and the **list itself is in load order**.
> (c) When `creationdate` is missing on a `<tu>`, the segment is kept at its natural position in the returned list with `creationdate=None`, and its `t_index` is assigned after all dated entries (preserving relative natural order among the dateless).
> (d) `TranslationMemory.iter_chronological()` yields entries sorted by `t_index`. `iter_chronological(origin="foo.tmx")` filters by origin and yields chronological order within that origin.
> (e) `tm.entries` itself remains in natural load order — i.e. `[e['raw_index'] for e in tm.entries]` is `list(range(len(tm.entries)))`.
> Provide the test file as a fixture: write a small synthetic TMX into `tests/fixtures/tmx_unordered.tmx` and use it.
> DO NOT implement `read_tmx_with_timecodes` or `iter_chronological` yet. Verify the test FAILS with `ImportError` or `AttributeError`.
> Constraints: no edits to any file under `ui/`, `main.py`, `kg_editor_ui.py`, `import_book.py`, `app_state.py`, `config.py`, `translate_core/llm.py`, `translate_core/glossary.py`, `translate_core/qa.py`, `visualise_kg.py`. No edits to `lookup_fuzzy`/`search_concordance`/`search_prefix`.
> Report: paste the test code and the failing-test command output.

- [ ] **Step 1.2: Coordinator verifies test fails for the right reason.**

```bash
.venv/bin/python3 -m pytest tests/test_tm_timecodes.py -v 2>&1 | tail -30
```
Expected: failures cite the missing `read_tmx_with_timecodes` symbol — not a fixture loading error or unrelated noise.

- [ ] **Step 1.3: Coordinator dispatches subagent for TDD green.**

Agent dispatch prompt:

> Subagent type: `python-development:python-pro`.
> Read first: the failing test, `ontology.md` §4 invariant 7, `docs/parsing_simplification_audit.md` §4, `translate_core/tm.py:1-100`.
> Implement `translate_core/tm_timecodes.py` with a function `read_tmx_with_timecodes(path: Path) -> list[dict]`. Use `lxml.etree` to parse the TMX XML directly (NOT `translate.storage.tmx.tmxfile`, which discards attributes). Read `creationdate` from each `<tu>` element. Return dicts in **natural file order** with keys `{source, target, origin, raw_index, creationdate, t_index}`, where `t_index` is the position the entry would have under chronological sort (dateless entries assigned t_indexes after all dated ones, preserving relative natural order). Preserve the existing `clean_xml` normalization and the EN-source/SL-target swap behaviour from `tm.py:39-52`.
> Then modify `translate_core/tm.py:_load_tmx` to delegate to `read_tmx_with_timecodes` and persist `t_index`, `raw_index`, `creationdate` on every entry dict in `self.entries` (still in natural order — DO NOT sort `self.entries`).
> Add `TranslationMemory.iter_chronological(origin: str | None = None) -> Iterator[dict]` that yields entries sorted by `t_index`, optionally filtered by origin.
> DO NOT modify `lookup_fuzzy`, `search_concordance`, `search_prefix`. Run them against a sample input after the change and confirm output is unchanged.
> Verify: the failing test now passes; the full existing test suite still passes (`.venv/bin/python3 -m pytest tests/ -x -q`).
> Report: paste the new module, the modified `_load_tmx`, and both pytest results.

- [ ] **Step 1.4: Coordinator runs real-data verification.**

```bash
.venv/bin/python3 -c "
from translate_core.tm import TranslationMemory
tm = TranslationMemory()
# Prove (a) self.entries stayed in natural order and (b) chronological differs.
assert [e['raw_index'] for e in tm.entries] == list(range(len(tm.entries))), \
    'self.entries was reordered; the editor surface may break'
by_origin = {}
for e in tm.entries:
    by_origin.setdefault(e['origin'], []).append(e)
diffs = 0
for origin, lst in by_origin.items():
    dated = [e for e in lst if e.get('creationdate')]
    if len(dated) < 2: continue
    chrono_via_t = sorted(dated, key=lambda x: x['t_index'])
    natural = sorted(dated, key=lambda x: x['raw_index'])
    if [e['raw_index'] for e in chrono_via_t] != [e['raw_index'] for e in natural]:
        diffs += 1
        print(f'{origin}: natural != chronological')
assert diffs > 0, 'no origin shows natural != chronological; timecode reader likely broken'
print(f'OK: {diffs} origins where chronological differs from natural')

# Also verify iter_chronological works and is order-correct.
chrono = list(tm.iter_chronological())
t_indexes = [e['t_index'] for e in chrono]
assert t_indexes == sorted(t_indexes), 'iter_chronological did not return t_index-sorted order'
print(f'iter_chronological yields {len(chrono)} entries in t_index order')
"
```
Expected: assertions pass; at least one origin shows natural != chronological; `iter_chronological` yields a t_index-sorted stream. If `self.entries` was reordered, STOP — the editor surface (`main.py:246-251`) reads `tm.entries` and any positional access elsewhere would break.

- [ ] **Step 1.5: Coordinator confirms editor side did not regress.**

```bash
.venv/bin/python3 -c "
from translate_core.tm import TranslationMemory
tm = TranslationMemory()
hits = tm.lookup_fuzzy('art', threshold=70.0, limit=3)
print('lookup_fuzzy hits:', len(hits))
assert hits, 'lookup_fuzzy returned empty — search APIs are broken'
"
```
Expected: non-empty hit list.

- [ ] **Step 1.6: Commit Phase 1.**

```bash
git add translate_core/tm_timecodes.py translate_core/tm.py tests/test_tm_timecodes.py tests/fixtures/tmx_unordered.tmx
git commit -m "tm: read TMX with creationdate-sorted t_index alongside raw_index"
```

### Phase 1 verification gate

- [ ] `tests/test_tm_timecodes.py` passes.
- [ ] Existing test suite still passes (no regression in editor-side TM users).
- [ ] Real-data check: at least one TMX origin has natural-order != chronological-order.
- [ ] `lookup_fuzzy` returns hits.

---

## Phase 2 — Extract editor-safe symbols from `vl_parser.py`

**Purpose:** `import_book.py` (editor doc prep, OUT OF SCOPE) imports `split_paragraphs` and `BookOutline` from `translate_core/vl_parser.py`. Before we can delete the rest of `vl_parser.py` in Phase 7, those two symbols must be moved to a small in-scope module so the editor keeps working.

**Files:**
- Create: `translate_core/book_outline.py` (houses `BookOutline` dataclass + `split_paragraphs` function)
- Modify: `import_book.py:112,147` to import from the new module
- Test: `tests/test_book_outline.py` (smoke-level — these symbols are simple)
- Do NOT touch the rest of `vl_parser.py` yet.

### Tasks

- [ ] **Step 2.1: Coordinator dispatches subagent.**

> Subagent type: `python-development:python-pro`.
> Read first: `translate_core/vl_parser.py:137-200` (`BookOutline` dataclass), `translate_core/vl_parser.py:554-620` (`split_paragraphs`), `import_book.py:100-160`.
> Task: create `translate_core/book_outline.py` containing a verbatim copy of the `BookOutline` dataclass and the `split_paragraphs` function (no other vl_parser internals). Update `import_book.py` lines 112 and 147 to import from the new module instead.
> Then write `tests/test_book_outline.py` with two smoke tests: (a) `split_paragraphs("a"*4000)` returns multiple chunks each ≤ `max_chars`; (b) `BookOutline()` instantiates with the expected default fields.
> Verify the existing `vl_parser.py` file remains UNCHANGED (we still need its other definitions of `BookOutline`/`split_paragraphs` working until Phase 7 deletes the file; the imports above just point elsewhere).
> Confirm `import_book.py` still imports without error: `.venv/bin/python3 -c "import import_book"` returns 0.
> Report: new module, modified imports in `import_book.py`, pytest output for new tests.

- [ ] **Step 2.2: Coordinator real-data check.**

```bash
.venv/bin/python3 -c "import import_book; print('OK')"
.venv/bin/python3 -c "from translate_core.book_outline import BookOutline, split_paragraphs; print('OK', len(split_paragraphs('x'*3000)))"
.venv/bin/python3 -m pytest tests/test_book_outline.py -v
```

- [ ] **Step 2.3: Commit Phase 2.**

```bash
git add translate_core/book_outline.py import_book.py tests/test_book_outline.py
git commit -m "book_outline: extract BookOutline + split_paragraphs from vl_parser"
```

### Phase 2 verification gate

- [ ] `import_book.py` imports without error.
- [ ] New module imports both symbols.
- [ ] `vl_parser.py` itself unchanged (verified via `git diff translate_core/vl_parser.py` showing no changes).

---

## Phase 3 — Confidence-bump fix (review-queue bypass)

**Purpose:** the universal `+0.60` for `smol_verified_classification` (`confidence.py:73`) floors every smol record at ≥0.85 and bypasses the review queue (ontology §4 invariant 9). Replace with a 2-of-3 composite gate.

**Files:**
- Modify: `translate_core/entity_extraction/confidence.py:67-78`
- Test: `tests/test_confidence.py` (new)
- DO NOT MODIFY: the per-record-kind bumps below line 80; those are correct.

### Tasks

- [ ] **Step 3.1: Coordinator dispatches subagent for TDD red.**

> Subagent type: `python-development:python-pro`.
> Read first: `docs/parsing_simplification_audit.md` §3.4 and §10.10, `ontology.md` §4 invariant 9, `translate_core/entity_extraction/confidence.py:1-150`, `translate_core/entity_extraction/smol_extractor.py:380-460` (sees what `signals` smol sets on agent and institution records).
> Task: write `tests/test_confidence.py` covering these cases against `score_record`:
> (a) A smol record with `smol_verified_classification=True` and `smol_extracted=True` ONLY (no bilingual title, no container attached, no typed project) MUST score below the DIRECT_WRITE threshold so it routes to review.
> (b) A smol record with all three composite signals — `title_bilingual=True`, `container_attached=True`, `project_type_typed=True` — gets +0.30 on top of `smol_extracted`+`verified_from_text`, landing it ≥ DIRECT_WRITE.
> (c) Exactly two of three signals: still routes to review.
> (d) Curator endorsement (`curator_endorsed=True`) is its own +0.40 and bypasses the composite requirement.
> (e) The current per-record-kind bumps (`translated_work`, `cited_work`, `agent`, `institution`, `concept`) are unchanged.
> Read the existing DIRECT_WRITE threshold from the code (do NOT guess; cite the line). If the threshold is not a named constant, ALSO surface that as a finding.
> Run the tests; they must fail against the current implementation (currently every smol record passes (a)).
> Report: test code + pytest output showing failures.

- [ ] **Step 3.2: Coordinator dispatches subagent for TDD green.**

> Subagent type: `python-development:python-pro`.
> Read first: the failing tests; current `confidence.py`.
> Task: rewrite `confidence.py:67-78` so the three universal bumps become:
> - `smol_extracted` → unchanged at `+0.15` (still useful as a base signal).
> - `smol_verified_classification` → REMOVED as a universal bump.
> - `verified_from_text` → unchanged at `+0.10`.
> Add a new composite gate: count truthy signals among `{title_bilingual, container_attached, project_type_typed}`; if ≥2 of 3, add `+0.30`. If `curator_endorsed`, add `+0.40` (separate path).
> Smol record builders in `smol_extractor.py` must set `container_attached=True` only when `container_work_id` is present and non-empty, and `project_type_typed=True` only when `project_type` is one of the typed values (NOT `cited_work`).
> Update `smol_extractor._build_cited_work`, `_build_agent_person`, `_build_institution`, `_build_concept`, `_build_artwork`, `_build_performance` to set these signals correctly per the above rules. Do NOT remove the `smol_verified_classification` signal — other code may read it as an informational flag.
> Verify tests now pass. Run the full existing test suite; expect SOME failures in tests that asserted "smol records direct-write" — surface them and fix only those that were encoding the bypass-the-queue behaviour. If a test is checking real correctness, do NOT mutate it; ask the coordinator.
> Report: code diff, test outputs, list of pre-existing tests now failing and why.

- [ ] **Step 3.3: Coordinator real-data verification.**

After the subagent's edit the coordinator runs a scoring sweep over real smol records. Exact code depends on what helper the subagent exposes; the pattern is:

```bash
.venv/bin/python3 -c "
import json
from pathlib import Path
from translate_core.entity_extraction.confidence import score_record, DIRECT_WRITE_THRESHOLD
data = json.loads(Path('data/smol_entities_map/smol_extractions.json').read_text())
# Pull the first 200 records' entity payloads and re-derive signals via the
# subagent's now-canonical signal helper (subagent must expose one — its
# name goes here after Phase 3.2 reports). If no helper is exposed, the
# coordinator computes signals inline using the rules in confidence.py.
direct = review = 0
for blob in data[:200]:
    for ent in blob.get('entities', []):
        signals = {
            'smol_extracted': True,
            'verified_from_text': bool(ent.get('verified_from_text')),
            'title_bilingual': bool(ent.get('title_orig') and ent.get('title_translation')),
            'container_attached': bool(blob.get('container_work_id')),
            'project_type_typed': ent.get('project_type') not in (None, '', 'cited_work'),
        }
        s = score_record(ent.get('kind') or 'cited_work', signals)
        if s >= DIRECT_WRITE_THRESHOLD: direct += 1
        else: review += 1
print(f'direct={direct} review={review}')
"
```
Expected: `review > 0` (was 0 before the fix). A reasonable split is 30–70% to review depending on smol coverage. **Hard failure if `review / (direct + review) > 0.80`** — that indicates the composite gate is mis-tuned and Phase 5/6 KG writes would starve. Surface to user before proceeding.

- [ ] **Step 3.4: Commit Phase 3.**

```bash
git add translate_core/entity_extraction/confidence.py \
        translate_core/entity_extraction/smol_extractor.py \
        tests/test_confidence.py
git commit -m "confidence: remove blanket +0.60 smol bump; add 2-of-3 composite gate"
```

### Phase 3 verification gate

- [ ] New tests pass.
- [ ] Real-data sweep over 200 smol records shows non-zero review routing.
- [ ] No regression in existing tests other than ones that encoded the bypass behaviour.

---

## Phase 1B — Language-neutrality remediation (corrective)

**Purpose:** the independent language-neutrality audit (`docs/parsing_simplification_lang_neutrality_audit.md`) found that Phase 1 / Phase 3 committed code hardcodes EN/SL in two runtime-critical sites. Phase 1B fixes them WITH a compatibility shim that keeps the editor working unchanged. This phase MUST run before Phase 4 so Phases 4+ inherit truly neutral primitives.

**Findings being remediated (audit §3.1, §3.2, §3.4):**

1. `translate_core/tm_timecodes.py:97-106` pairs `<tuv>` by hardcoded `lang == "en"` / `lang == "sl"`. A non-EN/SL TMX loads as zero entries.
2. `translate_core/tm_timecodes.py:41-42, 130-131` defaults `srclang` to `"en"` and forces every entry to `source_lang="en", target_lang="sl"`.
3. `translate_core/entity_extraction/smol_extractor.py:187-199` (`_detect_source_lang`) returns `(LANG_EN, LANG_SL)` on no filename match.
4. `translate_core/entity_extraction/smol_extractor.py:295-296` builder default args `src_lang=LANG_EN, tgt_lang=LANG_SL`.
5. `translate_core/entity_extraction/confidence.py:129-130` signal `has_sl_edition` carries SL in the name.
6. `tests/fixtures/tmx_unordered.tmx` only covers EN/SL. No HR-SL / DE-SL regression net.

**Compatibility shim — required by editor (audit §6):**
- `tm.entries` MUST remain the current EN→SL-normalised view so `inline_test/test_confirm_pipeline.py:62-68` (literal `source_lang="en", target_lang="sl"`) and `tm.lookup_fuzzy` / `tm.search_prefix` (which search/return only `source` / `target` fields normalised to the EN/SL convention) keep working unchanged for today's corpus.
- A new internal index `_entries_by_pair: dict[tuple[str, str], list[dict]]` keyed by `(source_lang, target_lang)` carries the raw, non-normalised view. Each entry retains its actual `xml:lang` codes.
- `tm.entries` is computed as a filtered view of `_entries_by_pair` containing the EN/SL pair (with SL→EN entries swapped to maintain EN→SL orientation). For today's corpus this view is byte-identical to what Phase 1 produced.
- New consumers (Phase 6 attribution, Phase 4 COBISS direction detection, smol extractor) read `_entries_by_pair` and operate per-pair without the EN/SL assumption.

**Files:**
- Modify: `translate_core/tm_timecodes.py` (pair `<tuv>` by `srclang`/positional evidence, not by literal language labels; carry actual `xml:lang` codes; default missing `srclang` to `None`).
- Modify: `translate_core/tm.py` (add `_entries_by_pair` index; `tm.entries` becomes a filtered EN/SL-normalised view of it; `iter_chronological` reads from `_entries_by_pair`).
- Modify: `translate_core/entity_extraction/smol_extractor.py` (`_detect_source_lang` returns `(None, None)` on no match; builders accept `None` lang values; the three `title_en`/`title_sl` alias write sites at lines 529-530, 727-728, 850-851 are wrapped in a `_LEGACY_SL_EN_DUPLICATE_WRITE` flag with a sunset target of Phase 11; constants `LANG_EN`/`LANG_SL` stay as data values but are NOT used as flow-control defaults).
- Modify: `translate_core/entity_extraction/confidence.py` (rename `has_sl_edition` → `has_target_lang_edition`; update smol-builder sites that set it).
- Create: `tests/fixtures/tmx_hr_sl.tmx` (HR→SL fixture).
- Modify: `tests/test_tm_timecodes.py` (parametrise existing tests over both EN/SL and HR/SL fixtures; add an "unknown pair" case to assert correct routing without default-EN-SL).
- Test: `tests/test_tm_pair_indexing.py` (new — exercises `_entries_by_pair`).
- DO NOT MODIFY: `main.py`, `ui/*.py`, `inline_test/test_confirm_pipeline.py` (the compat shim must keep these passing without changes), the OUT OF SCOPE list.

### Agent mix
- Step 1B.0: `Explore` (survey every reader of `tm.entries`, `e["source_lang"]`, `e["target_lang"]`, `LANG_EN`, `LANG_SL`, `has_sl_edition` to confirm compat-shim sufficiency)
- Step 1B.1: `feature-dev:code-architect` (design the `_entries_by_pair` index + view derivation + smol detector refactor)
- Step 1B.2: `python-development:python-pro` for TDD red (HR-SL fixture, parametrised tests, _entries_by_pair contract)
- Step 1B.3: `python-development:python-pro` for TDD green (the refactor)
- Step 1B.4: `pythonista-reviewer` for the diff
- Skills the coordinator invokes before dispatching: `superpowers:test-driven-development`, `python-development:python-testing-patterns`, `python-development:python-design-patterns` (compat-shim is a Façade / Adapter pattern).

### Tasks

- [ ] **Step 1B.0: Coordinator dispatches `Explore` for the impact survey.**

Brief: "Find every reader in the repo of these symbols (exclude `.venv/`, `__pycache__/`):
  - `tm.entries`, `tm._entries_by_pair` (won't exist yet)
  - `e['source_lang']`, `e['target_lang']` reads on TM entry dicts
  - `LANG_EN`, `LANG_SL` constants and any string literal `'en'` / `'sl'` in `translate_core/entity_extraction/smol_extractor.py`
  - `has_sl_edition` signal reads anywhere in confidence / smol / kg_ingest code
  - `title_en`, `title_sl` writes (not reads) on `source_text` records anywhere
  - `inline_test/test_confirm_pipeline.py` — full file, its assertions on TM entry shape
For each, report file:line and what the consumer expects. Specifically answer: does keeping `tm.entries` as an EN→SL-normalised filtered view keep every current consumer working? What's the inventory of `title_en`/`title_sl` write sites that need a sunset flag?"

- [ ] **Step 1B.1: Coordinator invokes `python-development:python-design-patterns`, then dispatches `feature-dev:code-architect`.**

> Subagent type: `feature-dev:code-architect`.
> Out of scope + constraints 9 and 10 (verbatim).
> Read first: `docs/parsing_simplification_lang_neutrality_audit.md` (full file), `translate_core/tm_timecodes.py`, `translate_core/tm.py`, `translate_core/entity_extraction/smol_extractor.py:170-230, 280-320, 460-570, 700-870`, the Step 1B.0 Explore report (paste in).
> Task: produce a design blueprint (no code) covering:
>   - The new `_entries_by_pair: dict[tuple[str | None, str | None], list[dict]]` index — exact key shape, how entries are bucketed, what `None` keys mean.
>   - How `tm.entries` is derived from `_entries_by_pair` so it remains byte-identical for today's EN/SL corpus.
>   - How `iter_chronological(origin=None)` reads from `_entries_by_pair`.
>   - The new `read_tmx_with_timecodes` behavior: pair `<tuv>` by header `srclang` evidence (positional within the TU) with a fallback to per-`<tuv>` `xml:lang`; populate actual codes on the returned dicts; never default to `"en"`.
>   - The smol `_detect_source_lang` refactor: regex-based ISO-code pair detection from filename; return `(None, None)` on no match.
>   - How smol builders cope with `None` lang values: the `title_en`/`title_sl` write becomes conditional on both langs being EN/SL; otherwise the legacy fields are omitted (canonical fields still written).
>   - The deletion target for the legacy `title_en`/`title_sl` write paths (Phase 11 sunset) — what consumers must migrate first.

- [ ] **Step 1B.2: Coordinator invokes `superpowers:test-driven-development` + `python-development:python-testing-patterns`, then dispatches `python-development:python-pro` for TDD red.**

> Subagent type: `python-development:python-pro`.
> Out of scope + constraints 9 and 10 (verbatim).
> Read first: the architect's blueprint (paste in); the audit's §4 ("Recommended design").
> Task:
> (a) Create `tests/fixtures/tmx_hr_sl.tmx` — three TUs with `<tuv xml:lang="HR">` / `<tuv xml:lang="SL">` and creationdates, header `srclang="HR"`. Valid lxml-parseable.
> (b) Add a parametrised test in `tests/test_tm_timecodes.py` that loads both the EN-SL fixture and the HR-SL fixture and asserts: all 3 entries load from HR-SL with `source_lang="hr"`, `target_lang="sl"`. The current EN-SL test still passes.
> (c) Create `tests/test_tm_pair_indexing.py` covering:
>     - `TranslationMemory(tm_dir=tmp_dir_with_both_fixtures)._entries_by_pair` has keys for both `("en", "sl")` and `("hr", "sl")`, each containing the right number of entries.
>     - `tm.entries` (the EN/SL compat view) contains ONLY the EN/SL entries (with SL→EN swapped to EN→SL) and matches the byte-identical Phase 1 output for an EN-SL-only corpus.
>     - For each entry in `_entries_by_pair`, `e["source_lang"]` and `e["target_lang"]` reflect the actual `xml:lang` codes (not normalised).
> (d) Add a test for `smol_extractor._detect_source_lang`: `_detect_source_lang("big-HR-SL.tmx") == ("hr", "sl")`, `_detect_source_lang("foo.tmx") == (None, None)`, `_detect_source_lang("en-sl.tmx") == ("en", "sl")`.
> (e) Add a test for `smol_extractor._build_cited_work` that confirms: when called with `src_lang="hr"`, `tgt_lang="sl"`, `title_orig="X"`, `title_translation="Y"`, the payload has `title_orig` + `title_translation` + `orig_lang="hr"` + `translation_lang="sl"` and does NOT have `title_en` or `title_sl` keys (the conditional legacy-write should not fire when neither side is EN/SL).
> (f) Add a test asserting `confidence.score_record(..., signals={"has_target_lang_edition": True, ...})` increments correctly (renamed signal name).
> (g) Assert that `inline_test/test_confirm_pipeline.py:62-68` still passes against the new shim (you don't touch the inline test; you just confirm it passes after the refactor — list the run command in the report).
> Verify all tests FAIL against current code (function/file/key doesn't exist yet). Report: test code + failure output.

- [ ] **Step 1B.3: Coordinator dispatches `python-development:python-pro` for TDD green.**

> Subagent type: `python-development:python-pro`.
> Out of scope + constraints 9 and 10 (verbatim).
> Read first: the failing tests; the architect's blueprint; the audit's §4–§6.
> Task: implement per the blueprint.
>   - `tm_timecodes.py`: pair `<tuv>` by header `srclang` evidence (positional within the TU) + per-`<tuv>` `xml:lang` fallback. Populate `source_lang` and `target_lang` from actual codes. Default missing `srclang` to `None`.
>   - `tm.py`: introduce `_entries_by_pair`; `tm.entries` becomes a property derived as the EN/SL filtered + SL→EN-swapped view. Preserve `raw_index`, `creationdate`, `t_index` semantics globally across all pairs. `iter_chronological` reads from `_entries_by_pair`.
>   - `smol_extractor.py`: rewrite `_detect_source_lang` (regex `[a-z]{2}-[a-z]{2}` filename match, return `(None, None)` on miss). Builder default args removed (no more `src_lang=LANG_EN`). The three `title_en`/`title_sl` alias write sites become conditional on `LANG_EN in {orig_lang, translation_lang}` AND `LANG_SL in {orig_lang, translation_lang}`; otherwise the legacy fields are omitted entirely (canonical fields always written). Add a module-level comment naming Phase 11 as the sunset.
>   - `confidence.py`: rename `has_sl_edition` → `has_target_lang_edition`. Update every smol-builder site that sets it.
> Verify: all new tests pass; existing tests pass (including the inline_test fixture); full suite green.
> Report: full per-file diff; test outputs; the exact view-derivation logic used for `tm.entries`.

- [ ] **Step 1B.4: Coordinator dispatches `pythonista-reviewer` for the diff.**

Brief: "Review the Phase 1B diff (`tm_timecodes.py`, `tm.py`, `smol_extractor.py`, `confidence.py`, new tests, new fixture). Confirm: no string literal `'en'` or `'sl'` appears in a flow-control conditional anywhere in the changed code (literals are OK as data values inside maps, e.g. `LANG_EN = 'en'` itself); `tm.entries` is byte-identical for today's EN/SL corpus (run the comparison if possible); `_entries_by_pair` correctly carries actual codes; the legacy `title_en`/`title_sl` write sites are gated and tagged with a sunset; `inline_test/test_confirm_pipeline.py:62-68` literal assertions still pass without modification. Report only high-confidence findings."

- [ ] **Step 1B.5: Coordinator real-data verification.**

```bash
.venv/bin/python3 -c "
from translate_core.tm import TranslationMemory
tm = TranslationMemory()
# Byte-identical entries view for today's EN/SL corpus
assert all(e['source_lang'] == 'en' and e['target_lang'] == 'sl' for e in tm.entries), \
    'tm.entries no longer EN→SL — compat shim broken'
# _entries_by_pair carries real codes
pairs = set(tm._entries_by_pair.keys())
print('pairs:', pairs)
total = sum(len(v) for v in tm._entries_by_pair.values())
assert total == len(tm.entries), f'pair index total {total} != entries view {len(tm.entries)}'
print(f'OK: total_entries={len(tm.entries)} pairs_seen={pairs}')
"

# Also confirm the inline test still passes
.venv/bin/python3 -m pytest inline_test/test_confirm_pipeline.py -v 2>&1 | tail -10
```

- [ ] **Step 1B.6: Commit Phase 1B.**

```bash
git add translate_core/tm.py \
        translate_core/tm_timecodes.py \
        translate_core/entity_extraction/smol_extractor.py \
        translate_core/entity_extraction/confidence.py \
        tests/test_tm_timecodes.py \
        tests/test_tm_pair_indexing.py \
        tests/fixtures/tmx_hr_sl.tmx
git commit -m "phase1b: language-neutral TM loader + smol detector; EN/SL compat shim"
```

### Phase 1B verification gate

- [ ] HR-SL fixture loads with `source_lang="hr"`, `target_lang="sl"`.
- [ ] `_entries_by_pair` carries actual codes per origin.
- [ ] `tm.entries` byte-identical to Phase 1 output for today's EN/SL corpus (compat shim preserved).
- [ ] `inline_test/test_confirm_pipeline.py` passes unmodified.
- [ ] `editor's lookup_fuzzy / search_prefix` return non-empty hits.
- [ ] smol's `_detect_source_lang` returns `(None, None)` for non-matching filenames.
- [ ] Reviewer pass clean: no `'en'` / `'sl'` literals in flow-control conditionals in changed code.

---

## Phase 4 — Language-neutral KG: ontology cleanup, end-to-end one shape, COBISS authoritative containers

**Mission for this phase:** The KG is recent and was built with SL/EN-named fields/edges by previous sessions — that was a mistake. Phase 4 fixes the mistake **end-to-end in one coherent shape**: the ontology becomes language-neutral, every writer emits only the neutral shape, the existing data is transformed (no data loss, no information dropped) into the neutral shape, every reader reads only the neutral shape, the validator enforces it. **No backward-compat fallbacks. No "legacy" parallel path. No review-queue routing for COBISS** — COBISS is your curated authoritative container source; its purpose is to anchor the container side so TM segments map to the correct work. Routing COBISS entries to review defeats that purpose.

**Language codes flow as VALUES at every boundary; never as field/edge/method names:**

- TMX loader (Phase 1B — done): real `xml:lang` codes per `<tu>` entry.
- Smol detector (Phase 1B — done): ISO pair from filename regex; `(None, None)` on miss.
- COBISS ingest (this phase): the existing ingest script ALREADY parses and writes correctly; Phase 4 just renames the kwargs from `title_sl=`/`title_en=` to `title_orig=`/`title_translation=` per the `belina_role` branch, plus finishes the existing bilingual publisher split TODO at lines 269-271. The COBISS parser convention (`entry.title` = SL side, `entry.title_en` = EN side) is trusted as-is; rare EN-target translations go through the extra-container path. NO new dependencies, NO text-level language detection added.
- Extra-container ingest (this phase): user-curated JSON for containers not in COBISS (including EN-target translations that COBISS doesn't index). Explicit `orig_lang`/`translation_lang` values typed by curator.
- Curator editor: explicit codes typed by user.

### Why a single phase

The five work threads are inseparable: changing the ontology breaks writers and readers; migrating data without changing writers leaves the data in the old shape on the next run; changing writers without migrating breaks compatibility with existing nodes; readers need to match the new shape; the validator needs to enforce it. Phase 4 lands all five in one atomic, reversible commit pair (ontology + migration committed first so the migration can be re-run; writer/reader/validator changes follow). After Phase 4 the KG is in ONE shape with ZERO SL/EN-named field/edge names anywhere in code, ontology, or data.

### Ontology revisions

| Location | Current | Replacement |
|---|---|---|
| `ontology.md` §2.4.2 line 167 | `title_en` and `title_sl` second-paragraph encoding | DELETE the second paragraph. The canonical encoding (`title_orig`+`title_translation`+`orig_lang`+`translation_lang`) is the only one. |
| `ontology.md` §2.4.2 line 168 | `slovenian_edition: {publisher, city, year, translator}` sub-dict | REPLACE with `translation_edition: {publisher, city, year, translator, language}` where `language` is an ISO 639-1 code value. |
| `ontology.md` §3.2 line 246 | edge `sl_published_by` | RENAME to `translation_published_by`. The relation describes the role, not the language. |
| `ontology.md` §4 invariant 4 line 297 | "MUST carry both `title_en` and `title_sl`" | REWRITE: "MUST carry `title_orig`+`title_translation`+`orig_lang`+`translation_lang`." |

### Files

- Modify: `ontology.md` (the four locations in the ontology revision table above).
- Create: `scripts/migrate_to_neutral_ontology.py` (one-off; `--dry-run` + `--apply` modes; idempotent; uses field-NAME and edge-attribution evidence to resolve direction; STRIPS legacy field names from nodes after copying values; RENAMES `sl_published_by` edges to `translation_published_by`).
- Modify: `scripts/ingest_personal_bibliography.py` — SMALL CHANGE: rename the kwargs to `kg.add_source_text_node(...)` from `title_sl=`/`title_en=` to `title_orig=`/`title_translation=` per the `belina_role` branch, with lang-code values `"sl"` / `"en"` written as DATA based on the COBISS parser convention (`entry.title` = SL, `entry.title_en` = EN). Finish the bilingual publisher split TODO at lines 269-271: wire `published_by` + `translation_published_by` to the two institutions. No other rewrite. No new dependency.
- Create: `scripts/ingest_extra_containers.py` (NEW — reads `data/extra_containers.json` user-curated list of containers not in COBISS. Curator types `title_orig`/`title_translation`/`orig_lang`/`translation_lang` explicitly. Same neutral encoding. Same `translated_by` edge wiring. Idempotent on container id).
- Create: `data/extra_containers.json` (initial empty list; user populates over time).
- Modify: `translate_core/entity_extraction/smol_extractor.py` (DELETE the three `# SUNSET: Phase 11`-tagged alias write blocks at `:548-555`, `:759-763`, `:884-888`; the builder's `slovenian_edition`→`translation_edition` rename happens at the BUILDER LEVEL: the smol prompt still emits the historical key `slovenian_edition` but the builder maps it to neutral `translation_edition`. **Phase 4 leaves a NEW `# SUNSET: Phase 11` tag on the prompt-level `slovenian_edition` JSON-schema key reference** — that prompt-level rename requires separate model-regression testing and lands in Phase 11).
- Modify: `translate_core/knowledge_graph.py` (`add_source_text_node` accepts the new field names; any helper that hardcoded `slovenian_edition` / `sl_published_by` switches to neutral names).
- Modify: every reader from the Step 4.0b inventory (`docs/phase4_reader_inventory.md`). 75+ reads across 26 files. Read the neutral fields only; no fallbacks.
- Modify: `scripts/validate_kg.py` to ENFORCE the neutral shape — `title_en`/`title_sl`/`slovenian_edition` on source_text nodes are violations; `sl_published_by` edges are violations.
- Test: `tests/test_neutral_ontology_migration.py` (new — covers the migration script).
- Test: `tests/test_ingest_personal_bibliography_neutral.py` (new — covers the COBISS kwarg-rename + finished publisher split).
- Test: `tests/test_ingest_extra_containers.py` (new).
- Test: extend `tests/test_smol_extractor_lang_neutral.py` with assertions that builders NEVER emit `title_en`/`title_sl`/`slovenian_edition` keys.
- Test: `tests/test_validate_kg_neutral.py` (new — covers the new validator violations).

### Agent mix
- Step 4.0a (done): `Explore` — COBISS data shape (`docs/cobiss_actual_shape.md`).
- Step 4.0b (done): `Explore` — reader inventory (`docs/phase4_reader_inventory.md`).
- Step 4.1: `feature-dev:code-architect` — blueprint (`docs/phase4_blueprint.md`) covering ontology revisions, COBISS kwarg-rename diff + finished publisher split, extra-container ingest format, migration script logic (per-node resolution using field NAMES and `translated_by`/`written_by` edges as evidence — NO review queue routing, NO text-level language detection, NO new dependencies), reader-update sequence (75 sites grouped by surface and update strategy), validator update, test plan, and explicit `# SUNSET: Phase 11` placement on the smol prompt-level `slovenian_edition` key.
- Step 4.2: `python-development:python-pro` — TDD red.
- Step 4.3: `python-development:python-pro` — TDD green.
- Step 4.4: `pythonista-reviewer` — diff review focused on constraint 7: no `"en"`/`"sl"`/`"sl_published_by"`/`"title_en"`/`"title_sl"`/`"slovenian_edition"` flow-control or identifier in the new code (these strings may appear only as DATA VALUES being written based on the COBISS parser convention / curator input, or as input data on the migration-input side; the migration explicitly STRIPS the legacy field names after copying).
- Skills: `superpowers:test-driven-development`, `python-development:python-testing-patterns`, `python-development:python-design-patterns` (migration as one-shot transform).

### COBISS ingest — minimal kwarg rename + finish the publisher split

The COBISS ingest script `scripts/ingest_personal_bibliography.py` is ALREADY DONE — it parses, classifies, and writes nodes. Phase 4's change is small:

**Trust the COBISS parser convention** (`translate_core/cobiss_parser.py:42-43` comment): `entry.title` carries the SL side; `entry.title_en` carries the EN side after `=`. For Belina's bibliography that's the structural reality of his Slovenian-library-service export; rare EN-target translations that COBISS records differently go through the extra-container path. **No text-level language detection. No new dependency.**

**Per-entry kwarg rename** (the only writer change):

The current call (around `:174-215`) passes `title_sl=entry.title, title_en=entry.title_en`. Replace by branching on `belina_role`:

```python
if belina_role == "author":
    # Belina wrote it in Slovenian; English side (if any) is the translation alias.
    kwargs["title_orig"] = entry.title
    kwargs["orig_lang"] = "sl"
    if entry.title_en:
        kwargs["title_translation"] = entry.title_en
        kwargs["translation_lang"] = "en"
elif belina_role == "translator":
    # Belina translated INTO Slovenian; SL side is the translation.
    kwargs["title_translation"] = entry.title
    kwargs["translation_lang"] = "sl"
    if entry.title_en:
        # The English side is the original-language title.
        kwargs["title_orig"] = entry.title_en
        kwargs["orig_lang"] = "en"
    # If entry.title_en is empty, COBISS doesn't carry the original-language
    # title; title_orig / orig_lang stay unset. The rest of the record is
    # still authoritative.
elif belina_role == "editor":
    # Treat like author: he edited the SL side; if EN side exists it's a
    # parallel translation.
    kwargs["title_orig"] = entry.title
    kwargs["orig_lang"] = "sl"
    if entry.title_en:
        kwargs["title_translation"] = entry.title_en
        kwargs["translation_lang"] = "en"
# (None, None) classification continues to land in data/cobiss_unclassified_entries.json,
# unchanged from today.
```

The `"sl"` and `"en"` literals here are VALUES being written as data based on the COBISS parser convention — the value enters as data, not as flow control on the KG layer (constraint 7 + 9 compliant).

**Finish the bilingual publisher TODO at lines 269-271:**

When `entry.publisher` contains `: =`, split into `(left, right)`:
- Create two `institution` nodes (one per side).
- Wire `published_by` to the SL-side institution (left of `: =`).
- Wire `translation_published_by` to the translation-side institution (right of `: =`).

When no `: =` separator: single institution + single `published_by` edge (unchanged).

**Set `provenance="cobiss_personal"`** on every record the script emits (per Phase 5 chokepoint).

That's the whole COBISS ingest change. Everything else in the script stays.

### Extra-container ingest

Some containers will be added by the curator OUTSIDE COBISS — works the user translated that COBISS doesn't include (or where the COBISS record is wrong/missing). Format: `data/extra_containers.json`, a JSON list of records that look like the COBISS-produced container records (same neutral encoding, same field names):

```json
[
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
]
```

`scripts/ingest_extra_containers.py` reads this file, calls `kg.add_source_text_node(...)` per entry with the same factory calls the COBISS ingest uses, wires `translated_by` edges. Idempotent on `container_id`. New `provenance` value: `curator_extra`. Phase 5's routing chokepoint accepts `cobiss_personal` OR `curator_extra` as valid provenance for `kind="translated_work"`.

### Tasks

_Step 4.0c was removed_ — earlier draft proposed a `lingua-py` survey; after user pushback, Phase 4 no longer adds a language-detection dependency. The migration uses field NAMES (`title_sl` value is in Slovenian by definition of the field that held it; `title_en` value is in English) + edge attribution; the COBISS ingest uses the parser convention + classifier role. No new library.

- [ ] **Step 4.1: Coordinator invokes `python-development:python-design-patterns`, then dispatches `feature-dev:code-architect` for the design.**

> Subagent type: `feature-dev:code-architect`.
> Out of scope + constraints 9, 10, 7 (verbatim — the new constraint 7: KG has no SL/EN-named fields/edges; values flow as data from boundaries; NO backward-compat fallbacks; NO legacy parallel paths; NO new dependencies).
> Read first: `docs/cobiss_actual_shape.md`; `docs/phase4_reader_inventory.md` (NOTE: this inventory was written before the "no backward-compat fallback" rule landed and recommends `d.get("title_orig") or d.get("title_en")` fallbacks throughout — IGNORE those recommendations. The plan constraint (constraint 7 + Phase 4's `No backward-compat fallbacks` rule) is authoritative: NO fallbacks. The inventory doc's value is the file/site listings per surface; ignore its migration-strategy section); `docs/parsing_simplification_lang_neutrality_audit.md`; `ontology.md`; `scripts/ingest_personal_bibliography.py`; `translate_core/entity_extraction/smol_extractor.py:540-595, 745-770, 870-895`; `translate_core/knowledge_graph.py` (find `add_source_text_node`, `add_institution_node`); `scripts/validate_kg.py`.
> Task: produce a written blueprint at `docs/phase4_blueprint.md` covering:
>   1. **Ontology revision diff.** Exact before/after for `ontology.md` §2.4.2 (delete the second-paragraph `title_en`+`title_sl`+`slovenian_edition` encoding entirely; the canonical four-field encoding is the only one); §3.2 (rename `sl_published_by` → `translation_published_by`); §4 invariant 4 (rewrite to reference canonical fields).
>   2. **COBISS ingest kwarg-rename diff.** The `belina_role` branch shown in the plan's "COBISS ingest" section. Exact pre/post diff against `scripts/ingest_personal_bibliography.py:174-215`. Add the publisher TODO completion at `:269-271` — split on `: =`, wire two edges. NO new dependency. NO text-level language detection.
>   3. **Extra-container ingest.** `scripts/ingest_extra_containers.py` and `data/extra_containers.json` schema (already shown in plan). Idempotency on `container_id`. `provenance="curator_extra"`. Same `kg.add_source_text_node(...)` factory call as the COBISS path.
>   4. **Smol_extractor cleanup.** Delete the three `# SUNSET: Phase 11` alias write blocks at `:548-555, :759-763, :884-888`. The smol prompt continues to emit `slovenian_edition` as a JSON key (changing the prompt risks model regression). The BUILDER reads that key and writes a canonical `translation_edition: {publisher, city, year, translator, language}` to the payload where `language` is derived from the EXISTING extraction context (the smol detector's `src_lang`/`tgt_lang` already in scope) — no NEW language detection. Phase 4 places a NEW `# SUNSET: Phase 11` tag on the prompt-level `slovenian_edition` JSON-schema reference; that final rename ships in Phase 11 after model-regression testing.
>   5. **Migration script.** Function-level breakdown of `scripts/migrate_to_neutral_ontology.py`:
>     - Edge rename: every `sl_published_by` edge → `translation_published_by`. Audit baseline: 20 edges.
>     - Node field migration: for each source_text node carrying `title_en` / `title_sl` / `slovenian_edition`, the resolution uses **field NAMES as evidence**: a value in the `title_sl` field is in Slovenian; a value in the `title_en` field is in English. Direction comes from existing edges:
>       - Node has `translated_by` edge to a translator agent → `title_translation` = the field that was in the translator's target language (read from the agent's stored working pair if available; otherwise default `title_sl`→`title_translation` with `translation_lang="sl"` since SL is this translator's dominant target). The other field → `title_orig` with its corresponding lang code.
>       - Node has `written_by` edge (no `translated_by`) → `title_orig` = `title_sl` (with `orig_lang="sl"`) when present; otherwise `title_orig` = `title_en` (with `orig_lang="en"`). Second field → `title_translation`.
>       - Node has NEITHER edge → smol/doc_pair extraction. If both `title_sl` and `title_en` exist, write `title_orig` = `title_sl`, `orig_lang="sl"`, `title_translation` = `title_en`, `translation_lang="en"`. If only one exists, write `title_orig` = that one with the corresponding lang code.
>       - `slovenian_edition` sub-dict → `translation_edition` with added `language: "sl"` field (since the slovenian-edition data is structurally Slovenian).
>       - After copying values, DELETE the legacy attributes from the node.
>     - Idempotency: a node already in neutral shape (no `title_en`/`title_sl`/`slovenian_edition`) is skipped.
>     - `--dry-run` reports counts: `edges_renamed`, `nodes_migrated`, `nodes_already_neutral`, `nodes_skipped_no_legacy_fields`.
>     - NO text-level language detection. NO new dependency.
>   6. **`knowledge_graph.py` writer factory.** `add_source_text_node` accepts the new field names (it should already accept arbitrary kwargs; verify). Remove any internal handling that converts SL/EN-named kwargs.
>   7. **Reader migration.** Per `docs/phase4_reader_inventory.md`, group the 75+ readers and propose the order of edits. **No backward-compat fallbacks** (`d.get("title_orig") or d.get("title_en")` is BANNED). The migration runs FIRST in the green-phase implementation order; readers are updated AFTER the migration, so they read fully-migrated nodes.
>   8. **Validator (`scripts/validate_kg.py`).** Add ENFORCEMENT: a source_text node carrying `title_en` / `title_sl` / `slovenian_edition` is a violation; an edge with relation `sl_published_by` is a violation. The validator becomes the regression net.
>   9. **Test plan.** Cover: migration script idempotency + each branch (translator-edge, author-edge, neither-edge); writer-rewire output for each `belina_role`; bilingual publisher split (with `: =`); extra-container ingest; smol_extractor builder output; validator catches violations; readers read neutral fields and render correctly.
>  10. **Implementation order for TDD green.** Strict sequence so readers don't run against partially-migrated data: ontology edit → migration script implementation → migration `--dry-run` against KG copy → migration `--apply` against KG copy verified → writers updated → extra-container ingest added → smol_extractor cleanup → readers updated → validator updated → full test suite + run validator on the migrated copy.
>  11. **Risks.** Anything you spot.

- [ ] **Step 4.2: Coordinator invokes `superpowers:test-driven-development` + `python-development:python-testing-patterns`, then dispatches `python-development:python-pro` for TDD red.**

> Subagent type: `python-development:python-pro`.
> Out of scope + constraints 9, 10, 7 (verbatim).
> Read first: the architect's blueprint at `docs/phase4_blueprint.md`; current state of every file the blueprint says will change.
> Task: write failing tests per the blueprint's §9 test plan.
>
> Required test files:
>   - `tests/test_neutral_ontology_migration.py` — covers each migration branch (translator-edge, author-edge, neither-edge with both fields, neither-edge with only one field), idempotency, `slovenian_edition`→`translation_edition`, edge rename. Uses synthetic small KG fixtures via `tmp_path`.
>   - `tests/test_ingest_personal_bibliography_neutral.py` — uses a small fixture COBISS export covering one Belina-as-author entry, one Belina-as-translator entry, one Belina-as-editor entry, and the bilingual exhibition catalogue case (with `: =` publisher). Asserts kwargs passed to `add_source_text_node` carry only neutral field names + the bilingual publisher split wires `published_by` + `translation_published_by`.
>   - `tests/test_ingest_extra_containers.py` — covers idempotency + `provenance="curator_extra"` + neutral encoding from the JSON.
>   - Extend `tests/test_smol_extractor_lang_neutral.py` — assert builders NEVER emit `title_en`/`title_sl`/`slovenian_edition` keys for ANY pair (EN/SL included).
>   - `tests/test_validate_kg_neutral.py` (new) — covers the new validator violations.
>
> Verify all new tests FAIL. Note any existing test that asserts the SL/EN-specific shape (those need the green-phase edit too — list them).
> Report: test code + failure output + list of existing tests needing update.

- [ ] **Step 4.3: Coordinator dispatches `python-development:python-pro` for TDD green.**

> Subagent type: `python-development:python-pro`.
> Out of scope + constraints 9, 10, 7 (verbatim).
> Read first: failing tests; architect's blueprint.
> Task: implement per the blueprint's §10 strict order. STOP and report at the end of each step (the coordinator runs the gate before continuing):
>   1. Edit `ontology.md` per the blueprint §1.
>   2. Implement `scripts/migrate_to_neutral_ontology.py` per §5. Run `tests/test_neutral_ontology_migration.py`.
>   3. Coordinator runs migration `--dry-run` against KG copy (Step 4.5 task).
>   4. Coordinator runs migration `--apply` against KG copy with user confirmation (Steps 4.6/4.7).
>   5. COBISS ingest kwarg-rename + publisher TODO per §2. Run `tests/test_ingest_personal_bibliography_neutral.py`.
>   6. Implement extra-container script per §3. Run its tests.
>   7. Update smol_extractor per §4 (delete the SUNSET blocks; place the new SUNSET tag on the prompt-level `slovenian_edition` reference). Run smol tests.
>   8. `knowledge_graph.py` factory update per §6 if needed.
>   9. Update readers per §7. NO backward-compat fallbacks.
>  10. Update validator per §8. Run validator tests.
>  11. Run full test suite. Run validator on the migrated KG copy. Both must pass clean.
> Report: per-step diff + pytest output. Surface anything unexpected at the step it surfaces; don't bundle.

- [ ] **Step 4.4: Coordinator dispatches `pythonista-reviewer` for diff review.**

Brief: "Review the entire Phase 4 diff (ontology, migration script, COBISS ingest, extra-container ingest, smol_extractor, knowledge_graph, every reader, validator, tests). Hard checks:
(a) `grep -rn 'title_en\\|title_sl\\|slovenian_edition\\|sl_published_by' /Users/bel/CascadeProjects/sl_translator --include='*.py' | grep -v __pycache__ | grep -v .venv/` returns ONLY: (i) the migration script's INPUT-side scan for nodes carrying legacy attributes; (ii) tests asserting violations / migration behaviour; (iii) the validator's forbidden-attribute list; (iv) the new `# SUNSET: Phase 11` tag at the smol prompt-level `slovenian_edition` reference. NO writes. NO reader fallbacks. NO 'or d.get(\"title_en\")' patterns.
(b) `grep -rn '\"en\"\\|\"sl\"' translate_core/ scripts/ --include='*.py' | grep -v __pycache__ | grep -v .venv/` returns ONLY: (i) the COBISS ingest's belina_role branches writing those as DATA VALUES into neutral kwargs; (ii) tests with expected-value assertions; (iii) the migration's lang-code values being written based on field-name evidence. NO conditional branches on these literals.
(c) Ontology revisions match the blueprint's §1 exactly.
(d) Migration script is idempotent (run twice on same input, no second-round writes).
(e) No node in the migrated KG carries any of the legacy fields; no edge carries the legacy relation.
(f) Smol builders never emit the legacy keys for ANY pair.
(g) Validator catches violations.
(h) The new `# SUNSET: Phase 11` tag on the smol prompt-level `slovenian_edition` reference is present and clearly documents the deferred work.
Report only high-confidence findings."

- [ ] **Step 4.5: Coordinator runs migration `--dry-run` against a KG copy.**

```bash
cp data/knowledge.db /tmp/knowledge.db.phase4.dryrun
.venv/bin/python3 scripts/migrate_to_neutral_ontology.py --dry-run --kg-path /tmp/knowledge.db.phase4.dryrun 2>&1 | tail -30
```

Coordinator inspects: renamed edges should be 20 (audit baseline); migrated nodes should be a meaningful count; routed-to-review should be small enough that the curator can clear it.

- [ ] **Step 4.6: Coordinator confirms with user before `--apply`.**

If dry-run numbers look right, ask the user explicitly whether to apply the migration to the live KG (this is a destructive step on live data, even though no data is deleted).

- [ ] **Step 4.7: Apply migration on live KG.**

```bash
cp data/knowledge.db data/knowledge.db.phase4.bak
.venv/bin/python3 scripts/migrate_to_neutral_ontology.py --apply 2>&1 | tail -20
```

- [ ] **Step 4.8: Real-data verification post-migration.**

```bash
.venv/bin/python3 -c "
from translate_core.knowledge_graph import KnowledgeGraph
from collections import Counter
kg = KnowledgeGraph()
node_types = Counter(d.get('type') for _, d in kg.G.nodes(data=True))
edge_rels  = Counter(d.get('relation') for _, _, d in kg.G.edges(data=True))
print('EDGES:', dict(edge_rels))
# Confirm sl_published_by is gone, translation_published_by appears.
assert edge_rels.get('sl_published_by', 0) == 0, 'sl_published_by edges still present'
print('translation_published_by:', edge_rels.get('translation_published_by', 0))
# Confirm no node carries title_en / title_sl / slovenian_edition — migration strips them after value copy.
leftover_title_en = sum(1 for _, d in kg.G.nodes(data=True) if d.get('title_en'))
leftover_title_sl = sum(1 for _, d in kg.G.nodes(data=True) if d.get('title_sl'))
leftover_sl_edition = sum(1 for _, d in kg.G.nodes(data=True) if d.get('slovenian_edition'))
print(f'leftover_title_en={leftover_title_en} leftover_title_sl={leftover_title_sl} leftover_slovenian_edition={leftover_sl_edition}')
# All three should be 0 after migration --apply.
assert leftover_title_en == 0
assert leftover_title_sl == 0
assert leftover_sl_edition == 0
print('OK')
"
```

- [ ] **Step 4.9: Commit Phase 4.**

```bash
git add ontology.md \
        scripts/migrate_to_neutral_ontology.py \
        scripts/ingest_personal_bibliography.py \
        translate_core/entity_extraction/smol_extractor.py \
        translate_core/knowledge_graph.py \
        tests/test_neutral_ontology_migration.py \
        tests/test_ingest_personal_bibliography_neutral.py \
        tests/test_smol_extractor_lang_neutral.py
# Plus any reader changes flagged by Step 4.0a (ui/, kg_editor_ui.py, validate_kg.py, kg_ingest_entities.py)
git add <those files as needed>
git commit -m "phase4: language-neutral ontology; migrate sl_published_by + title_en/title_sl; COBISS rewire"
```

### Phase 4 verification gate

- [ ] Ontology revisions land: §2.4.2 has ONLY the canonical four-field encoding; the legacy second-paragraph encoding is REMOVED entirely. §3.2 has `translation_published_by`; `sl_published_by` no longer documented. §4 invariant 4 references canonical fields.
- [ ] Migration `--apply` ran cleanly; live KG has ZERO `sl_published_by` edges, ZERO nodes with `title_en` / `title_sl` / `slovenian_edition` attributes (the migration strips them after copying values).
- [ ] `translation_published_by` edges count ≥ 20 (the migrated baseline).
- [ ] COBISS ingest writes ONLY neutral fields; values come from the `belina_role` branch (SL data from `entry.title`, EN data from `entry.title_en`) per the COBISS parser convention. No text-level language detection.
- [ ] Extra-container ingest works; reads `data/extra_containers.json` (which may be initially empty); idempotent.
- [ ] Smol builders never emit `title_en` / `title_sl` / `slovenian_edition` keys for ANY language pair.
- [ ] Every reader from the Step 4.0b inventory updated to read ONLY the neutral fields. NO `d.get("title_orig") or d.get("title_en")` fallback patterns.
- [ ] Editor surfaces render correctly on the migrated KG.
- [ ] `validate_kg.py` enforces the neutral shape; running it against the migrated KG produces zero violations.
- [ ] `pytest tests/` green.
- [ ] Pythonista-reviewer pass clean on the constraint-7 checks (no SL/EN-named identifiers in code; no flow-control conditionals on `"en"`/`"sl"` literals; values only from boundaries).

---

## Phase 5 — Container vs. cited-work routing chokepoint

**Scope reminder — constraints 9 + 10:** Phase 5 enforces the bibliography bright line in code. Provenance values are `"cobiss_personal"` (personal-bibliography records — containers AND self-authored, from `scripts/ingest_personal_bibliography.py`), `"curator_extra"` (user-curated containers not in COBISS, from `scripts/ingest_extra_containers.py` introduced in Phase 4), `"tm_smol"` (cited works extracted from TM segments by the smol pipeline), `"doc_pair"` (cited works extracted from translated DOCX/MD pairs). The legacy `"book_bibliography"` provenance from `ingest_book_*.py` is NOT in scope — those scripts are deleted in Phase 11; their value never reaches the router. Containers (`kind="translated_work"`) accept `provenance="cobiss_personal"` OR `provenance="curator_extra"` — both are authoritative container sources. All other provenance values (`tm_smol`, `doc_pair`, unset, unknown) route a `translated_work` record to review — they are the audit's "seeded-book" bug class (audit §10.4).

**Purpose:** the audit §5 calls for a single dispatcher inside `kg_ingest_entities.py` that decides record routing from `record["source"]["provenance"]` and rejects mismatches.

**Files:**
- Modify: `translate_core/kg_ingest_entities.py:514-547` (current translated_work routing; tighten the gate) and the `write_to_kg` entry point that fan-outs by record kind.
- Modify: `scripts/ingest_personal_bibliography.py` to stamp `provenance="cobiss_personal"` on every record it emits (containers AND self-authored).
- Modify: `translate_core/entity_extraction/smol_extractor.py` to stamp `provenance="tm_smol"` on every record it emits.
- Modify: `translate_core/document_pair_pipeline.py` to stamp `provenance="doc_pair"` on records it emits.
- Test: `tests/test_kg_ingest_routing.py` (new)

### Agent mix
- Step 5.0: `Explore` (callgraph survey)
- Step 5.1: `feature-dev:code-architect` (dispatcher design — no code, returns blueprint)
- Step 5.2: `python-development:python-pro` for TDD red
- Step 5.3: `python-development:python-pro` for TDD green
- Step 5.4: `pythonista-reviewer` for diff review
- Skills the coordinator invokes before dispatching: `python-development:python-design-patterns` (SRP, single-chokepoint design), `python-development:python-error-handling` (review-queue routing), `superpowers:test-driven-development`, `python-development:python-testing-patterns`.

### Tasks

- [ ] **Step 5.0: Coordinator dispatches `Explore` for callgraph survey.**

Brief: "Find every call site that currently writes to the existing review queue (file path: locate it by searching for `extraction_review` or similar markers). For each, report (a) the exact write API, (b) the record-shape expected, (c) whether the existing routes-to-review pattern is a single function or many. Then find every reader of `record['source']['provenance']` if any (likely zero today, since the audit says we are introducing this field)."

- [ ] **Step 5.1: Coordinator invokes `python-development:python-design-patterns` skill, then dispatches `feature-dev:code-architect` for the dispatcher design.**

> Subagent type: `feature-dev:code-architect`.
> Out of scope + constraints 9 and 10 (verbatim from this plan).
> Read first: `ontology.md` §2.4.1 + §3.2, `docs/parsing_simplification_audit.md` §5, `translate_core/kg_ingest_entities.py:1-100, 463-780`, the Step 5.0 Explore report (paste in).
> Task: produce a design blueprint (no code yet) for `_route_record(record) -> tuple[str, dict]`. Specify:
>   - The function signature, return tuple semantics, and the exact set of return values for `kind="translated_work"` vs. `kind="cited_work"` vs. other kinds (agent_person, institution, concept, artwork, performance) under each allowed provenance value.
>   - The review-queue write API (taken from Step 5.0).
>   - The exact existing code location to replace and what its current behaviour is.
>   - File/function boundaries — where does `_route_record` live (top of `kg_ingest_entities.py`)? Where is it called from? What does the caller look like after the change?
>   - Any constants that need to move to a single source of truth (e.g. `CONTAINER_TYPES`, `CITED_TYPES`, valid provenance set).
> Constraint reminder: containers accept `provenance="cobiss_personal"` OR `provenance="curator_extra"`. NO other provenance value is allowed for `kind="translated_work"`. Both producers (Phase 4's `scripts/ingest_personal_bibliography.py` and `scripts/ingest_extra_containers.py`) are authoritative sources; the routing chokepoint enforces "container-class provenance" not "COBISS provenance".

- [ ] **Step 5.2: Coordinator invokes `superpowers:test-driven-development` + `python-development:python-testing-patterns`, then dispatches `python-development:python-pro` for TDD red.**

> Subagent type: `python-development:python-pro`.
> Out of scope + constraints 9 and 10 (verbatim).
> Read first: the architect's blueprint (paste in); `ontology.md` §2.4.1 + §3.2; `docs/parsing_simplification_audit.md` §5 and §10.4.
> Task: write `tests/test_kg_ingest_routing.py` (pytest function-style; tmp_path-scoped KG; no live KG mutation). Cover:
> (a) `kind="translated_work"` + `provenance="cobiss_personal"` → ACCEPTED; container node written with `translated_by` edge. Use a non-SL/EN example title pair to surface any latent language-pair bug.
> (a') `kind="translated_work"` + `provenance="curator_extra"` → ACCEPTED; container node written with `translated_by` edge. Cover one entry with explicit `orig_lang="de"` + `translation_lang="en"` to confirm non-SL pairs work via the extra-container path.
> (b) `kind="translated_work"` + `provenance="tm_smol"` → REVIEW with reason `provenance_mismatch_for_translated_work`. The seeded-book bug class (audit §10.4) MUST surface here.
> (c) `kind="translated_work"` + `provenance="doc_pair"` → REVIEW (same reason). Doc-pair records are cited works, never containers.
> (d) `kind="cited_work"` + `provenance="tm_smol"` → ACCEPTED, typed `source_text` written with `cited_in` to its `container_work_id`.
> (e) `kind="cited_work"` + `provenance="doc_pair"` → ACCEPTED, same.
> (f) `kind="cited_work"` + `provenance="cobiss_personal"` → ACCEPTED (the self-authored case from Phase 4); `written_by` edge. NO `cited_in` edge.
> (g) `kind="cited_work"` + valid provenance + `container_work_id` that does NOT resolve to an existing container node → REVIEW with reason `container_not_found`. The cited record is NOT written.
> (h) `kind="cited_work"` + provenance UNSET or unknown value → REVIEW with reason `provenance_missing_or_unknown`.
> (i) other kinds (agent_person, institution, concept, artwork, performance) accept ANY of the three valid provenance values. ANY OTHER provenance value routes to review.
> (j) language-pair undetermined routing (audit checklist §5): `kind="cited_work"` + valid provenance + `orig_lang is None` AND `translation_lang is None` → REVIEW with reason `language_pair_undetermined`. After Phase 1B the smol detector returns `(None, None)` for unrecognised filenames; without this gate those records would slip through.
> Verify all tests FAIL against current code. Report: test code + failure output.

- [ ] **Step 5.3: Coordinator invokes `python-development:python-error-handling`, then dispatches `python-development:python-pro` for TDD green.**

> Subagent type: `python-development:python-pro`.
> Out of scope + constraints 9 and 10 (verbatim).
> Read first: the failing tests; the architect's blueprint.
> Task: implement `_route_record` per the blueprint. Replace the existing `if kind == "translated_work"` block in `write_to_kg`. Stamp `provenance` at each producer:
> - `scripts/ingest_personal_bibliography.py` → `"cobiss_personal"` on every record (containers AND self-authored).
> - `translate_core/entity_extraction/smol_extractor.py` → `"tm_smol"` on every record returned by every `_build_*` function (set in `record["source"]["provenance"]`).
> - `translate_core/document_pair_pipeline.py` → `"doc_pair"` on every record it produces.
> Reject paths route via the existing review-queue mechanism (from Step 5.0).
> Verify: new tests pass; all existing tests pass; the diff does NOT introduce raw `kg.G.add_edge` calls.
> Report: full per-file diff; pytest output for new tests AND for the full suite.

- [ ] **Step 5.4: Coordinator dispatches `pythonista-reviewer` for the diff review.**

> Subagent type: `pythonista-reviewer`.
> Brief: "Review the Phase 5 diff (`translate_core/kg_ingest_entities.py`, smol/cobiss/doc_pair producers, the new test file). Check specifically for: constraint 9 (no hardcoded language pair anywhere); constraint 10 (containers ONLY from cobiss_personal, cited_work from any of the three valid provenances, NO `cited_in` on self-authored COBISS records); no `kg.G.add_edge` direct writes; review-queue write goes to the existing mechanism, not a new file. Report only high-confidence findings."

- [ ] **Step 5.5: Coordinator real-data check (dry-run).**

```bash
cp data/knowledge.db data/knowledge.db.phase5.bak
# Run the smol-side ingest harness with the new router. The subagent must
# expose either an `--inspect` flag on run_entity_extraction.py or a function
# `simulate_routing(records) -> dict` that returns counts without writing.
.venv/bin/python3 run_entity_extraction.py --inspect 2>&1 | grep -E "router|direct|review|reject" | head -20
```
The coordinator examines: (a) are any `translated_work` records arriving from non-COBISS provenance routed to review (the seeded-book bug class)? Expected: at least a handful — proves the gate fires. (b) Are any records routed with reason `language_pair_undetermined`? After Phase 1B fixes the smol detector, this should fire for any TMX origin whose filename doesn't carry an ISO pair.

- [ ] **Step 5.6: Commit Phase 5.**

```bash
git add translate_core/kg_ingest_entities.py \
        translate_core/entity_extraction/smol_extractor.py \
        translate_core/document_pair_pipeline.py \
        scripts/ingest_personal_bibliography.py \
        tests/test_kg_ingest_routing.py
git commit -m "kg_ingest: single-chokepoint routing by provenance; reject mis-typed records"
```

### Phase 5 verification gate

- [ ] New routing tests pass.
- [ ] Real-data dry-run: at least one `translated_work` record from non-COBISS provenance routed to review (proving the gate fires).
- [ ] Existing `book_translation` count on live KG unchanged (gate is on new writes, not retroactive).
- [ ] Reviewer pass clean on constraints 9 + 10.

---

## Phase 6 — Chronological-anchor container attribution

**Purpose:** replace `seeded_book_finder.py`'s naive-index window matching and `run_entity_extraction.py:586-677`'s proximity propagation with the curator-anchored chronological-walk approach from audit §4. Promote `data/quarantine/_segment_title_attribution.json` to canonical input.

**Files:**
- Create: `translate_core/container_attribution.py`
- Modify: `scripts/build_segment_attribution.py` to emit `t_index` ranges (audit §4 "Files that should own this")
- Modify: `run_entity_extraction.py` — remove the proximity-propagation block (lines 586–677) and the seeded-records branch (lines 148–156). Wire the new attribution module in their place.
- Promote: `data/quarantine/_segment_title_attribution.json` → `data/segment_title_attribution.json`
- Test: `tests/test_container_attribution.py` (new)
- DO NOT MODIFY: `translate_core/entity_extraction/seeded_book_finder.py` (deleted in Phase 11; in this phase only stop calling it).

### Agent mix
- Step 6.0: `Explore` (callgraph survey of seeded_book_finder callers + curator-file shape inspection)
- Step 6.1: coordinator promotes the curator file (file copy + force-add)
- Step 6.2: `feature-dev:code-architect` for the chronological-anchor algorithm design
- Step 6.3: `python-development:python-pro` for TDD red
- Step 6.4: `python-development:python-pro` for TDD green
- Step 6.5: `python-development:python-pro` for the path-mismatch fix
- Step 6.6: `pythonista-reviewer` for the diff review
- Skills the coordinator invokes before dispatching: `python-development:python-design-patterns` (separation of attribution from ingest), `python-development:python-project-structure` (where the new module lives), `superpowers:test-driven-development`, `python-development:python-testing-patterns`.

### Tasks

- [ ] **Step 6.0: Coordinator dispatches `Explore` for callgraph survey.**

Brief: "(a) Find every reader and writer of `data/quarantine/_segment_title_attribution.json` so we know what depends on its current shape. (b) Find every caller of `seeded_book_finder.find_book_anchors`, `seeded_book_finder.book_claims_to_records`, `BookClaim.contains`, and `claim_for_segment` — these are the naive-index sites Phase 6 retires. (c) Find every consumer of `seg_idx` / `global_idx` from `run_entity_extraction.export_segments` and the smol payload — they will need to consume `t_index` going forward. Report file:line for each."

- [ ] **Step 6.1: Coordinator promotes the curator file.**

```bash
cp data/quarantine/_segment_title_attribution.json data/segment_title_attribution.json
# data/ is gitignored — force-add this curator INPUT file:
git add -f data/segment_title_attribution.json
```
No commit yet — bundled with Step 6.7.

- [ ] **Step 6.2: Coordinator invokes `python-development:python-design-patterns` + `python-development:python-project-structure` skills, then dispatches `feature-dev:code-architect` for the algorithm design.**

> Subagent type: `feature-dev:code-architect`.
> Out of scope + constraints 9 and 10 (verbatim).
> Read first: `docs/parsing_simplification_audit.md` §4 in full; `ontology.md` §2.4.1 + §3.2; the Step 6.0 Explore report (paste in); `translate_core/tm.py` post-Phase-1 (exposes `t_index`); `data/segment_title_attribution.json` top.
> Task: produce a design blueprint (no code) for `translate_core/container_attribution.py`. Specify:
>   - The public surface: `attribute_segments_to_containers(anchors, tm_entries) -> tuple[dict, list, list]` — exact shape of each return value; how conflicts and unanchored entries are distinguished.
>   - Loader helpers: `load_curator_anchors(path)` and `load_ngram_anchors(path)` — input file shapes, output anchor shape.
>   - Updates to `scripts/build_segment_attribution.py` (existing n-gram code) — what to change to emit `t_index` ranges; what file path to write to.
>   - Updates to `run_entity_extraction.py` — exactly which lines (148–156 seeded-records branch; 586–677 proximity-propagation block) get replaced with what.
>   - Updates to `translate_core/entity_extraction/smol_extractor.parse_smol_response` — read `t_index` from the payload with a `seg_idx` fallback, surfacing the fallback as a logged warning.
>   - Boundary semantics: how the chronological walker handles (a) origins with curator coverage only, (b) origins with n-gram coverage only, (c) origins with both that agree, (d) origins with both that disagree.

- [ ] **Step 6.3: Coordinator invokes `superpowers:test-driven-development` + `python-development:python-testing-patterns`, then dispatches `python-development:python-pro` for TDD red.**

> Subagent type: `python-development:python-pro`.
> Out of scope + constraints 9 and 10 (verbatim).
> Read first: the architect's blueprint (paste in).
> Task: write `tests/test_container_attribution.py` covering:
> (a) Given a sorted list of `(origin, t_index, container_id)` anchors and a stream of TM entries in `t_index` order, `attribute_segments_to_containers(anchors, tm_entries)` returns a dict `{(origin, t_index): container_id}` where each segment inherits from the most recent anchor whose `t_index <= seg.t_index` and whose `origin` matches. No `MAX_GAP` heuristic.
> (b) When two anchors for the same origin disagree (curator file says X for `t_index=400`, n-gram says Y for `t_index=400`), the conflict is queued to a review list returned from the function; the segment is NOT auto-attributed.
> (c) Segments earlier than any anchor for their origin are returned with `container_id=None` and a separate "unanchored" list, NOT auto-attributed to anything.
> (d) The function operates on `t_index` only; passing `raw_index` should not silently coerce.
> (e) (audit checklist §6) Multi-pair corpus: when the TM holds entries from BOTH an `en-sl` origin and an `hr-sl` origin, attribution must work for both. Add a fixture / synthetic test that exercises a multi-pair scenario; the chronological walker must NOT assume all entries share the same `source_lang`.
> (f) The attribution module surfaces `origin → (source_lang, target_lang)` alongside the container mapping so downstream consumers (Phase 12 spot-checks, future cited_in reasoning) can reason about which side of the bilingual pair a citation came from.
> Tests must fail against current code (the function does not exist).
> Report: tests + failure output.

- [ ] **Step 6.4: Coordinator dispatches `python-development:python-pro` for TDD green.**

> Subagent type: `python-development:python-pro`.
> Out of scope + constraints 9 and 10 (verbatim).
> Read first: the failing tests; the architect's blueprint; current `seeded_book_finder.py` (to understand what we replace); audit §4 "Recommended approach" primary + fallback.
> Task: implement per the blueprint.
>   - `translate_core/container_attribution.py` with `attribute_segments_to_containers(anchors, tm_entries)`, `load_curator_anchors(path="data/segment_title_attribution.json")`, `load_ngram_anchors(path="data/segment_attribution_ngram.json")`.
>   - Modify `scripts/build_segment_attribution.py` to emit `t_index`-keyed ranges into `data/segment_attribution_ngram.json` (NOT inside quarantine).
>   - Modify `run_entity_extraction.py`: remove the seeded-records branch (lines 148–156); replace lines 586–677 with a single call to the attribution module; emit `t_index` on every exported record in `export_segments`.
>   - Modify `smol_extractor.parse_smol_response` to read `t_index` from the smol payload with a `seg_idx` fallback and a logged warning when the fallback fires.
>   - Apply the SMOL_EXTRACTIONS_PATH fix from audit §8 in the same diff: change `run_entity_extraction.py:60` to `Path("data/smol_entities_map/smol_extractions.json")`.
> Verify: new tests pass; existing tests pass; `.venv/bin/python3 -c "from run_entity_extraction import SMOL_EXTRACTIONS_PATH; print(SMOL_EXTRACTIONS_PATH.exists())"` returns `True`.
> Report: full diff.

- [ ] **Step 6.5: Coordinator dispatches `pythonista-reviewer` for the diff review.**

Brief: "Review the Phase 6 diff. Confirm: no naive `enumerate(tm.entries)` slicing reintroduced; the chronological walker keys off `t_index` only; conflicts are surfaced (not silently resolved); no `MAX_GAP` heuristic; `data/` paths are correct; SMOL_EXTRACTIONS_PATH fix applied. Report only high-confidence findings."

- [ ] **Step 6.6: Coordinator real-data dry-run.**

```bash
cp data/knowledge.db data/knowledge.db.phase6.bak
# Dry-run the new attribution against the real curator file + the real TM:
.venv/bin/python3 -c "
from translate_core.tm import TranslationMemory
from translate_core.container_attribution import attribute_segments_to_containers, load_curator_anchors
anchors = load_curator_anchors()
tm = TranslationMemory()
attrib, conflicts, unanchored = attribute_segments_to_containers(anchors, tm.entries)
print(f'attributed={len(attrib)}  conflicts={len(conflicts)}  unanchored={len(unanchored)}')
print('sample attributions:', list(attrib.items())[:3])
"
```
Expected: a sizeable `attributed` count, small but non-zero `conflicts` (these should be queued — NOT auto-resolved), and an `unanchored` count for origins lacking curator coverage.

- [ ] **Step 6.7: Commit Phase 6.**

```bash
git add translate_core/container_attribution.py \
        scripts/build_segment_attribution.py \
        run_entity_extraction.py \
        translate_core/entity_extraction/smol_extractor.py \
        tests/test_container_attribution.py
# data/segment_title_attribution.json was already force-added in Step 6.1
git commit -m "attribution: chronological-anchor container walker replaces naive-index propagation"
```

### Phase 6 verification gate

- [ ] New tests pass.
- [ ] `SMOL_EXTRACTIONS_PATH.exists()` returns True.
- [ ] Real-data attribution: attributed count > 1000, conflicts > 0 (proving conflict detection works), unanchored count surfaced.
- [ ] No call to `seeded_book_finder.find_book_anchors` remains in `run_entity_extraction.py` (`grep -n seeded_book_finder run_entity_extraction.py` returns nothing).
- [ ] Reviewer pass clean.

---

## Phase 7 — DELETE VL-era files

**Purpose:** with Phase 2 having moved the editor-needed symbols, the rest of `vl_parser.py` plus the VL extractor stack are now unreachable from in-scope code.

**Files (DELETE):**
- `translate_core/vl_parser.py`
- `translate_core/vl_extractor.py`
- `translate_core/vl_prompts.py`
- `translate_core/vl_server.py`
- `translate_core/entity_extraction/vl_typed_extractor.py`
- `translate_core/entity_extraction/vl_typed_verifier.py`
- `translate_core/entity_extraction/vl_citation_verifier.py`
- `translate_core/entity_extraction/bilingual_enrichment.py`
- `translate_core/entity_extraction/bilingual_enrichment_batch.py`
- `translate_core/entity_extraction/bilingual_titles.py`
- `tools/vl_smoke.py`
- `tests/test_vl_extractor.py`, `tests/test_vl_extractor_compliance.py`, `tests/test_typed_vl_compliance.py`, `tests/test_vl_parser.py`

**Files (MODIFY — strip VL branches):**
- `translate_core/doc_parser.py:316-353` — remove the `if use_vl and source.suffix.lower() == ".pdf"` branch.
- `ui/workspace.py:365` — confirm the VL-extractor argument was passed as `None`; remove the parameter from the call.

### Agent mix
- Step 7.0: `Explore` (exhaustive callgraph survey — this is a multi-file deletion, blast radius matters)
- Step 7.1: `python-development:python-pro` for the surgery
- Step 7.2: `pythonista-reviewer` for post-deletion cleanliness check
- Skills the coordinator invokes before dispatching: `python-development:python-anti-patterns` (recognise the smells the dying code may have planted nearby), `python-development:python-code-style` (catch broken imports / dead re-exports).
- **Coordinator pause**: this is a destructive phase. Before dispatching Step 7.1, the coordinator confirms with the user that the Step 7.0 Explore report shows no hidden callers.

### Tasks

- [ ] **Step 7.0: Coordinator dispatches `Explore` for the exhaustive callgraph survey.**

Brief: "For each of the following symbols, find every reference in the repo (excluding `.venv/`, `__pycache__/`, and files we plan to delete). Symbol list: `vl_parser`, `vl_extractor`, `vl_prompts`, `vl_server`, `vl_typed_extractor`, `vl_typed_verifier`, `vl_citation_verifier`, `bilingual_enrichment`, `bilingual_enrichment_batch`, `bilingual_titles`. For each call site that survives the deletion (not in a doomed file), report file:line and what would break. Specifically check that `translate_core/doc_parser.py:316-353` is the only `use_vl` consumer and `ui/workspace.py:365` passes None to a VL-extractor argument. Surface anything else."

- [ ] **Step 7.1: Coordinator (after user confirmation) dispatches `python-development:python-pro` for the surgery.**

> Subagent type: `python-development:python-pro`.
> Out of scope: `main.py`, `ui/*.py`, `kg_editor_ui.py`, `import_book.py`, `app_state.py`, `config.py`, `translate_core/llm.py`, `translate_core/glossary.py`, `translate_core/qa.py`, `visualise_kg.py` — EXCEPT `ui/workspace.py:365` is a single in-scope edit (removing a None-pass argument). Be surgical.
> Read first: `docs/parsing_simplification_audit.md` §3 DELETE list + §11; the Step 7.0 Explore report (paste in verbatim).
> Task: execute the deletes in the file list above and the two REDUCE edits in `doc_parser.py` and `ui/workspace.py`. For `doc_parser.py`, the diff should keep the PyMuPDF/text path and drop only the VL branch. For `ui/workspace.py`, the existing argument is already `None`; remove just the argument from the call.
> After each edit, run `.venv/bin/python3 -c "import <touched-module>"` to confirm it still imports.
> Then run the full test suite: `.venv/bin/python3 -m pytest tests/ -x -q`. Expect green; if anything fails because of a missed reference, surface it — do NOT add a shim to paper over it.
> Report: full list of deleted files, the two edits, and pytest output.

- [ ] **Step 7.2: Coordinator dispatches `pythonista-reviewer` for post-delete cleanliness check.**

Brief: "Review the Phase 7 deletion diff. Confirm: no dangling imports, no orphaned re-exports, no commented-out code left behind, no defensive `try: import ... except ImportError: pass` blocks shielding deleted modules, no dead `if False:` blocks. Run `grep -rn 'vl_parser\\|vl_extractor\\|vl_prompts\\|vl_server\\|vl_typed_\\|vl_citation_\\|bilingual_enrichment\\|bilingual_titles' --include='*.py' .` and report any surviving references."

- [ ] **Step 7.3: Coordinator real-data check.**

```bash
.venv/bin/python3 -c "import import_book; print('OK')"     # editor doc-prep
.venv/bin/python3 -c "import main; print('OK')"            # NiceGUI entry (will fail if it tries to start the UI; that is OK — we only want import-time errors)
.venv/bin/python3 -c "from translate_core import doc_parser; print('OK')"
.venv/bin/python3 -c "from translate_core.entity_extraction import smol_extractor; print('OK')"
```
All return `OK` (or controlled-exit if NiceGUI requires runtime setup).

- [ ] **Step 7.4: Commit Phase 7.**

```bash
git add -A
git commit -m "vl: delete VL-era parser/extractor stack; strip use_vl branch from doc_parser"
```

### Phase 7 verification gate

- [ ] All target files are gone (`ls translate_core/vl_*.py` returns no matches; same for vl_typed_*, vl_citation_*, bilingual_enrichment*, bilingual_titles).
- [ ] `.venv/bin/python3 -m pytest tests/ -x -q` is green.
- [ ] `import_book.py` still imports.
- [ ] Live KG unchanged (`data/knowledge.db` byte-identical to `data/knowledge.db.phase6.bak`).
- [ ] Pythonista-reviewer pass clean.

---

## Phase 8 — DELETE `seed_kg.py` + `seed_from_tm` + ontology §6 factories

**Purpose:** these are the structural noise sources. Per ontology §6 they were already out-of-scope; per audit §3 + §10.7–10.8 they account for the 8,455 noise concepts.

**Files (DELETE):**
- `seed_kg.py`

**Files (MODIFY):**
- `translate_core/knowledge_graph.py` — DELETE methods:
  - `seed_from_tm` (`:856-1154`)
  - `add_collocation_node` (`:545-569`)
  - `add_segment_node` (`:571-594`)
  - `add_domain_node` (`:596-604`)

### Agent mix
- Step 8.0: `Explore` (find every caller of the four to-be-deleted factories)
- Step 8.1: `python-development:python-pro` for the surgery
- Step 8.2: `pythonista-reviewer` for post-delete review
- Skills the coordinator invokes before dispatching: `python-development:python-anti-patterns`.
- **Coordinator pause**: destructive phase. Coordinator confirms with the user after the Step 8.0 Explore report before dispatching Step 8.1.

### Tasks

- [ ] **Step 8.0: Coordinator dispatches `Explore` for callgraph survey.**

Brief: "Find every reference (excluding `.venv/`, `__pycache__/`, and files we plan to delete) to: `seed_from_tm`, `add_collocation_node`, `add_segment_node`, `add_domain_node`, and any `from seed_kg` / `import seed_kg`. Expected: only intra-file definitions in `translate_core/knowledge_graph.py` and the `seed_kg.py` script itself. Any other call site is a blocker — report it explicitly with file:line and what it does. Also scan tests/ for tests that depend on these symbols — they must be deleted in lockstep."

- [ ] **Step 8.1: Coordinator (after user confirmation) dispatches `python-development:python-pro` for the surgery.**

> Subagent type: `python-development:python-pro`.
> Out of scope + constraints 9 and 10 (verbatim).
> Read first: `ontology.md` §6, `docs/parsing_simplification_audit.md` §3 DELETE list + §6 + §10.7-10.8, `translate_core/knowledge_graph.py:545-604` and `:856-1154`, full `seed_kg.py`, the Step 8.0 Explore report (paste in).
> Task: delete `seed_kg.py`. In `translate_core/knowledge_graph.py`, delete the four methods listed (and any helper code they ONLY use — verify no other method calls them; if shared helpers exist, leave the helpers alone).
> Run the full test suite. Any test that exercised the deleted methods must also be deleted (e.g. tests under `tests/` that import `seed_from_tm` or instantiate `add_collocation_node`).
> Report: files deleted, KG file diff, test outputs.

- [ ] **Step 8.2: Coordinator dispatches `pythonista-reviewer` for review.**

Brief: "Review the Phase 8 deletion diff. Confirm: no orphaned helpers left in `knowledge_graph.py`; no dead imports; no tests reference deleted symbols. Run `grep -rn 'seed_from_tm\\|add_collocation_node\\|add_segment_node\\|add_domain_node' --include='*.py' .` and report any survivors."

- [ ] **Step 8.3: Coordinator real-data check.**

```bash
.venv/bin/python3 -c "
from translate_core.knowledge_graph import KnowledgeGraph
kg = KnowledgeGraph()
assert not hasattr(kg, 'seed_from_tm'), 'seed_from_tm still present'
assert not hasattr(kg, 'add_collocation_node')
assert not hasattr(kg, 'add_segment_node')
assert not hasattr(kg, 'add_domain_node')
print('OK')
"
```

- [ ] **Step 8.4: Commit Phase 8.**

```bash
git add -A
git commit -m "kg: delete seed_kg + seed_from_tm + ontology §6 out-of-scope factories"
```

### Phase 8 verification gate

- [ ] Live KG byte-identical to `data/knowledge.db.phase7.bak` (this phase only removes WRITERS, not data).
- [ ] No remaining imports of the deleted names anywhere in the repo.
- [ ] Test suite green.

---

## Phase 9 — Drain noise concepts from live KG

**Purpose:** the live KG has 8,455 `concept` nodes with zero definitions and zero lineage edges. With Phase 8 the writers are gone; now we delete the data.

**Files:**
- Create: `scripts/drain_noise_concepts.py` (one-off cleanup)
- Test: `tests/test_drain_noise_concepts.py` (new — exercises the keep/delete predicate on synthetic nodes)

### Agent mix
- Step 9.1: coordinator backs up the KG
- Step 9.2: `python-development:python-pro` for TDD red
- Step 9.3: `python-development:python-pro` for TDD green
- Step 9.4-5: coordinator runs the dry-run + `--apply` (no agent dispatch — just shell)
- Step 9.6: `pythonista-reviewer` for the drain script's correctness before `--apply`
- Skills the coordinator invokes before dispatching: `superpowers:test-driven-development`, `python-development:python-testing-patterns`, `python-development:python-performance-optimization` (touching 8,455 nodes; avoid quadratic walks).
- **Coordinator pause**: destructive KG mutation. Coordinator confirms with the user after the dry-run shows the would-delete count before any `--apply`.

### Tasks

- [ ] **Step 9.1: Coordinator backs up the live KG.**

```bash
cp data/knowledge.db data/knowledge.db.phase9.bak
ls -la data/knowledge.db data/knowledge.db.phase9.bak
```

- [ ] **Step 9.2: Coordinator invokes `superpowers:test-driven-development` + `python-development:python-testing-patterns`, then dispatches `python-development:python-pro` for TDD red.**

> Subagent type: `python-development:python-pro`.
> Out of scope + constraints 9 and 10 (verbatim).
> Read first: `ontology.md` §2.2 + §3.4, `docs/parsing_simplification_audit.md` §6 "Recommendation" item 2.
> Task: write `tests/test_drain_noise_concepts.py` against a function `is_noise_concept(node_id, kg) -> bool`:
> (a) Returns True when concept has `definition==""` AND has no incoming/outgoing edges with relations in `{extends, critiques, redefines, reappropriates, related_to, attributed_to}` AND has no `originating_author` field.
> (b) Returns False when concept has a non-empty `definition`.
> (c) Returns False when concept participates in ANY lineage edge.
> (d) Returns False when concept has `originating_author` (smol provenance).
> (e) Returns False when concept has `instantiates_concept` incoming edges from `term` nodes whose count > N (curator-decided minimum — use 5 as default; this protects highly-attested concepts even without a definition pending later curation).
> Tests must fail (function doesn't exist).
> Report: tests + failures.

- [ ] **Step 9.3: Coordinator invokes `python-development:python-performance-optimization`, then dispatches `python-development:python-pro` for TDD green.**

> Subagent type: `python-development:python-pro`.
> Out of scope + constraints 9 and 10 (verbatim).
> Read first: failing tests; `translate_core/knowledge_graph.py` (factories + delete helpers).
> Task: implement `scripts/drain_noise_concepts.py` with two modes: `--dry-run` (prints `would_delete=N keep=M` per category) and `--apply` (calls a NEW `KnowledgeGraph.remove_concept_node(id)` factory method that cleans up incident edges; do NOT call `kg.G.remove_node` directly — the ontology rule is no raw graph mutation).
> Important: when deleting a concept, also delete its incident `instantiates_concept` edges from term nodes; do NOT leave dangling edges. The factory method enforces this.
> The script must (a) load `data/knowledge.db`, (b) walk concept nodes in a single pass (the live KG has 8,455 concepts — quadratic scans are unacceptable), (c) classify each via `is_noise_concept`, (d) in `--apply` mode call the factory delete helper, then `kg.save()`.
> Verify tests pass; full suite green.
> Report.

- [ ] **Step 9.3a: Coordinator dispatches `pythonista-reviewer` for drain-script review.**

Brief: "Review `scripts/drain_noise_concepts.py` and the new `KnowledgeGraph.remove_concept_node` factory. Confirm: only the factory method touches the graph (no raw `kg.G.remove_node`); incident edges are cleaned exhaustively (no dangling edges possible); the `--apply` path is idempotent; `--dry-run` reports the same set the `--apply` would delete. Single-pass walk only — no nested concept-over-edge loops. Report only high-confidence findings."

- [ ] **Step 9.4: Coordinator runs `--dry-run` and inspects.**

```bash
.venv/bin/python3 scripts/drain_noise_concepts.py --dry-run
```
Expected: `would_delete` ≈ 8,455 (give or take a few hundred if some have term-attestation ≥ 5). Coordinator records the exact numbers in the phase log.

- [ ] **Step 9.5: Coordinator decides whether to `--apply`.**

If `would_delete` >= 95% of concept count, ask user before applying. If user approves:
```bash
.venv/bin/python3 scripts/drain_noise_concepts.py --apply
```

- [ ] **Step 9.6: Coordinator real-data verification.**

```bash
.venv/bin/python3 -c "
from translate_core.knowledge_graph import KnowledgeGraph
from collections import Counter
kg = KnowledgeGraph()
node_types = Counter(d.get('type') for _, d in kg.G.nodes(data=True))
print('NODES:', dict(node_types))
edge_rels = Counter(d.get('relation') for _, _, d in kg.G.edges(data=True))
print('EDGES:', dict(edge_rels))
# Confirm no dangling edges referencing deleted concepts
broken = [(u, v, d) for u, v, d in kg.G.edges(data=True) if u not in kg.G.nodes or v not in kg.G.nodes]
print('broken_edges:', len(broken))
assert not broken
"
```

- [ ] **Step 9.7: Commit Phase 9.**

```bash
git add scripts/drain_noise_concepts.py tests/test_drain_noise_concepts.py
# NOTE: data/knowledge.db is gitignored on purpose. KG state changes are
# captured in docs/superpowers/plans/2026-06-06-phase-log.md (force-add docs
# or use a backup snapshot to track the change externally).
git commit -m "kg: drain ~8455 noise concepts lacking definitions/lineage/provenance"
```

### Phase 9 verification gate

- [ ] `concept` count drops to a small curated number (likely <500).
- [ ] No dangling edges.
- [ ] All non-noise concepts (those with definitions or lineage edges) preserved.

---

## Phase 10 — Wire curator lineage data

**Purpose:** `data/quarantine/_lineage_schools.json` and `_concept_theorists.json` are curated lineage data that has never reached the KG. Promote and ingest.

**Files:**
- Promote: `data/quarantine/_lineage_schools.json` → `data/lineage_schools.json`
- Promote: `data/quarantine/_concept_theorists.json` → `data/concept_theorists.json`
- Create: `scripts/ingest_curator_lineages.py`
- Test: `tests/test_ingest_curator_lineages.py`

### Agent mix
- Step 10.1: coordinator inspects file shapes (no agent needed — single Python read)
- Step 10.3: `python-development:python-pro` for TDD red
- Step 10.4: `python-development:python-pro` for TDD green
- Step 10.5: `pythonista-reviewer` for the ingester review
- Step 10.6: coordinator runs the ingester against a KG copy
- Skills the coordinator invokes before dispatching: `superpowers:test-driven-development`, `python-development:python-testing-patterns`, `python-development:python-error-handling` (missing-agent routing to review).

### Tasks

- [ ] **Step 10.1: Coordinator inspects the curator files.**

```bash
.venv/bin/python3 -c "
import json
sch = json.load(open('data/quarantine/_lineage_schools.json'))
cts = json.load(open('data/quarantine/_concept_theorists.json'))
print('schools entries:', len(sch), 'sample:', list(sch.items() if isinstance(sch,dict) else sch[:3])[:3])
print('theorists entries:', len(cts), 'sample:', list(cts.items() if isinstance(cts,dict) else cts[:3])[:3])
"
```
Coordinator records the actual shape and a sample in the phase log so the subagent can be precisely briefed.

- [ ] **Step 10.2: Promote files.**

```bash
cp data/quarantine/_lineage_schools.json data/lineage_schools.json
cp data/quarantine/_concept_theorists.json data/concept_theorists.json
```

- [ ] **Step 10.3: Coordinator invokes `superpowers:test-driven-development` + `python-development:python-testing-patterns`, then dispatches `python-development:python-pro` for TDD red.**

> Subagent type: `python-development:python-pro`.
> Out of scope + constraints 9 and 10 (verbatim).
> Read first: `ontology.md` §2.2 + §3.3 + §3.4, `docs/parsing_simplification_audit.md` §6 "Recommendation" items 3-4. Read the actual file shapes (the coordinator will paste a 10-line sample of each).
> Task: write `tests/test_ingest_curator_lineages.py` covering:
> (a) After ingest, every entry from `data/concept_theorists.json` has a corresponding `concept` node with non-empty `definition` (the curator's note, even if short) AND an `attributed_to` edge from the concept to the theorist's `agent` node.
> (b) Every relation between two concepts in `data/lineage_schools.json` becomes an edge with `relation ∈ {extends, critiques, redefines, reappropriates, related_to}`. Invalid relations are coerced to `related_to` per `link_concepts_rhizomatic` semantics.
> (c) The ingester is idempotent: running twice does not duplicate edges or concepts.
> (d) The ingester uses `kg.add_concept_node`, `kg.link_concepts_rhizomatic`, `kg.link_attributed_to` exclusively — no raw `G.add_edge`.
> (e) When the curator file references an `agent:` node that doesn't exist in the KG, the ingester routes the entry to the review log with reason `missing_agent` and does NOT create a stub agent.
> (f) (audit checklist §10) Concept labels carry the language they are written in: when the curator file specifies a Slovenian label and an English alias (or any other pair), the ingested `concept` node carries `label`, `label_lang`, `label_translation`, `label_translation_lang` populated from the curator file's declared languages — NOT defaulted to EN/SL.
> Tests fail (script doesn't exist).
> Report: tests + failures.

- [ ] **Step 10.4: Coordinator invokes `python-development:python-error-handling`, then dispatches `python-development:python-pro` for TDD green.**

> Subagent type: `python-development:python-pro`.
> Out of scope + constraints 9 and 10 (verbatim).
> Task: implement `scripts/ingest_curator_lineages.py`. Use ONLY factory methods. The agent must verify the `agent:` node for each theorist exists before linking; if missing, route to the existing review queue with the missing agent ID (do NOT create a stub agent — the COBISS ingest is authoritative for agents).
> Verify tests pass.
> Report.

- [ ] **Step 10.4a: Coordinator dispatches `pythonista-reviewer` for the ingester review.**

Brief: "Review `scripts/ingest_curator_lineages.py` and its tests. Confirm: factory methods only (no raw `G.add_edge`); idempotent; missing-agent routes to existing review queue, not a new file; concept definitions come from the curator notes (not auto-generated); lineage relations are validated against the ontology §3.4 set. Report only high-confidence findings."

- [ ] **Step 10.5: Coordinator runs the ingester against a copy of the KG.**

```bash
cp data/knowledge.db data/knowledge.db.phase10.bak
.venv/bin/python3 scripts/ingest_curator_lineages.py --dry-run
# Inspect report; if reasonable:
.venv/bin/python3 scripts/ingest_curator_lineages.py --apply
```

- [ ] **Step 10.6: Coordinator real-data verification.**

```bash
.venv/bin/python3 -c "
from translate_core.knowledge_graph import KnowledgeGraph
from collections import Counter
kg = KnowledgeGraph()
concepts = [(n,d) for n,d in kg.G.nodes(data=True) if d.get('type')=='concept']
with_def = sum(1 for _,d in concepts if (d.get('definition') or '').strip())
lineage_rels = {'extends','critiques','redefines','reappropriates','related_to'}
lineage_edges = sum(1 for _,_,d in kg.G.edges(data=True) if d.get('relation') in lineage_rels)
attributed = sum(1 for _,_,d in kg.G.edges(data=True) if d.get('relation') == 'attributed_to')
print(f'concepts={len(concepts)} with_def={with_def} lineage_edges={lineage_edges} attributed_to={attributed}')
"
```
Expected: `with_def` > 0 (was 0), `lineage_edges` > 0 (was 0), `attributed_to` > 0.

- [ ] **Step 10.7: Commit Phase 10.**

```bash
git add scripts/ingest_curator_lineages.py tests/test_ingest_curator_lineages.py
# data/ is gitignored. Force-add the curator INPUT files (they are
# authoritative inputs, not generated data), but NOT the KG snapshot:
git add -f data/lineage_schools.json data/concept_theorists.json
git commit -m "kg: wire curator lineage_schools + concept_theorists; concept defs + lineage edges populated"
```

### Phase 10 verification gate

- [ ] Concepts with non-empty `definition` > 0 (the goal is non-zero; exact count depends on curator file).
- [ ] Lineage edges count matches the source file's relation count within rounding.
- [ ] `attributed_to` edges > 0.

---

## Phase 11 — DELETE book-ingest scripts and dependents

**Purpose:** with Phase 5's chokepoint and Phase 6's chronological attribution, the legacy book-ingest scripts (which the audit flagged as ontology violators) are unreachable from the canonical pipeline.

**Files (DELETE):**
- `ingest_book_bibliography.py`
- `ingest_book_footnotes.py`
- `translate_core/entity_extraction/seeded_book_finder.py`
- `translate_core/entity_extraction/bilingual_tm_matcher.py`
- `translate_core/entity_extraction/book_extractor.py`
- `translate_core/citation_collector.py`
- Tests that target these: `tests/test_book_extractor*.py`, `tests/test_seeded_book*.py`, `tests/test_bilingual_tm_matcher*.py`, `tests/test_citation_collector.py`

**SUNSET work remaining for Phase 11** (Phase 4 handled the bulk: smol_extractor alias-write deletion, KG migration, edge rename, reader updates, validator enforcement. Three items remained deferred because they require Phase 4 to be complete AND additional regression testing):

1. **Smol prompt-level `slovenian_edition` JSON-schema key rename.** Phase 4 changed the BUILDER to write the canonical `translation_edition` to the payload, but the prompt template the smol model receives still mentions `slovenian_edition` as the JSON key. Renaming the prompt key risks model-output regression (the LLM may emit subtly different content under the new key name). Phase 11 ships the prompt-level rename after a separate regression-test pass against a representative TM sample. The `# SUNSET: Phase 11` tag placed at the prompt site in Phase 4 marks the exact location.

2. **`CobissEntry.title_en` dataclass field rename.** The COBISS parser dataclass (`translate_core/cobiss_parser.py:43`) calls its `=`-separator second-side field `title_en`. That field name leaks language into an identifier. Renaming it requires updating `cobiss_classifier.py:88-94, 101-110` (which scans `title + " " + title_en` for keywords) and any other reader of the dataclass attribute. Phase 11 ships the rename after Phase 4 has confirmed nobody else outside the COBISS layer depends on the field name.

3. **Any new `# SUNSET: Phase 11` tags Phase 4 placed for items that legitimately needed book-ingest scripts already gone before they could be cleaned up.** Coordinator runs `grep -rn '# SUNSET: Phase 11' --include='*.py' .` at the start of Phase 11 and triages each surviving tag. If any tag's prerequisite (book-ingest scripts deleted) is now satisfied, the sunset removal lands here.

### Agent mix
- Step 11.0: `Explore` (final callgraph survey for the legacy book-ingest stack)
- Step 11.1: `python-development:python-pro` for the surgery
- Step 11.2: `pythonista-reviewer` for the final cleanliness check
- Skills the coordinator invokes before dispatching: `python-development:python-anti-patterns`.
- **Coordinator pause**: destructive phase. Coordinator confirms with the user after the Step 11.0 Explore report before Step 11.1.

### Tasks

- [ ] **Step 11.0: Coordinator dispatches `Explore` for callgraph survey.**

Brief: "For each of these symbols/files, find every surviving reference: `ingest_book_bibliography`, `ingest_book_footnotes`, `seeded_book_finder`, `bilingual_tm_matcher`, `book_extractor`, `citation_collector`. Report file:line and what would break. Expected: zero callers in in-scope code because Phase 5 and Phase 6 retired the call sites. If anything surfaces, STOP."

- [ ] **Step 11.1: Coordinator (after user confirmation) dispatches `python-development:python-pro` for the book-ingest deletion.**

> Subagent type: `python-development:python-pro`.
> Out of scope + constraints 9 and 10 (verbatim).
> Read first: `docs/parsing_simplification_audit.md` §10.5 / §10.6 / §3 DELETE list / §11; the Step 11.0 Explore report (paste in).
> Task:
>   - Delete the listed files. Any test that imports a deleted symbol gets deleted with it; do not invent shims.
>   - Run the test suite.
> Report: deleted files, test outputs.

- [ ] **Step 11.2: Coordinator dispatches `python-development:python-pro` for the smol prompt-level SUNSET work.**

> Subagent type: `python-development:python-pro`.
> Out of scope + constraints 9, 10, 7 (verbatim).
> Read first: the `# SUNSET: Phase 11` tag placed by Phase 4 at the smol prompt-level `slovenian_edition` JSON-schema reference; `translate_core/entity_extraction/smol_extractor.py` prompt template (the JSON schema documentation given to the model).
> Task: rename the prompt-level `slovenian_edition` key to `translation_edition` in the prompt template. Update the builder to read the new key (the builder may need a fallback for transitional smol outputs that still emit the old key during the model's adjustment — keep that fallback for ONE phase only, marked `# TRANSITION: remove after one verified smol re-run`). Run a regression test: re-extract a small sample of representative TM segments via smol and confirm the output's content (titles, publishers, etc.) is unchanged from before the prompt rename. The keys CHANGE; the values must not.
> Report: prompt diff, builder diff, regression-test sample showing before/after content unchanged.

- [ ] **Step 11.3: Coordinator dispatches `python-development:python-pro` for the `CobissEntry.title_en` field rename.**

> Subagent type: `python-development:python-pro`.
> Out of scope: editor surfaces. In scope: `translate_core/cobiss_parser.py`, `translate_core/cobiss_classifier.py`, `scripts/ingest_personal_bibliography.py`, any test reading `CobissEntry.title_en`.
> Task: rename `CobissEntry.title_en` field to a neutral name (suggest `title_second_side` — reflects the `=`-separator structural role, not a language). Update the parser to populate the renamed field. Update `cobiss_classifier.py:88-94, 101-110` (the `title + " " + title_en` keyword scan sites). Update `scripts/ingest_personal_bibliography.py` (the kwarg-building site from Phase 4 that reads `entry.title_en`). Update tests.
> Verify the COBISS ingest still produces the same KG output (the field rename is name-only; values unchanged).
> Report: per-file diff, test outputs.

- [ ] **Step 11.4: Coordinator dispatches `pythonista-reviewer` for the final cleanliness check.**

Brief: "Review the entire Phase 11 diff (book-ingest deletion + smol prompt rename + CobissEntry field rename). Confirm: (a) no dangling imports, no orphaned helpers; (b) `grep -rn 'ingest_book_bibliography\\|ingest_book_footnotes\\|seeded_book_finder\\|bilingual_tm_matcher\\|book_extractor\\|citation_collector' --include='*.py' .` returns no survivors; (c) `grep -rn 'slovenian_edition' --include='*.py' .` returns either zero hits OR only the one-phase transitional fallback in the smol builder tagged `# TRANSITION`; (d) `grep -rn '\\.title_en' --include='*.py' .` returns no surviving reads of the renamed field; (e) the smol regression-test sample shows content-equivalent output before/after the prompt rename. Report only high-confidence findings."

- [ ] **Step 11.5: Coordinator real-data check.**

```bash
.venv/bin/python3 -m pytest tests/ -x -q
.venv/bin/python3 run_entity_extraction.py --help 2>&1 | head -20    # confirms the entry-point still parses
.venv/bin/python3 scripts/validate_kg.py 2>&1 | tail -10   # post-Phase-4 validator should still pass
```

- [ ] **Step 11.6: Commit Phase 11.**

```bash
git add -A
git commit -m "phase11: delete book-ingest stack; smol prompt translation_edition rename; CobissEntry.title_en field rename"
```

### Phase 11 verification gate

- [ ] No deleted symbol referenced anywhere.
- [ ] `# SUNSET: Phase 11` tags placed in Phase 4 are all addressed (either deleted by the work above or explicitly deferred with a reason recorded in the phase log).
- [ ] `.venv/bin/python3 -m pytest tests/ -x -q` green.
- [ ] `validate_kg.py` clean on the live KG.
- [ ] `run_entity_extraction.py --help` still works.
- [ ] Smol regression-test sample confirms content unchanged after prompt rename.
- [ ] Pythonista-reviewer pass clean.

---

## Phase 12 — End-to-end verification

**Purpose:** run the smol entity ingest end-to-end on a copy of the KG and diff against the post-Phase-10 baseline. Confirm the system produces ontology-clean output and no regressions.

**Caveat on smol payload shape — read this before running:** The current `data/smol_entities_map/smol_extractions.json` was produced by smol jobs that did NOT emit `t_index` (Phase 6 introduces the requirement; the consumer falls back to `seg_idx`). A true end-to-end pass against Phase 6's chronological-anchor improvements requires re-dispatching smol extraction with the new payload shape. Decide before this phase:
  - **Option A (recommended): in-scope re-run.** The coordinator dispatches the external smol extraction harness (OMP / DeepSeek-flash) with `t_index` enabled, regenerates `data/smol_entities_map/smol_extractions.json`, then runs Phase 12. This is the only path to a real-world verification.
  - **Option B: ship Phase 12 against stale smol data.** Verification gate is partial. Container-attribution improvements from Phase 6 are technically wired but not exercised end-to-end. Mark in the phase log; schedule the smol re-run as a follow-up.

The coordinator picks A or B and records the choice in `2026-06-06-phase-log.md` before Step 12.1.

### Agent mix
- Step 12.1: coordinator snapshots (no agent)
- Step 12.2: `python-development:python-pro` for the integration-run harness (only needed if `run_entity_extraction.py` doesn't already support `--kg-path`)
- Step 12.3: coordinator runs the delta script
- Step 12.4: `feature-dev:code-explorer` for the random-sample spot-checks (it can pick 5 nodes and walk their edges far faster than the coordinator inline)
- Step 12.5: coordinator runs `validate_kg.py`
- Step 12.6: coordinator writes the phase log
- Step 12.7: coordinator invokes `superpowers:finishing-a-development-branch` and presents user with merge/PR options
- Skills the coordinator invokes before dispatching: `python-development:python-resource-management` (test-run KG copy handling), `superpowers:verification-before-completion` (final sanity gate).
- **Coordinator pause**: before Step 12.2 the coordinator confirms with the user that Option A vs B has been chosen and recorded.

### Tasks

- [ ] **Step 12.1: Coordinator snapshots the live KG and prepares a test copy.**

```bash
cp data/knowledge.db data/knowledge.db.phase12.bak
cp data/knowledge.db /tmp/knowledge.db.test_run
# Point a test config at the copy, OR run ingest in --dry-mode that writes to the copy.
```

- [ ] **Step 12.2: Run full smol ingest.**

```bash
.venv/bin/python3 run_entity_extraction.py --kg-path /tmp/knowledge.db.test_run --no-fallback
```
(If the CLI doesn't expose `--kg-path`, the coordinator dispatches `python-development:python-pro` for the small flag addition — brief: "Add a `--kg-path` flag to `run_entity_extraction.py` that, when present, makes the script use that file as the KG instead of the default. Default behaviour unchanged. Tests: a single smoke test that calls the script with `--kg-path /tmp/test.db` and confirms the live KG isn't touched." OR temporarily moves the live KG aside and runs against a copy if that's faster.)

- [ ] **Step 12.3: Diff the test KG against the post-Phase-10 baseline.**

```bash
.venv/bin/python3 -c "
import networkx as nx
from translate_core.knowledge_graph import KnowledgeGraph
def load(p):
    import config
    config.KG_PATH = p  # if the loader uses module-level config
    return KnowledgeGraph()
kg_before = load('data/knowledge.db.phase10.bak')
kg_after  = load('/tmp/knowledge.db.test_run')
from collections import Counter
def stats(kg):
    nt = Counter(d.get('type') for _,d in kg.G.nodes(data=True))
    er = Counter(d.get('relation') for _,_,d in kg.G.edges(data=True))
    return nt, er
nb, eb = stats(kg_before); na, ea = stats(kg_after)
print('NODES delta:', {k: na.get(k,0) - nb.get(k,0) for k in set(nb)|set(na)})
print('EDGES delta:', {k: ea.get(k,0) - eb.get(k,0) for k in set(eb)|set(ea)})
"
```
Coordinator inspects the deltas: net node growth from smol additions, no negative changes for curated concepts or lineage edges, no new node types, no new edge relations.

- [ ] **Step 12.4: Coordinator dispatches `feature-dev:code-explorer` for random-sample spot-checks.**

Brief: "Load `/tmp/knowledge.db.test_run`. Pick 5 cited_work source_text nodes at random and report for each: `provenance` value (must be in `{tm_smol, doc_pair, cobiss_personal}`), `project_type` (must be typed, NOT `cited_work` fallback), the existence of a `cited_in` edge to a container node, and whether the canonical bilingual title fields (`title_orig`+`title_translation`+`orig_lang`+`translation_lang`) are populated when the entry's TM origin has both languages of its declared `(source_lang, target_lang)` pair available. 'Both languages' means the pair declared by the entry's origin — NOT 'EN and SL'. If the corpus by this point contains a non-EN/SL origin (e.g. `hr-sl.tmx`), include at least one cited_work from that origin in the sample. Pick 5 container source_text nodes (project_type in `{book_translation, article_translation, festival_programme, exhibition_catalogue}`) and report: canonical bilingual title pair populated, `translated_by` edge present pointing to an agent node. Report findings as a tabular summary."

- [ ] **Step 12.5: Ontology validator + language-neutrality check.**

```bash
.venv/bin/python3 scripts/validate_kg.py /tmp/knowledge.db.test_run 2>&1 | tail -40
```
Expected: zero violations.

Additional language-neutrality assertion (audit checklist §12):
```bash
.venv/bin/python3 -c "
from translate_core.knowledge_graph import KnowledgeGraph
kg = KnowledgeGraph(db_path='/tmp/knowledge.db.test_run')
violations = []
for nid, d in kg.G.nodes(data=True):
    if d.get('type') != 'source_text': continue
    if d.get('title_translation') and not (d.get('orig_lang') and d.get('translation_lang')):
        violations.append((nid, 'has title_translation but missing orig_lang/translation_lang'))
    # After Phase 4 migration, NO node should carry title_en / title_sl /
    # slovenian_edition. The migration strips them after copying values into
    # the canonical fields. Any survivor is a migration miss.
    if d.get('title_en') or d.get('title_sl') or d.get('slovenian_edition'):
        violations.append((nid, 'legacy field survived Phase 4 migration'))
print(f'language_neutrality_violations: {len(violations)}')
for v in violations[:10]: print(' ', v)
assert not violations, 'language-neutrality violations found'
"
```
Expected: zero violations.

- [ ] **Step 12.6: Final report.**

Coordinator writes `docs/superpowers/plans/2026-06-06-phase-log.md` summarizing per-phase counts, before/after deltas, any deferred items.

- [ ] **Step 12.7: Decide on merge / next steps.**

Per superpowers:finishing-a-development-branch, present the user with the options: merge to main, open PR, leave on branch, etc.

### Phase 12 verification gate

- [ ] Smol ingest runs end-to-end without errors.
- [ ] Validator reports zero violations.
- [ ] Spot-checks confirm bilingual + container-attached cited works and bilingual containers.
- [ ] No new node types or edge relations introduced.

---

## Coordinator's standing rules (every phase)

1. Before dispatching any subagent, read the relevant audit section IN FULL and include it (or a precise quote) in the prompt.
2. Every subagent prompt includes the **OUT OF SCOPE** list: `main.py`, `ui/*.py`, `kg_editor_ui.py`, `import_book.py`, `app_state.py`, `config.py`, `translate_core/llm.py`, `translate_core/glossary.py`, `translate_core/qa.py`, `visualise_kg.py`, the TM search APIs.
3. Every subagent prompt includes the **ontology reference**: `/Users/bel/CascadeProjects/sl_translator/ontology.md`.
4. Every subagent prompt names its verification gate explicitly — the gate is the same one in the phase's "verification gate" section above.
5. After the subagent reports, the coordinator runs the verification gate commands directly. **Subagent self-assessment is not acceptance.** This is the rule the user invoked when they said "you fucked this up several times".
6. If a verification fails, the coordinator does NOT advance the phase; either re-dispatches with the failure diagnosis or reports to the user.
7. After every phase, the coordinator commits with the message format shown. Each commit is atomic and revertable.
8. No phase is "skipped because the smoke test passed". Real-data verification is the only verification that counts.
9. If the audit and the live code disagree, trust the live code (it is more recent). Re-read the relevant section and report the discrepancy.
10. **Every subagent prompt restates constraints 9 and 10** (language neutrality + bibliography bright line) verbatim.

## Agent assignments per phase

Dispatch the agent type that matches the WORK, not the same generic agent for everything. Before each TDD phase, the coordinator primes themselves with the `superpowers:test-driven-development` skill so the dispatch carries TDD discipline.

| Phase | Step type | Agent type |
|---|---|---|
| All phases | Pre-flight grep / impact survey | `Explore` |
| 1, 3, 4 (TDD-heavy) | Write failing tests (TDD red) | `python-development:python-pro` (priming skill: `superpowers:test-driven-development`) |
| 1, 3, 4 (TDD-heavy) | Implement to pass (TDD green) | `python-development:python-pro` |
| **1B** (lang-neutrality remediation) | Impact survey | `Explore` |
| **1B** | Compat-shim design | `feature-dev:code-architect` |
| **1B** | TDD red + green | `python-development:python-pro` |
| 5, 6 (require design) | Design step (algorithm / dispatcher) | `feature-dev:code-architect` |
| 5, 6 (require design) | Implement design | `python-development:python-pro` |
| 7, 8, 11 (deletions) | Pre-flight callgraph survey | `Explore` |
| 7, 8, 11 (deletions) | Surgery | `python-development:python-pro` |
| 9, 10 (KG data work) | Implement | `python-development:python-pro` |
| 12 (end-to-end) | Integration harness | `python-development:python-pro` |
| Between green and commit, every substantive phase | Independent review pass | `pythonista-reviewer` |

Rule of thumb: dispatch `Explore` whenever the coordinator would otherwise run more than two greps inline; the protected-context savings are real on large phases.

---

## Appendix: subagent dispatch template

For every dispatch the coordinator uses this skeleton. **Pick the subagent type from the per-phase Agent mix subsection — not always `python-pro`.** Allowed types in this work: `Explore` (read-only search), `feature-dev:code-architect` (design), `python-development:python-pro` (implementation), `pythonista-reviewer` / `pythonista-reviewer` (review), `feature-dev:code-explorer` (deep walks).

```
Subagent type: <pick from the phase's Agent mix subsection>
Working directory: /Users/bel/CascadeProjects/sl_translator

OUT OF SCOPE — DO NOT modify any of:
  main.py, ui/*.py, kg_editor_ui.py, import_book.py, app_state.py,
  config.py, translate_core/llm.py, translate_core/glossary.py,
  translate_core/qa.py, visualise_kg.py,
  TranslationMemory.lookup_fuzzy / .search_concordance / .search_prefix.

Authoritative ontology: /Users/bel/CascadeProjects/sl_translator/ontology.md
  - Six node types, thirteen edge relations, no extensions.
  - The only authorised KG writers are KnowledgeGraph factory methods
    in translate_core/knowledge_graph.py. NEVER call kg.G.add_node /
    kg.G.add_edge directly.

Constraint 9 — language neutrality (verbatim from plan):
  No hardcoded "sl"/"en" string literals as if they were the only
  languages. No default orig_lang="sl" fallback. Direction comes from
  evidence in the data; absent evidence → route to review with reason
  direction_undetermined.

Constraint 10 — bibliography bright line (verbatim from plan):
  Personal/COBISS bibliography produces containers (translated_by) and
  self-authored records (written_by). Book bibliography (deleted in
  Phase 11) produces cited works (cited_in). NEVER conflate.

Audit context for THIS task: <paste the relevant audit section verbatim>

Task: <bite-sized step>

Verification gate (you must pass before reporting done):
  <commands + expected output>

Reporting requirements:
  - Paste every file you created or modified, full content or unified diff.
  - Paste the verification command output.
  - List any deferred / surprising findings — do not silently fix things
    outside this task's scope.
```
