# KG Editor Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 7-page KG editor with a single unified browser page that has cached lazy loading, a side-panel edit form for all 6 node types, and a full edge panel per node — eliminating the full-re-render crashes.

**Architecture:** One page (`/kg`) with a 3-column layout: sidebar (search + stats + ops), results list (type-filtered, paginated, cached), edit panel (fields + edges + save/delete). Review and kg-review pages keep their routes but share the new sidebar. A new `update_institution_node` method is added to the KG API.

**Tech Stack:** NiceGUI, NetworkX DiGraph, python-docx, SQLite KG persistence

---

## File Structure

| File | Responsibility |
|---|---|
| `ui/kg_editor/browser.py` | **NEW** — unified browser page: results list, edit panel, edge panel |
| `ui/kg_editor/common.py` | **MODIFY** — reworked sidebar: search bar, stats (cached), hard save, 3 nav items |
| `ui/kg_editor/__init__.py` | **MODIFY** — route `/kg` to browser, remove old page imports |
| `ui/kg_editor/review.py` | **MODIFY** — update sidebar call to new `kg_frame` signature |
| `ui/kg_editor/kg_review.py` | **MODIFY** — update sidebar call to new `kg_frame` signature |
| `translate_core/knowledge_graph.py` | **MODIFY** — add `update_institution_node` method |
| `ui/kg_editor/concepts.py` | **DELETE** |
| `ui/kg_editor/terms.py` | **DELETE** |
| `ui/kg_editor/agents.py` | **DELETE** |
| `ui/kg_editor/sources.py` | **DELETE** |
| `ui/kg_editor/lineages.py` | **DELETE** |
| `tests/test_kg_editor_browser.py` | **NEW** — tests for the browser page |

---

### Task 1: Add `update_institution_node` to KnowledgeGraph

**Files:**
- Modify: `translate_core/knowledge_graph.py` (after `update_source_text_node`, around line 1786)
- Test: `tests/test_kg_editor_browser.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kg_editor_browser.py
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.knowledge_graph import KnowledgeGraph


class TestUpdateInstitutionNode:
    def test_update_name(self, tmp_path):
        kg = KnowledgeGraph(db_path=tmp_path / "test.db")
        kg.add_institution_node("maska", "Maska", kind="publisher")
        assert kg.update_institution_node("institution:maska", name="Maska Publishers")
        node = kg.G.nodes["institution:maska"]
        assert node["name"] == "Maska Publishers"
        assert node["kind"] == "publisher"

    def test_update_kind(self, tmp_path):
        kg = KnowledgeGraph(db_path=tmp_path / "test.db")
        kg.add_institution_node("tate", "Tate Modern", kind="museum")
        assert kg.update_institution_node("institution:tate", kind="gallery")
        assert kg.G.nodes["institution:tate"]["kind"] == "gallery"

    def test_update_city(self, tmp_path):
        kg = KnowledgeGraph(db_path=tmp_path / "test.db")
        kg.add_institution_node("routledge", "Routledge")
        assert kg.update_institution_node("institution:routledge", city="London")
        assert kg.G.nodes["institution:routledge"]["city"] == "London"

    def test_nonexistent_returns_false(self, tmp_path):
        kg = KnowledgeGraph(db_path=tmp_path / "test.db")
        assert not kg.update_institution_node("institution:nonexistent", name="X")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kg_editor_browser.py::TestUpdateInstitutionNode -v`
Expected: FAIL with `AttributeError: 'KnowledgeGraph' object has no attribute 'update_institution_node'`

- [ ] **Step 3: Implement `update_institution_node`**

Add after `update_source_text_node` (around line 1786 in `translate_core/knowledge_graph.py`):

```python
    def update_institution_node(
        self,
        institution_id: str,
        name: Optional[str] = None,
        kind: Optional[str] = None,
        city: Optional[str] = None,
    ) -> bool:
        """Update mutable fields on an existing institution node.

        Only name, kind, and city are mutable. kind MUST be one of the
        O-14 allowlist values but this method does not enforce it —
        the UI layer validates before calling.
        """
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

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_kg_editor_browser.py::TestUpdateInstitutionNode -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add translate_core/knowledge_graph.py tests/test_kg_editor_browser.py
git commit -m "feat: add update_institution_node to KnowledgeGraph API"
```

---

### Task 2: Rework `common.py` — new sidebar with search, cached stats, 3 nav items

**Files:**
- Modify: `ui/kg_editor/common.py`

- [ ] **Step 1: Rewrite `kg_frame` and `_PAGES`**

Replace the entire content of `ui/kg_editor/common.py` with:

```python
"""Shared KG editor chrome — sidebar with search, cached stats, navigation.

Provides kg_frame(): top bar + left drawer with search input, type chips,
cached stats, hard save button. Used by the browser page and both review
pages.
"""
from __future__ import annotations

from nicegui import run, ui

from translate_core.kg_review_ops import (
    AGENT_ROLES,
    INSTITUTION_KINDS,
)


# -----------------------------------------------------------------------
# Navigation — only 3 pages now
# -----------------------------------------------------------------------
_PAGES = [
    ("/kg", "Browser", "search"),
    ("/kg/review", "Extraction Review", "inbox"),
    ("/kg/kg-review", "KG Review", "rule"),
]


# -----------------------------------------------------------------------
# Ontology constants for the edit panel
# -----------------------------------------------------------------------
NODE_TYPES = ["concept", "agent", "source_text", "term", "institution", "translation_mapping"]

AGENT_ROLES = ["author", "translator", "editor", "curator", "artist",
               "interviewer", "interviewee", "choreographer", "director",
               "performer", "dancer", "composer", "dramaturg", "agent"]

INSTITUTION_KINDS = ["publisher", "gallery", "museum", "university",
                     "festival", "theatre", "journal", "organization",
                     "sponsor", "country", "other"]

PROJECT_TYPES = ["book_translation", "article_translation", "festival_programme",
                 "exhibition_catalogue", "book", "book_chapter", "journal_article",
                 "magazine_article", "newspaper_article", "web_source",
                 "exhibition_catalog", "interview", "thesis_dissertation",
                 "artwork", "performance", "cited_container", "cited_work"]

CONCEPT_RELATIONS = ["extends", "critiques", "redefines", "reappropriates", "related_to"]

# Valid edge relations per (source_type, target_type) — from ontology §3
EDGE_RELATIONS = {
    ("term", "translation_mapping"): ["has_mapping"],
    ("translation_mapping", "term"): ["maps_to"],
    ("term", "term"): ["translates_to"],
    ("term", "concept"): ["instantiates_concept"],
    ("source_text", "agent"): ["written_by", "translated_by", "edited_by", "performed_by"],
    ("source_text", "institution"): ["published_by", "translation_published_by", "hosted_by"],
    ("source_text", "source_text"): ["cited_in", "appears_in"],
    ("translation_mapping", "source_text"): ["instantiated_in"],
    ("translation_mapping", "agent"): ["attributed_to"],
    ("concept", "agent"): ["attributed_to"],
    ("concept", "concept"): CONCEPT_RELATIONS,
}


# -----------------------------------------------------------------------
# Resource access
# -----------------------------------------------------------------------
def get_resources() -> tuple:
    """Return (kg, glossary) from app_state."""
    import app_state
    kg = app_state.kg
    glossary = app_state.glossary
    if kg is None:
        return None, None
    return kg, glossary


# -----------------------------------------------------------------------
# Shared chrome: top bar + left drawer
# -----------------------------------------------------------------------
def kg_frame(active: str, *, search_callback=None):
    """Build the shared KG editor frame.

    Parameters
    ----------
    active : str
        Path of the current page (e.g. "/kg") for nav highlighting.
    search_callback : callable or None
        If provided, a search input is rendered in the sidebar and
        search_callback(query) is called on each keystroke (debounced).
        Only the browser page passes this; review pages don't need it.

    Returns (kg, glossary) or (None, None) if still initializing.
    """
    kg, glossary = get_resources()
    if kg is None:
        with ui.column().classes("w-full h-screen items-center justify-center"):
            ui.icon("hourglass_empty", size="48px").props("color=primary")
            ui.label("Backend still initializing — please reload in a moment.").classes("text-lg mt-2")
        return None, None

    import app_state
    from ..settings import install_dark_mode, dark_toggle_button

    app_state.apply_colors()
    dm = install_dark_mode()

    # ── Top bar ──
    with ui.card().classes(
        "w-full flex-row items-center justify-between rounded-none px-5 py-3"
    ).props("flat bordered"):
        with ui.row().classes("items-center gap-3"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
                "flat round dense"
            ).tooltip("Back to projects")
            ui.label("Knowledge Graph").classes("text-sm font-bold leading-none")
        with ui.row().classes("gap-2 items-center"):
            dark_toggle_button(dm)

    # ── Left drawer ──
    with ui.left_drawer(value=True, fixed=True).props("width=260 bordered"):
        with ui.column().classes("w-full p-4 gap-2"):
            # Navigation
            ui.label("Navigation").classes("text-xs font-bold uppercase tracking-widest opacity-50 mb-1")
            for path, label, icon in _PAGES:
                ui.button(label, icon=icon, on_click=lambda p=path: ui.navigate.to(p)).props(
                    f"dense no-caps align=left {'' if path == active else 'outline'}"
                    + (" color=primary" if path == active else " color=grey-7")
                ).classes("w-full justify-start")

            ui.separator().classes("my-2")

            # Statistics (cached — fetched once)
            ui.label("Statistics").classes("text-xs font-bold uppercase tracking-widest opacity-50 mb-1")
            stats = kg.stats()
            for label, key in [
                ("Nodes", "nodes_total"),
                ("Edges", "edges_total"),
                ("Terms", "node_term"),
                ("Mappings", "node_translation_mapping"),
                ("Concepts", "node_concept"),
                ("Agents", "node_agent"),
                ("Sources", "node_source_text"),
                ("Institutions", "node_institution"),
            ]:
                with ui.row().classes("w-full justify-between"):
                    ui.label(label).classes("text-xs opacity-70")
                    ui.label(str(stats.get(key, 0))).classes("text-xs font-mono")

            ui.separator().classes("my-2")

            # Hard save
            async def _hard_save():
                from ..components import busy_overlay
                async with busy_overlay("Saving database…"):
                    await run.io_bound(kg.save)
                ui.notify("Database saved.", type="positive")
            ui.button("Hard Save to Disk", icon="save", on_click=_hard_save).props(
                "flat dense no-caps color=primary"
            ).classes("w-full")

    return kg, glossary
```

- [ ] **Step 2: Verify import works**

Run: `.venv/bin/python -c "import ui.kg_editor.common; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add ui/kg_editor/common.py
git commit -m "refactor: rework KG editor sidebar — search, cached stats, 3 nav items"
```

---

### Task 3: Build the unified browser page (`browser.py`)

This is the core task. The file is large but has one clear responsibility: render the results list + edit panel + edge panel with cached loading.

**Files:**
- Create: `ui/kg_editor/browser.py`

- [ ] **Step 1: Create the browser page skeleton**

Create `ui/kg_editor/browser.py` with the full implementation:

```python
"""Unified KG browser — single-page editor for all 6 node types.

Replaces the 7 separate KG editor pages with one page that has:
- Type filter chips (cached lazy loading per type)
- Paginated results list (no full re-render on save)
- Side edit panel with all ontology fields per node type
- Edge panel with add/remove per node
"""
from __future__ import annotations

import html as html_lib
from typing import Any

from nicegui import run, ui

from .common import (
    AGENT_ROLES,
    CONCEPT_RELATIONS,
    EDGE_RELATIONS,
    INSTITUTION_KINDS,
    NODE_TYPES,
    PROJECT_TYPES,
    kg_frame,
)

PAGE_SIZE = 25

# ── Type metadata for display ──
_TYPE_LABELS = {
    "concept": "Concepts",
    "agent": "Agents",
    "source_text": "Sources",
    "term": "Terms",
    "institution": "Institutions",
    "translation_mapping": "Mappings",
}
_TYPE_ICONS = {
    "concept": "lightbulb",
    "agent": "person",
    "source_text": "menu_book",
    "term": "translate",
    "institution": "domain",
    "translation_mapping": "compare_arrows",
}
_TYPE_FIELD_LABEL = {
    "concept": "label",
    "agent": "name",
    "source_text": "title",
    "term": "term",
    "institution": "name",
    "translation_mapping": "lineage",
}


@ui.page("/kg")
def page_browser():
    kg, _glossary = kg_frame("/kg")
    if kg is None:
        return

    # ── State ──
    active_type: dict[str, str | None] = {"value": None}  # None = no chip selected
    cached_lists: dict[str, list[dict]] = {}  # type -> list of node dicts
    selected_node: dict[str, dict | None] = {"value": None}
    search_query: dict[str, str] = {"value": ""}
    page_ref: dict[str, int] = {"value": 1}

    # ── Layout: results column + edit panel ──
    with ui.row().classes("w-full gap-0 items-start"):
        # Results column (flex-1)
        results_col = ui.column().classes("flex-1 px-4 pb-4 gap-1")
        # Edit panel (fixed width)
        edit_col = ui.column().classes("w-[420px] shrink-0 p-4 gap-2")

    # ── Type chips row ──
    chips_row = ui.row().classes("w-full gap-2 px-4 pt-2")

    def _build_chips():
        chips_row.clear()
        with chips_row:
            # "All" chip
            ui.button("All", icon="apps",
                      on_click=lambda: _select_type(None)
                      ).props("dense no-caps outline color=primary").classes("text-xs")
            for t in NODE_TYPES:
                count = len(cached_lists.get(t, [])) if t in cached_lists else None
                label = _TYPE_LABELS.get(t, t)
                if count is not None:
                    label += f" ({count:,})"
                ui.button(label, icon=_TYPE_ICONS.get(t, "circle"),
                          on_click=lambda tt=t: _select_type(tt)
                          ).props("dense no-caps outline color=grey-7").classes("text-xs")

    # ── Fetch and cache a type list ──
    def _ensure_cached(ntype: str):
        if ntype not in cached_lists:
            cached_lists[ntype] = kg.get_all_by_type(ntype)
            _build_chips()  # update chip counts

    # ── Select a type chip ──
    def _select_type(ntype: str | None):
        active_type["value"] = ntype
        page_ref["value"] = 1
        search_query["value"] = ""
        if ntype is not None:
            _ensure_cached(ntype)
        _render_results()

    # ── Render the results list ──
    def _render_results():
        results_col.clear()
        with results_col:
            # Search bar
            search_input = ui.input(
                placeholder="Search by label, name, title, or ID…",
            ).props("outlined dense clearable debounce=300").classes("w-full mb-2")
            search_input.value = search_query["value"]
            search_input.on_value_change(lambda e: _on_search(e))

            atype = active_type["value"]
            if atype is None:
                ui.label("Select a type above to browse, or search across all types.").classes(
                    "text-caption text-grey-6 py-8"
                )
                return

            nodes = cached_lists.get(atype, [])
            q = search_query["value"].strip().lower()
            if q:
                field_key = _TYPE_FIELD_LABEL.get(atype, "label")
                filtered = [
                    n for n in nodes
                    if q in str(n.get(field_key, "")).lower()
                    or q in str(n.get("id", "")).lower()
                ]
            else:
                filtered = nodes

            total = len(filtered)
            total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
            current_page = min(page_ref["value"], total_pages)
            page_ref["value"] = current_page
            start = (current_page - 1) * PAGE_SIZE
            page_slice = filtered[start:start + PAGE_SIZE]

            ui.label(f"{total:,} {_TYPE_LABELS.get(atype, atype)}").classes(
                "text-caption text-grey-6 mb-1"
            )

            # Result rows
            for node in page_slice:
                _result_row(node, atype)

            # Pagination
            if total_pages > 1:
                ui.pagination(
                    1, total_pages, direction_links=True, value=current_page,
                    on_change=lambda e: _on_page_change(e.value),
                ).classes("mt-2")

    def _on_search(e):
        search_query["value"] = e.value or ""
        page_ref["value"] = 1
        _render_results()

    def _on_page_change(page_num):
        page_ref["value"] = page_num
        _render_results()

    # ── One result row ──
    def _result_row(node: dict, ntype: str):
        field_key = _TYPE_FIELD_LABEL.get(ntype, "label")
        label = str(node.get(field_key, node.get("id", "?")))
        node_id = node.get("id", "?")

        # Edge count
        edge_count = kg.G.degree(node_id) if kg.G.has_node(node_id) else 0

        caption_parts = []
        if ntype == "concept" and node.get("domain"):
            caption_parts.append(node["domain"])
        elif ntype == "agent" and node.get("role"):
            caption_parts.append(node["role"])
        elif ntype == "source_text" and node.get("year"):
            caption_parts.append(str(node["year"]))
        elif ntype == "term" and node.get("lang"):
            caption_parts.append(node["lang"])
        elif ntype == "institution" and node.get("kind"):
            caption_parts.append(node["kind"])
        elif ntype == "translation_mapping" and node.get("lineage"):
            caption_parts.append(node["lineage"])
        caption_parts.append(f"{edge_count} edges")
        caption = " · ".join(caption_parts)

        with ui.row().classes("w-full items-center gap-2 py-1 px-2 rounded cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800"
                              ).on("click", lambda nid=node_id: _select_node(nid)):
            ui.icon(_TYPE_ICONS.get(ntype, "circle"), size="16px").props("color=grey-6")
            with ui.column().classes("flex-1 min-w-0 gap-0"):
                ui.label(label).classes("text-sm font-medium truncate")
                ui.label(caption).classes("text-[10px] opacity-50")

    # ── Select a node → populate edit panel ──
    def _select_node(node_id: str):
        if not kg.G.has_node(node_id):
            return
        node_data = dict(kg.G.nodes[node_id])
        node_data["id"] = node_id
        selected_node["value"] = node_data
        _render_edit_panel()

    # ── Render the edit panel ──
    def _render_edit_panel():
        edit_col.clear()
        node = selected_node["value"]
        if node is None:
            with edit_col:
                ui.label("Select a node to edit").classes("text-caption text-grey-6 py-8")
            return

        ntype = node.get("type", "unknown")
        nid = node.get("id", "?")

        with edit_col:
            # Header
            with ui.row().classes("w-full items-center gap-2"):
                ui.icon(_TYPE_ICONS.get(ntype, "circle"), size="20px").props("color=primary")
                ui.label(_TYPE_LABELS.get(ntype, ntype)).classes("text-sm font-bold")
            ui.label(nid).classes("text-[10px] opacity-40 font-mono break-all")
            ui.separator().classes("my-2")

            # ── Fields section ──
            _render_fields(node, ntype, nid)

            # ── Edges section ──
            ui.separator().classes("my-2")
            _render_edges(nid)

            # ── Save / Delete ──
            ui.separator().classes("my-2")
            with ui.row().classes("gap-2"):
                ui.button("Save", icon="save",
                          on_click=lambda: _save_node(ntype, nid)
                          ).props("color=primary unelevated dense")
                ui.button("Delete", icon="delete",
                          on_click=lambda: _delete_node(nid)
                          ).props("color=negative outline dense")

    # ── Field forms per type ──
    _field_refs: dict[str, Any] = {}  # populated by _render_fields

    def _render_fields(node: dict, ntype: str, nid: str):
        _field_refs.clear()

        if ntype == "concept":
            _field_refs["label"] = ui.input("Label", value=node.get("label", "")).props("outlined dense").classes("w-full")
            _field_refs["domain"] = ui.input("Domain", value=node.get("domain", "")).props("outlined dense").classes("w-full")
            _field_refs["definition"] = ui.textarea("Definition", value=node.get("definition", "")).props("outlined dense").classes("w-full")
            ui.label("Bilingual fields").classes("text-caption opacity-60 mt-1")
            _field_refs["label_orig"] = ui.input("Label (orig)", value=node.get("label_orig") or "").props("outlined dense").classes("w-full")
            _field_refs["label_translation"] = ui.input("Label (translation)", value=node.get("label_translation") or "").props("outlined dense").classes("w-full")
            _field_refs["orig_lang"] = ui.input("Orig lang", value=node.get("orig_lang") or "").props("outlined dense").classes("w-full")
            _field_refs["translation_lang"] = ui.input("Translation lang", value=node.get("translation_lang") or "").props("outlined dense").classes("w-full")

        elif ntype == "agent":
            _field_refs["name"] = ui.input("Name", value=node.get("name", "")).props("outlined dense").classes("w-full")
            _field_refs["role"] = ui.select(AGENT_ROLES, value=node.get("role", "agent"), label="Role").props("outlined dense").classes("w-full")
            _field_refs["dedup_group"] = ui.input("Dedup group", value=node.get("dedup_group", "")).props("outlined dense readonly").classes("w-full")
            _field_refs["alt_spellings"] = ui.input("Alt spellings (comma-separated)", value=", ".join(node.get("alt_spellings", []))).props("outlined dense").classes("w-full")
            all_roles = node.get("all_roles", [])
            with ui.row().classes("gap-1"):
                for r in all_roles:
                    ui.badge(r, color="primary").classes("text-[10px]")
            ui.label(f"Mention count: {node.get('mention_count', 0)}").classes("text-caption opacity-60")
            _field_refs["origin"] = ui.input("Origin", value=node.get("origin") or "").props("outlined dense").classes("w-full")

        elif ntype == "source_text":
            _field_refs["title"] = ui.input("Title", value=node.get("title", "")).props("outlined dense").classes("w-full")
            _field_refs["year"] = ui.number("Year", value=node.get("year") or None, min=1800, max=2030).props("outlined dense").classes("w-full")
            _field_refs["project_type"] = ui.select(PROJECT_TYPES, value=node.get("project_type", ""), label="Project type").props("outlined dense").classes("w-full")
            ui.label("Bilingual fields").classes("text-caption opacity-60 mt-1")
            _field_refs["title_orig"] = ui.input("Title (orig)", value=node.get("title_orig") or "").props("outlined dense").classes("w-full")
            _field_refs["title_translation"] = ui.input("Title (translation)", value=node.get("title_translation") or "").props("outlined dense").classes("w-full")
            _field_refs["orig_lang"] = ui.input("Orig lang", value=node.get("orig_lang") or "").props("outlined dense").classes("w-full")
            _field_refs["translation_lang"] = ui.input("Translation lang", value=node.get("translation_lang") or "").props("outlined dense").classes("w-full")
            ui.label("Translation edition").classes("text-caption opacity-60 mt-1")
            te = node.get("translation_edition") or {}
            _field_refs["te_publisher"] = ui.input("Publisher", value=te.get("publisher") or "").props("outlined dense").classes("w-full")
            _field_refs["te_city"] = ui.input("City", value=te.get("city") or "").props("outlined dense").classes("w-full")
            _field_refs["te_year"] = ui.input("Year", value=str(te.get("year") or "")).props("outlined dense").classes("w-full")
            _field_refs["te_translator"] = ui.input("Translator", value=te.get("translator") or "").props("outlined dense").classes("w-full")
            _field_refs["te_language"] = ui.input("Language", value=te.get("language") or "").props("outlined dense").classes("w-full")

        elif ntype == "term":
            _field_refs["display_form"] = ui.input("Display form", value=node.get("display_form", "")).props("outlined dense").classes("w-full")
            _field_refs["is_animate"] = ui.checkbox("Animate", value=node.get("is_animate", False))
            _field_refs["is_phrase"] = ui.checkbox("Phrase", value=node.get("is_phrase", False))
            variants = node.get("variants", [])
            if variants:
                ui.label("Variants: " + ", ".join(variants)).classes("text-caption opacity-60")
            ui.label(f"Term: {node.get('term', '?')} · Lang: {node.get('lang', '?')} · Frequency: {node.get('frequency', 0)}").classes("text-caption opacity-60")

        elif ntype == "institution":
            _field_refs["name"] = ui.input("Name", value=node.get("name", "")).props("outlined dense").classes("w-full")
            _field_refs["kind"] = ui.select(INSTITUTION_KINDS, value=node.get("kind", "other"), label="Kind").props("outlined dense").classes("w-full")
            _field_refs["city"] = ui.input("City", value=node.get("city") or "").props("outlined dense").classes("w-full")

        elif ntype == "translation_mapping":
            _field_refs["confidence"] = ui.number("Confidence", value=node.get("confidence", 0.5), min=0.0, max=1.0, step=0.05, format="%.2f").props("outlined dense").classes("w-full")
            _field_refs["lineage"] = ui.input("Lineage", value=node.get("lineage", "")).props("outlined dense").classes("w-full")
            _field_refs["register"] = ui.input("Register", value=node.get("register", "")).props("outlined dense").classes("w-full")
            _field_refs["gloss"] = ui.textarea("Gloss", value=node.get("gloss") or "").props("outlined dense").classes("w-full")
            _field_refs["year"] = ui.number("Year", value=node.get("year") or None, min=1800, max=2030).props("outlined dense").classes("w-full")
            verified = node.get("verified", False)
            _field_refs["verified"] = ui.checkbox("Verified", value=verified)
            if verified:
                _field_refs["verified"].props("disabled")  # monotonic — can't uncheck

        else:
            ui.label(f"No editor for type '{ntype}'").classes("text-caption text-warning")

    # ── Edge panel ──
    def _render_edges(nid: str):
        ui.label("Edges").classes("text-xs font-bold uppercase tracking-widest opacity-50")

        # Outgoing edges
        out_edges = list(kg.G.out_edges(nid, data=True)) if kg.G.has_node(nid) else []
        in_edges = list(kg.G.in_edges(nid, data=True)) if kg.G.has_node(nid) else []

        if out_edges:
            ui.label("Outgoing").classes("text-caption font-bold mt-1")
            for u, v, d in out_edges:
                rel = d.get("relation", "?")
                target_name = _node_label(v)
                with ui.row().classes("w-full items-center gap-1"):
                    ui.badge(rel, color="blue-2").classes("text-[9px]")
                    ui.label("→").classes("text-xs opacity-50")
                    ui.label(target_name).classes("text-xs flex-1 truncate")
                    ui.button(icon="close", on_click=lambda uu=u, vv=v: _remove_edge(uu, vv)
                              ).props("flat round dense size=xs color=negative")

        if in_edges:
            ui.label("Incoming").classes("text-caption font-bold mt-1")
            for u, v, d in in_edges:
                rel = d.get("relation", "?")
                source_name = _node_label(u)
                with ui.row().classes("w-full items-center gap-1"):
                    ui.label(source_name).classes("text-xs flex-1 truncate")
                    ui.label("→").classes("text-xs opacity-50")
                    ui.badge(rel, color="green-2").classes("text-[9px]")
                    ui.button(icon="close", on_click=lambda uu=u, vv=v: _remove_edge(uu, vv)
                              ).props("flat round dense size=xs color=negative")

        if not out_edges and not in_edges:
            ui.label("No edges.").classes("text-caption text-grey-6")

        # Add edge
        with ui.expansion("Add edge", icon="add").classes("w-full mt-2"):
            _render_add_edge(nid)

    def _render_add_edge(nid: str):
        node_type = kg.G.nodes[nid].get("type", "") if kg.G.has_node(nid) else ""

        # Build valid relations based on source type
        valid_rels = []
        for (src_t, tgt_t), rels in EDGE_RELATIONS.items():
            if src_t == node_type:
                for r in rels:
                    valid_rels.append((r, tgt_t))

        if not valid_rels:
            ui.label("No valid edge types for this node type.").classes("text-caption text-grey-6")
            return

        rel_select = ui.select(
            {r: f"{r} → {tgt}" for r, tgt in valid_rels},
            label="Relation",
        ).props("outlined dense").classes("w-full")

        target_input = ui.input("Target node ID", placeholder="e.g. agent:michel-foucault").props("outlined dense").classes("w-full")

        async def _do_add():
            if not rel_select.value or not target_input.value:
                ui.notify("Select relation and target.", type="warning")
                return
            rel_val = rel_select.value  # format: "relation -> target_type"
            rel_name = rel_val.split(" → ")[0] if " → " in rel_val else rel_val
            tgt = target_input.value.strip()
            if not kg.G.has_node(tgt):
                ui.notify(f"Node '{tgt}' not found.", type="negative")
                return
            await run.io_bound(_add_edge_async, nid, tgt, rel_name)

        ui.button("Link", icon="link", on_click=_do_add).props("color=primary dense unelevated").classes("w-full mt-1")

    async def _add_edge_async(src: str, tgt: str, rel: str):
        if not kg.G.has_edge(src, tgt):
            kg.G.add_edge(src, tgt, relation=rel)
            kg._persist_edge(src, tgt)
        ui.notify("Edge added.", type="positive")
        _render_edit_panel()  # refresh edge list only

    async def _remove_edge(u: str, v: str):
        await run.io_bound(_remove_edge_async, u, v)

    async def _remove_edge_async(u: str, v: str):
        if kg.G.has_edge(u, v):
            kg.G.remove_edge(u, v)
            kg._delete_edge_persist(u, v)
        ui.notify("Edge removed.", type="positive")
        _render_edit_panel()

    def _node_label(node_id: str) -> str:
        if not kg.G.has_node(node_id):
            return node_id
        d = kg.G.nodes[node_id]
        for key in ("name", "label", "title", "term"):
            if d.get(key):
                return str(d[key])
        return node_id

    # ── Save node ──
    async def _save_node(ntype: str, nid: str):
        f = _field_refs
        try:
            if ntype == "concept":
                await run.io_bound(kg.update_concept_metadata, nid,
                                   label=f["label"].value, domain=f["domain"].value,
                                   definition=f["definition"].value,
                                   label_orig=f["label_orig"].value or None,
                                   label_translation=f["label_translation"].value or None,
                                   orig_lang=f["orig_lang"].value or None,
                                   translation_lang=f["translation_lang"].value or None)
            elif ntype == "agent":
                alt_sp = [s.strip() for s in (f["alt_spellings"].value or "").split(",") if s.strip()]
                await run.io_bound(kg.update_agent_node, nid,
                                   name=f["name"].value, role=f["role"].value)
                # alt_spellings update directly (no API method yet)
                node = kg.G.nodes[nid]
                node["alt_spellings"] = alt_sp
                if f["origin"].value:
                    node["origin"] = f["origin"].value
                kg._persist_node(nid)
            elif ntype == "source_text":
                te_parts = {}
                if f["te_publisher"].value: te_parts["publisher"] = f["te_publisher"].value.strip()
                if f["te_city"].value: te_parts["city"] = f["te_city"].value.strip()
                if f["te_translator"].value: te_parts["translator"] = f["te_translator"].value.strip()
                if f["te_language"].value: te_parts["language"] = f["te_language"].value.strip()
                te_year_val = (f["te_year"].value or "").strip()
                if te_year_val:
                    try: te_parts["year"] = int(te_year_val)
                    except ValueError: pass
                await run.io_bound(kg.update_source_text_node, nid,
                                   title=f["title"].value,
                                   year=int(f["year"].value) if f["year"].value else None,
                                   project_type=f["project_type"].value,
                                   title_orig=f["title_orig"].value or None,
                                   title_translation=f["title_translation"].value or None,
                                   orig_lang=f["orig_lang"].value or None,
                                   translation_lang=f["translation_lang"].value or None,
                                   translation_edition=te_parts if te_parts else None)
            elif ntype == "term":
                await run.io_bound(kg.update_term_node, nid,
                                   display_form=f["display_form"].value or None,
                                   is_animate=f["is_animate"].value,
                                   is_phrase=f["is_phrase"].value)
            elif ntype == "institution":
                await run.io_bound(kg.update_institution_node, nid,
                                   name=f["name"].value,
                                   kind=f["kind"].value,
                                   city=f["city"].value or None)
            elif ntype == "translation_mapping":
                verified = f["verified"].value
                await run.io_bound(kg.update_translation_mapping, nid,
                                   confidence=f["confidence"].value,
                                   lineage=f["lineage"].value,
                                   register=f["register"].value,
                                   gloss=f["gloss"].value or None,
                                   year=int(f["year"].value) if f["year"].value else None,
                                   verified=verified if verified and not kg.G.nodes[nid].get("verified") else None)
            ui.notify("Saved.", type="positive")
            # Update cached list entry (no re-fetch)
            if ntype in cached_lists:
                for i, n in enumerate(cached_lists[ntype]):
                    if n.get("id") == nid:
                        cached_lists[ntype][i] = dict(kg.G.nodes[nid])
                        cached_lists[ntype][i]["id"] = nid
                        break
            # Re-render results to update label
            _render_results()
            _render_edit_panel()
        except Exception as e:
            ui.notify(f"Save failed: {e}", type="negative")

    async def _delete_node(nid: str):
        # Confirmation dialog
        with ui.dialog() as dialog:
            with ui.card().classes("min-w-[360px]"):
                ui.label("Delete node?").classes("text-lg font-bold")
                ui.label(f"This will remove '{nid}' and all its edges. This cannot be undone.").classes("text-sm opacity-70 my-3")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Cancel", on_click=lambda: dialog.close()).props("flat")
                    ui.button("Delete", on_click=lambda: (dialog.close(), _do_delete(nid))).props("color=negative unelevated")
        dialog.open()

    async def _do_delete(nid: str):
        await run.io_bound(kg.remove_node, nid)
        ui.notify("Deleted.", type="positive")
        # Remove from cache
        node_type = selected_node["value"].get("type") if selected_node["value"] else None
        if node_type and node_type in cached_lists:
            cached_lists[node_type] = [n for n in cached_lists[node_type] if n.get("id") != nid]
        selected_node["value"] = None
        _render_results()
        _render_edit_panel()
        _build_chips()

    # ── Initial state ──
    _build_chips()
    with results_col:
        ui.label("Select a type above to browse, or search across all types.").classes(
            "text-caption text-grey-6 py-8"
        )
    with edit_col:
        ui.label("Select a node to edit").classes("text-caption text-grey-6 py-8")
```

- [ ] **Step 2: Verify import works**

Run: `.venv/bin/python -c "import ui.kg_editor.browser; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add ui/kg_editor/browser.py tests/test_kg_editor_browser.py
git commit -m "feat: unified KG browser page with cached loading, side panel edit, edge panel"
```

---

### Task 4: Update `__init__.py` and delete old pages

**Files:**
- Modify: `ui/kg_editor/__init__.py`
- Delete: `ui/kg_editor/concepts.py`, `terms.py`, `agents.py`, `sources.py`, `lineages.py`
- Modify: `ui/kg_editor/review.py`, `ui/kg_editor/kg_review.py` (sidebar call)

- [ ] **Step 1: Update `__init__.py`**

Replace `ui/kg_editor/__init__.py` with:

```python
"""KG editor pages — unified browser + review queues."""
from . import common  # noqa: F401
from . import browser  # noqa: F401
from . import review, kg_review  # noqa: F401
```

- [ ] **Step 2: Delete old pages**

```bash
rm ui/kg_editor/concepts.py ui/kg_editor/terms.py ui/kg_editor/agents.py ui/kg_editor/sources.py ui/kg_editor/lineages.py
```

- [ ] **Step 3: Update review.py to use new `kg_frame`**

In `ui/kg_editor/review.py`, the call to `kg_frame` needs to pass the active path. Find the `kg_frame` call and ensure it's called as `kg_frame("/kg/review")`.

- [ ] **Step 4: Update kg_review.py to use new `kg_frame`**

In `ui/kg_editor/kg_review.py`, ensure `kg_frame("/kg/kg-review")` is called.

- [ ] **Step 5: Verify imports work**

Run: `.venv/bin/python -c "import ui.kg_editor; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add ui/kg_editor/__init__.py ui/kg_editor/review.py ui/kg_editor/kg_review.py
git rm ui/kg_editor/concepts.py ui/kg_editor/terms.py ui/kg_editor/agents.py ui/kg_editor/sources.py ui/kg_editor/lineages.py
git commit -m "refactor: delete old KG editor pages, route /kg to unified browser"
```

---

### Task 5: Verify and run tests

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ inline_test/ -v --timeout=120 -q`
Expected: Same pass/fail count as before (3 pre-existing failures), no new failures.

- [ ] **Step 2: Smoke test the browser page in the app**

Open `http://localhost:8080/kg` in the browser. Verify:
- Type chips appear at the top
- Clicking "Concepts" loads a paginated list
- Clicking a concept opens the edit panel on the right
- All fields are visible (label, domain, definition, bilingual)
- Edges section shows incoming/outgoing edges
- Save works without re-rendering the whole list
- Delete shows a confirmation dialog

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: post-implementation fixes for KG browser"
```