"""Persistent editor card — pure NiceGUI high-level API.

Built once per page render. Content mutates in place when state.active_index
changes (via `bind_value` on state.current, `set_text` on labels, and
`element.clear()` + repopulate for the small QA / suggestion sub-trees).
No raw HTML, no JS injection, no custom CSS.
"""
from __future__ import annotations

import asyncio
from typing import Callable

import html as html_lib

from nicegui import background_tasks, ui

from . import predictions, settings as ui_settings
from .state import WorkspaceState


def build(state: WorkspaceState, deps: dict, on_confirm: Callable[[], None]) -> dict:
    translator = deps["translator"]
    tm = deps["tm"]
    glossary = deps["glossary"]
    kg = deps["kg"]
    qa_engine = deps["qa_engine"]
    llm_executor = deps["llm_executor"]
    parse_lang_pair = deps["parse_lang_pair"]

    seg = state.segments[state.active_index] if state.segments else {
        "id": 0, "source": "", "target": "", "status": "pending",
    }

    card = ui.card().classes("w-full p-0 rounded-2xl my-6 overflow-hidden").props(
        "bordered id=sl-editor-card"
    )

    with card:
        with ui.row().classes("w-full px-6 py-3 justify-between items-center"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("tag", size="14px").props("color=grey-6")
                header_index_label = ui.label(f"SEGMENT {seg['id'] + 1}").classes(
                    "text-[10px] font-black tracking-[.2em] opacity-60"
                )
            status_badge = ui.badge(
                "CONFIRMED" if seg["status"] == "done" else "DRAFTING",
                color="positive" if seg["status"] == "done" else "info",
            ).classes("text-[9px] font-bold px-2 py-0.5 rounded-full")
        ui.separator()

        with ui.column().classes("w-full px-6 pt-4 pb-2 gap-2"):
            ui.label("SOURCE").classes("text-[9px] font-black tracking-[0.2em] uppercase opacity-50")
            with ui.card().props("flat bordered").classes("rounded-xl p-4"):
                source_label = ui.label(seg["source"]).classes(
                    "leading-relaxed"
                ).style(
                    'font-family: "Inter", -apple-system, BlinkMacSystemFont, '
                    '"Segoe UI", Roboto, sans-serif; font-size: 16px; line-height: 1.625;'
                )

        with ui.column().classes("w-full px-6 pb-4 gap-2"):
            ui.label("TARGET").classes("text-[9px] font-black tracking-[0.2em] uppercase opacity-50")
            qa_row = ui.column().classes("w-full gap-1 mb-2")
            # Dual-layer ghost-text editor: a transparent textarea sits on top
            # of an HTML overlay that mirrors the value plus the suggested
            # continuation. Both elements are NiceGUI primitives; the
            # transparency / overlay technique is the only Copilot-style
            # pattern that needs the small CSS in settings.SHARED_CSS.
            with ui.card().props("flat bordered").classes("relative w-full rounded-xl p-0"):
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
                    .props("borderless dense autogrow")
                    .classes("w-full h-full z-20 prediction-textarea")
                )

        ui.separator()
        with ui.row().classes("w-full px-6 py-3 justify-between items-center"):
            ai_master_on = ui_settings.ai_master_enabled()
            if ai_master_on:
                regen_btn = ui.button(icon="auto_awesome").props(
                    "flat round dense size=md color=primary"
                ).tooltip("Regenerate AI Draft")
            else:
                regen_btn = ui.button(icon="auto_awesome").props(
                    "flat round dense size=md disable color=grey"
                ).tooltip("AI Translation is disabled on the home page")
            with ui.row().classes("items-center gap-4"):
                ui.label("⌘↵ confirm").classes(
                    "text-[10px] font-bold uppercase tracking-wider opacity-50"
                )
                confirm_btn = ui.button("CONFIRM", on_click=lambda _: on_confirm()).props(
                    "unelevated rounded color=positive"
                ).classes("px-8 py-2 font-black tracking-[.2em] text-[11px]")

    refs = {
        "card": card,
        "header_index_label": header_index_label,
        "status_badge": status_badge,
        "source_label": source_label,
        "qa_row": qa_row,
        "target_textarea": target_textarea,
        "ghost_overlay": ghost_overlay,
        "regen_btn": regen_btn,
        "confirm_btn": confirm_btn,
    }

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
            return qa_engine.check_segment(seg_now["source"], seg_now["target"], g_hits, src_lang=src, tgt_lang=tgt) if qa_engine else []

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
                with ui.card().props(
                    f"flat bordered text-color={'negative' if is_err else 'warning'}"
                ).classes("w-full px-3 py-2 flex-row items-center gap-2"):
                    ui.icon("error" if is_err else "warning", size="16px").props(
                        f"color={'negative' if is_err else 'warning'}"
                    )
                    ui.label(w.get("message", "")).classes("font-medium text-xs")

    async def _ai_draft(force: bool = False):
        if not state.segments:
            return
        if not ui_settings.ai_master_enabled():
            return
        idx = state.active_index
        seg_now = state.segments[idx]
        if not force and seg_now["target"].strip():
            return
        if not force and not ui_settings.ai_pretranslate_enabled():
            return
        src, tgt = _src_tgt()
        loop = asyncio.get_running_loop()

        def _lookups():
            a = tm.lookup_fuzzy(seg_now["source"], threshold=90.0, limit=1) if tm else []
            b = glossary.lookup_terms(seg_now["source"], src, tgt) if glossary else []
            c = tm.search_concordance(seg_now["source"], top_n=2) if tm else []
            k = kg.extract_entities(seg_now["source"]) if kg else []
            return a, b, c, k

        try:
            target_textarea.props("loading")
        except Exception:
            pass
        try:
            a, b, c, k = await loop.run_in_executor(None, _lookups)
            _, text = await loop.run_in_executor(
                llm_executor,
                lambda: translator.translate(seg_now["source"], src, tgt, a, b, c, k),
            )
            if not state.segments:
                return
            captured_seg = state.segments[idx]
            if force or not captured_seg["target"].strip():
                captured_seg["target"] = text
                if state.active_index == idx:
                    state.current["target"] = text
                state.request_autosave()
        except Exception as ex:
            print(f"[AI] {ex}")
        finally:
            try:
                target_textarea.props(remove="loading")
            except Exception:
                pass

    regen_btn.on_click(lambda _: background_tasks.create(_ai_draft(force=True), name="ai_regen"))

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

        src, tgt = _src_tgt()
        background_tasks.create(
            predictions.push_bundle(target_textarea.id, seg_now["source"], src, tgt, tm, glossary, kg, client=state.client),
            name="push_bundle",
        )
        background_tasks.create(_refresh_qa(), name="qa_refresh")
        if not seg_now["target"].strip() and ui_settings.ai_pretranslate_enabled():
            background_tasks.create(_ai_draft(), name="ai_init")

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
            return
        src, tgt = _src_tgt()
        await predictions.push_bundle(target_textarea.id, seg_now["source"], src, tgt, tm, glossary, kg, client=state.client)
        await _refresh_qa()
        if not seg_now["target"].strip() and ui_settings.ai_pretranslate_enabled():
            background_tasks.create(_ai_draft(), name="ai_initial")

    background_tasks.create(_initial(), name="editor_initial")

    return refs
