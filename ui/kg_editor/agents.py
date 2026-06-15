"""KG editor — Agents page. NiceGUI port of kg_editor_ui.py page_agents (lines 696-764)."""

from __future__ import annotations

from nicegui import run, ui

from translate_core.kg_review_ops import AGENT_ROLES

PAGE_SIZE = 25


@ui.page("/kg/agents")
def page_agents():
    from ui.kg_editor.common import kg_frame

    kg, _glossary = kg_frame("/kg/agents")
    if kg is None:
        return
    results = ui.column().classes("w-full px-4 pb-4 gap-2")

    # ── State closures ────────────────────────────────────────────────────
    search_ref: dict = {"value": ""}
    role_filter_ref: dict = {"value": "all"}
    page_ref: dict = {"value": 1}

    # ── Filter inputs (built once, outside render) ──────────────────────
    with ui.row().classes("w-full items-center q-gutter-sm"):
        search_input = ui.input(
            "Search name:",
            value=search_ref["value"],
            placeholder="e.g. foucault, ahmed, maska",
        ).props("outlined dense clearable debounce=300").classes("col-7")
        search_input.on_value_change(lambda e: _update_search(e))

        role_choices = ["all"] + sorted({a.get("role", "?") for a in (kg.get_all_by_type("agent") if kg else [])})
        role_select = ui.select(
            role_choices,
            value=role_filter_ref["value"],
            label="Role",
        ).classes("col-3")
        role_select.on_value_change(lambda e: _update_role(e))

    def render():
        agents = kg.get_all_by_type("agent") if kg else []
        if not agents:
            results.clear()
            with results:
                ui.label("No agents registered yet.").classes("text-grey-6 q-pa-md")
            return

        # Filters
        agent_search = search_ref["value"].strip().lower()
        agent_role_filter = role_filter_ref["value"]

        def agent_matches(a: dict) -> bool:
            if agent_role_filter != "all" and a.get("role") != agent_role_filter:
                return False
            if agent_search and agent_search not in (a.get("name", "") or "").lower():
                return False
            return True

        agents_filtered = [a for a in agents if agent_matches(a)]

        results.clear()
        with results:
            ui.label(f"{len(agents_filtered)} match filters.").classes("text-caption q-mb-sm")

            # Pagination
            total_pages = max(1, (len(agents_filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
            current_page = min(page_ref["value"], total_pages)
            page_ref["value"] = current_page
            page_start = (current_page - 1) * PAGE_SIZE
            page_slice = agents_filtered[page_start : page_start + PAGE_SIZE]

            if total_pages > 1:
                ui.pagination(
                    1,
                    total_pages,
                    direction_links=True,
                    value=current_page,
                    on_change=lambda e: _update_page(e.value),
                ).classes("q-mb-md")

            # Agent list
            for a in page_slice:
                _render_agent(a, kg, render)

            # Divider + New Agent
            ui.separator().classes("q-my-md")
            with ui.expansion("New Agent", icon="add").classes("w-full"):
                _render_new_agent(kg, render)
    # ── Filter update helpers ─────────────────────────────────────────────
    def _update_search(e):
        search_ref["value"] = e.value if hasattr(e, "value") else (e or "")
        page_ref["value"] = 1
        render()

    def _update_role(e):
        role_filter_ref["value"] = e.value if hasattr(e, "value") else (e or "all")
        page_ref["value"] = 1
        render()

    def _update_page(page_num):
        page_ref["value"] = page_num
        render()

    # ── Render single agent expansion ─────────────────────────────────────
    def _render_agent(a: dict, kg, render_fn):
        a_id = a["id"]
        with ui.expansion(a.get("name", a_id), caption=a.get("role", "—")).classes("w-full"):
            # Edit form
            name_input = ui.input("Name:", value=a.get("name", "")).classes("w-full q-mb-sm")
            current_role = a.get("role", "author")
            roles_for_this = list(AGENT_ROLES)
            if current_role not in roles_for_this:
                roles_for_this.append(current_role)
            role_select = ui.select(
                roles_for_this,
                value=current_role,
                label="Role",
            ).classes("w-full q-mb-sm")

            async def _on_save():
                await run.io_bound(
                    kg.update_agent_node, a_id, name=name_input.value, role=role_select.value
                )
                ui.notify("Updated.", type="positive")
                render_fn()

            async def _on_delete():
                await run.io_bound(kg.remove_node, a_id)
                ui.notify("Deleted.", type="positive")
                render_fn()

            with ui.row().classes("q-gutter-sm"):
                ui.button("Save", icon="save", color="primary", on_click=_on_save)
                ui.button("Delete", icon="delete", color="negative", on_click=_on_delete)

    # ── New Agent form ────────────────────────────────────────────────────
    def _render_new_agent(kg, render_fn):
        na_id_input = ui.input("Short ID (e.g. haraway):").classes("w-full q-mb-sm")
        na_name_input = ui.input("Full Name:").classes("w-full q-mb-sm")
        na_role_select = ui.select(AGENT_ROLES, value="author", label="Role").classes(
            "w-full q-mb-sm"
        )

        async def _on_create():
            na_id = na_id_input.value.strip()
            na_name = na_name_input.value.strip()
            if not na_id or not na_name:
                ui.notify("Both Short ID and Full Name are required.", type="warning")
                return
            await run.io_bound(kg.add_agent_node, na_id, na_name, role=na_role_select.value)
            ui.notify("Created.", type="positive")
            render_fn()

        ui.button("Create", icon="add", color="primary", on_click=_on_create).classes("w-full")

    # Initial render
    render()