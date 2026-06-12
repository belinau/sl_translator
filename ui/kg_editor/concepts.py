"""Concepts page — search, edit, delete, rhizomatic connections, new concept."""

from __future__ import annotations

from nicegui import ui

from .common import kg_frame


@ui.page("/kg/concepts")
def page_concepts():
    result = kg_frame("/kg/concepts")
    if result is None or result[0] is None:
        return
    kg, _glossary = result

    # --- Search input (outside results container so value survives re-renders) ---
    search_input = ui.input(
        "Search concepts (label, domain, or ID)",
    ).props("outlined dense clearable debounce=300").classes("w-full mt-4")
    results = ui.column().classes("w-full px-4 pb-4 gap-2")

    def render():
        q = (search_input.value or "").strip().lower()
        concepts_all = kg.get_all_by_type("concept")
        hits = (
            [
                c
                for c in concepts_all
                if q in c.get("label", "").lower()
                or q in c.get("domain", "").lower()
                or q in c.get("id", "").lower()
            ]
            if q
            else []
        )
        results.clear()
        with results:
            if q:
                ui.label(f"{len(hits)} of {len(concepts_all)} concepts match.").classes(
                    "text-caption"
                )
                for c in hits:
                    _concept_card(c, kg, render)
                if not hits:
                    ui.label(f"No concepts matching '{search_input.value}'.").classes(
                        "text-grey-6"
                    )
            else:
                ui.label(
                    f"{len(concepts_all)} concepts in graph. "
                    "Type a search to find and edit them."
                ).classes("text-grey-6")

            # Rhizomatic connection section (visible when ≥2 concepts)
            if len(concepts_all) >= 2:
                _rhizome_section(kg, concepts_all, render)

            # New concept section (always visible)
            _new_concept_section(kg, render)

    search_input.on_value_change(lambda e: render())
    render()


# ---------------------------------------------------------------------------
# Concept card
# ---------------------------------------------------------------------------
def _concept_card(c: dict, kg, render_fn):
    """Expansion card for a single concept: edit form + delete."""
    c_id = c["id"]
    with ui.expansion(c.get('label', c_id), caption=c.get('domain', '—')).classes(
        "w-full"
    ):
        ui.label(f'ID: {c_id}').classes('text-caption opacity-60')
        if c.get("definition"):
            ui.markdown(c["definition"])

        label_input = ui.input("Label:", value=c.get("label", "")).props(
            "outlined dense"
        ).classes("w-full")
        domain_input = ui.input("Domain:", value=c.get("domain", "")).props(
            "outlined dense"
        ).classes("w-full")
        def_input = ui.textarea("Definition:", value=c.get("definition", "")).props(
            "outlined dense"
        ).classes("w-full")

        with ui.row().classes("gap-2"):
            ui.button(
                "Save",
                on_click=lambda: _save_concept(c_id, label_input, domain_input, def_input, kg, render_fn),
            ).props("flat color=primary")

            ui.button(
                "Delete",
                icon="delete",
                on_click=lambda: _delete_concept(c_id, kg, render_fn),
            ).props("flat color=negative")


def _save_concept(c_id: str, label_input, domain_input, def_input, kg, render_fn):
    kg.update_concept_metadata(
        c_id,
        label=label_input.value,
        domain=domain_input.value,
        definition=def_input.value,
    )
    ui.notify("Updated.", type="positive")
    render_fn()


def _delete_concept(c_id: str, kg, render_fn):
    kg.remove_node(c_id)
    ui.notify("Deleted.", type="positive")
    render_fn()


# ---------------------------------------------------------------------------
# Rhizomatic connection section
# ---------------------------------------------------------------------------
def _rhizome_section(kg, concepts_all: list, render_fn):
    """Connect Rhizomatic Concepts — two filtered selects + relation."""
    ui.separator().classes("my-4")
    with ui.expansion("Connect Rhizomatic Concepts", icon="hub").classes("w-full"):
        search_a = ui.input("Find first concept:").props(
            "outlined dense clearable"
        ).classes("w-full")
        search_b = ui.input("Find second concept:").props(
            "outlined dense clearable"
        ).classes("w-full")

        opts_a = {c["id"]: c.get("label", c["id"]) for c in concepts_all[:50]}
        opts_b = dict(opts_a)

        select_a = ui.select(opts_a, label="First:", with_input=True).props(
            "outlined dense"
        ).classes("w-full")
        select_b = ui.select(opts_b, label="Second:", with_input=True).props(
            "outlined dense"
        ).classes("w-full")
        rel_select = ui.select(
            ["critiques", "extends", "redefines", "reappropriates", "related_to"],
            value="extends",
            label="Relation:",
        ).props("outlined dense").classes("w-full")

        def _update_a():
            qa = (search_a.value or "").strip().lower()
            hits = (
                [
                    c
                    for c in concepts_all
                    if qa in c.get("label", "").lower() or qa in c.get("id", "").lower()
                ]
                if qa
                else concepts_all[:50]
            )
            new_opts = {c["id"]: c.get("label", c["id"]) for c in hits}
            select_a.set_options(new_opts)
            if new_opts and select_a.value not in new_opts:
                select_a.value = list(new_opts.keys())[0]

        def _update_b():
            qb = (search_b.value or "").strip().lower()
            hits = (
                [
                    c
                    for c in concepts_all
                    if qb in c.get("label", "").lower() or qb in c.get("id", "").lower()
                ]
                if qb
                else concepts_all[:50]
            )
            new_opts = {c["id"]: c.get("label", c["id"]) for c in hits}
            select_b.set_options(new_opts)
            if new_opts and select_b.value not in new_opts:
                select_b.value = list(new_opts.keys())[0]

        search_a.on_value_change(lambda e: _update_a())
        search_b.on_value_change(lambda e: _update_b())

        ui.button(
            "Connect",
            on_click=lambda: _connect_rhizome(select_a, select_b, rel_select, kg, render_fn),
        ).props("flat color=primary").classes("w-full")


def _connect_rhizome(select_a, select_b, rel_select, kg, render_fn):
    con_a = select_a.value
    con_b = select_b.value
    if not con_a or not con_b:
        ui.notify("Select both concepts first.", type="warning")
        return
    if con_a == con_b:
        ui.notify("Cannot connect a concept to itself.", type="negative")
        return
    kg.link_concepts_rhizomatic(con_a, con_b, rel_select.value)
    ui.notify("Connected.", type="positive")
    render_fn()


# ---------------------------------------------------------------------------
# New concept section
# ---------------------------------------------------------------------------
def _new_concept_section(kg, render_fn):
    """New Concept creation form."""
    ui.separator().classes("my-4")
    with ui.expansion("New Concept", icon="add").classes("w-full"):
        nc_id = ui.input("Identifier (e.g. cyborg_theory):").props("outlined dense").classes(
            "w-full"
        )
        nc_lbl = ui.input("Display Name:").props("outlined dense").classes("w-full")
        nc_dom = ui.input("Domain:").props("outlined dense").classes("w-full")
        nc_def = ui.textarea("Definition:").props("outlined dense").classes("w-full")

        ui.button(
            "Create",
            on_click=lambda: _create_concept(nc_id, nc_lbl, nc_dom, nc_def, kg, render_fn),
        ).props("flat color=primary").classes("w-full")


def _create_concept(nc_id, nc_lbl, nc_dom, nc_def, kg, render_fn):
    cid = nc_id.value.strip()
    clbl = nc_lbl.value.strip()
    if not cid or not clbl:
        ui.notify("Identifier and Display Name are required.", type="negative")
        return
    kg.add_concept_node(
        f"concept:{cid.lower()}",
        label=clbl,
        domain=nc_dom.value.strip(),
        definition=nc_def.value.strip(),
    )
    ui.notify("Created.", type="positive")
    render_fn()