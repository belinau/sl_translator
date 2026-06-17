"""KG editor — Lineage Cleanup page.

NiceGUI port of Streamlit ``page_lineages`` (kg_editor_ui.py lines 883–912).
Pattern: single ``results = ui.column()`` container; ``render()`` closure
re-invoked after every mutation.
"""
from __future__ import annotations

from nicegui import run, ui

from .common import kg_frame


@ui.page("/kg/lineages")
def page_lineages():
    kg, glossary = kg_frame("/kg/lineages")
    if kg is None:
        return

    results = ui.column().classes("w-full gap-4 p-6")

    # -- state held in the parent scope so render() can read/write them --
    messy_select = None  # set inside render()
    clean_input = None

    def render():
        nonlocal messy_select, clean_input
        results.clear()
        with results:
            ui.label("Lineage Cleanup").classes("text-h5")
            ui.label("Merge messy imported lineages into clean conceptual ones.").classes(
                "text-caption opacity-60"
            )

            all_lineages = sorted(kg.get_all_lineages())
            if not all_lineages:
                ui.label("No lineages registered yet.").classes("text-caption")
                return

            ui.label(f"{len(all_lineages)} distinct lineages in graph.").classes(
                "text-caption opacity-50"
            )

            # --- Merge form ---
            with ui.card().classes("w-full p-4").props("flat bordered"):
                messy_select = ui.select(
                    options=all_lineages,
                    label="Select lineages to merge",
                    multiple=True,
                    value=[],
                ).classes("w-full")
                messy_select.props('use-chips')

                clean_input = ui.input(
                    label="Merge into",
                    placeholder="e.g. Lacanian Psychoanalysis",
                ).classes("w-full")

                async def on_merge():
                    from ..components import busy_overlay, confirm_dialog
                    selected = messy_select.value if messy_select else []
                    clean = (clean_input.value or "").strip() if clean_input else ""
                    if not selected or not clean:
                        ui.notify("Select at least one lineage and provide a target name.", type="warning")
                        return
                    if not await confirm_dialog(
                        f"Merge {len(selected)} lineages into '{clean}'? This rewrites mappings across the graph.",
                        title="Merge lineages",
                        confirm_label="Merge",
                    ):
                        return
                    async with busy_overlay("Merging lineages…"):
                        changes = await run.io_bound(kg.merge_lineages, selected, clean)
                    ui.notify(f"Unified {changes} mappings into '{clean}'.", type="positive")
                    render()

                ui.button("Unify Lineages", on_click=on_merge).props("flat no-caps color=primary").classes(
                    "w-full"
                )

            # --- Auto-Align to Glossary ---
            if glossary.entries:
                async def on_auto_align():
                    from ..components import busy_overlay, confirm_dialog
                    if not await confirm_dialog(
                        "Auto-align all matching translations to glossary lineages?",
                        title="Auto-align lineages",
                        confirm_label="Align",
                    ):
                        return
                    async with busy_overlay("Aligning lineages…"):
                        aligned = await run.io_bound(kg.bulk_align_lineages_with_glossary, glossary.entries)
                    ui.notify(f"Auto-aligned {aligned} translations.", type="positive")
                    render()

                with ui.card().classes("w-full p-4").props("flat bordered"):
                    ui.button(
                        "Auto-Align to Glossary",
                        icon="auto_fix_high",
                        on_click=on_auto_align,
                    ).props("flat no-caps color=primary").classes("w-full").tooltip(
                        "Match chaotic lineages against your manual glossary"
                    )

    render()