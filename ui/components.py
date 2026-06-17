"""Shared high-level NiceGUI components for dialogs, confirmations, and busy feedback.

No run_javascript, add_head_html, custom CSS, or raw threads. All helpers use
the public NiceGUI API only (ui.dialog, ui.spinner, ui.label, ui.button, run).
"""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

from nicegui import ui


async def confirm_dialog(
    message: str,
    *,
    title: str = "Confirm",
    confirm_label: str = "Proceed",
    confirm_color: str = "negative",
) -> bool:
    """Blocking confirmation dialog. Returns True if the user clicked Proceed."""
    with ui.dialog() as dialog, ui.card().classes("min-w-[360px]").style("gap: 0.5rem"):
        ui.label(title).classes("text-h6")
        ui.label(message).classes("text-body2 opacity-80")
        with ui.row().classes("w-full justify-end").style("gap: 0.5rem"):
            ui.button("Cancel", on_click=lambda: dialog.submit(False)).props("flat")
            ui.button(
                confirm_label,
                on_click=lambda: dialog.submit(True),
            ).props(f"unelevated color={confirm_color}")
    return bool(await dialog)


@asynccontextmanager
async def busy_overlay(message: str = "Working…"):
    """Modal spinner overlay that blocks interaction while work runs.

    Usage:
        async with busy_overlay("Saving…"):
            await run.io_bound(expensive_call)
    """
    with ui.dialog().props("persistent") as dialog, ui.card().classes(
        "items-center"
    ).style("gap: 0.75rem"):
        ui.spinner(size="lg")
        ui.label(message).classes("text-body2")
    dialog.open()
    try:
        yield
    finally:
        dialog.close()


def run_once_disabled(
    button: ui.button,
    coro_factory: Callable[[], Coroutine[Any, Any, None]],
    *,
    label_while_running: str | None = None,
) -> None:
    """Wire a button to run an async handler exactly once and stay disabled until done.

    Keeps handler signatures ``async def _on_x():`` with no event parameter.
    """
    original_label = button.props.get("label") or button.text or ""

    async def _wrapped() -> None:
        button.disable()
        if label_while_running:
            button.set_text(label_while_running)
        try:
            await coro_factory()
        finally:
            if label_while_running:
                button.set_text(original_label)
            button.enable()

    button.on_click(_wrapped)


__all__ = ["busy_overlay", "confirm_dialog", "run_once_disabled"]
