# KG Editor Redesign

**Date:** 2026-08-22
**Status:** Approved by user

## Problem

The current KG editor has 7 separate pages (`/kg/terms`, `/kg/concepts`,
`/kg/agents`, `/kg/sources`, `/kg/lineages`, `/kg/review`,
`/kg/kg-review`). Each page calls `kg.get_all_by_type()` on every render
(loading 90k+ nodes into memory), does `results.clear()` + rebuilds all
expansion cards, and loses context on navigation. Saving a single
concept triggers a full re-fetch and re-render of every node. Pages hang
or crash on large node sets. Most node types lack full property editing
(institutions have no editor at all; agents are missing O-12 fields). Edge
editing is minimal — you can add some edges but can't see or remove
existing ones.

## Design

### Architecture: unified browser + side panel edit

One page (`/kg`) replaces the current 7 node-editing pages. Review and
kg-review stay as separate operational pages but share the sidebar.

**Layout (3-column):**

```
┌──────────┬──────────────────────────┬───────────────┐
│ Sidebar  │  Results column           │  Edit panel   │
│ (260px)  │  (flex-1)                 │  (420px)      │
│          │                           │               │
│ SEARCH   │  [Type filter chips]      │  Node info    │
│ [____]   │  All Concepts Agents ...  │  + fields     │
│          │                           │  + edges      │
│ STATS    │  ▸ Result row             │  + save/del   │
│ (cached) │  ▸ Result row             │               │
│          │  ▸ Result row             │               │
│ OPS      │                           │               │
│ Hard Save│  [pagination]             │               │
└──────────┴──────────────────────────┴───────────────┘
```

### Loading re-engineering

**Current pattern (crashes):**
```python
def render():
    concepts = kg.get_all_by_type("concept")  # 90k nodes
    results.clear()
    with results:
        for c in concepts:  # 90k expansion cards
            _concept_card(c, kg, render_fn)
```

**New pattern:**

1. **Fetch once, cache in closure.** On page load, `kg.get_all_by_type(type)`
   is called once per type chip click and stored in a local variable.
   Search/filter works against this cache without re-fetching.

2. **Paginate, never render all.** 25 results per page. Page changes
   swap only the visible slice — no re-fetch, no rebuild.

3. **Edit panel is independent.** Clicking a result populates the right
   panel. Save writes to KG via `run.io_bound`, updates only the result
   row label, and shows a toast. No `results.clear()`, no list re-render.

4. **Stats cached.** `kg.stats()` on page load, displayed in sidebar. Not
   re-fetched on every action.

5. **Search is debounced.** 300ms debounce on the search input. Searches
   against the cached type list (or all types if "All" chip is selected).

### Type filter chips

Horizontal chips at the top of the results column:
`All` · `Concepts` · `Agents` · `Sources` · `Terms` · `Institutions` · `Mappings`

Clicking a chip:
- If the type list is not yet cached, fetch `kg.get_all_by_type(type)` once
  and store it.
- Show count: "1,234 concepts"
- Display paginated results (25 per page).
- "All" chip searches across all cached type lists.

### Result rows

Each result is a compact row (not an expansion card):
```
▸ [concept] Cyborg theory
  humanities · 3 edges
```
Click → populates the edit panel on the right. The row highlights as
selected. No re-render of the list.

### Edit panel — full property editing per ontology

Each node type gets a form with all ontology-defined fields.

**Term:**
- `term` (readonly), `lang` (readonly), `type` badge
- `display_form` (text input)
- `is_animate` (checkbox), `is_phrase` (checkbox)
- `variants` (readonly list)
- `frequency` (readonly), `created_at` (readonly)
- API: `kg.update_term_node(term_id, display_form, is_animate, is_phrase)`

**Concept:**
- `label` (text), `domain` (text), `definition` (textarea)
- `label_orig` (text), `label_translation` (text)
- `orig_lang` (text), `translation_lang` (text)
- `created_at` (readonly)
- API: `kg.update_concept_metadata(...)`

**Translation mapping:**
- `confidence` (number 0.0–1.0), `lineage` (text)
- `register` (text), `gloss` (textarea)
- `year` (number), `verified` (checkbox — monotonic, disabled if True)
- `created_at` (readonly)
- API: `kg.update_translation_mapping(...)`

**Source text:**
- `title` (text), `year` (number), `project_type` (select)
- `title_orig` (text), `title_translation` (text)
- `orig_lang` (text), `translation_lang` (text)
- `translation_edition` (nested: publisher, city, year, translator, language)
- `created_at` (readonly)
- API: `kg.update_source_text_node(...)`

**Agent:**
- `name` (text), `role` (select from O-13 allowlist)
- `dedup_group` (readonly), `alt_spellings` (editable comma-separated)
- `all_roles` (readonly badges), `mention_count` (readonly)
- `origin` (text), `created_at` (readonly)
- API: `kg.update_agent_node(agent_id, name, role)` — extend to accept
  alt_spellings and origin

**Institution:**
- `name` (text), `kind` (select from O-14 allowlist)
- `city` (text), `created_at` (readonly)
- API: **NEW** `kg.update_institution_node(...)` — does not exist yet,
  must be added to `knowledge_graph.py`

### Edge panel per node

When a node is selected, the edit panel shows its edges below the
fields section:

```
── EDGES (5) ──
OUTGOING:
  written_by → Michel Foucault (agent)        [✕]
  cited_in → Kunst, življenje (source_text)    [✕]
INCOMING:
  cited_in ← Some Other Work (source_text)     [✕]
  has_mapping ← term:en:gaze (term)            [✕]

[+ Add edge]
  Relation: [written_by ▼]  (filtered by valid source→target type combos)
  Target:   [search node... ▼]
  [Link]
```

**Edge removal:** calls `kg.G.remove_edge(u, v)` +
`kg._delete_edge_persist(u, v)`. Updates only the edge list section, no
full re-render.

**Edge addition:** validates relation against ontology §3.1–§3.4.
The relation dropdown only offers relations valid for the source node
type → target node type. Target search uses the same cached type lists.

**Valid relation matrix (from ontology §3):**

| Source type | Relation | Target type |
|---|---|---|
| term | has_mapping | translation_mapping |
| translation_mapping | maps_to | term |
| term | translates_to | term |
| term | instantiates_concept | concept |
| source_text | written_by | agent |
| source_text | translated_by | agent |
| source_text | edited_by | agent |
| source_text | performed_by | agent |
| source_text | published_by | institution |
| source_text | translation_published_by | institution |
| source_text | hosted_by | institution |
| source_text | cited_in | source_text |
| source_text | appears_in | source_text |
| translation_mapping | instantiated_in | source_text |
| translation_mapping | attributed_to | agent |
| concept | attributed_to | agent |
| concept | extends/critiques/redefines/reappropriates/related_to | concept |

### New KG API method

`update_institution_node` — follows the same pattern as
`update_agent_node`:

```python
def update_institution_node(
    self,
    institution_id: str,
    name: Optional[str] = None,
    kind: Optional[str] = None,
    city: Optional[str] = None,
) -> bool:
    if not self.G.has_node(institution_id):
        return False
    node = self.G.nodes[institution_id]
    if name is not None:
        node["name"] = name
    if kind is not None:
        node["kind"] = kind
    if city is not None:
        node["city"] = city
    self._persist_node(institution_id)
    return True
```

### Files to change

| File | Change |
|---|---|
| `ui/kg_editor/browser.py` | **NEW** — unified browser page with results list, edit panel, edge panel |
| `ui/kg_editor/common.py` | Rework sidebar: search bar, stats (cached), hard save. Remove old nav pages. |
| `translate_core/knowledge_graph.py` | Add `update_institution_node` method |
| `ui/kg_editor/__init__.py` | Route `/kg` to browser, remove old page imports |
| `ui/kg_editor/review.py` | Keep, update sidebar to new style |
| `ui/kg_editor/kg_review.py` | Keep, update sidebar to new style |
| `ui/kg_editor/concepts.py` | **DELETE** — merged into browser |
| `ui/kg_editor/terms.py` | **DELETE** — merged into browser |
| `ui/kg_editor/agents.py` | **DELETE** — merged into browser |
| `ui/kg_editor/sources.py` | **DELETE** — merged into browser |
| `ui/kg_editor/lineages.py` | **DELETE** — lineage merge moves to ops section in sidebar |

### What stays the same

- `kg_frame()` in `common.py` — still builds the top bar + left drawer, but
  nav buttons point to `/kg`, `/kg/review`, `/kg/kg-review` only (3 items
  instead of 7).
- Review and kg-review pages keep their existing routes and logic.
- All KG factory methods and the ontology constraints are unchanged.
- The `Hard Save` button stays.
- Dark mode handling stays.

### Performance targets

- Page load: <2s (stats cached, no list rendered until chip click)
- Type chip click: <1s for first load (one `get_all_by_type` call),
  instant on subsequent clicks (cached)
- Search: <100ms (filters cached list in Python)
- Save: <500ms (io_bound write, no re-render, toast only)
- Edge add/remove: <500ms (io_bound, edge list section update only)