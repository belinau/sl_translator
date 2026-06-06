# Parsing-simplification language-neutrality audit

Companion read-only audit of the plan
`docs/superpowers/plans/2026-06-06-parsing-simplification.md` and the code
committed under Phases 0–3, against constraint 9 of that plan and the
user's rule: the editor and KG must work across arbitrary language pairs;
SL+EN is the present default, not an axis to hardcode.

---

## 1. Executive summary

Phases 0–3 land working code, but language-neutrality compliance is **roughly 40%**: the universal scoring logic and the editor-safe outline are neutral; the TMX loader and the smol extractor are not. The two worst defects are both in committed code:

1. `tm_timecodes.py:97-106` silently drops any `<tuv>` whose `xml:lang` is neither `en` nor `sl`. A future `HR-SL.tmx` or `DE-SL.tmx` loads as **zero entries**. This is a hard runtime bug, not stylistic.
2. `smol_extractor._detect_source_lang` only recognises the substrings `en-sl` / `sl-en` and defaults to `(LANG_EN, LANG_SL)` for everything else, including TMXs whose filename advertises another pair.

The plan itself enables both: Phase 1 dispatch prompt (line 192) instructed the subagent to *"preserve the EN-source/SL-target swap behaviour"*; constraint 7 (line 19) sanctions the SL/EN-specific `title_en`+`title_sl`+`slovenian_edition` encoding as an OR-equal canonical form. Constraint 9 was added later, so Phases 1–3 carry pre-constraint-9 assumptions in their dispatch wording and verification examples. Phases 4–12 reference constraint 9 but contain SL/EN-favoured fixtures and prompt examples. Remediation requires a per-phase prompt rewrite plus targeted code edits before any new TMX language pair lands.

---

## 2. Plan-level findings

### Phase 0 — pre-flight

No language-pair assumptions. Pure inventory.

### Phase 1 — TMX chronological ordering (root violation)

Constraint 9 was added after Phase 1 was written. The Phase 1 dispatch wording bakes the SL/EN axis in.

- `2026-06-06-parsing-simplification.md:167` — "Read first: …the TMX header of `data/tm/2022-SL-EN.tmx` lines 1–30." The example file is an SL-EN TMX. No instruction to also look at any non-SL-EN TMX (none exist in the repo today; that itself is the assumption).
- `2026-06-06-parsing-simplification.md:192` — explicit instruction to the implementer: *"Preserve the existing `clean_xml` normalization and the EN-source/SL-target swap behaviour from `tm.py:39-52`."* This sentence is the proximate cause of the `tm_timecodes.py:97-106` hardcoding. The Phase 1 implementer was told to preserve a SL/EN-specific normalisation; they did exactly that.
- Phase 1's verification (`:202-228`) checks only that `chronological ≠ natural` somewhere, that `iter_chronological()` is sorted, and that `lookup_fuzzy('art')` returns hits. None of these would fail if the loader silently dropped an HR-SL TMX. The gate cannot detect the language-pair regression.
- Plan asserts (`:148-152`) that `self.entries` order must not change for the editor's sake — correct, but the editor's broader assumption (target-language=SL is the only translation direction) is not surfaced as a constraint.

### Phase 2 — extract editor-safe symbols

No language assumptions in scope. `book_outline.py` operates on punctuation regex only. Plan-level compliance: clean.

### Phase 3 — confidence-bump fix

Constraint 9 not violated; constraint surface only.

- `2026-06-06-parsing-simplification.md:365-368` — the real-data sweep example computes the `title_bilingual` signal from `(title_en AND title_sl) OR (title_orig AND title_translation)`. The OR form is language-neutral; the AND form preserves the SL/EN-specific legacy. Acceptable as a transitional read, but the example bakes the legacy fields into the verification recipe, which propagates to anyone copying it.
- Plan does not require Phase 3 tests to cover non-SL/EN cases. Phase 3 tests (`tests/test_confidence.py`) use only the abstract signal-flag interface so no language hardcoding ended up in test data — but only by luck.

### Phase 4 — COBISS bilingual title encoding

Strongest constraint-9 enforcement in the plan; mostly compliant.

- `2026-06-06-parsing-simplification.md:418` — explicitly asks for non-SL/EN pairs in at least three test cases. Good.
- `2026-06-06-parsing-simplification.md:437` — instructs the subagent to read the bibliography file first so the marker map is data-driven, not hardcoded. Good.
- `2026-06-06-parsing-simplification.md:417` — *"the field names `title` and `title_en` are legacy; treat `title_en` as 'the secondary-language side of a bilingual `=`-separated COBISS title', NOT specifically English"*. Correct reframing of `CobissEntry.title_en`.
- BUT `2026-06-06-parsing-simplification.md:440-444` — the canonical-encoding instruction writes `title_orig`+`title_translation`+`orig_lang`+`translation_lang`, then *also* allows `title_en` as a transitional duplicate when readers would break. The transitional escape hatch is open-ended; remediation should bound it with a sunset.
- `2026-06-06-parsing-simplification.md:474-486` — verification queries `d.get('title_orig')` directly (good) but only over container/self-auth `project_type` enums that are language-neutral. Compliant.

### Phase 5 — routing chokepoint

Constraint reminder restated (line 511); but verification fixtures lean SL/EN.

- `2026-06-06-parsing-simplification.md:511-518` — provenance values `cobiss_personal` / `tm_smol` / `doc_pair` are language-neutral. Good.
- `2026-06-06-parsing-simplification.md:556` — *"Use a non-SL/EN example title pair to surface any latent language-pair bug."* Single non-pair instruction; suffices.
- The 5.5 dry-run command (`:591`) greps for `router|direct|review|reject` words only. Nothing asserts that non-SL/EN payloads route correctly. Verification gate cannot detect a language-pair regression in the router.

### Phase 6 — chronological-anchor attribution

No direct hardcoding, but reuses `tm.entries`/`t_index` whose language assumptions come from Phase 1.

- `2026-06-06-parsing-simplification.md:671-674` — anchor algorithm uses `(origin, t_index, container_id)` tuples only. Language-neutral.
- Latent dependency: `t_index` and `origin` come from `tm_timecodes.read_tmx_with_timecodes`. If that loader drops non-SL/EN entries (it does — see §3.1), the attribution module operates on a partial view of the corpus and its conflict-detection cannot fire on the missing entries.

### Phase 7 — VL deletion

No language-pair surface.

### Phase 8 — seed_kg deletion

The `seed_kg.py` script writes hardcoded `source_lang="en", target_lang="sl"` at lines 67-68 and 161-162. Deletion removes the violation. Good.

### Phase 9 — drain noise concepts

Operates on graph topology + curator signals; no language axis. Compliant.

### Phase 10 — wire curator lineages

No language axis in plan-level fixtures; the curator JSON files dictate languages. Compliant.

### Phase 11 — book-ingest deletion

Removes `translate_core/citation_collector.py:132-167` which writes `source_lang="en", target_lang="sl"` hardcoded. Deletion removes the violation. Good.

### Phase 12 — end-to-end verification

- `2026-06-06-parsing-simplification.md:1220` — spot-check brief asks for "bilingual title fields (`title_orig`+`title_translation` OR `title_en`+`title_sl`) populated when the TM has both languages". The OR form is language-pair-neutral on the canonical axis but still allows the legacy SL/EN-specific check. The cited TM-coverage check uses "both languages" loosely; for a future HR-SL TMX, "both languages" means HR+SL, not EN+SL.
- Validator (`:1224`) is `validate_kg.py` and does not enforce language-neutrality (FORBIDDEN_NODE_TYPES only). No gate catches a language-pair regression at the end.

### Cross-cutting plan issue

**Constraint 7** at `2026-06-06-parsing-simplification.md:19` is the OR-loophole. By sanctioning `title_en`+`title_sl`+`slovenian_edition` as canonical-equivalent for citation-typed records, it created downstream code that writes both encodings (smol_extractor.py:529-530, 727-728, 850-851). The user's framing per the task brief is that the legacy encoding is a backwards-compat allowance, not a goal. Phase 4 and Phase 12 wording treats the OR as a choice; it should treat the legacy encoding as a transitional write-only shim with a deletion target.

---

## 3. Code-level findings

### `translate_core/tm.py` (Phase 1 modified)

Largely neutral in this file specifically; violations are inherited from the loader it delegates to.

- `tm.py:38-53` — `_load_tmx` delegates to `tm_timecodes.read_tmx_with_timecodes`. No hardcoding here, but each entry returned by the loader carries `source_lang="en", target_lang="sl"` (see §3.2). Those fields ride along into `self.entries`.
- `tm.py:89-126` — `lookup_fuzzy` searches `e["source"]` only. Because the loader normalises every entry to source=EN/target=SL, this is effectively an EN→SL lookup with no way to query the SL side. For HR→SL or SL→DE pairs this is broken (queries against the non-EN source side return nothing).
- `tm.py:128-171` — `search_concordance` checks both source and target text fields, so it is robust to language. Compliant.
- `tm.py:173-192` — `search_prefix` returns completions from `e["target"]` only. Hardcodes the target-language axis through the SL/EN normalisation in the loader. For pairs where SL is the source (e.g. HR-SL), prefix completion would suggest source-language tokens, not translation-language tokens.

### `translate_core/tm_timecodes.py` (Phase 1 created) — primary violation site

- `tm_timecodes.py:35` — docstring: *"Return the TMX header's ``srclang`` attribute (normalized) or ``en``."* Default to `"en"`.
- `tm_timecodes.py:41,42` — `return "en"` twice. Hardcoded fallback when the TMX header lacks `srclang`; the function should return `None` or surface the absence, not silently pick `en`.
- `tm_timecodes.py:80-83` — docstring: *"Source/target are normalized so the in-memory representation is always EN -> SL, matching the existing convention in `tm.py`."* This is the SL/EN-specific convention frozen into the new loader.
- **`tm_timecodes.py:95-106` — primary violation.** Pairs `<tuv>` children by hardcoded language label:
  ```python
  for tuv in tu.findall("tuv"):
      lang = _norm_lang(tuv.get(XML_LANG))
      text = _seg_text(tuv)
      if lang == "en":
          src_text = text
      elif lang == "sl":
          tgt_text = text
      # Unknown langs are ignored
  ```
  Consequence: an HR-SL, DE-SL, FR-SL, IT-SL or any other non-EN TMX produces `src_text=""` and `tgt_text` only when SL is present (and even then, only the SL side appears in `tgt_text`; nothing populates `src_text`). The `if not src_text or not tgt_text: continue` at line 122 then drops every TU. **A non-SL-EN TMX loads as zero entries.**
- `tm_timecodes.py:114-120` — defensive fallback fires only when both `src_text` and `tgt_text` are empty AND the header's `srclang == "sl"`. Does not rescue HR-SL or any other non-EN pair.
- `tm_timecodes.py:130-131` — every entry forced to `"source_lang": "en", "target_lang": "sl"` regardless of what the TMX actually contained. This is the field-level lie when the corpus expands.

### `translate_core/book_outline.py` (Phase 2 created)

Language-neutral. Regex operates on punctuation (`#`, `>`, `[-*+]`, `[.!?]`) and hyphenation rules. The capital-letter sentence-boundary regex `(?<=[.!?])\s+(?=[A-Z"…«¿¡])` favours Latin scripts but covers Slovenian and English plus several other European languages; it would degrade on Cyrillic or non-Latin scripts. Out of scope for this audit. No SL/EN string literals; no language-pair branching. **Clean.**

### `translate_core/entity_extraction/confidence.py` (Phase 3 modified)

Universal scoring logic is language-neutral. The OR-aliasing of `title_bilingual`/`has_bilingual_title` is correct (composite gate counts either alias once). One residual:

- `confidence.py:129-130` — `has_sl_edition` signal still earns `+0.10` on the legacy `cited_work` branch. The signal name itself bakes SL in. Functionally this maps to the legacy `slovenian_edition` field per ontology §2.4.2; for non-SL-target corpora the signal will never fire, so it is dead credit, not a wrong-language credit. Rename to e.g. `has_target_lang_edition` and source it from the canonical `title_translation` presence to make it neutral.
- Per-kind branches `book`, `book_chapter`, `journal_article`, `magazine_article`, `newspaper_article`, `web_source`, `exhibition_catalog`, `interview`, `thesis_dissertation`, `artwork`, `performance`, `concept`, `artist`, `agent_person`, `institution`, `festival` — all use abstract signal names (`has_title`, `has_year`, etc.). Compliant.

### `translate_core/entity_extraction/smol_extractor.py` (Phase 3 modified) — multiple violations

- `smol_extractor.py:76-77` — module-level constants `LANG_EN = "en"` and `LANG_SL = "sl"`. The constants themselves are fine (string literals tied to data values); the violation is that downstream code branches on these.
- `smol_extractor.py:82-97` — `SYSTEM_PROMPT` describes corpus as *"bilingual (EN/SL) translation segments from a Slovenian translator's corpus."* Future Croatian-source corpus would receive a prompt that misrepresents the corpus.
- **`smol_extractor.py:187-199` — `_detect_source_lang`.** Recognises only `en-sl` / `sl-en` substrings:
  ```python
  if "en-sl" in o:    return (LANG_EN, LANG_SL)
  if "sl-en" in o:    return (LANG_SL, LANG_EN)
  return (LANG_EN, LANG_SL)   # default
  ```
  A file named `big-HR-SL.tmx` falls through the default and is labelled EN→SL despite the filename advertising HR→SL. Should return `(None, None)` when no pair is recognised and surface the absence; the prompt template then needs to handle missing language tags (probably by inferring per-segment or routing to review).
- `smol_extractor.py:209` — `format_extract_prompt` calls `_detect_source_lang` and unconditionally formats `src_lang=src_lang.upper(), tgt_lang=tgt_lang.upper()` into the prompt. When the detector defaults wrong, the prompt lies to the model about which language each side is in.
- `smol_extractor.py:295-296` — `build_record(..., src_lang: str = LANG_EN, tgt_lang: str = LANG_SL)` default arguments embed the EN/SL axis. Callers (line 916) pass values from `_detect_source_lang`, so the default rarely fires — but any new caller using the default inherits the SL/EN assumption silently.
- `smol_extractor.py:460-464` — accepts legacy `title_en`/`title_sl` keys as a fallback when `title_orig`/`title_translation` are absent. This is a read-side compat allowance; acceptable per ontology §2.4.2 and constraint 7. Document with a sunset target.
- **`smol_extractor.py:529-530, 727-728, 850-851` — actively write `title_en` / `title_sl` aliases**:
  ```python
  "title_en": title_orig if orig_lang == LANG_EN else (title_translation if translation_lang == LANG_EN else None),
  "title_sl": title_orig if orig_lang == LANG_SL else (title_translation if translation_lang == LANG_SL else None),
  ```
  These three sites (cited_work, artwork, performance builders) compare `orig_lang`/`translation_lang` against `LANG_EN`/`LANG_SL` to decide which side goes into the SL/EN-shaped legacy fields. When neither orig nor translation is EN/SL (e.g. an HR→DE work), both aliases are `None`. The fields then get dropped by the `payload = {k: v for k, v in payload.items() if v is not None}` filter at lines 547/737/860 — so no false data is written, but the downstream consumers (`kg_ingest_entities.deferred_artwork`) that "expect title_en/title_sl" (line 687 docstring; line 726 comment) silently get nothing. Either migrate those consumers to read `title_orig`/`title_translation`, or document this as a known degradation for non-SL/EN works.
- `smol_extractor.py:614` — comment *"Pick canonical label (prefer EN form per ontology §2.2)"*. Ontology §2.2 says the label is *"typically EN form"* — qualified, not normative. The smol builder reads `label_orig or label_translation`, which gives EN-preference only when the segment's source language is EN. Compliant; the comment overstates.
- `smol_extractor.py:892-922` — `ingest_smol_extractions` calls `_detect_source_lang(origin)` (line 914) and passes the detected pair into the builders. Same default-to-EN-SL defect as line 199 propagates here.

### `tests/test_tm_timecodes.py` (Phase 1 new)

- `tests/fixtures/tmx_unordered.tmx` — uses `xml:lang="EN"` and `xml:lang="SL"` exclusively. A drop-test for `xml:lang="HR"` or `xml:lang="DE"` would have caught the violation in §3.2. Missing.
- `test_tm_timecodes.py` itself has no SL/EN literals; it tests structural ordering. Compliant on its own; insufficient as a regression net.

### `tests/test_book_outline.py` (Phase 2 new)

Sentences in test data are English; the segmentation logic is language-neutral so this does not bake an axis in. Compliant.

### `tests/test_confidence.py` (Phase 3 new)

Tests use only abstract signal flags (`smol_extracted`, `title_bilingual`, etc.) and never reference language codes. Compliant.

---

## 4. Recommended design

The user already gave the principle: `source` / `target` are neutral axes; `orig_lang` / `translation_lang` are language-CODE data. Detection comes from evidence; absent evidence routes to review.

### Field names

| Layer | Neutral name | Legacy SL/EN name (sunset) |
|---|---|---|
| Translation memory entries | `source` / `target` text, plus `source_lang` / `target_lang` ISO codes derived from `xml:lang` | (already neutral; values are wrong today) |
| `source_text` canonical bilingual | `title_orig`, `title_translation`, `orig_lang`, `translation_lang` | `title_en`, `title_sl`, `slovenian_edition` |
| Confidence signals | `has_target_lang_edition` | `has_sl_edition` |
| Edge: `sl_published_by` | `target_lang_published_by` (or keep the relation as `sl_published_by` if the ontology can absorb a rename) | `sl_published_by` |

The legacy column is a write-only compat shim, never the canonical write.

### Detection helpers

```python
def detect_pair_from_filename(origin: str) -> tuple[str | None, str | None]:
    """Return (src, tgt) when the filename embeds an ISO-639-1 pair, else (None, None).
    Recognises substrings of the form ``[A-Z]{2}-[A-Z]{2}`` flanked by separators.
    """
    m = re.search(r"(?<![a-z])([a-z]{2})-([a-z]{2})(?![a-z])", origin.lower())
    return (m.group(1), m.group(2)) if m else (None, None)
```

For TMX `<tuv>` pairing, do not branch on hardcoded language codes; pair by position when the header's `srclang` is available, by `xml:lang` otherwise:

```python
def split_tuv_by_srclang(tu, srclang: str | None) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (src_text, src_lang, tgt_text, tgt_lang). All four can be None.
    Caller routes to review when src_lang or tgt_lang is None.
    """
    tuvs = tu.findall("tuv")
    if len(tuvs) != 2:
        return None, None, None, None
    a_lang = _norm_lang(tuvs[0].get(XML_LANG))
    b_lang = _norm_lang(tuvs[1].get(XML_LANG))
    a_text = _seg_text(tuvs[0]); b_text = _seg_text(tuvs[1])
    if srclang and a_lang == srclang:
        return a_text, a_lang, b_text, b_lang
    if srclang and b_lang == srclang:
        return b_text, b_lang, a_text, a_lang
    # No srclang or no match: use file order, surface both langs as-is.
    return a_text, a_lang or None, b_text, b_lang or None
```

### Direction-detection in COBISS

Already specified correctly in Phase 4 (table → review). Reuse that pattern for the smol extractor: when filename does not yield a pair, prompt-emit `orig_lang: null` and let the downstream gate route to review.

### TM `lookup_fuzzy` for arbitrary pairs

Two options:

1. **Two-sided index** (preferred): add `target` to the search corpus alongside `source`, query both, return matches with a `matched_side: "source" | "target"` field.
2. **Query-language probe**: caller passes `query_lang: str`, and the lookup picks the matching side per entry via `e["source_lang"] == query_lang`. Cheaper but requires every caller to know the query language.

The editor today only knows SL→EN translation flow, so option 2 works for now if the editor passes its known direction. Option 1 is the future-proof choice once the editor handles multiple working pairs.

---

## 5. Per-phase remediation checklist

### Phase 0
- No remediation needed.

### Phase 1 (committed; needs revision)
- [ ] **Revise `tm_timecodes.py`**: stop hardcoding `lang == "en"` / `lang == "sl"` at lines 101-104. Pair `<tuv>` children by `srclang` evidence (header attribute) plus positional fallback. Carry actual `source_lang`/`target_lang` per entry from `xml:lang`, not literals.
- [ ] Default `srclang` to `None`, not `"en"` (`tm_timecodes.py:41-42`).
- [ ] Drop the `"source_lang": "en", "target_lang": "sl"` literals at lines 130-131; populate from detected `xml:lang`.
- [ ] Replace the SL-specific defensive fallback (`tm_timecodes.py:114-120`) with a srclang-agnostic positional fallback when `xml:lang` is missing on both `<tuv>` children.
- [ ] Add a test fixture `tests/fixtures/tmx_hr_sl.tmx` with `xml:lang="HR"` / `xml:lang="SL"`; assert all entries are loaded and `source_lang="hr"`, `target_lang="sl"`.
- [ ] Add a test for a TMX with header `srclang="de"` and `<tuv xml:lang="DE">`/`<tuv xml:lang="FR">`; assert loaded entries carry those codes.
- [ ] Update `tests/test_tm_timecodes.py` so the existing 4-entry fixture is a single case in a parametrised set that also covers HR-SL and an unknown-pair TMX.
- [ ] Compatibility shim: the editor reads `tm.entries[*]["source"]` as if it were always EN. Until the editor is migrated, expose `tm.entries` filtered to the working pair (SL↔EN) via a property `tm.editor_entries`, and have the editor use that. New `tm.entries` carries all pairs.
- [ ] Rewrite Phase 1's plan dispatch wording (lines 167, 192) to remove "EN-source/SL-target swap" instruction and replace with the constraint-9 routing rule.

### Phase 2 (committed; clean)
- No remediation.

### Phase 3 (committed; minor)
- [ ] Rename `has_sl_edition` signal to `has_target_lang_edition` in `confidence.py:129-130` and at every smol-builder site that sets it.
- [ ] Rewrite the plan's `:365-368` verification example to use only `(title_orig AND title_translation)` for `title_bilingual` detection. The legacy `title_en`/`title_sl` path stays in code as a read-side compat shim, but the verification example should not perpetuate it.

### Phase 4 (not started; plan compliant)
- [ ] In the test brief at `:418`, replace "Example pairs to mix in" guidance with a hard requirement: at least one of `(hr, sl)`, `(de, sl)`, `(fr, sl)` MUST appear in the test fixture set.
- [ ] In the green-step instruction at `:444`, add an explicit deletion target for the transitional `title_en` duplicate (e.g. "remove in Phase 11").
- [ ] Cross-reference Phase 5's `provenance_mismatch_for_translated_work` review reason: COBISS records with undetermined direction should also route via the same review queue.

### Phase 5 (not started; plan mostly compliant)
- [ ] Add to the routing test set (`:556`) a case for `kind="cited_work" + orig_lang absent + translation_lang absent` → review with reason `language_pair_undetermined`. Without this, smol records produced by today's `_detect_source_lang` default-EN-SL get a free pass through the chokepoint.
- [ ] Real-data check (`:585-591`): grep for "language_pair_undetermined" routing alongside `provenance_mismatch`.

### Phase 6 (not started)
- [ ] Attribution algorithm is language-neutral, but it consumes `tm.entries`. Add a test case that the chronological walker correctly attributes containers in a multi-pair TM corpus (use a fixture with at least one non-SL-EN origin).
- [ ] Surface `origin → (src_lang, tgt_lang)` in the attribution module's outputs so downstream `cited_in` edges can reason about which side of the bilingual data the citation came from.

### Phase 7 (not started; clean)
- VL stack is being deleted; no remediation needed.

### Phase 8 (not started)
- `seed_kg.py` deletion removes its `source_lang="en", target_lang="sl"` literals. Compliant by deletion. No further action.

### Phase 9 (not started)
- Concept drain is language-axis-free. No remediation.

### Phase 10 (not started)
- Add a check that ingested concept labels carry `orig_lang` / `translation_lang` populated from the curator file, not defaulted to SL/EN.

### Phase 11 (not started)
- `citation_collector.py` deletion removes the `source_lang="en", target_lang="sl"` literals. Compliant by deletion.
- Add to this phase: also delete the now-unused `title_en` / `title_sl` write paths in `smol_extractor.py:529-530, 727-728, 850-851` once `kg_ingest_entities.deferred_artwork` is migrated to canonical fields (cross-reference Phase 4's transitional-duplicate sunset).

### Phase 12 (not started)
- Spot-check brief at `:1220` — change "both languages" to "both languages of the entry's pair (`source_lang` + `target_lang`)". Run the spot-check on a non-SL-EN origin if one exists in the corpus by then.
- `validate_kg.py` should add a check: every `source_text` with `title_translation` populated MUST carry `orig_lang` and `translation_lang`. Records writing only `title_en`/`title_sl` without `orig_lang`/`translation_lang` are flagged.

---

## 6. Risk assessment — stripping EN→SL normalisation today

**Cannot be done in one step. Compatibility shim is non-optional.**

What relies on the current `tm.py` / `tm_timecodes.py` SL/EN normalisation:

- `inline_test/test_confirm_pipeline.py:62-68` — asserts `tm.entries[0]` equals a dict with literal `"source_lang": "en", "target_lang": "sl"`. Test breaks the moment `tm_timecodes.py` returns actual `xml:lang` values for an EN-SL file (would still be `"en"`/`"sl"` for today's corpus, so this specific test survives if the editor's own files are EN-SL). It breaks immediately for a future non-SL-EN file.
- `inline_test/test_confirm_pipeline.py:94` — reads `tm.entries[0]["target"]` positionally. Depends on `self.entries` natural order, which Phase 1 preserved. Not at risk from de-hardcoding the language axis.
- `tm.py:96, 173-192` (`lookup_fuzzy`, `search_prefix`) — search `e["source"]` and complete from `e["target"]`. The editor's translation flow today is SL-source-text → EN-completion (or EN-source → SL-completion, depending on the working pair). The functions only ever return matches against the EN side (because every entry's `source` is normalised to EN). If the loader stops normalising:
  - For today's SL/EN TMX entries: `source` would now be the actual `xml:lang` source side (often EN, sometimes SL depending on per-TMX header). `lookup_fuzzy` against an EN query string would miss entries where SL is the source field — i.e. a portion of the existing corpus suddenly disappears from search.
- `main.py:246-251` (per phase log) — iterates by content; order-agnostic; safe.
- `ui/*.py` consumers of `kg.extract_entities(target_lang="sl")` — operate on KG term nodes (which carry `lang` per ontology §2.1), not on TM entries directly. Not at risk.

**Required compatibility shim before any de-hardcoding lands:**

1. `tm.py` exposes a new internal index `tm._entries_by_pair: dict[tuple[str, str], list[dict]]` keyed by `(source_lang, target_lang)`.
2. `tm.entries` continues to expose the EN→SL view filtered from `_entries_by_pair[("en", "sl")] + swapped _entries_by_pair[("sl", "en")]` so the inline test and editor-search behaviour are byte-identical for today's corpus.
3. New consumers (Phase 6 attribution, future editor work) read `_entries_by_pair` and operate per-pair.
4. The inline test gets a follow-up edit: assert against a parametrised pair, not a hardcoded one.

This shim is the only way to land constraint 9 without breaking the editor on day one. Without it, the inline test fails and the live editor's TM search returns a degraded result set the first time a non-SL-EN TMX appears in `data/tm/`.

**Estimated effort:** the shim itself is small (≈ 60 LOC of `tm.py` change + 30 LOC of `tm_timecodes.py` change + 2 new test fixtures + 1 new pair-indexing test). The dependent updates (Phase 4 COBISS direction detection, Phase 5 router, Phase 6 attribution) all benefit from real per-entry language codes; in many cases the dependent work gets *simpler* once the shim is in place.

**Risk of NOT remediating now:** the moment the user drops `2027-HR-SL.tmx` into `data/tm/`, the loader silently ignores it. The editor's TM search returns zero hits for any HR query. The smol extractor's `_detect_source_lang` mislabels every segment as EN→SL. Every cited_work record produced from that TMX carries the wrong `orig_lang`. The KG accumulates EN-labelled HR data. This is a data-corruption defect, not a feature gap.
