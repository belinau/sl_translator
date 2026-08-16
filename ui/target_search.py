"""Find & Replace in target segments — main column, below the KG section.

Searches every ``target`` field in the current project's segments, shows
matches with the query highlighted in orange, and offers a confirmed
"Replace All" that updates segments in place and triggers the normal
debounced autosave. Scoped to a single ``WorkspaceState`` — other open
projects are untouched.
"""
from __future__ import annotations

import re

from nicegui import ui

from .kg_search import highlight_query
from .state import WorkspaceState


def build(state: WorkspaceState, deps: dict) -> dict:
    refs: dict = {}

    with ui.card().props("flat bordered").classes("w-full p-4 rounded-2xl"):
        with ui.row().classes("w-full items-center gap-2 mb-2"):
            ui.icon("find_replace", size="16px").props("color=primary")
            ui.label("FIND & REPLACE IN TARGETS").classes(
                "text-[10px] font-black tracking-[.3em] opacity-70"
            )

        with ui.row().classes("w-full items-start gap-2"):
            find_input = (
                ui.input(placeholder="Find in target segments")
                .props("dense outlined clearable")
                .classes("flex-1")
            )
            replace_input = (
                ui.input(placeholder="Replace with")
                .props("dense outlined clearable")
                .classes("flex-1")
            )

        with ui.row().classes("w-full items-center gap-2 mt-1"):
            match_case = ui.checkbox("Match case", value=False).classes("text-xs")
            search_btn = ui.button("Search", icon="search").props(
                "flat dense color=primary"
            ).classes("text-xs")
            replace_btn = ui.button(
                "Replace All", icon="find_replace"
            ).props("flat dense color=warning").classes("text-xs")

        results = ui.column().classes("w-full gap-1 mt-2")
        refs["find_input"] = find_input
        refs["replace_input"] = replace_input
        refs["results"] = results

        # ------------------------------------------------------------------
        # Matching helpers
        # ------------------------------------------------------------------
        def _matches(text: str, q: str) -> bool:
            if not q:
                return False
            if match_case.value:
                return q in (text or "")
            return q.lower() in (text or "").lower()

        def _count_occurrences(text: str, q: str) -> int:
            if not q:
                return 0
            flags = 0 if match_case.value else re.IGNORECASE
            return len(re.findall(re.escape(q), text or "", flags))

        def _do_replace(text: str, q: str, repl: str) -> str:
            if not q:
                return text
            flags = 0 if match_case.value else re.IGNORECASE
            return re.sub(re.escape(q), lambda m: repl, text or "", flags=flags)

        # ------------------------------------------------------------------
        # Search
        # ------------------------------------------------------------------
        def _run_search() -> None:
            q = (find_input.value or "").strip()
            results.clear()
            if not q:
                return
            hits: list[tuple[int, dict, int]] = []
            for i, s in enumerate(state.segments):
                t = s.get("target", "")
                if _matches(t, q):
                    hits.append((i, s, _count_occurrences(t, q)))
            with results:
                if not hits:
                    ui.label("No target segments match.").classes(
                        "text-xs italic opacity-60"
                    )
                    return
                total_occ = sum(n for _, _, n in hits)
                ui.label(
                    f"{total_occ} match{'es' if total_occ != 1 else ''} "
                    f"in {len(hits)} segment{'s' if len(hits) != 1 else ''}"
                ).classes("text-[10px] font-bold opacity-70 mb-1")
                for idx, s, n_occ in hits:
                    with ui.card().props("flat bordered").classes(
                        "w-full rounded-lg p-2 cursor-pointer hover:bg-primary/5"
                    ).on("click", lambda _e, i=idx: state.set_active(i)):
                        with ui.row().classes(
                            "w-full items-center gap-2 mb-0.5"
                        ):
                            ui.label(f"#{s.get('id', idx) + 1}").classes(
                                "text-[10px] font-black tabular-nums opacity-50"
                            )
                            if s.get("status") == "done":
                                ui.badge("done", color="positive").classes(
                                    "text-[8px] px-1"
                                )
                            ui.label(f"{n_occ}×").classes(
                                "text-[9px] opacity-50"
                            )
                        ui.html(highlight_query(s.get("target", ""), q)).classes(
                            "text-xs leading-snug"
                        ).style("white-space:normal;word-break:break-word")

        # ------------------------------------------------------------------
        # Replace All — confirmation dialog
        # ------------------------------------------------------------------
        def _run_replace() -> None:
            q = (find_input.value or "").strip()
            repl = replace_input.value or ""
            if not q:
                ui.notify("Enter a search term first.", type="warning")
                return
            # Snapshot matched segments
            matched: list[tuple[int, dict, int]] = []
            for i, s in enumerate(state.segments):
                t = s.get("target", "")
                if _matches(t, q):
                    matched.append((i, s, _count_occurrences(t, q)))
            if not matched:
                ui.notify("No matches to replace.", type="info")
                return
            total_occ = sum(n for _, _, n in matched)

            with ui.dialog() as dialog, ui.card().classes("min-w-[420px] p-4 gap-3"):
                ui.label("Confirm Replace All").classes("text-base font-bold")
                ui.label(
                    f"Replace {total_occ} occurrence"
                    f"{'s' if total_occ != 1 else ''} of "
                    f"\u201c{q}\u201d with \u201c{repl}\u201d "
                    f"in {len(matched)} segment"
                    f"{'s' if len(matched) != 1 else ''}?"
                ).classes("text-sm opacity-80").style(
                    "white-space:normal;word-break:break-word"
                )
                # Preview first 3 changes
                ui.label("Preview:").classes("text-[10px] font-bold opacity-60 mt-1")
                with ui.column().classes("w-full gap-1"):
                    for idx, s, _n in matched[:3]:
                        old_t = s.get("target", "")
                        new_t = _do_replace(old_t, q, repl)
                        with ui.row().classes("w-full gap-1 items-start"):
                            ui.label(f"#{s.get('id', idx) + 1}").classes(
                                "text-[9px] opacity-50 shrink-0 w-8"
                            )
                            with ui.column().classes("flex-1 min-w-0 gap-0"):
                                ui.html(
                                    highlight_query(old_t, q)
                                ).classes("text-[10px] opacity-60 line-through").style(
                                    "white-space:normal;word-break:break-word"
                                )
                                ui.label(new_t).classes(
                                    "text-[10px] font-semibold"
                                ).style(
                                    "white-space:normal;word-break:break-word"
                                )
                    if len(matched) > 3:
                        ui.label(
                            f"… and {len(matched) - 3} more segment"
                            f"{'s' if len(matched) - 3 != 1 else ''}"
                        ).classes("text-[9px] italic opacity-50")

                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Cancel", on_click=dialog.close).props(
                        "flat dense color=grey-6"
                    )
                    def _confirm() -> None:
                        dialog.close()
                        _execute_replace(q, repl, matched, total_occ)
                    ui.button("Replace", on_click=_confirm).props(
                        "unelevated dense color=warning"
                    )
            dialog.open()

        def _execute_replace(
            q: str, repl: str,
            matched: list[tuple[int, dict, int]],
            total_occ: int,
        ) -> None:
            active_idx = state.active_index
            active_changed = False
            for i, s, _n in matched:
                old_t = s.get("target", "")
                new_t = _do_replace(old_t, q, repl)
                s["target"] = new_t
                if i == active_idx:
                    # Keep the editor textarea binding in sync — in-place
                    # mutation, same pattern as state.set_target().
                    state.current["target"] = new_t
                    active_changed = True
            state.is_dirty = True
            state.notify("segments")
            if active_changed:
                state.notify("target")
            state.request_autosave()
            ui.notify(
                f"Replaced {total_occ} occurrence"
                f"{'s' if total_occ != 1 else ''} "
                f"in {len(matched)} segment"
                f"{'s' if len(matched) != 1 else ''}.",
                type="positive",
            )
            _run_search()  # refresh results to show new state

        # ------------------------------------------------------------------
        # Wire up controls
        # ------------------------------------------------------------------
        search_btn.on("click", lambda _e: _run_search())
        find_input.on("keydown.enter", lambda _e: _run_search())
        replace_btn.on("click", lambda _e: _run_replace())

    return refs