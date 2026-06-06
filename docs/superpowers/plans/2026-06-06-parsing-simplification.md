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
7. **Bilingual fields are canonical per ontology §2.4.2.** Use `title_orig`+`title_translation`+`orig_lang`+`translation_lang` (canonical) OR `title_en`+`title_sl`+`slovenian_edition` (citation-typed records only, per §2.4.2 second paragraph). No flat `publisher_en` / `publisher_sl`.
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
Phase 4 (COBISS bilingual title encoding)
   ↓
Phase 5 (container/cited-work routing chokepoint)
   ↓
Phase 6 (chronological-anchor container attribution)     ── depends on Phase 1
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
            'title_bilingual': bool(ent.get('title_en') and ent.get('title_sl')) or bool(ent.get('title_orig') and ent.get('title_translation')),
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

## Phase 4 — COBISS bilingual title encoding (personal bibliography only)

**Scope reminder — the bibliography bright line (constraint 10):** Phase 4 touches ONLY `scripts/ingest_personal_bibliography.py` and the records it emits from the translator's personal COBISS bibliography. It does NOT touch any code that ingests book bibliographies (those are out of scope for this phase and deleted in Phase 11). The COBISS export contains both kinds of records — works the translator translated (containers) AND works the translator authored himself (self-authored source_texts). The bilingual encoding applies uniformly to both, because both come from the same personal-bibliography source.

**Purpose:** `scripts/ingest_personal_bibliography.py` currently sets only `title=cobiss_entry.title` and (legacy) `title_en=cobiss_entry.title_en` on the nodes it produces. Per ontology §2.4.2 the canonical encoding on these nodes is `title_orig` + `title_translation` + `orig_lang` + `translation_lang`. The `title` plain-field is preserved for UI search and carries the original-language title.

**Language neutrality (constraint 9):** Direction must be detected from EVIDENCE in the data (explicit translation markers in the COBISS raw_text, role evidence on the translator agent, TM-segment `xml:lang` when an origin matches). No hardcoded `orig_lang="sl"` fallback. When evidence is absent, route the record to the review queue with reason `direction_undetermined` and do NOT write `orig_lang` at all.

**Files:**
- Modify: `scripts/ingest_personal_bibliography.py` (record-construction site; both container and self-authored branches)
- Test: `tests/test_ingest_personal_bibliography_bilingual.py` (new)
- DO NOT MODIFY: `translate_core/cobiss_parser.py` (its `CobissEntry` field names `title` / `title_en` are inputs we READ; renaming them is out of scope) or `translate_core/cobiss_classifier.py`.

### Tasks

- [ ] **Step 4.0: Coordinator dispatches `Explore` for a pre-flight callgraph survey.**

Question to answer: "Which code reads `title_en` (as opposed to `title_orig` / `title`) on `source_text` nodes? Is each reader safe if `title_en` is no longer written by `scripts/ingest_personal_bibliography.py`?" The coordinator pastes the Explore findings into Step 4.2's prompt.

- [ ] **Step 4.1: Coordinator primes with `superpowers:test-driven-development`, then dispatches `python-development:python-pro` for TDD red.**

> Subagent type: `python-development:python-pro`.
> Out of scope: per the plan's "OUT OF SCOPE" list + constraint 10 (do NOT modify cobiss_parser/classifier).
> Read first: `ontology.md` §2.4 + §2.4.1 + §2.4.2; this plan's constraints 9 (language neutrality) and 10 (bibliography bright line); `docs/parsing_simplification_audit.md` §7 and §5; `translate_core/cobiss_parser.py` lines 35–65 (`CobissEntry` shape — the field names `title` and `title_en` are legacy; treat `title_en` as "the secondary-language side of a bilingual `=`-separated COBISS title", NOT specifically English); `translate_core/cobiss_classifier.py` (full file — see what direction/role evidence the classifier surfaces); `scripts/ingest_personal_bibliography.py` (full file — the record-construction site you will test).
> Task: write `tests/test_ingest_personal_bibliography_bilingual.py` (pytest function-style). Use `tmp_path`-scoped fresh `KnowledgeGraph` instances; do NOT touch the live KG. Cover the cases below. Use language-agnostic example values — pick any plausible language pair OTHER than `sl`+`en` in at least three of the test cases (so SL/EN-coincident bugs surface). Example pairs to mix in: `("hr", "sl")`, `("en", "sl")`, `("de", "fr")` (hypothetical), `("sl", "en")`.
>
> (a) **explicit translation marker present.** A `CobissEntry` whose `raw_text` contains an explicit translation marker (e.g. Slovenian: `"prevedel iz hrvaškega"` — "translated from Croatian") with bilingual title sides, plus a translator role on the relevant agent. The ingested container node carries `title_orig` = the source-language side, `title_translation` = the destination-language side, `orig_lang` and `translation_lang` populated with the detected ISO 639-1 codes.
> (b) **plain `title` field on the node equals `title_orig`** (back-compat for UI search).
> (c) **reversed direction.** Same as (a) but the marker indicates the opposite direction. Assignments are inverted.
> (d) **unilingual entry.** `title_en` empty, only `title` set. The node has `title_orig` and possibly `orig_lang` (if a direction signal exists for it); `title_translation` and `translation_lang` keys are ABSENT from the node (not empty strings — absent).
> (e) **direction undetermined → review.** A bilingual `CobissEntry` (both `title` and `title_en` non-empty) with NO direction signal in `raw_text` and no translator-role evidence. The ingester does NOT write the container; instead it appends to the review queue (locate the current review-queue write path by reading the script and ingest layer — if multiple candidates exist, note them and surface; pick the one the script currently uses) with reason `direction_undetermined` and the original entry's identifying fields.
> (f) **idempotency.** Running the ingest twice on the same entry does not duplicate nodes or alter bilingual fields.
> (g) **self-authored record (not a container).** When the classifier flags the entry as Belina-as-author (NOT translator) — `project_type ∈ {book, magazine_article, ...}` — the SAME bilingual encoding (`title_orig`, `title_translation`, `orig_lang`, `translation_lang`) must apply to that record too. This is still the personal-bibliography pipeline; it is NOT a "book-bibliography cited work" (those come from a different pipeline and are out of scope for Phase 4). The `cited_in` edge that book-bibliography cited works carry MUST NOT be wired here; self-authored records use `written_by`.
> (h) **no `title_en` field on the node.** Phase 4 transitions to the canonical encoding. Assert the resulting node does NOT have a `title_en` key at all (or — if the coordinator's Step 4.0 Explore report shows readers that would break — explicitly assert that `title_en` is populated AS WELL during a transition window, and surface the migration as a follow-up).
>
> Verify all tests FAIL against the current implementation. Report: test code + failure output.

- [ ] **Step 4.2: Coordinator dispatches `python-development:python-pro` for TDD green.**

> Subagent type: `python-development:python-pro`.
> Out of scope + constraints 9 and 10 (verbatim).
> Read first: the failing tests; the Step 4.0 Explore report (paste in); current `scripts/ingest_personal_bibliography.py`; how the script currently routes to review (find the actual write target).
> Task: introduce a helper `_detect_direction(entry: CobissEntry, classifier_result) -> tuple[str | None, str | None]` that returns `(orig_lang, translation_lang)` based on evidence only. Implementation:
>   1. Build a small mapping `_LANG_MARKER_TO_CODE` that maps explicit translation phrases to ISO 639-1 codes. Read a sample of `data/personal bibliography/bibliography_belina.txt` first to see what phrases actually occur — only include phrases observed there. The mapping is a small dict, NOT a hardcoded if/else.
>   2. Consult the classifier's role/agent output for the translator's working direction when raw_text markers are inconclusive.
>   3. If neither signal is conclusive, return `(None, None)` — do NOT default-guess.
> At the record-construction site, replace the current `title=`/`title_en=` assignments with:
> - `title_orig` = the COBISS `title` field (source-language side)
> - `title_translation` = the COBISS `title_en` field, only when non-empty (destination-language side)
> - `orig_lang`, `translation_lang` = from `_detect_direction` (omit keys when value is `None`)
> - plain `title` = `title_orig` (UI compatibility)
> - DO NOT write `title_en` going forward, UNLESS the Step 4.0 Explore report identified readers that would break. In that case keep `title_en` as a transitional duplicate and note for follow-up removal.
> When `_detect_direction` returns `(None, None)` AND the entry is bilingual, route to the existing review queue with reason `direction_undetermined`; skip writing the node.
> Apply the SAME canonical encoding to BOTH the container branch (Belina-as-translator) and the self-authored branch (Belina-as-author). Constraint 10: BOTH come from the same personal-bibliography source.
> DO NOT introduce a `cited_in` edge anywhere in this script — self-authored records use `written_by`, containers use `translated_by`.
> Verify new tests pass; existing tests pass; existing live KG container count (`book_translation=93` per audit baseline) is unchanged after a dry re-ingest (script is idempotent on existing nodes by id).
> Report: full diff, the `_LANG_MARKER_TO_CODE` table you used + which markers were observed in the bibliography file, test outputs.

- [ ] **Step 4.3: Coordinator dispatches `feature-dev:code-reviewer` for an independent review pass.**

The reviewer's brief: confirm constraints 9 and 10 are upheld in the diff; confirm no hardcoded language pair, no `cited_in` edge, no conflation of self-authored records with book-bibliography cited works. Confirm the review-queue routing matches the existing mechanism (no new ad-hoc file).

- [ ] **Step 4.4: Coordinator real-data dry-run.**

```bash
cp data/knowledge.db data/knowledge.db.phase4.bak
# Use a tmp copy so the live KG is never mutated by a dry-run:
cp data/knowledge.db /tmp/knowledge.db.phase4.dryrun
KG_DB_PATH=/tmp/knowledge.db.phase4.dryrun .venv/bin/python3 scripts/ingest_personal_bibliography.py 2>&1 | tail -40
# If the script doesn't honour KG_DB_PATH, use a different mechanism (the
# script may expose a --kg-path flag or it may always read config.KG_DB);
# never mutate data/knowledge.db.
```

```bash
.venv/bin/python3 -c "
from translate_core.knowledge_graph import KnowledgeGraph
kg = KnowledgeGraph(db_path='/tmp/knowledge.db.phase4.dryrun')
container_types = {'book_translation','article_translation','festival_programme','exhibition_catalogue'}
selfauth_types = {'book','magazine_article','journal_article','newspaper_article','book_chapter'}
def counts(kg, types):
    n=bilingual=direction=0
    for _, d in kg.G.nodes(data=True):
        if d.get('type')!='source_text' or d.get('project_type') not in types: continue
        n += 1
        if d.get('title_orig') and d.get('title_translation'): bilingual += 1
        if d.get('orig_lang'): direction += 1
    return n, bilingual, direction
nc, bc, dc = counts(kg, container_types)
ns, bs, ds = counts(kg, selfauth_types)
print(f'containers: n={nc} bilingual={bc} with_orig_lang={dc}')
print(f'self_authored: n={ns} bilingual={bs} with_orig_lang={ds}')
"
```
Expected: `bilingual` count rises substantially for both kinds from the baseline. `with_orig_lang` should equal `bilingual` (every bilingual node has a known direction; unilingual nodes may have it or not, depending on signal).

- [ ] **Step 4.5: Commit Phase 4.**

```bash
git add scripts/ingest_personal_bibliography.py tests/test_ingest_personal_bibliography_bilingual.py
git commit -m "cobiss ingest: canonical title_orig/title_translation; language-neutral direction"
```

### Phase 4 verification gate

- [ ] New tests pass (including the direction-undetermined route-to-review case).
- [ ] No SL/EN hardcoding in the new code (grep the diff for `"sl"` / `"en"` string literals; each occurrence must be inside a data table or detection helper, NEVER inside a conditional branch).
- [ ] Real-data: container nodes with bilingual fields > 50% of bilingual-eligible container count (entries that have both `title` AND `title_en`).
- [ ] Same applied to self-authored records (the bilingual-encoding rule applies uniformly per constraint 10).
- [ ] Existing UI search by `title` still finds at least one expected container (the coordinator runs a generic search through `ui/kg_search.py`'s helpers, NOT a hardcoded "Foucault" check).
- [ ] No `cited_in` edge wired by this script (grep the diff).
- [ ] Reviewer pass clean.

---

## Phase 5 — Container vs. cited-work routing chokepoint

**Purpose:** the audit §5 calls for a single dispatcher inside `kg_ingest_entities.py` that decides `kind ∈ {container, cited}` from `record["source"]["provenance"]` and rejects mismatches (e.g., a `translated_work` record without `provenance="cobiss_personal"`).

**Files:**
- Modify: `translate_core/kg_ingest_entities.py:514-547` (current translated_work routing; tighten the gate) and the `write_to_kg` entry point that fan-outs by record kind.
- Modify: `scripts/ingest_personal_bibliography.py` to stamp `provenance="cobiss_personal"` on every record it emits.
- Modify: `translate_core/entity_extraction/smol_extractor.py` to stamp `provenance="tm_smol"` on every record it emits.
- Modify: `translate_core/document_pair_pipeline.py` to stamp `provenance="doc_pair"`.
- Test: `tests/test_kg_ingest_routing.py` (new)

### Tasks

- [ ] **Step 5.1: Coordinator dispatches subagent for TDD red.**

> Subagent type: `python-development:python-pro`.
> Read first: `ontology.md` §2.4.1 + §3.2, `docs/parsing_simplification_audit.md` §5, `translate_core/kg_ingest_entities.py:1-100, 463-780`, `translate_core/entity_extraction/smol_extractor.py:454-569`.
> Task: write `tests/test_kg_ingest_routing.py` covering:
> (a) A record with `kind="translated_work"` and `provenance="cobiss_personal"` is accepted and writes a container node with `translated_by` edge.
> (b) A record with `kind="translated_work"` and `provenance="tm_smol"` is REJECTED to the review queue (NOT silently coerced to `book_translation`).
> (c) A record with `kind="cited_work"` and `provenance ∈ {tm_smol, doc_pair, book_bibliography}` is accepted and writes a typed `source_text` with `cited_in` to its `container_work_id`.
> (d) A record with `kind="cited_work"` whose `container_work_id` does NOT resolve to an existing container node routes to review with reason `container_not_found`.
> Test fails against current code.
> Report: test code + failures.

- [ ] **Step 5.2: Coordinator dispatches subagent for TDD green.**

> Subagent type: `python-development:python-pro`.
> Read first: the failing tests; current routing in `kg_ingest_entities.py`.
> Task: implement a new private function `_route_record(record) -> tuple[str, dict]` returning `("direct", routed_record)`, `("review", {"reason": ...})`, or `("reject", {...})`. Replace the existing `if kind == "translated_work"` block with delegation to this router. Reject paths must route to the existing review-queue mechanism (cite the actual review-queue write location; if it's `data/extraction_review.json` or similar, append there).
> Stamp `provenance` at each producer:
> - `scripts/ingest_personal_bibliography.py` → `"cobiss_personal"`
> - `translate_core/entity_extraction/smol_extractor.py` → `"tm_smol"`
> - `translate_core/document_pair_pipeline.py` → `"doc_pair"`
> Verify new tests pass and existing tests pass.
> Report: diff per file, tests passing.

- [ ] **Step 5.3: Coordinator real-data check (dry-run).**

```bash
cp data/knowledge.db data/knowledge.db.phase5.bak
# Run the smol-side ingest harness with the new router. The subagent must
# expose either an `--inspect` flag on run_entity_extraction.py or a function
# `simulate_routing(records) -> dict` that returns counts without writing.
.venv/bin/python3 run_entity_extraction.py --inspect 2>&1 | grep -E "router|direct|review|reject" | head -20
```
The coordinator examines: are any `translated_work` records arriving from non-COBISS provenance routed to review (the seeded-book bug class)? Expected: at least a handful of such routings logged — proves the gate fires.

- [ ] **Step 5.4: Commit Phase 5.**

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
- [ ] Existing `book_translation` count on live KG unchanged (we are gating new writes, not retroactively deleting).

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

### Tasks

- [ ] **Step 6.1: Coordinator promotes the curator file.**

```bash
cp data/quarantine/_segment_title_attribution.json data/segment_title_attribution.json
# data/ is gitignored — force-add this curator INPUT file:
git add -f data/segment_title_attribution.json
```
No commit yet — bundled with Step 6.6.

- [ ] **Step 6.2: Coordinator dispatches subagent for TDD red.**

> Subagent type: `python-development:python-pro`.
> Read first: `docs/parsing_simplification_audit.md` §4 (entire section, including "Recommended approach", "Files that should own this", "Existing naive-index slice sites"). Then `ontology.md` §2.4.1 + §3.2. Then `translate_core/tm.py` (post-Phase 1, exposes `t_index`), `data/segment_title_attribution.json` (top of file for shape), `scripts/build_segment_attribution.py:1-80`.
> Task: write `tests/test_container_attribution.py` covering:
> (a) Given a sorted list of `(origin, t_index, container_id)` anchors and a stream of TM entries in `t_index` order, `attribute_segments_to_containers(anchors, tm_entries)` returns a dict `{(origin, t_index): container_id}` where each segment inherits from the most recent anchor whose `t_index <= seg.t_index` and whose `origin` matches. No `MAX_GAP` heuristic.
> (b) When two anchors for the same origin disagree (curator file says X for `t_index=400`, n-gram says Y for `t_index=400`), the conflict is queued to a review list returned from the function; the segment is NOT auto-attributed.
> (c) Segments earlier than any anchor for their origin are returned with `container_id=None` and a separate "unanchored" list, NOT auto-attributed to anything.
> (d) The function operates on `t_index` only; passing `raw_index` should not silently coerce.
> Tests must fail against current code (the function does not exist).
> Report: tests + failure output.

- [ ] **Step 6.3: Coordinator dispatches subagent for TDD green.**

> Subagent type: `python-development:python-pro`.
> Read first: the failing tests; current `seeded_book_finder.py` (to understand what we replace); audit §4 "Recommended approach" primary + fallback.
> Task: implement `translate_core/container_attribution.py` with `attribute_segments_to_containers(anchors, tm_entries)` per the test spec, plus a helper `load_curator_anchors(path="data/segment_title_attribution.json")` and `load_ngram_anchors(path="data/segment_attribution_ngram.json")` that read the canonical and derived attribution files into the (origin, t_index, container_id) shape. Modify `scripts/build_segment_attribution.py` so its output JSON keys segments by `t_index` and writes to `data/segment_attribution_ngram.json` (NOT inside quarantine).
> Modify `run_entity_extraction.py`:
> - Remove the seeded-records branch (lines 148–156). The chosen attribution comes from `attribute_segments_to_containers`, NOT from book-claim promotion.
> - Replace lines 586–677 (the resolve + propagate block) with a single call to the new attribution module. Pass the resulting `attribution` dict into `export_segments`.
> - In `export_segments` (lines 65–124), emit `t_index` on every exported record (NOT `seg_idx = origin_start + seg_idx`).
> Smol-side change required to consume the new index: write to `translate_core/entity_extraction/smol_extractor.py` so its `parse_smol_response` reads `t_index` from the smol payload. Document that the SMOL EXTRACTION JOB's output must include `t_index`. If existing `smol_extractions.json` does not have `t_index`, the consumer falls back to `seg_idx` but the coordinator notes this in the phase log as a known follow-up (the smol jobs must be re-run after Phase 12).
> Verify tests pass; existing tests pass.
> Report: full diff.

- [ ] **Step 6.4: Coordinator dispatches a fix for the SMOL_EXTRACTIONS_PATH bug.**

(This is audit §8 path mismatch.) Subagent same type.
> Task: change `run_entity_extraction.py:60` from `SMOL_EXTRACTIONS_PATH = Path("data/smol_extractions.json")` to `SMOL_EXTRACTIONS_PATH = Path("data/smol_entities_map/smol_extractions.json")`. Verify by running `.venv/bin/python3 -c "from run_entity_extraction import SMOL_EXTRACTIONS_PATH; print(SMOL_EXTRACTIONS_PATH.exists())"` returns `True`.
> Report.

- [ ] **Step 6.5: Coordinator real-data dry-run.**

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

- [ ] **Step 6.6: Commit Phase 6.**

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

### Tasks

- [ ] **Step 7.1: Coordinator pre-flight grep.**

```bash
for f in vl_parser vl_extractor vl_prompts vl_server vl_typed_extractor vl_typed_verifier vl_citation_verifier bilingual_enrichment bilingual_enrichment_batch bilingual_titles; do
  echo "=== $f ==="
  grep -rn "from translate_core.*$f\|import $f\|$f\." /Users/bel/CascadeProjects/sl_translator \
    --include="*.py" 2>&1 | grep -v __pycache__ | grep -v "tests/test_vl\|tests/test_typed_vl\|tools/vl_smoke"
done
```
Expected: every hit is in a file we are also deleting OR is the `doc_parser.py:350` lazy import OR the `ui/workspace.py` None-pass site. If anything else surfaces, the coordinator STOPS and re-scopes.

- [ ] **Step 7.2: Coordinator dispatches subagent.**

> Subagent type: `python-development:python-pro`.
> Read first: `docs/parsing_simplification_audit.md` §3 DELETE list + §11. Then the grep output from Step 7.1 the coordinator pastes into the prompt.
> Task: execute the deletes in the file list above and the two REDUCE edits in `doc_parser.py` and `ui/workspace.py`. For `doc_parser.py`, the diff should keep the PyMuPDF/text path and drop only the VL branch. For `ui/workspace.py`, the existing argument is already `None`; remove just the argument from the call.
> After each edit, run `.venv/bin/python3 -c "import <touched-module>"` to confirm it still imports.
> Then run the full test suite: `.venv/bin/python3 -m pytest tests/ -x -q`. Expect green; if anything fails because of a missed reference, surface it — do NOT add a shim to paper over it.
> Report: full list of deleted files, the two edits, and pytest output.

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

### Tasks

- [ ] **Step 8.1: Coordinator pre-flight grep.**

```bash
grep -rn "seed_from_tm\|add_collocation_node\|add_segment_node\|add_domain_node\|from seed_kg\|import seed_kg" \
  /Users/bel/CascadeProjects/sl_translator --include="*.py" | grep -v __pycache__
```
Expected: only intra-file definitions and the `seed_kg.py` script itself (which we are deleting). If any other call site surfaces, the coordinator STOPS and reports.

- [ ] **Step 8.2: Coordinator dispatches subagent.**

> Subagent type: `python-development:python-pro`.
> Read first: `ontology.md` §6, `docs/parsing_simplification_audit.md` §3 DELETE list + §6 + §10.7-10.8, `translate_core/knowledge_graph.py:545-604` and `:856-1154`, full `seed_kg.py`.
> Task: delete `seed_kg.py`. In `translate_core/knowledge_graph.py`, delete the four methods listed (and any helper code they ONLY use — verify no other method calls them; if shared helpers exist, leave the helpers alone).
> Run the full test suite. Any test that exercised the deleted methods must also be deleted (e.g. tests under `tests/` that import `seed_from_tm` or instantiate `add_collocation_node`).
> Report: files deleted, KG file diff, test outputs.

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

### Tasks

- [ ] **Step 9.1: Coordinator backs up the live KG.**

```bash
cp data/knowledge.db data/knowledge.db.phase9.bak
ls -la data/knowledge.db data/knowledge.db.phase9.bak
```

- [ ] **Step 9.2: Coordinator dispatches subagent for TDD red.**

> Subagent type: `python-development:python-pro`.
> Read first: `ontology.md` §2.2 + §3.4, `docs/parsing_simplification_audit.md` §6 "Recommendation" item 2.
> Task: write `tests/test_drain_noise_concepts.py` against a function `is_noise_concept(node_id, kg) -> bool`:
> (a) Returns True when concept has `definition==""` AND has no incoming/outgoing edges with relations in `{extends, critiques, redefines, reappropriates, related_to, attributed_to}` AND has no `originating_author` field.
> (b) Returns False when concept has a non-empty `definition`.
> (c) Returns False when concept participates in ANY lineage edge.
> (d) Returns False when concept has `originating_author` (smol provenance).
> (e) Returns False when concept has `instantiates_concept` incoming edges from `term` nodes whose count > N (curator-decided minimum — use 5 as default; this protects highly-attested concepts even without a definition pending later curation).
> Tests must fail (function doesn't exist).
> Report: tests + failures.

- [ ] **Step 9.3: Coordinator dispatches subagent for TDD green.**

> Subagent type: `python-development:python-pro`.
> Read first: failing tests; `translate_core/knowledge_graph.py` (factories + delete helpers).
> Task: implement `scripts/drain_noise_concepts.py` with two modes: `--dry-run` (prints `would_delete=N keep=M` per category) and `--apply` (calls `kg.G.remove_node` for each — but for symmetry, expose the helper at the factory level, e.g. a new `KnowledgeGraph.remove_concept_node(id)` if missing).
> Important: when deleting a concept, also delete its incident `instantiates_concept` edges from term nodes; do NOT leave dangling edges.
> The script must (a) load `data/knowledge.db`, (b) walk concept nodes, (c) classify each via `is_noise_concept`, (d) in `--apply` mode call the factory delete helper, then `kg.save()`.
> Verify tests pass.
> Report.

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

- [ ] **Step 10.3: Coordinator dispatches subagent for TDD red.**

> Subagent type: `python-development:python-pro`.
> Read first: `ontology.md` §2.2 + §3.3 + §3.4, `docs/parsing_simplification_audit.md` §6 "Recommendation" items 3-4. Read the actual file shapes (the coordinator will paste a 10-line sample of each).
> Task: write `tests/test_ingest_curator_lineages.py` covering:
> (a) After ingest, every entry from `data/concept_theorists.json` has a corresponding `concept` node with non-empty `definition` (the curator's note, even if short) AND an `attributed_to` edge from the concept to the theorist's `agent` node.
> (b) Every relation between two concepts in `data/lineage_schools.json` becomes an edge with `relation ∈ {extends, critiques, redefines, reappropriates, related_to}`. Invalid relations are coerced to `related_to` per `link_concepts_rhizomatic` semantics.
> (c) The ingester is idempotent: running twice does not duplicate edges or concepts.
> (d) The ingester uses `kg.add_concept_node`, `kg.link_concepts_rhizomatic`, `kg.link_attributed_to` exclusively — no raw `G.add_edge`.
> Tests fail (script doesn't exist).
> Report: tests + failures.

- [ ] **Step 10.4: Coordinator dispatches subagent for TDD green.**

> Subagent type: `python-development:python-pro`.
> Task: implement `scripts/ingest_curator_lineages.py`. Use ONLY factory methods. The agent must verify the `agent:` node for each theorist exists before linking; if missing, route to a review log with the missing agent ID (do NOT create a stub agent — the COBISS ingest is authoritative for agents).
> Verify tests pass.
> Report.

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

### Tasks

- [ ] **Step 11.1: Coordinator pre-flight grep.**

```bash
for f in ingest_book_bibliography ingest_book_footnotes seeded_book_finder bilingual_tm_matcher book_extractor citation_collector; do
  echo "=== $f ==="
  grep -rn "from .*$f\|import $f\|$f\." /Users/bel/CascadeProjects/sl_translator --include="*.py" \
    | grep -v __pycache__ | grep -v tests/
done
```
Expected: no live callers from in-scope code. If anything in scope still calls these, STOP.

- [ ] **Step 11.2: Coordinator dispatches subagent.**

> Subagent type: `python-development:python-pro`.
> Read first: `docs/parsing_simplification_audit.md` §10.5 / §10.6 / §3 DELETE list / §11.
> Task: delete the listed files. Run the test suite. Any test that imports a deleted symbol gets deleted with it; do not invent shims.
> Report.

- [ ] **Step 11.3: Coordinator real-data check.**

```bash
.venv/bin/python3 -m pytest tests/ -x -q
.venv/bin/python3 run_entity_extraction.py --help 2>&1 | head -20    # confirms the entry-point still parses
```

- [ ] **Step 11.4: Commit Phase 11.**

```bash
git add -A
git commit -m "ingest: delete legacy book-bibliography / footnote / seeded-book ingesters and dependents"
```

### Phase 11 verification gate

- [ ] No deleted symbol referenced anywhere.
- [ ] `.venv/bin/python3 -m pytest tests/ -x -q` green.
- [ ] `run_entity_extraction.py --help` still works.

---

## Phase 12 — End-to-end verification

**Purpose:** run the smol entity ingest end-to-end on a copy of the KG and diff against the post-Phase-10 baseline. Confirm the system produces ontology-clean output and no regressions.

**Caveat on smol payload shape — read this before running:** The current `data/smol_entities_map/smol_extractions.json` was produced by smol jobs that did NOT emit `t_index` (Phase 6 introduces the requirement; the consumer falls back to `seg_idx`). A true end-to-end pass against Phase 6's chronological-anchor improvements requires re-dispatching smol extraction with the new payload shape. Decide before this phase:
  - **Option A (recommended): in-scope re-run.** The coordinator dispatches the external smol extraction harness (OMP / DeepSeek-flash) with `t_index` enabled, regenerates `data/smol_entities_map/smol_extractions.json`, then runs Phase 12. This is the only path to a real-world verification.
  - **Option B: ship Phase 12 against stale smol data.** Verification gate is partial. Container-attribution improvements from Phase 6 are technically wired but not exercised end-to-end. Mark in the phase log; schedule the smol re-run as a follow-up.

The coordinator picks A or B and records the choice in `2026-06-06-phase-log.md` before Step 12.1.

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
(If the CLI doesn't expose `--kg-path`, the coordinator either adds the flag in a small subagent dispatch OR temporarily moves the live KG aside and runs against a copy.)

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

- [ ] **Step 12.4: Manual spot-checks.**

Coordinator picks 5 cited works at random from the smol output and verifies each:
- has `provenance="tm_smol"` (or another in-allowlist value),
- has a typed `project_type` (not `cited_work`),
- has `cited_in` to an existing container node,
- carries bilingual title fields when both languages were available in the TM.

Coordinator picks 5 container nodes and verifies each has bilingual title pair + `translated_by` edge.

- [ ] **Step 12.5: Ontology validator.**

```bash
.venv/bin/python3 scripts/validate_kg.py /tmp/knowledge.db.test_run 2>&1 | tail -40
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
| 5, 6 (require design) | Design step (algorithm / dispatcher) | `feature-dev:code-architect` |
| 5, 6 (require design) | Implement design | `python-development:python-pro` |
| 7, 8, 11 (deletions) | Pre-flight callgraph survey | `Explore` |
| 7, 8, 11 (deletions) | Surgery | `python-development:python-pro` |
| 9, 10 (KG data work) | Implement | `python-development:python-pro` |
| 12 (end-to-end) | Integration harness | `python-development:python-pro` |
| Between green and commit, every substantive phase | Independent review pass | `feature-dev:code-reviewer` OR `pythonista-reviewer` (pick one; rotate if reviewing the same agent's work) |

Rule of thumb: dispatch `Explore` whenever the coordinator would otherwise run more than two greps inline; the protected-context savings are real on large phases.

---

## Appendix: subagent dispatch template

For every dispatch the coordinator uses this skeleton:

```
Subagent type: python-development:python-pro
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
