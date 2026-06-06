"""Always-on KG + TM lookup under the segments pane.

A search input that is visible at all times (no click-to-open) with results that
flow inline below it. Type a query, press Enter, and
see KG term matches + TM concordance segments (full text). Click a result to
insert it at the cursor. Pure NiceGUI / Tailwind.
"""
from __future__ import annotations

import asyncio
import json

from nicegui import background_tasks, ui

from .state import WorkspaceState


def build(state: WorkspaceState, deps: dict) -> dict:
    kg = deps.get("kg")
    tm = deps.get("tm")
    parse_lang_pair = deps["parse_lang_pair"]

    def _insert(text: str) -> None:
        if not text:
            return
        try:
            (state.client or ui).run_javascript(
                f"window.__sl_predictor && window.__sl_predictor.insertAtCursor({json.dumps(text)});"
            )
        except Exception as ex:
            print(f"[search insert] {ex}")

    refs: dict = {}
    with ui.column().classes("w-full gap-1 mt-3"):
        ui.label("SEARCH KG & TM").classes(
            "text-[9px] font-black tracking-[.2em] opacity-60"
        )
        query_input = (
            ui.input(placeholder="Search terms / segments — press Enter")
            .props("dense outlined clearable")
            .classes("w-full")
        )
        results = ui.column().classes("w-full gap-1")
        refs["query_input"] = query_input
        refs["results"] = results

        async def _run() -> None:
            q = (query_input.value or "").strip()
            results.clear()
            if not q:
                return
            _src_lang, tgt_lang = parse_lang_pair(state.lang_pair)
            loop = asyncio.get_running_loop()
            try:
                kg_hits = await loop.run_in_executor(
                    None, lambda: kg.extract_entities(q, target_lang=tgt_lang) if kg else []
                )
            except Exception as e:
                kg_hits = []
                print(f"[search kg] {e}")
            try:
                tm_hits = await loop.run_in_executor(
                    None, lambda: tm.search_concordance(q, top_n=25) if tm else []
                )
            except Exception as e:
                tm_hits = []
                print(f"[search tm] {e}")

            with results:
                if kg_hits:
                    ui.label("KG TERMS").classes(
                        "text-[9px] font-black tracking-[.2em] opacity-60 mt-1"
                    )
                    for h in kg_hits:
                        term = h.get("term", "")
                        trans = [
                            t.get("term", "")
                            for t in (h.get("translations") or [])
                            if t.get("term")
                        ]
                        first = trans[0] if trans else ""
                        with ui.row().classes(
                            "w-full items-center gap-1 cursor-pointer hover:bg-primary/5 rounded"
                        ).on("click", lambda _e, t=first: _insert(t)):
                            ui.label(term).classes("text-xs opacity-70")
                            ui.label("→").classes("text-[10px] opacity-30")
                            ui.label(", ".join(trans[:6]) or "—").classes(
                                "text-xs font-bold"
                            ).style("white-space:normal;word-break:break-word")
                if tm_hits:
                    ui.label("TM SEGMENTS").classes(
                        "text-[9px] font-black tracking-[.2em] opacity-60 mt-2"
                    )
                    for m in tm_hits:
                        with ui.card().props("flat bordered").classes(
                            "w-full rounded-lg p-2 cursor-pointer hover:bg-primary/5"
                        ).on("click", lambda _e, t=m.get("target", ""): _insert(t)):
                            ui.label(m.get("source", "")).classes(
                                "text-[11px] italic leading-snug opacity-60"
                            ).style("white-space:normal;word-break:break-word")
                            ui.label(m.get("target", "")).classes(
                                "text-[12px] font-medium leading-snug"
                            ).style("white-space:normal;word-break:break-word")
                if not kg_hits and not tm_hits:
                    ui.label("No KG or TM matches.").classes("text-xs italic opacity-60")

        query_input.on(
            "keydown.enter", lambda _e: background_tasks.create(_run(), name="kg_search")
        )

    return refs
