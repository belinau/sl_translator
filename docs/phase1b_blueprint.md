# Phase 1B Blueprint — Language-Neutrality Remediation

Produced by Phase 1B Step 1B.1 (`feature-dev:code-architect`). Read before
implementing Step 1B.2 (TDD red) and 1B.3 (TDD green).

---

## 1. Module changes overview

- **`translate_core/tm_timecodes.py`** — `_read_srclang` loses its `"en"` fallback and returns `None` when no `srclang` is found. The `<tuv>` pairing loop loses its `lang == "en"` / `lang == "sl"` branches; replaced by evidence-based pairing (header `srclang` + positional fallback). Returned entries carry ACTUAL `xml:lang` codes in `source_lang`/`target_lang`.
- **`translate_core/tm.py`** — New `_entries_by_pair: dict[tuple[str | None, str | None], list[dict]]` populated during `_load_all`. `self.entries` retained as an eagerly-computed concrete list (NOT a property — `main.py:251` does `tm.entries.append(...)` at runtime). `_reindex_t_index` extended to span all pair buckets globally. `iter_chronological` reads from `_entries_by_pair`.
- **`translate_core/entity_extraction/smol_extractor.py`** — `_detect_source_lang` rewritten as a single regex returning `(None, None)` on no match. `format_extract_prompt` gets a None-guard. `build_record` drops `src_lang=LANG_EN, tgt_lang=LANG_SL` defaults. The three `title_en`/`title_sl` legacy-alias write sites become conditional on both EN and SL appearing in the pair.
- **`translate_core/entity_extraction/confidence.py`** — Signal key `has_sl_edition` renamed to `has_target_lang_edition` (1 read site + 1 write site).

## 2. The `_entries_by_pair` index

**Key:** `tuple[str | None, str | None]` — `(source_lang, target_lang)` as normalised lowercase two-letter ISO codes or `None`. Valid keys include `("en", "sl")`, `("sl", "en")`, `("hr", "sl")`, `("en", None)`, `(None, "sl")`, `(None, None)`.

**Value:** `list[dict]` with keys `source, target, origin, source_lang, target_lang, raw_index, creationdate, t_index`. The `source_lang`/`target_lang` values are the ACTUAL `xml:lang` codes from the TMX (NOT normalised to EN/SL).

**`raw_index`:** global counter across ALL pairs in natural load order. First entry from first TMX = 0, regardless of pair bucket.

**`t_index`:** global chronological rank across ALL pairs and origins, assigned by `_reindex_t_index` at end of `_load_all`. Sort key: `(creationdate is None, creationdate or "", raw_index)`. Dated before undated; ties break by `raw_index`.

## 3. `tm.entries` compat-shim derivation

`tm.entries` is an **eagerly-computed concrete list** (not a `@property`), built ONCE at end of `_load_all` AFTER `_reindex_t_index` runs. Reason: `main.py:251` appends to `tm.entries` at runtime; a derived property would silently lose those appends.

**Pair-key selection:** `tm.entries` draws from EXACTLY `("en", "sl")` and `("sl", "en")` buckets. Other pair keys are excluded.

**Swap mechanic for `("sl", "en")` entries:** produce a shallow-copy dict per entry where `"source"` becomes the original `"target"` text, `"target"` becomes the original `"source"` text, `"source_lang"` is forced to `"en"`, `"target_lang"` is forced to `"sl"`. Other fields (`raw_index`, `creationdate`, `t_index`, `origin`) copied unchanged. Original entry in `_entries_by_pair` is NOT mutated.

**`# COMPAT-SHIM: audited exception to constraint 9; sunset in Phase 11`** marks both the pair-key filter and the swap. These are the only EN/SL string literals permitted in flow control within the changed code.

**Assembly:** merge originals from `("en", "sl")` with swapped copies from `("sl", "en")`, sort by `raw_index`.

**Invariant:** for today's EN/SL-only corpus, `[e["raw_index"] for e in tm.entries] == list(range(len(tm.entries)))`. A future HR-SL TMX adds to `_entries_by_pair[("hr", "sl")]` only, leaving `tm.entries` unchanged.

## 4. `iter_chronological(origin=None)` after the refactor

Reads from ALL pair buckets in `_entries_by_pair`, flatten, optionally filter by `origin`, sort ascending by `t_index`. Yields entries with ACTUAL codes (no swap, no normalisation). New consumers (Phase 6 attribution) use these actual codes.

**Documented asymmetry (Risk 2):** `main.py:251` appends to `tm.entries` but NOT `_entries_by_pair`. Runtime-confirmed pairs are visible in `tm.entries` but invisible to `iter_chronological` within the same session. Acceptable for Phase 6 (batch pass). A `TODO` comment on `iter_chronological` documents this.

## 5. `read_tmx_with_timecodes` refactor

`_read_srclang`: both `return "en"` fallback sites become `return None`.

`<tuv>` pairing replaced:

```python
tuvs = tu.findall("tuv")
if len(tuvs) != 2:
    continue  # skip TU silently; matches today's behaviour
a_lang = _norm_lang(tuvs[0].get(XML_LANG))  # "" if absent
b_lang = _norm_lang(tuvs[1].get(XML_LANG))

if srclang and a_lang and a_lang == srclang:
    src_text, src_lang = _seg_text(tuvs[0]), a_lang
    tgt_text, tgt_lang = _seg_text(tuvs[1]), b_lang or None
elif srclang and b_lang and b_lang == srclang:
    src_text, src_lang = _seg_text(tuvs[1]), b_lang
    tgt_text, tgt_lang = _seg_text(tuvs[0]), a_lang or None
else:
    # Positional fallback
    src_text, src_lang = _seg_text(tuvs[0]), a_lang or None
    tgt_text, tgt_lang = _seg_text(tuvs[1]), b_lang or None
```

Returned entry `source_lang`/`target_lang` are ACTUAL codes (or `None`). No hardcoded literals. Existing `if not src_text or not tgt_text: continue` guard preserved.

## 6. `smol_extractor._detect_source_lang` refactor

```python
def _detect_source_lang(origin: str) -> tuple[str | None, str | None]:
    m = re.search(r"(?<![a-z])([a-z]{2})-([a-z]{2})(?![a-z])", origin.lower())
    return (m.group(1), m.group(2)) if m else (None, None)
```

Recognises `big-HR-SL.tmx`, `2022-SL-EN.tmx`, `mglc-EN-SL.tmx`, `en-sl.tmx`, `DE-FR.tmx`. Returns `(None, None)` for `foo.tmx`, `working.tmx`.

**Caller fix (Risk 3):** `format_extract_prompt` at line 209-214 calls `.upper()` on the results — crashes on `None`. Add guard: `src_lang_label = src_lang.upper() if src_lang else "??"`, same for target. Use `??` placeholders in the prompt format; signals to the LLM the language is undetermined.

`LANG_EN`/`LANG_SL` constants remain as data values for use inside the legacy-alias conditional only.

## 7. Smol `_build_*` builder changes

### `_build_cited_work` (lines 519-583)

Canonical fields (`title_orig`, `title_translation`, `orig_lang`, `translation_lang`) always written when data is available.

Legacy alias conditional replaces lines 529-530:

```python
if LANG_EN in {orig_lang, translation_lang} and LANG_SL in {orig_lang, translation_lang}:
    payload["title_en"] = title_orig if orig_lang == LANG_EN else title_translation
    payload["title_sl"] = title_orig if orig_lang == LANG_SL else title_translation
# SUNSET: Phase 11 — delete when kg_ingest_entities.deferred_artwork
# + all consumers are migrated to title_orig/title_translation.
```

Set-membership `LANG_EN in {None, "sl"}` is `False`, so `None` lang values skip the block safely.

Signal key change at line 565: `"has_sl_edition"` → `"has_target_lang_edition"`. Value `bool(slovenian_edition)` unchanged.

### `_build_artwork` (lines 718-737) and `_build_performance` (lines 842-860)

Same legacy-alias conditional pattern. No `slovenian_edition` concept; no signal rename here.

### `build_record` defaults (lines 295-296)

`src_lang: str = LANG_EN` and `tgt_lang: str = LANG_SL` change to `str | None` with NO defaults. Both become required keyword args. Only external caller is `ingest_smol_extractions` line 918, which passes explicit values.

## 8. `confidence.py` rename

Three locations:
- `confidence.py:129` — `signals.get("has_sl_edition")` → `signals.get("has_target_lang_edition")`.
- `confidence.py:130` — `bump("has_sl_edition", 0.10)` → `bump("has_target_lang_edition", 0.10)`.
- `smol_extractor.py:565` — `"has_sl_edition": bool(slovenian_edition)` → `"has_target_lang_edition": bool(slovenian_edition)`.

`+0.10` bump magnitude unchanged. Code comment at the read site documents the rename + ontology §2.4.2 mapping.

## 9. Test plan

### Fixture: `tests/fixtures/tmx_hr_sl.tmx`

Valid lxml-parseable TMX, header `srclang="HR"`, three TUs each with `<tuv xml:lang="HR">` + `<tuv xml:lang="SL">`. All TUs have `creationdate`; dates out of natural order (e.g. 2024, 2022, 2023) to exercise `t_index` sorting.

### `tests/test_tm_timecodes.py` — parametrised extension

`@pytest.mark.parametrize` over `tmx_unordered.tmx` (existing) and `tmx_hr_sl.tmx`. For HR-SL:
- 3 entries returned.
- Every entry: `source_lang="hr"`, `target_lang="sl"` (actual codes).
- `raw_index` = `[0, 1, 2]`.
- `t_index` reflects chronological sort (2022 → 0, 2023 → 1, 2024 → 2).

Existing EN/SL test must still pass unchanged.

**Unknown-pair case:** fixture with no `srclang` and no `xml:lang`. Assert `source_lang=None, target_lang=None` (no EN/SL default).

### `tests/test_tm_pair_indexing.py` (new)

- `test_entries_by_pair_keys`: load EN-SL + HR-SL fixtures together; assert `_entries_by_pair` has both keys with expected counts.
- `test_entries_by_pair_actual_codes`: HR-SL entries carry `source_lang="hr"`; EN-SL entries carry `source_lang="en"`.
- `test_tm_entries_compat_view`: load EN-SL and SL-EN files; `tm.entries` contains all entries; every entry `source_lang="en", target_lang="sl"`; `raw_index` is contiguous.
- `test_tm_entries_excludes_hr_sl`: load EN-SL + HR-SL; `len(tm.entries) == len(_entries_by_pair[("en", "sl")])`. HR-SL invisible to compat view.
- `test_tm_entries_byte_identical_to_pre_refactor`: load EN-SL only; snapshot the entries; assert field-by-field match to pre-refactor expected values.

### Smol detector tests

- `_detect_source_lang("big-HR-SL.tmx") == ("hr", "sl")`
- `_detect_source_lang("2022-SL-EN.tmx") == ("sl", "en")`
- `_detect_source_lang("en-sl.tmx") == ("en", "sl")`
- `_detect_source_lang("foo.tmx") == (None, None)`
- `_detect_source_lang("DE-FR.tmx") == ("de", "fr")`

### `_build_cited_work` with HR/SL pair

Call with `src_lang="hr"`, `tgt_lang="sl"`, `ent={"title_orig": "X", "title_translation": "Y", "orig_lang": "hr", "translation_lang": "sl", ...}`. Assert:
- Canonical fields populated.
- `"title_en" not in payload`, `"title_sl" not in payload`.

### `confidence.score_record` with renamed signal

- `score_record("cited_work", {"has_target_lang_edition": True, ...})` → `"has_target_lang_edition"` in `reason_codes`, `+0.10` applied.
- `score_record("cited_work", {"has_sl_edition": True, ...})` → no `+0.10` for the old key.

### Regression guard

`.venv/bin/python3 -m pytest inline_test/test_confirm_pipeline.py -v` passes unmodified.

## 10. Migration sequence

1. Add `_entries_by_pair` instance dict to `TranslationMemory.__init__`.
2. Refactor `read_tmx_with_timecodes` per §5.
3. Update `tm._load_tmx` to bucket entries into `_entries_by_pair` and rebase global `raw_index`.
4. **Critical ordering (Risk 1):** at end of `_load_all`, FIRST call `_reindex_t_index` over all pair buckets to assign `t_index` to original dicts. THEN build `self.entries` from the swapped copies (which inherit the now-assigned `t_index`).
5. Update `iter_chronological` to read from `_entries_by_pair`.
6. Refactor `_detect_source_lang` per §6.
7. Add `None`-guard in `format_extract_prompt` (Risk 3 fix).
8. Update `_build_cited_work`, `_build_artwork`, `_build_performance` legacy-alias writes per §7.
9. Drop `build_record` defaults per §7.
10. Rename `has_sl_edition` → `has_target_lang_edition` per §8.
11. Run full suite including `inline_test/`.

## 11. Risks & open questions

1. **`_reindex_t_index` ordering:** assign `t_index` to originals in `_entries_by_pair` BEFORE building swapped copies for `self.entries`. Step 4 above sequences this correctly.

2. **Runtime-append asymmetry:** `main.py:251` appends to `self.entries` only. `iter_chronological` (reading `_entries_by_pair`) won't see runtime confirms. Acceptable for Phase 6. Document via `TODO` on `iter_chronological`.

3. **`format_extract_prompt` crash:** `.upper()` on `None`. Guard required (described in §6). TDD red MUST include a test for `format_extract_prompt(..., "foo.tmx")` to catch this.

4. **`_norm_lang` locale strip:** existing behaviour — `EN-GB` → `en`. No regression.

5. **`(None, None)` bucket:** invisible to `lookup_fuzzy` (which reads `tm.entries`). Log a load-time warning when this bucket is non-empty.

**Open (deferred):**
- `SYSTEM_PROMPT` text at smol_extractor.py:82-83 describes corpus as "(EN/SL)". Prompt-level change is out of Phase 1B scope (risks model output regression). Defer to Phase 4 or later.
- `slovenian_edition` JSON schema key in the prompt template (line 133) and sub-dict field name are deferred to Phase 11.

---

**Files affected:** `translate_core/tm_timecodes.py`, `translate_core/tm.py`, `translate_core/entity_extraction/smol_extractor.py`, `translate_core/entity_extraction/confidence.py`.

**Files to create:** `tests/fixtures/tmx_hr_sl.tmx`, `tests/test_tm_pair_indexing.py`.

**Files to extend:** `tests/test_tm_timecodes.py`.

**Files guaranteed unchanged:** `main.py`, `ui/*.py`, `kg_editor_ui.py`, `import_book.py`, `app_state.py`, `config.py`, `translate_core/llm.py`, `translate_core/glossary.py`, `translate_core/qa.py`, `visualise_kg.py`, `inline_test/test_confirm_pipeline.py`.
