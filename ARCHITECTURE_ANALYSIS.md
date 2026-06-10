# sl_translator — Architecture Analysis Report

**Date:** 2026-06-10 (revised)
**Scope:** Full codebase audit for feature completeness, dead code, structural gaps, and code quality.
**Revision note:** This report replaces the 2026-06-09 version, which conflated several distinct pipelines and misidentified dead vs. active code. The data flow (§2.2), module inventory (§2.1), and dead code sections (§3) have been substantially corrected.

---

## 1. Executive Summary

sl_translator is a bilingual translation workbench with an integrated knowledge graph. The core architecture is sound — factory-method-only KG writes, tiered confidence scoring, bilingual citation matching — but carries technical debt from a partial VL→smol pipeline migration. Several VL-era modules were deleted (source gone, only .pyc ghosts remain), a few carry stub code awaiting Phase 11 cleanup, and two modules are dead code (never imported). The project has **no README, no CI/CD, no linter, no dependency specification, and no structured logging**. The test suite is substantial (**353 test functions, ~8,750 LOC**) with strong ontology compliance coverage.

**Severity breakdown:**
- 🔴 Critical (must fix): 3
- 🟠 High (should fix soon): 7
- 🟡 Medium (plan to fix): 10
- 🔵 Low (nice to have): 7

---

## 2. Architecture Overview

### 2.1 Module Inventory

| Layer | Module | LOC | Role |
|---|---|---|---|
| **Core** | `knowledge_graph.py` | ~1920 | JSON-backed NetworkX DiGraph, 6 node types, 15 edge relations, factory methods |
| | `kg_ingest_entities.py` | ~1185 | Record routing, confidence scoring, dedup, bilingual merge, KG writes |
| | `tm.py` | ~260 | TMX loading, fuzzy/concordance/prefix search |
| | `tm_timecodes.py` | ~173 | TMX parsing with creationdate preservation |
| | `qa.py` | ~340 | Lemma QA with spacy/classla/stanza backends |
| | `llm.py` | ~169 | MLX-Llama translation (Apple Silicon GPU) — **planned for removal** |
| | `glossary.py` | ~279 | TBX/TSV/CSV glossary loading with FlashText indices |
| | `doc_parser.py` | ~520 | PDF/DOCX → markdown, endnote→footnote normalization, bare-digit ref renumbering, list remapping, DOCX compilation with native footnotes |
| | `document_pair_pipeline.py` | ~886 | Bilingual EN↔SL citation matching + TMX export (standalone CLI pipeline) |
| | `citation_collector.py` | ~428 | Unified CitationSnippet extraction — **degraded**: Phase 7 retired VL-typed extraction, currently only filters short refs and drops noise |
| | `container_attribution.py` | ~318 | Segment→container attribution via curator + n-gram anchors |
| | `book_outline.py` | ~137 | TOC + paragraph segmentation (copied from deleted vl_parser.py, comments stale) |
| | `cobiss_parser.py` | ~720 | COBISS plain-text bibliography parser |
| | `cobiss_classifier.py` | ~252 | COBISS entry → project_type/role classification |
| **Entity Extraction** | `smol_extractor.py` | ~980 | Prompt formatting + result parsing for OMP smol agents |
| | `book_extractor.py` | ~927 | High-recall regex-based extraction from classified segments (fallback for smol); has Phase 7 bilingual stub |
| | `segment_classifier.py` | ~553 | Per-segment type classification (footnote, biblio, body, noise, artwork, etc.) |
| | `confidence.py` | ~397 | Confidence scoring with typed-pipeline signals |
| | `citation_types.py` | ~373 | CitationType/CitationStyle enums, style detection, short-reference detection, author-form detection. **Contains dead VL prompt templates** (CLASSIFY_TEMPLATE, prompt_for_type, per-type templates) — only CitationType/CitationStyle/detect_style/is_short_reference/detect_author_form are used |
| | `bilingual_tm_matcher.py` | ~307 | TM-based citation ↔ TM matching; used by footnote/bibliography ingestion CLIs, not by smol pipeline |
| | `bibliography_parser.py` | ~494 | Structured bibliography entry parsing (used by footnote/bibliography CLIs) |
| | `footnote_parser.py` | ~332 | Markdown footnote citation parsing (used by footnote CLI + citation collector) |
| | `docx_footnote_parser.py` | ~116 | DOCX footnote XML extraction (used by citation collector) |
| | `name_dedup.py` | ~133 | Person-name canonicalisation for dedup groups |
| | `origin_walker.py` | ~105 | Origin context walking for TM extraction pipeline |
| | `seeded_book_finder.py` | ~241 | **DEAD CODE** — not imported anywhere |
| | `multi_segment_merge.py` | ~92 | **DEAD CODE** — not imported anywhere |
| | `_slug.py` | ~29 | Canonical slugify implementation (never imported — 8 other copies exist) |
| **UI** | `workspace.py` | ~565 | Main translation workspace page |
| | `intel_panel.py` | ~670 | KG + TM + Glossary intelligence panel |
| | `segment_editor.py` | ~286 | Segment editing + confirm UI |
| | `predictions.py` | ~286 | Ghost-text copilot prediction engine (TM + glossary + KG suggestions) |
| | `kg_search.py` | ~286 | KG search panel |
| | `segment_navigator.py` | ~90 | Segment navigation sidebar |
| | `settings.py` | ~90 | UI theme/colors |
| | `state.py` | ~151 | WorkspaceState + subscriber pattern |
| **Scripts** | 16 files | ~3413 total | One-off migration, validation, ingestion scripts |
| **Top-level** | `main.py` | ~569 | NiceGUI app entry + project CRUD |
| | `import_book.py` | ~124 | CLI book importer (full books with segments_meta) |
| | `run_entity_extraction.py` | ~538 | TM entity extraction orchestrator (Phase A/B) |
| | `visualise_kg.py` | ~1802 | 6-view HTML5 Canvas KG visualizer |
| | `kg_editor_ui.py` | ~1261 | Streamlit KG curation workspace |
| | `process_document_pair.py` | ~138 | CLI for document-pair pipeline |
| | `ingest_book_footnotes.py` | ~406 | CLI footnote ingestion (standalone pipeline) |
| | `ingest_book_bibliography.py` | ~296 | CLI bibliography ingestion (standalone pipeline) |
| | `test.py` | ~4 | Wrapper that exec's visualise_kg.py — **dead code** |

### 2.2 Data Flows

The system has **seven distinct pipelines**, not one. The previous report conflated them.

#### Pipeline 1: Translation Editor (document import for translation)

Two entry points for getting documents into the editor:

```
Small docs (NiceGUI upload):
  PDF/DOCX → main.py handle_new_upload
    → _parse_docx (python-docx paragraphs) or _parse_pdf (MarkItDown)
    → flat segment list → project JSON → workspace editor

Full books (CLI import):
  PDF/DOCX → import_book.py
    → python-docx (DOCX) or MarkItDown (PDF)
    → book_outline.split_paragraphs (paragraph segmentation)
    → segments with segments_meta → project JSON → workspace editor
```

Key difference: `import_book.py` produces `segments_meta` (footnote, page info) alongside flat text segments. The NiceGUI upload path only produces flat source/target pairs.

#### Pipeline 2: TM Entity Extraction (TM entries → KG)

```
TM entries (data/tm/)
  → segment_classifier.py (per-segment classification)
  → origin_walker.py (group by origin, derive project profile)
  → container_attribution.py (segment → container anchors)
  → smol_extractor.py (Phase A: prompt export; Phase B: result parsing)
  → book_extractor.py (regex fallback when smol returns nothing)
  → run_entity_extraction.py (orchestrate: export → dispatch → ingest)
  → confidence.py → score_all
  → kg_ingest_entities.py → dedup_records → write_to_kg
  → knowledge_graph.py (factory methods)
  → data/knowledge.db
```

This is the primary entity extraction path. It operates on **existing TM entries** — it does not parse documents directly.

#### Pipeline 3: Document Pair (bilingual matching → KG + TMX)

```
EN.pdf + SL.docx
  → process_document_pair.py (CLI)
  → document_pair_pipeline.py
    → parse_side() → doc_parser.to_markdown_with_meta(use_vl=False)
    → _extract_footnotes_from_md (heuristic extraction)
    → _heuristic_record (author/title extraction, NO VL/LLM)
    → _match_citations (EN↔SL pairing by token overlap)
    → _ingest_match (merged source_text with title_en + title_sl)
    → _build_tmx (sentence-aligned TMX from matched footnotes)
    → KG factory methods + TMX file
```

This pipeline is **heuristic-only** — no VL or LLM calls. It matches two sides of a bilingual document pair and creates merged bilingual source_text nodes (O-5).

#### Pipeline 4: Footnote Ingestion (book footnotes → KG)

```
Book markdown export
  → ingest_book_footnotes.py (CLI)
    → footnote_parser.py / docx_footnote_parser.py (parse footnotes)
    → _resolve_ibid_references (ibid shortform resolution)
    → bilingual_tm_matcher.py (match citations against TM for context)
    → KG factory methods (add_agent_node, add_source_text_node, link_cited_in, etc.)
```

Standalone CLI pipeline. Does NOT use smol_extractor or book_extractor.

#### Pipeline 5: Bibliography Ingestion (book bibliography → KG)

```
Book DOCX
  → ingest_book_bibliography.py (CLI)
    → bibliography_parser.py (parse bibliography entries)
    → bilingual_tm_matcher.py (match citations against TM for context)
    → KG factory methods
```

Standalone CLI pipeline. Does NOT use smol_extractor or book_extractor.

#### Pipeline 6: COBISS Personal Bibliography (COBISS export → KG)

```
COBISS .txt export
  → scripts/ingest_personal_bibliography.py (CLI)
    → cobiss_parser.py (parse entries)
    → cobiss_classifier.py (classify project_type + curator role)
    → KG factory methods
```

Standalone CLI pipeline. Has its own parser and classifier.

#### Pipeline 7: Citation Collector (editor segments → KG, currently degraded)

```
Editor-confirmed segment
  → citation_collector.py
    → CitationSnippet (frozen dataclass, universal intermediate)
    → extract_and_ingest()
      → is_short_reference filter (drops short refs)
      → noise filter (drops segments < 15 chars)
      → Phase 7: typed extraction BRANCH REMOVED — all remaining snippets count as errors
    → score_all → dedup_records → write_to_kg (currently near-empty pipeline)
```

This pipeline is **degraded** since Phase 7 retired the VL-typed extraction. It can still construct `CitationSnippet` objects from segments/metadata/MD/DOCX, but the downstream typed extraction step was removed. Phase 11 notes plan to delete `citation_collector.py` outright.

### 2.3 UI Architecture

```
NiceGUI (main.py)
  ├── / (project list + upload)
  └── /translate/{id} (workspace.py)
       ├── top bar (lang pair, dark mode, AI auto-draft toggle*, glossary)
       ├── segment_navigator (sidebar)
       ├── segment_editor (main column)
       ├── intel_panel (KG + TM + Glossary, below editor)
       └── predictions (ghost-text copilot, client-side JS)

Streamlit (kg_editor_ui.py)
  └── multipage: Terms, Concepts, Agents, Sources, Lineages,
                 Extraction Review, KG Review

\* The AI auto-draft toggle controls MLX-Llama pretranslation (`llm.py`). Only
`llm.py` and the auto-draft feature are planned for removal. The ghost-text copilot
(`predictions.py`) is a core feature that draws from TM, glossary, and KG — it stays.

---

## 3. Dead Code & Redundancy

### 🔴 CRITICAL

#### 3.1 `_slugify` duplicated 8 times (canonical module exists but unused)

| File | Lines | Notes |
|---|---|---|
| `translate_core/kg_ingest_entities.py:28` | 5 | Canonical active copy |
| `translate_core/entity_extraction/_slug.py:18` | 7 | Canonical module — **never imported** |
| `translate_core/entity_extraction/book_extractor.py:153` | 5 | Independent copy |
| `translate_core/entity_extraction/smol_extractor.py:256` | 5 | Delegates to `kg_ingest_entities._slugify` |
| `translate_core/document_pair_pipeline.py:119` | 10 | Independent copy with docstring |
| `ingest_book_footnotes.py:45` | 5 | Independent copy |
| `ingest_book_bibliography.py:49` | 5 | Independent copy |
| `scripts/ingest_personal_bibliography.py:58` | 5 | Independent copy |
| `scripts/forensic_audit.py:29` | 5 | Independent copy (commented "Copy of slug helpers") |

**Risk:** Any algorithm change must be applied in 8+ places. The canonical `_slug.py` exists but is never imported. Only `smol_extractor.py` delegates; all others maintain private copies.

**Fix:** Import from `translate_core/entity_extraction/_slug.py` everywhere. Delete 7 redundant implementations.

---

#### 3.2 Compiled modules with deleted source (ghost .pyc files)

The `__pycache__` directories contain bytecode for **~20 Python modules whose source files no longer exist**. These will cause confusing import errors if any code path attempts to import them.

**translate_core/__pycache__:**
- `vl_extractor.cpython-314.pyc` — old VL extraction module (source deleted)
- `vl_parser.cpython-314.pyc` — VL parser (source deleted)
- `vl_server.cpython-314.pyc` — VL server module (source deleted)
- `vl_prompts.cpython-314.pyc` — VL prompts (source deleted)

**translate_core/entity_extraction/__pycache__:**
- `vl_typed_extractor.cpython-314.pyc` — two-call VL pipeline (source deleted)
- `vl_typed_verifier.cpython-314.pyc` — field verification (source deleted)
- `vl_citation_verifier.cpython-314.pyc` — citation verification (source deleted)
- `ontology_validator.cpython-314.pyc` — ontology checks (source deleted)
- `bilingual_enrichment.cpython-314.pyc` — bilingual enrichment (source deleted)
- `bilingual_enrichment_batch.cpython-314.pyc` — batch enrichment (source deleted)
- `bilingual_titles.cpython-314.pyc` — bilingual title parsing (source deleted)

**scripts/__pycache__:**
- 15 migration scripts whose source was deleted (e.g., `graft_lineages_from_old_kg`, `merge_cited_to_containers`, `smol_batch_worker`, `connect_theorists`, etc.)

**Fix:** Delete all orphaned .pyc files. Add `**/__pycache__/` to .gitignore (already present for `__pycache__/` but not for `**/__pycache__/`).

---

#### 3.3 Dead top-level files

| File | Issue |
|---|---|
| `test.py` | 4 lines, just `exec(open("visualise_kg.py").read())`. No purpose. |
| `test_kg_integrity.py` | Standalone unittest file at root; should be in `tests/` and converted to pytest. |

**Fix:** Delete `test.py`. Move `test_kg_integrity.py` to `tests/` and convert to pytest.

---

### 🟠 HIGH

#### 3.4 VL-era dead code still in source tree

Two modules have source files but are **never imported anywhere**:

| Module | LOC | Status |
|---|---|---|
| `seeded_book_finder.py` | 241 | Never imported. Was for title-anchored translated_work detection. |
| `multi_segment_merge.py` | 92 | Never imported. Was for forward-spill citation merging. |

Additionally, `citation_types.py` contains dead VL prompt code (~170 lines of `CLASSIFY_TEMPLATE`, `prompt_for_type`, per-type `_BOOK_TEMPLATE`, etc.) that is only referenced by the deleted `vl_typed_extractor.py`. The smol extractor has its own prompt system. The active portions of `citation_types.py` are: `CitationType`, `CitationStyle`, `detect_style`, `is_short_reference`, `detect_author_form`, `TYPE_SCHEMAS`.

And `book_extractor.py` carries a Phase 7 bilingual stub (`_NoopBilingualTitle` class and `parse_bilingual_title` returning None) with a comment saying "this file goes away in Phase 11."

**Fix:** Delete `seeded_book_finder.py` and `multi_segment_merge.py`. Remove VL prompt templates from `citation_types.py`. Remove bilingual stub from `book_extractor.py`.

---

#### 3.5 Stale comments referencing deleted vl_parser.py

`book_outline.py` lines 24 and 50 contain comments saying "copied verbatim from vl_parser.py" and referencing vl_parser line numbers. The vl_parser.py source no longer exists.

**Fix:** Update comments to remove stale vl_parser references.

---

#### 3.6 Backup files in source tree

| File | Size | Issue |
|---|---|---|
| `translate_core/entity_extraction/citation_types.py.bak` | 20.6KB | Old backup in package directory |
| `data/knowledge.db.bak` | 96.7MB | Auto-rotated by KG.save() |
| `data/knowledge.db.pre-*.bak` (5 files) | ~460MB | Historical KG snapshots |
| `data/segment_title_attribution.json*.bak` (5 files) | ~0.4MB | Pre-session backups |
| `data/extraction_review_after_flush.json` | 5.3MB | Pre-flush snapshot |

**Fix:** Add `*.bak` to .gitignore. Remove `citation_types.py.bak` from source. Consider a cleanup script for stale data/ backups.

---

#### 3.7 citation_collector.py is a degraded pipeline

`extract_and_ingest()` in `citation_collector.py` currently:
1. Filters out short references via `is_short_reference()`
2. Drops segments shorter than 15 characters
3. Counts all remaining snippets as errors ("no typed extractor wired in Phase 7+")
4. Writes near-empty results to KG

The module remains live only so that editor callers can still construct `CitationSnippet` objects. Phase 11 notes say it will be deleted outright.

**Fix:** Document this as known degradation. Either wire a new typed extractor or remove the module entirely.

---

### 🟡 MEDIUM

#### 3.8 TM `_entries_by_pair` inconsistency (TODO)

`tm.py` documents that `main.py:251` appends runtime-confirmed TUs to `self.entries` but NOT to `self._entries_by_pair`. This means `iter_chronological` within the same session misses newly confirmed entries.

**Fix:** Append to both collections in the save_pair_to_tm path.

---

#### 3.9 `__init__.py` packages export incomplete public APIs

`translate_core/__init__.py` exports only 6 symbols: `TranslationMemory`, `Glossary`, `KnowledgeGraph`, `Translator`, `DocumentParser`, `QAEngine`. Missing from the public API:

- `citation_collector` (CitationSnippet, collect_from_md, etc.)
- `document_pair_pipeline` (parse_side, process_pair)
- `kg_ingest_entities` (score_all, write_to_kg, etc.)
- `cobiss_parser`, `cobiss_classifier`
- `container_attribution`
- `book_outline`
- `tm_timecodes`

`entity_extraction/__init__.py` exports only `classify_segments`, `walk_origin`, `dedup_group_key`, `normalize_person_name`, `score_record`, `ConfidenceTier`. Missing:

- `book_extractor`, `smol_extractor` (the two main extractors)
- `citation_types` (CitationType, CitationStyle — the dead VL prompts should be removed first)
- `bilingual_tm_matcher`
- `footnote_parser`, `bibliography_parser`, `docx_footnote_parser`
- `name_dedup` (partially exported — `dedup_group_key` and `normalize_person_name` but not `is_plausible_person_name`, `looks_like_organization`)

**Fix:** Either expand `__all__` to cover the actual public API, or explicitly document that these are internal modules accessed via direct import.

---

#### 3.10 Inline test directory outside main tests

`inline_test/` contains 5 test files (`test_confirm_pipeline.py`, `test_kg_query_v2.py`, `test_workspace_integration.py`, `test_inline_prediction.py`, `test_workspace_phase1.py`) plus `conftest.py` and `_kg_helpers.py`. These are not in the `tests/` directory and may not be picked up by pytest discovery.

**Fix:** Either move into `tests/` or add `inline_test/` to the pytest config.

---

#### 3.11 `_ensure_agent` / `_ensure_institution` duplicated in ingest scripts

Both `ingest_book_footnotes.py` and `ingest_book_bibliography.py` define nearly identical `_ensure_agent`, `_ensure_institution`, and `_cited_work_id` helpers. These should share a common module.

**Fix:** Extract into a shared utility (e.g., `translate_core/entity_extraction/ingest_helpers.py`).

---

#### 3.12 Scripts with only .pyc artifacts (no source)

The `scripts/__pycache__` directory contains bytecode for ~15 one-off migration scripts whose source was deleted. While these are truly one-off, their compiled remnants should be cleaned.

**Fix:** Delete `scripts/__pycache__`.

---

#### 3.13 `_provenance_kwargs` is NOT deprecated

The previous report claimed `_provenance_kwargs` in `kg_ingest_entities.py:150` had "no callers". This is **incorrect** — it is called at line 775 within `write_to_kg()`. The "Deprecated" docstring is misleading. The function returns an empty dict (TM provenance was retired), but it is still invoked.

**Fix:** Either remove the function and its call site, or update the docstring to explain the current state.

---

## 4. Feature Completeness Assessment

### 4.1 Fully Implemented Features

| Feature | Module(s) | Status |
|---|---|---|
| Translation editor (NiceGUI) | main.py, ui/workspace.py | ✅ Complete |
| ~~AI pretranslate (MLX-Llama)~~ | llm.py | ⚠️ Planned for removal |
| Ghost-text copilot | ui/predictions.py | ✅ Complete (TM + glossary + KG suggestions) |
| Translation memory (TMX) | tm.py, tm_timecodes.py | ✅ Complete |
| Fuzzy/concordance search | tm.py | ✅ Complete |
| Glossary (TBX/TSV/CSV) | glossary.py | ✅ Complete |
| Knowledge graph (6 node types, 15 edge relations) | knowledge_graph.py | ✅ Complete |
| Term/concept management | knowledge_graph.py | ✅ Complete |
| Translation mappings + curator verification | knowledge_graph.py | ✅ Complete |
| Entity extraction (smol agent + regex fallback) | smol_extractor.py, book_extractor.py, run_entity_extraction.py | ✅ Complete |
| Confidence scoring + tiered routing | confidence.py, kg_ingest_entities.py | ✅ Complete |
| Review queue (non-bypassable) | kg_ingest_entities.py, data/extraction_review.json | ✅ Complete |
| COBISS bibliography ingestion | cobiss_parser.py, cobiss_classifier.py | ✅ Complete |
| Bilingual document-pair pipeline | document_pair_pipeline.py | ✅ Complete |
| Citation collection (MD/segments/DOCX) | citation_collector.py | ⚠️ Degraded (Phase 7 retired typed extraction) |
| Container attribution | container_attribution.py | ✅ Complete |
| Footnote ingestion (CLI) | ingest_book_footnotes.py, footnote_parser.py | ✅ Complete |
| Bibliography ingestion (CLI) | ingest_book_bibliography.py, bibliography_parser.py | ✅ Complete |
| KG visualization (6 HTML views) | visualise_kg.py | ✅ Complete |
| KG curation UI (Streamlit) | kg_editor_ui.py | ✅ Complete |
| Book import (PDF/DOCX) | import_book.py, doc_parser.py, book_outline.py | ✅ Complete |
| Small document import (NiceGUI) | main.py, doc_parser.py | ✅ Complete |
| DOCX compilation with footnotes | doc_parser.py | ✅ Complete |
| Dark mode persistence | ui/settings.py, main.py | ✅ Complete |
| Segment QA (lemma-aware) | qa.py | ✅ Complete |
| Gender-inclusive SL translation | knowledge_graph.py, intel_panel.py | ✅ Complete |
| Rhizomatic concept network | knowledge_graph.py | ✅ Complete |

### 4.2 Partially Implemented / Degraded Features

| Feature | Status | Gap |
|---|---|---|
| Citation collector → KG pipeline | ⚠️ Degraded | Phase 7 retired VL-typed extraction; `extract_and_ingest()` drops all snippets as errors. Module stays live for CitationSnippet construction. |
| LLM pretranslation | ⚠️ Planned removal | Not useful; `llm.py` and AI auto-draft toggle to be removed |
| Ghost-text copilot | ✅ Complete | Core feature — draws from TM, glossary, and KG; NOT tied to LLM removal |

### 4.3 Dead Code / Removed Features

| Feature | Status | Notes |
|---|---|---|
| VL extraction (generic) | ❌ Removed | Source deleted, only .pyc ghost |
| VL typed extraction (2-call pipeline) | ❌ Removed | Source deleted |
| VL citation verification | ❌ Removed | Source deleted |
| Bilingual enrichment | ❌ Removed | Source deleted |
| Bilingual title parsing | ❌ Removed | Source deleted |
| Ontology validator | ❌ Removed | Source deleted |
| Seeded book finder | ❌ Dead code | Source exists but never imported |
| Multi-segment merge | ❌ Dead code | Source exists but never imported |
| VL prompt templates in citation_types.py | ❌ Dead code | Still in source, unused by smol_extractor |

### 4.4 Missing Features / Gaps

| Feature | Priority | Notes |
|---|---|---|
| **No dependency management** | 🔴 | No `requirements.txt`, no `[project.dependencies]` in pyproject.toml |
| **No README.md** | 🔴 | Project has zero onboarding documentation |
| **No CI/CD** | 🟠 | No automated test runs on push |
| **No linter/formatter** | 🟠 | No ruff, black, or flake8 configured |
| **No structured logging** | 🟠 | Mix of `print()`, `logging`, bare `except` |
| **No KG migration system** | 🟠 | Schema changes handled by manual scripts, no rollback |
| **No .gitignore for data/** | 🟡 | 460MB+ of .bak files + 175MB extraction batches tracked (data/ is in .gitignore but .bak files in other locations aren't) |
| **No API documentation** | 🟡 | No Sphinx/mkdocs; only ontology.md + AGENTS.md |
| **No config validation** | 🟡 | config.py is 23 lines with hardcoded paths |
| **No type checking enforcement** | 🟡 | pyright basic mode, most strict checks disabled |
| **Test coverage gaps** | 🟡 | 9 modules with zero test coverage (see §5) |
| **No keyboard shortcuts documentation** | 🔵 | workspace.py binds keys but no visible help |
| **No concurrent user support** | 🔵 | Single-user desktop app, no auth |
| **VL cache orphaned** | 🔵 | `data/.vl_cache/` (14MB) from retired VL pipeline |

---

## 5. Test Coverage Analysis

The project has a **substantial test suite**: **353 test functions across 27 files, ~8,750 LOC**. Tests use TDD red-green discipline with strong ontology compliance coverage.

### 5.1 Test Suite Inventory

| Test File | Tests | LOC | Covers | Quality |
|---|---|---|---|---|
| `test_cobiss_parser.py` | 33 | 395 | COBISS parsing: entries, agents, URLs, ISBN, awards, bilingual titles, edge cases | Excellent |
| `test_workspace_integration.py` | 31 | 1103 | NiceGUI workspace: overlay sync, intel panel, navigator, editor binding, KG query | Excellent |
| `test_document_pair_pipeline.py` | 24 | 299 | Normalisation, footnote extraction, author/title parsing, citation matching, O-constraints | Good |
| `test_cobiss_classifier.py` | 24 | 246 | Curator detection, own-work vs. translation classification, institution kinds, O-constraints | Good |
| `test_phase1.py` | 24 | 266 | Confidence signals, detect_author_form, slugify, prompt token parity | Good |
| `test_qa_lemma.py` | 23 | 317 | `_norm_lang`, lemmatize (mocked NLP), QA baseline, lemma-aware glossary checking | Good |
| `test_drain_noise_concepts.py` | 20 | 449 | Noise concept detection (5 criteria), term-attestation rescue, `remove_concept_node` factory | Excellent |
| `test_citation_collector.py` | 17 | 293 | CitationSnippet dataclass, collect_from_editor_segment, O-17 self-loops, TMX manifest | Good |
| `test_ingest_curator_lineages.py` | 13 | 542 | Concept-theorist ingest, lineage-schools ingest, missing-agent routing, idempotency, CLI | Excellent |
| `test_confidence.py` | 13 | 511 | Thresholds, composite 2-of-3, curator_endorsed, language-neutral signals, alias handling | Good |
| `test_container_attribution.py` | 12 | 383 | Chronological propagation, conflict detection, unanchored list, t_index ordering, loaders | Excellent |
| `test_kg_ingest_routing.py` | 12 | 540 | Phase 5 routing: translated_work (4 cases), cited_work (7 cases), agent_person (2 cases) | Excellent |
| `test_kg_ingest_compliance.py` | 11 | 307 | O-12 quartet, factory-only edges, O-20 translated_by, allowlist coercions, dead code removal | Excellent |
| `test_confirm_pipeline.py` | 11 | 355 | TM upsert (idempotent, rewrite, XML chars), KG promote_pair (NLP stubs, verified, concept link) | Excellent |
| `test_inline_prediction.py` | 10 | 327 | Binding, chip interaction, keyboard, background tasks, closure bug, cursor insertion | Good |
| `test_kg_query_v2.py` | 10 | 467 | Inflected variants, phrase ranking, concept label/domain, low-confidence, verified priority, quota split | Excellent |
| `test_workspace_phase1.py` | 10 | 129 | WorkspaceState: set_active, set_target, subscribe, progress, autosave debounce | Good |
| `test_tm_timecodes.py` | 9 | 277 | Natural file order, chronological t_index, dateless entries, iter_chronological, HR-SL fixture | Good |
| `test_ingest_personal_bibliography_neutral.py` | 8 | 358 | Author/translator/editor entries, no legacy fields, provenance stamp, bilingual publisher split | Good |
| `test_segment_classifier_prose_guard.py` | 7 | 100 | Prose false-positive guard, bibliography/footnote still detected, end-to-end pipeline | Good |
| `test_tm_pair_indexing.py` | 7 | 277 | `_entries_by_pair` surface, compat-shim EN/SL, HR-SL exclusion, raw_index, SL-EN swap | Good |
| `test_smol_extractor_lang_neutral.py` | 6 | 208 | HR-SL legacy alias absence, EN-SL legacy absence, `slovenian_edition` sunset, `translation_edition.language` | Good |
| `test_kg_integrity.py` (root) | 5 | 232 | KG save/load round-trip, edge property collision, term variants | OK |
| `test_link_translation_published_by.py` | 4 | 90 | Edge creation, idempotency, dangling node, self-loop parity | Good |
| `test_validate_kg.py` | 4 | 98 | Clean payload, each hard invariant, container with translated_by, live-KG gate | Good |
| `test_book_outline.py` | 4 | 92 | split_paragraphs (long text, empty), BookOutline defaults, TOCEntry source_page | OK |
| `test_smol_extractor_detect.py` | 3 | 107 | Pair detection positive/negative, format_extract_prompt graceful None handling | OK |
| `test_ingest_personal_bibliography_factories.py` | 1 | 115 | Factory-method-only edge writes from ingest script | Good |

**Test infrastructure:**
- `inline_test/conftest.py` — Fast NiceGUI test setup, patches heavy backends (KG NLP, MLX, TMX loading)
- `inline_test/_kg_helpers.py` — KG seeding helpers (seed_term, seed_mapping, seed_concept, seed_pair)
- `tests/fixtures/` — TMX test fixtures (unordered, HR-SL, SL-EN)

### 5.2 Coverage Strengths

1. **Ontology compliance** — 50+ tests enforce O-constraints (O-1 factory-only writes, O-5 bilingual fields, O-10 review queue, O-12 agent quartet, O-13/O-14 allowlists, O-17 no self-loops, O-20 translated_by)
2. **Routing coverage** — Every branch of `_route_record` (Phase 5) has a dedicated test case
3. **COBISS parser** — 33 tests against real 114-entry fixture; exhaustive edge cases
4. **Confidence scoring** — Signal-level precision: composite 2-of-3, curator_endorsed, language-neutral aliases, threshold boundaries
5. **NiceGUI integration** — 31 async tests with stub backends testing actual UI rendering
6. **TDD discipline** — Many test files were written RED before implementation
7. **Pure-function testing** — Key algorithms tested in isolation without I/O
8. **FakeKG pattern** — Lightweight shims recording factory-method calls for O-1 compliance assertions

### 5.3 Coverage Gaps

Modules with **zero dedicated test coverage**:

| Module | LOC | Risk | Notes |
|---|---|---|---|
| `doc_parser.py` | ~520 | High | Footnote normalization, bare-digit ref conversion, DOCX compilation untested |
| `glossary.py` | ~279 | Medium | TBX/TSV parsing, FlashText index building — complex format handling untested |
| `ingest_book_footnotes.py` | ~406 | Medium | Footnote ingestion, ibid resolution |
| `ingest_book_bibliography.py` | ~296 | Medium | Bibliography ingestion |
| `llm.py` | ~169 | Low | Planned removal |
| `visualise_kg.py` | ~1802 | Low | Visualization — output-only |
| `kg_editor_ui.py` | ~1261 | Low | Streamlit curation — hard to test |
| `import_book.py` | ~124 | Low | Thin CLI wrapper |
| `process_document_pair.py` | ~138 | Low | Thin CLI wrapper |

**Partially covered modules** (tested indirectly but lacking dedicated tests):

| Module | Indirect Coverage Via | Gap |
|---|---|---|
| `knowledge_graph.py` | test_kg_integrity, test_kg_ingest_compliance, test_link_translation_published_by | Factory methods not individually tested; NLP paths untested |
| `tm.py` | test_tm_pair_indexing, test_tm_timecodes | fuzzy_lookup, concordance untested directly |
| `segment_classifier.py` | test_segment_classifier_prose_guard | Only prose guard tested; full classification pipeline untested |
| `ui/intel_panel.py` | test_workspace_integration, test_kg_query_v2 | Covered via integration; no isolated unit tests |
| `ui/predictions.py` | test_inline_prediction | ✅ Active — ghost-text copilot (TM+KG+glossary) |
| `ui/workspace.py` | test_workspace_integration | Covered via integration; no isolated unit tests |

---

## 6. Structural Quality Issues

### 6.1 Architecture Strengths

1. **Factory-method-only KG writes** — O-1 constraint enforced at code level, prevents data corruption
2. **Tiered confidence routing** — DIRECT_WRITE ≥ 0.85 / REVIEW ≥ 0.55 / DROP < 0.55, non-bypassable
3. **Bilingual-first design** — `title_orig`/`title_translation` schema, enforced in ontology
4. **Ontology as specification** — `ontology.md` is normative, code that contradicts it is wrong
5. **Subscriber pattern in UI** — `WorkspaceState` with field-level notifications avoids scattered refresh
6. **Atomic KG saves** — temp file + `os.replace` prevents corruption
7. **Clean pipeline separation** — Seven distinct pipelines with clear entry points, not one monolith
8. **Strong test suite** — 353 tests with TDD discipline, ontology compliance gates, FakeKG shims
9. **Smol + regex fallback architecture** — Primary extraction via smol agents with deterministic regex fallback when smol returns nothing

### 6.2 Architecture Weaknesses

1. **No dependency injection** — Singletons (tm, kg, glossary, translator) stored as module-level globals in `main.py`, accessed via `app_state.py`. Hard to test, impossible to parallelize.
2. **Mixed UI frameworks** — NiceGUI for translation, Streamlit for KG curation. Two different paradigms, two different runtimes.
3. **VL pipeline remnants** — Dead VL prompt code in `citation_types.py`, stubs in `book_extractor.py`, stale comments in `book_outline.py`, degraded `citation_collector.py`, 14MB of VL cache in `data/.vl_cache/`. Should be cleaned up.
4. **Two dead modules** — `seeded_book_finder.py` and `multi_segment_merge.py` are never imported. Should be deleted.
5. **knowledge_graph.py is 1920 lines** — God class handling 6 node types, 15 edge relations, NLP, search, statistics, and term extraction. Should be decomposed.
6. **No event bus / pub-sub for KG changes** — UI polls or refreshes; no reactive subscription to graph mutations.
7. **Thread-based LLM** — `llm.py` uses `threading.Lock` for GPU access. Planned for removal, but if kept, should use async.
8. **print() instead of logging** — `main.py`, `import_book.py`, `run_entity_extraction.py`, `visualise_kg.py`, `process_document_pair.py` all use `print()` for status output instead of `logging`.
9. **Planned removal not yet executed** — LLM pretranslation (`llm.py` + AI auto-draft toggle) is acknowledged as not useful but still present. The ghost-text copilot (`predictions.py`) is a separate core feature that stays.

---

## 7. Data Directory Health

### 7.1 Size Analysis

| Path | Size | Issue |
|---|---|---|
| `data/knowledge.db` | 96.7MB | Normal — 93,554 nodes, 378,543 edges |
| `data/knowledge.db.bak` | 96.7MB | Auto-rotated by KG.save() |
| `data/knowledge.db.pre-*.bak` (5 files) | ~460MB | Historical KG snapshots |
| `data/smold_segments_for_extraction.json` | 175MB | Extraction intermediate, should be temporary |
| `data/smol_batches/` (648 files) | ~163MB | Batch extraction cache |
| `data/smol_batches_small/` (6484 files) | ~175MB | Smaller batch cache |
| `data/smol_entities_map/` | 10MB | Extraction results cache |
| `data/quarantine/` | ~114MB | Orphan analysis, TMX matches |
| `data/.vl_cache/` | 14MB | VL model cache (pipeline retired) |

**Total data/ size: ~1.2GB**, of which ~460MB is backup files and ~528MB is smol batch/cache data.

### 7.2 Recommendation

1. Add `*.bak`, `data/knowledge.db.pre-*.bak`, `data/smol_batches*/`, `data/smold_segments_for_extraction.json`, `data/.vl_cache/` to .gitignore
2. Delete `data/.vl_cache/` — the VL pipeline is retired
3. Create a `scripts/clean_data.py` utility to prune stale backups (>7 days)
4. Document which files are safe to delete

---

## 8. Gap Analysis — Detailed

### 8.1 Dependency Management 🔴

The project has no `requirements.txt` and no `[project.dependencies]` in `pyproject.toml`. `pyproject.toml` only configures pyright. Dependencies are implicit in the `.venv/` directory.

**Key dependencies observed in imports:**
- `nicegui` — UI framework
- `streamlit` — KG editor
- `networkx` — Knowledge graph
- `spacy`, `classla`, `stanza` — NLP backends (optional)
- `rapidfuzz` — TM fuzzy matching
- `flashtext` — Glossary keyword extraction
- `lxml` — TMX parsing
- `markitdown` — PDF/DOCX conversion
- `python-docx` — DOCX manipulation
- `pymupdf` / `fitz` — PDF text extraction
- `mlx-lm` — Apple Silicon LLM (planned for removal)
- `plotly` — Visualization
- `groq` — API client

**Fix:** Add `[project]` section to pyproject.toml with all dependencies, or create requirements.txt.

### 8.2 No CI/CD 🟠

No GitHub Actions, no pre-commit hooks, no automated test runs.

**Fix:** Add a minimal `.github/workflows/test.yml` that runs `pytest tests/ --ignore=tests/test_vl_parser.py`.

### 8.3 No Linter/Formatter 🟠

No ruff, black, flake8, or isort configured.

**Fix:** Add ruff with a minimal config. Run on CI.

### 8.4 No Structured Logging 🟠

The project mixes `print()`, `logging.getLogger()`, and bare exception handling.

**Fix:** Adopt `structlog` or enforce `logging` usage. Replace all `print()` calls with `logger.info()`.

### 8.5 Config Is Minimal 🟡

`config.py` is 23 lines with 7 hardcoded settings. No validation, no environment variable support, no profiles.

**Fix:** Consider `pydantic-settings` or at minimum add env var overrides.

### 8.6 Planned Removals Not Executed 🟡
`llm.py` (MLX-Llama pretranslation) and the AI auto-draft toggle are planned for removal. The ghost-text copilot (`predictions.py`) is a **core feature** that draws from TM, glossary, and KG — it stays.

**Fix:** Remove `llm.py` and the AI toggle/pretranslation UI. Clean up `app_state` references and the `llm_executor` thread pool. `predictions.py` and the ghost-text overlay are NOT part of this removal — they use TM+glossary+KG, not LLM.

---

## 9. Recommendations (Prioritized)

### Immediate (Critical)

1. **Consolidate `_slugify`** — Import from `_slug.py` everywhere, delete 7 redundant copies
2. **Delete orphaned .pyc files** — Clean all `__pycache__` directories of ghost modules
3. **Delete `test.py`** — 4-line exec wrapper with no value

### Short-term (High)

4. **Delete dead modules** — Remove `seeded_book_finder.py`, `multi_segment_merge.py`
5. **Clean VL remnants** — Remove dead VL prompts from `citation_types.py`, remove bilingual stub from `book_extractor.py`, update stale comments in `book_outline.py`
6. **Add `.gitignore` entries** — `*.bak`, `data/knowledge.db.*.bak`, `data/smol_batches*/`, `data/smold_segments_for_extraction.json`, `data/.vl_cache/`
7. **Add dependency declaration** — Create `[project.dependencies]` or `requirements.txt`
8. **Add minimal CI** — GitHub Actions workflow for pytest
9. **Add ruff** — Linting + formatting in one tool
10. **Adopt structured logging** — Replace print() with logging
11. **Move `test_kg_integrity.py`** — Into tests/ directory, convert to pytest
12. **Delete VL cache** — `data/.vl_cache/` is 14MB of orphaned data from a retired pipeline

### Medium-term (Medium)

13. **Decompose `knowledge_graph.py`** — Extract NLP helpers, search, statistics into submodules
14. **Expand `__init__.py` exports** — Document the actual public API
15. **Consolidate `inline_test/`** — Merge into `tests/` or add to pytest config
16. **Fix TM `_entries_by_pair` bug** — Append to both collections in save path
17. **Extract shared ingest helpers** — From ingest_book_*.py into common module
18. **Add config validation** — Support env var overrides
19. **Create cleanup script** — Prune stale data/ backups
20. **Clean stale comments** — Remove references to deleted vl_parser.py
21. **Remove or fix `_provenance_kwargs`** — Either delete and remove call site, or correct the "Deprecated" docstring
22. **Remove LLM pretranslation** — Delete `llm.py`, AI auto-draft toggle, `llm_executor`; keep `predictions.py` (ghost-text copilot uses TM+KG+glossary, not LLM)

### Long-term (Low)

24. **Unify UI framework** — Consider NiceGUI-only (drop Streamlit dependency)
25. **Add KG migration system** — Versioned schema with upgrade path
26. **Add keyboard shortcuts panel** — Visible help in workspace
27. **Add test coverage for doc_parser** — Largest untested module
28. **Type-safe app_state** — Replace `Any` with proper protocols

---

## 10. Metrics Summary

| Metric | Value |
|---|---|
| Total Python source files | ~55 (excluding .venv) |
| Total source LOC (approx) | ~16,000 |
| Test files | 22 (tests/) + 5 (inline_test/) + 1 (root) |
| Test functions | 353 |
| Test LOC | ~8,750 |
| Test-to-code ratio | ~1:2 |
| Duplicated `_slugify` copies | 8 (plus 1 canonical unused, 1 delegator) |
| Orphaned .pyc files | ~20 |
| Data directory size | ~1.2GB |
| Backup data size | ~460MB |
| VL cache (orphaned) | 14MB |
| KG nodes | ~93,554 |
| KG edges | ~378,543 |
| Node types | 6 (term, concept, translation_mapping, source_text, agent, institution) |
| Edge relations | 15 |
| Ontology constraints | 20 (O-1 through O-20) |
| Known test failures | 1 (test_vl_parser.py — source deleted) |
| Dead modules (source exists, never imported) | 2 (seeded_book_finder, multi_segment_merge) |
| Degraded pipelines | 1 (citation_collector) |
| Planned removals | 1 (llm.py + AI auto-draft toggle) |