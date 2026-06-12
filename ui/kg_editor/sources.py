"""KG editor — Source Texts page. NiceGUI port of kg_editor_ui.py page_sources (lines 770-877)."""

from __future__ import annotations

from nicegui import run, ui

PAGE_SIZE = 15


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
    page_ref: dict = {"value": 1}

    results = ui.column().classes("w-full")

    def render():
        sources = kg.get_all_by_type("source_text") if kg else []
        if not sources:
            results.clear()
            with results:
                ui.label("No source texts registered yet.").classes("text-grey-6 q-pa-md")
            return

        # Build agent_opts and src_author on each render (mirrors Streamlit cached fns)
        agent_opts: dict[str, str] = {
            a["id"]: a.get("name", a["id"]) for a in kg.get_all_by_type("agent")
        }
        src_author: dict[str, str] = {}
        for u, v, d in kg.G.edges(data=True):
            if d.get("relation") == "written_by":
                src_author.setdefault(u, v)

        # Filters
        src_search = search_ref["value"].strip().lower()
        src_ptype = ptype_ref["value"]
        no_author_only = noauth_ref["value"]

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
                _render_source(s, kg, agent_opts, src_author, render)

            # Divider + New Source Text
            ui.separator().classes("q-my-md")
            with ui.expansion("New Source Text", icon="add").classes("w-full"):
                _render_new_source(kg, agent_opts, render)

    # ── Filter controls (built once, outside render) ─────────────────────
    ptype_opts = ["all"] + sorted(
        {s.get("project_type", "_unset") or "_unset" for s in (kg.get_all_by_type("source_text") if kg else [])}
    )

    search_input = ui.input(
        'Search title/author:',
        value=search_ref["value"],
        placeholder="e.g. foucault, life of art, maska",
    ).props("outlined dense clearable debounce=300").classes("col-5")
    search_input.on_value_change(lambda e: _update_search(e))

    ptype_select = ui.select(
        ptype_opts,
        value=ptype_ref["value"],
        label="Project type",
    ).classes("col-4")
    ptype_select.on_value_change(lambda e: _update_ptype(e))

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

    def _update_page(page_num):
        page_ref["value"] = page_num
        render()

    # ── Render single source expansion ───────────────────────────────────
    def _render_source(
        s: dict, kg, agent_opts: dict, src_author: dict, render_fn,
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

        with ui.expansion(s.get("title", s_id), caption=f'{s.get("year", "—")} · {connected_str}').classes("w-full"):
            # Edit form
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

            with ui.row().classes("q-gutter-sm"):
                save_btn = ui.button("Save", icon="save", color="primary")
                delete_btn = ui.button("Delete", icon="delete", color="negative")

            async def _on_save():
                auth_val = None if author_select.value == "_none" else author_select.value
                await run.io_bound(
                    kg.update_source_text_node,
                    s_id,
                    title=title_input.value,
                    year=int(year_input.value) if year_input.value else None,
                    author_id=auth_val,
                )
                ui.notify("Updated.", type="positive")
                render_fn()

            async def _on_delete():
                await run.io_bound(kg.remove_node, s_id)
                ui.notify("Deleted.", type="positive")
                render_fn()

            save_btn.on("click", _on_save)
            delete_btn.on("click", _on_delete)

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

        create_btn = ui.button("Create", icon="add", color="primary").classes("w-full")

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

        create_btn.on("click", _on_create)

    # Initial render
    render()