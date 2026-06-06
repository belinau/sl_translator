# Parsing & KG-ingestion simplification audit

Generated: 2026-06-06.
Scope: entity recognition, KG ingestion, container/cited-work resolution,
bilingual encoding, VL-era guards, smol pipeline.
Out of scope: NiceGUI translation editor and its document-prep (segmenter,
in-editor TMX, alignment for editing).

Live KG counts (read from `/Users/bel/CascadeProjects/sl_translator/data/knowledge.db`):
`agent=5415`, `source_text=3625`, `institution=3931`, `concept=8455`,
`term=16798`, `translation_mapping=54152`; edges include `translates_to=102180`,
`has_mapping=54152`, `maps_to=54152`, `instantiates_concept=46897`,
`instantiated_in=64111`, `attributed_to=56925`, `written_by=2672`,
`cited_in=1292`, `published_by=575`, `translated_by=185`.
Concept lineage edges (`extends`/`critiques`/`redefines`/`reappropriates`/
`related_to`): **0**. Concepts with non-empty `definition`: **0**.
No non-ontology relations are persisted; no `tm_segment` / `collocation` /
`domain` nodes are persisted; no bare-agent nodes (all 5415 carry
`dedup_group`). Persisted KG is currently clean wrt §3 / §4 invariants;
the bugs we are about to enumerate live in **source code that has not run
recently** and that would re-pollute the next ingest run.

---

## 1. Executive summary

The current pipeline has three independent ingest paths and they do not
agree:

1. The **smol path** (`run_entity_extraction.py` → `data/smol_entities_map/smol_extractions.json` → `translate_core/kg_ingest_entities.write_to_kg`) is the only entry point that uses ontology-compliant factory methods, typed `project_type` values, and the bilingual `title_orig`/`title_translation` canonical fields. Keep this as the trunk.
2. The **VL-era debris**: `translate_core/vl_extractor.py`, `translate_core/vl_parser.py`, `translate_core/vl_prompts.py`, `translate_core/vl_server.py`, `translate_core/entity_extraction/vl_typed_extractor.py`, `translate_core/entity_extraction/vl_typed_verifier.py`, `translate_core/entity_extraction/vl_citation_verifier.py`, `translate_core/entity_extraction/bilingual_enrichment_batch.py`, `translate_core/entity_extraction/bilingual_titles.py`, `translate_core/citation_collector.py`, `translate_core/entity_extraction/book_extractor.py`, `translate_core/entity_extraction/segment_classifier.py` (the unreliable parts). Mostly DELETE; a small subset of regex/classifier utilities can be REDUCED.
3. The **two "fast parser" ingesters** the user warned about: `ingest_book_bibliography.py` and `ingest_book_footnotes.py`. Both bypass `KnowledgeGraph` factories (raw `kg.G.add_edge`), invent `alt_published_by` (not in the 13-edge allowlist), write `tm_segment_refs` and `citation_type` onto `source_text` nodes (§4 invariant 3 violation), and label everything `project_type="cited_work"` instead of typed. DELETE outright; the smol path already produces typed cited_works with `cited_in` to the right container.

Three highest-leverage moves:

* **Fix TM segment ordering and the container-boundary problem at the source**: rewrite `translate_core/tm.py:36-62` to read `creationdate`/`changedate` from each TMX `<tu>`/`<tuv>` and sort segments chronologically per origin. Then drive container boundaries via `scripts/build_segment_attribution.py` (n-gram of source-DOCX text vs. TM segment) — the curator file `data/quarantine/_segment_title_attribution.json` plus that n-gram matcher already exist; promote them to the canonical attribution source.
* **Delete the two book-ingest scripts and `seeded_book_finder.py`**: they use raw `enumerate(entries)` indices, a hardcoded `DEFAULT_WINDOW=150`, "proximity propagation" with `MAX_GAP=100` — exactly the heuristics the user has called out as broken. The smol path covers their job.
* **Retire NLP-driven noise emitters**: `seed_kg.py` + `KnowledgeGraph.seed_from_tm` (`translate_core/knowledge_graph.py:856-1154`) are responsible for the 8455 concepts that have zero definitions and zero lineage edges. Gate concept creation behind a curated whitelist (`data/quarantine/_lineage_schools.json` + `_concept_theorists.json`) and curator-approved smol concept records only.

---

## 2. Pipeline map (file:line → KG)

### 2.1 TMX blobs (`data/tm/*.tmx`)
- Loader: `translate_core/tm.py:23-62` (`TranslationMemory._load_all` → `_load_tmx`). Uses `tmxfile(f).unit_iter()` — natural ordinal — and ignores all `creationdate`/`changedate` attributes (verified present in `data/tm/2022-SL-EN.tmx`).
- Origin/global-idx synthesis: `run_entity_extraction.py:687-693`. The "global_idx" is `enumerate(tm.entries)` order, which by construction is wrong (see §4).
- Per-origin context: `translate_core/entity_extraction/origin_walker.py:62-105`.
- Per-segment classification: `translate_core/entity_extraction/segment_classifier.py:1-553` (rule-based, two-pass smoothing).
- Smol prompt formatting: `translate_core/entity_extraction/smol_extractor.py:202-221`.
- After smol returns: `run_entity_extraction.py:128-390` → `kg_ingest_entities.write_to_kg` (`translate_core/kg_ingest_entities.py:463-1043`).
- Resulting node/edge types: `agent`, `institution`, `source_text` (typed `book`/`journal_article`/.../`artwork`/`performance`/`cited_work`), `concept`. Edges: `written_by`, `translated_by`, `edited_by`, `performed_by`, `published_by`, `sl_published_by`, `hosted_by`, `cited_in`, `instantiates_concept` (the smol path does not emit `translates_to`/`has_mapping`).

### 2.2 Translated book DOCX/MD pairs
- CLI: `process_document_pair.py:38-138`.
- Orchestrator: `translate_core/document_pair_pipeline.py:751-854` (`process_pair`).
- Parsing: `translate_core/doc_parser.py:316-353` with `use_vl=False`.
- Footnote/biblio extraction: `translate_core/document_pair_pipeline.py:149-200`.
- Matching: `_match_citations` at `:380-431`, threshold `0.6` at `:357`.
- KG writes: `_ingest_match` at `:560-622`. Uses `kg.add_source_text_node` and `kg.link_cited_in` — ontology-clean.
- Side-effect: TMX sidecar manifest written by `:653-695`, read by `translate_core/citation_collector.py:124-146`.
- Resulting types: `source_text` (with `title_en`+`title_sl`), `institution` (publisher), `cited_in` edges to the container.
- Two **separate, alternative** book ingesters do the same job differently and badly (see §3.3, §10 below).

### 2.3 COBISS personal bibliography
- Source: `data/personal bibliography/bibliography_belina.txt`.
- Parser: `translate_core/cobiss_parser.py` (~720 lines, deterministic regex). Produces `CobissEntry`.
- Classifier: `translate_core/cobiss_classifier.py:126-193` (`classify_entry`). Heuristics for festival/exhibition/article/book.
- Ingester: `scripts/ingest_personal_bibliography.py:91-...` builds container `source_text` nodes with `project_type ∈ {book_translation, article_translation, festival_programme, exhibition_catalogue}` for Belina-as-translator, and cited `project_type ∈ {book, magazine_article, ...}` when Belina is first-author (his own works). Wires `translated_by` to `agent:urban-belina`.

### 2.4 New smol entity map (`data/smol_entities_map/smol_extractions.json`)
- Producer: external smol/DeepSeek-flash agents (out of this repo).
- Consumer: `run_entity_extraction.py:142` reads `SMOL_EXTRACTIONS_PATH = Path("data/smol_extractions.json")` — **path mismatch**. The actual file lives at `data/smol_entities_map/smol_extractions.json`. Either the producer writes to the wrong place or the consumer reads from the wrong place. Verified: `data/smol_extractions.json` does not exist (`ls data/*.json` → only `kg_review.json`).
- Format (verified by `head` of the real file): `[{origin, seg_idx, global_idx, container_work_id, klass, entities:[…]}]`. Per-entity shape matches `smol_extractor.parse_smol_response` (`translate_core/entity_extraction/smol_extractor.py:238-285`).
- Record builders: `_build_agent_person`, `_build_institution`, `_build_cited_work`, `_build_concept`, `_build_artwork`, `_build_performance` (`smol_extractor.py:333-856`). All use ontology-compliant fields and slug discipline.

### 2.5 Quarantine outputs (`data/quarantine/`)
- See §9. Loaded by `run_entity_extraction.py:521-680` as attribution helpers.

---

## 3. VL-era debris inventory

### DELETE

- `translate_core/vl_extractor.py` (1242 lines, `translate_core/vl_extractor.py:1-50`). Hard-coded to LFM2.5-VL-1.6B at `localhost:8081`; the only non-test callers are `translate_core/citation_collector.py:411` (passes `vl_extractor=None`) and `ui/workspace.py:365` (also passes `None`). With no real caller and the user moving away from VL, this entire file is dead weight.
- `translate_core/vl_parser.py` (2322 lines, ontology §4 invariant 7 already says PyMuPDF is the primary path). Only one non-test reachable user: `translate_core/doc_parser.py:349-352` behind `use_vl=True`; in all in-scope entry points `use_vl=False` (e.g. `translate_core/document_pair_pipeline.py:445`). Delete after removing the `if use_vl` branch in `doc_parser.py` (REDUCE step there).
- `translate_core/vl_prompts.py` (123 lines).
- `translate_core/vl_server.py` (109 lines, FastAPI shim around the 1.6B VL).
- `translate_core/entity_extraction/vl_typed_extractor.py` (663 lines, two-call VL classify → extract).
- `translate_core/entity_extraction/vl_typed_verifier.py` (371 lines, post-VL field verifier — defends against hallucinated field values that smol does not produce).
- `translate_core/entity_extraction/vl_citation_verifier.py` (189 lines, "drop any field that doesn't literally appear in the source").
- `translate_core/citation_collector.py` (501 lines, see §3.1). The smol path replaces the unified-collector role entirely.
- `translate_core/entity_extraction/bilingual_enrichment.py` and `bilingual_enrichment_batch.py`. Both require an `extractor` (VL); the smol-path caller passes `extractor=None` (`run_entity_extraction.py:320`), at which point `bilingual_enrichment.enrich_titles_sl` short-circuits at `translate_core/entity_extraction/bilingual_enrichment.py:45-46`. The bilingual pairing the user wants must come from the TMX (paired tu) or from `_build_cited_work` directly populating `title_orig`+`title_translation` — both already work without this module.
- `translate_core/entity_extraction/bilingual_titles.py` (254 lines, VL-side title parser used by `book_extractor.py`).
- `translate_core/entity_extraction/book_extractor.py` (905 lines of regex citation extraction). With smol coverage the user has stated explicitly via `--no-fallback` (`run_entity_extraction.py:472-474`) that this should not run. Keep until smol coverage is confirmed complete on all 5 TMX files, then delete.
- `tools/vl_smoke.py`.
- Test files that exist only to test VL pipeline behavior: `tests/test_vl_extractor.py`, `tests/test_vl_extractor_compliance.py`, `tests/test_typed_vl_compliance.py`. `tests/test_vl_parser.py` already has a known broken test per `AGENTS.md:135`; delete the file with `vl_parser.py`.

### KEEP

- `translate_core/knowledge_graph.py` (the factories at `:386-823` are the only authorised writers).
- `translate_core/entity_extraction/smol_extractor.py` (the modern entry).
- `translate_core/entity_extraction/name_dedup.py` (used by `kg_ingest_entities.py:608-643` and by smol's `_build_agent_person:357-358`).
- `translate_core/entity_extraction/origin_walker.py` (it builds per-origin segment contexts and a profile that drives downstream noise filtering).
- `translate_core/cobiss_parser.py` + `translate_core/cobiss_classifier.py` (deterministic regex; the only authority for container provenance — ontology §2.4.1 explicitly says containers come from this bibliography).
- `scripts/ingest_personal_bibliography.py`, `scripts/build_segment_attribution.py`, `scripts/validate_kg.py`, `scripts/normalize_lineages.py`, `scripts/migrate_dedup_groups.py`, `scripts/enrich_bilingual.py`.
- `translate_core/entity_extraction/confidence.py` (scoring) **with a fix described in §3.4**.
- `translate_core/entity_extraction/segment_classifier.py` (`:1-553`). The rule-based classifier itself is fine; only the VL-prompt allowlist that lives in `vl_extractor.py:45-49` needs to go. Keep this whole file but treat its labels as soft filters for smol prompt selection.
- `translate_core/entity_extraction/__init__.py`, `multi_segment_merge.py`, `docx_footnote_parser.py`, `footnote_parser.py`.
- `process_document_pair.py` + `translate_core/document_pair_pipeline.py` (only uses `use_vl=False`).

### REDUCE

- `translate_core/doc_parser.py:316-353`. Strip the `if use_vl and source.suffix.lower() == ".pdf"` branch (lines 349-353). After that, `vl_parser.py` is unreachable and can be deleted.
- `translate_core/kg_ingest_entities.py:35-65`. The triple-allowlist defining `ROLE_ALLOWLIST`/`KIND_ALLOWLIST`/`CONTAINER_TYPES`/`CITED_TYPES`/`STYLE_ALLOWLIST` is duplicated verbatim in `translate_core/entity_extraction/smol_extractor.py:51-77` and again in `translate_core/cobiss_classifier.py:26-57` and `scripts/validate_kg.py:26-43`. Move to one module (e.g. `translate_core/entity_extraction/ontology_allowlists.py`) and import. The VL prompt-example placeholder enforcement that lives inside `vl_typed_extractor.py` becomes irrelevant once those files are deleted.
- `translate_core/entity_extraction/confidence.py`. The "review queue is non-bypassable" invariant (ontology §4 invariant 9) is violated by the `smol_verified_classification +0.60` blanket bump at `:73-78` combined with the `smol_extracted +0.15` and `verified_from_text +0.10` universal bumps; almost every smol record clears 0.85 and bypasses review. Reduce to: require either curator endorsement (`signals.curator_endorsed`) or two-of-three bilingual + container-attached + typed-project to hit DIRECT_WRITE. The +0.60 single-flag write should not exist.

---

## 4. Container-work boundary problem

The user's framing is exactly right: **the natural `enumerate(tm.entries)` index does not order segments chronologically; TMX `creationdate` does**. Sources of evidence:

- `data/tm/2022-SL-EN.tmx` carries `creationdate` on the `tmx` header AND `changedate`/`creationdate` per `<tu>`. Verified with grep.
- `translate_core/tm.py:36-62` ignores every timecode.
- `seeded_book_finder.find_book_anchors` (`translate_core/entity_extraction/seeded_book_finder.py:107-178`) walks `for global_idx, e in enumerate(entries)` and uses that index to claim segment_ranges with `segment_window=150` (or `manifest.default_window` at `:33`). Wrong both because the index is the wrong order AND because 150 contiguous segments is structurally insufficient.
- `run_entity_extraction.py:656-677` then runs "proximity propagation" with `MAX_GAP=100` to fill *between* attributions whose bookends agree. Compounded wrong: the bookends' `local_map[s1]` and `local_map[s2]` indices are themselves natural-order positions.
- `bilingual_tm_matcher.py:50-61` then writes those `global_idx` values to `tm_segment_refs` on `source_text` nodes (which would also be a §4 invariant 3 violation if ever run, see §10.5).

### Recommended approach

**Primary**:

1. Rewrite `translate_core/tm.py` to read TMX with the `lxml` parser (or `tmxfile` + dropping into `<tu>` element attrs), pulling `creationdate`/`changedate` per TU. Sort each origin's entries by `creationdate`. Expose a `t_index` (chronological) alongside `raw_index` (natural). All downstream attribution should use `t_index`.
2. Treat each known container title (from COBISS / `_segment_title_attribution.json`) as the anchor for **one or more contiguous chronological windows**. The end-of-work signal is **the chronological gap before the next container's first anchored segment**. Specifically: for each origin, list `(t_index, container_id)` pairs (curator file + n-gram match from `scripts/build_segment_attribution.py`), sort by `t_index`, and treat container ownership as the most recent container ID until the next change. This requires NO `MAX_GAP` heuristic — segments inherit from the previous chronological anchor by definition.
3. Cross-validate against `scripts/build_segment_attribution.py` (already does n-gram match of source DOCX text against TM segments; output already in `data/quarantine/_segment_title_attribution.json`). Where the curator file and the n-gram match agree, lock the boundary; where they disagree, queue for review — do NOT auto-resolve.

**Fallback**:

- For TMX origins with no matched source DOCX (the COBISS container exists but the DOCX wasn't located), fall back to **language-direction switches and footnote-density bursts**. Both signals already live in `segment_classifier`'s classes (`BIBLIOGRAPHY_ENTRY`, `FOOTNOTE`, `INLINE_CITATION`). Implement as: a sustained run (≥5 segments) of high biblio/footnote density adjacent to BODY_TEXT marks the end of one book and beginning of the next book's apparatus. Mark as low-confidence and queue.

### Files that should own this

- New: `translate_core/tm_timecodes.py` — TMX timecode reader, returning entries with `t_index`.
- Modify: `translate_core/tm.py:36-62` to consume the new module and sort.
- Modify: `scripts/build_segment_attribution.py` (already does the n-gram half; teach it to emit `t_index` ranges instead of raw idx ranges).
- DELETE: `translate_core/entity_extraction/seeded_book_finder.py` (replaced by the chronological-anchor logic).
- DELETE: `run_entity_extraction.py:651-677` ("Proximity propagation: fill gaps").
- DELETE: `run_entity_extraction.py:586-649` (the brittle "stem-prefix resolve" of ngram_attribution against current KG container slugs).

### Existing naive-index slice sites (all wrong)

- `translate_core/tm.py:46-62` (the loader itself).
- `run_entity_extraction.py:687-693` (`origin_offsets` keyed by first-occurrence in `enumerate(entries)`).
- `run_entity_extraction.py:78-114` (`export_segments` writes `global_idx = origin_start + seg_idx` to the smol JSON — this index then re-enters as `r["source"]["segment_idx"]`).
- `translate_core/entity_extraction/seeded_book_finder.py:127-148, 60-64, 187-194` (`BookClaim.contains` and `claim_for_segment` all use global_idx).
- `translate_core/entity_extraction/bilingual_tm_matcher.py:50-61` (`global_idx` baked into `tm_segment_refs`).
- `ingest_book_bibliography.py:117` (`tm_segment_refs` attribute).
- `ingest_book_footnotes.py:187, 201-205` (`tm_segment_refs` accumulator).
- `translate_core/entity_extraction/bilingual_enrichment.py:89-93` (`seg_idx_int < 0 or seg_idx_int >= len(entries)` index lookup).

---

## 5. Container vs. cited-work separation

The ontology is clear (§2.4.1 + §3.2): containers come from the personal/COBISS bibliography and get `translated_by`; cited works come from a book's own bibliography/footnotes and get `cited_in` to the container.

**Current chokepoints**:

- COBISS → containers: `scripts/ingest_personal_bibliography.py` (correct; ontology-compliant).
- Smol → cited works: `translate_core/entity_extraction/smol_extractor.py:454-569` (correct; produces `cited_work` with `container_work_id`, then `kg_ingest_entities.py:656-779` writes the `cited_in` edge).

**Conflation risk sites**:

- `translate_core/entity_extraction/seeded_book_finder.py:197-241` (`book_claims_to_records`) emits records with `kind="translated_work"` from TM title-anchor matches — i.e. it *promotes a TM-detected book to a container*, parallel to the COBISS path. This is the worst structural conflation: a smol-detected title in a TM segment becomes a container without curator approval. DELETE.
- `run_entity_extraction.py:148-156` (`seeded_records = book_claims_to_records(book_claims); all_records.extend(seeded_records)`). Same — TM-detected containers slip into the same ingest stream as COBISS containers.
- `ingest_book_bibliography.py:114` (`"project_type": "cited_work"` unconditionally for bibliography-parsed entries). Currently correct in direction (cited side) but loses type information.
- `translate_core/kg_ingest_entities.py:514-547`. The translated_work path writes `extra["project_type"] = p["project_type"] if p.get("project_type") in CONTAINER_TYPES else "book_translation"` — so a cited type accidentally sent here would silently become `book_translation`. Tighten the gate: reject (route to review) any `translated_work` record whose `project_type` is not in `CONTAINER_TYPES`.

**Recommended chokepoint**:

A single dispatcher function inside `translate_core/kg_ingest_entities.py` that takes a record, decides `kind ∈ {container, cited}` from its provenance (COBISS-or-not), and routes to either the `translated_by` path or the `cited_in` path. Provenance flag should be on `record["source"]["provenance"]` ∈ `{"cobiss_personal", "book_bibliography", "tm_smol", "doc_pair"}`. Reject `kind="translated_work"` records that aren't `provenance="cobiss_personal"`.

---

## 6. Noise-term cleanup vs. lineage preservation

The numbers are stark:
- 8455 `concept` nodes in the live KG.
- **Zero** of them have a non-empty `definition`.
- **Zero** participate in any `extends`/`critiques`/`redefines`/`reappropriates`/`related_to` edge.
- 16798 `term` nodes; the top-5 by frequency are `art`, `one`, `work`, `between`, `time` — stop-word-grade noise.

The good lineage material lives **on disk in `data/quarantine/`**, NOT in the KG:
- `data/quarantine/_concept_theorists.json` — curated `concept → originating-theorist` map (rich; humanities-correct).
- `data/quarantine/_lineage_schools.json` — curated `agent → school-of-thought` map.

So we have the lineage data; the writer never consumed it.

### Mass noise emitters

- `translate_core/knowledge_graph.py:856-1154` (`KnowledgeGraph.seed_from_tm`). Creates one `concept` node per surface-form pair from spaCy noun chunks + Classla/Stanza dependency phrases, with `definition=""` and `domain` set to the freeform parameter. Only caller: `seed_kg.py:159`.
- `seed_kg.py:99-178`. Walks every TMX file once, calls `seed_from_tm`. Also treats each TMX *filename* as the source_text title (`:152`), conflating multiple translated books into one synthetic container — see §5.
- `translate_core/knowledge_graph.py:1128-1140` (inside `seed_from_tm`'s dice loop). Adds a `concept` node and two `instantiates_concept` edges for every dice-passing pair — this is the source of the 46897 `instantiates_concept` edges.

### Curated lineage writers (KEEP)

- `translate_core/knowledge_graph.py:448-462` (`link_concepts_rhizomatic`). The valid-relation enforcement (`extends`/`critiques`/`redefines`/`reappropriates`/`related_to`) is correct.
- `kg_editor_ui.py:409-413, 575-582, 687-690` (`add_concept_node` calls). Curator UI — keep.
- `translate_core/kg_ingest_entities.py:952-1027` (concept deferred-pass). Concepts that come from smol (`smol_extractor._build_concept` at `:572-657`) require `originating_author` and `source_work_title` to score above review threshold — already gated correctly.

### Recommendation

1. **Retire `seed_kg.py`** and `KnowledgeGraph.seed_from_tm` (delete the function from `knowledge_graph.py:856-1154`). They violate ontology §6 in spirit by emitting `concept`/`term` nodes at scale without curator participation. The factories `add_collocation_node` (`:545`), `add_segment_node` (`:571`), `add_domain_node` (`:596`) are already listed as forbidden in ontology §6; remove them too.
2. **Drain existing noise nodes** without harming curated lineages: a one-off cleanup script that drops any `concept` with `definition==""` AND no participation in lineage edges AND no smol `originating_author` provenance. The 8455 figure suggests all of them currently qualify for deletion.
3. **Wire the curated lineage data**: a new ingester reads `data/quarantine/_lineage_schools.json` and `data/quarantine/_concept_theorists.json`, calls `add_concept_node(..., definition=<from curator notes>)`, `link_concepts_rhizomatic`, and `link_attributed_to` per ontology §3.3/§3.4.
4. **Smol concept records** continue to land through `kg_ingest_entities._build_concept`-driven path with the confidence gate fix from §3.4.

---

## 7. Bilingual completeness

The ontology §2.4.2 canonical encoding is `title_orig` + `title_translation` (plus `orig_lang`/`translation_lang`), or `title_en` + `title_sl` (plus `slovenian_edition` when SL edition differs). The smol pipeline writes both correctly (`smol_extractor.py:455-547`); but several other writers degrade to unilingual.

Defects (file:line, expected behaviour):

- `ingest_book_bibliography.py:99-128`. Writes only `title_short`/`subtitle`/`container_title` — NO `title_orig`/`title_translation`/`title_en`/`title_sl`. Should populate the bilingual pair by matching the parsed citation against the TMX (the matcher *runs* at `:228` but its output `tm_segment_refs` is stored on the node instead of being mined for the SL surface form). DELETE this script (§10).
- `ingest_book_footnotes.py:171-189`. Same defect, plus stores `footnote_numbers` directly on the persisted node.
- `translate_core/document_pair_pipeline.py:596-608` (`_ingest_match`). Stores `title_en`/`title_sl` but never `title_orig`/`title_translation`/`orig_lang`/`translation_lang` — which means an SL-original book translated to EN would get its original-language title under `title_sl` and its translation under `title_en` (wrong way around).
- `translate_core/entity_extraction/seeded_book_finder.py:206-212` (`book_claims_to_records`). The assignment of `title_en`/`title_sl` is conditional on `translation_lang == "en"/"sl"` (correct logic) but the BookClaim payload always sets `title_orig`/`title_translation`, so the smol-side ingest can read those. After this script is deleted, this gap moves to whichever container source replaces it (COBISS); `cobiss_parser.CobissEntry` already has `title` + `title_en` (`translate_core/cobiss_parser.py:42-44`) which is the SL+EN bilingual encoding.
- `translate_core/cobiss_classifier.py:88-94, 101-110`. Title classification uses `title + " " + title_en` for keyword scanning — that's fine — but `scripts/ingest_personal_bibliography.py` should ensure `title_orig=cobiss_entry.title, title_translation=cobiss_entry.title_en, orig_lang="sl", translation_lang="en"` is set on the persisted container node. Currently it sets `title=cobiss_entry.title` only (verifiable around line 100-200; I read only the first 120 lines of that script).
- `translate_core/kg_ingest_entities.py:518-540`. The translated_work pass *does* pass through `title_orig`/`title_translation`/`orig_lang`/`translation_lang` if present in the payload — but only if the upstream record populated them. COBISS doesn't (see previous bullet); the smol seeded_book records do.

`smol_extractor._build_artwork:699-718` and `_build_performance:816-834` correctly populate both canonical and legacy bilingual fields — leave these alone.

---

## 8. Smol entity map integration

Current location: `data/smol_entities_map/smol_extractions.json` (10 MB, ~thousands of `{origin, seg_idx, entities[...]}` records). Format inspected directly; matches `smol_extractor.parse_smol_response` shape and `_build_*` builders.

Producer: external smol/DeepSeek-flash agents dispatched via the OMP harness (the user's words; `translate_core/entity_extraction/smol_extractor.py:1-15` documents this).

Consumer: `run_entity_extraction.py:60` reads `Path("data/smol_extractions.json")` — wrong path; the actual artifacts are under `data/smol_entities_map/`. Either fix `SMOL_EXTRACTIONS_PATH` or rename the directory. Recommend: point the constant to `data/smol_entities_map/smol_extractions.json`.

**Cleanest path to making smol primary**:

1. Move `SMOL_EXTRACTIONS_PATH` to `data/smol_entities_map/smol_extractions.json`.
2. Default `args.no_fallback = True` in `run_entity_extraction.py:472-474`, then delete the `book_extractor` fallback at `:209-224` and delete `translate_core/entity_extraction/book_extractor.py`.
3. Promote smol's `seg_idx` to `t_index` once `tm.py` is fixed per §4 — both producer and consumer need to agree on which index they speak.
4. Delete `seeded_records` branch (`run_entity_extraction.py:148-156`) — smol container detection already comes through the smol payload's `container_work_id` field, populated upstream from the curator attribution.
5. Delete `seeded_book_finder.py` entirely (§4 / §5).

Classes/functions that should now accept smol records directly as input:
- `translate_core/kg_ingest_entities.write_to_kg` (already does — keep as the single sink).
- `translate_core/kg_ingest_entities.aggregate_agent_signals` and `aggregate_institution_signals` (already do).
- `translate_core/kg_ingest_entities.dedup_records` (already does).

---

## 9. Quarantine review

Contents of `data/quarantine/` (size, role):

| File | Size | Verdict |
|---|---|---|
| `knowledge.db.good4534` | 79 MB | Reference KG snapshot. KEEP as backup. |
| `_segment_title_attribution.json` | 41 KB | Curator-validated segment→container map. Authoritative for §4. PROMOTE to `data/segment_title_attribution.json` and load directly. |
| `_lineage_schools.json` | 13 KB | Curated theorist→school map (e.g. Foucault → post-structuralism). Authoritative lineage source for §6. PROMOTE. |
| `_concept_theorists.json` | 23 KB | Curated concept→originating-author map. Authoritative for §6. PROMOTE. |
| `_concept_class.json` | 1.0 MB | Per-concept `{label, theory:bool, canonical, theorist}` — many entries are pure noise ("kasimir family", "relatives", "friends"). The theory=true subset is curator-vetted; the rest is the noise-emitter output. FILTER (keep `theory==true`), then PROMOTE the filtered subset. |
| `_agent_container_mapping.json` | 122 KB | Maps `agent:X → list of container source_text IDs`. Useful as a cross-check for §5. KEEP as derived artifact, do NOT re-ingest. |
| `_agents.json` | 170 KB | Bulk agent extraction snapshot. KEEP as a backup. The live agent layer is already populated. |
| `_article_docx_map.json` | 27 KB | Maps article slugs to source DOCX paths. KEEP — useful for §4 fallback. |
| `_citation_to_container.json` | 54 KB | Maps cited_work id → container source_text id. Per-segment provenance — already inferable from current `cited_in` edges, useful as cross-check only. |
| `_cohort.json` | 782 KB | Looks like batched dedup-candidate cohorts. Inspect contents before discarding; likely curator-staging. KEEP pending review. |
| `_orphan_tmx_matches.pkl` | 23 MB | Pickled orphan-segment matches. Quarantine; do NOT auto-promote (pickle + size = trust risk). |
| `_performances_artworks.json` | 288 KB | Performance/artwork records pre-smol. The smol path now produces these directly — RETIRE once smol coverage is confirmed. |
| `_segment_attribution_ngram.json` (referenced by `run_entity_extraction.py:586` though not in `ls` — may be derived on-demand by `scripts/build_segment_attribution.py`) | — | DERIVED. Always regenerate from `build_segment_attribution.py`; do not re-curate. |
| `_segment_to_artwork.json` | 58 KB | Per-segment artwork attribution; useful for §4 cross-check. KEEP. |
| `_segment_to_book.json` (referenced by `run_entity_extraction.py:553-580`) | — | Short book-key → container slug map; can be folded into `_segment_title_attribution.json` after PROMOTE. |
| `_skrb_footnotes.json` | 48 KB | Per-book footnote dump for Kunst's book. After §8 promotion: re-ingest via smol or delete. |
| `_zavzemanje_footnotes.json` | 98 KB | Same for Založnik's book. |
| `_final_orphan_*.json/txt` | small | Audit trail. KEEP for forensic value. |

---

## 10. "Fast parser + fake results" anti-pattern hits

(File:line — what's assumed — why wrong — minimal fix.)

### 10.1 `translate_core/tm.py:36-62`

Assumes natural `unit_iter()` order is the document order of the source book(s). Wrong: TMX `creationdate` is the only valid chronological signal and is ignored. Downstream code consumes `entries[i]` as if it had semantics. Fix: read `creationdate`, sort per origin, expose chronological index.

### 10.2 `translate_core/entity_extraction/seeded_book_finder.py:107-178`

Assumes the first title-pattern match in `enumerate(entries)` is the start of a book and a hard-coded 150-segment window is its body. Wrong on both counts; user has explicitly called the proximity-propagation / window heuristics "stupid shit parser" (per `sl_translator_constraints.md:16`). Fix: delete file; rely on chronological-ordered curator attribution per §4.

### 10.3 `run_entity_extraction.py:586-677`

Resolves ngram-attribution slugs to KG container IDs via prefix match against `prefix_to_container[stem]` (`:602-608`), then runs "proximity propagation" with MAX_GAP=100 (`:656-677`). Assumes (a) any KG container whose slug shares a prefix with the ngram slug is *that* container, and (b) two attributed segments at most 100 raw-index apart must belong to the same container. Both unsafe. Fix: delete entire block; replace with the chronological-anchor scheme from §4.

### 10.4 `run_entity_extraction.py:148-156`

`seeded_records = book_claims_to_records(book_claims); all_records.extend(seeded_records)`. Promotes TM-detected book candidates to container `translated_work` records on parity with COBISS containers. Fix: drop; containers only come from COBISS.

### 10.5 `ingest_book_bibliography.py:117, 134, 144, 161, 169, 181, 199, 205`

Bypasses `KnowledgeGraph` factory methods (O-1 violation). Invents `relation="alt_published_by"` at `:183` which is not in the 13-edge allowlist. Writes `tm_segment_refs` and `alt_publishers` onto the `source_text` node (§4 invariant 3 — segments do not live in KG). Writes `project_type="cited_work"` regardless of the parsed citation type. Fix: DELETE.

### 10.6 `ingest_book_footnotes.py:187, 192-205, 218-253`

Same factory bypass and `tm_segment_refs` leak. Also writes `footnote_numbers` directly on the persisted node and accumulates more on rerun (`:194-205`). Fix: DELETE; smol covers footnote-derived `cited_work`s with `cited_in` to the right container.

### 10.7 `seed_kg.py:152-169`

Assumes one TMX file = one source_text container. Wrong: a TMX file is a translation memory pool that mixes many books and articles. Calls `seed_from_tm` with `default_source_id=source_id` (the filename), wiring every concept and translation_mapping for that file to a fake container. Fix: DELETE.

### 10.8 `translate_core/knowledge_graph.py:856-1154` (`seed_from_tm`)

Assumes spaCy/Classla noun-chunks + dice co-occurrence are semantically meaningful concepts. Empirically: 8455 created concepts, 0 definitions, 0 lineage edges. Fix: DELETE the method; concepts only come from curator + smol with `originating_author`.

### 10.9 `translate_core/entity_extraction/bilingual_tm_matcher.py:107-...`

Per-citation pre-compiled regexes over all TM entries; the matched-form gets a confidence number based on signal count, not on linguistic verification. Its outputs are only consumed by `ingest_book_*.py` (which are themselves being deleted). Fix: DELETE once §10.5 / §10.6 are gone.

### 10.10 `translate_core/entity_extraction/confidence.py:73-78`

Assumes any record that carries `smol_verified_classification=True` is curator-grade (+0.60 universal). But every smol record sets this flag via `smol_extractor._build_*` (e.g. `:394` for agents, `:449` for institutions, `:567` for cited_works). Net effect: smol records cannot fall below 0.85 → DIRECT_WRITE, bypassing the review queue and violating §4 invariant 9. Fix: replace `+0.60` blanket bump with a 2-of-3 composite (bilingual + container-attached + typed-project) capped at +0.30.

### 10.11 `translate_core/citation_collector.py:174-226`

`collect_from_tmx` loads a TMX via `tm._load_tmx`, iterates `enumerate(tm.entries)` (same wrong-order bug), and emits `CitationSnippet(segment_idx=i)`. Same naive-index problem propagated into snippet metadata. Fix: drop once `tm.py` is fixed and `citation_collector.py` itself is retired (§3 DELETE list).

### 10.12 `translate_core/vl_extractor.py:70-83`

`_log_rejected_segment` writes diagnostic JSONL by best-effort, swallowing errors silently. Not load-bearing, but reflects the "VL produced bad output, log and move on" pattern that no longer needs to exist.

---

## 11. Deletion shortlist

- `translate_core/vl_parser.py` — old VL book parser; only `use_vl=True` reaches it.
- `translate_core/vl_extractor.py` — old VL entity extractor; no live caller.
- `translate_core/vl_prompts.py` — accompanying prompts.
- `translate_core/vl_server.py` — local VL FastAPI shim.
- `translate_core/entity_extraction/vl_typed_extractor.py` — two-call VL pipeline.
- `translate_core/entity_extraction/vl_typed_verifier.py` — VL field verifier.
- `translate_core/entity_extraction/vl_citation_verifier.py` — VL citation verifier.
- `translate_core/entity_extraction/bilingual_enrichment.py` — needs an extractor (None at the only caller).
- `translate_core/entity_extraction/bilingual_enrichment_batch.py` — same dependency.
- `translate_core/entity_extraction/bilingual_titles.py` — VL-era title parser.
- `translate_core/entity_extraction/book_extractor.py` — regex citation extractor; smol replaces it.
- `translate_core/entity_extraction/seeded_book_finder.py` — naive-index seeded "books-in-TM" detector.
- `translate_core/entity_extraction/bilingual_tm_matcher.py` — only consumers are the two book-ingest scripts below.
- `translate_core/citation_collector.py` — VL-orchestrating snippet collector.
- `translate_core/citation_collector.py`'s TMX manifest writer is harmless but goes with its file.
- `ingest_book_bibliography.py` — bypasses KG factories, invents `alt_published_by`, leaks `tm_segment_refs`.
- `ingest_book_footnotes.py` — same problems plus persistent `footnote_numbers`.
- `seed_kg.py` — one-TMX = one-container conflation; calls into the noise emitter.
- `KnowledgeGraph.seed_from_tm` (`translate_core/knowledge_graph.py:856-1154`) — emits the 8455 noise concepts.
- `KnowledgeGraph.add_collocation_node` (`translate_core/knowledge_graph.py:545-569`), `add_segment_node` (`:571-594`), `add_domain_node` (`:596-604`) — already declared out-of-scope by ontology §6.
- `tools/vl_smoke.py` — VL smoke test driver.
- Tests: `tests/test_vl_extractor.py`, `tests/test_vl_extractor_compliance.py`, `tests/test_typed_vl_compliance.py`, `tests/test_vl_parser.py`.
- `data/quarantine/_orphan_tmx_matches.pkl` — 23 MB pickle; not promotable without curator pass.
- `data/quarantine/_performances_artworks.json` and the `_skrb_footnotes.json` / `_zavzemanje_footnotes.json` once smol coverage is verified for the two named books.
- The "review-queue" diagnostic file `data/extraction_rejected_for_review.jsonl` (referenced at `translate_core/vl_extractor.py:67`) — orphans with the VL extractor.

Note: deleting the `add_collocation_node` / `add_segment_node` / `add_domain_node` factories requires verifying nothing imports them. `grep -rn "add_collocation_node\|add_segment_node\|add_domain_node"` should be clean before removal; quick check shows no callers in the in-scope modules.

---

## 12. What NOT to change

(Per the user's hard scope boundary — translation-editor doc prep stays untouched.)

- `main.py` — NiceGUI app entry point.
- `ui/workspace.py`, `ui/segment_editor.py`, `ui/segment_navigator.py`, `ui/intel_panel.py`, `ui/kg_search.py` — editor surfaces. Note: `ui/workspace.py:545-546` does call `kg.add_term_node` to wire terms from a confirmed segment; that is in-editor termbase work and stays.
- `kg_editor_ui.py` — curator UI for the KG; calls `add_concept_node`, `link_concepts_rhizomatic`, `add_term_node` directly with curator-provided data. Stays.
- `import_book.py` — book import for the editor; uses `vl_parser.split_paragraphs` (`:112`) and `BookOutline` (`:147`). If `vl_parser.py` is fully deleted those imports need a thin shim or this whole script also needs to move off VL — but the user has excluded editor doc-prep, so I am calling this OUT OF SCOPE for the deletion list above and noting only that any deletion of `vl_parser.py` must also retain or refactor `split_paragraphs` and `BookOutline`.
- `app_state.py`, `config.py`, `translate_core/tm.py`'s **search APIs** (`lookup_fuzzy`, `search_concordance`, `search_prefix`) — used live by the editor; only the loader (`_load_all` / `_load_tmx`) needs the §4 fix.
- `translate_core/llm.py`, `translate_core/glossary.py`, `translate_core/qa.py` — translation/QA surfaces.
- `visualise_kg.py` — KG visualisation.
- The COBISS pipeline (`translate_core/cobiss_parser.py`, `translate_core/cobiss_classifier.py`, `scripts/ingest_personal_bibliography.py`) stays as the authoritative container source.
- `translate_core/knowledge_graph.py` factory methods (everything *except* `seed_from_tm`, `add_collocation_node`, `add_segment_node`, `add_domain_node`) — these are the single legal write surface.

---

## Appendix A — uncertainties

- `data/extraction_review.json` is not present at audit time; only `data/kg_review.json` (170 KB, contains placeholder-year/title cases). I could not verify the current size of the review queue per ontology §4 invariant 9.
- I did not verify whether `scripts/ingest_personal_bibliography.py` populates `title_orig`/`title_translation` on container nodes; I read only the first 120 lines. The persisted KG shows `book_translation=93` container nodes, which is consistent with COBISS having ingested but does not by itself prove bilingual completeness.
- The `data/smol_entities_map/` directory contains a single 10 MB `smol_extractions.json`. Whether earlier smol runs were appended to this same file or replaced wholesale, I could not determine.
- `docs/citation-extraction-spec.md` is referenced normatively from `ontology.md` (e.g. §2.4.1 / §4 invariant 6) but **does not exist** in the repo. The audit treats `ontology.md` plus the role/kind/type allowlists in `translate_core/kg_ingest_entities.py:38-65` and `scripts/validate_kg.py:26-43` as the operative constraint set. If the spec file is expected to ship, that is a separate gap.
- `agent` count of 5415 in the live KG vs. 2672 `written_by` edges plus 185 `translated_by` plus 56 `performed_by` etc. implies many agents have no role-bearing edges. I did not enumerate which.
