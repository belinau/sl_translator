# Agent Context — sl_translator parsing simplification

Single doc every subagent reads. Brief per-task prompts may say
"read `docs/AGENT_CONTEXT.md`" + the task — no need to repeat constraints.

## Mission

Simplify the parsing/KG pipeline after migration to smol/DeepSeek-flash
(replacing the previous 1.6B VL model). Strip VL-era guards. Make the
KG language-neutral end-to-end. Don't break the NiceGUI translation
editor.

## Key data paths

- Live KG: `data/knowledge.db` (NetworkX DiGraph serialized as JSON)
- TMs: `data/tm/*.tmx`
- Personal bibliography (COBISS): `data/personal bibliography/bibliography_belina.txt`
- Curator-extra containers: `data/extra_containers.json` (added in Phase 4)
- Smol extractions: `data/smol_entities_map/smol_extractions.json`
- Ontology spec: `ontology.md` (post-Phase-4 shape)
- Plan: `docs/superpowers/plans/2026-06-06-parsing-simplification.md`
- Phase 4 blueprint: `docs/phase4_blueprint.md`

## Hard constraints (binding on every subagent)

1. **Language neutrality (KG side).** No SL/EN-named fields, edges, or
   identifiers in the KG ontology or in new code writes. Neutral fields
   only: `title_orig` + `title_translation` + `orig_lang` + `translation_lang`,
   `translation_edition: {publisher, city, year, translator, language}`,
   `translation_published_by` edge. Language codes (`"sl"`, `"en"`, `"hr"`,
   ...) appear ONLY as VALUES being written based on evidence — never as
   field names, edge names, class names, or flow-control conditionals.

2. **No blind direction assignment.** When no evidence supplies
   direction, route to curator review. Don't guess.

3. **No backward-compat fallbacks.** Patterns like
   `d.get("title_orig") or d.get("title_en")` are BANNED. The migration
   runs first; by the time readers update, the data is in neutral shape.

4. **No new dependencies.** No `lingua-py`, `langdetect`, `fasttext`,
   or anything else not already imported by the codebase.

5. **Bibliography bright line.** Personal/COBISS produces containers
   (`translated_by`) and self-authored records (`written_by`). Book
   bibliography (deleted in Phase 11) produces cited works (`cited_in`).
   Never conflate.

6. **OUT OF SCOPE — do not edit:** `main.py`, `ui/*.py`, `kg_editor_ui.py`
   logic (field-name substitutions in it ARE in scope), `import_book.py`,
   `app_state.py`, `config.py`, `translate_core/llm.py`,
   `translate_core/glossary.py`, `translate_core/qa.py`, `visualise_kg.py`,
   `inline_test/*`, `TranslationMemory.lookup_fuzzy` /
   `.search_concordance` / `.search_prefix`.

7. **Use the project venv:** `.venv/bin/python3` (NEVER call bare
   `python` — the venv has no `python` symlink).

## What's already done (don't re-do)

- Phase 0 — KG baseline confirmed.
- Phase 1 (commit `b723134`) — TMX `t_index` chronological.
- Phase 1B (commit `d152a20`) — `_entries_by_pair` index; smol detector
  returns `(None, None)` on no match; `has_sl_edition` →
  `has_target_lang_edition`; three `# SUNSET: Phase 11` alias-write
  blocks placed in `smol_extractor.py` (Phase 4 deletes them).
- Phase 2 (commit `cd121b3`) — `BookOutline` + `split_paragraphs`
  extracted to `book_outline.py`.
- Phase 3 (commit `7d2e5c2`) — composite confidence gate.

## COBISS facts (from `docs/cobiss_actual_shape.md`)

- COBISS is the user's authoritative curated container source.
- Parser convention: `entry.title` is the SL side; `entry.title_en` is
  the `=`-separator second side (overwhelmingly English in observed data).
- Classifier `classify_entry(entry)` returns `(project_type, belina_role)`.
  Roles: `author` / `translator` / `editor` / unclassified `(None, None)`.
- COBISS entries are NEVER routed to review for direction. Direction
  comes from `belina_role`. Unclassified entries continue to land in
  `data/cobiss_unclassified_entries.json` (unchanged behavior).

## Phase 4 scope — concrete

1. Edit `ontology.md` per blueprint §1.
2. New `scripts/migrate_to_neutral_ontology.py` per blueprint §5
   (uses field-NAME + edge evidence; routes neither-edge nodes to
   `data/migration_review.json`; no language detection).
3. **TWO surgical edits to `scripts/ingest_personal_bibliography.py`:**
   - kwarg rename per `belina_role` branch (blueprint §2.1)
   - finish bilingual publisher TODO at lines 269-271 (blueprint §2.2)
4. New `scripts/ingest_extra_containers.py` + `data/extra_containers.json`
   (blueprint §3).
5. Delete the three `# SUNSET: Phase 11` alias blocks in
   `smol_extractor.py:548-555, 759-763, 884-888`. Builder remaps
   `slovenian_edition` → `translation_edition`. Place new `# SUNSET`
   tag on the prompt-level reference (blueprint §4).
6. Rename `link_sl_published_by` → `link_translation_published_by` in
   `translate_core/knowledge_graph.py`; update all callers.
7. Reader migration — 75 sites get NAKED renames (blueprint §7).
8. `scripts/validate_kg.py` enforces neutral shape (blueprint §8).

## Phase 5 anchor (so blueprints don't contradict)

Containers (`kind="translated_work"`) accept `provenance="cobiss_personal"`
OR `provenance="curator_extra"`. Other provenances route to review.

## Phase 11 SUNSET (deferred from Phase 4)

- Smol prompt-level `slovenian_edition` JSON-schema key rename (needs
  model-regression test).
- `CobissEntry.title_en` dataclass field rename.
- Sweep of any remaining `# SUNSET: Phase 11` tags.
