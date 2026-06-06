# Phase 8 Callgraph Survey — seed_kg.py & Four Factory Methods

**Survey Date:** 2026-06-06  
**Scope:** Full codebase excluding `.venv/`, `__pycache__/`  
**Status:** CLEAN — NO BLOCKERS DETECTED

---

## Files to be Deleted

1. **`seed_kg.py`** (lines 1–182)
   - Root-level script; no imports outside the translation_core ecosystem
   - Single call site: `kg.seed_from_tm()` at line 159

2. **`translate_core/knowledge_graph.py`** — Four methods (lines as specified):
   - `seed_from_tm` (lines 856–1154) — main seeding loop
   - `add_collocation_node` (lines 545–569) — ontology §6 factory, forbidden
   - `add_segment_node` (lines 571–594) — ontology §6 factory, forbidden
   - `add_domain_node` (lines 596–604) — ontology §6 factory, forbidden

---

## Blast Radius Survey Results

### Function References Found

| Symbol | Definition | Call Sites | Status |
|--------|-----------|-----------|--------|
| `seed_from_tm` | `knowledge_graph.py:856` | `seed_kg.py:159` (TO DELETE) | ✓ SAFE |
| `add_collocation_node` | `knowledge_graph.py:545` | None found | ✓ SAFE |
| `add_segment_node` | `knowledge_graph.py:571` | None found | ✓ SAFE |
| `add_domain_node` | `knowledge_graph.py:596` | None found | ✓ SAFE |

### seed_kg Module Imports

| Import Pattern | Call Sites | Status |
|---|---|---|
| `from seed_kg import ...` | None found | ✓ SAFE |
| `import seed_kg` | None found | ✓ SAFE |

### Intra-File Comments

The following are documentation-only references (not executable call sites):

1. **`knowledge_graph.py:1586`** — Comment: `"# ── EN extraction (same logic as seed_from_tm step 1) ─────────"`
2. **`knowledge_graph.py:1620`** — Comment: `"# ── SL extraction (same logic as seed_from_tm step 2) ─────────"`
3. **`knowledge_graph.py:1643`** — Comment: `"# min_freq filtering that seed_from_tm earns over thousands of"`

These comments reference `seed_from_tm` conceptually but do not call it. They document parallel logic in different functions.

---

## Test Suite Scan

**Result:** No tests import, call, or instantiate any of the four methods.

Comprehensive search across:
- `tests/` (all `*.py` files) — 0 references
- `inline_test/` (all `*.py` files) — 0 references
- `scripts/` (all `*.py` files) — 0 references

No test deletions required.

---

## Complete Reference Inventory

| File | Line | Reference | Type | Action |
|---|---|---|---|---|
| `seed_kg.py` | 159 | `kg.seed_from_tm(...)` | Method call | DELETE FILE |
| `knowledge_graph.py` | 545 | `def add_collocation_node(...)` | Definition | DELETE METHOD |
| `knowledge_graph.py` | 571 | `def add_segment_node(...)` | Definition | DELETE METHOD |
| `knowledge_graph.py` | 596 | `def add_domain_node(...)` | Definition | DELETE METHOD |
| `knowledge_graph.py` | 856 | `def seed_from_tm(...)` | Definition | DELETE METHOD |
| `knowledge_graph.py` | 1586 | Comment reference | Doc only | No action |
| `knowledge_graph.py` | 1620 | Comment reference | Doc only | No action |
| `knowledge_graph.py` | 1643 | Comment reference | Doc only | No action |

---

## Verdict

**NO BLOCKERS.** All references are either:
1. Definitions in files marked for deletion, OR
2. Documentation comments with no executable flow, OR
3. Contained within `seed_kg.py` itself (to be deleted)

Safe to proceed with Phase 8 deletion.

---

## Verification Checklist

- [x] `seed_from_tm` — only defined & called in files to delete
- [x] `add_collocation_node` — only defined in `knowledge_graph.py`; no callers outside
- [x] `add_segment_node` — only defined in `knowledge_graph.py`; no callers outside
- [x] `add_domain_node` — only defined in `knowledge_graph.py`; no callers outside
- [x] `seed_kg` module — no imports anywhere in codebase
- [x] Tests — no dependencies on deleted symbols
- [x] Documentation comments — all marked as reference-only, no behavioural impact

