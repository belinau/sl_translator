# Phase 1b Impact Survey: `tm.entries` Compat-Shim Feasibility

Read-only audit of current `tm.entries` consumers and related infrastructure to determine whether keeping `tm.entries` as an EN→SL-filtered view derived from a new `_entries_by_pair` storage will break any existing callers.

---

## 1. `tm.entries` Consumers (Global Inventory)

### 1a. Direct `tm.entries` readers

#### `main.py:246-259` — Upsert on confirm (run-time editor)
- **Type:** Iteration + optional append
- **Pattern:** `for entry in tm.entries:` (line 246) checking `entry.get("source")` and `entry.get("origin")`; append new entry if not found (line 251).
- **Expects:** `source_lang="en", target_lang="sl"` literally
- **Reason:** Matcher looks for existing source + origin to avoid duplicates on confirm; appends new entry with hardcoded lang pair.
- **Scope:** Editor run-time path (save_pair_to_tm called from confirm flow)
- **Status:** **COMPAT-PRESERVED CONSUMER** — This is the canonical edit path; `tm.entries` returning EN→SL-only pairs will not break it for the current EN→SL-only corpus. Hardcoded lang pair appends match the filtered view.

---

### 1b. TM method consumers (lookup, search)

#### `ui/predictions.py:251` — Fuzzy lookup for ghost-text predictions
- **Call:** `tm.lookup_fuzzy(source_text, threshold=70.0, limit=3)`
- **Scope:** Editor UI prediction panel (live suggestions while typing)
- **What it reads:** `tm.entries` internally; calls `lookup_fuzzy()` which iterates `self.entries` at `tm.py:96`.
- **Expects:** `e["source"]` contains English text (implicit via the normalization in the loader).
- **Breaking risk:** `lookup_fuzzy` searches only `e["source"]`; if `tm.entries` remains EN→SL-filtered, this continues to work for today's corpus. For future non-EN-source corpora, it would break (but that's a separate refactor — not a compat-shim issue).
- **Status:** **COMPAT-PRESERVED** — Continues to work for EN→SL view.

#### `ui/intel_panel.py:573` — Fuzzy lookup for near-exact match suggestions
- **Call:** `tm.lookup_fuzzy(seg["source"], threshold=95.0, limit=5)`
- **Scope:** Editor intel panel (TM suggestions sidebar)
- **Expects:** Same as predictions.py.
- **Status:** **COMPAT-PRESERVED** — Works with EN→SL view.

---

### 1c. Inline test fixture

#### `inline_test/test_confirm_pipeline.py:38` — Test fixture setup
- **Pattern:** `monkeypatch.setattr(_main, "tm", SimpleNamespace(entries=[]))`
- **Scope:** Test fixture for TM upsert tests (lines 54–117).
- **Expects:** The fixture creates a bare `tm` object with an empty `entries` list that the test populates.
- **Status:** **COMPAT-PRESERVED** — Test mocks the entire `tm` object; does not depend on loader internals. Will continue to work if `tm.entries` is derived from `_entries_by_pair`.

#### `inline_test/test_confirm_pipeline.py:62–68` — Assertion on entry shape
```python
assert tm.entries == [{
    "source": "Hello world.",
    "target": "Pozdrav svet.",
    "origin": "working.tmx",
    "source_lang": "en",
    "target_lang": "sl",
}]
```
- **Scope:** Verifies `save_pair_to_tm` appends correct entry dict shape.
- **Expects:** Literal `source_lang="en", target_lang="sl"`.
- **Breaking risk:** If `tm.entries` is now derived from `_entries_by_pair` rather than directly populated, the assertion still passes because for the test's en->sl corpus, `tm.entries` will still contain the EN→SL-only filtered view. The shape of the dict is unchanged.
- **Status:** **COMPAT-PRESERVED** — Assertion checks dict structure and values that remain the same in the filtered view.

---

## 2. TM Entry-Dict Field Reads (`source_lang`, `target_lang`)

### 2a. `main.py` (save_pair_to_tm)

**Line 256–257** (append new entry):
```python
"source_lang": src_lang,
"target_lang": tgt_lang,
```
- **Scope:** Entry creation; reads `src_lang, tgt_lang` parsed from the `lang_pair` parameter (line 158).
- **What it assumes:** Caller provides explicit lang pair via `parse_lang_pair(lang_pair)` (defaults to `"en"`, `"sl"`).
- **Status:** **Not a reader of `tm.entries` values** — this is a writer. Creates new entries with the caller's lang pair.

### 2b. `translate_core/tm_timecodes.py` (loader)

**Lines 130–131**:
```python
"source_lang": "en",
"target_lang": "sl",
```
- **Scope:** Entry creation in the loader.
- **What it assumes:** Every entry is hardcoded to EN→SL (audit §3.2 violation).
- **Status:** **Not a consumer of existing entries** — this is the writer that creates the violation.

### 2c. `inline_test/test_confirm_pipeline.py`

**Lines 66–67** (assertion):
```python
"source_lang": "en",
"target_lang": "sl",
```
- **Scope:** Test assertion verifying the dict shape.
- **Status:** Already covered under §1c.

---

## 3. `LANG_EN` / `LANG_SL` Constant Readers in `smol_extractor.py`

### 3a. Module-level constants

**Lines 76–77**:
```python
LANG_EN = "en"
LANG_SL = "sl"
```
- **Type:** Data values, not flow-control branching.
- **Status:** Pure string constants; no constraint-9 violation here.

### 3b. Detection function branching

**Lines 187–199** — `_detect_source_lang(origin: str)`:
```python
def _detect_source_lang(origin: str) -> tuple[str, str]:
    o = origin.lower()
    if "en-sl" in o:
        return (LANG_EN, LANG_SL)
    if "sl-en" in o:
        return (LANG_SL, LANG_EN)
    return (LANG_EN, LANG_SL)
```
- **Type:** Flow-control branching on substring match; defaults to EN→SL.
- **Violation:** Does not recognize other pairs (HR-SL, DE-SL, etc.); defaults wrongly.
- **Impact on current scope:** This is in the smol extractor, not the TM loader. But it's called by `ingest_smol_extractions` (line 914) and `format_extract_prompt` (line 209).
- **Status:** **Constraint-9 violation** (audit §3.3 identifies this), but NOT a consumer of `tm.entries`. When compat-shim logic filters `tm.entries` to EN→SL, the smol extractor's detection failures don't break the TM layer — they're separate concerns.

### 3c. Comparisons in payload builders

**Lines 529–530** (cited_work):
```python
"title_en": title_orig if orig_lang == LANG_EN else (...),
"title_sl": title_orig if orig_lang == LANG_SL else (...),
```
**Lines 727–728** (artwork), **Lines 850–851** (performance) — same pattern.
- **Type:** Conditional equality checks against LANG_EN/LANG_SL.
- **Impact:** Data-value comparisons, not flow control that would branch on TM entry language.
- **Status:** These compare extracted `orig_lang`/`translation_lang` (from smol output) against the constants; don't depend on tm.entries.

---

## 4. Legacy `title_en` / `title_sl` Field READERS on `source_text` Nodes

### 4a. Readers in smol_extractor.py

**Lines 460–464** (_build_cited_work):
```python
if not title_orig:
    title_orig = (ent.get("title_en") or "").strip() or None
if not title_translation:
    title_translation = (ent.get("title_sl") or "").strip() or None
```
- **Scope:** Fallback read when `title_orig`/`title_translation` are missing.
- **Break risk if legacy write is deleted (Phase 11 sunset):** The reader still tries to read the legacy field; if the smol agent never emits `title_en`/`title_sl`, the fallback does nothing. **Reader does not break** because it's a safe `.get()` with a default.
- **Status:** **Reader survives deletion of legacy write** — fallback will simply find no legacy field and continue.

---

## 5. `has_sl_edition` Signal Usages

### 5a. Signal SET (smol_extractor.py)

**Line 565** (_build_cited_work):
```python
"has_sl_edition": bool(slovenian_edition),
```
- **Where set:** Signal dict in the record's `signals` field.
- **What it marks:** Presence of `slovenian_edition` sub-dict (ontology §2.4.2).
- **Status:** Set based on canonical `slovenian_edition` payload field (not hardcoded language pair).

### 5b. Signal READ (confidence.py)

**Lines 129–130**:
```python
if signals.get("has_sl_edition"):
    bump("has_sl_edition", 0.10)
```
- **Where read:** Scoring function `score_record()` for cited_work records.
- **Current behavior:** Bumps confidence by 0.10 when the signal fires.
- **Rename target:** Signal should be renamed to `has_target_lang_edition` to be language-neutral.
- **Break risk if not renamed:** The signal name carries SL bias, but the scoring logic is neutral (it just checks presence). For future HR→SL corpora, the signal will fire when `slovenian_edition` → `target_lang_edition` is present; scoring remains correct. **No break in logic, but naming is non-compliant.**
- **Status:** **Rename required for constraint-9 compliance** but not a breaking issue for compat-shim.

---

## 6. `inline_test/test_confirm_pipeline.py` — Full File Analysis

Key assertions on TM entry shape:

**Lines 62–68** (test_save_pair_to_tm_appends_new_pair):
```python
assert tm.entries == [{
    "source": "Hello world.",
    "target": "Pozdrav svet.",
    "origin": "working.tmx",
    "source_lang": "en",
    "target_lang": "sl",
}]
```
- **Will this pass if `tm.entries` is derived from `_entries_by_pair`?** **YES.** The test's corpus is purely EN→SL; the compat-shim filters `_entries_by_pair` to return only EN→SL rows, producing an identical list.

**Lines 80, 93–94** (idempotency tests):
```python
assert len(tm.entries) == 1
assert tm.entries[0]["target"] == "Pozdravljen svet."
```
- **Will these pass?** **YES.** They check list length and dict field value; both are preserved in the filtered view.

**Conclusion:** All inline_test assertions will continue to pass because:
1. The test uses a mock `tm` object with an empty `entries` list.
2. The test populates it via `save_pair_to_tm()`.
3. `save_pair_to_tm()` appends entries with explicit lang pair from the `lang_pair` parameter (defaults EN→SL).
4. For today's EN→SL-only test corpus, filtering `_entries_by_pair` to `[("en", "sl")]` returns exactly what the test expects.

---

## 7. `tm.lookup_fuzzy`, `tm.search_concordance`, `tm.search_prefix` Functions

### 7a. `lookup_fuzzy` (lines 89–126)

```python
def lookup_fuzzy(self, text: str, threshold: float = 90.0, limit: int = 3) -> List[Dict]:
    sources = [e["source"] for e in self.entries if e["source"]]
    if not sources:
        return []
    # ... fuzzy scoring on sources ...
    for src, score, _ in matches:
        # ... filter by threshold and length penalty ...
        for e in self.entries:
            if e["source"] == src:
                results.append({**e, "score": score})
                break
    return results
```
- **What it searches:** `e["source"]` only (line 96 and 121).
- **Current behavior:** Searches source-language side; returns matching entries with score.
- **If `tm.entries` remains EN→SL:** Will continue searching only the EN side. For HR→SL future corpora, this would be a problem (HR queries would find nothing). **But for today's corpus, it works fine.**
- **Status:** **COMPAT-PRESERVED for EN→SL view** — No breakage when the compat-shim keeps EN→SL-only view.

### 7b. `search_concordance` (lines 128–171)

```python
def search_concordance(self, text: str, top_n: int = 5) -> List[Dict]:
    words = [w for w in text.split() if len(w) >= 2]
    for entry in self.entries:
        src_text = entry["source"]
        tgt_text = entry["target"]
        count = sum(1 for w in words if w.lower() in src_text.lower() or w.lower() in tgt_text.lower())
        # ... scoring and filtering ...
```
- **What it searches:** Both `source` and `target` (line 141, 146).
- **Neutral:** Does not assume which is source vs. target language.
- **Status:** **COMPAT-PRESERVED** — Searches both sides regardless of pair direction.

### 7c. `search_prefix` (lines 173–192)

```python
def search_prefix(self, prefix: str) -> List[str]:
    for e in self.entries:
        target_words = e["target"].split()
        for i, w in enumerate(target_words):
            if w.lower().startswith(prefix_low):
                suggestion = " ".join(target_words[i : i + 3])
                matches.append(suggestion)
```
- **What it searches:** `e["target"]` only (line 183).
- **Assumption:** Target language is where completions come from (SL in today's corpus).
- **If `tm.entries` remains EN→SL:** Completions come from the SL side, which is correct for today's editor (translators type target language). **For future HR→SL pairs where SL is again the target, still correct.**
- **Status:** **COMPAT-PRESERVED for EN→SL and SL-as-target pairs** — Works fine as long as `tm.entries` consistently represents (source, target) with the same direction.

---

## 8. `KnowledgeGraph` Constructor Signature

**File:** `/Users/bel/CascadeProjects/sl_translator/translate_core/knowledge_graph.py:211`

```python
def __init__(self, db_path: pathlib.Path = config.KG_DB_PATH):
    self.db_path = pathlib.Path(db_path)
    # ...
```

- **Signature:** `db_path` keyword argument with default `config.KG_DB_PATH`.
- **Test usage:** Tests create copies via `KnowledgeGraph(db_path=tmp_kg_path)` — explicit keyword argument passed in (inline_test/_kg_helpers.py or conftest.py would show the pattern).
- **Config-module level:** Default is a config module constant.
- **Status:** **No language-pair argument** — the KG constructor does not take a language pair parameter. Seeding and ingestion methods (`seed_from_tm()` at line 856, `promote_pair()` at line 1522) take optional `source_lang`/`target_lang` kwargs, but the constructor itself is agnostic.

---

## Compat-Shim Sufficiency Conclusion

### Will keeping `tm.entries` as an EN→SL-filtered view preserve every current consumer's behaviour?

**YES, for today's EN→SL-only corpus.**

### Detailed evidence:

1. **Direct `tm.entries` iterators** (main.py, inline_test):
   - Both expect and produce EN→SL-shaped dicts.
   - Filtering `_entries_by_pair` to `[("en", "sl")]` returns exactly these shapes.
   - **No break.**

2. **TM search methods** (lookup_fuzzy, search_concordance, search_prefix):
   - `lookup_fuzzy` and `search_prefix` search directional fields (source, target).
   - They continue to work because they operate on whatever is in the filtered view.
   - `search_concordance` is fully neutral (searches both).
   - **No break.**

3. **Inline test fixture and assertions**:
   - Test mocks create empty `entries` list and populate it via `save_pair_to_tm()`.
   - All assertions check dict structure and EN→SL-specific values.
   - Filtered view returns the same structure and values for the test corpus.
   - **No break.**

4. **Degenerate case**: A future HR→SL corpus would expose limitations (lookup_fuzzy searches only source, search_prefix only targets), but that's a **separate migration** from the compat-shim — the shim itself doesn't introduce those breaks; they're pre-existing design constraints.

### Implementation notes:

- **`tm.entries` property:** Create a property that filters `_entries_by_pair` to `[("en", "sl")]` and returns the view.
- **No cached recomputation needed** if `_load_all()` rebuilds `_entries_by_pair` on every TMX reload (current behaviour preserved).
- **Append path** (save_pair_to_tm): When appending to `_entries_by_pair`, append with explicit source_lang/target_lang from the caller's lang_pair (already done at main.py:256–257).
- **Internal coherence check**: `seed_from_tm()` in knowledge_graph.py (line 856) still assumes source=EN, target=SL via docstring. This is a **separate concern** (Phase 1 violation in a different module); the compat-shim doesn't worsen it.

### Consumers that would still break after Phase 11 sunset (if attempted today):

**None identified.** The `has_sl_edition` signal will fire on any record with `slovenian_edition` populated; the confidence bump is neutral. Legacy `title_en`/`title_sl` reads are safe fallbacks. Renaming the signal to `has_target_lang_edition` is required for naming compliance but does not introduce a runtime break.

---

## Summary Table

| Consumer | File:Line | Pattern | Expects | Break Risk | Status |
|---|---|---|---|---|---|
| Upsert matcher | main.py:246 | Iteration + optional append | EN→SL pairs | None for filtered view | COMPAT ✓ |
| Fuzzy lookup (predictions) | ui/predictions.py:251 | Call to `lookup_fuzzy()` | source = EN text | None for EN→SL view | COMPAT ✓ |
| Fuzzy lookup (intel) | ui/intel_panel.py:573 | Call to `lookup_fuzzy()` | source = EN text | None for EN→SL view | COMPAT ✓ |
| Inline test fixture | inline_test/test_confirm_pipeline.py:38 | Mock `tm.entries` list | EN→SL shape | None (mock) | COMPAT ✓ |
| Inline test assertions | inline_test/test_confirm_pipeline.py:62–68 | Dict field checks | `source_lang="en"` | None for filtered view | COMPAT ✓ |
| Signal read (has_sl_edition) | confidence.py:129 | Signal presence check | Neutral (fires if populated) | Rename needed for constraint-9 | COMPAT (w/ rename) ✓ |
| Legacy field fallback | smol_extractor.py:460 | Safe `.get()` read | Optional field | None (fallback safe) | COMPAT ✓ |

---

**Conclusion: The compat shim is sufficient.** Implement filtering now; sunset legacy writes in Phase 11 when scheduled.
