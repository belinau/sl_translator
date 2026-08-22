"""Concepts page — search, edit, delete, rhizomatic connections, new concept,
plus untranslated-target queue for fast bilingual-label entry."""

from __future__ import annotations

from nicegui import run, ui

from .common import kg_frame

PAGE_SIZE = 30


def _needs_target(c: dict) -> bool:
    """A concept needs a target label if it has a translation_lang slot set
    but label_translation is empty. This cleanly captures the ~116
    bilingual-scaffolded concepts without dragging in ~4300 legacy concepts."""
    return bool(c.get("translation_lang")) and not (c.get("label_translation") or "").strip()


@ui.page("/kg/concepts")
def page_concepts():
    result = kg_frame("/kg/concepts")
    if result is None or result[0] is None:
        return
    kg, _glossary = result

    # ── State closures ─────────────────────────────────────────────────────
    search_ref: dict = {"value": ""}
    mode_ref: dict = {"value": "search"}
    page_ref: dict = {"value": 1}

    # ── Filter inputs (built once, outside render) ────────────────────────
    with ui.row().classes("w-full items-center q-gutter-sm"):
        search_input = ui.input(
            "Search concepts (label, domain, or ID)",
        ).props("outlined dense clearable debounce=300").classes("col-7")
        search_input.on_value_change(lambda e: _update_search(e))

        # Untranslated count is computed dynamically in render()
        mode_toggle = ui.toggle(
            {"search": "Search", "untranslated": "Untranslated"},
            value="search",
        ).classes("col")
        mode_toggle.on_value_change(lambda e: _update_mode(e))

    results = ui.column().classes("w-full px-4 pb-4 gap-2")

    # ── Filter helpers ─────────────────────────────────────────────────────
    def _update_search(e):
        search_ref["value"] = e.value if hasattr(e, "value") else (e or "")
        page_ref["value"] = 1
        render()

    def _update_mode(e):
        mode_ref["value"] = e.value if hasattr(e, "value") else (e or "search")
        page_ref["value"] = 1
        render()

    def _update_page(page_num):
        page_ref["value"] = page_num
        render()

    # ── Master render ──────────────────────────────────────────────────────
    def render():
        concepts_all = kg.get_all_by_type("concept") if kg else []

        # Show/hide search input based on mode
        if mode_ref["value"] == "search":
            search_input.set_visibility(True)
        else:
            search_input.set_visibility(False)

        results.clear()
        with results:
            ui.label("Concepts").classes("text-h6 q-mb-sm")
            if mode_ref["value"] == "untranslated":
                _render_untranslated(kg, concepts_all, render, page_ref, _update_page)
            else:
                _render_search(kg, concepts_all, render, search_ref)
            # Rhizomatic connection section (visible when ≥2 concepts)
            if len(concepts_all) >= 2:
                _rhizome_section(kg, concepts_all, render)

            # New concept section (always visible)
            _new_concept_section(kg, render)

    render()


# ---------------------------------------------------------------------------
# Search mode render
# ---------------------------------------------------------------------------
def _render_search(kg, concepts_all: list, render_fn, search_ref: dict):
    q = search_ref["value"].strip().lower()
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
    if q:
        ui.label(f"{len(hits)} of {len(concepts_all)} concepts match.").classes(
            "text-caption"
        )
        for c in hits:
            _concept_card(c, kg, render_fn)
        if not hits:
            ui.label(f"No concepts matching '{q}'.").classes("text-grey-6")
    else:
        ui.label(
            f"{len(concepts_all)} concepts in graph. "
            "Type a search to find and edit them, or switch to Untranslated to see missing target labels."
        ).classes("text-grey-6")

# ---------------------------------------------------------------------------
# Untranslated queue
# ---------------------------------------------------------------------------
def _render_untranslated(kg, concepts_all: list, render_fn, page_ref: dict, update_page_fn):
    queue = sorted(
        [c for c in concepts_all if _needs_target(c)],
        key=lambda c: c.get("label_orig") or c.get("label") or c.get("id", ""),
    )
    ui.label(f"{len(queue)} concepts need a target label.").classes("text-caption q-mb-sm")

    if not queue:
        ui.label("All bilingual concepts have target labels!").classes("text-positive")
        return

    # Pagination
    total_pages = max(1, (len(queue) + PAGE_SIZE - 1) // PAGE_SIZE)
    current_page = min(page_ref["value"], total_pages)
    page_ref["value"] = current_page
    page_start = (current_page - 1) * PAGE_SIZE
    page_slice = queue[page_start : page_start + PAGE_SIZE]

    if total_pages > 1:
        ui.pagination(
            1,
            total_pages,
            direction_links=True,
            value=current_page,
            on_change=lambda e: update_page_fn(e.value),
        ).classes("q-mb-md")

    for c in page_slice:
        _queue_row(c, kg, render_fn)


def _queue_row(c: dict, kg, render_fn):
    """One dense row: source label + domain caption + target-label input + save."""
    c_id = c["id"]
    label = c.get("label_orig") or c.get("label") or c_id
    domain = c.get("domain", "")

    with ui.row().classes("w-full items-center q-gutter-sm"):
        ui.label(label).classes("col-4 text-body2")
        if domain:
            ui.label(domain).classes("text-caption text-grey-6")
        inp = ui.input("target label", value=c.get("label_translation") or "").props(
            "outlined dense"
        ).classes("col-4")

        async def _save(val=inp, cid=c_id, rfn=render_fn):
            v = (val.value or "").strip()
            if not v:
                ui.notify("Enter a target label", type="warning")
                return
            await run.io_bound(kg.update_concept_metadata, cid, label_translation=v)
            ui.notify("Saved.", type="positive")
            rfn()

        ui.button(icon="check", on_click=_save).props("flat round dense color=primary")
        inp.on("keydown.enter", _save)


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
            ui.label(c.get("definition", "")).classes("text-sm")

        label_input = ui.input("Label:", value=c.get("label", "")).props(
            "outlined dense"
        ).classes("w-full")
        domain_input = ui.input("Domain:", value=c.get("domain", "")).props(
            "outlined dense"
        ).classes("w-full")
        def_input = ui.textarea("Definition:", value=c.get("definition", "")).props(
            "outlined dense"
        ).classes("w-full")

        ui.separator().classes("q-my-sm")
        ui.label("Bilingual fields").classes("text-caption")
        label_orig_input = ui.input("Label (orig):", value=c.get("label_orig") or "").props(
            "outlined dense"
        ).classes("w-full")
        label_trans_input = ui.input("Label (translation):", value=c.get("label_translation") or "").props(
            "outlined dense"
        ).classes("w-full")
        orig_lang_input = ui.input("Orig lang:", value=c.get("orig_lang") or "").props(
            "outlined dense"
        ).classes("w-full")
        trans_lang_input = ui.input("Translation lang:", value=c.get("translation_lang") or "").props(
            "outlined dense"
        ).classes("w-full")

        with ui.row().classes("gap-2"):
            ui.button(
                "Save",
                on_click=lambda: _save_concept(
                    c_id, label_input, domain_input, def_input,
                    label_orig_input, label_trans_input, orig_lang_input, trans_lang_input,
                    kg, render_fn,
                ),
            ).props("flat color=primary")

            ui.button(
                "Delete",
                icon="delete",
                on_click=lambda: _delete_concept(c_id, kg, render_fn),
            ).props("flat color=negative")


async def _save_concept(
    c_id: str, label_input, domain_input, def_input,
    label_orig_input, label_trans_input, orig_lang_input, trans_lang_input,
    kg, render_fn,
):
    await run.io_bound(
        kg.update_concept_metadata,
        c_id,
        label=label_input.value,
        domain=domain_input.value,
        definition=def_input.value,
        label_orig=label_orig_input.value or None,
        label_translation=label_trans_input.value or None,
        orig_lang=orig_lang_input.value or None,
        translation_lang=trans_lang_input.value or None,
    )
    ui.notify("Updated.", type="positive")
    render_fn()


async def _delete_concept(c_id: str, kg, render_fn):
    await run.io_bound(kg.remove_node, c_id)
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


async def _connect_rhizome(select_a, select_b, rel_select, kg, render_fn):
    con_a = select_a.value
    con_b = select_b.value
    if not con_a or not con_b:
        ui.notify("Select both concepts first.", type="warning")
        return
    if con_a == con_b:
        ui.notify("Cannot connect a concept to itself.", type="negative")
        return
    await run.io_bound(kg.link_concepts_rhizomatic, con_a, con_b, rel_select.value)
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


async def _create_concept(nc_id, nc_lbl, nc_dom, nc_def, kg, render_fn):
    cid = nc_id.value.strip()
    clbl = nc_lbl.value.strip()
    if not cid or not clbl:
        ui.notify("Identifier and Display Name are required.", type="negative")
        return
    await run.io_bound(
        kg.add_concept_node,
        f"concept:{cid.lower()}",
        label=clbl,
        domain=nc_dom.value.strip(),
        definition=nc_def.value.strip(),
    )
    ui.notify("Created.", type="positive")
    render_fn()