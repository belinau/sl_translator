# sl_translator KG ontology — normative definition

**Status:** authoritative. Any code, prompt, parser, or document that
contradicts this file is wrong and must be corrected to match this file.

The KG is a NetworkX `DiGraph` serialised to JSON at `data/knowledge.db`,
managed by `translate_core/knowledge_graph.py:210` (`KnowledgeGraph`).
The factory methods on that class are the only authorised writers.

This document defines:
- the six node types, their IDs, required fields, optional fields;
- the twelve edge relations, their typed endpoints, their data fields;
- cross-cutting invariants (slugify, monotonicity, what is NEVER stored);
- per-type sub-taxonomy of `source_text` (delegates to
  `docs/citation-extraction-spec.md`).

## 0. Glossary

| Term | Meaning |
|---|---|
| Node | One vertex in the graph, keyed by its `id`. |
| Edge | One directed link with a `relation` field and optional data fields. |
| Required | The field MUST be present and non-empty on every node of that type. |
| Optional | The field MAY be absent. If present, it MUST conform to the type / constraint stated. |
| Reified edge | A relationship encoded as a node (with its own data) connected to its endpoints by two edges. Used for `translation_mapping`. |
| Slugify | NFKD strip diacritics → lowercase → non-alphanumeric → `-` → truncate to 80 chars. Empty slug becomes `"unknown"`. |

## 1. Storage model

- File: `data/knowledge.db` (JSON, NOT SQLite, NOT Kuzu).
- Read/write: only via `KnowledgeGraph` factory methods.
- `save()` writes atomically (temp file + `os.replace`) and rotates a `.bak`.
- `id` is unique across the entire graph regardless of type.
- Edges are directed. `(u) -[relation]-> (v)` is NOT the same as `(v) -[relation]-> (u)` unless explicitly stated.

## 2. Node types

There are exactly **six** node types. Anything else is a violation.

### 2.1 `term`

A surface form in one language. Indexed for prediction / glossary use.

| Field | Required? | Type | Constraint |
|---|---|---|---|
| `id` | yes | str | `term:<lang>:<term.lower()>` |
| `type` | yes | str | `"term"` |
| `term` | yes | str | the surface form, lowercased |
| `lang` | yes | str | `"en"` or `"sl"` |
| `is_phrase` | yes | bool | `True` if multi-token |
| `is_animate` | yes | bool | for SL gender-aware translation |
| `gender_strategies` | yes | dict | populated for SL animate terms (see §6); `{}` otherwise |
| `frequency` | yes | int | incremented every time the term is seen |
| `created_at` | yes | str | ISO-8601 timestamp |
| `display_form` | optional | str | when the surface differs in casing/diacritics from `term` |
| `variants` | optional | list[str] | accumulated alt surface forms |

Authoritative writer: `KnowledgeGraph.add_term_node` (`knowledge_graph.py:427`).

### 2.2 `concept`

A language-independent meaning. Many terms in many languages may instantiate one concept.

| Field | Required? | Type | Constraint |
|---|---|---|---|
| `id` | yes | str | `concept:<slugified_label>` — underscore-joined, lowercased |
| `type` | yes | str | `"concept"` |
| `label` | yes | str | the canonical human-readable label (typically EN form) |
| `domain` | yes | str | e.g. `"humanities"`, `"performance"`, `"visual-art"` |
| `definition` | yes | str | curator's prose definition; `""` until curated |
| `created_at` | yes | str | ISO-8601 timestamp |
| `label_orig` | optional | str | source-language label (mirrors `label` for bilingual concepts) |
| `label_translation` | optional | str | target-language label (the translated form) |
| `orig_lang` | optional | str | ISO 639-1 code for the source language |
| `translation_lang` | optional | str | ISO 639-1 code for the target language |

The four bilingual fields use the language-neutral `*_orig` / `*_translation`
naming convention. They are written at creation via `add_concept_node` and
updated via `update_concept_metadata`, which accepts them through the sentinel
convention (omitted = leave unchanged, explicit value = set). The fields
`label_en`/`label_sl` are FORBIDDEN; use `label_orig`/`label_translation`
instead.

Authoritative writer: `KnowledgeGraph.add_concept_node` (`knowledge_graph.py:390`).
Authoritative updater: `KnowledgeGraph.update_concept_metadata` (`knowledge_graph.py:1520`).

### 2.3 `translation_mapping` — REIFIED EDGE

A single curated decision that *this* source term maps to *that* target
term under a specific lineage. Reified as a node so multiple competing
mappings can coexist between the same term pair, each with its own
confidence, provenance, and curator-verification.

| Field | Required? | Type | Constraint |
|---|---|---|---|
| `id` | yes | str | `map:<src_term_id>>><tgt_term_id>:<lineage_slug>` |
| `type` | yes | str | `"translation_mapping"` |
| `confidence` | yes | float | 0.0 ≤ x ≤ 1.0 |
| `lineage` | yes | str | freeform, e.g. `"performance"`, `"visual-art"`, `"manual"`, `"general"` |
| `register` | yes | str | e.g. `"academic"`, `"colloquial"` |
| `gloss` | yes | str \| null | optional curator note |
| `year` | yes | int \| null | year of attestation if known |
| `verified` | yes | bool | curator has approved this mapping. MONOTONIC: once `True`, must never become `False` |
| `created_at` | yes | str | ISO-8601 timestamp |

Authoritative writer: `KnowledgeGraph.link_translations_with_context`
(`knowledge_graph.py:657`). Also wires the two anchor edges
(`has_mapping`, `maps_to`) and optionally the bridge edges
(`instantiated_in`, `attributed_to`).

Every `translation_mapping` node MUST have exactly:
- one incoming `(term) -[has_mapping]-> (this)` edge
- one outgoing `(this) -[maps_to]-> (term)` edge

### 2.4 `source_text`

A text-bearing work: a book the user translated (container), or anything
cited inside such a work, or an artwork.

| Field | Required? | Type | Constraint |
|---|---|---|---|
| `id` | yes | str | `source:<slugified_work_id>` |
| `type` | yes | str | `"source_text"` |
| `title` | yes | str | display title in the canonical language of the work |
| `year` | yes | int \| null | year of publication; null when unknown |
| `project_type` | yes | str | one of the values defined in §2.4.1 |
| `created_at` | yes | str | ISO-8601 timestamp |

All other fields are **optional**. The set of optional fields a record
carries depends on its `project_type`. The authoritative per-type schema
is in `docs/citation-extraction-spec.md` §4 (book / book_chapter /
journal_article / magazine_article / newspaper_article / web_source /
exhibition_catalog / interview / thesis_dissertation / short_reference /
other).

Authoritative writer: `KnowledgeGraph.add_source_text_node`
(`knowledge_graph.py:365`).
Authoritative updater: `KnowledgeGraph.update_source_text_node`
(`knowledge_graph.py:1586`), which handles bilingual fields and
`translation_edition` through the sentinel convention (omitted = leave
unchanged, explicit value = set).

#### 2.4.1 Valid `project_type` values

Container types (works the user has translated; cited works link to these via `cited_in`):
- `book_translation` — a book the user translated end-to-end.
- `article_translation` — a standalone article translation (no book container).
- `festival_programme` — a festival catalogue / programme the user translated.
- `exhibition_catalogue` (when serving as a top-level container, not as a cited work).

**Provenance:** Container nodes are created from the translator's **personal bibliography** (COBISS or a curated list of all translated works) — NOT from the book bibliographies of individual translated works. Each container node carries a `translated_by` edge pointing to the translator. The personal bibliography is the authoritative source for which works the user has translated. See `docs/pipeline-analysis-report.md` §1 and §6.2a for the pipeline details.

**Distinction:** The **book bibliography** (end-bibliography or footnotes within a specific translated work) lists works *cited in* that work and creates `cited_in` edges pointing to the container. The **personal/COBISS bibliography** lists works *translated by* the translator and creates container nodes with `translated_by` edges. These are different bibliographies serving different purposes and must not be conflated.

Cited types (per `docs/citation-extraction-spec.md` §4):
- `book`, `book_chapter`, `journal_article`, `magazine_article`,
  `newspaper_article`, `web_source`, `exhibition_catalog`, `interview`,
  `thesis_dissertation`.

Other:
- `artwork` — visual work with title + (artist) + (year) + (medium).
- `performance` — a live/time-based work (dance, theatre, performance art,
  festival act). Creators (choreographer, director) attach via `written_by`;
  cast (performer, dancer) attach via `performed_by` (§3.2).
- `cited_container` — synthetic parent created for a chapter whose book
  isn't its own cited record.
- `cited_work` — **legacy generic**. Used when no per-type schema fit.
  New writes SHOULD NOT use this value unless the typed pipeline
  explicitly fell back to it.

#### 2.4.2 Bilingual title fields (canonical, from `docs/citation-extraction-spec.md`)

When a `source_text` exists in two languages (the user's translation
case), the bilingual form is encoded as:

- `title_orig` — the original-language title
- `title_translation` — the title in the translation language
- `orig_lang`, `translation_lang` — two-letter ISO 639-1 language codes
- `translation_edition: {publisher, city, year, translator, language}` —
  populated when the translation edition has distinct publication
  metadata (publisher, year, or translator differ from the original).
  The `language` field MUST be an ISO 639-1 code matching
  `translation_lang`.

DO NOT invent flat ad-hoc fields like `publisher_en` / `publisher_sl` for
new writes. The bilingual encoding above is canonical. The legacy
names `title_en`, `title_sl`, and `slovenian_edition` are FORBIDDEN in
new writes; nodes carrying them are hard invariant violations caught
by `scripts/validate_kg.py`.

### 2.5 `agent`

A person playing one or more roles relative to source_texts.

| Field | Required? | Type | Constraint |
|---|---|---|---|
| `id` | yes | str | `agent:<slugified_name>` |
| `type` | yes | str | `"agent"` |
| `name` | yes | str | canonical display name (longest non-abbreviated form seen) |
| `role` | yes | str | primary role |
| `created_at` | yes | str | ISO-8601 timestamp |
| `dedup_group` | REQUIRED FOR NEW WRITES | str | `<first-initial>.<lastname>` per `name_dedup.dedup_group_key`. Groups merge candidates without auto-merging. |
| `alt_spellings` | REQUIRED FOR NEW WRITES | list[str] | all surface forms observed for this dedup group |
| `all_roles` | REQUIRED FOR NEW WRITES | list[str] | every role this agent has appeared in |
| `mention_count` | REQUIRED FOR NEW WRITES | int | how many segments mentioned the agent |
| `mention_segments` | optional | list[dict] | up to 20 `{origin, segment_idx}` provenance pointers |
| `origin` | optional | str | TM origin of first mention |
| `segment_idx` | optional | int | segment index of first mention |

`role` MUST be one of: `author`, `translator`, `editor`, `curator`,
`artist`, `interviewer`, `interviewee`, `choreographer`, `director`,
`performer`, `dancer`, `composer`, `dramaturg`, `agent`. The catch-all
`"agent"` is used when VL emitted a role outside this allowlist and the
curator has not yet reassigned it.

Authoritative writer: `KnowledgeGraph.add_agent_node`
(`knowledge_graph.py:341`). The factory enforces the O-12 quartet
(`dedup_group`, `alt_spellings`, `all_roles`, `mention_count`) when a
caller omits them. Callers MAY still pass explicit values.

### 2.6 `institution`

A publisher, gallery, museum, festival, university, theatre, journal, or
similar organisation.

| Field | Required? | Type | Constraint |
|---|---|---|---|
| `id` | yes | str | `institution:<slugified_name>` |
| `type` | yes | str | `"institution"` |
| `name` | yes | str | display name |
| `kind` | yes | str | one of: `publisher`, `gallery`, `museum`, `university`, `festival`, `theatre`, `journal`, `organization`, `sponsor`, `country`, `other` |
| `city` | optional | str | city of the institution |
| `created_at` | yes | str | ISO-8601 timestamp |

Authoritative writer: `KnowledgeGraph.add_institution_node`
(`knowledge_graph.py:514`).

## 3. Edge relations

There are exactly **twenty** edge relations, enumerated in §3.1–§3.4. Anything else is a violation.

### 3.1 Termbase layer

| Edge | Required data fields | Constraints |
|---|---|---|
| `(term) -[has_mapping]-> (translation_mapping)` | — | Exactly one per mapping. |
| `(translation_mapping) -[maps_to]-> (term)` | — | Exactly one per mapping. |
| `(term) -[translates_to]-> (term)` | `confidence: float, verified: bool, provenance: str, last_updated: str` | Written by `link_translations_with_context` for backward compatibility. Always written in BOTH directions (`a→b` AND `b→a`). |
| `(term) -[instantiates_concept]-> (concept)` | — | One term may instantiate many concepts; one concept may have many terms in many languages. |

### 3.2 Bibliography layer

All bibliography edges carry NO data fields. The relation name is the
entire payload.

| Edge | Semantics |
|---|---|
| `(source_text) -[written_by]-> (agent)` | author of the work |
| `(source_text) -[translated_by]-> (agent)` | translator of the work |
| `(source_text) -[edited_by]-> (agent)` | editor of the work (chapters, anthologies); also curator of an exhibition |
| `(source_text) -[performed_by]-> (agent)` | performer / dancer / cast member appearing in a `performance` (creators use `written_by`) |
| `(source_text) -[published_by]-> (institution)` | original / first publisher |
| `(source_text) -[translation_published_by]-> (institution)` | Publisher of the translation edition when it differs from the original-edition publisher |
| `(source_text) -[hosted_by]-> (institution)` | venue / host institution (exhibitions, talks) |
| `(source_text) -[cited_in]-> (source_text)` | this cited work appears inside the target container work |
| `(source_text) -[appears_in]-> (source_text)` | this chapter appears inside the target book (chapter→book relation) |

**Provenance for `cited_in`:** The authoritative source for `cited_in` edges is the **book bibliography** of the container work — the end-bibliography or footnotes within a specific translated work. Every citation extracted from a book's bibliography automatically gets a `cited_in` edge to that book's container node, because we know which book we're parsing. See `docs/pipeline-analysis-report.md` §1.

**Provenance for `translated_by`:** The authoritative source for `translated_by` edges (and for the existence of container nodes) is the **personal/COBISS bibliography** — the translator's complete list of translated works. This is a different source than the book bibliography and must not be conflated with it.

Loops (`a -[cited_in]-> a`) are forbidden and dropped at write time
(`knowledge_graph.py:539`).

### 3.3 Bridge layer (termbase ↔ bibliography)

These edges anchor a curated `translation_mapping` to the work in which
that mapping was attested, and the translator who made it.

| Edge | Required data fields | Semantics |
|---|---|---|
| `(translation_mapping) -[instantiated_in]-> (source_text)` | — | this mapping was made while translating that work |
| `(translation_mapping) -[attributed_to]-> (agent)` | — | this mapping is attributable to that translator/curator |
| `(concept) -[attributed_to]-> (agent)` | — | this concept is attributable to that theorist/curator |

These are SUPPORTED by `link_translations_with_context` but only when
the caller passes `source_text_id=` and `agent_id=`. They are the
primary mechanism for evidence-anchoring a translation choice.

### 3.4 Concept layer

| Edge | Required data fields | Semantics |
|---|---|---|
| `(concept) -[extends \| critiques \| redefines \| reappropriates \| related_to]-> (concept)` | `last_updated: str` | rhizomatic concept network. The `relation` field MUST be one of those five values. |

Authoritative writer: `KnowledgeGraph.link_concepts_rhizomatic`
(`knowledge_graph.py:411`). Any other value is silently coerced to
`related_to`.

## 4. Invariants the writers must respect

1. **Slugify discipline.** Every `id` slug component goes through the
   same algorithm: NFKD strip → lowercase → non-alphanumeric → `-` →
   truncate 80 chars → fallback `"unknown"` when empty.
2. **`verified` is monotonic.** Once a `translation_mapping.verified`
   becomes `True`, it never returns to `False`, regardless of how many
   later auto-seed calls touch the same mapping.
3. **Segments do not live in the KG.** TM segment pairs belong to the
   TMX file (`data/tm/*.tmx`) and the in-memory `TranslationMemory`
   only. The KG ingests segments via NLP → terms / concepts / mappings.
   It is FORBIDDEN to create `tm_segment` nodes, sentence-shaped term
   nodes, or sentence-shaped concept nodes from segments.
4. **Bilingual data extraction is not optional.** A `source_text`
   record representing a citation that exists in both languages of the
   TM MUST carry `title_orig`, `title_translation`, `orig_lang`, and
   `translation_lang`. Unilingual citation records produced when the
   TM holds the other-language form are a correctness defect, not an
   acceptable interim state. The legacy names `title_en` and `title_sl`
   are FORBIDDEN; a node carrying them is a hard invariant violation
   caught by `scripts/validate_kg.py`.
5. **Real names never appear in VL prompt examples.** The 1.6B VL model
   copies example values verbatim into its output, attributing real
   people / works / publishers to segments that do not mention them.
   All prompt example values MUST be abstract placeholders
   (`"..."` / `"Firstname Lastname"` / `"Lastname"`).
6. **Citation styles must be identified by name.** Records carrying a
   `citation_style` field MUST cite a style defined in
   `docs/citation-extraction-spec.md` §1 (`chicago_en`, `chicago_sl`,
   `mla`, `sist_iso690`). Reverse-engineered style labels are
   forbidden.
7. **VL hallucinates on text-heavy humanities pages.** When parsing
   source documents, PyMuPDF text extraction is preferred for every
   page that has embedded text. VL is used for layout classification
   (one-word page type) only.
8. **Real KG verification required.** Before claiming a change works,
   load `data/knowledge.db` and count what the changed code emits.
   Unit-test stub assertions do not substitute for real-data runs.
9. **Review queue is non-bypassable.** Records with confidence below
   the direct-write threshold are routed to `data/extraction_review.json`
   for curator approval. Direct writes that bypass the review queue
   are a process violation regardless of how confident the parser
   thinks it is.
10. **Translations are EDGES.** Code that reads "what does term X
    translate to?" must traverse `has_mapping → maps_to` (preferred,
    carries curator data) and/or `translates_to` (legacy fallback).
    There is no `translations` attribute on `term` nodes; assuming one
    silently misses curator metadata.

## 5. Defaults & ID examples

```
term:en:photographs
term:sl:fotograf
concept:kasimir_family
map:term:en:potestas>>term:sl:moč:performance
source:kunst-zivljenje-umetnosti
source:kunst-zivljenje-umetnosti     ← project_type=book_translation
source:foucault-discipline-and-punish-1977   ← project_type=book, cited_in →
                                              source:kunst-zivljenje-umetnosti
agent:michel-foucault
agent:jelka-bajt
institution:maska
institution:routledge
```

## 6. Removed legacy node types (never write these)

The following node types existed in earlier versions of `KnowledgeGraph` but
have been removed. They are forbidden node types: `scripts/validate_kg.py`
flags any node with one of these types as a hard invariant violation
(`forbidden_node_type`).

- `collocation` — experimental phrase storage; superseded by `is_phrase=True` on `term`.
- `tm_segment` — violates §4 invariant #3.
- `domain` — superseded by the `domain` field on `concept`.

These are listed here so reviewers know to flag any new call site that
references them.

## 7. Where this ontology comes from

This document is derived from direct inspection of the live KG
(`data/knowledge.db`, 94 109 nodes / 276 175 edges as of 2026-06-02)
plus the factory method signatures in
`translate_core/knowledge_graph.py`. The `source_text` sub-taxonomy
(§2.4.1) and the typed-record field schemas live in
`docs/citation-extraction-spec.md` and are normative there; this
document references them rather than duplicating them.

If a future change to `knowledge_graph.py` introduces a new node type
or edge relation, this document MUST be updated in the same commit.
Code that adds nodes or edges not defined here is non-compliant and
must be reverted or this document must be amended first.
