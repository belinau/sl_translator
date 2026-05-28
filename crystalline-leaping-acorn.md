# Bilingual humanities translation workspace — superior intel layer

## Context

The translator is producing Slovenian translations of English humanities texts (currently a 1,694-segment feminist/queer/crip scholarship monograph). They have lost faith in the current intel surface because:

- KG returns only single English words (theoretical *concepts* in humanities live in 2–3-word phrases — "imagined futures", "compulsory able-bodiedness")
- Translations are buried in sub-sections instead of being the visual focal point
- Sibling concepts come back in source language (useless when writing target)
- Concordance bloats the viewport with sentences far longer than typical segments
- Tabs added unnecessary clicks during translation flow

The goal isn't to patch these. It's to design a translator-flow-aware intel layer that's **superior to Trados/memoQ/OmegaT** because it exploits something they don't have: a humanities-curated bilingual concept graph (920k nodes, 3.3M edges, including 16k multi-word terms and 26k concept nodes with `instantiates_concept` relations).

This document defines what "superior" means concretely, designs the layer accordingly, and specifies real-humanities-content tests.

## Translator workflow — the hard truth about the human in the loop

A humanities translator's per-segment loop:

1. **Parse source.** Identify (a) theoretical terms, (b) named entities, (c) idiom/style features, (d) cited works.
2. **Probe memory.** Have I translated this whole sentence before? (TM ≥95%) Have I translated this *concept* before? (KG concept hits) Is the target term established in glossary?
3. **Resolve terminology.** For each theoretical term: is there a canonical Slovenian rendition? If yes, use it. If no, do I have *prior renderings in this corpus* I should align with?
4. **Compose target.** Write the Slovenian draft. The KG/glossary/TM should feed predictions so I'm not retyping established renderings.
5. **Verify.** Glance: did I miss a term? Did I rephrase consistently with my own corpus? Numbers/punctuation OK?
6. **Move on.** Confirm, advance.

Steps 2–5 happen while the cursor is in the target textarea — the eye should never travel more than ~150 px below the cursor to find a reference. Tab clicks, scrolling, hover delays — all destroy flow.

**Critical insight for humanities:** the concept hierarchy matters more than fuzzy TM matches. A near-exact TM hit is rare; a *conceptually adjacent* prior translation is common. This is the unique value the KG provides that other CAT tools don't have.

## Why this beats Trados / memoQ / OmegaT / MateCat

| Feature | Existing CAT | This editor |
|---|---|---|
| Fuzzy TM matching | ✓ | ✓ (95%+ only — noise floor) |
| Glossary term recognition | ✓ | ✓ (with inline ghost prediction integration) |
| Concordance search | ✓ on demand | suppressed by default (too noisy at in-flow scale; available as drill-down) |
| Term recognition | flat list | **concept-hierarchical**: clicks reveal sibling terms across the corpus |
| Bilingual concept exploration | none | **first-class**: each KG card is `src → tgt` with sibling chips ordered target-first |
| Inline ghost predictions | none / chips | **Copilot-style ghost text** in the textarea, fed from KG/TM/glossary |
| Multi-word phrase matching | basic | **ngram-scored**: trigrams beat bigrams beat unigrams; noise unigrams suppressed |
| Domain-tuned filters | generic | humanities stopword list, confidence floor 0.6, verified-first sort |

## Architecture decisions (locked)

1. **NiceGUI high-level API only.** Every UI element is `ui.card`, `ui.row`, `ui.column`, `ui.button`, `ui.badge`, `ui.icon`, `ui.label`, `ui.separator`, `ui.html`, `ui.textarea`, `ui.input`, `ui.table`, `ui.right_drawer`, `ui.dark_mode`, `ui.dialog`, `ui.linear_progress`, `ui.switch`, `ui.dropdown_button`, `ui.item`, `ui.keyboard`, `ui.notify`. No raw Vue templates, no Quasar primitives by name. JS only via `ui.add_body_html` / `client.run_javascript` for the ghost-text engine (NiceGUI has no widget for this).
2. **Quasar auto-dark handles all surfaces.** `ui.card()` with `props("flat bordered")` flips with `body.body--dark` automatically — no Tailwind `dark:` peppering, no custom-CSS surface overrides. The only CSS is the structural pixel-alignment block for the ghost-text dual-layer technique (locked from commit b5af27a).
3. **`app_state` shared module is the resource backbone.** main.py populates `app_state.tm/glossary/kg/translator/...` on startup; workspace.py reads them live. Sidesteps the NiceGUI auto-reload `__mp_main__` pitfall.
4. **Intel panel lives BELOW the editor card** in the central column. Right drawer holds the navigator only. Three sections (KG, TM, Glossary) always visible — no tabs.
5. **State + subscribers + bind_value** drive every UI mutation. `state.set_active` triggers subscriber callbacks; the editor card, navigator, and intel panel each subscribe to relevant fields. No `@ui.refreshable` top-level rebuilds.

## KG query strategy (the unique-value layer)

### Query intent

For each source segment, surface up to **5** bilingual concept hits, prioritised so the most useful appears first. A hit is useful when:

- It's a 2- or 3-word phrase (single-word hits only survive if they have a high-confidence target translation AND aren't in the noise list).
- It has at least one verified translation, OR ≥0.6-confidence translation.
- It instantiates a concept node that links to sibling terms — the *conceptual cluster*.

### Query algorithm

Given source text + (src_lang, tgt_lang):

1. **Tokenise** the source into lowercase word tokens (regex `[\w'\-]+`).
2. **Generate ngrams** in order: 3-grams → 2-grams → 1-grams.
3. **Suppress contained shorter ngrams**: once a longer ngram hits, any contained range is excluded from further consideration (so "imagined futures" prevents "imagined" and "futures" from competing).
4. **Lookup** each candidate as `term:{lang}:{phrase}` in `kg.G`, trying `src_lang` first, then `"en"`, then `"sl"` (corpus has mis-labels).
5. **Resolve to bilingual hit**:
   - `src_term`, `src_lang` from the matched node
   - `tgt_term`, `tgt_lang`, `confidence`, `verified` from `_filter_translations(node.translations)[0]` (best surviving translation)
   - `alt_translations`: up to 2 fallbacks for users who want choice
   - `related`: siblings via `_related_via_concept(node_id, preferred_lang=tgt_lang)`
6. **Sort** by ngram length descending, then frequency descending. Cap at 5.

### `_filter_translations(translations)` rules

- Skip empty / noise unigrams.
- Drop confidence < 0.6 unless `verified=True`.
- Replace term with `gender_strategies.underscore_inclusivity` when present (humanities ethics).
- Dedup case-insensitive.
- Sort: verified first, then confidence desc. Cap at 4.

### `_related_via_concept(node_id, preferred_lang)` rules

1. Walk `term -[instantiates_concept]-> concept` edges to gather concept node ids.
2. For each concept, walk in-edges `?term -[instantiates_concept]-> concept` to gather siblings.
3. Exclude the original node, exclude noise unigrams.
4. Sort key: `(s.lang != preferred_lang, -s.freq)` — target-lang first, then by corpus frequency.
5. Cap at 6.

### Why this hits humanities right

- The corpus's `instantiates_concept` edges are the curated concept hierarchy. They were built for humanities terminology specifically.
- The bigram/trigram preference matches how humanities terms are written.
- The noise filter excludes generic function-words but keeps the long tail of theoretical vocabulary.

## UI / UX layout (where every pixel goes)

### Top bar (unchanged)
- `ui.card` flat bordered, full-width, rounded-none. Contents (l→r): back arrow, filename + lang-pair label, glossary-add button, progress bar, AI auto-draft `ui.switch`, dark-mode toggle, Auto-translate `ui.button`, Export `ui.dropdown_button`.

### Editor card (unchanged structure)
- `ui.card` rounded-2xl, "active-card" outer ring.
- Header row: segment index + status badge.
- Source: `ui.card` flat bordered, source text serif. KG entities highlighted inline via regex span injection during `_highlight_entities` (already implemented).
- Target wrap: `ui.card` flat bordered + the transparent textarea + the ghost-overlay span. Pixel-aligned typography (the restored Inter+`text-rendering: optimizeSpeed`+`font-feature-settings: "liga" 0` block).
- QA warning row (above textarea, in column).
- Footer: regen button (`auto_awesome`), kbd hint, CONFIRM button.

### Intel stack — directly below the editor card

Section order (closest-to-cursor first, since these are the actionable references):

#### 1. Knowledge Graph (`ui.card` flat bordered, rounded-2xl, p-4)
- Header: hub icon + "KNOWLEDGE GRAPH" label.
- 0–5 hits, each is a `ui.card` flat bordered rounded-xl p-3.
- **Hit card row 1** — the bilingual header:
  ```
  [hub icon]  imagined futures  [en]  →  imaginirane prihodnosti  [sl]  [verified ✓ or 92%]
  ```
  Implemented as a `ui.row` with: `ui.icon` (small, primary), `ui.label` (src_term, bold), `ui.badge` (src_lang, secondary), `ui.icon("arrow_forward")`, `ui.label` (tgt_term, bold positive), `ui.badge` (tgt_lang, positive), `ui.icon("verified")` OR `ui.label` (confidence %).
  Clicking anywhere on the row → `state.client.run_javascript('window.__sl_predictor.insertAtCursor(tgt_term)')`.
- **Hit card row 2** (only if `related` non-empty) — sibling cluster:
  ```
  RELATED   [imaginarne prihodnosti]  [prihodnost]  [vizije]  ...
  ```
  Implemented as `ui.row` with a tiny `ui.label` ("RELATED") and a flex-wrap row of `ui.button` chips. Target-lang chips → `color=positive`. Source-lang chips → `color=secondary`. Each click → `insertAtCursor`.
- **Hit card row 3** (only if `alt_translations`) — alternative renderings:
  ```
  ALT   imaginarni svet (78%)   imaginirana prihodnost (75%)
  ```
  Same row pattern, smaller text.

#### 2. Translation Memory (`ui.card` flat bordered, rounded-2xl, p-4)
- Header: memory icon + "TRANSLATION MEMORY" label.
- ≥95% fuzzy matches only (≤3). Each is a `ui.card` flat bordered rounded-xl p-3 row:
  ```
  [score badge]  src: <truncated 80 chars> ↘  tgt: <truncated 80 chars>  [content_paste icon]
  ```
  Source/target lines are `ui.label` with `_truncate(text, 80)`. Click anywhere → insertAtCursor(target).
- No concordance section in the default surface. (Future: a "show concordance" trigger when the user wants drill-down.)
- Empty state: `ui.label("No near-exact matches (≥95%).").classes("text-xs italic opacity-60")`.

#### 3. Glossary (`ui.card` flat bordered, rounded-2xl, p-4)
- Header: book icon + "GLOSSARY" label.
- All glossary hits as `ui.button` chips: label = `src_term → tgt_term` (each truncated to 30 chars then joined). `color=positive`, dense, rounded.
- Click → insertAtCursor(tgt_term).
- Empty state: italic small.

### Right drawer
- `ui.right_drawer` width=380, bordered.
- Contains `ui.table` virtual-scroll over all segments (already implemented).

1
| Source | Target | Concept type |
|---|---|---|
| imagined futures | imaginirane prihodnosti | theoretical-phrase (2-gram) |
| compulsory able-bodiedness | obvezna telesna sposobnost | theoretical-phrase (3-gram) |
| intersectionality | intersekcionalnost | theoretical-singleton (1-gram, ≥4 chars) |
| crip theory | krip teorija | theoretical-phrase (2-gram) |
| feminist epistemology | feministična epistemologija | theoretical-phrase (2-gram) |
| queer phenomenology | queer fenomenologija | theoretical-phrase (2-gram) |
| common | (none — should be filtered out) | noise-unigram |
| the | (none — function word) | noise-unigram |

Fixture builds a `networkx.DiGraph` with these as `term:` nodes, links them to `concept:` nodes via `instantiates_concept`, adds Slovenian sibling terms to the same concepts, sets `verified` on canonical translations.

Tests:
1. `test_kg_query_prefers_humanities_phrases` — assert "imagined futures" (2-gram) ranks above any single-word hit when both are present.
2. `test_kg_query_filters_noise_unigrams_in_humanities_context` — assert "common", "the" don't appear in hits.
3. `test_kg_hit_is_bilingual` — assert `hits[0].src_term=="imagined futures"`, `tgt_term=="imaginirane prihodnosti"`, `src_lang=="en"`, `tgt_lang=="sl"`, `verified=True`.
4. `test_kg_siblings_target_language_first` — fake KG with EN and SL siblings for one concept; assert SL siblings come first.
5. `test_kg_caps_at_five_hits_when_corpus_is_dense` — 8 qualifying hits → exactly 5 returned.
6. `test_intel_card_renders_arrow_glyph_and_lang_badges` — visible: `imagined futures`, `→`, `imaginirane prihodnosti`, `en`, `sl`.
7. `test_intel_concordance_is_not_shown_by_default` — `should_not_see("CONCORDANCE")`.
8. `test_tm_threshold_95_excludes_low_matches` — fake TM with 90% match; assert nothing surfaces.
9. `test_glossary_chip_shows_src_to_tgt_directionality` — `should_see("compulsory able-bodiedness → obvezna telesna sposobnost")` (truncated forms).

## Implementation phases (each ends in a verified state)

### Phase A — Query shape + sibling sort
1. Refactor `_kg_query` to return bilingual hits with explicit `src_term`/`tgt_term`/`src_lang`/`tgt_lang`/`confidence`/`verified`/`alt_translations`/`related`/`n`/`freq`.
2. Refactor `_filter_translations(translations)` (drop the now-unused tgt_lang arg).
3. Extend `_related_via_concept` with `preferred_lang` keyword and the target-first sort.
4. Add `_truncate(text, n=90)` helper.
5. Type-check: `basedpyright` must report 0 errors.

### Phase B — Card rendering
6. Rewrite KG-card rendering in `_refresh_kg` to the row-1 (bilingual header) / row-2 (sibling chips) / optional row-3 (alt translations) layout.
7. Apply `_truncate` to every visible string in TM cards and remove the concordance section.
8. Rewrite glossary chips to `src → tgt` labels.

### Phase C — Tests
9. Replace the existing KG fixtures with the real-humanities builder.
10. Add the 9 tests listed above.
11. Update the three existing-but-now-stale tests (`test_intel_panel_paints_tm_on_initial_load`, `test_intel_panel_renders_kg_entities_with_relations`, `test_intel_panel_shows_all_three_sections_simultaneously`) to match the new layout.

### Phase D — Predictor parity
12. Re-route `predictions.push_bundle` to consume `_kg_query` output for the `candidates` array. Same source of truth as the visible cards.

### Phase E — Verification
13. `.venv/bin/basedpyright` reports `0 errors`.
14. `.venv/bin/python -m pytest inline_test/` reports `≥ 44 passed, 0 failed`.
15. Restart app. Open a real EN→SL segment containing at least one of the listed humanities phrases. Confirm visually:
    - KG card 1 shows `<phrase> [en] → <translation> [sl]` on one row, with sibling chips below in Slovenian.
    - No concordance section anywhere.
    - TM section either shows a 95%+ match or the "no near-exact matches" line.
    - Glossary chips show directional `→` labels.
16. Click a sibling chip → it appears at the cursor in the textarea.
17. Type the first 3 chars of a humanities phrase → ghost continuation in the overlay matches the canonical translation.

## Out of scope (deliberate cuts)

- Concept-graph visual (mermaid/echart map) — useful eventually, but not required for the in-flow workflow.
- Reverse direction lookup (typing target → discover source equivalents).
- KG path queries (shortest path between two concepts).
- Term promotion shortcut (a button that adds the current target as a new KG translation).
- Concordance drill-down trigger.

These are post-MVP features that can be designed once the in-flow surface is rock-solid.

## Acceptance gate

I will not declare complete until:
- Pyright is 0 errors across `main.py`, `ui/`, `app_state.py`, `inline_test/`.
- Every test in the test corpus above passes.
- I have driven the app end-to-end against a real segment and seen the bilingual card shape, the target-first siblings, the 95% TM cut, and the directional glossary chips.

The "verified before declaring done" pattern stays as the absolute bar.
