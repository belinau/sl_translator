"""Reusable comments panel — used by the main editor and the review pane.

Renders the full comment history for a segment (frozen entries read-only)
plus an input for adding a new comment in the current session. Each
mutable entry owned by the current session has an inline edit textarea
+ delete button. Frozen entries show author/round metadata only.

The component is built once per segment render and rebuilt in place
when the active segment changes (caller clears + rebuilds via
``rebuild(seg, author, round, review_id)``).
"""
from __future__ import annotations

from typing import Any, Callable

from nicegui import ui

from translate_core import comments as cm

_AUTHOR_ICON = {
    cm.AUTHOR_TRANSLATOR: ("edit_note", "grey-7"),
    cm.AUTHOR_REVIEWER: ("rate_review", "info"),
}


def _round_label(r: int) -> str:
    if r == 0:
        return "draft"
    return f"round {r}"


def build(
    on_change: Callable[[], None] | None = None,
    editable: bool = True,
) -> dict[str, Any]:
    """Construct the panel container. Returns a handle with ``rebuild``.

    ``editable=False`` hides the input (used by the merge view, which
    shows history only).
    """
    container = ui.column().classes("w-full gap-1")
    handle: dict[str, Any] = {"container": container}

    def rebuild(seg: dict, author: str, round: int, review_id: str = "") -> None:
        container.clear()
        cs = cm.ensure_comments(seg)
        with container:
            if not cs and not editable:
                ui.label("No comments.").classes("text-[10px] italic opacity-40")
                return
            for c in cs:
                _render_entry(c, seg, on_change)
            if editable:
                _render_input(seg, author, round, review_id, on_change)

    handle["rebuild"] = rebuild
    return handle


def _render_entry(c: dict, seg: dict, on_change) -> None:
    icon, color = _AUTHOR_ICON.get(c.get("author", ""), ("comment", "grey-6"))
    mutable = c.get("mutable", False)
    with ui.row().classes("w-full items-start gap-2 px-1"):
        ui.icon(icon, size="14px").props(f"color={color}").classes("shrink-0 mt-1")
        with ui.column().classes("flex-1 gap-0 min-w-0"):
            with ui.row().classes("items-center gap-2"):
                ui.label(c.get("author", "?").capitalize()).classes(
                    "text-[9px] font-black uppercase opacity-60"
                )
                ui.label(_round_label(c.get("round", 0))).classes(
                    "text-[9px] opacity-40"
                )
                if not mutable:
                    ui.badge("history", color="grey-6").classes("text-[8px]")
            if mutable:
                _render_editable_text(c, seg, on_change)
            else:
                ui.label(c.get("text", "")).classes(
                    "text-xs leading-relaxed w-full"
                ).style(
                    'font-family: "Inter", sans-serif;'
                )
            if c.get("created_at"):
                ui.label(c["created_at"]).classes("text-[8px] opacity-30")


def _render_editable_text(c: dict, seg: dict, on_change) -> None:
    inp = ui.textarea(value=c.get("text", "")).props(
        "outlined dense autogrow"
    ).classes("w-full text-xs")

    def _on_edit(e):
        cm.update_comment(seg, c["id"], e.value or "")
        if on_change:
            on_change()

    inp.on_value_change(_on_edit)

    def _del(_=None):
        cm.delete_comment(seg, c["id"])
        if on_change:
            on_change()

    ui.button(icon="delete", on_click=_del).props(
        "flat round dense size=xs color=grey-5"
    ).tooltip("Delete comment")


def _render_input(seg: dict, author: str, round: int, review_id: str, on_change) -> None:
    """Render the add-comment input for the current (author, round)."""
    with ui.row().classes("w-full items-start gap-2 px-1 pt-1"):
        ui.icon("add_comment", size="14px").props("color=primary").classes("shrink-0 mt-1")
        inp = ui.input(placeholder="Add a comment…").props(
            "outlined dense"
        ).classes("w-full text-xs")

        def _on_add(_=None):
            text = (inp.value or "").strip()
            if not text:
                return
            cm.add_comment(seg, author, round, text, review_id=review_id)
            inp.set_value("")
            if on_change:
                on_change()

        ui.button(icon="send", on_click=_on_add).props(
            "flat round dense size=xs color=primary"
        ).tooltip("Add comment")