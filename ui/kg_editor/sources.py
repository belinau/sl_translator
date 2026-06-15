"""KG editor — Source Texts page. NiceGUI port of kg_editor_ui.py page_sources (lines 770-877)."""

from __future__ import annotations

from nicegui import run, ui

PAGE_SIZE = 15


def _has_translation(s: dict, has_translator: bool) -> bool:
    """A source has a known published translation iff it has translation_edition,
    a title_translation, or a translated_by edge."""
    if s.get("translation_edition"):
        return True
    if (s.get("title_translation") or "").strip():
        return True
    return has_translator


@ui.page("/kg/sources")
async def page_sources():
    from ui.kg_editor.common import kg_frame

    kg, _glossary = kg_frame("/kg/sources")
    if kg is None:
        return

    # ── State closures ────────────────────────────────────────────────────
    search_ref: dict = {"value": ""}
    ptype_ref: dict = {"value": "all"}
    noauth_ref: dict = {"value": False}
    tstatus_ref: dict = {"value": "all"}
    page_ref: dict = {"value": 1}

    results = ui.column().classes("w-full")

    def render():
        sources = kg.get_all_by_type("source_text") if kg else []
        if not sources:
            results.clear()
            with results:
                ui.label("No source texts registered yet.").classes("text-grey-6 q-pa-md")
            return

        # Build agent_opts, src_author, src_translator on each render
        agent_opts: dict[str, str] = {
            a["id"]: a.get("name", a["id"]) for a in kg.get_all_by_type("agent")
        }
        src_author: dict[str, str] = {}
        src_translator: dict[str, str] = {}
        for u, v, d in kg.G.edges(data=True):
            rel = d.get("relation")
            if rel == "written_by":
                src_author.setdefault(u, v)
            elif rel == "translated_by":
                src_translator.setdefault(u, v)

        # Filters
        src_search = search_ref["value"].strip().lower()
        src_ptype = ptype_ref["value"]
        no_author_only = noauth_ref["value"]
        src_tstatus = tstatus_ref["value"]

        def _source_matches(s: dict) -> bool:
            if src_ptype != "all" and (s.get("project_type", "_unset") or "_unset") != src_ptype:
                return False
            if no_author_only and src_author.get(s["id"]):
                return False
            if src_search:
                title = (s.get("title", "") or "").lower()
                author_id = src_author.get(s["id"], "")
                author_name = agent_opts.get(author_id, "").lower()
                if src_search not in title and src_search not in author_name:
                    return False
            if src_tstatus != "all":
                has_t = _has_translation(s, s["id"] in src_translator)
                if src_tstatus == "translated" and not has_t:
                    return False
                if src_tstatus == "untranslated" and has_t:
                    return False
            return True

        filtered = [s for s in sources if _source_matches(s)]

        results.clear()
        with results:
            ui.label(f"{len(sources)} sources total. Filter below to narrow before browsing.").classes(
                "text-caption q-mb-sm"
            )

            ui.label(f"{len(filtered)} match filters.").classes("text-caption q-mb-sm")

            # Pagination
            total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
            current_page = min(page_ref["value"], total_pages)
            page_ref["value"] = current_page
            page_start = (current_page - 1) * PAGE_SIZE
            page_slice = filtered[page_start : page_start + PAGE_SIZE]

            if total_pages > 1:
                ui.pagination(
                    1, total_pages,
                    direction_links=True,
                    value=current_page,
                    on_change=lambda e: _update_page(e.value),
                ).classes("q-mb-md")

            # Source list
            for s in page_slice:
                _render_source(s, kg, agent_opts, src_author, src_translator, render)

            # Divider + New Source Text
            ui.separator().classes("q-my-md")
            with ui.expansion("New Source Text", icon="add").classes("w-full"):
                _render_new_source(kg, agent_opts, render)

    # ── Filter controls (built once, outside render) ─────────────────────
    ptype_opts = ["all"] + sorted(
        {s.get("project_type", "_unset") or "_unset" for s in (kg.get_all_by_type("source_text") if kg else [])}
    )

    with ui.row().classes("w-full items-center q-gutter-sm"):
        search_input = ui.input(
            "Search sources",
            value=search_ref["value"],
        ).props("outlined dense clearable debounce=300").classes("col-5")
        search_input.on_value_change(lambda e: _update_search(e))

        ptype_select = ui.select(
            ptype_opts,
            value=ptype_ref["value"],
            label="Project type",
        ).classes("col-4")
        ptype_select.on_value_change(lambda e: _update_ptype(e))

        tstatus_select = ui.select(
            {"all": "All", "translated": "Translated", "untranslated": "Untranslated"},
            value=tstatus_ref["value"],
            label="Translation",
        ).classes("col-3")
        tstatus_select.on_value_change(lambda e: _update_tstatus(e))

    noauth_cb = ui.checkbox("Only no-author", value=noauth_ref["value"])
    noauth_cb.on_value_change(lambda e: _update_noauth(e))

    def _update_search(e):
        search_ref["value"] = e.value if hasattr(e, "value") else ""
        page_ref["value"] = 1
        render()

    def _update_ptype(e):
        ptype_ref["value"] = e.value if hasattr(e, "value") else "all"
        page_ref["value"] = 1
        render()

    def _update_noauth(e):
        noauth_ref["value"] = e.value
        page_ref["value"] = 1
        render()

    def _update_tstatus(e):
        tstatus_ref["value"] = e.value if hasattr(e, "value") else "all"
        page_ref["value"] = 1
        render()

    def _update_page(page_num):
        page_ref["value"] = page_num
        render()

    # ── Render single source expansion ───────────────────────────────────
    def _render_source(
        s: dict, kg, agent_opts: dict, src_author: dict, src_translator: dict, render_fn,
    ):
        s_id = s["id"]
        current_author_id = src_author.get(s_id, "_none")

        # Connected agent edges summary (out_edges with written_by/translated_by/edited_by)
        connected: list[str] = []
        for _, tgt, data in kg.G.out_edges(s_id, data=True):
            rel = data.get("relation")
            if rel in ("written_by", "translated_by", "edited_by"):
                agent_name = kg.G.nodes[tgt].get("name", tgt) if kg.G.has_node(tgt) else tgt
                connected.append(f"{rel.replace('_', ' ')}: {agent_name}")
        connected_str = " | ".join(connected) if connected else "(no agent edges)"

        # Translation status badge
        has_t = _has_translation(s, s_id in src_translator)
        status_badge = "⌖ translated" if has_t else "no translation"
        caption = f'{s.get("year", "—")} · {status_badge} · {connected_str}'

        with ui.expansion(s.get("title", s_id), caption=caption).classes("w-full"):
            # ── Core edit fields ─────────────────────────────────────────
            title_input = ui.input("Title:", value=s.get("title", "")).classes("w-full q-mb-sm")

            year_val = s.get("year") or 2000
            year_input = ui.number("Year:", value=year_val, min=1800, max=2030).classes("w-full q-mb-sm")

            author_display = {"_none": "None"}
            author_display.update({k: v for k, v in agent_opts.items()})
            author_select = ui.select(
                author_display,
                value=current_author_id if current_author_id in author_display else "_none",
                label="Author",
            ).classes("w-full q-mb-sm")

            # ── Bilingual fields ──────────────────────────────────────────
            ui.separator().classes("q-my-sm")
            ui.label("Bilingual fields").classes("text-caption")
            title_orig_input = ui.input("Title (orig):", value=s.get("title_orig") or "").props(
                "outlined dense"
            ).classes("w-full q-mb-sm")
            title_trans_input = ui.input("Title (translation):", value=s.get("title_translation") or "").props(
                "outlined dense"
            ).classes("w-full q-mb-sm")
            orig_lang_input = ui.input("Orig lang:", value=s.get("orig_lang") or "").props(
                "outlined dense"
            ).classes("w-full q-mb-xs col-6")
            trans_lang_input = ui.input("Translation lang:", value=s.get("translation_lang") or "").props(
                "outlined dense"
            ).classes("w-full q-mb-sm")

            # ── Translation edition ───────────────────────────────────────
            ui.separator().classes("q-my-sm")
            ui.label("Translation edition").classes("text-caption")
            te = s.get("translation_edition") or {}
            te_publisher = ui.input("Publisher:", value=te.get("publisher") or "").props(
                "outlined dense"
            ).classes("w-full q-mb-xs")
            te_city = ui.input("City:", value=te.get("city") or "").props(
                "outlined dense"
            ).classes("w-full q-mb-xs")
            te_year = ui.input("Year:", value=str(te.get("year") or "")).props(
                "outlined dense"
            ).classes("w-full q-mb-xs")
            te_translator = ui.input("Translator:", value=te.get("translator") or "").props(
                "outlined dense"
            ).classes("w-full q-mb-xs")
            te_language = ui.input("Language:", value=te.get("language") or "").props(
                "outlined dense"
            ).classes("w-full q-mb-sm")

            # ── Link translator / editor ──────────────────────────────────
            ui.separator().classes("q-my-sm")
            ui.label("Link agent").classes("text-caption")
            link_opts = {"_none": "— none —"}
            link_opts.update({k: v for k, v in agent_opts.items()})

            with ui.row().classes("w-full items-center q-gutter-sm"):
                trans_select = ui.select(link_opts, value="_none", label="Add translator").classes("col-6")
                ui.button("Link", icon="link", on_click=lambda: _link_agent(
                    s_id, trans_select, "translated_by", kg, render_fn,
                )).props("flat dense color=primary")

            with ui.row().classes("w-full items-center q-gutter-sm"):
                edit_select = ui.select(link_opts, value="_none", label="Add editor").classes("col-6")
                ui.button("Link", icon="link", on_click=lambda: _link_agent(
                    s_id, edit_select, "edited_by", kg, render_fn,
                )).props("flat dense color=primary")

            # ── Save / Delete ────────────────────────────────────────────
            async def _on_save():
                auth_val = None if author_select.value == "_none" else author_select.value
                # Assemble translation_edition dict
                te_dict: dict | None = None
                te_parts: dict[str, str | int] = {}
                if te_publisher.value:
                    te_parts["publisher"] = te_publisher.value.strip()
                if te_city.value:
                    te_parts["city"] = te_city.value.strip()
                if te_translator.value:
                    te_parts["translator"] = te_translator.value.strip()
                if te_language.value:
                    te_parts["language"] = te_language.value.strip()
                te_year_val = (te_year.value or "").strip()
                if te_year_val:
                    try:
                        te_parts["year"] = int(te_year_val)
                    except ValueError:
                        pass
                if te_parts:
                    te_dict = te_parts

                await run.io_bound(
                    kg.update_source_text_node,
                    s_id,
                    title=title_input.value,
                    year=int(year_input.value) if year_input.value else None,
                    author_id=auth_val,
                    title_orig=title_orig_input.value or None,
                    title_translation=title_trans_input.value or None,
                    orig_lang=orig_lang_input.value or None,
                    translation_lang=trans_lang_input.value or None,
                    translation_edition=te_dict,
                )
                ui.notify("Updated.", type="positive")
                render_fn()

            async def _on_delete():
                await run.io_bound(kg.remove_node, s_id)
                ui.notify("Deleted.", type="positive")
                render_fn()

            with ui.row().classes("q-gutter-sm"):
                ui.button("Save", icon="save", color="primary", on_click=_on_save)
                ui.button("Delete", icon="delete", color="negative", on_click=_on_delete)

    # ── New Source Text form ──────────────────────────────────────────────
    def _render_new_source(kg, agent_opts: dict, render_fn):
        ns_id_input = ui.input("Short ID:").classes("w-full q-mb-sm")
        ns_title_input = ui.input("Title:").classes("w-full q-mb-sm")
        ns_year_input = ui.number("Year:", value=2000, min=1800, max=2030).classes("w-full q-mb-sm")
        ns_author_display = {"_none": "None"}
        ns_author_display.update({k: v for k, v in agent_opts.items()})
        ns_author_select = ui.select(
            ns_author_display,
            value="_none",
            label="Author",
        ).classes("w-full q-mb-sm")

        async def _on_create():
            ns_id = ns_id_input.value.strip()
            ns_title = ns_title_input.value.strip()
            if not ns_id or not ns_title:
                ui.notify("Both Short ID and Title are required.", type="warning")
                return
            auth_val = None if ns_author_select.value == "_none" else ns_author_select.value
            await run.io_bound(
                kg.add_source_text_node, ns_id, ns_title,
                author_id=auth_val,
                year=int(ns_year_input.value) if ns_year_input.value else None,
            )
            ui.notify("Created.", type="positive")
            render_fn()

        ui.button("Create", icon="add", color="primary", on_click=_on_create).classes("w-full")

    async def _link_agent(s_id, agent_select, relation, kg, render_fn):
        """Link an agent to a source via translated_by or edited_by."""
        agent_id = agent_select.value
        if not agent_id or agent_id == "_none":
            ui.notify("Select an agent first.", type="warning")
            return
        if relation == "translated_by":
            await run.io_bound(kg.link_translated_by, s_id, agent_id)
        elif relation == "edited_by":
            await run.io_bound(kg.link_edited_by, s_id, agent_id)
        else:
            ui.notify(f"Unknown relation: {relation}", type="negative")
            return
        ui.notify("Linked.", type="positive")
        render_fn()

    # Initial render
    render()