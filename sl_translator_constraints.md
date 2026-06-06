# sl_translator KG — hard constraints (DO NOT DRIFT)

## Ontology
- `ontology.md` is authoritative. NEVER touch it.
- 6 node types ONLY: term, concept, translation_mapping, source_text, agent, institution.
- 13 edge relations ONLY (see §3).
- Only KnowledgeGraph factory methods write to KG (O-1).

## My past inventions that violated this — NEVER repeat:
- `lineage` node type (synthetic) — FORBIDDEN
- `agent_in_lineage` / `concept_in_lineage` / `work_in_lineage` edges — NEVER existed
- `concept → attributed_to → agent` / `concept → instantiated_in → source_text` — §3.3 restricts these edges to translation_mapping as source
- `data/lineage_taxonomy.json` side file — invention
- Filter-out-translators hack — user said "I didn't order to exclude translators"
- Proximity propagation parser — "stupid shit parser"
- N-gram heuristic matching invented by me — user has own n-gram tool already

## User intent (final, authoritative):
- Every entity properly connected to its origin container (Belina's translated works)
- Bilingual extraction at all layers per ontology §2.4.2
- No terms (failed in old KG)
- V5/V6 will stay empty until user decides on term/translation_mapping layer
- Stop writing parallel scripts; use existing infrastructure

## Existing infrastructure to USE (not duplicate):
- `scripts/ingest_personal_bibliography.py` — COBISS containers
- `scripts/build_segment_attribution.py` — n-gram attribution
- `run_entity_extraction.py` — smol ingest, attribution loaders (curator + segment_to_book + ngram)
- `translate_core/knowledge_graph.py` — factory methods only

## Missing 79 containers from OLD KG (article translations, exhibition
## catalogues, festival programmes the user did but not in current COBISS):
- TASK: graft them via KG factory methods, sanitize TM-internal fields
  (anchor_origin, anchor_idx, matched_pattern, segment_window) per §4 invariant #3.
- Old KG backup at: data/knowledge.db.before_smol_promotion_20260605_225637

## Duplicates to merge:
- smol-extracted cited_works that title-match a container (e.g.
  "The Life of Art..." should merge into source:kunst-zivljenje-umetnosti)
- container-container duplicates (e.g. zaloznik-zavzemanje-prostora vs
  zaloznik-jasmina-zavzemanje-prostora-2024 — year-aware merge)

## Operational rules:
- NEVER REVERT what user has approved
- NEVER write new parser scripts
- Use existing scripts; modify them surgically if needed
- Always run via KG factory methods (add_source_text_node, link_translated_by, etc.)
