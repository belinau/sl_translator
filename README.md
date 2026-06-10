# sl_translator

**Bilingual translation workbench with an integrated knowledge graph for citation and entity extraction.**

A desktop application for literary translators working between source and target text. Combines a segment-based translation editor with citation and entity extraction, a translation memory, glossary management, and a deeply structured knowledge graph that captures authors, works, institutions, concepts, and their interrelationships from book footnotes and bibliographies.

Built on **NiceGUI** for the translation workspace and **Streamlit** for knowledge graph curation.

---

## Features

### Translation Workspace

- **Segment-based editor** — Source text is split into editable segments. Translate one segment at a time with full context visible.
- **Ghost-text predictions** — As-you-type predictions from translation memory, glossary, and knowledge graph appear as inline ghost text. Accept with cmd + E.
- **Translation memory (TM)** — Loads TMX files from previous projects. Fuzzy matching (rapidfuzz), concordance search, and prefix search across the full corpus.
- **Glossary** — Reads TBX, TSV, and CSV glossary files. FlashText-powered instant lookup. Entries display in the intelligence panel and feed into predictions.
- **Dark mode** — Toggle between light and dark themes. Preference persists across sessions.
- **Keyboard shortcuts** — Navigate segments, confirm predictions and segment translations.
- **Progress tracking** — Visual progress bar. Confirmed segments auto-save with debounced disk writes.
- **Project management** — Create, list, delete translation projects. Upload PDF or DOCX source files directly. Stand alone book parser for larger PDF files.

### Knowledge Graph

The knowledge graph is a **JSON-backed NetworkX directed graph** with six node types and thirteen edge relations, governed by a formal ontology (see `ontology.md`).

**Node types:**
- **term** — Surface forms in EN or SL, indexed for prediction and glossary lookup
- **concept** — Language-independent meanings with domain classification
- **translation_mapping** — Reified curator-verified translation decisions (monotonic: once verified, never un-verified)
- **source_text** — Books, articles, artworks, performances, exhibitions — any cited or translated work
- **agent** — Persons (authors, translators, editors, curators, artists, performers, etc.)
- **institution** — Publishers, galleries, museums, universities, festivals, theatres, journals, etc.

**Edge relations:**
- Termbase: `has_mapping`, `maps_to`, `translates_to`, `instantiates_concept`
- Bibliography: `written_by`, `translated_by`, `edited_by`, `performed_by`, `published_by`, `translation_published_by`, `hosted_by`, `cited_in`, `appears_in`
- Bridge: `instantiated_in`, `attributed_to`
- Concept: `extends`, `critiques`, `redefines`, `reappropriates`, `related_to`

**Key properties:**
- Factory-method-only writes — no code directly manipulates the JSON file
- Confidence-tiered ingestion — records score 0–1; direct write ≥ 0.85, review queue ≥ 0.55, drop < 0.55
- Review queue is non-bypassable — low-confidence records go to `data/extraction_review.json` for curator approval
- Bilingual-first — source_text nodes carry `title_orig` + `title_translation` with language codes
- Rhizomatic concept network — concepts link via extends/critiques/redefines/reappropriates/related_to

### Entity Extraction Pipeline

Extracts typed citation entities from book footnotes, bibliographies, and body text:

1. **Document parsing** — PDF (PyMuPDF) and DOCX (python-docx) → markdown + page metadata
2. **Segment classification** — Each segment labeled as footnote, bibliography, body text, etc.
3. **Citation collection** — Unified `CitationSnippet` extraction from markdown footnotes, DOCX footnotes, and segment metadata
4. **Entity extraction** — Two extractors:
   - **Regex book extractor** — High-recall pattern matching for Chicago/MLA/SIST citation formats
   - **Smol agent extractor** — Prompt formatting and result parsing for LLM-based extraction (Ollama)
5. **Confidence scoring** — Multi-signal scoring with typed-pipeline bonuses, verification flags
6. **Dedup & merge** — Person name canonicalisation, bilingual payload merge, exact-duplicate collapse
7. **KG ingestion** — Tiered write: direct-write, review queue, or drop

### Bilingual Document-Pair Pipeline

Process matched EN/SL document pairs end-to-end:

1. Parse each side independently (text-only, no VL calls)
2. Collect citation snippets from both sides
3. Heuristic author + title extraction (no LLM needed)
4. Match EN↔SL citations by author surname + title token overlap
5. Write merged bilingual source_text nodes to the KG
6. Optionally export a sentence-aligned TMX from matched footnote texts or whole documents

### COBISS Bibliography Ingestion

Parse COBISS (Cooperative Online Bibliographic System & Services) plain-text bibliography exports:

- Classify entries as container types (book_translation, article_translation, etc.) or cited types
- Detect the translator's role for container vs. own-work disambiguation
- Classify institution kinds from COBISS metadata
- Create KG nodes + edges via factory methods only

### KG Visualization

Six HTML5 Canvas-based interactive visualizations (D3.js force-directed):

| View | Content |
|---|---|
| **Provenance** | Concepts, agents, source texts, institutions — intellectual lineage projection |
| **Terms** | Bilingual term dictionary mapping with concept bridges |
| **Agents & Works** | Agent–work–institution network with projected connections |
| **Artworks** | Artworks, performances, their creators, and lineage hubs |
| **Lineage → Concepts** | Lineage schools projected onto the concept network |
| **Lineage → Works** | Lineage affiliation of works and agents |

Each view supports physics-tuned force layout, type-aware pruning, and metadata inspector panels.

### KG Curation UI (Streamlit)

Search-first curation workspace for the knowledge graph:

- **Terms & Mappings** — Search, edit, verify translation mappings
- **Concepts** — Create, link with rhizomatic relations, manage domain labels
- **Agents** — Search, edit roles, merge duplicates, manage alt_spellings
- **Sources** — Full CRUD for source_text nodes with bilingual fields
- **Lineage Cleanup** — Review and normalize translation lineages
- **Extraction Review** — Approve, reclassify, or drop review-queue records
- **KG Review** — Flag and fix dubious live nodes (low-frequency, empty names, etc.)

### Segment QA Engine

Lemma-aware quality assurance for translated segments:

- Detects untranslated words (source lemmas surviving in target text)
- Language-specific lemmatization via spacy (EN), classla (SL), or stanza (fallback)
- Caches NLP pipelines per language
- Returns per-lemma warnings with confidence scores

### Document Compilation

- **DOCX export** — Compile translated segments back into a DOCX with proper formatting
- **Footnote injection** — Rebuild Word-compatible `footnotes.xml` and inject into the DOCX package
- **Endnote → footnote normalization** — Restructure source documents before translation (for books with endnotes we change to footnotes)

---

## Project Structure

```
sl_translator/
├── main.py                          # NiceGUI app entry point
├── config.py                         # Project configuration
├── app_state.py                      # Shared resource singletons for UI
├── ontology.md                       # Normative KG ontology specification
├── import_book.py                    # CLI: Import PDF/DOCX → translation project
├── run_entity_extraction.py          # CLI: Entity extraction orchestrator
├── process_document_pair.py          # CLI: Bilingual document-pair pipeline
├── ingest_book_footnotes.py          # CLI: Footnote ingestion into KG
├── ingest_book_bibliography.py       # CLI: Bibliography ingestion into KG
├── visualise_kg.py                   # 6-view HTML5 Canvas KG visualizer
├── kg_editor_ui.py                   # Streamlit KG curation workspace
│
├── translate_core/                   # Core library
│   ├── knowledge_graph.py            # KG: 6 node types, 13 edge relations
│   ├── kg_ingest_entities.py         # Record routing, scoring, dedup, KG writes
│   ├── tm.py                         # Translation memory (TMX)
│   ├── tm_timecodes.py               # TMX with creationdate preservation
│   ├── glossary.py                   # TBX/TSV/CSV glossary
│   ├── qa.py                         # Lemma QA engine
│   ├── doc_parser.py                 # PDF/DOCX → markdown + DOCX compilation
│   ├── document_pair_pipeline.py     # Bilingual citation matching
│   ├── citation_collector.py         # Unified citation extraction
│   ├── container_attribution.py      # Segment → container attribution
│   ├── book_outline.py               # TOC + paragraph segmentation
│   ├── cobiss_parser.py              # COBISS bibliography parser
│   ├── cobiss_classifier.py          # COBISS entry classification
│   │
│   └── entity_extraction/            # Extraction pipeline
│       ├── smol_extractor.py         # Smol agent prompt formatting + parsing
│       ├── book_extractor.py         # Regex-based high-recall extraction
│       ├── segment_classifier.py     # Per-segment type classification
│       ├── confidence.py             # Confidence scoring
│       ├── citation_types.py         # CitationType/CitationStyle enums
│       ├── bilingual_tm_matcher.py    # TM-based citation matching
│       ├── footnote_parser.py        # Markdown footnote parsing
│       ├── bibliography_parser.py    # Structured bibliography parsing
│       ├── docx_footnote_parser.py    # DOCX footnote XML extraction
│       ├── name_dedup.py             # Person-name canonicalisation
│       ├── origin_walker.py          # Origin context walking
│       └── _slug.py                  # Canonical slugify implementation
│
├── ui/                               # NiceGUI workspace UI
│   ├── workspace.py                  # Main translation workspace page
│   ├── intel_panel.py                # KG + TM + Glossary intelligence panel
│   ├── segment_editor.py             # Segment editing + confirm UI
│   ├── predictions.py               # Ghost-text copilot engine
│   ├── kg_search.py                 # KG search panel
│   ├── segment_navigator.py         # Segment navigation sidebar
│   ├── settings.py                  # Theme/colors
│   └── state.py                     # WorkspaceState + subscriber pattern
│
├── scripts/                          # One-off migration & utility scripts
│   ├── ingest_personal_bibliography.py   # COBISS ingestion
│   ├── ingest_curator_lineages.py        # Lineage ingestion
│   ├── ingest_curator_citations.py      # Curator citation ingestion
│   ├── ingest_curator_agent_mentions.py  # Agent mention ingestion
│   ├── ingest_performances_artworks.py  # Performance/artwork ingestion
│   ├── ingest_glossary_to_kg.py         # Glossary → KG term nodes
│   ├── build_segment_attribution.py     # Build attribution JSON
│   ├── harvest_boundary_anchors.py      # Boundary anchor harvesting
│   ├── harvest_title_anchors.py         # Title anchor harvesting
│   ├── drop_broken_anchors.py            # Drop broken anchors
│   ├── verify_container_spans.py         # Verify container spans
│   ├── drain_noise_concepts.py          # Remove noise concepts
│   ├── validate_kg.py                   # KG validation script
│   ├── forensic_audit.py               # KG forensic audit
│   ├── normalize_lineages.py            # Lineage normalization
│   └── migrate_dedup_groups.py          # Dedup group migration
│
├── tests/                            # pytest test suite
├── data/                             # Runtime data
│   ├── knowledge.db                  # KG JSON (nodes + edges)
│   ├── extraction_review.json        # Review queue
│   ├── projects/                     # Per-project workspaces
│   ├── tm/                           # TMX translation memories
│   ├── glossary/                     # Glossary files
│   ├── books/                        # Source PDFs and DOCXs
│   └── personal bibliography/        # COBISS exports
│
└── docs/                             # Documentation
    ├── ontology.md (→ ../ontology.md)
    ├── ARCHITECTURE_ANALYSIS.md      # This analysis report
    └── ...                           # Phase blueprints, surveys
```

---

## Quick Start

### Prerequisites

- Python 3.14+
`.venv/` with dependencies installed

### Install Dependencies

```bash
# If a requirements.txt exists:
pip install -r requirements.txt

# Otherwise, dependencies are in .venv/ already
```

### Run the Translation Workspace

```bash
python main.py
```

Opens at `http://localhost:8080`. Upload a PDF or DOCX, translate segment by segment.

### Import a Book

```bash
python import_book.py data/books/book.pdf
python import_book.py data/books/book.docx --lang-pair sl->en
```

### Run Entity Extraction

```bash
# Phase A: Export segments for extraction
python run_entity_extraction.py --export-segments

# Phase B: Ingest extraction results
python run_entity_extraction.py --ingest-extractions
```

### Process a Bilingual Document Pair

```bash
python process_document_pair.py \
    --en data/books/Book-EN.pdf \
    --sl data/books/Book-SL.docx \
    --container source:book-slug
```

### Ingest Personal Bibliography (COBISS)

```bash
python scripts/ingest_personal_bibliography.py data/personal\ bibliography/bibliography_export.txt
```

### Visualize the Knowledge Graph

```bash
python visualise_kg.py                        # All 6 views
python visualise_kg.py --limit 5000           # More nodes
python visualise_kg.py --lang en              # Filter terms by language
python visualise_kg.py --out-dir ./output     # Custom output directory
```

Opens 6 HTML files with interactive D3.js force-directed graphs.

### Curate the Knowledge Graph

```bash
streamlit run kg_editor_ui.py
```

### Validate the Knowledge Graph

```bash
python scripts/validate_kg.py
```

### Run Tests

```bash
python -m pytest tests/ inline_test/ -v
```

---

## Ontology & Constraints

The knowledge graph is governed by a formal ontology defined in `ontology.md`. Key constraints:

| ID | Constraint |
|---|---|
| O-1 | Only `KnowledgeGraph` factory methods write to the KG. Never write directly to `data/knowledge.db`. |
| O-2 | All ID slugs use NFKD strip → lowercase → non-alphanumeric → `-` → truncate 80 → `"unknown"` fallback. |
| O-3 | `translation_mapping.verified` is monotonic (True → never False). |
| O-4 | Never write `tm_segment`, `collocation`, or `domain` nodes. |
| O-5 | Bilingual citations carry `title_orig` AND `title_translation` (not `title_en`/`title_sl`). |
| O-10 | Records below confidence 0.85 go to `data/extraction_review.json`. Never bypass. |
| O-12 | Agent writes must include `dedup_group`, `alt_spellings`, `all_roles`, `mention_count`. |
| O-13 | `role` ∈ `{author, translator, editor, curator, artist, interviewer, interviewee, choreographer, director, performer, dancer, composer, dramaturg, agent}`. |
| O-14 | `institution.kind` ∈ `{publisher, gallery, museum, university, festival, theatre, journal, organization, sponsor, country, other}`. |
| O-17 | `cited_in` self-loops forbidden. |
| O-20 | Every container node must have a `translated_by` edge. |

See `ontology.md` for the complete specification.

---

## Technology Stack

| Component | Technology |
|---|---|
| Translation UI | NiceGUI |
| KG Curation UI | Streamlit |
| Knowledge Graph | NetworkX (JSON-persisted) |
| Translation Memory | Custom TMX parser (lxml) |
| Fuzzy Matching | rapidfuzz |
| Glossary | FlashText |
| NLP | spacy (EN), classla (SL), stanza (fallback) |
| Document Parsing | PyMuPDF (PDF), python-docx (DOCX), MarkItDown |
| Entity Extraction | smol agent via Ollama (live on segment confirm + offline batch), regex fallback |
| Visualization | D3.js (HTML5 Canvas) |
| Python | 3.14 |
| Type Checking | basedpyright (basic mode) |

---

## Data Format

### Knowledge Graph (`data/knowledge.db`)

JSON file with two top-level arrays:

```json
{
  "nodes": [
    {"id": "term:en:photographs", "type": "term", "term": "photographs", "lang": "en", ...},
    {"id": "concept:kasimir_family", "type": "concept", "label": "Kasimir family", ...},
    {"id": "source:feminist-queer-crip-kafer", "type": "source_text", "title": "Feminist, Queer, Crip", ...}
  ],
  "edges": [
    {"source": "term:en:photographs", "target": "concept:kasimir_family", "relation": "instantiates_concept"},
    {"source": "source:feminist-queer-crip-kafer", "target": "agent:alison-kafer", "relation": "written_by"}
  ]
}
```

### Translation Projects (`data/projects/*.json`)

```json
{
  "project_id": "a2aee457",
  "lang_pair": "en->sl",
  "segments": [
    {"id": 0, "source": "Source text...", "target": "", "status": "pending"},
    {"id": 1, "source": "...", "target": "Translated text", "status": "confirmed"}
  ]
}
```

---

## Configuration

`config.py` defines:

| Setting | Default | Purpose |
|---|---|---|
| `LANG_PAIRS` | `[en↔sl]` | Supported language pairs |
| `DEFAULT_SOURCE_LANG` | `"en"` | Default source language |
| `DEFAULT_TARGET_LANG` | `"sl"` | Default target language |
| `TM_DIR` | `data/tm/` | Translation memory directory |
| `GLOSSARY_DIR` | `data/glossary/` | Glossary directory |
| `KG_DB_PATH` | `data/knowledge.db` | Knowledge graph file |

---

## License

Experimental project. Not currently licensed for redistribution.
