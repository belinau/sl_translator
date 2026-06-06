# Phase 7 Deletion Blast Radius Survey

## Verified Safe Deletions

The following symbols are ONLY referenced in files marked for deletion:
- `vl_prompts`
- `vl_server`
- `vl_citation_verifier`

All references to these symbols are contained within VL-era files (test files and the modules themselves).

---

## Blockers Found

### 1. `test_phase1.py` → `vl_typed_extractor._slugify`

**File:** `/Users/bel/CascadeProjects/sl_translator/tests/test_phase1.py`  
**Lines:** 21, 227–238  
**Import:** `from translate_core.entity_extraction.vl_typed_extractor import _slugify`

**What breaks:** The test class `TestTypeFreeWorkIDs` uses `_slugify()` to verify that work IDs no longer include type prefixes (Phase 1 requirement). This is a LIVE, NON-DELETE test file.

**Severity:** BLOCKER — must extract `_slugify()` to a stable module before deleting `vl_typed_extractor.py`.

---

### 2. `citation_collector.py` → VL typed extraction pipeline

**File:** `/Users/bel/CascadeProjects/sl_translator/translate_core/citation_collector.py`  
**Lines:** 26–30 (imports), 411–456 (usage)

**Imports:**
```python
from .entity_extraction.vl_typed_extractor import (
    extract_typed_citation,
    records_from_verified,
)
from .entity_extraction.vl_typed_verifier import verify_typed_citation
```

**What breaks:** The `extract_and_ingest()` function (line 370) conditionally runs the typed VL pipeline when `vl_extractor is not None` (line 411). The entire typed extraction path (lines 412–456) depends on `extract_typed_citation()` and `records_from_verified()`.

**Current state:** Called only from `ui/workspace.py:365` with `vl_extractor=None`. When vl_extractor is None, the pipeline skips the extraction at line 411 and falls through to line 459 (log.debug and error increment).

**Severity:** BLOCKER — `citation_collector.py` is a LIVE module (not deleted). Either:
- (a) Delete this branch entirely and remove the typed extraction fallback, OR
- (b) Extract typed extraction symbols to a stable module before deleting `vl_typed_extractor.py` and `vl_typed_verifier.py`.

The audit notes suggest no live callers currently use the typed pipeline, but the import and function signature remain coupled to VL-era code.

---

### 3. `bilingual_enrichment.py` → `vl_typed_extractor.extract_typed_citation`

**File:** `/Users/bel/CascadeProjects/sl_translator/translate_core/entity_extraction/bilingual_enrichment.py`  
**Lines:** 1–17 (docstring), 140 (lazy import)

**Code:**
```python
try:
    from .vl_typed_extractor import extract_typed_citation
except ImportError:
    return None  # line 142
```

**What breaks:** The `_try_paired_extraction()` function (line 131) attempts a lazy import of `extract_typed_citation`. If the import fails, it silently returns None.

**Current state:** Called only from `enrich_titles_sl()` (line 101) when `extractor is not None`. The call site `run_entity_extraction.py:48` imports `enrich_titles_sl`, but the caller at `:320` passes `extractor=None`, short-circuiting at line 45.

**Severity:** BLOCKER (but with safety hatch) — The defensive `try/except ImportError` will keep the code from crashing when the module is deleted. However, the import statement at line 140 will fail. This is in a LIVE file (`bilingual_enrichment.py` is not deleted).

---

### 4. `book_extractor.py` → `bilingual_titles`

**File:** `/Users/bel/CascadeProjects/sl_translator/translate_core/entity_extraction/book_extractor.py`  
**Line:** 36

**Import:**
```python
from .bilingual_titles import parse_bilingual_title, _extract_pub_info
```

**What breaks:** Hard import (not lazy, not guarded) of `parse_bilingual_title` and `_extract_pub_info`. This is a LIVE file (not deleted).

**Severity:** BLOCKER — Must extract `bilingual_titles` symbols or refactor `book_extractor.py` before deleting `bilingual_titles.py`.

---

### 5. `entity_extraction/__init__.py` → `bilingual_titles`

**File:** `/Users/bel/CascadeProjects/sl_translator/translate_core/entity_extraction/__init__.py`  
**Lines:** 11, 21

**Code:**
```python
from .bilingual_titles import parse_bilingual_title, BilingualTitle
...
__all__ = [
    ...,
    "parse_bilingual_title",
    "BilingualTitle",
    ...
]
```

**What breaks:** Public API export of `parse_bilingual_title` and `BilingualTitle` from `bilingual_titles.py`.

**Severity:** BLOCKER — The `__init__.py` module remains live. It re-exports symbols that will become unreachable if `bilingual_titles.py` is deleted.

---

### 6. `run_entity_extraction.py` → `bilingual_enrichment.enrich_titles_sl`

**File:** `/Users/bel/CascadeProjects/sl_translator/run_entity_extraction.py`  
**Line:** 48

**Import:**
```python
from translate_core.entity_extraction.bilingual_enrichment import enrich_titles_sl
```

**What breaks:** Hard import (no guard). `enrich_titles_sl` is imported but the call at `:320` passes `extractor=None`, which short-circuits the function at line 45. However, the import itself will fail once `bilingual_enrichment.py` is deleted.

**Severity:** BLOCKER — Must remove the import and call, or move `enrich_titles_sl` to a stable module.

---

### 7. `import_book.py` → `vl_server.VLMServerManager`

**File:** `/Users/bel/CascadeProjects/sl_translator/import_book.py`  
**Lines:** 65, 68–72

**Code:**
```python
if use_vl:
    from translate_core.vl_server import VLMServerManager
    ...
    server = VLMServerManager()
```

**What breaks:** Lazy import of `VLMServerManager` for optional VL parsing. When `use_vl=True`, the import will fail.

**Current state:** `import_book.py` is OUT OF SCOPE (editor doc preparation). However, it WILL break if Phase 7 deletes `vl_server.py`.

**Severity:** BLOCKER (editor scope) — Must either:
- (a) Remove the `use_vl` branch from `import_book.py`, OR
- (b) Retain `vl_server.py` for the editor, OR
- (c) Extract a minimal VL server shim.

Per the task description, `ui/workspace.py` and editor logic are out of scope, but `import_book.py` is a standalone script that appears to be in-scope.

---

### 8. `main.py` → `vl_server.VLMServerManager`

**File:** `/Users/bel/CascadeProjects/sl_translator/main.py`  
**Lines:** 495, 496–501

**Code:**
```python
if use_vl:
    from translate_core.vl_server import VLMServerManager
    server = VLMServerManager()
    ui.notify("Starting VL server…", type="info")
```

**What breaks:** Lazy import of `VLMServerManager` for optional VL parsing in the editor. When `use_vl=True`, the import will fail.

**Current state:** `main.py` is OUT OF SCOPE per AGENT_CONTEXT.md constraint 6. But it WILL break at runtime if the user enables VL parsing in the editor and Phase 7 deletes `vl_server.py`.

**Severity:** MEDIUM (editor scope, out of task scope) — Editor logic is out of scope, but this edge case may cause runtime crashes.

---

### 9. `tools/vl_smoke.py` → `vl_parser`, `vl_server`, `vl_prompts`

**File:** `/Users/bel/CascadeProjects/sl_translator/tools/vl_smoke.py`  
**Lines:** Multiple imports

**What breaks:** This file is on the deletion list, so references here are not blockers.

---

## Verified Safe Modifications

### `translate_core/doc_parser.py:316–353`

**Status:** VERIFIED SAFE

The `use_vl` parameter is:
- Declared at line 316 and 340
- Used only at line 349: `if use_vl and source.suffix.lower() == ".pdf":`
- The conditional imports and uses `VLBookParser` at lines 350–358

**Confirmation:** The else-branch (PyMuPDF via MarkItDown) is the default path and is the only path called from in-scope entry points.

After deleting the `if use_vl` branch (lines 349–358), `vl_parser.py` becomes unreachable.

---

### `ui/workspace.py:365`

**Status:** VERIFIED SAFE

Code:
```python
extract_and_ingest(
    [snippet], kg, vl_extractor=None,
)
```

**Confirmation:** The call explicitly passes `vl_extractor=None`, which causes `extract_and_ingest()` to skip the typed extraction pipeline and fall through to error logging (line 459–460).

---

## Summary

**Deletable without modification:**
- `vl_prompts` ✓
- `vl_server` ✓ (except `import_book.py` and `main.py`, which are scope-gray)
- `vl_citation_verifier` ✓
- `vl_parser` ✓ (after deleting the `if use_vl` branch in `doc_parser.py`)

**Cannot delete without refactoring or extraction:**
- `vl_typed_extractor` (exports `_slugify` to `test_phase1.py` + `extract_typed_citation`/`records_from_verified` to `citation_collector.py`)
- `vl_typed_verifier` (exports `verify_typed_citation` to `citation_collector.py`)
- `bilingual_enrichment` (imported by `run_entity_extraction.py`)
- `bilingual_enrichment_batch` (imported by `citation_collector.py:468`, `run_entity_extraction.py`)
- `bilingual_titles` (hard-imported by `book_extractor.py` and `__init__.py`)

**Phase 7 must choose:**
1. Extract symbols to stable modules before deletion, OR
2. Refactor callers to not import from VL-era code, OR
3. Mark imports as optional with defensive `try/except ImportError` guards (not recommended for hard imports).

