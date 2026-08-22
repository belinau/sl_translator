"""Unified KG browser — single-page editor for all 6 node types.

Replaces the 7 separate KG editor pages with one page that has:
- Type filter chips (cached lazy loading per type)
- Paginated results list (no full re-render on save)
- Side edit panel with all ontology fields per node type
- Edge panel with add/remove per node
"""
from __future__ import annotations

from typing import Any

from nicegui import run, ui

from .common import (
    AGENT_ROLES,
    EDGE_RELATIONS,
    INSTITUTION_KINDS,
    NODE_TYPES,
    PROJECT_TYPES,
    kg_frame,
)

PAGE_SIZE = 25

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
    active_type: dict[str, str | None] = {"value": None}
    cached_lists: dict[str, list[dict]] = {}
    selected_node: dict[str, dict | None] = {"value": None}
    search_query: dict[str, str] = {"value": ""}
    page_ref: dict[str, int] = {"value": 1}
    _field_refs: dict[str, Any] = {}

    # ── Layout ──
    with ui.row().classes("w-full gap-0 items-start"):
        results_col = ui.column().classes("flex-1 px-4 pb-4 gap-1")
        edit_col = ui.column().classes("w-[420px] shrink-0 p-4 gap-2")

    chips_row = ui.row().classes("w-full gap-2 px-4 pt-2")

    def _build_chips():
        chips_row.clear()
        with chips_row:
            # "All" chip — acts as reset to category overview
            all_active = active_type["value"] is None
            ui.button("All categories", icon="apps", on_click=lambda: _select_type(None)
                      ).props(
                          f"dense no-caps {'unelevated color=primary' if all_active else 'outline color=grey-7'}"
                      ).classes("text-xs")
            for t in NODE_TYPES:
                count = len(cached_lists.get(t, [])) if t in cached_lists else None
                label = _TYPE_LABELS.get(t, t)
                if count is not None:
                    label += f" ({count:,})"
                is_active = active_type["value"] == t
                ui.button(label, icon=_TYPE_ICONS.get(t, "circle"),
                          on_click=lambda tt=t: _select_type(tt)
                          ).props(
                              f"dense no-caps {'unelevated color=primary' if is_active else 'outline color=grey-7'}"
                          ).classes("text-xs")

    def _ensure_cached(ntype: str):
        if ntype not in cached_lists:
            cached_lists[ntype] = kg.get_all_by_type(ntype)
            _build_chips()

    def _select_type(ntype: str | None):
        active_type["value"] = ntype
        page_ref["value"] = 1
        search_query["value"] = ""
        if ntype is not None:
            _ensure_cached(ntype)
        _build_chips()
        _render_results()

    def _render_results():
        results_col.clear()
        with results_col:
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

            for node in page_slice:
                _result_row(node, atype)

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

    def _result_row(node: dict, ntype: str):
        field_key = _TYPE_FIELD_LABEL.get(ntype, "label")
        label = str(node.get(field_key, node.get("id", "?")))
        node_id = node.get("id", "?")
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

        with ui.row().classes(
            "w-full items-center gap-2 py-1 px-2 rounded cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800"
        ).on("click", lambda nid=node_id: _select_node(nid)):
            ui.icon(_TYPE_ICONS.get(ntype, "circle"), size="16px").props("color=grey-6")
            with ui.column().classes("flex-1 min-w-0 gap-0"):
                ui.label(label).classes("text-sm font-medium truncate")
                ui.label(caption).classes("text-[10px] opacity-50")

    def _select_node(node_id: str):
        if not kg.G.has_node(node_id):
            return
        node_data = dict(kg.G.nodes[node_id])
        node_data["id"] = node_id
        selected_node["value"] = node_data
        _render_edit_panel()

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
            with ui.row().classes("w-full items-center gap-2"):
                ui.icon(_TYPE_ICONS.get(ntype, "circle"), size="20px").props("color=primary")
                ui.label(_TYPE_LABELS.get(ntype, ntype)).classes("text-sm font-bold")
            ui.label(nid).classes("text-[10px] opacity-40 font-mono break-all")
            ui.separator().classes("my-2")

            _render_fields(node, ntype, nid)

            ui.separator().classes("my-2")
            _render_edges(nid)

            ui.separator().classes("my-2")
            with ui.row().classes("gap-2"):
                ui.button("Save", icon="save",
                          on_click=lambda: _save_node(ntype, nid)
                          ).props("color=primary unelevated dense")
                ui.button("Delete", icon="delete",
                          on_click=lambda: _delete_node(nid)
                          ).props("color=negative outline dense")

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
            with ui.row().classes("gap-1 flex-wrap"):
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
                _field_refs["verified"].props("disabled")

        else:
            ui.label(f"No editor for type '{ntype}'").classes("text-caption text-warning")

    def _render_edges(nid: str):
        ui.label("Edges").classes("text-xs font-bold uppercase tracking-widest opacity-50")

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

        with ui.expansion("Add edge", icon="add").classes("w-full mt-2"):
            _render_add_edge(nid)

    def _render_add_edge(nid: str):
        node_type = kg.G.nodes[nid].get("type", "") if kg.G.has_node(nid) else ""
        valid_rels: list[tuple[str, str]] = []
        for (src_t, tgt_t), rels in EDGE_RELATIONS.items():
            if src_t == node_type:
                for r in rels:
                    valid_rels.append((r, tgt_t))

        if not valid_rels:
            ui.label("No valid edge types for this node type.").classes("text-caption text-grey-6")
            return

        rel_select = ui.select(
            {f"{r} → {tgt}": f"{r} → {tgt}" for r, tgt in valid_rels},
            label="Relation",
        ).props("outlined dense").classes("w-full")

        target_input = ui.input("Target node ID", placeholder="e.g. agent:michel-foucault").props("outlined dense").classes("w-full")

        async def _do_add():
            if not rel_select.value or not target_input.value:
                ui.notify("Select relation and target.", type="warning")
                return
            rel_name = rel_select.value.split(" → ")[0] if " → " in rel_select.value else rel_select.value
            tgt = target_input.value.strip()
            if not kg.G.has_node(tgt):
                ui.notify(f"Node '{tgt}' not found.", type="negative")
                return
            await run.io_bound(_add_edge_async, nid, tgt, rel_name)

        ui.button("Link", icon="link", on_click=_do_add).props("color=primary dense unelevated").classes("w-full mt-1")

    async def _add_edge(src: str, tgt: str, rel: str):
        def _do_add():
            if not kg.G.has_edge(src, tgt):
                kg.G.add_edge(src, tgt, relation=rel)
                kg._persist_edge(src, tgt)
                return True
            return False
        added = await run.io_bound(_do_add)
        if added:
            ui.notify("Edge added.", type="positive")
        else:
            ui.notify("Edge already exists.", type="info")
        _render_edit_panel()

    async def _remove_edge(u: str, v: str):
        def _do_remove():
            if kg.G.has_edge(u, v):
                kg.G.remove_edge(u, v)
                kg._delete_edge_persist(u, v)
                return True
            return False
        removed = await run.io_bound(_do_remove)
        if removed:
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
                node = kg.G.nodes[nid]
                node["alt_spellings"] = alt_sp
                if f["origin"].value:
                    node["origin"] = f["origin"].value
                kg._persist_node(nid)
            elif ntype == "source_text":
                te_parts: dict = {}
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
                already_verified = kg.G.nodes[nid].get("verified", False)
                await run.io_bound(kg.update_translation_mapping, nid,
                                   confidence=f["confidence"].value,
                                   lineage=f["lineage"].value,
                                   register=f["register"].value,
                                   gloss=f["gloss"].value or None,
                                   year=int(f["year"].value) if f["year"].value else None,
                                   verified=verified if verified and not already_verified else None)
            ui.notify("Saved.", type="positive")
            if ntype in cached_lists:
                for i, n in enumerate(cached_lists[ntype]):
                    if n.get("id") == nid:
                        cached_lists[ntype][i] = dict(kg.G.nodes[nid])
                        cached_lists[ntype][i]["id"] = nid
                        break
            _render_results()
            _render_edit_panel()
        except Exception as e:
            ui.notify(f"Save failed: {e}", type="negative")

    def _delete_node(nid: str):
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