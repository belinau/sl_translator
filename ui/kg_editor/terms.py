"""KG editor — Terms & Mappings page. NiceGUI port of kg_editor_ui.py page_terms (lines 477–606)."""

from __future__ import annotations

from nicegui import run, ui


# ---------------------------------------------------------------------------
# Page registration — /kg redirects here; /kg/terms is the canonical path.
# ---------------------------------------------------------------------------


@ui.page("/kg")
@ui.page("/kg/terms")
async def page_terms():
    from ui.kg_editor.common import kg_frame

    kg, _glossary = kg_frame("/kg/terms")
    if kg is None:
        return

    results = ui.column().classes("w-full q-px-md q-pb-md")

    search_ref: dict = {"value": ""}

    search_input = ui.input(
        "Search term (English or Slovenian):",
        placeholder="e.g. gaze, pogled",
    ).props("outlined dense clearable debounce=300").classes("w-full q-mb-md")

    def _on_search_change(e):
        search_ref["value"] = e.value or ""
        render()

    search_input.on_value_change(_on_search_change)

    # ── Master render ─────────────────────────────────────────────────────
    def render():
        query = search_ref["value"].strip()

        results.clear()
        with results:
            ui.label("Search Terms & Edit Mappings").classes("text-h6 q-mb-sm")

            if not query:
                stats = kg.stats()
                total_terms = stats.get("node_term", 0)
                ui.label(
                    f"{total_terms} terms in graph. "
                    "Type a search query above to find and edit terms and their mappings."
                ).classes(
                    "text-caption text-grey-6"
                )
                _render_quick_add(kg, render)
                _render_add_variant(kg, render)
                return

            matches = kg.extract_entities(query, target_lang="sl")
            if not matches:
                ui.label(f"No terms matching '{query}'.").classes("text-warning q-mb-md")
                _render_quick_add(kg, render)
                _render_add_variant(kg, render)
                return

            ui.label(f"{len(matches)} terms found. Click a term to expand.").classes(
                "text-caption q-mb-sm"
            )

            for match in matches:
                _render_match(match, kg, render)

            ui.separator().classes("q-my-md")
            _render_quick_add(kg, render)
            _render_add_variant(kg, render)


    # ── Render a single match expansion ───────────────────────────────────
    def _render_match(match: dict, kg, render_fn):
        term_id = match.get("id")

        with ui.expansion(
            f"{match.get('term', '?')} ({match.get('lang', '?').upper()})",
            caption=f"freq {match.get('frequency', 0)}",
        ).classes("w-full q-mb-sm"):
            ui.label(f"ID: {term_id}  |  Animate: {match.get('is_animate', False)}").classes(
                "text-caption q-mb-sm"
            )

            # ── Term edit form ────────────────────────────────────────────
            cur_display = match.get("display_form", "") or ""
            cur_animate = match.get("is_animate", False)
            cur_phrase = match.get("is_phrase", False)

            display_input = ui.input("Display form:", value=cur_display).classes("w-full q-mb-xs")
            anim_check = ui.checkbox("Animate", value=cur_animate)
            phrase_check = ui.checkbox("Phrase", value=cur_phrase)

            async def _on_update():
                await run.io_bound(
                    kg.update_term_node,
                    term_id,
                    display_form=display_input.value or None,
                    is_animate=anim_check.value,
                    is_phrase=phrase_check.value,
                )
                ui.notify("Term updated.", type="positive")
                render_fn()

            async def _on_delete():
                await run.io_bound(kg.remove_node, term_id)
                ui.notify("Deleted.", type="positive")
                render_fn()

            with ui.row().classes("q-gutter-sm q-mb-md"):
                ui.button("Update Term", icon="save", color="primary", on_click=_on_update)
                ui.button("Delete", icon="delete", color="negative", on_click=_on_delete)

            # ── Variants ──────────────────────────────────────────────────
            variants = match.get("variants", [])
            if variants:
                ui.markdown(f"**Variants:** {', '.join(variants)}").classes("q-mb-xs")

            # ── Translation mappings ────────────────────────────────────
            translations = match.get("translations", [])
            if not translations:
                ui.label("No mappings.").classes("text-caption text-grey-6")
            else:
                ui.markdown(f"**{len(translations)} translations:**").classes("q-mb-xs")
                for t in translations:
                    _render_translation(t, kg, render_fn)

    # ── Render a single translation mapping ───────────────────────────────
    def _render_translation(t: dict, kg, render_fn):
        mapping_id = t.get("mapping_id")
        lineage = t.get("lineage", "general")

        ui.markdown(
            f"→ **`{t.get('term')}`** | Lineage: *{lineage}* | "
            f"Conf: `{t.get('confidence', 0.5):.2f}`"
        )

        sources = t.get("sources", [])
        agents = t.get("agents", [])
        if sources or agents:
            ui.label(f"Context: {', '.join(sources + agents)}").classes(
                "text-[11px] opacity-45 q-mb-xs"
            )

        if mapping_id and kg.G.has_node(mapping_id):
            with ui.card().classes("w-full q-mb-sm").props("flat bordered"):
                m_lin = ui.input("Lineage:", value=lineage).classes("w-full q-mb-xs")
                m_reg = ui.select(
                    ["academic", "manifesto", "poetic", "colloquial"],
                    value=t.get("register", "academic"),
                    label="Register",
                ).classes("w-full q-mb-xs")
                m_gloss = ui.input("Gloss:", value=t.get("gloss", "")).classes("w-full q-mb-xs")
                m_conf = ui.slider(min=0.0, max=1.0, step=0.05, value=float(t.get("confidence", 0.5))).props(
                    "label-always"
                ).classes("w-full q-mb-xs")
                async def _on_update_mapping():
                    await run.io_bound(
                        kg.update_translation_mapping,
                        mapping_id,
                        lineage=m_lin.value,
                        register=m_reg.value,
                        gloss=m_gloss.value,
                        confidence=m_conf.value,
                    )
                    ui.notify("Updated.", type="positive")
                    render_fn()

                async def _on_delete_mapping():
                    await run.io_bound(kg.remove_node, mapping_id)
                    ui.notify("Deleted.", type="positive")
                    render_fn()

                with ui.row().classes("q-gutter-sm"):
                    ui.button("Update", icon="save", color="primary", on_click=_on_update_mapping)
                    ui.button("Delete", icon="delete", color="negative", on_click=_on_delete_mapping)

    # ── Quick-Add Translation Mapping ─────────────────────────────────────
    def _render_quick_add(kg, render_fn):
        with ui.expansion("Quick-Add Translation Mapping", icon="bolt").classes("w-full q-mb-md"):
            q_src = ui.input("Source term (en):", placeholder="e.g. gaze").classes("w-full q-mb-xs")
            q_tgt = ui.input("Target term (sl):", placeholder="e.g. pogled").classes("w-full q-mb-xs")
            q_lin = ui.input("Lineage:", placeholder="e.g. Mulveyan").classes("w-full q-mb-xs")
            q_gloss = ui.input("Gloss:", placeholder="optional note").classes("w-full q-mb-xs")

            async def _on_create():
                src = q_src.value.strip()
                tgt = q_tgt.value.strip()
                if not src or not tgt:
                    ui.notify("Both source and target terms required.", type="warning")
                    return
                await run.io_bound(
                    _quick_add_mapping, kg, src, tgt,
                    q_lin.value.strip(), q_gloss.value.strip(),
                )
                ui.notify(f"Created: '{src}' → '{tgt}'.", type="positive")
                render_fn()

            ui.button("Create Mapping", icon="add", color="primary", on_click=_on_create).classes("w-full")

    # ── Add Variant ───────────────────────────────────────────────────────
    def _render_add_variant(kg, render_fn):
        with ui.expansion("Add Variant to Term", icon="label").classes("w-full q-mb-md"):
            var_search = ui.input("Find term:").props("outlined dense clearable debounce=300").classes("w-full q-mb-xs")

            # Container for search results — populated on input
            var_results = ui.column().classes("w-full")

            def _on_var_search():
                var_search_val = (var_search.value or "").strip().lower()
                var_results.clear()
                if not var_search_val:
                    return
                all_terms = kg.get_all_by_type("term")
                hits = [d for d in all_terms if var_search_val in (d.get("term", "") or "").lower()]
                if not hits:
                    with var_results:
                        ui.label(f"No terms matching '{var_search_val}'.").classes("text-caption text-grey-6")
                    return
                choices = {d["id"]: f"{d.get('term')} ({d.get('lang')})" for d in hits}
                with var_results:
                    sel = ui.select(choices, value=list(choices.keys())[0] if choices else None, label="Term").classes(
                        "w-full q-mb-xs"
                    )
                    var_text = ui.input("Variant form:").classes("w-full q-mb-xs")

                    async def _on_add():
                        if not sel.value or not (var_text.value or "").strip():
                            ui.notify("Select a term and enter a variant form.", type="warning")
                            return
                        await run.io_bound(kg.add_variant, sel.value, var_text.value.strip())
                        ui.notify(f"Added '{var_text.value.strip()}'.", type="positive")
                        render_fn()

                    ui.button("Add Variant", icon="add", color="primary", on_click=_on_add).classes("w-full")

            var_search.on_value_change(lambda e: _on_var_search())

    # ── Quick-add helper (runs on io thread) ──────────────────────────────
    def _quick_add_mapping(kg, src: str, tgt: str, lin: str, gloss: str):
        cid = kg.add_concept_node(
            f"concept:{src.replace(' ', '_').lower()}", label=src, domain="General",
        )
        sid = kg.add_term_node(src, "en", concept_id=cid, is_phrase=True)
        tid = kg.add_term_node(tgt, "sl", is_phrase=True)
        kg.link_translations_with_context(
            src_term_id=sid, tgt_term_id=tid,
            confidence=1.0, lineage=lin or "general", gloss=gloss, verified=True,
        )

    # ── Initial render ────────────────────────────────────────────────────
    render()