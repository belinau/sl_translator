"""Persistent editor card — pure NiceGUI high-level API.

Built once per page render. Content mutates in place when state.active_index
changes (via `bind_value` on state.current, `set_text` on labels, and
`element.clear()` + repopulate for the small QA / suggestion sub-trees).
No raw HTML, no JS injection, no custom CSS.
"""
from __future__ import annotations

import asyncio
import re
from typing import Callable

import html as html_lib

from nicegui import background_tasks, ui

from . import predictions
from .comments_panel import build as build_comments_panel
from .state import WorkspaceState
from translate_core import comments as cm


def build(state: WorkspaceState, deps: dict, on_confirm: Callable[[], None]) -> dict:
    tm = deps["tm"]
    glossary = deps["glossary"]
    kg = deps["kg"]
    qa_engine = deps["qa_engine"]
    parse_lang_pair = deps["parse_lang_pair"]

    seg = state.segments[state.active_index] if state.segments else {
        "id": 0, "source": "", "target": "", "status": "pending",
    }

    card = ui.card().classes("w-full p-0 gap-0 rounded-2xl overflow-hidden flex flex-col shrink-0 no-wrap").props(
        "bordered id=sl-editor-card"
    )

    with card:
        # --- Prev rail (single-line context, click = navigate) ---
        prev_strip = ui.row().classes(
            "w-full px-4 py-2 shrink-0 items-center gap-3 no-wrap cursor-pointer "
            "bg-gray-50 dark:bg-gray-800/60 hover:bg-primary/10 transition-colors "
            "border-b border-gray-200 dark:border-gray-700"
        ).on("click", lambda: state.set_active(state.active_index - 1))
        prev_strip.tooltip("Go to previous segment (⌘↑)")
        with prev_strip:
            ui.icon("keyboard_arrow_up", size="14px").classes("opacity-40 shrink-0")
            prev_caption = ui.label("").classes(
                "text-[10px] font-bold tabular-nums opacity-40 shrink-0 w-8 text-right"
            )
            prev_label = ui.label("").classes("flex-1 min-w-0 text-xs opacity-60 truncate")
        # --- Header row ---
        with ui.row().classes("w-full px-4 py-2 justify-between items-center shrink-0 border-b border-gray-200 dark:border-gray-700"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("tag", size="14px").props("color=grey-6")
                header_index_label = ui.label(f"SEGMENT {seg['id'] + 1}").classes(
                    "text-[10px] font-black tracking-[.2em] opacity-60"
                )
            status_badge = ui.badge(
                "CONFIRMED" if seg["status"] == "done" else "DRAFTING",
                color="positive" if seg["status"] == "done" else "info",
            ).classes("text-[9px] font-bold px-2 py-0.5 rounded-full")

        # --- Source zone (content-sized with cap; scrolls internally) ---
        with ui.column().classes("w-full px-4 pt-3 pb-1 gap-1 shrink-0 no-wrap"):
            ui.label("SOURCE").classes("text-[9px] font-black tracking-[0.2em] uppercase opacity-50")
            with ui.card().props("flat bordered").classes(
                "w-full rounded-xl p-4"
            ):
                source_label = ui.label(seg["source"]).classes(
                    "leading-relaxed"
                ).style(
                    'font-family: "Inter", -apple-system, BlinkMacSystemFont, '
                    '"Segoe UI", Roboto, sans-serif; font-size: 16px; line-height: 1.625;'
                )

        # --- Target zone (flexes between min/max; scrolls internally) ---
        with ui.column().classes("w-full px-4 pt-2 pb-1 gap-1 shrink-0 no-wrap"):
            ui.label("TARGET").classes("text-[9px] font-black tracking-[0.2em] uppercase opacity-50")
            with ui.card().props("flat bordered").classes(
                "target-zone w-full rounded-xl p-0"
            ):
                with ui.element("div").classes("relative w-full"):
                    ghost_overlay = (
                        ui.html(f"<span>{html_lib.escape(seg['target'])}</span>")
                        .classes("absolute inset-0 pointer-events-none overflow-hidden z-10 ghost-prediction-overlay")
                        .props("id=sl-ghost-overlay")
                    )
                    # NiceGUI ui.textarea emits <textarea id="c{int}"> directly,
                    # so passing target_textarea.id to predictions.push_bundle is
                    # enough for the JS runtime to locate it via getElementById.
                    target_textarea = (
                        ui.textarea(value=seg["target"])
                        .bind_value(state.current, "target")
                        .props('borderless dense autogrow rows="5"')
                        .classes("w-full z-20 prediction-textarea")
                    )

        # --- QA zone (below target so warnings don't shift the focal field) ---
        qa_row = ui.column().classes("w-full px-4 gap-1 shrink-0")


        # --- Comments zone (translator notes + reviewer history) ---
        comments_zone = ui.column().classes("w-full px-4 pb-1 gap-1 shrink-0")
        with comments_zone:
            comments_handle = build_comments_panel(on_change=lambda: state.request_autosave())
        # --- Footer (confirm bar) ---
        with ui.row().classes("w-full px-4 py-2 justify-between items-center shrink-0"):
            with ui.row().classes("items-center gap-4"):
                ui.label("⌘↵ confirm").classes(
                    "text-[10px] font-bold uppercase tracking-wider opacity-50"
                )
                ui.label("⌘↑ previous  ·  ⌘↓ next").classes(
                    "text-[10px] opacity-50"
                )
                confirm_btn = ui.button("CONFIRM", on_click=lambda _: on_confirm()).props(
                    "unelevated rounded color=positive"
                ).classes("px-8 py-2 font-black tracking-[.2em] text-[11px]")

        # --- Next rail (single-line context, click = navigate) ---
        next_strip = ui.row().classes(
            "w-full px-4 py-2 shrink-0 items-center gap-3 no-wrap cursor-pointer "
            "bg-gray-50 dark:bg-gray-800/60 hover:bg-primary/10 transition-colors "
            "border-t border-gray-200 dark:border-gray-700"
        ).on("click", lambda: state.set_active(state.active_index + 1))
        next_strip.tooltip("Go to next segment (⌘↓)")
        with next_strip:
            ui.icon("keyboard_arrow_down", size="14px").classes("opacity-40 shrink-0")
            next_caption = ui.label("").classes(
                "text-[10px] font-bold tabular-nums opacity-40 shrink-0 w-8 text-right"
            )
            next_label = ui.label("").classes("flex-1 min-w-0 text-xs opacity-60 truncate")

    refs = {
        "card": card,
        "header_index_label": header_index_label,
        "status_badge": status_badge,
        "source_label": source_label,
        "qa_row": qa_row,
        "comments_zone": comments_zone,
        "target_textarea": target_textarea,
        "ghost_overlay": ghost_overlay,
        "confirm_btn": confirm_btn,
        "prev_strip": prev_strip,
        "prev_label": prev_label,
        "prev_caption": prev_caption,
        "next_strip": next_strip,
        "next_label": next_label,
        "next_caption": next_caption,
    }

    def _rebuild_comments() -> None:
        if not state.segments:
            return
        seg = state.segments[state.active_index]
        comments_handle["rebuild"](seg, cm.AUTHOR_TRANSLATOR, round=0, review_id="")

    def _refresh_strips() -> None:
        """Update prev/next context strip text and visibility."""
        if not state.segments:
            prev_strip.set_visibility(False)
            next_strip.set_visibility(False)
            return
        idx = state.active_index
        if idx > 0:
            prev = state.segments[idx - 1]
            prev_label.set_text((prev.get("target") or prev.get("source", ""))[:200])
            prev_caption.set_text(f"#{prev['id'] + 1}")
            prev_strip.set_visibility(True)
        else:
            prev_strip.set_visibility(False)
        if idx < len(state.segments) - 1:
            nxt = state.segments[idx + 1]
            next_label.set_text(nxt.get("source", "")[:200])
            next_caption.set_text(f"#{nxt['id'] + 1}")
            next_strip.set_visibility(True)
        else:
            next_strip.set_visibility(False)
    def _paint_overlay_full(value: str) -> None:
        """Mirror a full textarea value into the ghost overlay. Used ONLY
        on segment-switch / external mutation (AI draft completion, intel
        click-to-insert). NOT called on every keystroke — that path is
        owned by the JS runtime's `P._paint`, which keeps the ghost
        fragment alive between server roundtrips."""
        if ghost_overlay.is_deleted:
            return
        ghost_overlay.set_content(f"<span>{html_lib.escape(value or '')}</span>")

    def _on_target_change(e):
        # Keep state.segments in sync with the bound textarea value.
        # Crucially: DO NOT repaint the overlay here. The JS runtime
        # repaints on every keystroke and any Python-side overlay write
        # races against it and wipes the ghost fragment.
        state.set_target(e.value or "")

    target_textarea.on_value_change(_on_target_change)

    def _src_tgt():
        return parse_lang_pair(state.lang_pair)

    async def _refresh_qa():
        if not state.segments:
            return
        idx = state.active_index
        seg_now = state.segments[idx]
        if not seg_now["target"].strip():
            if not qa_row.is_deleted:
                qa_row.clear()
            return
        src, tgt = _src_tgt()
        loop = asyncio.get_running_loop()

        def _run():
            g_hits = glossary.lookup_terms(seg_now["source"], src, tgt) if glossary else []
            return qa_engine.check_segment(seg_now["source"], seg_now["target"], g_hits, src_lang=src, tgt_lang=tgt, pipeline=state.pipeline) if qa_engine else []

        try:
            warnings = await loop.run_in_executor(None, _run)
        except Exception as e:
            print(f"[editor QA] {e}")
            return
        if state.active_index != idx or qa_row.is_deleted:
            return
        qa_row.clear()
        with qa_row:
            for w in warnings:
                is_err = w.get("type") == "error"
                with ui.row().classes(
                    "w-full items-center gap-2 px-2 py-1 rounded "
                    + ("bg-red-500/10" if is_err else "bg-amber-500/10")
                ):
                    ui.icon("error" if is_err else "warning", size="14px").props(
                        f"color={'negative' if is_err else 'warning'}")
                    ui.label(w.get("message", "")).classes("text-xs font-medium flex-1 min-w-0")
                    if w.get("action") == "fix_double_space":
                        def _fix(_e):
                            # High-level NiceGUI path: mutate the bound
                            # textarea element. set_value fires
                            # on_value_change -> _on_target_change ->
                            # state.set_target, which syncs segments and
                            # notifies "target" so QA re-runs and the
                            # warning clears. The ghost overlay is repainted
                            # via the documented external-mutation painter
                            # (the JS runtime does not repaint on a
                            # server-side set_value).
                            fixed = re.sub(r' {2,}', ' ', target_textarea.value or "")
                            if fixed == (target_textarea.value or ""):
                                return
                            target_textarea.set_value(fixed)
                            _paint_overlay_full(fixed)
                        ui.button("Fix", icon="auto_fix_high", on_click=_fix).props(
                            "flat dense unelevated color=warning"
                        ).classes("text-xs normal-case")


    def _on_active_change():
        if not state.segments:
            return
        idx = state.active_index
        seg_now = state.segments[idx]

        header_index_label.set_text(f"SEGMENT {seg_now['id'] + 1}")
        status_badge.set_text("CONFIRMED" if seg_now["status"] == "done" else "DRAFTING")
        status_badge.props(f'color={"positive" if seg_now["status"] == "done" else "info"}')
        source_label.set_text(seg_now["source"])
        # Initial overlay paint for the newly-active segment — this is the
        # one time Python writes the overlay; from here on, the JS runtime
        # owns it.
        _paint_overlay_full(seg_now["target"])
        if not qa_row.is_deleted:
            qa_row.clear()
        target_textarea.run_method("focus")
        _refresh_strips()

        src, tgt = _src_tgt()
        background_tasks.create(
            predictions.push_bundle(target_textarea.id, seg_now["source"], src, tgt, tm, glossary, kg, client=state.client),
            name="push_bundle",
        )
        background_tasks.create(_refresh_qa(), name="qa_refresh")
        _rebuild_comments()

    def _on_status_change():
        if not state.segments:
            return
        seg_now = state.segments[state.active_index]
        status_badge.set_text("CONFIRMED" if seg_now["status"] == "done" else "DRAFTING")
        status_badge.props(f'color={"positive" if seg_now["status"] == "done" else "info"}')

    def _on_target_notify():
        # Fires whenever state.set_target mutates the target. Sources:
        #   1. The user typing (via _on_target_change above).
        #   2. External mutations: intel-panel click-to-insert, AI draft
        #      completion.
        # For case (1) we MUST NOT repaint — JS owns the overlay during
        # typing and our paint would wipe the ghost fragment. For case (2)
        # the JS runtime needs to be re-seeded with the new value via the
        # textarea, which bind_value handles; the JS runtime's input
        # listener then repaints on the synthetic 'input' event that
        # insertAtCursor / acceptWord dispatch.
        # → No overlay write here. QA is the only thing to refresh.
        background_tasks.create(_refresh_qa(), name="qa_refresh_typing")

    state.subscribe("active_index", _on_active_change)
    state.subscribe("segments", _on_status_change)
    state.subscribe("target", _on_target_notify)

    async def _initial():
        seg_now = state.segments[state.active_index] if state.segments else None
        if seg_now is None:
            _refresh_strips()
            return
        _refresh_strips()
        src, tgt = _src_tgt()
        await predictions.push_bundle(target_textarea.id, seg_now["source"], src, tgt, tm, glossary, kg, client=state.client)
        await _refresh_qa()
        _rebuild_comments()

    background_tasks.create(_initial(), name="editor_initial")

    return refs
