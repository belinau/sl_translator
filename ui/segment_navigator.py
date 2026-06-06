"""Virtualized segment navigator built with NiceGUI's ui.table.

`ui.table` wraps Quasar's QTable; with `virtual-scroll` enabled it only renders
visible rows. No raw Vue templates, no manual JS injection — pure NiceGUI API.
Row clicks fire NiceGUI's built-in `on_row_click` (no $emit hack).
"""
from __future__ import annotations

from nicegui import ui

from .state import WorkspaceState


_COLUMNS = [
    {"name": "n", "label": "#", "field": "n", "align": "right",
     "classes": "text-[10px] font-black opacity-50"},
    {"name": "preview", "label": "Segment", "field": "preview", "align": "left"},
    {"name": "status", "label": "", "field": "status", "align": "right"},
]


def _rows(state: WorkspaceState) -> list[dict]:
    active = state.active_index
    out = []
    for s in state.segments:
        is_active = s["id"] == active
        out.append({
            "id": s["id"],
            # leading marker makes the active row unmistakable without slots/JS
            "n": (f"▶ {s['id'] + 1}" if is_active else str(s["id"] + 1)),
            "preview": ("▸ " if is_active else "") + (s["target"] or s["source"] or "")[:120],
            "status": "✓" if s["status"] == "done" else ("●" if is_active else ""),
        })
    return out


def build(state: WorkspaceState) -> dict:
    with ui.column().classes("w-full gap-2"):
        with ui.row().classes("w-full items-center justify-between px-1 mb-1"):
            ui.label("SEGMENTS").classes(
                "text-[10px] font-black tracking-[.3em] opacity-60"
            )
            count_label = ui.label(f"{len(state.segments)}").classes(
                "text-[10px] font-medium opacity-60"
            )

        table = (
            ui.table(
                columns=_COLUMNS,
                rows=_rows(state),
                row_key="id",
                pagination=0,
            )
            .props("virtual-scroll dense flat bordered hide-header hide-bottom :rows-per-page-options='[0]'")
            .classes("w-full rounded-xl h-[58vh] max-h-[900px]")
        )

    def _on_row_click(e):
        # e.args = [event_dict, row_dict, index]
        try:
            row = e.args[1]
            state.set_active(int(row["id"]))
        except Exception as ex:
            print(f"[navigator row click] {ex}")

    table.on("row-click", _on_row_click)

    def _refresh_rows():
        try:
            table.rows = _rows(state)
            table.update()
        except Exception as ex:
            print(f"[navigator refresh] {ex}")

    def _scroll_to_active():
        # ui.table exposes scrollTo on its underlying virtual-scroll. Call via the
        # method-call helper NiceGUI provides on Element. No raw run_javascript.
        try:
            table.run_method("scrollTo", state.active_index, "center-force")
        except Exception as ex:
            print(f"[navigator scroll] {ex}")

    def _on_active() -> None:
        _refresh_rows()
        _scroll_to_active()

    state.subscribe("active_index", _on_active)
    state.subscribe("segments", _refresh_rows)

    return {"table": table, "count_label": count_label}
